"""Relational model for voluntary aid and privacy-preserving campaign reports."""

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

FORM_CHECK = "('MONEY','GOODS','LABOR','SERVICE','LOGISTICS','INFRASTRUCTURE')"


class SolidarityFund(Base):
    __tablename__ = "funds"
    __table_args__ = (
        CheckConstraint("version >= 1 AND policy_version >= 1", name="versions_positive"),
        CheckConstraint("status IN ('DRAFT','ACTIVE','SUSPENDED','CLOSED')", name="status_allowed"),
        CheckConstraint(
            "residue_rule IN ('RETAIN_IN_FUND','RETURN_TO_DONORS','TRANSFER_APPROVED_CAMPAIGN')",
            name="residue_rule_allowed",
        ),
        CheckConstraint(
            "admin_expense_limit >= 0 AND admin_expense_limit <= 1",
            name="admin_expense_limit_bounded",
        ),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        CheckConstraint(
            "(status = 'DRAFT' AND approved_event_id IS NULL) OR "
            "(status IN ('ACTIVE','SUSPENDED','CLOSED') AND approved_event_id IS NOT NULL)",
            name="approval_consistent",
        ),
        UniqueConstraint("cooperative_id", "fund_code", name="uq_solidarity_fund_code"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    fund_code: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    residue_rule: Mapped[str] = mapped_column(String(40), nullable=False)
    admin_expense_limit: Mapped[Decimal] = mapped_column(Numeric(8, 7), nullable=False)
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


class AidCampaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("ends_at > starts_at", name="period_ordered"),
        CheckConstraint(
            "status IN ('DRAFT','OPEN','SUSPENDED','CLOSED','CANCELLED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "residue_rule IN ('RETAIN_IN_FUND','RETURN_TO_DONORS','TRANSFER_APPROVED_CAMPAIGN')",
            name="residue_rule_allowed",
        ),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        CheckConstraint(
            "(status = 'DRAFT' AND opened_event_id IS NULL) OR "
            "(status IN ('OPEN','SUSPENDED') AND opened_event_id IS NOT NULL "
            "AND closed_event_id IS NULL) OR "
            "(status IN ('CLOSED','CANCELLED') AND closed_event_id IS NOT NULL)",
            name="lifecycle_consistent",
        ),
        UniqueConstraint("cooperative_id", "campaign_code", name="uq_solidarity_campaign_code"),
        Index("ix_solidarity_campaigns_status", "cooperative_id", "status"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    fund_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.funds.id", ondelete="RESTRICT")
    )
    campaign_code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    public_purpose: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    accepted_forms: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    residue_rule: Mapped[str] = mapped_column(String(40), nullable=False)
    terms_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(71), nullable=False)
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
    opened_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    opened_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    opened_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    opened_event_id: Mapped[UUID | None] = mapped_column(
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
    closed_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    closed_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class Pledge(Base):
    __tablename__ = "pledges"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(f"contribution_form IN {FORM_CHECK}", name="form_allowed"),
        CheckConstraint(
            "status IN ('ACTIVE','FULFILLED','CANCELLED','EXPIRED')", name="status_allowed"
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "(status = 'FULFILLED' AND fulfilled_contribution_id IS NOT NULL) OR "
            "(status <> 'FULFILLED' AND fulfilled_contribution_id IS NULL)",
            name="fulfillment_consistent",
        ),
        Index("ix_solidarity_pledges_campaign_status", "campaign_id", "status"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    campaign_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.campaigns.id", ondelete="RESTRICT")
    )
    donor_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    contribution_form: Mapped[str] = mapped_column(String(24), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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
    fulfilled_contribution_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "solidarity.contributions.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_pledges_fulfilled_contribution_id_contributions",
        ),
    )
    cancelled_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class Contribution(Base):
    __tablename__ = "contributions"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(f"contribution_form IN {FORM_CHECK}", name="form_allowed"),
        CheckConstraint(
            "status IN ('RECEIVED','VERIFIED','REJECTED','REFUNDED')", name="status_allowed"
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "(status = 'RECEIVED' AND verified_event_id IS NULL) OR "
            "(status IN ('VERIFIED','REJECTED','REFUNDED') AND verified_event_id IS NOT NULL)",
            name="verification_consistent",
        ),
        UniqueConstraint("pledge_id", name="uq_solidarity_contribution_pledge"),
        Index("ix_solidarity_contributions_campaign_status", "campaign_id", "status"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    campaign_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.campaigns.id", ondelete="RESTRICT")
    )
    pledge_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.pledges.id", ondelete="RESTRICT")
    )
    donor_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    contribution_form: Mapped[str] = mapped_column(String(24), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    received_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    received_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    received_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    received_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    verified_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    verified_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    verified_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    verified_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    verification_note: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class AidApplication(Base):
    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="quantity_positive"),
        CheckConstraint(f"requested_form IN {FORM_CHECK}", name="form_allowed"),
        CheckConstraint(
            "need_category IN ('BASIC_FOOD','MEDICAL','SHELTER','TRANSPORT','CARE','OTHER')",
            name="need_category_allowed",
        ),
        CheckConstraint(
            "privacy_scope IN ('PARTICIPANT_STAFF','RESTRICTED')", name="privacy_allowed"
        ),
        CheckConstraint(
            "status IN ('SUBMITTED','ELIGIBLE','REJECTED','ALLOCATED','CLOSED')",
            name="status_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_solidarity_applications_campaign_status", "campaign_id", "status"),
        Index("ix_solidarity_applications_recipient", "recipient_member_id", "status"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    campaign_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.campaigns.id", ondelete="RESTRICT")
    )
    recipient_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    need_category: Mapped[str] = mapped_column(String(24), nullable=False)
    requested_form: Mapped[str] = mapped_column(String(24), nullable=False)
    requested_unit_code: Mapped[str] = mapped_column(String(24), nullable=False)
    requested_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    privacy_scope: Mapped[str] = mapped_column(String(24), nullable=False)
    private_evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    submitted_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    submitted_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    submitted_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    submitted_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    reviewed_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    reviewed_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    reviewed_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    eligibility_note: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class AidAllocation(Base):
    __tablename__ = "allocations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(f"contribution_form IN {FORM_CHECK}", name="form_allowed"),
        CheckConstraint(
            "status IN ('PROPOSED','APPROVED','SUSPENDED','DELIVERED','CANCELLED')",
            name="status_allowed",
        ),
        CheckConstraint("allocation_hash ~ '^sha256:[0-9a-f]{64}$'", name="hash_sha256"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_solidarity_allocation_active_application",
            "application_id",
            unique=True,
            postgresql_where=text("status IN ('PROPOSED','APPROVED','SUSPENDED','DELIVERED')"),
        ),
        Index("ix_solidarity_allocations_campaign_status", "campaign_id", "status"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    campaign_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.campaigns.id", ondelete="RESTRICT")
    )
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.applications.id", ondelete="RESTRICT")
    )
    recipient_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    contribution_form: Mapped[str] = mapped_column(String(24), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    public_summary: Mapped[str] = mapped_column(String(240), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    policy_terms_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    allocation_hash: Mapped[str] = mapped_column(String(71), nullable=False)
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class AllocationApproval(Base):
    __tablename__ = "allocation_approvals"
    __table_args__ = (
        CheckConstraint("decision IN ('APPROVED','REJECTED')", name="decision_allowed"),
        CheckConstraint("allocation_hash ~ '^sha256:[0-9a-f]{64}$'", name="hash_sha256"),
        UniqueConstraint("allocation_id", name="uq_solidarity_allocation_approval"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    allocation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.allocations.id", ondelete="RESTRICT")
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    allocation_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    conflict_statement: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    decided_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    decided_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    decided_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AidDelivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        CheckConstraint(
            "attestor_kind IN ('RECIPIENT','REPRESENTATIVE','WITNESS')",
            name="attestor_kind_allowed",
        ),
        UniqueConstraint("allocation_id", name="uq_solidarity_delivery_allocation"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    allocation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.allocations.id", ondelete="RESTRICT")
    )
    recipient_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    attestor_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    attested_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    attested_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    attested_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    acknowledgement: Mapped[str] = mapped_column(Text, nullable=False)
    delivered_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class SolidarityComplaint(Base):
    __tablename__ = "complaints"
    __table_args__ = (
        CheckConstraint(
            "category IN ('ELIGIBILITY','ALLOCATION','DELIVERY','CONTRIBUTION','PRIVACY','OTHER')",
            name="category_allowed",
        ),
        CheckConstraint(
            "privacy_scope IN ('PARTICIPANT_STAFF','RESTRICTED')", name="privacy_allowed"
        ),
        CheckConstraint("status IN ('OPEN','RESOLVED','REJECTED')", name="status_allowed"),
        CheckConstraint(
            "resolution_action IS NULL OR resolution_action IN "
            "('RESTORE_ALLOCATION','CANCEL_ALLOCATION','NOTE_ONLY')",
            name="resolution_action_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_solidarity_complaints_campaign_status", "campaign_id", "status"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    campaign_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.campaigns.id", ondelete="RESTRICT")
    )
    allocation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.allocations.id", ondelete="RESTRICT")
    )
    contribution_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.contributions.id", ondelete="RESTRICT")
    )
    complainant_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    summary: Mapped[str] = mapped_column(String(240), nullable=False)
    privacy_scope: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    opened_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    opened_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    opened_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    resolved_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    resolved_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    resolved_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    resolution_action: Mapped[str | None] = mapped_column(String(32))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class CampaignReport(Base):
    __tablename__ = "campaign_reports"
    __table_args__ = (
        CheckConstraint("report_hash ~ '^sha256:[0-9a-f]{64}$'", name="hash_sha256"),
        CheckConstraint(
            "residue_rule IN ('RETAIN_IN_FUND','RETURN_TO_DONORS','TRANSFER_APPROVED_CAMPAIGN')",
            name="residue_rule_allowed",
        ),
        CheckConstraint(
            "contribution_count >= 0 AND allocation_count >= 0 AND "
            "delivery_count >= 0 AND complaint_count >= 0",
            name="counts_nonnegative",
        ),
        UniqueConstraint("campaign_id", name="uq_solidarity_campaign_report"),
        {"schema": "solidarity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    campaign_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solidarity.campaigns.id", ondelete="RESTRICT")
    )
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    bucket_totals: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    contribution_count: Mapped[int] = mapped_column(Integer, nullable=False)
    allocation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_count: Mapped[int] = mapped_column(Integer, nullable=False)
    complaint_count: Mapped[int] = mapped_column(Integer, nullable=False)
    residue_rule: Mapped[str] = mapped_column(String(40), nullable=False)
    responsibility_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    report_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    generated_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    generated_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    generated_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    generated_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
