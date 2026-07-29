"""Transactional command helpers shared by crisis use cases."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.audit.infrastructure.repository import (
    AuditRepository,
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.crisis.domain.types import crisis_error
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


@dataclass(frozen=True, slots=True)
class CrisisCommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool


CRISIS_ASSURANCE_EVENTS = {
    "crisis.reserve_target_proposed": (ExposureEffect.REQUEST, False),
    "crisis.reserve_target_retired": (ExposureEffect.REVOKE, True),
    "crisis.reserve_target_approved": (ExposureEffect.APPROVE, True),
    "crisis.reserve_snapshot_recorded": (ExposureEffect.RECORD, True),
    "crisis.mandate_proposed": (ExposureEffect.REQUEST, False),
    "crisis.mandate_activated": (ExposureEffect.APPROVE, True),
    "crisis.mandate_reviewed": (ExposureEffect.DECIDE, True),
    "crisis.rationing_rule_proposed": (ExposureEffect.REQUEST, False),
    "crisis.rationing_rule_retired": (ExposureEffect.REVOKE, True),
    "crisis.rationing_rule_approved": (ExposureEffect.APPROVE, True),
    "crisis.rationing_previewed": (ExposureEffect.RECORD, False),
    "crisis.rationing_confirmed": (ExposureEffect.RESERVE, True),
    "crisis.rationing_cancelled": (ExposureEffect.RELEASE, True),
    "crisis.ration_issued": (ExposureEffect.EXECUTE, True),
    "crisis.paper_form_issued": (ExposureEffect.CREATE, False),
    "crisis.paper_form_recorded": (ExposureEffect.RECORD, True),
    "crisis.mandate_expired": (ExposureEffect.CLOSE, True),
    "crisis.mandate_closed": (ExposureEffect.CLOSE, True),
}


def crisis_command_assurance(
    *,
    principal: Principal,
    actor: ActorClaim,
    event_type: str,
    subject_type: str,
    subject_id: UUID,
    command_record: IdempotencyRecord | None = None,
    evidence_refs: Sequence[object] = (),
    next_member_ids: Sequence[UUID | None] = (),
    attester_member_ids: Sequence[UUID | None] = (),
    amount: Decimal | None = None,
    unit: str | None = None,
) -> CommandAssurance:
    mapped = CRISIS_ASSURANCE_EVENTS.get(event_type)
    if mapped is None or actor.organization_id is None:
        raise crisis_error("COMMAND_ASSURANCE_EVENT_UNMAPPED", 500)
    effect, is_decision = mapped
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
        if member_id is None:
            continue
        if member_id in seen_members:
            continue
        seen_members.add(member_id)
        next_parties.append(member_party(member_id))
    attesters = [
        member_party(member_id)
        for member_id in dict.fromkeys(attester_member_ids)
        if member_id is not None
    ]
    if not is_decision:
        attesters.append(actor_ref)
    return CommandAssurance(
        on_behalf_of=cooperative,
        exposure=ExposureClaim(
            category=ExposureCategory.CRISIS,
            effect=effect,
            subject_type=subject_type,
            subject_id=subject_id,
            amount=amount,
            unit=unit,
            basis_refs=tuple(basis_refs),
        ),
        evidence_refs=tuple(evidence),
        next_responsible=tuple(next_parties),
        attesters=tuple(attesters),
        approvers=(actor_ref,) if is_decision else (),
    )


async def begin_crisis_command(
    session: AsyncSession,
    principal: Principal,
    operation: str,
    idempotency_key: str,
    payload: object,
) -> tuple[IdempotencyRecord, CrisisCommandResult | None]:
    record = await IdempotencyRepository(session).begin(
        actor_user_id=principal.user_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_payload_hash(payload),
    )
    if record.status == "COMPLETED":
        stored = record.response_payload or {}
        return record, CrisisCommandResult(
            event_id=UUID(str(stored["event_id"])),
            object_id=UUID(str(stored["object_id"])),
            replayed=True,
        )
    return record, None


def complete_crisis_command(
    record: IdempotencyRecord, event_id: UUID, object_id: UUID
) -> CrisisCommandResult:
    IdempotencyRepository.complete(
        record,
        response_status=201,
        response_payload={"event_id": str(event_id), "object_id": str(object_id)},
    )
    return CrisisCommandResult(event_id, object_id, False)


async def crisis_role_actor(
    session: AsyncSession,
    principal: Principal,
    cooperative_id: UUID,
    roles: set[RoleCode],
) -> ActorClaim:
    if principal.member_id is None:
        raise crisis_error("PERSONAL_ACTOR_REQUIRED", 403)
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
        raise crisis_error("ACTOR_NOT_ACTIVE", 403)
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
    raise crisis_error("AUTHORIZATION_DENIED", 403)


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


async def audit_crisis_action(
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
