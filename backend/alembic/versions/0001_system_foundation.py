"""Create the local node foundation tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_system_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS node")
    op.create_table(
        "node_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_code", sa.String(length=63), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column(
            "demo_data_loaded", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
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
            "environment IN ('dev','test','staging-node','pilot','production')",
            name="ck_node_profiles_environment_allowed",
        ),
        sa.CheckConstraint("version >= 1", name="ck_node_profiles_version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_node_profiles"),
        sa.UniqueConstraint("node_code", name="uq_node_profiles_node_code"),
        schema="node",
    )
    op.create_table(
        "system_notices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("message_key", sa.String(length=200), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "severity IN ('INFO','WARNING','CRITICAL')",
            name="ck_system_notices_severity_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','RESOLVED')",
            name="ck_system_notices_status_allowed",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_system_notices"),
        sa.UniqueConstraint("code", name="uq_system_notices_code"),
        schema="node",
    )
    op.create_index(
        "ix_system_notices_active_created_at",
        "system_notices",
        ["created_at"],
        unique=False,
        schema="node",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_name", sa.String(length=100), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("release", sa.String(length=100), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("worker_name", name="pk_worker_heartbeats"),
        schema="node",
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coop_app') THEN
            EXECUTE 'GRANT USAGE ON SCHEMA node TO coop_app';
            EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA node TO coop_app';
            EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA node GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO coop_app';
            EXECUTE 'GRANT SELECT ON TABLE public.alembic_version TO coop_app';
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats", schema="node")
    op.drop_index(
        "ix_system_notices_active_created_at",
        table_name="system_notices",
        schema="node",
    )
    op.drop_table("system_notices", schema="node")
    op.drop_table("node_profiles", schema="node")
    op.execute("DROP SCHEMA IF EXISTS node")
