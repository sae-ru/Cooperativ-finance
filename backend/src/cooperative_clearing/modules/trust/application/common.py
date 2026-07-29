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
from cooperative_clearing.modules.journal.domain.assurance import (
    AccountabilityParty,
    AccountabilityPartyKind,
    CommandAssurance,
    ExposureCategory,
    ExposureClaim,
    ExposureEffect,
    actor_party,
    member_party,
)
from cooperative_clearing.modules.trust.domain.types import trust_error


@dataclass(frozen=True, slots=True)
class TrustCommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool


TRUST_ASSURANCE_EVENTS = {
    "trust.policy_proposed": (ExposureCategory.GOVERNANCE, ExposureEffect.REQUEST, False),
    "trust.policy_superseded": (ExposureCategory.GOVERNANCE, ExposureEffect.REVOKE, True),
    "trust.policy_approved": (ExposureCategory.GOVERNANCE, ExposureEffect.APPROVE, True),
    "disputes.dispute_opened": (ExposureCategory.GOVERNANCE, ExposureEffect.REQUEST, False),
    "disputes.response_recorded": (ExposureCategory.GOVERNANCE, ExposureEffect.RECORD, False),
    "disputes.case_ready_for_decision": (
        ExposureCategory.GOVERNANCE,
        ExposureEffect.REQUEST,
        True,
    ),
    "disputes.conflict_declared": (ExposureCategory.GOVERNANCE, ExposureEffect.RECORD, False),
    "disputes.decision_issued": (ExposureCategory.GOVERNANCE, ExposureEffect.DECIDE, True),
    "sanctions.protective_measure_imposed": (
        ExposureCategory.SANCTION,
        ExposureEffect.HOLD,
        True,
    ),
    "sanctions.protective_measure_lifted": (
        ExposureCategory.SANCTION,
        ExposureEffect.RELEASE,
        True,
    ),
    "sanctions.protective_measure_revoked": (
        ExposureCategory.SANCTION,
        ExposureEffect.REVOKE,
        True,
    ),
    "sanctions.sanction_proposed": (
        ExposureCategory.SANCTION,
        ExposureEffect.REQUEST,
        True,
    ),
    "sanctions.sanction_finalized": (
        ExposureCategory.SANCTION,
        ExposureEffect.FINALIZE,
        True,
    ),
    "sanctions.sanction_revoked": (
        ExposureCategory.SANCTION,
        ExposureEffect.REVOKE,
        True,
    ),
    "appeals.appeal_submitted": (ExposureCategory.GOVERNANCE, ExposureEffect.REQUEST, False),
    "appeals.appeal_decided": (ExposureCategory.GOVERNANCE, ExposureEffect.DECIDE, True),
    "reputation.event_recorded": (ExposureCategory.REPUTATION, ExposureEffect.RECORD, True),
    "reputation.event_activated": (ExposureCategory.REPUTATION, ExposureEffect.APPROVE, True),
    "reputation.event_corrected": (ExposureCategory.REPUTATION, ExposureEffect.CORRECT, True),
    "reputation.rehabilitation_recorded": (
        ExposureCategory.REPUTATION,
        ExposureEffect.RECORD,
        True,
    ),
    "rehabilitation.plan_created": (ExposureCategory.SANCTION, ExposureEffect.CREATE, True),
    "rehabilitation.step_completed": (ExposureCategory.SANCTION, ExposureEffect.RECORD, False),
    "rehabilitation.plan_completed": (
        ExposureCategory.SANCTION,
        ExposureEffect.FINALIZE,
        True,
    ),
    "rehabilitation.plan_cancelled": (
        ExposureCategory.SANCTION,
        ExposureEffect.REVOKE,
        True,
    ),
}


def trust_command_assurance(
    *,
    principal: Principal,
    actor: ActorClaim,
    event_type: str,
    subject_type: str,
    subject_id: UUID,
    command_record: IdempotencyRecord | None = None,
    evidence_refs: Sequence[object] = (),
    next_member_ids: Sequence[UUID] = (),
) -> CommandAssurance:
    mapped = TRUST_ASSURANCE_EVENTS.get(event_type)
    if mapped is None or actor.organization_id is None:
        raise trust_error("COMMAND_ASSURANCE_EVENT_UNMAPPED", 500)
    category, effect, is_decision = mapped
    cooperative = AccountabilityParty(
        kind=AccountabilityPartyKind.COOPERATIVE,
        reference=str(actor.organization_id),
    )
    evidence: list[object] = [
        {"authenticated_session_id": str(principal.session_id)},
        {
            "event_subject": {
                "event_type": event_type,
                "subject_type": subject_type,
                "subject_id": str(subject_id),
            }
        },
    ]
    basis_refs = [event_type, str(subject_id)]
    if command_record is not None:
        evidence.append({"idempotency_record_id": str(command_record.id)})
        basis_refs.append(command_record.request_hash)
    evidence.extend(evidence_refs)
    actor_ref = actor_party(actor)
    next_parties = [cooperative]
    seen_members: set[UUID] = set()
    for member_id in next_member_ids:
        if member_id in seen_members:
            continue
        seen_members.add(member_id)
        next_parties.append(member_party(member_id))
    return CommandAssurance(
        on_behalf_of=cooperative,
        exposure=ExposureClaim(
            category=category,
            effect=effect,
            subject_type=subject_type,
            subject_id=subject_id,
            basis_refs=tuple(basis_refs),
        ),
        evidence_refs=tuple(evidence),
        next_responsible=tuple(next_parties),
        attesters=() if is_decision else (actor_ref,),
        approvers=(actor_ref,) if is_decision else (),
    )


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
