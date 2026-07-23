"""Relational model for bounded crisis mandates, reserves, and rationing."""

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


class ReserveTarget(Base):
    __tablename__ = "reserve_targets"
    __table_args__ = (
        CheckConstraint("version >= 1 AND policy_version >= 1", name="versions_positive"),
        CheckConstraint("target_quantity > 0", name="target_positive"),
        CheckConstraint(
            "critical_minimum >= 0 AND critical_minimum <= target_quantity",
            name="critical_minimum_bounded",
        ),
        CheckConstraint(
            "warning_coverage_days >= critical_coverage_days AND critical_coverage_days >= 0",
            name="coverage_thresholds_ordered",
        ),
        CheckConstraint("max_snapshot_age_hours BETWEEN 1 AND 720", name="snapshot_age_bounded"),
        CheckConstraint("status IN ('DRAFT','ACTIVE','RETIRED')", name="status_allowed"),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        CheckConstraint(
            "(status = 'DRAFT' AND approved_event_id IS NULL) OR "
            "(status IN ('ACTIVE','RETIRED') AND approved_event_id IS NOT NULL)",
            name="approval_consistent",
        ),
        UniqueConstraint(
            "cooperative_id", "resource_code", "policy_version", name="uq_reserve_target_policy"
        ),
        Index("ix_reserve_targets_active", "cooperative_id", "resource_code", "status"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    resource_code: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(24), nullable=False)
    target_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    critical_minimum: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    warning_coverage_days: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    critical_coverage_days: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    max_snapshot_age_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    terms_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(71), nullable=False)
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


class ReserveSnapshot(Base):
    __tablename__ = "reserve_snapshots"
    __table_args__ = (
        CheckConstraint("physical_verified_quantity >= 0", name="verified_nonnegative"),
        CheckConstraint("committed_quantity >= 0", name="committed_nonnegative"),
        CheckConstraint(
            "available_quantity = physical_verified_quantity - committed_quantity "
            "AND available_quantity >= 0",
            name="available_consistent",
        ),
        CheckConstraint("consumption_rate_per_day >= 0", name="consumption_nonnegative"),
        CheckConstraint("coverage_days IS NULL OR coverage_days >= 0", name="coverage_nonnegative"),
        CheckConstraint(
            "expiring_quantity >= 0 AND expiring_quantity <= physical_verified_quantity",
            name="expiring_bounded",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_bounded"),
        CheckConstraint(
            "quality_status IN ('ACCEPTED','DEGRADED','REJECTED')", name="quality_allowed"
        ),
        CheckConstraint(
            "reserve_level IN ('UNKNOWN','NORMAL','WARNING','CRITICAL')", name="level_allowed"
        ),
        CheckConstraint("snapshot_hash ~ '^sha256:[0-9a-f]{64}$'", name="snapshot_hash_sha256"),
        CheckConstraint("jsonb_array_length(evidence_ids) > 0", name="evidence_required"),
        Index("ix_reserve_snapshots_target_observed", "target_id", "observed_at"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    target_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.reserve_targets.id", ondelete="RESTRICT")
    )
    physical_verified_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    committed_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    available_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    consumption_rate_per_day: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    coverage_days: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    expiring_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(8, 7), nullable=False)
    reserve_level: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    recorded_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    recorded_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    recorded_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    recorded_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CrisisMandate(Base):
    __tablename__ = "crisis_mandates"
    __table_args__ = (
        CheckConstraint("version >= 1 AND policy_version >= 1", name="versions_positive"),
        CheckConstraint(
            "starts_at < review_at AND review_at <= expires_at AND expires_at <= maximum_end_at",
            name="period_ordered",
        ),
        CheckConstraint("status IN ('DRAFT','ACTIVE','CLOSED','EXPIRED')", name="status_allowed"),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        CheckConstraint("jsonb_array_length(capabilities) > 0", name="capability_required"),
        CheckConstraint("jsonb_array_length(evidence_ids) > 0", name="evidence_required"),
        CheckConstraint(
            "(status = 'DRAFT' AND activated_event_id IS NULL AND closed_event_id IS NULL) OR "
            "(status = 'ACTIVE' AND activated_event_id IS NOT NULL AND closed_event_id IS NULL) OR "
            "(status IN ('CLOSED','EXPIRED') AND activated_event_id IS NOT NULL "
            "AND closed_event_id IS NOT NULL)",
            name="lifecycle_consistent",
        ),
        UniqueConstraint("cooperative_id", "mandate_code", name="uq_crisis_mandate_code"),
        Index("ix_crisis_mandates_status", "cooperative_id", "status"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    mandate_code: Mapped[str] = mapped_column(String(64), nullable=False)
    crisis_type: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    exit_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    safe_state: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    maximum_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terms_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(71), nullable=False)
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
    activated_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    activated_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    activated_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    activated_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    closed_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    closed_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    closed_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class CrisisReview(Base):
    __tablename__ = "crisis_reviews"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('CONTINUE','EXTEND','CLOSE','EXPIRE')", name="decision_allowed"
        ),
        CheckConstraint(
            "new_review_at IS NULL OR new_review_at < new_expires_at", name="new_period_ordered"
        ),
        UniqueConstraint("mandate_id", "decision_round", name="uq_crisis_review_round"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    mandate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.crisis_mandates.id", ondelete="RESTRICT")
    )
    decision_round: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    facts_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    previous_review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    new_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    reviewer_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    reviewer_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class RationingRule(Base):
    __tablename__ = "rationing_rules"
    __table_args__ = (
        CheckConstraint("version >= 1 AND policy_version >= 1", name="versions_positive"),
        CheckConstraint(
            "protected_minimum >= 0 AND maximum_per_member > 0 "
            "AND protected_minimum <= maximum_per_member",
            name="limits_ordered",
        ),
        CheckConstraint("period_hours BETWEEN 1 AND 720", name="period_bounded"),
        CheckConstraint(
            "formula IN ('EQUAL_PER_MEMBER','WEIGHTED_PRIORITY')", name="formula_allowed"
        ),
        CheckConstraint("status IN ('DRAFT','ACTIVE','RETIRED')", name="status_allowed"),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        UniqueConstraint(
            "mandate_id", "target_id", "policy_version", name="uq_rationing_rule_policy"
        ),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    mandate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.crisis_mandates.id", ondelete="RESTRICT")
    )
    target_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.reserve_targets.id", ondelete="RESTRICT")
    )
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    formula: Mapped[str] = mapped_column(String(32), nullable=False)
    eligibility_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    protected_minimum: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    maximum_per_member: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    period_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    terms_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(71), nullable=False)
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


