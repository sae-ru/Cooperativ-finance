"""Home-node resource holds created by authenticated remote buyer nodes."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from cooperative_clearing.shared.infrastructure.orm import Base


class PeerResourceReservation(Base):
    __tablename__ = "peer_resource_reservations"
    __table_args__ = (
        CheckConstraint("kind IN ('GOODS','LOGISTICS')", name="kind_allowed"),
        CheckConstraint(
            "(kind = 'GOODS' AND offer_record_id IS NOT NULL AND quote_record_id IS NULL) OR "
            "(kind = 'LOGISTICS' AND offer_record_id IS NULL AND quote_record_id IS NOT NULL)",
            name="resource_matches_kind",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','COMMITTED','RELEASED','EXPIRED')", name="status_allowed"
        ),
        CheckConstraint("amount > 0 AND exposure_amount >= 0", name="amount_positive"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint("summary_hash ~ '^sha256:[0-9a-f]{64}$'", name="summary_hash_sha256"),
        CheckConstraint("receipt_hash ~ '^sha256:[0-9a-f]{64}$'", name="receipt_hash_sha256"),
        CheckConstraint(
            "commit_hash IS NULL OR commit_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="commit_hash_sha256",
        ),
        CheckConstraint(
            "release_hash IS NULL OR release_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="release_hash_sha256",
        ),
        UniqueConstraint(
            "buyer_node_id", "buyer_intent_id", "kind", name="uq_peer_reservation_intent_kind"
        ),
        Index("ix_peer_reservations_resource_status", "resource_ref", "status", "expires_at"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    buyer_node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    buyer_intent_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    offer_record_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.federated_offers.id", ondelete="RESTRICT"),
    )
    quote_record_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.logistics_quotes.id", ondelete="RESTRICT"),
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(32), nullable=False)
    capability: Mapped[str] = mapped_column(String(40), nullable=False)
    exposure_amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    exposure_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    summary_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    receipt_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    receipt_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    commit_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    release_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    expiry_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    commit_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    commit_hash: Mapped[str | None] = mapped_column(String(71))
    commit_signature: Mapped[bytes | None] = mapped_column(LargeBinary)
    release_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    release_hash: Mapped[str | None] = mapped_column(String(71))
    release_signature: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
