"""Relational model for disputes, sanctions, appeals, and contextual reliability."""

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


class TrustPolicy(Base):
    __tablename__ = "trust_policies"
    __table_args__ = (
        CheckConstraint("policy_version >= 1 AND version >= 1", name="versions_positive"),
        CheckConstraint("policy_code = 'TRUST_PROCEDURE'", name="code_allowed"),
        CheckConstraint(
            "status IN ('PROPOSED','ACTIVE','REJECTED','SUPERSEDED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "appeal_window_seconds BETWEEN 0 AND 2592000", name="appeal_window_bounded"
        ),
        CheckConstraint(
            "max_protective_seconds BETWEEN 1 AND 2592000", name="protective_window_bounded"
        ),
        CheckConstraint("panel_quorum BETWEEN 1 AND 9", name="panel_quorum_bounded"),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        CheckConstraint(
            "(status = 'PROPOSED' AND approved_event_id IS NULL) OR "
            "(status IN ('ACTIVE','SUPERSEDED') AND approved_event_id IS NOT NULL) OR "
            "status = 'REJECTED'",
            name="approval_consistent",
        ),
        UniqueConstraint("cooperative_id", "policy_version", name="uq_trust_policy_version"),
        Index(
            "uq_trust_policy_active",
            "cooperative_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        {"schema": "trust"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_code: Mapped[str] = mapped_column(String(40), nullable=False)
    semantic_version: Mapped[str] = mapped_column(String(24), nullable=False)
    appeal_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_protective_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    panel_quorum: Mapped[int] = mapped_column(Integer, nullable=False)
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


class TrustCase(Base):
    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "source_type IN "
            "('LIABILITY','EXCHANGE','CLEARING','INVENTORY','RIGHTS','NODE','OTHER')",
            name="source_type_allowed",
        ),
        CheckConstraint(
            "status IN ('OPEN','RESPONSE_RECEIVED','READY_FOR_DECISION','DECIDED',"
            "'UNDER_APPEAL','REMANDED','CLOSED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "confidentiality IN ('NORMAL','RESTRICTED')", name="confidentiality_allowed"
        ),
        CheckConstraint(
            "(response_event_id IS NULL AND response_text IS NULL AND responded_at IS NULL) OR "
            "(response_event_id IS NOT NULL AND response_text IS NOT NULL "
            "AND responded_at IS NOT NULL)",
            name="response_consistent",
        ),
        UniqueConstraint("cooperative_id", "case_reference", name="uq_trust_case_reference"),
        Index("ix_trust_cases_subject_status", "subject_member_id", "status"),
        Index("ix_trust_cases_cooperative_status", "cooperative_id", "status"),
        {"schema": "trust"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    policy_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.trust_policies.id", ondelete="RESTRICT")
    )
    case_reference: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    claimant_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    source_event_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(String(240), nullable=False)
    facts: Mapped[str] = mapped_column(Text, nullable=False)
    requested_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    confidentiality: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    opened_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    opened_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    opened_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    opened_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    response_text: Mapped[str | None] = mapped_column(Text)
    response_evidence_refs: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    responded_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    response_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    appeal_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ConflictDeclaration(Base):
    __tablename__ = "conflict_declarations"
    __table_args__ = (
        CheckConstraint("stage IN ('ORIGINAL','APPEAL','REHABILITATION')", name="stage_allowed"),
        CheckConstraint("assessment IN ('CLEAR','CONFLICT')", name="assessment_allowed"),
        UniqueConstraint(
            "case_id", "stage", "member_id", name="uq_trust_conflict_case_stage_member"
        ),
        {"schema": "trust"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.cases.id", ondelete="RESTRICT")
    )
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    assessment: Mapped[str] = mapped_column(String(16), nullable=False)
    relationship: Mapped[str] = mapped_column(String(120), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    declared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ProtectiveMeasure(Base):
    __tablename__ = "protective_measures"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "measure_type IN ('ADDITIONAL_REVIEW','LIMIT_SCOPE','SUSPEND_ROLE',"
            "'SUSPEND_KEY','BLOCK_NEW_GUARANTEES')",
            name="type_allowed",
        ),
        CheckConstraint("status IN ('ACTIVE','LIFTED','EXPIRED','REVOKED')", name="status_allowed"),
        CheckConstraint("expires_at > starts_at", name="period_ordered"),
        CheckConstraint("review_at <= expires_at", name="review_before_expiry"),
        CheckConstraint(
            "(status = 'ACTIVE' AND lifted_event_id IS NULL) OR "
            "(status IN ('LIFTED','REVOKED') AND lifted_event_id IS NOT NULL) OR "
            "status = 'EXPIRED'",
            name="lifecycle_consistent",
        ),
        Index("ix_trust_measures_subject_status", "subject_member_id", "status"),
        {"schema": "trust"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.cases.id", ondelete="RESTRICT")
    )
    subject_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    measure_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    imposed_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    imposed_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    imposed_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    imposed_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    lifted_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    lifted_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    lifted_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    lift_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ArbitrationDecision(Base):
    __tablename__ = "arbitration_decisions"
    __table_args__ = (
        CheckConstraint("stage IN ('ORIGINAL','APPEAL')", name="stage_allowed"),
        CheckConstraint(
            "outcome IN ('SUBSTANTIATED','PARTLY_SUBSTANTIATED','UNSUBSTANTIATED',"
            "'AFFIRMED','MODIFIED','OVERTURNED','REMANDED')",
            name="outcome_allowed",
        ),
        CheckConstraint(
            "fault_class IS NULL OR fault_class IN ('FORCE_MAJEURE','GOOD_FAITH_ERROR',"
            "'NEGLIGENCE','GROSS_NEGLIGENCE','INTENT','COLLUSION')",
            name="fault_allowed",
        ),
        CheckConstraint(
            "established_loss IS NULL OR established_loss >= 0", name="loss_nonnegative"
        ),
        CheckConstraint("decision_round >= 1", name="round_positive"),
        UniqueConstraint("case_id", "stage", "decision_round", name="uq_trust_decision_round"),
        Index("ix_trust_decisions_case_stage", "case_id", "stage"),
        {"schema": "trust"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.cases.id", ondelete="RESTRICT")
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_round: Mapped[int] = mapped_column(Integer, nullable=False)
    related_object_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    standard_of_proof: Mapped[str] = mapped_column(String(120), nullable=False)
    fault_class: Mapped[str | None] = mapped_column(String(32))
    causal_findings: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    established_loss: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    consequence_spec: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    panel_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
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
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Sanction(Base):
    __tablename__ = "sanctions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "measure_type IN ('WARNING','TRAINING','ADDITIONAL_REVIEW','LIMIT_SCOPE',"
            "'SUSPEND_ROLE','BLOCK_NEW_GUARANTEES','TERMINATE_ROLE')",
            name="type_allowed",
        ),
        CheckConstraint("severity IN ('LOW','MEDIUM','HIGH','CRITICAL')", name="severity_allowed"),
        CheckConstraint(
            "status IN ('PROPOSED','PENDING_APPEAL','ACTIVE','REVOKED','COMPLETED','CORRECTED')",
            name="status_allowed",
        ),
        CheckConstraint("expires_at IS NULL OR expires_at > starts_at", name="period_ordered"),
        CheckConstraint(
            "(status IN ('PROPOSED','PENDING_APPEAL') AND finalized_event_id IS NULL) OR "
            "(status IN ('ACTIVE','COMPLETED','CORRECTED') AND finalized_event_id IS NOT NULL) OR "
            "status = 'REVOKED'",
            name="finalization_consistent",
        ),
        Index("ix_trust_sanctions_subject_status", "subject_member_id", "status"),
        {"schema": "trust"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.cases.id", ondelete="RESTRICT")
    )
    decision_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.arbitration_decisions.id", ondelete="RESTRICT")
    )
    subject_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    measure_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    appeal_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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
    finalized_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    finalized_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    finalized_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    revoked_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class Appeal(Base):
    __tablename__ = "appeals"
    __table_args__ = (
        CheckConstraint("status IN ('SUBMITTED','DECIDED','WITHDRAWN')", name="status_allowed"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('AFFIRMED','MODIFIED','OVERTURNED','REMANDED')",
            name="outcome_allowed",
        ),
        CheckConstraint(
            "(status = 'SUBMITTED' AND appeal_decision_id IS NULL AND outcome IS NULL) OR "
            "(status = 'DECIDED' AND appeal_decision_id IS NOT NULL AND outcome IS NOT NULL) OR "
            "status = 'WITHDRAWN'",
            name="decision_consistent",
        ),
        UniqueConstraint("original_decision_id", name="uq_trust_appeal_original_decision"),
        Index("ix_trust_appeals_case_status", "case_id", "status"),
        {"schema": "trust"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.cases.id", ondelete="RESTRICT")
    )
    original_decision_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.arbitration_decisions.id", ondelete="RESTRICT")
    )
    sanction_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.sanctions.id", ondelete="RESTRICT")
    )
    appellant_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    grounds: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    submitted_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    submitted_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    appeal_decision_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.arbitration_decisions.id", ondelete="RESTRICT")
    )
    outcome: Mapped[str | None] = mapped_column(String(24))
    decided_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReputationEvent(Base):
    __tablename__ = "reputation_events"
    __table_args__ = (
        CheckConstraint(
            "context IN ('SUPPLY','QUALITY','STORAGE','LOGISTICS','SERVICE','OBLIGATION',"
            "'GUARANTEE','WAREHOUSE_CONTROL','AUDIT','ARBITRATION','FUND_GOVERNANCE',"
            "'NODE_SECURITY')",
            name="context_allowed",
        ),
        CheckConstraint(
            "classification IN ('FULFILLED','BREACH','SELF_REPORTED_ERROR','CORRECTION',"
            "'REHABILITATION')",
            name="classification_allowed",
        ),
        CheckConstraint("severity BETWEEN 0 AND 5", name="severity_bounded"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_bounded"),
        CheckConstraint("observation_end >= observation_start", name="observation_ordered"),
        CheckConstraint(
            "appeal_state IN ('NONE','PENDING','AFFIRMED','OVERTURNED')",
            name="appeal_state_allowed",
        ),
        CheckConstraint("status IN ('ACTIVE','DISPUTED','VOIDED')", name="status_allowed"),
        CheckConstraint(
            "visibility IN ('PARTICIPANT','COOPERATIVE','RESTRICTED')",
            name="visibility_allowed",
        ),
        CheckConstraint(
            "(classification = 'CORRECTION' AND corrects_event_id IS NOT NULL) OR "
            "(classification <> 'CORRECTION' AND corrects_event_id IS NULL)",
            name="correction_consistent",
        ),
        Index("ix_reputation_events_subject_context", "subject_member_id", "context"),
        {"schema": "trust"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    case_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.cases.id", ondelete="RESTRICT")
    )
    decision_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.arbitration_decisions.id", ondelete="RESTRICT")
    )
    subject_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    context: Mapped[str] = mapped_column(String(32), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    observation_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_event_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    appeal_state: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    corrects_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.reputation_events.id", ondelete="RESTRICT")
    )
    recorded_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    recorded_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    recorded_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class RehabilitationPlan(Base):
    __tablename__ = "rehabilitation_plans"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("status IN ('ACTIVE','COMPLETED','CANCELLED')", name="status_allowed"),
        CheckConstraint("due_at > starts_at", name="period_ordered"),
        CheckConstraint(
            "(status = 'ACTIVE' AND closed_event_id IS NULL) OR "
            "(status IN ('COMPLETED','CANCELLED') AND closed_event_id IS NOT NULL)",
            name="closure_consistent",
        ),
        Index("ix_rehabilitation_plans_subject_status", "subject_member_id", "status"),
        {"schema": "trust"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.cases.id", ondelete="RESTRICT")
    )
    decision_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.arbitration_decisions.id", ondelete="RESTRICT")
    )
    subject_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    completion_criteria: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    created_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    created_event_id: Mapped[UUID] = mapped_column(
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
    closure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class RehabilitationStep(Base):
    __tablename__ = "rehabilitation_steps"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("status IN ('PENDING','COMPLETED','WAIVED')", name="status_allowed"),
        CheckConstraint(
            "(status = 'PENDING' AND completed_event_id IS NULL) OR "
            "(status IN ('COMPLETED','WAIVED') AND completed_event_id IS NOT NULL)",
            name="completion_consistent",
        ),
        UniqueConstraint("plan_id", "sequence", name="uq_rehabilitation_step_sequence"),
        {"schema": "trust"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    plan_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trust.rehabilitation_plans.id", ondelete="RESTRICT")
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    completion_criterion: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    completed_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    completed_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    completed_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
