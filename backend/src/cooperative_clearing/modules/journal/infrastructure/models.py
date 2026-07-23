"""Persistence models for the signed append-only journal and outbox."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from cooperative_clearing.shared.infrastructure.orm import Base


class NodeChainState(Base):
    __tablename__ = "node_chain_states"
    __table_args__ = (
        CheckConstraint("next_sequence >= 1", name="next_sequence_positive"),
        {"schema": "journal"},
    )

    node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("node.node_profiles.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    next_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    last_event_hash: Mapped[str | None] = mapped_column(String(71))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class SignedEvent(Base):
    __tablename__ = "signed_events"
    __table_args__ = (
        CheckConstraint("local_sequence >= 1", name="local_sequence_positive"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
        UniqueConstraint("node_id", "local_sequence", name="uq_signed_event_node_sequence"),
        UniqueConstraint("event_hash", name="uq_signed_events_event_hash"),
        Index("ix_signed_events_aggregate", "aggregate_type", "aggregate_id", "local_sequence"),
        Index("ix_signed_events_occurred", "occurred_at", "event_id"),
        {"schema": "journal"},
    )

    event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(20), nullable=False)
    canonicalization_profile: Mapped[str] = mapped_column(String(40), nullable=False)
    node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("node.node_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    offline_epoch_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "federation.offline_epochs.id",
            name="fk_signed_events_offline_epoch_id_offline_epochs",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )
    local_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_person_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.members.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.cooperatives.id", ondelete="RESTRICT"),
    )
    actor_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.role_assignments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[list[object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    previous_event_hash: Mapped[str | None] = mapped_column(String(71))
    payload_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    canonical_envelope: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class EventSignature(Base):
    __tablename__ = "event_signatures"
    __table_args__ = (
        CheckConstraint("signature_scope IN ('NODE','OPERATOR')", name="scope_allowed"),
        UniqueConstraint("event_id", "key_id", "signature_scope", name="uq_event_signature"),
        {"schema": "journal"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        nullable=False,
    )
    key_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("node.key_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    signature_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(20), nullable=False)
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','PUBLISHED','QUARANTINED')",
            name="status_allowed",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        UniqueConstraint("event_id", "topic", name="uq_outbox_event_topic"),
        Index("ix_outbox_ready", "status", "available_at", "id"),
        {"schema": "journal"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        nullable=False,
    )
    topic: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    lease_owner: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ConsumerReceipt(Base):
    __tablename__ = "consumer_receipts"
    __table_args__ = (
        UniqueConstraint("event_id", "consumer_name", name="uq_consumer_event"),
        {"schema": "journal"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        nullable=False,
    )
    consumer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
