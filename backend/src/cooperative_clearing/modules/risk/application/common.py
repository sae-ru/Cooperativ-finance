"""Idempotency and personal-actor helpers for bounded risk commands."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.audit.infrastructure.repository import (
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.journal.application.service import ActorClaim
from cooperative_clearing.modules.risk.domain.types import risk_error


@dataclass(frozen=True, slots=True)
class RiskCommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool


async def begin_risk_command(
    session: AsyncSession,
    principal: Principal,
    operation: str,
    idempotency_key: str,
    payload: object,
) -> tuple[IdempotencyRecord, RiskCommandResult | None]:
    record = await IdempotencyRepository(session).begin(
        actor_user_id=principal.user_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_payload_hash(payload),
    )
    if record.status == "COMPLETED":
        stored = record.response_payload or {}
        return record, RiskCommandResult(
            event_id=UUID(str(stored["event_id"])),
            object_id=UUID(str(stored["object_id"])),
            replayed=True,
        )
    return record, None


def complete_risk_command(
    record: IdempotencyRecord, event_id: UUID, object_id: UUID
) -> RiskCommandResult:
    IdempotencyRepository.complete(
        record,
        response_status=201,
        response_payload={"event_id": str(event_id), "object_id": str(object_id)},
    )
    return RiskCommandResult(event_id, object_id, False)


def risk_role_actor(principal: Principal, cooperative_id: UUID, roles: set[RoleCode]) -> ActorClaim:
    if principal.member_id is None:
        raise risk_error("PERSONAL_ACTOR_REQUIRED", 403)
    for grant in principal.roles:
        if grant.role in roles and grant.cooperative_id in {None, cooperative_id}:
            return ActorClaim(
                person_id=principal.member_id,
                organization_id=cooperative_id,
                role_assignment_id=grant.assignment_id,
            )
    raise risk_error("AUTHORIZATION_DENIED", 403)


def risk_owner_actor(
    principal: Principal, cooperative_id: UUID, owner_member_id: UUID
) -> ActorClaim:
    if principal.member_id != owner_member_id:
        raise risk_error("ACCOUNT_OWNER_REQUIRED", 403)
    for grant in principal.roles:
        if grant.cooperative_id in {None, cooperative_id}:
            return ActorClaim(
                person_id=owner_member_id,
                organization_id=cooperative_id,
                role_assignment_id=grant.assignment_id,
            )
    raise risk_error("ACTIVE_ROLE_REQUIRED", 403)
