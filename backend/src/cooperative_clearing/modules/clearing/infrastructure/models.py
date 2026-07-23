"""SQLAlchemy persistence for local clearing cycles and proofs."""

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


class ClearingPolicy(Base):
    __tablename__ = "clearing_policies"
    __table_args__ = (
        CheckConstraint("policy_version >= 1 AND version >= 1", name="versions_positive"),
        CheckConstraint("algorithm_id = 'LOCAL_NETTING'", name="algorithm_allowed"),
        CheckConstraint("algorithm_version = '1.0.0'", name="algorithm_version_allowed"),
        CheckConstraint("decimal_scale BETWEEN 0 AND 12", name="decimal_scale_allowed"),
        CheckConstraint("rounding_mode IN ('DOWN','HALF_EVEN')", name="rounding_allowed"),
        CheckConstraint("minimum_operation >= 0", name="minimum_nonnegative"),
        CheckConstraint("max_iterations BETWEEN 1 AND 100000", name="iterations_bounded"),
        CheckConstraint("max_cycle_length BETWEEN 3 AND 12", name="cycle_length_bounded"),
        CheckConstraint("required_approvals BETWEEN 1 AND 3", name="approvals_bounded"),
        CheckConstraint(
            "dispute_window_seconds BETWEEN 0 AND 2592000", name="dispute_window_bounded"
        ),
        CheckConstraint(
            "status IN ('PROPOSED','ACTIVE','REJECTED','SUPERSEDED')", name="status_allowed"
        ),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        UniqueConstraint(
            "cooperative_id", "policy_version", name="uq_clearing_policy_cooperative_version"
        ),
        Index("ix_clearing_policies_cooperative_status", "cooperative_id", "status"),
        Index(
            "uq_clearing_policy_active",
            "cooperative_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    valuation_unit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.units_of_measure.id", ondelete="RESTRICT")
    )
    algorithm_id: Mapped[str] = mapped_column(String(32), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(16), nullable=False)
    decimal_scale: Mapped[int] = mapped_column(Integer, nullable=False)
    rounding_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    minimum_operation: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    max_cycle_length: Mapped[int] = mapped_column(Integer, nullable=False)
    dispute_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    required_approvals: Mapped[int] = mapped_column(Integer, nullable=False)
    liquidity_order: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
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


