"""Add identity, membership, sessions, RBAC, and append-only audit."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_identity_and_audit"
down_revision: str | None = "0001_system_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS identity")
    op.execute("CREATE SCHEMA IF NOT EXISTS journal")

    op.create_table(
        "cooperatives",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
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
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE','SUSPENDED')", name="ck_cooperatives_status_allowed"
        ),
        sa.CheckConstraint("version >= 1", name="ck_cooperatives_version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_cooperatives"),
        sa.UniqueConstraint("code", name="uq_cooperatives_code"),
        schema="identity",
    )
    op.create_table(
        "members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
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
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "status IN ('APPLICANT','PENDING_VERIFICATION','LIMITED','ACTIVE','SUSPENDED','REJECTED','EXITED')",
            name="ck_members_status_allowed",
        ),
        sa.CheckConstraint("version >= 1", name="ck_members_version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_members"),
        schema="identity",
    )
    op.create_index(
        "ix_members_status_created_at", "members", ["status", "created_at"], schema="identity"
    )
    op.create_table(
        "member_identifiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identifier_type", sa.String(length=40), nullable=False),
        sa.Column("value_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["identity.members.id"],
            name="fk_member_identifiers_member_id_members",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_member_identifiers"),
        sa.UniqueConstraint("identifier_type", "value_hash", name="uq_member_identifier_hash"),
        schema="identity",
    )
    op.create_index(
        "ix_member_identifiers_member_id", "member_identifiers", ["member_id"], schema="identity"
    )
    op.create_table(
        "memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_number", sa.String(length=63), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','ACTIVE','SUSPENDED','ENDED')",
            name="ck_memberships_status_allowed",
        ),
        sa.CheckConstraint("version >= 1", name="ck_memberships_version_positive"),
        sa.ForeignKeyConstraint(
            ["cooperative_id"],
            ["identity.cooperatives.id"],
            name="fk_memberships_cooperative_id_cooperatives",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["identity.members.id"],
            name="fk_memberships_member_id_members",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
        sa.UniqueConstraint("cooperative_id", "member_id", name="uq_membership_member"),
        sa.UniqueConstraint("cooperative_id", "member_number", name="uq_membership_number"),
        schema="identity",
    )
    op.create_index(
        "ix_memberships_member_status", "memberships", ["member_id", "status"], schema="identity"
    )
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("login", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "must_change_password", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "failed_login_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("status IN ('ACTIVE','DISABLED')", name="ck_users_status_allowed"),
        sa.CheckConstraint(
            "failed_login_attempts >= 0", name="ck_users_failed_attempts_nonnegative"
        ),
        sa.CheckConstraint("version >= 1", name="ck_users_version_positive"),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["identity.members.id"],
            name="fk_users_member_id_members",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("login", name="uq_users_login"),
        sa.UniqueConstraint("member_id", name="uq_users_member_id"),
        schema="identity",
    )
    op.create_table(
        "role_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_code", sa.String(length=40), nullable=False),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("granted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "role_code IN ('MEMBER_REGISTRAR','COOPERATIVE_ADMIN','DATA_STEWARD','RISK_ADMIN','SECURITY_ADMIN','NODE_REGISTRAR','AUDITOR')",
            name="ck_role_assignments_role_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_APPROVAL','ACTIVE','REVOKED','REJECTED')",
            name="ck_role_assignments_status_allowed",
        ),
        sa.CheckConstraint("version >= 1", name="ck_role_assignments_version_positive"),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["identity.users.id"],
            name="fk_role_assignments_approved_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cooperative_id"],
            ["identity.cooperatives.id"],
            name="fk_role_assignments_cooperative_id_cooperatives",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["identity.users.id"],
            name="fk_role_assignments_granted_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity.users.id"],
            name="fk_role_assignments_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_role_assignments"),
        schema="identity",
    )
    op.create_index(
        "ix_role_assignments_user_id", "role_assignments", ["user_id"], schema="identity"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_role_assignments_active_scope ON identity.role_assignments "
        "(user_id, role_code, COALESCE(cooperative_id, '00000000-0000-0000-0000-000000000000'::uuid)) "
        "WHERE status IN ('PENDING_APPROVAL','ACTIVE')"
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("access_token_hash", sa.String(length=64), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('ACTIVE','REVOKED','EXPIRED')", name="ck_auth_sessions_status_allowed"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity.users.id"],
            name="fk_auth_sessions_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.UniqueConstraint("access_token_hash", name="uq_auth_sessions_access_token_hash"),
        sa.UniqueConstraint("refresh_token_hash", name="uq_auth_sessions_refresh_token_hash"),
        schema="identity",
    )
    op.create_index(
        "ix_auth_sessions_user_status", "auth_sessions", ["user_id", "status"], schema="identity"
    )

    op.create_table(
        "audit_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("object_type", sa.String(length=80), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('SUCCESS','DENIED','FAILURE')", name="ck_audit_entries_outcome_allowed"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_entries"),
        schema="journal",
    )
    op.create_index(
        "ix_audit_entries_actor_occurred",
        "audit_entries",
        ["actor_user_id", "occurred_at"],
        schema="journal",
    )
    op.create_index(
        "ix_audit_entries_occurred_at_id", "audit_entries", ["occurred_at", "id"], schema="journal"
    )
    op.create_table(
        "idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('PROCESSING','COMPLETED')", name="ck_idempotency_records_status_allowed"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_records"),
        sa.UniqueConstraint(
            "actor_user_id",
            "operation",
            "idempotency_key",
            name="uq_idempotency_actor_operation_key",
        ),
        schema="journal",
    )
    op.create_index(
        "ix_idempotency_records_expires_at", "idempotency_records", ["expires_at"], schema="journal"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION journal.prevent_audit_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'journal.audit_entries is append-only';
        END;
        $$;
        CREATE TRIGGER trg_audit_entries_append_only
        BEFORE UPDATE OR DELETE ON journal.audit_entries
        FOR EACH ROW EXECUTE FUNCTION journal.prevent_audit_mutation();
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coop_app') THEN
            GRANT USAGE ON SCHEMA identity, journal TO coop_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA identity TO coop_app;
            GRANT SELECT, INSERT ON journal.audit_entries TO coop_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON journal.idempotency_records TO coop_app;
            ALTER DEFAULT PRIVILEGES IN SCHEMA identity
              GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO coop_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_entries_append_only ON journal.audit_entries")
    op.execute("DROP FUNCTION IF EXISTS journal.prevent_audit_mutation")
    op.drop_table("idempotency_records", schema="journal")
    op.drop_table("audit_entries", schema="journal")
    op.drop_table("auth_sessions", schema="identity")
    op.drop_table("role_assignments", schema="identity")
    op.drop_table("users", schema="identity")
    op.drop_table("memberships", schema="identity")
    op.drop_table("member_identifiers", schema="identity")
    op.drop_table("members", schema="identity")
    op.drop_table("cooperatives", schema="identity")
    op.execute("DROP SCHEMA IF EXISTS journal")
    op.execute("DROP SCHEMA IF EXISTS identity")
