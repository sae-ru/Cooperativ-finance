"""Add local step-up, dual-control recovery, and break-glass grants.

Revision ID: 0025_identity_step_up
Revises: 0024_marketplace_scope
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_identity_step_up"
down_revision: str | None = "0024_marketplace_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.add_column(
        "auth_sessions",
        sa.Column("step_up_method", sa.String(length=16), nullable=True),
        schema="identity",
    )
    op.add_column(
        "auth_sessions",
        sa.Column("step_up_verified_at", sa.DateTime(timezone=True), nullable=True),
        schema="identity",
    )
    op.add_column(
        "auth_sessions",
        sa.Column("step_up_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="identity",
    )

    op.create_table(
        "authentication_factors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("factor_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("secret_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("encryption_key_version", sa.String(length=16), nullable=False),
        sa.Column("last_accepted_counter", sa.BigInteger(), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enrollment_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("factor_type IN ('TOTP')", name="factor_type_allowed"),
        sa.CheckConstraint("status IN ('PENDING','ACTIVE','DISABLED')", name="status_allowed"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema="identity",
    )
    op.create_index(
        "ix_identity_authentication_factors_user_id",
        "authentication_factors",
        ["user_id"],
        unique=False,
        schema="identity",
    )
    op.create_index(
        "uq_authentication_factors_active",
        "authentication_factors",
        ["user_id", "factor_type"],
        unique=True,
        schema="identity",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "uq_authentication_factors_pending",
        "authentication_factors",
        ["user_id", "factor_type"],
        unique=True,
        schema="identity",
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "account_recovery_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("temporary_password_hash", sa.String(length=255), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("evidence_id", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING_APPROVAL','EXECUTED','REJECTED','EXPIRED')",
            name="status_allowed",
        ),
        sa.CheckConstraint(
            "decided_by_user_id IS NULL OR "
            "(decided_by_user_id <> requested_by_user_id "
            "AND decided_by_user_id <> target_user_id)",
            name="independent_decider",
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.ForeignKeyConstraint(["target_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema="identity",
    )
    op.create_index(
        "uq_account_recovery_pending_target",
        "account_recovery_requests",
        ["target_user_id"],
        unique=True,
        schema="identity",
        postgresql_where=sa.text("status = 'PENDING_APPROVAL'"),
    )

    op.create_table(
        "break_glass_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_code", sa.String(length=40), nullable=False),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoked_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("evidence_id", sa.String(length=200), nullable=False),
        sa.Column("requested_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "role_code IN ('SECURITY_ADMIN','NODE_SECURITY_ADMIN',"
            "'NODE_TECHNICAL_CUSTODIAN','CRISIS_OPERATOR','CRISIS_CONTROLLER')",
            name="role_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_APPROVAL','ACTIVE','REJECTED','REVOKED','EXPIRED')",
            name="status_allowed",
        ),
        sa.CheckConstraint(
            "approved_by_user_id IS NULL OR "
            "(approved_by_user_id <> requested_by_user_id "
            "AND approved_by_user_id <> target_user_id)",
            name="independent_approver",
        ),
        sa.CheckConstraint(
            "requested_duration_minutes BETWEEN 15 AND 240", name="duration_allowed"
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.ForeignKeyConstraint(["target_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["cooperative_id"], ["identity.cooperatives.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema="identity",
    )
    op.create_index(
        "uq_break_glass_open_scope",
        "break_glass_grants",
        [
            "target_user_id",
            "role_code",
            sa.text("COALESCE(cooperative_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
        ],
        unique=True,
        schema="identity",
        postgresql_where=sa.text("status IN ('PENDING_APPROVAL','ACTIVE')"),
    )


def downgrade() -> None:
    op.drop_index("uq_break_glass_open_scope", table_name="break_glass_grants", schema="identity")
    op.drop_table("break_glass_grants", schema="identity")
    op.drop_index(
        "uq_account_recovery_pending_target",
        table_name="account_recovery_requests",
        schema="identity",
    )
    op.drop_table("account_recovery_requests", schema="identity")
    op.drop_index(
        "uq_authentication_factors_pending",
        table_name="authentication_factors",
        schema="identity",
    )
    op.drop_index(
        "uq_authentication_factors_active",
        table_name="authentication_factors",
        schema="identity",
    )
    op.drop_index(
        "ix_identity_authentication_factors_user_id",
        table_name="authentication_factors",
        schema="identity",
    )
    op.drop_table("authentication_factors", schema="identity")
    op.drop_column("auth_sessions", "step_up_expires_at", schema="identity")
    op.drop_column("auth_sessions", "step_up_verified_at", schema="identity")
    op.drop_column("auth_sessions", "step_up_method", schema="identity")