class ClearingCycle(Base):
    __tablename__ = "clearing_cycles"
    __table_args__ = (
        CheckConstraint("period_end > period_start", name="period_ordered"),
        CheckConstraint("version >= 1 AND collected_count >= 0", name="version_count_valid"),
        CheckConstraint(
            "status IN ('DRAFT','COLLECTING','INPUT_FROZEN','PREVIEWED','DISPUTE_WINDOW',"
            "'DISPUTED','READY_TO_FINALIZE','FINALIZED','RECONCILED','CANCELLED',"
            "'FAILED_FINALIZATION','SUPERSEDED')",
            name="status_allowed",
        ),
        UniqueConstraint("cooperative_id", "cycle_code", name="uq_clearing_cycle_code"),
        Index("ix_clearing_cycles_cooperative_status", "cooperative_id", "status"),
        Index("ix_clearing_cycles_period", "period_start", "period_end"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    policy_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.clearing_policies.id", ondelete="RESTRICT")
    )
    cycle_code: Mapped[str] = mapped_column(String(80), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    collected_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    input_hash: Mapped[str | None] = mapped_column(String(71))
    parameters_hash: Mapped[str | None] = mapped_column(String(71))
    result_hash: Mapped[str | None] = mapped_column(String(71))
    dispute_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    created_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    created_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    collection_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    freeze_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    preview_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    ready_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    finalized_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    reconciled_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    previewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ClearingInputSnapshot(Base):
    __tablename__ = "clearing_input_snapshots"
    __table_args__ = (
        CheckConstraint("input_version >= 1", name="version_positive"),
        CheckConstraint("input_hash ~ '^sha256:[0-9a-f]{64}$'", name="input_hash_sha256"),
        UniqueConstraint("cycle_id", "input_version", name="uq_clearing_snapshot_cycle_version"),
        UniqueConstraint("cycle_id", name="uq_clearing_snapshot_cycle"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.clearing_cycles.id", ondelete="RESTRICT")
    )
    input_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    ordered_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    frozen_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    frozen_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    frozen_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ClearingEntry(Base):
    __tablename__ = "clearing_entries"
    __table_args__ = (
        CheckConstraint(
            "amount_before > 0 AND cleared_amount >= 0 AND amount_after >= 0 "
            "AND cleared_amount <= amount_before AND amount_after = amount_before - cleared_amount",
            name="amounts_bounded",
        ),
        CheckConstraint("obligation_version >= 1", name="obligation_version_positive"),
        CheckConstraint(
            "inclusion_status IN ('INCLUDED','EXCLUDED')", name="inclusion_status_allowed"
        ),
        UniqueConstraint("cycle_id", "obligation_id", name="uq_clearing_entry_obligation"),
        Index("ix_clearing_entries_cycle_member", "cycle_id", "debtor_member_id"),
        Index("ix_clearing_entries_cycle_creditor", "cycle_id", "creditor_member_id"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.clearing_cycles.id", ondelete="RESTRICT")
    )
    obligation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.obligations.id", ondelete="RESTRICT")
    )
    debtor_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    creditor_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    unit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.units_of_measure.id", ondelete="RESTRICT")
    )
    obligation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_before: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    cleared_amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    amount_after: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    inclusion_status: Mapped[str] = mapped_column(String(16), nullable=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(80))
    allocations: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ClearingPosition(Base):
    __tablename__ = "clearing_positions"
    __table_args__ = (
        CheckConstraint(
            "incoming_before >= 0 AND outgoing_before >= 0 AND incoming_cleared >= 0 "
            "AND outgoing_cleared >= 0 AND incoming_after >= 0 AND outgoing_after >= 0",
            name="amounts_nonnegative",
        ),
        UniqueConstraint("cycle_id", "member_id", "unit_id", name="uq_clearing_position"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.clearing_cycles.id", ondelete="RESTRICT")
    )
    member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    unit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.units_of_measure.id", ondelete="RESTRICT")
    )
    incoming_before: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    outgoing_before: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    incoming_cleared: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    outgoing_cleared: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    incoming_after: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    outgoing_after: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    net_before: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    net_after: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ClearingApproval(Base):
    __tablename__ = "clearing_approvals"
    __table_args__ = (
        CheckConstraint("approval_type = 'CONTROLLER'", name="type_allowed"),
        UniqueConstraint("cycle_id", "member_id", name="uq_clearing_approval_member"),
        UniqueConstraint("event_id", name="uq_clearing_approval_event"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.clearing_cycles.id", ondelete="RESTRICT")
    )
    approval_type: Mapped[str] = mapped_column(String(24), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ClearingDispute(Base):
    __tablename__ = "clearing_disputes"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN','UPHELD','REJECTED')", name="status_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_clearing_disputes_cycle_status", "cycle_id", "status"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.clearing_cycles.id", ondelete="RESTRICT")
    )
    entry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.clearing_entries.id", ondelete="RESTRICT")
    )
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    opened_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    opened_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    opened_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    resolved_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    resolution_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ClearingProof(Base):
    __tablename__ = "clearing_proofs"
    __table_args__ = (
        CheckConstraint("proof_hash ~ '^sha256:[0-9a-f]{64}$'", name="proof_hash_sha256"),
        UniqueConstraint("cycle_id", name="uq_clearing_proof_cycle"),
        UniqueConstraint("finalized_event_id", name="uq_clearing_proof_event"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.clearing_cycles.id", ondelete="RESTRICT")
    )
    proof_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    proof_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    finalized_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    node_event_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ClearingStatement(Base):
    __tablename__ = "clearing_statements"
    __table_args__ = (
        CheckConstraint("statement_hash ~ '^sha256:[0-9a-f]{64}$'", name="hash_sha256"),
        UniqueConstraint("cycle_id", "member_id", "unit_id", name="uq_clearing_statement"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.clearing_cycles.id", ondelete="RESTRICT")
    )
    member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    unit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.units_of_measure.id", ondelete="RESTRICT")
    )
    statement_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    statement_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ClearingAccountingExport(Base):
    __tablename__ = "clearing_accounting_exports"
    __table_args__ = (
        CheckConstraint("package_hash ~ '^sha256:[0-9a-f]{64}$'", name="hash_sha256"),
        UniqueConstraint("cycle_id", name="uq_clearing_accounting_export_cycle"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.clearing_cycles.id", ondelete="RESTRICT")
    )
    export_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    package_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
