"""Persistence for explicit policy, share exposure, and liability cases."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from cooperative_clearing.shared.infrastructure.orm import Base


class RiskPolicy(Base):
    __tablename__ = "risk_policies"
    __table_args__ = (
        CheckConstraint("policy_version >= 1 AND version >= 1", name="versions_positive"),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        CheckConstraint(
            "max_member_exposure > 0 AND max_related_exposure > 0 "
            "AND max_related_exposure >= max_member_exposure",
            name="limits_positive",
        ),
        CheckConstraint("max_guarantee_chain_depth BETWEEN 1 AND 20", name="chain_depth_bounded"),
        CheckConstraint(
            "status IN ('PROPOSED','ACTIVE','REJECTED','SUPERSEDED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "(status = 'PROPOSED' AND approved_event_id IS NULL AND approved_at IS NULL) OR "
            "(status IN ('ACTIVE','SUPERSEDED') AND approved_event_id IS NOT NULL "
            "AND approved_at IS NOT NULL) OR status = 'REJECTED'",
            name="approval_consistent",
        ),
        UniqueConstraint(
            "cooperative_id", "policy_version", name="uq_risk_policies_cooperative_version"
        ),
        Index(
            "uq_risk_policies_active_denomination",
            "cooperative_id",
            "denomination",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        {"schema": "risk"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    denomination: Mapped[str] = mapped_column(String(32), nullable=False)
    max_member_exposure: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    max_related_exposure: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    max_guarantee_chain_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    terms_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    proposed_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    proposed_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    proposed_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    proposed_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    approved_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    approved_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    approved_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ShareAccount(Base):
    __tablename__ = "share_accounts"
    __table_args__ = (
        CheckConstraint(
            "contour IN ('PRIMARY','GUARANTEE','ROLE','INFRASTRUCTURE','SOLIDARITY')",
            name="contour_allowed",
        ),
        CheckConstraint("status IN ('ACTIVE','FROZEN','CLOSED')", name="status_allowed"),
        CheckConstraint(
            "balance >= 0 AND protected_amount >= 0 AND executed_not_settled >= 0 "
            "AND protected_amount + executed_not_settled <= balance",
            name="amounts_bounded",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint(
            "cooperative_id",
            "member_id",
            "contour",
            "denomination",
            name="uq_share_accounts_member_contour_denomination",
        ),
        Index("ix_share_accounts_member_status", "member_id", "status"),
        {"schema": "risk"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    opening_policy_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("risk.risk_policies.id", ondelete="RESTRICT")
    )
    contour: Mapped[str] = mapped_column(String(24), nullable=False)
    denomination: Mapped[str] = mapped_column(String(32), nullable=False)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, server_default=text("0")
    )
    protected_amount: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, server_default=text("0")
    )
    executed_not_settled: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    last_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ShareContribution(Base):
    __tablename__ = "share_contributions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("entry_type = 'CONTRIBUTION'", name="entry_type_allowed"),
        Index("ix_share_contributions_account_created", "account_id", "created_at"),
        {"schema": "risk"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("risk.share_accounts.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(300), nullable=False)
    recorded_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class RelatedPartyLink(Base):
    __tablename__ = "related_party_links"
    __table_args__ = (
        CheckConstraint("member_a_id < member_b_id", name="members_ordered_distinct"),
        CheckConstraint(
            "relation_type IN ('HOUSEHOLD','CONTROL','RELATED')", name="relation_allowed"
        ),
        CheckConstraint(
            "status IN ('PENDING_APPROVAL','ACTIVE','REJECTED','ENDED')",
            name="status_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_related_party_links_open_pair",
            "cooperative_id",
            "member_a_id",
            "member_b_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING_APPROVAL','ACTIVE')"),
        ),
        Index("ix_related_party_links_member_a_status", "member_a_id", "status"),
        Index("ix_related_party_links_member_b_status", "member_b_id", "status"),
        {"schema": "risk"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    member_a_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    member_b_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    relation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    proposed_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    proposed_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    proposed_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    decided_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    decision_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ExposureCommitment(Base):
    __tablename__ = "exposure_commitments"
    __table_args__ = (
        CheckConstraint(
            "commitment_type IN ('DIRECT_OBLIGATION','GUARANTEE','CREDIT_LIMIT','ROLE_BOND')",
            name="type_allowed",
        ),
        CheckConstraint(
            "status IN ('PROPOSED','ACTIVE','RELEASED','CANCELLED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "amount_reserved > 0 AND max_loss > 0 AND max_loss <= amount_reserved "
            "AND executed_amount >= 0 AND executed_amount <= max_loss",
            name="amounts_bounded",
        ),
        CheckConstraint("coverage_ratio > 0 AND coverage_ratio <= 1", name="ratio_bounded"),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        CheckConstraint("starts_at < expires_at", name="period_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "(commitment_type = 'GUARANTEE' AND debtor_member_id IS NOT NULL "
            "AND beneficiary_member_id IS NOT NULL) OR commitment_type <> 'GUARANTEE'",
            name="guarantee_parties_present",
        ),
        CheckConstraint(
            "(commitment_type = 'ROLE_BOND' AND role_assignment_id IS NOT NULL) "
            "OR commitment_type <> 'ROLE_BOND'",
            name="role_bond_assignment_present",
        ),
        CheckConstraint(
            "(status = 'PROPOSED' AND accepted_event_id IS NULL AND released_event_id IS NULL) OR "
            "(status = 'ACTIVE' AND accepted_event_id IS NOT NULL AND released_event_id IS NULL) "
            "OR (status IN ('RELEASED','CANCELLED') AND released_event_id IS NOT NULL)",
            name="lifecycle_consistent",
        ),
        Index("ix_exposure_commitments_owner_status", "owner_member_id", "status"),
        Index("ix_exposure_commitments_subject_status", "risk_id", "status"),
        Index(
            "uq_exposure_commitments_active_risk_account",
            "account_id",
            "risk_type",
            "risk_id",
            unique=True,
            postgresql_where=text("status IN ('PROPOSED','ACTIVE')"),
        ),
        {"schema": "risk"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    policy_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("risk.risk_policies.id", ondelete="RESTRICT")
    )
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("risk.share_accounts.id", ondelete="RESTRICT")
    )
    owner_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    commitment_type: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_type: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    debtor_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    beneficiary_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    amount_reserved: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    max_loss: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    executed_amount: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, server_default=text("0")
    )
    coverage_ratio: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    release_condition: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_conditions: Mapped[str] = mapped_column(Text, nullable=False)
    exclusions: Mapped[str] = mapped_column(Text, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    terms_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    proposed_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    proposed_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    proposed_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    proposed_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    accepted_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    accepted_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    accepted_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    released_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    released_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    release_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class LiabilityCase(Base):
    __tablename__ = "liability_cases"
    __table_args__ = (
        CheckConstraint("affected_amount > 0", name="affected_positive"),
        CheckConstraint("assessed_loss IS NULL OR assessed_loss >= 0", name="loss_nonnegative"),
        CheckConstraint("status IN ('OPEN','ASSESSED','CLOSED')", name="status_allowed"),
        CheckConstraint(
            "fault_class IS NULL OR fault_class IN "
            "('FORCE_MAJEURE','GOOD_FAITH_ERROR','NEGLIGENCE','GROSS_NEGLIGENCE',"
            "'INTENT','COLLUSION')",
            name="fault_allowed",
        ),
        CheckConstraint(
            "(status = 'OPEN' AND assessed_event_id IS NULL AND assessed_loss IS NULL) OR "
            "(status IN ('ASSESSED','CLOSED') AND assessed_event_id IS NOT NULL "
            "AND assessed_loss IS NOT NULL AND fault_class IS NOT NULL)",
            name="assessment_consistent",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint(
            "cooperative_id",
            "incident_reference",
            name="uq_liability_cases_cooperative_incident",
        ),
        Index("ix_liability_cases_responsible_status", "responsible_member_id", "status"),
        {"schema": "risk"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    commitment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("risk.exposure_commitments.id", ondelete="RESTRICT")
    )
    incident_reference: Mapped[str] = mapped_column(String(80), nullable=False)
    responsible_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    affected_amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    facts: Mapped[str] = mapped_column(Text, nullable=False)
    causal_graph: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    opened_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    opened_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    opened_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    fault_class: Mapped[str | None] = mapped_column(String(32))
    assessed_loss: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    coverage_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    assessment_rationale: Mapped[str | None] = mapped_column(Text)
    assessed_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    assessed_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    assessed_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    appeal_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class CompensationTransfer(Base):
    __tablename__ = "compensation_transfers"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "status IN ('PENDING_ACCEPTANCE','SETTLED','VOIDED')",
            name="status_allowed",
        ),
        CheckConstraint("source_account_id <> destination_account_id", name="accounts_distinct"),
        CheckConstraint("responsible_member_id <> recipient_member_id", name="members_distinct"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "jsonb_typeof(authorization_evidence_refs) = 'array' "
            "AND jsonb_array_length(authorization_evidence_refs) >= 1",
            name="authorization_evidence_nonempty",
        ),
        CheckConstraint(
            "void_evidence_refs IS NULL OR jsonb_typeof(void_evidence_refs) = 'array'",
            name="void_evidence_array",
        ),
        CheckConstraint(
            "(status = 'PENDING_ACCEPTANCE' AND accepted_event_id IS NULL "
            "AND voided_event_id IS NULL AND accepted_by_user_id IS NULL "
            "AND voided_by_user_id IS NULL AND source_balance_before IS NULL "
            "AND source_balance_after IS NULL AND destination_balance_before IS NULL "
            "AND destination_balance_after IS NULL) OR "
            "(status = 'SETTLED' AND accepted_event_id IS NOT NULL "
            "AND accepted_by_user_id IS NOT NULL AND accepted_by_member_id IS NOT NULL "
            "AND accepted_role_assignment_id IS NOT NULL AND accepted_at IS NOT NULL "
            "AND voided_event_id IS NULL AND source_balance_before IS NOT NULL "
            "AND source_balance_after IS NOT NULL AND destination_balance_before IS NOT NULL "
            "AND destination_balance_after IS NOT NULL) OR "
            "(status = 'VOIDED' AND voided_event_id IS NOT NULL "
            "AND voided_by_user_id IS NOT NULL AND voided_by_member_id IS NOT NULL "
            "AND voided_role_assignment_id IS NOT NULL AND voided_at IS NOT NULL "
            "AND accepted_event_id IS NULL AND source_balance_before IS NULL "
            "AND source_balance_after IS NULL AND destination_balance_before IS NULL "
            "AND destination_balance_after IS NULL)",
            name="lifecycle_consistent",
        ),
        CheckConstraint(
            "status <> 'SETTLED' OR "
            "(source_balance_before - amount = source_balance_after "
            "AND destination_balance_before + amount = destination_balance_after)",
            name="settlement_balanced",
        ),
        Index("ix_compensation_transfers_cooperative_status", "cooperative_id", "status"),
        Index("ix_compensation_transfers_recipient_status", "recipient_member_id", "status"),
        Index(
            "uq_compensation_transfers_active_case",
            "liability_case_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING_ACCEPTANCE','SETTLED')"),
        ),
        {"schema": "risk"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    liability_case_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("risk.liability_cases.id", ondelete="RESTRICT")
    )
    trust_case_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.cases.id", ondelete="RESTRICT")
    )
    trust_decision_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trust.arbitration_decisions.id", ondelete="RESTRICT"),
        unique=True,
    )
    commitment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("risk.exposure_commitments.id", ondelete="RESTRICT")
    )
    source_account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("risk.share_accounts.id", ondelete="RESTRICT")
    )
    destination_account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("risk.share_accounts.id", ondelete="RESTRICT")
    )
    responsible_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    recipient_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    denomination: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    authorization_evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False
    )
    authorized_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    authorized_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    authorized_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    authorized_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    source_account_version_before: Mapped[int] = mapped_column(Integer, nullable=False)
    destination_account_version_at_authorization: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    commitment_version_before: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    accepted_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    accepted_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    accepted_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    voided_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    voided_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    voided_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    voided_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    void_reason: Mapped[str | None] = mapped_column(Text)
    void_evidence_refs: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    source_balance_before: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    source_balance_after: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    destination_balance_before: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    destination_balance_after: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    authorized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

class AntifraudScan(Base):
    __tablename__ = "antifraud_scans"
    __table_args__ = (
        CheckConstraint("lookback_hours BETWEEN 1 AND 2160", name="lookback_bounded"),
        CheckConstraint("finding_count >= 0", name="finding_count_nonnegative"),
        CheckConstraint(
            "rule_manifest_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="rule_manifest_hash_sha256",
        ),
        Index("ix_antifraud_scans_cooperative_created", "cooperative_id", "created_at"),
        {"schema": "risk"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_manifest_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    calibration_dataset_version: Mapped[str] = mapped_column(String(40), nullable=False)
    lookback_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    input_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    result_summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    initiated_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    initiated_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    initiated_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    completed_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AntifraudSignal(Base):
    __tablename__ = "antifraud_signals"
    __table_args__ = (
        CheckConstraint("rule_version >= 1 AND version >= 1", name="versions_positive"),
        CheckConstraint("occurrence_count >= 1", name="occurrence_count_positive"),
        CheckConstraint(
            "subject_type IN ('MEMBER','OFFER','LOGISTICS_QUOTE','PURCHASE_INTENT',"
            "'SHARE_ACCOUNT','EXPOSURE_COMMITMENT')",
            name="subject_type_allowed",
        ),
        CheckConstraint("severity IN ('LOW','MEDIUM','HIGH','CRITICAL')", name="severity_allowed"),
        CheckConstraint("automation_action IN ('WARN','HOLD')", name="action_allowed"),
        CheckConstraint(
            "status IN ('OPEN','IN_REVIEW','CLEARED','CONFIRMED')", name="status_allowed"
        ),
        CheckConstraint("dedupe_key ~ '^sha256:[0-9a-f]{64}$'", name="dedupe_key_sha256"),
        CheckConstraint("last_seen_at >= first_seen_at", name="seen_period_valid"),
        CheckConstraint(
            "reviewer_member_id IS NULL OR reviewer_member_id <> detected_by_member_id",
            name="reviewer_independent",
        ),
        CheckConstraint(
            "(status = 'OPEN' AND reviewer_user_id IS NULL AND reviewer_member_id IS NULL "
            "AND reviewer_role_assignment_id IS NULL AND review_started_event_id IS NULL "
            "AND decision_event_id IS NULL) OR "
            "(status = 'IN_REVIEW' AND reviewer_user_id IS NOT NULL "
            "AND reviewer_member_id IS NOT NULL AND reviewer_role_assignment_id IS NOT NULL "
            "AND review_started_event_id IS NOT NULL AND decision_event_id IS NULL) OR "
            "(status IN ('CLEARED','CONFIRMED') AND reviewer_user_id IS NOT NULL "
            "AND reviewer_member_id IS NOT NULL AND reviewer_role_assignment_id IS NOT NULL "
            "AND review_started_event_id IS NOT NULL AND decision_event_id IS NOT NULL "
            "AND decision_rationale IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="review_lifecycle_consistent",
        ),
        Index(
            "uq_antifraud_signals_active_subject",
            "cooperative_id",
            "rule_code",
            "subject_type",
            "subject_id",
            unique=True,
            postgresql_where=text("status IN ('OPEN','IN_REVIEW','CONFIRMED')"),
        ),
        Index(
            "ix_antifraud_signals_cooperative_status",
            "cooperative_id",
            "status",
            "severity",
            "last_seen_at",
        ),
        Index("ix_antifraud_signals_subject", "subject_type", "subject_id"),
        {"schema": "risk"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    scan_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("risk.antifraud_scans.id", ondelete="RESTRICT")
    )
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    automation_action: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_key: Mapped[str] = mapped_column(String(160), nullable=False)
    observed_data: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    threshold_data: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(71), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detected_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    detected_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    detected_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    detected_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    reviewer_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    reviewer_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    reviewer_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    review_started_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    decision_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    decision_rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
