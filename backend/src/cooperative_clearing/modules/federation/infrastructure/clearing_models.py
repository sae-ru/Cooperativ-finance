"""Persistence for inter-node clearing state and signed evidence."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
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


class FederatedClearingPolicyRecord(Base):
    __tablename__ = "federated_clearing_policies"
    __table_args__ = (
        CheckConstraint("policy_version >= 1 AND version >= 1", name="versions_positive"),
        CheckConstraint("algorithm_id = 'FEDERATED_NETTING'", name="algorithm_allowed"),
        CheckConstraint("algorithm_version = '1.0.0'", name="algorithm_version_allowed"),
        CheckConstraint("decimal_scale BETWEEN 0 AND 12", name="decimal_scale_allowed"),
        CheckConstraint("rounding_mode IN ('DOWN','HALF_EVEN')", name="rounding_allowed"),
        CheckConstraint("minimum_operation >= 0", name="minimum_nonnegative"),
        CheckConstraint("max_iterations BETWEEN 1 AND 100000", name="iterations_bounded"),
        CheckConstraint("max_cycle_length BETWEEN 3 AND 12", name="cycle_length_bounded"),
        CheckConstraint("prepare_ttl_seconds BETWEEN 30 AND 86400", name="prepare_ttl_bounded"),
        CheckConstraint("status IN ('ACTIVE','SUPERSEDED','REVOKED')", name="status_allowed"),
        CheckConstraint("policy_hash ~ '^sha256:[0-9a-f]{64}$'", name="policy_hash_sha256"),
        UniqueConstraint("policy_code", "policy_version", name="uq_federated_policy_version"),
        Index(
            "uq_federated_policy_active",
            "policy_code",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    policy_code: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    valuation_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    algorithm_id: Mapped[str] = mapped_column(String(32), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(16), nullable=False)
    decimal_scale: Mapped[int] = mapped_column(Integer, nullable=False)
    rounding_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    minimum_operation: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    max_cycle_length: Mapped[int] = mapped_column(Integer, nullable=False)
    prepare_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
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
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)


class InterNodeObligation(Base):
    __tablename__ = "inter_node_obligations"
    __table_args__ = (
        CheckConstraint(
            "home_node_code IN (debtor_node_code, creditor_node_code)", name="home_party"
        ),
        CheckConstraint("debtor_node_code <> creditor_node_code", name="parties_distinct"),
        CheckConstraint(
            "original_amount > 0 AND outstanding_amount >= 0 AND cleared_amount >= 0 "
            "AND outstanding_amount + cleared_amount = original_amount",
            name="amounts_consistent",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "status IN ('CONFIRMED','PREPARED','PARTIALLY_CLEARED','CLEARED',"
            "'DISPUTED','CANCELLED')",
            name="status_allowed",
        ),
        CheckConstraint("source_event_hash ~ '^sha256:[0-9a-f]{64}$'", name="source_hash_sha256"),
        Index("ix_inter_node_obligations_home_status", "home_node_code", "status", "unit_code"),
        Index("ix_inter_node_obligations_parties", "debtor_node_code", "creditor_node_code"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    home_node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    debtor_node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    creditor_node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(32), nullable=False)
    original_amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    outstanding_amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    cleared_amount: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), server_default=text("0"), nullable=False
    )
    source_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    source_event_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    liquidity_class: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    prepared_cycle_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.federated_clearing_cycles.id", ondelete="RESTRICT"),
    )
    prepared_input_hash: Mapped[str | None] = mapped_column(String(71))
    prepared_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)


class FederatedClearingCycle(Base):
    __tablename__ = "federated_clearing_cycles"
    __table_args__ = (
        CheckConstraint("period_end > period_start", name="period_ordered"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "status IN ('DRAFT','COLLECTING_SNAPSHOTS','PREPARING_NODES','PREPARED',"
            "'PROPOSED','VERIFYING','COMMIT_CERTIFIED','APPLYING','COMMITTED_PENDING_APPLY',"
            "'RECONCILED','PREPARE_EXPIRED','REJECTED','CONFLICT','CANCELLED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "input_hash IS NULL OR input_hash ~ '^sha256:[0-9a-f]{64}$'", name="input_hash_sha256"
        ),
        CheckConstraint(
            "result_hash IS NULL OR result_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="result_hash_sha256",
        ),
        CheckConstraint(
            "certificate_hash IS NULL OR certificate_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="certificate_hash_sha256",
        ),
        UniqueConstraint("coordinator_node_code", "cycle_code", name="uq_federated_cycle_code"),
        Index("ix_federated_cycles_status", "status", "updated_at"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_code: Mapped[str] = mapped_column(String(80), nullable=False)
    coordinator_node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    policy_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.federated_clearing_policies.id", ondelete="RESTRICT"),
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    participant_node_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    affected_node_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(71))
    result_hash: Mapped[str | None] = mapped_column(String(71))
    certificate_hash: Mapped[str | None] = mapped_column(String(71))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    created_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    created_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    created_actor_organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    certified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)


class FederatedInputSnapshot(Base):
    __tablename__ = "federated_input_snapshots"
    __table_args__ = (
        CheckConstraint("snapshot_hash ~ '^sha256:[0-9a-f]{64}$'", name="snapshot_hash_sha256"),
        CheckConstraint("checkpoint_hash ~ '^sha256:[0-9a-f]{64}$'", name="checkpoint_hash_sha256"),
        UniqueConstraint("cycle_id", "node_code", name="uq_federated_snapshot_node"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.federated_clearing_cycles.id", ondelete="RESTRICT"),
    )
    node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    snapshot_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    checkpoint_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    node_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    accepted_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class FederatedClearingProposal(Base):
    __tablename__ = "federated_clearing_proposals"
    __table_args__ = (
        CheckConstraint("input_hash ~ '^sha256:[0-9a-f]{64}$'", name="input_hash_sha256"),
        CheckConstraint("result_hash ~ '^sha256:[0-9a-f]{64}$'", name="result_hash_sha256"),
        UniqueConstraint("cycle_id", name="uq_federated_proposal_cycle"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.federated_clearing_cycles.id", ondelete="RESTRICT"),
    )
    proposal_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    coordinator_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class NodePrepareReceipt(Base):
    __tablename__ = "node_prepare_receipts"
    __table_args__ = (
        CheckConstraint("receipt_hash ~ '^sha256:[0-9a-f]{64}$'", name="receipt_hash_sha256"),
        UniqueConstraint("cycle_id", "node_code", name="uq_prepare_receipt_node"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.federated_clearing_cycles.id", ondelete="RESTRICT"),
    )
    node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    receipt_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    node_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    accepted_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class NodeClearingApproval(Base):
    __tablename__ = "node_clearing_approvals"
    __table_args__ = (
        CheckConstraint("approval_hash ~ '^sha256:[0-9a-f]{64}$'", name="approval_hash_sha256"),
        UniqueConstraint("cycle_id", "node_code", name="uq_clearing_approval_node"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.federated_clearing_cycles.id", ondelete="RESTRICT"),
    )
    node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    approval_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    approval_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    node_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    accepted_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FederatedCommitCertificate(Base):
    __tablename__ = "federated_commit_certificates"
    __table_args__ = (
        CheckConstraint(
            "certificate_hash ~ '^sha256:[0-9a-f]{64}$'", name="certificate_hash_sha256"
        ),
        UniqueConstraint("cycle_id", name="uq_commit_certificate_cycle"),
        UniqueConstraint("certificate_hash", name="uq_commit_certificate_hash"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.federated_clearing_cycles.id", ondelete="RESTRICT"),
    )
    certificate_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    certificate_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    coordinator_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    certified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NodeApplyReceipt(Base):
    __tablename__ = "node_apply_receipts"
    __table_args__ = (
        CheckConstraint("receipt_hash ~ '^sha256:[0-9a-f]{64}$'", name="receipt_hash_sha256"),
        CheckConstraint("applied_count >= 0 AND applied_amount >= 0", name="applied_nonnegative"),
        UniqueConstraint("cycle_id", "node_code", name="uq_apply_receipt_node"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.federated_clearing_cycles.id", ondelete="RESTRICT"),
    )
    node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    certificate_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    receipt_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    node_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    applied_count: Mapped[int] = mapped_column(Integer, nullable=False)
    applied_amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    accepted_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FederatedObligationApplication(Base):
    __tablename__ = "federated_obligation_applications"
    __table_args__ = (
        CheckConstraint(
            "amount_before > 0 AND cleared_amount > 0 AND amount_after >= 0 "
            "AND amount_after = amount_before - cleared_amount",
            name="amounts_consistent",
        ),
        CheckConstraint(
            "certificate_hash ~ '^sha256:[0-9a-f]{64}$'", name="certificate_hash_sha256"
        ),
        UniqueConstraint(
            "obligation_id", "certificate_hash", name="uq_obligation_certificate_application"
        ),
        Index("ix_obligation_applications_cycle", "cycle_id", "node_code"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.federated_clearing_cycles.id", ondelete="RESTRICT"),
    )
    obligation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.inter_node_obligations.id", ondelete="RESTRICT"),
    )
    node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    certificate_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    amount_before: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    cleared_amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    amount_after: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    applied_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
    )
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FederatedClearingProof(Base):
    __tablename__ = "federated_clearing_proofs"
    __table_args__ = (
        CheckConstraint("proof_hash ~ '^sha256:[0-9a-f]{64}$'", name="proof_hash_sha256"),
        UniqueConstraint("cycle_id", name="uq_federated_proof_cycle"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.federated_clearing_cycles.id", ondelete="RESTRICT"),
    )
    proof_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    proof_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class FederatedClearingConflict(Base):
    __tablename__ = "federated_clearing_conflicts"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN','RESOLVED')", name="status_allowed"),
        Index("ix_federated_conflicts_cycle_status", "cycle_id", "status"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.federated_clearing_cycles.id", ondelete="RESTRICT"),
    )
    node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    conflict_code: Mapped[str] = mapped_column(String(80), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    detected_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
