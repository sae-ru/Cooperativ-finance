"""Persistence for signed federated offers, logistics quotes, and reservation saga."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class FederatedOffer(Base):
    __tablename__ = "federated_offers"
    __table_args__ = (
        CheckConstraint("offer_version >= 1 AND node_sequence >= 1", name="versions_positive"),
        CheckConstraint(
            "quantity_available > 0 AND minimum_batch > 0 AND unit_scale BETWEEN 0 AND 12",
            name="quantities_valid",
        ),
        CheckConstraint(
            "unit_price >= 0 AND mandatory_fee_per_unit >= 0", name="prices_nonnegative"
        ),
        CheckConstraint("status IN ('ACTIVE','REVOKED','EXPIRED')", name="status_allowed"),
        CheckConstraint(
            "source_mode IN ('LOCAL','DIRECT','INDEXED','CACHED_OFFLINE')",
            name="source_mode_allowed",
        ),
        CheckConstraint(
            "origin_precision IN ('EXACT','DISTRICT','REGION')", name="origin_precision_allowed"
        ),
        CheckConstraint("valid_until > signed_at", name="signature_period_valid"),
        CheckConstraint("availability_until > availability_from", name="availability_valid"),
        CheckConstraint("payload_hash ~ '^sha256:[0-9a-f]{64}$'", name="payload_hash_sha256"),
        UniqueConstraint("offer_id", "offer_version", name="uq_federated_offer_version"),
        Index("ix_federated_offers_product_status", "product_code", "status", "valid_until"),
        Index("ix_federated_offers_home_sequence", "home_node_code", "node_sequence"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    offer_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    offer_version: Mapped[int] = mapped_column(Integer, nullable=False)
    external_node_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("federation.external_nodes.id", ondelete="RESTRICT")
    )
    home_node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    seller_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    product_code: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quality_grade: Mapped[str] = mapped_column(String(80), nullable=False)
    certificate_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    quantity_available: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    quantity_is_band: Mapped[bool] = mapped_column(Boolean, nullable=False)
    unit_code: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_scale: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_batch: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    divisible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    origin_region: Mapped[str] = mapped_column(String(200), nullable=False)
    origin_precision: Mapped[str] = mapped_column(String(16), nullable=False)
    pickup_address_text: Mapped[str | None] = mapped_column(String(500))
    pickup_contact_name: Mapped[str | None] = mapped_column(String(200))
    pickup_contact_phone: Mapped[str | None] = mapped_column(String(80))
    pickup_instructions: Mapped[str | None] = mapped_column(Text)
    availability_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    availability_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fulfillment_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    mandatory_fee_per_unit: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    valuation_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    price_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    handling_requirements: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    counterparty_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    geography_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    guarantee_terms: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    source_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    node_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    node_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    publisher_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    publisher_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    published_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    revoked_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class OfferIndexSnapshot(Base):
    __tablename__ = "offer_index_snapshots"
    __table_args__ = (
        CheckConstraint("source_mode IN ('INDEXED','CACHED_OFFLINE')", name="source_mode_allowed"),
        CheckConstraint("node_sequence >= 1", name="sequence_positive"),
        CheckConstraint("valid_until > signed_at", name="period_valid"),
        CheckConstraint("checkpoint_hash ~ '^sha256:[0-9a-f]{64}$'", name="checkpoint_sha256"),
        UniqueConstraint("home_node_code", "node_sequence", name="uq_offer_snapshot_sequence"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    external_node_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("federation.external_nodes.id", ondelete="RESTRICT")
    )
    home_node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    node_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ordered_offer_hashes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    checkpoint_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    node_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    recorded_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class LogisticsQuote(Base):
    __tablename__ = "logistics_quotes"
    __table_args__ = (
        CheckConstraint("quote_version >= 1 AND capacity > 0", name="version_capacity_valid"),
        CheckConstraint("liability_limit >= 0", name="liability_nonnegative"),
        CheckConstraint("cost_status IN ('CONFIRMED','ESTIMATED')", name="cost_status_allowed"),
        CheckConstraint("status IN ('ACTIVE','REVOKED','EXPIRED')", name="status_allowed"),
        CheckConstraint("valid_until > signed_at", name="signature_period_valid"),
        CheckConstraint("delivery_until >= delivery_from", name="delivery_period_valid"),
        CheckConstraint("route_request_hash ~ '^sha256:[0-9a-f]{64}$'", name="route_hash_sha256"),
        CheckConstraint("payload_hash ~ '^sha256:[0-9a-f]{64}$'", name="payload_hash_sha256"),
        UniqueConstraint("quote_id", "quote_version", name="uq_logistics_quote_version"),
        Index("ix_logistics_quotes_offer_status", "offer_record_id", "status", "valid_until"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    quote_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    quote_version: Mapped[int] = mapped_column(Integer, nullable=False)
    offer_record_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("federation.federated_offers.id", ondelete="RESTRICT")
    )
    external_node_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("federation.external_nodes.id", ondelete="RESTRICT")
    )
    home_node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    carrier_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    route_request_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    origin_region: Mapped[str] = mapped_column(String(200), nullable=False)
    destination_region: Mapped[str] = mapped_column(String(200), nullable=False)
    route_legs: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    custody_transfers: Mapped[int] = mapped_column(Integer, nullable=False)
    capacity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(32), nullable=False)
    cost_components: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    valuation_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    cost_status: Mapped[str] = mapped_column(String(16), nullable=False)
    delivery_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivery_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    liability_limit: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    bond_ref: Mapped[str | None] = mapped_column(String(160))
    assumptions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    node_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    issued_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    revoked_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class PurchaseIntent(Base):
    __tablename__ = "purchase_intents"
    __table_args__ = (
        CheckConstraint("quantity > 0 AND version >= 1", name="quantity_version_valid"),
        CheckConstraint("max_landed_cost >= 0", name="maximum_nonnegative"),
        CheckConstraint("cost_status IN ('CONFIRMED','ESTIMATED')", name="cost_status_allowed"),
        CheckConstraint(
            "status IN ('PREPARING','GOODS_RESERVED','PREPARED','COMMITTING','CANCELLING',"
            "'COMMITTED','COMPENSATED','CANCELLED','EXPIRED')",
            name="status_allowed",
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint("summary_hash ~ '^sha256:[0-9a-f]{64}$'", name="summary_hash_sha256"),
        CheckConstraint(
            "commit_request_hash IS NULL OR commit_request_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="commit_request_hash_sha256",
        ),
        Index("ix_purchase_intents_buyer_status", "buyer_member_id", "status", "created_at"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    buyer_node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    buyer_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    buyer_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    buyer_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    offer_record_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("federation.federated_offers.id", ondelete="RESTRICT")
    )
    quote_record_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("federation.logistics_quotes.id", ondelete="RESTRICT")
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_region: Mapped[str] = mapped_column(String(200), nullable=False)
    delivery_address_text: Mapped[str | None] = mapped_column(String(500))
    delivery_contact_name: Mapped[str | None] = mapped_column(String(200))
    delivery_contact_phone: Mapped[str | None] = mapped_column(String(80))
    delivery_instructions: Mapped[str | None] = mapped_column(Text)
    max_landed_cost: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    landed_cost_breakdown: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    cost_status: Mapped[str] = mapped_column(String(16), nullable=False)
    summary_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    committed_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    commit_requested_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    commit_request_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    commit_request_hash: Mapped[str | None] = mapped_column(String(71))
    commit_request_signature: Mapped[bytes | None] = mapped_column(LargeBinary)
    commit_request_signer_fingerprint: Mapped[str | None] = mapped_column(String(71))
    cancellation_requested_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    compensated_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    cancelled_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    commit_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ReservationReceipt(Base):
    __tablename__ = "reservation_receipts"
    __table_args__ = (
        CheckConstraint("kind IN ('GOODS','LOGISTICS')", name="kind_allowed"),
        CheckConstraint(
            "status IN ('ACTIVE','COMMITTED','RELEASED','EXPIRED')", name="status_allowed"
        ),
        CheckConstraint("amount > 0 AND version >= 1", name="amount_version_valid"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint("receipt_hash ~ '^sha256:[0-9a-f]{64}$'", name="receipt_hash_sha256"),
        CheckConstraint(
            "remote_commit_hash IS NULL OR remote_commit_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="remote_commit_hash_sha256",
        ),
        CheckConstraint(
            "remote_release_hash IS NULL OR remote_release_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="remote_release_hash_sha256",
        ),
        Index(
            "uq_reservation_receipt_active_kind",
            "intent_id",
            "kind",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_reservation_receipts_resource_status", "resource_ref", "status", "expires_at"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    intent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("federation.purchase_intents.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    home_node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    receipt_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    node_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    released_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
    )
    expiry_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
    )
    remote_commit_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    remote_commit_hash: Mapped[str | None] = mapped_column(String(71))
    remote_commit_signature: Mapped[bytes | None] = mapped_column(LargeBinary)
    remote_commit_signer_fingerprint: Mapped[str | None] = mapped_column(String(71))
    remote_release_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    remote_release_hash: Mapped[str | None] = mapped_column(String(71))
    remote_release_signature: Mapped[bytes | None] = mapped_column(LargeBinary)
    remote_release_signer_fingerprint: Mapped[str | None] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
