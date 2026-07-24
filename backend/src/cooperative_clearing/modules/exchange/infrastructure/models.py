"""SQLAlchemy persistence for deals, obligations, fulfillment, and logistics."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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


class Deal(Base):
    __tablename__ = "deals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PROPOSED','ACTIVE','PARTIALLY_FULFILLED','FULFILLED',"
            "'DISPUTED','CANCELLED','DEFAULTED')",
            name="status_allowed",
        ),
        CheckConstraint("terms_version >= 1 AND version >= 1", name="versions_positive"),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        Index("ix_deals_cooperative_status", "cooperative_id", "status"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    source_purchase_intent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.purchase_intents.id", ondelete="RESTRICT"),
        unique=True,
    )
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    terms_version: Mapped[int] = mapped_column(Integer, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(71), nullable=False)
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
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    confirmed_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class DealTermsVersion(Base):
    __tablename__ = "deal_terms_versions"
    __table_args__ = (
        CheckConstraint("terms_version >= 1", name="version_positive"),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        UniqueConstraint("deal_id", "terms_version", name="uq_deal_terms_deal_version"),
        UniqueConstraint(
            "deal_id", "terms_version", "terms_hash", name="uq_deal_terms_deal_version_hash"
        ),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    deal_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.deals.id", ondelete="RESTRICT")
    )
    terms_version: Mapped[int] = mapped_column(Integer, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    terms_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class DealParty(Base):
    __tablename__ = "deal_parties"
    __table_args__ = (
        CheckConstraint("terms_version >= 1", name="version_positive"),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        ForeignKeyConstraint(
            ["deal_id", "terms_version", "terms_hash"],
            [
                "exchange.deal_terms_versions.deal_id",
                "exchange.deal_terms_versions.terms_version",
                "exchange.deal_terms_versions.terms_hash",
            ],
            name="fk_deal_parties_terms_version_hash",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "deal_id", "terms_version", "member_id", name="uq_deal_parties_deal_version_member"
        ),
        Index("ix_deal_parties_member_deal", "member_id", "deal_id"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    deal_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.deals.id", ondelete="RESTRICT")
    )
    terms_version: Mapped[int] = mapped_column(Integer, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class DealConfirmation(Base):
    __tablename__ = "deal_confirmations"
    __table_args__ = (
        CheckConstraint("terms_version >= 1", name="version_positive"),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        ForeignKeyConstraint(
            ["deal_id", "terms_version", "terms_hash"],
            [
                "exchange.deal_terms_versions.deal_id",
                "exchange.deal_terms_versions.terms_version",
                "exchange.deal_terms_versions.terms_hash",
            ],
            name="fk_deal_confirmations_terms_version_hash",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["deal_id", "terms_version", "member_id"],
            [
                "exchange.deal_parties.deal_id",
                "exchange.deal_parties.terms_version",
                "exchange.deal_parties.member_id",
            ],
            name="fk_deal_confirmations_registered_party",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "deal_id", "terms_version", "member_id", name="uq_deal_confirmations_party_version"
        ),
        Index("ix_deal_confirmations_deal_version", "deal_id", "terms_version"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    deal_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.deals.id", ondelete="RESTRICT")
    )
    terms_version: Mapped[int] = mapped_column(Integer, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    confirmed_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Obligation(Base):
    __tablename__ = "obligations"
    __table_args__ = (
        CheckConstraint("quantity_total > 0", name="total_positive"),
        CheckConstraint(
            "quantity_submitted >= 0 AND quantity_fulfilled >= 0 AND quantity_cleared >= 0 AND "
            "quantity_submitted + quantity_fulfilled + quantity_cleared <= quantity_total",
            name="quantities_bounded",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','PARTIALLY_FULFILLED','FULFILLED','OVERDUE',"
            "'DISPUTED','DEFAULTED','CLOSED')",
            name="status_allowed",
        ),
        CheckConstraint("version >= 1 AND sequence_no >= 1", name="versions_positive"),
        ForeignKeyConstraint(
            ["deal_id", "terms_version"],
            [
                "exchange.deal_terms_versions.deal_id",
                "exchange.deal_terms_versions.terms_version",
            ],
            name="fk_obligations_terms_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["deal_id", "terms_version", "debtor_member_id"],
            [
                "exchange.deal_parties.deal_id",
                "exchange.deal_parties.terms_version",
                "exchange.deal_parties.member_id",
            ],
            name="fk_obligations_debtor_party",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["deal_id", "terms_version", "creditor_member_id"],
            [
                "exchange.deal_parties.deal_id",
                "exchange.deal_parties.terms_version",
                "exchange.deal_parties.member_id",
            ],
            name="fk_obligations_creditor_party",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("deal_id", "sequence_no", name="uq_obligations_deal_sequence"),
        Index("ix_obligations_debtor_status_due", "debtor_member_id", "status", "due_at"),
        Index("ix_obligations_creditor_status", "creditor_member_id", "status"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    deal_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.deals.id", ondelete="RESTRICT")
    )
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    terms_version: Mapped[int] = mapped_column(Integer, nullable=False)
    debtor_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    creditor_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quality_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    fulfillment_place: Mapped[str] = mapped_column(String(500), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.units_of_measure.id", ondelete="RESTRICT")
    )
    quantity_total: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    quantity_submitted: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, server_default=text("0")
    )
    quantity_fulfilled: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, server_default=text("0")
    )
    quantity_cleared: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, server_default=text("0")
    )
    clearing_allowed: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    partial_allowed: Mapped[bool] = mapped_column(nullable=False)
    evidence_required: Mapped[bool] = mapped_column(nullable=False)
    confirmation_method: Mapped[str] = mapped_column(String(200), nullable=False)
    substitute_policy: Mapped[str] = mapped_column(Text, nullable=False)
    valuation_source: Mapped[str] = mapped_column(String(300), nullable=False)
    liquidity_class: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
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


class LogisticsOrder(Base):
    __tablename__ = "logistics_orders"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "status IN ('OFFERED','ACCEPTED','IN_TRANSIT','DELIVERED','CANCELLED','DISPUTED')",
            name="status_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_logistics_orders_carrier_status", "carrier_member_id", "status"),
        Index("ix_logistics_orders_obligation_status", "obligation_id", "status"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    obligation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.obligations.id", ondelete="RESTRICT")
    )
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    carrier_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    unit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.units_of_measure.id", ondelete="RESTRICT")
    )
    origin_text: Mapped[str] = mapped_column(String(500), nullable=False)
    destination_text: Mapped[str] = mapped_column(String(500), nullable=False)
    origin_contact_name: Mapped[str | None] = mapped_column(String(200))
    origin_contact_phone: Mapped[str | None] = mapped_column(String(80))
    origin_instructions: Mapped[str | None] = mapped_column(Text)
    destination_contact_name: Mapped[str | None] = mapped_column(String(200))
    destination_contact_phone: Mapped[str | None] = mapped_column(String(80))
    destination_instructions: Mapped[str | None] = mapped_column(Text)
    pickup_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivery_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    offered_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    offered_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    carrier_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    carrier_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    accepted_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    pickup_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    delivered_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class Fulfillment(Base):
    __tablename__ = "fulfillments"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "accepted_quantity >= 0 AND accepted_quantity <= quantity",
            name="accepted_bounded",
        ),
        CheckConstraint(
            "status IN ('SUBMITTED','ACCEPTED','PARTIALLY_ACCEPTED','REJECTED','DISPUTED')",
            name="status_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_fulfillments_obligation_status", "obligation_id", "status"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    obligation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.obligations.id", ondelete="RESTRICT")
    )
    logistics_order_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.logistics_orders.id", ondelete="RESTRICT")
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    accepted_quantity: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, server_default=text("0")
    )
    quality_claim: Mapped[str] = mapped_column(Text, nullable=False)
    location_text: Mapped[str] = mapped_column(String(500), nullable=False)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    performed_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    performed_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    submitted_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    accepted_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class AcceptanceRecord(Base):
    __tablename__ = "acceptance_records"
    __table_args__ = (
        CheckConstraint("accepted_quantity >= 0", name="quantity_nonnegative"),
        CheckConstraint(
            "decision IN ('ACCEPTED','PARTIALLY_ACCEPTED','REJECTED')",
            name="decision_allowed",
        ),
        UniqueConstraint("fulfillment_id", name="uq_acceptance_fulfillment"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    fulfillment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.fulfillments.id", ondelete="RESTRICT")
    )
    accepted_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    accepted_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ObligationDispute(Base):
    __tablename__ = "obligation_disputes"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN','RESOLVED','REJECTED')", name="status_allowed"),
        CheckConstraint(
            "resolution_action IS NULL OR resolution_action IN "
            "('REJECT_CLAIM','CONTINUE_PERFORMANCE','DEFAULT_OBLIGATION','CLOSE_OBLIGATION')",
            name="resolution_action_allowed",
        ),
        CheckConstraint(
            "(status = 'OPEN' AND resolution_action IS NULL AND resolution_event_id IS NULL "
            "AND resolved_at IS NULL) OR (status IN ('RESOLVED','REJECTED') "
            "AND resolution_action IS NOT NULL AND resolution_event_id IS NOT NULL "
            "AND resolved_at IS NOT NULL)",
            name="resolution_consistent",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_obligation_disputes_obligation_status", "obligation_id", "status"),
        {"schema": "exchange"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    obligation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.obligations.id", ondelete="RESTRICT")
    )
    fulfillment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("exchange.fulfillments.id", ondelete="RESTRICT")
    )
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    previous_obligation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_fulfillment_status: Mapped[str | None] = mapped_column(String(32))
    opened_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    opened_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    resolution_action: Mapped[str | None] = mapped_column(String(32))
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    resolved_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    resolution_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
