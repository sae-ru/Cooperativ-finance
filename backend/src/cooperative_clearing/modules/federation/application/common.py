"""Transactional and actor helpers shared by federation commands."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.audit.infrastructure.repository import (
    AuditRepository,
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.federation.domain.types import federation_error
from cooperative_clearing.modules.federation.infrastructure.models import NodeResponsibleParty
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.journal.application.service import ActorClaim
from cooperative_clearing.modules.journal.domain.assurance import (
    CommandAssurance,
    ExposureCategory,
    ExposureClaim,
    ExposureEffect,
    actor_party,
    member_party,
    node_party,
)


@dataclass(frozen=True, slots=True)
class FederationCommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool


NODE_AUTHORITY_ASSURANCE_EVENTS = {
    "federation.node_application_created": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.REQUEST,
        False,
    ),
    "federation.node_responsibility_accepted": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.CREATE,
        False,
    ),
    "federation.node_application_submitted": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.REQUEST,
        False,
    ),
    "federation.node_identity_verified": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.APPROVE,
        True,
    ),
    "federation.node_challenge_issued": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.REQUEST,
        False,
    ),
    "federation.node_challenge_passed": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.APPROVE,
        True,
    ),
    "federation.node_audit_approved": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.APPROVE,
        True,
    ),
    "federation.node_application_rejected": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.REJECT,
        True,
    ),
    "federation.trust_contract_proposed": (
        ExposureCategory.NODE,
        ExposureEffect.REQUEST,
        False,
    ),
    "federation.trust_contract_activated": (
        ExposureCategory.NODE,
        ExposureEffect.APPROVE,
        True,
    ),
    "federation.bilateral_limit_proposed": (
        ExposureCategory.NODE,
        ExposureEffect.REQUEST,
        False,
    ),
    "federation.bilateral_limit_activated": (
        ExposureCategory.NODE,
        ExposureEffect.APPROVE,
        True,
    ),
    "federation.node_bond_activated": (
        ExposureCategory.NODE,
        ExposureEffect.HOLD,
        False,
    ),
    "federation.node_activated": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.APPROVE,
        True,
    ),
    "federation.node_suspended": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.HOLD,
        False,
    ),
    "federation.node_quarantined": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.HOLD,
        False,
    ),
    "federation.node_revoked": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.REVOKE,
        True,
    ),
    "federation.node_incident_opened": (
        ExposureCategory.NODE,
        ExposureEffect.HOLD,
        False,
    ),
    "federation.node_incident_resolved": (
        ExposureCategory.NODE,
        ExposureEffect.APPROVE,
        True,
    ),
    "federation.node_key_rotation_requested": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.REQUEST,
        False,
    ),
    "federation.node_key_rotated": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.APPROVE,
        True,
    ),
    "federation.node_key_rotation_rejected": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.REJECT,
        True,
    ),
    "federation.node_rehabilitated_limited": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.APPROVE,
        True,
    ),
    "federation.offline_epoch_opened": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.CREATE,
        False,
    ),
    "federation.offline_epoch_closed": (
        ExposureCategory.AUTHORITY,
        ExposureEffect.CLOSE,
        True,
    ),
    "federation.node_exposure_reserved": (
        ExposureCategory.NODE,
        ExposureEffect.RESERVE,
        False,
    ),
}


async def federation_command_assurance(
    session: AsyncSession,
    *,
    principal: Principal,
    actor: ActorClaim,
    local_node_reference: str,
    event_type: str,
    subject_type: str,
    subject_id: UUID,
    target_node_id: UUID,
    command_record: IdempotencyRecord | None = None,
    evidence_refs: Sequence[object] = (),
    next_member_ids: Sequence[UUID | None] = (),
    attester_user_ids: Sequence[UUID | None] = (),
    amount: Decimal | None = None,
    unit: str | None = None,
    maximum_loss: Decimal | None = None,
) -> CommandAssurance:
    mapped = NODE_AUTHORITY_ASSURANCE_EVENTS.get(event_type)
    if mapped is None:
        raise federation_error("COMMAND_ASSURANCE_EVENT_UNMAPPED", 500)
    category, effect, is_decision = mapped
    evidence: list[object] = [
        {"authenticated_session_id": str(principal.session_id)},
        {
            "event_subject": {
                "event_type": event_type,
                "subject_type": subject_type,
                "subject_id": str(subject_id),
                "target_node_id": str(target_node_id),
            }
        },
    ]
    basis_refs = [event_type, str(subject_id), str(target_node_id)]
    if command_record is not None:
        evidence.append({"idempotency_record_id": str(command_record.id)})
        basis_refs.append(command_record.request_hash)
    evidence.extend(evidence_refs)

    now = datetime.now(UTC)
    responsible_ids = list(
        (
            await session.execute(
                select(NodeResponsibleParty.member_id).where(
                    NodeResponsibleParty.node_id == target_node_id,
                    NodeResponsibleParty.status == "ACTIVE",
                    (NodeResponsibleParty.valid_until.is_(None))
                    | (NodeResponsibleParty.valid_until > now),
                )
            )
        ).scalars()
    )
    next_parties = [node_party(target_node_id)]
    for member_id in dict.fromkeys((*responsible_ids, *next_member_ids)):
        if member_id is not None:
            next_parties.append(member_party(member_id))

    attester_members: list[UUID] = []
    for user_id in dict.fromkeys(attester_user_ids):
        if user_id is None:
            continue
        user = await session.get(UserAccount, user_id)
        if user is not None and user.member_id is not None:
            attester_members.append(user.member_id)
    attesters = [member_party(member_id) for member_id in dict.fromkeys(attester_members)]
    actor_ref = actor_party(actor)
    if not is_decision:
        attesters.append(actor_ref)
    return CommandAssurance(
        on_behalf_of=node_party(local_node_reference),
        exposure=ExposureClaim(
            category=category,
            effect=effect,
            subject_type=subject_type,
            subject_id=subject_id,
            amount=amount,
            unit=unit,
            maximum_loss=maximum_loss,
            basis_refs=tuple(basis_refs),
        ),
        evidence_refs=tuple(evidence),
        next_responsible=tuple(next_parties),
        attesters=tuple(attesters),
        approvers=(actor_ref,) if is_decision else (),
    )


async def begin_federation_command(
    session: AsyncSession,
    principal: Principal,
    operation: str,
    idempotency_key: str,
    payload: object,
) -> tuple[IdempotencyRecord, FederationCommandResult | None]:
    record = await IdempotencyRepository(session).begin(
        actor_user_id=principal.user_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_payload_hash(payload),
    )
    if record.status == "COMPLETED":
        stored = record.response_payload or {}
        return record, FederationCommandResult(
            event_id=UUID(str(stored["event_id"])),
            object_id=UUID(str(stored["object_id"])),
            replayed=True,
        )
    return record, None


def complete_federation_command(
    record: IdempotencyRecord, event_id: UUID, object_id: UUID
) -> FederationCommandResult:
    IdempotencyRepository.complete(
        record,
        response_status=201,
        response_payload={"event_id": str(event_id), "object_id": str(object_id)},
    )
    return FederationCommandResult(event_id, object_id, False)


async def federation_actor(
    session: AsyncSession,
    principal: Principal,
    roles: set[RoleCode],
) -> ActorClaim:
    if principal.member_id is None:
        raise federation_error("PERSONAL_ACTOR_REQUIRED", 403)
    user = await session.get(UserAccount, principal.user_id)
    member = await session.get(Member, principal.member_id)
    if (
        user is None
        or user.status != "ACTIVE"
        or user.member_id != principal.member_id
        or member is None
        or member.status != "ACTIVE"
    ):
        raise federation_error("ACTOR_NOT_ACTIVE", 403)
    for grant in principal.roles:
        if grant.role not in roles:
            continue
        assignment = await session.get(RoleAssignment, grant.assignment_id)
        if (
            assignment is not None
            and assignment.status == "ACTIVE"
            and assignment.user_id == principal.user_id
            and assignment.role_code == grant.role.value
        ):
            return ActorClaim(
                person_id=principal.member_id,
                organization_id=assignment.cooperative_id,
                role_assignment_id=assignment.id,
            )
    raise federation_error("AUTHORIZATION_DENIED", 403)


async def audit_federation_action(
    session: AsyncSession,
    principal: Principal,
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
        actor_user_id=principal.user_id,
        outcome="SUCCESS",
        request_id=request_id,
        payload={"signed_event_id": str(event_id)},
    )
