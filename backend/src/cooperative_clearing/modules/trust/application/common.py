"""Idempotency, actor, evidence, and audit helpers for trust commands."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.audit.infrastructure.repository import (
    AuditRepository,
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.inventory.infrastructure.models import EvidenceBlob, EvidenceLink
from cooperative_clearing.modules.journal.application.service import ActorClaim
from cooperative_clearing.modules.trust.domain.types import trust_error


@dataclass(frozen=True, slots=True)
class TrustCommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool


async def begin_trust_command(
    session: AsyncSession,
    principal: Principal,
    operation: str,
    idempotency_key: str,
    payload: object,
) -> tuple[IdempotencyRecord, TrustCommandResult | None]:
    record = await IdempotencyRepository(session).begin(
        actor_user_id=principal.user_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_payload_hash(payload),
    )
    if record.status == "COMPLETED":
        stored = record.response_payload or {}
        return record, TrustCommandResult(
            event_id=UUID(str(stored["event_id"])),
            object_id=UUID(str(stored["object_id"])),
            replayed=True,
        )
    return record, None


def complete_trust_command(
    record: IdempotencyRecord, event_id: UUID, object_id: UUID
) -> TrustCommandResult:
    IdempotencyRepository.complete(
        record,
        response_status=201,
        response_payload={"event_id": str(event_id), "object_id": str(object_id)},
    )
    return TrustCommandResult(event_id, object_id, False)


async def trust_role_actor(
    session: AsyncSession,
    principal: Principal,
    cooperative_id: UUID,
    roles: set[RoleCode],
) -> ActorClaim:
    if principal.member_id is None:
        raise trust_error("PERSONAL_ACTOR_REQUIRED", 403)
    user = await session.get(UserAccount, principal.user_id)
    member = await session.get(Member, principal.member_id)
    membership = await session.scalar(
        select(Membership.id).where(
            Membership.cooperative_id == cooperative_id,
            Membership.member_id == principal.member_id,
            Membership.status == "ACTIVE",
        )
    )
    if (
        user is None
        or user.status != "ACTIVE"
        or user.member_id != principal.member_id
        or member is None
        or member.status != "ACTIVE"
        or membership is None
    ):
        raise trust_error("ACTOR_NOT_ACTIVE", 403)
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
    raise trust_error("AUTHORIZATION_DENIED", 403)


async def trust_participant_actor(
    session: AsyncSession,
    principal: Principal,
    cooperative_id: UUID,
) -> ActorClaim:
    return await trust_role_actor(
        session,
        principal,
        cooperative_id,
        {grant.role for grant in principal.roles},
    )


def evidence_payload(items: Sequence[EvidenceBlob]) -> list[dict[str, object]]:
    return [
        {
            "evidence_id": str(item.id),
            "sha256": item.expected_sha256,
            "size": item.expected_size,
            "kind": item.kind,
        }
        for item in items
    ]


def link_evidence(
    session: AsyncSession,
    evidence: Sequence[EvidenceBlob],
    event_id: UUID,
    subject_type: str,
    subject_id: UUID,
) -> None:
    session.add_all(
        [
            EvidenceLink(
                id=uuid4(),
                evidence_id=item.id,
                event_id=event_id,
                subject_type=subject_type,
                subject_id=subject_id,
            )
            for item in evidence
        ]
    )


async def audit_trust_action(
    session: AsyncSession,
    principal: Principal,
    cooperative_id: UUID,
    action: str,
    object_type: str,
    object_id: UUID,
    event_id: UUID,
    request_id: UUID | None,
) -> None:
    await AuditRepository(session).record(
        action=action,
        object_type=object_type,
        object_id=object_id,
        cooperative_id=cooperative_id,
        actor_user_id=principal.user_id,
        outcome="SUCCESS",
        request_id=request_id,
        payload={"signed_event_id": str(event_id)},
    )