class RationingPlan(Base):
    __tablename__ = "rationing_plans"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "available_input >= 0 AND total_allocated >= 0 AND total_allocated <= available_input",
            name="totals_bounded",
        ),
        CheckConstraint("eligible_count > 0", name="eligible_count_positive"),
        CheckConstraint(
            "status IN ('PREVIEWED','CONFIRMED','CANCELLED','EXPIRED')", name="status_allowed"
        ),
        CheckConstraint("input_hash ~ '^sha256:[0-9a-f]{64}$'", name="input_hash_sha256"),
        CheckConstraint(
            "allocations_hash ~ '^sha256:[0-9a-f]{64}$'", name="allocations_hash_sha256"
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_rationing_plans_rule_status", "rule_id", "status"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    rule_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.rationing_rules.id", ondelete="RESTRICT")
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.reserve_snapshots.id", ondelete="RESTRICT")
    )
    available_input: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    eligible_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_allocated: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    eligibility_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    allocations_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    proposed_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    proposed_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    proposed_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    preview_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    confirmed_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    confirmed_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    confirmed_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    confirmed_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class RationingAllocation(Base):
    __tablename__ = "rationing_allocations"
    __table_args__ = (
        CheckConstraint("weight BETWEEN 1 AND 100", name="weight_bounded"),
        CheckConstraint("quantity >= 0", name="quantity_nonnegative"),
        CheckConstraint(
            "status IN ('PROPOSED','RESERVED','ISSUED','CANCELLED')", name="status_allowed"
        ),
        UniqueConstraint("plan_id", "member_id", name="uq_rationing_allocation_member"),
        Index("ix_rationing_allocations_member", "member_id", "status"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    plan_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.rationing_plans.id", ondelete="RESTRICT")
    )
    member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RationIssuance(Base):
    __tablename__ = "ration_issuances"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("jsonb_array_length(evidence_ids) > 0", name="evidence_required"),
        UniqueConstraint("allocation_id", name="uq_ration_issuance_allocation"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    allocation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("solidarity.rationing_allocations.id", ondelete="RESTRICT"),
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    acknowledgement: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    issued_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    issued_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    issued_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CrisisPaperForm(Base):
    __tablename__ = "crisis_paper_forms"
    __table_args__ = (
        CheckConstraint(
            "form_type IN ('RESERVE_SNAPSHOT','RATION_ISSUANCE','INCIDENT','EXCEPTION')",
            name="type_allowed",
        ),
        CheckConstraint("status IN ('ISSUED','RECORDED','VOID','EXPIRED')", name="status_allowed"),
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
        CheckConstraint("checksum ~ '^[0-9A-F]{8}$'", name="checksum_format"),
        CheckConstraint(
            "payload_hash IS NULL OR payload_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="payload_hash_sha256",
        ),
        UniqueConstraint("cooperative_id", "serial_number", name="uq_crisis_paper_form_serial"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    mandate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.crisis_mandates.id", ondelete="RESTRICT")
    )
    serial_number: Mapped[str] = mapped_column(String(64), nullable=False)
    checksum: Mapped[str] = mapped_column(String(8), nullable=False)
    form_type: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_to_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    payload_hash: Mapped[str | None] = mapped_column(String(71))
    issued_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    issued_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    issued_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    issued_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    recorded_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    recorded_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    recorded_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CrisisReport(Base):
    __tablename__ = "crisis_reports"
    __table_args__ = (
        CheckConstraint("report_hash ~ '^sha256:[0-9a-f]{64}$'", name="report_hash_sha256"),
        UniqueConstraint("mandate_id", name="uq_crisis_report_mandate"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    mandate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.crisis_mandates.id", ondelete="RESTRICT")
    )
    report_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    report_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    generated_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
