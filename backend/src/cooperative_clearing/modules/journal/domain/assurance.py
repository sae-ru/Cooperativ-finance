"""Typed assurance contract for economically critical signed events."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ExposureCategory(StrEnum):
    COMMODITY = "COMMODITY"
    OBLIGATION = "OBLIGATION"
    SHARE = "SHARE"
    SOLIDARITY = "SOLIDARITY"
    NODE = "NODE"
    IDENTITY = "IDENTITY"
    AUTHORITY = "AUTHORITY"
    CUSTODY = "CUSTODY"
    GOVERNANCE = "GOVERNANCE"
    SANCTION = "SANCTION"
    REPUTATION = "REPUTATION"
    CRISIS = "CRISIS"


class ExposureEffect(StrEnum):
    REQUEST = "REQUEST"
    CREATE = "CREATE"
    APPROVE = "APPROVE"
    RESERVE = "RESERVE"
    HOLD = "HOLD"
    TRANSFER = "TRANSFER"
    REDUCE = "REDUCE"
    RELEASE = "RELEASE"
    REJECT = "REJECT"
    REVOKE = "REVOKE"
    EXECUTE = "EXECUTE"
    FINALIZE = "FINALIZE"
    DECIDE = "DECIDE"
    RECORD = "RECORD"
    CORRECT = "CORRECT"
    CLOSE = "CLOSE"


class AccountabilityPartyKind(StrEnum):
    MEMBER = "MEMBER"
    COOPERATIVE = "COOPERATIVE"
    NODE = "NODE"


@dataclass(frozen=True, slots=True)
class AccountabilityParty:
    kind: AccountabilityPartyKind
    reference: str
    role_assignment_id: UUID | None = None


class ActorIdentity(Protocol):
    @property
    def person_id(self) -> UUID: ...

    @property
    def organization_id(self) -> UUID | None: ...

    @property
    def role_assignment_id(self) -> UUID: ...


def actor_party(actor: ActorIdentity) -> AccountabilityParty:
    if actor.organization_id is not None:
        return AccountabilityParty(
            kind=AccountabilityPartyKind.COOPERATIVE,
            reference=str(actor.organization_id),
            role_assignment_id=actor.role_assignment_id,
        )
    return AccountabilityParty(
        kind=AccountabilityPartyKind.MEMBER,
        reference=str(actor.person_id),
        role_assignment_id=actor.role_assignment_id,
    )


def member_party(
    member_id: UUID,
    role_assignment_id: UUID | None = None,
) -> AccountabilityParty:
    return AccountabilityParty(
        kind=AccountabilityPartyKind.MEMBER,
        reference=str(member_id),
        role_assignment_id=role_assignment_id,
    )


def node_party(
    node_reference: UUID | str,
    role_assignment_id: UUID | None = None,
) -> AccountabilityParty:
    return AccountabilityParty(
        kind=AccountabilityPartyKind.NODE,
        reference=str(node_reference),
        role_assignment_id=role_assignment_id,
    )


@dataclass(frozen=True, slots=True)
class ExposureClaim:
    category: ExposureCategory
    effect: ExposureEffect
    subject_type: str
    subject_id: UUID
    amount: Decimal | None = None
    unit: str | None = None
    maximum_loss: Decimal | None = None
    basis_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandAssurance:
    on_behalf_of: AccountabilityParty
    exposure: ExposureClaim
    evidence_refs: tuple[object, ...]
    next_responsible: tuple[AccountabilityParty, ...]
    attesters: tuple[AccountabilityParty, ...] = ()
    approvers: tuple[AccountabilityParty, ...] = ()


CRITICAL_EVENT_TYPES = frozenset(
    {
        "inventory.quantity_reserved",
        "rights.commodity_right_issued",
        "rights.commodity_right_transferred",
        "rights.commodity_right_redeemed",
        "obligations.fulfillment_recorded",
        "obligations.fulfillment_provenance_reconciled",
        "obligations.fulfillment_accepted",
        "obligations.obligation_cleared",
        "clearing.cycle_finalized",
        "clearing.cycle_reconciled",
        "shares.contribution_recorded",
        "shares.exposure_reserved",
        "shares.exposure_cancelled",
        "shares.exposure_released",
        "liability.compensation_authorized",
        "liability.compensation_settled",
        "liability.compensation_voided",
        "solidarity.allocation_approved",
        "solidarity.aid_delivered",
        "federation.node_application_created",
        "federation.node_responsibility_accepted",
        "federation.node_application_submitted",
        "federation.node_identity_verified",
        "federation.node_challenge_issued",
        "federation.node_challenge_passed",
        "federation.node_audit_approved",
        "federation.node_application_rejected",
        "federation.trust_contract_proposed",
        "federation.trust_contract_activated",
        "federation.bilateral_limit_proposed",
        "federation.bilateral_limit_activated",
        "federation.node_bond_activated",
        "federation.node_activated",
        "federation.node_suspended",
        "federation.node_quarantined",
        "federation.node_revoked",
        "federation.node_incident_opened",
        "federation.node_incident_resolved",
        "federation.node_key_rotation_requested",
        "federation.node_key_rotated",
        "federation.node_key_rotation_rejected",
        "federation.node_rehabilitated_limited",
        "federation.offline_epoch_opened",
        "federation.offline_epoch_closed",
        "federation.node_exposure_reserved",
        "federation.clearing_node_prepared",
        "federation.clearing_commit_certified",
        "federation.clearing_certificate_applied",
        "federation.clearing_reconciled",
        "identity.account_recovery_requested",
        "identity.account_recovery_executed",
        "identity.account_recovery_rejected",
        "identity.break_glass_requested",
        "identity.break_glass_activated",
        "identity.break_glass_rejected",
        "identity.break_glass_revoked",
        "identity.role_assignment_requested",
        "identity.role_assignment_activated",
        "identity.role_assignment_approved",
        "identity.role_assignment_rejected",
        "identity.role_assignment_revoked",
        "identity.participant_address_created",
        "identity.participant_address_updated",
        "identity.participant_address_archived",
        "responsibility.custody_continuity_started",
        "responsibility.custody_hold_applied",
        "responsibility.custody_continuity_blocked",
        "responsibility.temporary_custodian_approved",
        "responsibility.custody_continuity_rejected",
        "responsibility.emergency_custody_accepted",
        "responsibility.emergency_custody_transferred",
        "responsibility.temporary_custodian_declined",
        "responsibility.custody_hold_released",
        "trust.policy_proposed",
        "trust.policy_superseded",
        "trust.policy_approved",
        "disputes.dispute_opened",
        "disputes.response_recorded",
        "disputes.case_ready_for_decision",
        "disputes.conflict_declared",
        "disputes.decision_issued",
        "sanctions.protective_measure_imposed",
        "sanctions.protective_measure_lifted",
        "sanctions.protective_measure_revoked",
        "sanctions.sanction_proposed",
        "sanctions.sanction_finalized",
        "sanctions.sanction_revoked",
        "appeals.appeal_submitted",
        "appeals.appeal_decided",
        "reputation.event_recorded",
        "reputation.event_activated",
        "reputation.event_corrected",
        "reputation.rehabilitation_recorded",
        "rehabilitation.plan_created",
        "rehabilitation.step_completed",
        "rehabilitation.plan_completed",
        "rehabilitation.plan_cancelled",
        "crisis.reserve_target_proposed",
        "crisis.reserve_target_retired",
        "crisis.reserve_target_approved",
        "crisis.reserve_snapshot_recorded",
        "crisis.mandate_proposed",
        "crisis.mandate_activated",
        "crisis.mandate_reviewed",
        "crisis.rationing_rule_proposed",
        "crisis.rationing_rule_retired",
        "crisis.rationing_rule_approved",
        "crisis.rationing_previewed",
        "crisis.rationing_confirmed",
        "crisis.rationing_cancelled",
        "crisis.ration_issued",
        "crisis.paper_form_issued",
        "crisis.paper_form_recorded",
        "crisis.mandate_expired",
        "crisis.mandate_closed",
    }
)
