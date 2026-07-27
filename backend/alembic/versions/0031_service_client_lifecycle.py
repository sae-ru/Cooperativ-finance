"""Add scoped service-client lifecycle and machine authentication.

Revision ID: 0031_service_client_lifecycle
Revises: 0030_safe_member_intake
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031_service_client_lifecycle"
down_revision: str | None = "0030_safe_member_intake"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_code", sa.String(length=63), nullable=False),
        sa.Column("owner_cooperative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("technical_contact_name", sa.String(length=200), nullable=False),
        sa.Column("technical_contact_email", sa.String(length=254), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("network_allowlist", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("registered_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE','SUSPENDED','REVOKED')", name="ck_service_clients_status_allowed"
        ),
        sa.CheckConstraint(
            "rate_limit_per_minute BETWEEN 1 AND 6000", name="ck_service_clients_rate_limit_bounded"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(scopes) = 'array' AND jsonb_array_length(scopes) >= 1",
            name="ck_service_clients_scopes_nonempty_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(network_allowlist) = 'array' AND jsonb_array_length(network_allowlist) >= 1",
            name="ck_service_clients_network_allowlist_nonempty_array",
        ),
        sa.CheckConstraint("version >= 1", name="ck_service_clients_version_positive"),
        sa.ForeignKeyConstraint(
            ["owner_cooperative_id"], ["identity.cooperatives.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["registered_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_code", name="uq_service_clients_client_code"),
        schema="identity",
    )
    op.create_index(
        "ix_service_clients_owner_status",
        "service_clients",
        ["owner_cooperative_id", "status", "created_at"],
        schema="identity",
    )
    op.create_index(
        "uq_service_clients_owner_name_live",
        "service_clients",
        ["owner_cooperative_id", sa.text("lower(display_name)")],
        unique=True,
        schema="identity",
        postgresql_where=sa.text("status <> 'REVOKED'"),
    )

    op.create_table(
        "service_client_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("secret_prefix", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("issued_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('ACTIVE','RETIRED','REVOKED')",
            name="ck_service_client_credentials_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["service_client_id"], ["identity.service_clients.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["issued_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("secret_hash", name="uq_service_client_credentials_secret_hash"),
        schema="identity",
    )
    op.create_index(
        "ix_service_client_credentials_client_status",
        "service_client_credentials",
        ["service_client_id", "status"],
        schema="identity",
    )
    op.create_index(
        "uq_service_client_credentials_active",
        "service_client_credentials",
        ["service_client_id"],
        unique=True,
        schema="identity",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        "service_client_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_cooperative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("proposed_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expected_client_version", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_reason_code", sa.String(length=100), nullable=True),
        sa.Column("issued_credential_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            "operation IN ('CREATE','UPDATE','ROTATE','REACTIVATE')",
            name="ck_service_client_requests_operation_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED')",
            name="ck_service_client_requests_status_allowed",
        ),
        sa.CheckConstraint(
            "decided_by_user_id IS NULL OR decided_by_user_id <> requested_by_user_id",
            name="ck_service_client_requests_independent_reviewer",
        ),
        sa.CheckConstraint("version >= 1", name="ck_service_client_requests_version_positive"),
        sa.ForeignKeyConstraint(
            ["service_client_id"], ["identity.service_clients.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["owner_cooperative_id"], ["identity.cooperatives.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["issued_credential_id"],
            ["identity.service_client_credentials.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="identity",
    )
    op.create_index(
        "ix_service_client_requests_owner_status",
        "service_client_requests",
        ["owner_cooperative_id", "status", "created_at"],
        schema="identity",
    )
    op.create_index(
        "uq_service_client_requests_pending_client",
        "service_client_requests",
        ["service_client_id"],
        unique=True,
        schema="identity",
        postgresql_where=sa.text("status = 'PENDING' AND service_client_id IS NOT NULL"),
    )

    op.create_table(
        "service_client_access_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("access_token_hash", sa.String(length=64), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('ACTIVE','REVOKED','EXPIRED')",
            name="ck_service_client_access_tokens_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["service_client_id"], ["identity.service_clients.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["identity.service_client_credentials.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "access_token_hash", name="uq_service_client_access_tokens_access_token_hash"
        ),
        schema="identity",
    )
    op.create_index(
        "ix_service_client_access_tokens_client_status",
        "service_client_access_tokens",
        ["service_client_id", "status"],
        schema="identity",
    )

    op.create_table(
        "service_client_rate_buckets",
        sa.Column("service_client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "request_count >= 1", name="ck_service_client_rate_buckets_request_count_positive"
        ),
        sa.ForeignKeyConstraint(
            ["service_client_id"], ["identity.service_clients.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("service_client_id", "window_started_at"),
        schema="identity",
    )


def downgrade() -> None:
    op.drop_table("service_client_rate_buckets", schema="identity")
    op.drop_index(
        "ix_service_client_access_tokens_client_status",
        table_name="service_client_access_tokens",
        schema="identity",
    )
    op.drop_table("service_client_access_tokens", schema="identity")
    op.drop_index(
        "uq_service_client_requests_pending_client",
        table_name="service_client_requests",
        schema="identity",
    )
    op.drop_index(
        "ix_service_client_requests_owner_status",
        table_name="service_client_requests",
        schema="identity",
    )
    op.drop_table("service_client_requests", schema="identity")
    op.drop_index(
        "uq_service_client_credentials_active",
        table_name="service_client_credentials",
        schema="identity",
    )
    op.drop_index(
        "ix_service_client_credentials_client_status",
        table_name="service_client_credentials",
        schema="identity",
    )
    op.drop_table("service_client_credentials", schema="identity")
    op.drop_index(
        "uq_service_clients_owner_name_live", table_name="service_clients", schema="identity"
    )
    op.drop_index(
        "ix_service_clients_owner_status", table_name="service_clients", schema="identity"
    )
    op.drop_table("service_clients", schema="identity")
