"""Current-state records for personal responsibility and independent decisions."""

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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from cooperative_clearing.shared.infrastructure.orm import Base


class ResponsibilityAssignment(Base):
    __tablename__ = "responsibility_assignments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_APPROVAL','PENDING_ACCEPTANCE','ACTIVE','REJECTED','RELEASED')",
            name="status_allowed",
        ),
        CheckConstraint("max_exposure >= 0", name="max_exposure_nonnegative"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(trim(subject_type)) > 0", name="subject_type_not_blank"),
        CheckConstraint("length(trim(scope)) > 0", name="scope_not_blank"),
        Index(
            "uq_responsibility_open_subject_person_scope",
            "subject_type",
            "subject_id",
            "member_id",
            "scope",
            unique=True,
            postgresql_where=text("status IN ('PENDING_APPROVAL','PENDING_ACCEPTANCE','ACTIVE')"),
        ),
        Index("ix_responsibility_subject", "subject_type", "subject_id", "created_at"),
        Index("ix_responsibility_member_status", "member_id", "status"),
        {"schema": "risk"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.cooperatives.id", ondelete="RESTRICT"),
        nullable=False,
    )
    member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.members.id", ondelete="RESTRICT"),
        nullable=False,
    )
    role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.role_assignments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scope: Mapped[str] = mapped_column(String(200), nullable=False)
    max_exposure: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    exposure_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.users.id", ondelete="RESTRICT"),
    )
    accepted_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.users.id", ondelete="RESTRICT"),
    )
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        nullable=False,
    )
    approved_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
    )
    accepted_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ResponsibilityApproval(Base):
    __tablename__ = "responsibility_approvals"
    __table_args__ = (
        CheckConstraint("decision IN ('APPROVE','REJECT')", name="decision_allowed"),
        UniqueConstraint("assignment_id", name="uq_responsibility_approval_assignment"),
        {"schema": "risk"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("risk.responsibility_assignments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    decided_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        nullable=False,
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
