"""Persistence for authenticated online peer protocol exchanges."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from cooperative_clearing.shared.infrastructure.orm import Base


class PeerProtocolExchange(Base):
    __tablename__ = "peer_protocol_exchanges"
    __table_args__ = (
        CheckConstraint("direction IN ('INBOUND','OUTBOUND')", name="direction_allowed"),
        CheckConstraint("status IN ('SUCCEEDED','FAILED')", name="status_allowed"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint("request_hash ~ '^sha256:[0-9a-f]{64}$'", name="request_hash_sha256"),
        CheckConstraint(
            "response_hash IS NULL OR response_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="response_hash_sha256",
        ),
        UniqueConstraint(
            "direction", "peer_node_id", "message_id", name="uq_peer_exchange_message"
        ),
        Index("ix_peer_exchanges_peer_created", "peer_node_id", "created_at"),
        Index("ix_peer_exchanges_status_expiry", "status", "expires_at"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    peer_node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    message_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    request_document: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    request_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    request_signer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    response_document: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    response_hash: Mapped[str | None] = mapped_column(String(71))
    response_signature: Mapped[bytes | None] = mapped_column(LargeBinary)
    response_signer_fingerprint: Mapped[str | None] = mapped_column(String(71))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
