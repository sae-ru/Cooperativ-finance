"""ORM models owned by the node context."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from cooperative_clearing.shared.infrastructure.orm import Base


class NodeProfile(Base):
    __tablename__ = "node_profiles"
    __table_args__ = (
        CheckConstraint(
            "environment IN ('dev','test','staging-node','pilot','production')",
            name="environment_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        {"schema": "node"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    node_code: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    demo_data_loaded: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class SystemNotice(Base):
    __tablename__ = "system_notices"
    __table_args__ = (
        CheckConstraint("severity IN ('INFO','WARNING','CRITICAL')", name="severity_allowed"),
        CheckConstraint("status IN ('ACTIVE','RESOLVED')", name="status_allowed"),
        Index(
            "ix_system_notices_active_created_at",
            "created_at",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        {"schema": "node"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    message_key: Mapped[str] = mapped_column(String(200), nullable=False)
    parameters: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    __table_args__ = ({"schema": "node"},)

    worker_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    instance_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    release: Mapped[str] = mapped_column(String(100), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[str | None] = mapped_column(Text)


class NodeKeyRecord(Base):
    __tablename__ = "key_records"
    __table_args__ = (
        CheckConstraint("purpose IN ('NODE_SIGNING')", name="purpose_allowed"),
        CheckConstraint("algorithm IN ('Ed25519')", name="algorithm_allowed"),
        CheckConstraint(
            "status IN ('ACTIVE','ROTATING','RETIRED','SUSPENDED','REVOKED','COMPROMISED')",
            name="status_allowed",
        ),
        Index("ix_key_records_node_status", "node_id", "status"),
        Index(
            "uq_key_records_active_node_purpose",
            "node_id",
            "purpose",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        {"schema": "node"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("node.node_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(20), nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(71), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
