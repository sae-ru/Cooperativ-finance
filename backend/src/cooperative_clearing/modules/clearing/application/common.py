"""Idempotency and actor helpers for clearing commands."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.audit.infrastructure.repository import (
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.clearing.domain.engine import clearing_error
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.journal.application.service import ActorClaim


@dataclass(frozen=True, slots=True)
class ClearingCommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool


async def begin_clearing_command(
    session: AsyncSession,
    principal: Principal,
    operation: str,
    idempotency_key: str,
    payload: object,
) -> tuple[IdempotencyRecord, ClearingCommandResult | None]:
    record = await IdempotencyRepository(session).begin(
        actor_user_id=principal.user_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_payload_hash(payload),
    )
    if record.status == "COMPLETED":
        stored = record.response_payload or {}
        return record, ClearingCommandResult(
            event_id=UUID(str(stored["event_id"])),
            object_id=UUID(str(stored["object_id"])),
            replayed=True,
        )
    return record, None


def complete_clearing_command(
    record: IdempotencyRecord, event_id: UUID, object_id: UUID
) -> ClearingCommandResult:
    IdempotencyRepository.complete(
        record,
        response_status=201,
        response_payload={"event_id": str(event_id), "object_id": str(object_id)},
    )
    return ClearingCommandResult(event_id, object_id, False)


async def clearing_role_actor(
    session: AsyncSession,
    principal: Principal,
    cooperative_id: UUID,
    roles: set[RoleCode],
) -> ActorClaim:
    if principal.member_id is None:
        raise clearing_error("PERSONAL_ACTOR_REQUIRED", 403)
    user = await session.get(UserAccount, principal.user_id)
    member = await session.get(Member, principal.member_id)
    membership = (
        await session.execute(
            select(Membership.id).where(
                Membership.cooperative_id == cooperative_id,
                Membership.member_id == principal.member_id,
                Membership.status == "ACTIVE",
            )
        )
    ).scalar_one_or_none()
    if (
        user is None
        or user.status != "ACTIVE"
        or user.member_id != principal.member_id
        or member is None
        or member.status != "ACTIVE"
        or membership is None
    ):
        raise clearing_error("ACTOR_NOT_ACTIVE", 403)
    for grant in principal.roles:
        if grant.role not in roles or grant.cooperative_id not in {None, cooperative_id}:
            continue
        assignment = await session.get(RoleAssignment, grant.assignment_id)
        if (
            assignment is not None
            and assignment.status == "ACTIVE"
            and assignment.user_id == principal.user_id
            and assignment.role_code == grant.role.value
            and assignment.cooperative_id in {None, cooperative_id}
        ):
            return ActorClaim(
                person_id=principal.member_id,
                organization_id=cooperative_id,
                role_assignment_id=assignment.id,
            )
    raise clearing_error("AUTHORIZATION_DENIED", 403)


async def clearing_participant_actor(
    session: AsyncSession,
    principal: Principal,
    cooperative_id: UUID,
) -> ActorClaim:
    return await clearing_role_actor(
        session,
        principal,
        cooperative_id,
        {grant.role for grant in principal.roles},
    )
