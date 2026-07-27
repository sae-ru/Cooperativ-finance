"""ORM models owned by the identity context."""

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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from cooperative_clearing.shared.infrastructure.orm import Base


class Cooperative(Base):
    __tablename__ = "cooperatives"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','SUSPENDED')", name="status_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        CheckConstraint(
            "status IN ('APPLICANT','PENDING_VERIFICATION','LIMITED','ACTIVE',"
            "'SUSPENDED','REJECTED','EXITED')",
            name="status_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_members_status_created_at", "status", "created_at"),
        Index("ix_members_registered_by_cooperative", "registered_by_cooperative_id"),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    registered_by_cooperative_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.cooperatives.id", ondelete="RESTRICT"),
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class MemberIdentifier(Base):
    __tablename__ = "member_identifiers"
    __table_args__ = (
        UniqueConstraint("identifier_type", "value_hash", name="uq_member_identifier_hash"),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.members.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    identifier_type: Mapped[str] = mapped_column(String(40), nullable=False)
    value_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("cooperative_id", "member_number", name="uq_membership_number"),
        UniqueConstraint("cooperative_id", "member_id", name="uq_membership_member"),
        CheckConstraint(
            "status IN ('PENDING','ACTIVE','SUSPENDED','ENDED')", name="status_allowed"
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_memberships_member_status", "member_id", "status"),
        {"schema": "identity"},
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
    member_number: Mapped[str] = mapped_column(String(63), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class UserAccount(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','DISABLED')", name="status_allowed"),
        CheckConstraint("failed_login_attempts >= 0", name="failed_attempts_nonnegative"),
        CheckConstraint("version >= 1", name="version_positive"),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    login: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.members.id", ondelete="RESTRICT"),
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class MemberImportBatch(Base):
    __tablename__ = "member_import_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('STAGED','PREVIEWED','APPROVED','REJECTED','APPLIED')",
            name="status_allowed",
        ),
        CheckConstraint("row_count >= 1", name="row_count_positive"),
        CheckConstraint(
            "ready_count >= 0 AND invalid_count >= 0 AND duplicate_count >= 0 "
            "AND applied_count >= 0",
            name="counts_nonnegative",
        ),
        CheckConstraint(
            "ready_count + invalid_count + duplicate_count <= row_count",
            name="preview_counts_bounded",
        ),
        CheckConstraint("applied_count <= ready_count", name="applied_count_bounded"),
        CheckConstraint(
            "reviewed_by_user_id IS NULL OR reviewed_by_user_id <> created_by_user_id",
            name="independent_reviewer",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_member_import_batches_cooperative_status_created",
            "cooperative_id",
            "status",
            "created_at",
        ),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.cooperatives.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ready_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    invalid_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    applied_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    decision_reason_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    previewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class MemberImportRow(Base):
    __tablename__ = "member_import_rows"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_number", name="uq_member_import_row_number"),
        CheckConstraint("row_number >= 1", name="row_number_positive"),
        CheckConstraint(
            "status IN ('STAGED','READY','INVALID','DUPLICATE','APPLIED')",
            name="status_allowed",
        ),
        Index("ix_member_import_rows_batch_status", "batch_id", "status", "row_number"),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.member_import_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(Text(), nullable=False)
    identifier_type: Mapped[str | None] = mapped_column(Text())
    identifier_hash: Mapped[str | None] = mapped_column(String(64))
    source_row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    match_basis: Mapped[str | None] = mapped_column(String(40))
    candidate_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    created_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ParticipantAddress(Base):
    __tablename__ = "participant_addresses"
    __table_args__ = (
        CheckConstraint("purpose IN ('PICKUP','DELIVERY','BOTH')", name="purpose_allowed"),
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="status_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_participant_addresses_member_status", "member_id", "status"),
        Index(
            "uq_participant_addresses_active_label",
            "member_id",
            "cooperative_id",
            text("lower(label)"),
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.members.id", ondelete="RESTRICT"),
        nullable=False,
    )
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.cooperatives.id", ondelete="RESTRICT"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    region_code: Mapped[str] = mapped_column(String(63), nullable=False)
    address_text: Mapped[str] = mapped_column(String(500), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(80), nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text())
    is_default_pickup: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    is_default_delivery: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ACTIVE'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class RoleAssignment(Base):
    __tablename__ = "role_assignments"
    __table_args__ = (
        CheckConstraint(
            "role_code IN ('EXCHANGE_PARTICIPANT','MEMBER_REGISTRAR',"
            "'COOPERATIVE_ADMIN','DATA_STEWARD',"
            "'WAREHOUSE_CUSTODIAN','INVENTORY_CONTROLLER','LOGISTICS_OPERATOR',"
            "'RIGHTS_OPERATOR','RISK_ADMIN','CLEARING_OPERATOR','CLEARING_CONTROLLER',"
            "'CLEARING_FINALIZER','SOLIDARITY_OPERATOR','SOLIDARITY_CONTROLLER','CRISIS_OPERATOR','CRISIS_CONTROLLER','SECURITY_ADMIN','NODE_REGISTRAR','NODE_TECHNICAL_CUSTODIAN','NODE_SECURITY_ADMIN','NODE_BUSINESS_OPERATOR','NODE_AUDITOR','AUDITOR','ARBITRATOR')",
            name="role_allowed",
        ),
        CheckConstraint(
            "status IN ('PENDING_APPROVAL','ACTIVE','REVOKED','REJECTED')",
            name="status_allowed",
        ),
        CheckConstraint("source IN ('ASSIGNMENT','BREAK_GLASS')", name="source_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_role_assignments_active_scope",
            "user_id",
            "role_code",
            text("COALESCE(cooperative_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            unique=True,
            postgresql_where=text("status IN ('PENDING_APPROVAL','ACTIVE')"),
        ),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role_code: Mapped[str] = mapped_column(String(40), nullable=False)
    cooperative_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.cooperatives.id", ondelete="RESTRICT"),
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ASSIGNMENT'")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    granted_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','REVOKED','EXPIRED')", name="status_allowed"),
        Index("ix_auth_sessions_user_status", "user_id", "status"),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    access_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    client_ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    step_up_method: Mapped[str | None] = mapped_column(String(16))
    step_up_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    step_up_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthenticationFactor(Base):
    __tablename__ = "authentication_factors"
    __table_args__ = (
        CheckConstraint("factor_type IN ('TOTP')", name="factor_type_allowed"),
        CheckConstraint("status IN ('PENDING','ACTIVE','DISABLED')", name="status_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_authentication_factors_active",
            "user_id",
            "factor_type",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "uq_authentication_factors_pending",
            "user_id",
            "factor_type",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    factor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_nonce: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    encryption_key_version: Mapped[str] = mapped_column(String(16), nullable=False)
    last_accepted_counter: Mapped[int | None] = mapped_column(BigInteger())
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enrollment_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class AccountRecoveryRequest(Base):
    __tablename__ = "account_recovery_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_APPROVAL','EXECUTED','REJECTED','EXPIRED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "decided_by_user_id IS NULL OR "
            "(decided_by_user_id <> requested_by_user_id "
            "AND decided_by_user_id <> target_user_id)",
            name="independent_decider",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_account_recovery_pending_target",
            "target_user_id",
            unique=True,
            postgresql_where=text("status = 'PENDING_APPROVAL'"),
        ),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    target_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    temporary_password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class BreakGlassGrant(Base):
    __tablename__ = "break_glass_grants"
    __table_args__ = (
        CheckConstraint(
            "role_code IN ('SECURITY_ADMIN','NODE_SECURITY_ADMIN','NODE_TECHNICAL_CUSTODIAN',"
            "'CRISIS_OPERATOR','CRISIS_CONTROLLER')",
            name="role_allowed",
        ),
        CheckConstraint(
            "status IN ('PENDING_APPROVAL','ACTIVE','REJECTED','REVOKED','EXPIRED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "approved_by_user_id IS NULL OR "
            "(approved_by_user_id <> requested_by_user_id "
            "AND approved_by_user_id <> target_user_id)",
            name="independent_approver",
        ),
        CheckConstraint("requested_duration_minutes BETWEEN 15 AND 240", name="duration_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_break_glass_open_scope",
            "target_user_id",
            "role_code",
            text("COALESCE(cooperative_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            unique=True,
            postgresql_where=text("status IN ('PENDING_APPROVAL','ACTIVE')"),
        ),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    target_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    role_code: Mapped[str] = mapped_column(String(40), nullable=False)
    cooperative_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    revoked_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(200), nullable=False)
    requested_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ServiceClient(Base):
    __tablename__ = "service_clients"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','SUSPENDED','REVOKED')", name="status_allowed"),
        CheckConstraint("rate_limit_per_minute BETWEEN 1 AND 6000", name="rate_limit_bounded"),
        CheckConstraint(
            "jsonb_typeof(scopes) = 'array' AND jsonb_array_length(scopes) >= 1",
            name="scopes_nonempty_array",
        ),
        CheckConstraint(
            "jsonb_typeof(network_allowlist) = 'array' "
            "AND jsonb_array_length(network_allowlist) >= 1",
            name="network_allowlist_nonempty_array",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_service_clients_owner_status", "owner_cooperative_id", "status", "created_at"),
        Index(
            "uq_service_clients_owner_name_live",
            "owner_cooperative_id",
            text("lower(display_name)"),
            unique=True,
            postgresql_where=text("status <> 'REVOKED'"),
        ),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    client_code: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    owner_cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.cooperatives.id", ondelete="RESTRICT"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    technical_contact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    technical_contact_email: Mapped[str] = mapped_column(String(254), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    network_allowlist: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    registered_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ServiceClientCredential(Base):
    __tablename__ = "service_client_credentials"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','RETIRED','REVOKED')", name="status_allowed"),
        Index("ix_service_client_credentials_client_status", "service_client_id", "status"),
        Index(
            "uq_service_client_credentials_active",
            "service_client_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    service_client_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.service_clients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    secret_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceClientRequest(Base):
    __tablename__ = "service_client_requests"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('CREATE','UPDATE','ROTATE','REACTIVATE')", name="operation_allowed"
        ),
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')", name="status_allowed"),
        CheckConstraint(
            "decided_by_user_id IS NULL OR decided_by_user_id <> requested_by_user_id",
            name="independent_reviewer",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_service_client_requests_owner_status",
            "owner_cooperative_id",
            "status",
            "created_at",
        ),
        Index(
            "uq_service_client_requests_pending_client",
            "service_client_id",
            unique=True,
            postgresql_where=text("status = 'PENDING' AND service_client_id IS NOT NULL"),
        ),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    service_client_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.service_clients.id", ondelete="RESTRICT")
    )
    owner_cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.cooperatives.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    proposed_config: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    expected_client_version: Mapped[int | None] = mapped_column(Integer)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    decision_reason_code: Mapped[str | None] = mapped_column(String(100))
    issued_credential_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.service_client_credentials.id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ServiceClientAccessToken(Base):
    __tablename__ = "service_client_access_tokens"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','REVOKED','EXPIRED')", name="status_allowed"),
        Index("ix_service_client_access_tokens_client_status", "service_client_id", "status"),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    service_client_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.service_clients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    credential_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.service_client_credentials.id", ondelete="RESTRICT"),
        nullable=False,
    )
    access_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceClientRateBucket(Base):
    __tablename__ = "service_client_rate_buckets"
    __table_args__ = (
        CheckConstraint("request_count >= 1", name="request_count_positive"),
        {"schema": "identity"},
    )

    service_client_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.service_clients.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
