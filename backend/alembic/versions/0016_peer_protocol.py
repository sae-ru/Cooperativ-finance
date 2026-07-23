"""add authenticated online peer protocol exchanges

Revision ID: 0016_peer_protocol
Revises: 0015_federated_discovery
Create Date: 2026-07-22 01:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_peer_protocol"
down_revision: str | None = "0015_federated_discovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "peer_protocol_exchanges",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("direction", sa.String(length=12), nullable=False),
        sa.Column("peer_node_id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("request_document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_hash", sa.String(length=71), nullable=False),
        sa.Column("request_signature", sa.LargeBinary(), nullable=False),
        sa.Column("request_signer_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("response_document", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_hash", sa.String(length=71), nullable=True),
        sa.Column("response_signature", sa.LargeBinary(), nullable=True),
        sa.Column("response_signer_fingerprint", sa.String(length=71), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "direction IN ('INBOUND','OUTBOUND')",
            name=op.f("ck_peer_protocol_exchanges_direction_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED','FAILED')",
            name=op.f("ck_peer_protocol_exchanges_status_allowed"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_peer_protocol_exchanges_expiry_after_creation"),
        ),
        sa.CheckConstraint(
            "request_hash ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_peer_protocol_exchanges_request_hash_sha256"),
        ),
        sa.CheckConstraint(
            "response_hash IS NULL OR response_hash ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_peer_protocol_exchanges_response_hash_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["peer_node_id"],
            ["federation.external_nodes.id"],
            name=op.f("fk_peer_protocol_exchanges_peer_node_id_external_nodes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_peer_protocol_exchanges")),
        sa.UniqueConstraint(
            "direction", "peer_node_id", "message_id", name="uq_peer_exchange_message"
        ),
        schema="federation",
    )
    op.create_index(
        "ix_peer_exchanges_peer_created",
        "peer_protocol_exchanges",
        ["peer_node_id", "created_at"],
        unique=False,
        schema="federation",
    )
    op.create_index(
        "ix_peer_exchanges_status_expiry",
        "peer_protocol_exchanges",
        ["status", "expires_at"],
        unique=False,
        schema="federation",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION federation.protect_peer_protocol_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'peer protocol evidence is append-only';
        END $$;

        CREATE TRIGGER trg_peer_protocol_exchange_evidence
          BEFORE UPDATE OR DELETE ON federation.peer_protocol_exchanges
          FOR EACH ROW EXECUTE FUNCTION federation.protect_peer_protocol_evidence();

        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coop_app') THEN
            GRANT SELECT, INSERT ON federation.peer_protocol_exchanges TO coop_app;
            REVOKE UPDATE, DELETE ON federation.peer_protocol_exchanges FROM coop_app;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM federation.peer_protocol_exchanges) THEN
            RAISE EXCEPTION 'cannot downgrade 0016: peer protocol evidence would be lost';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS federation.protect_peer_protocol_evidence CASCADE;
        """
    )
    op.drop_index(
        "ix_peer_exchanges_status_expiry",
        table_name="peer_protocol_exchanges",
        schema="federation",
    )
    op.drop_index(
        "ix_peer_exchanges_peer_created",
        table_name="peer_protocol_exchanges",
        schema="federation",
    )
    op.drop_table("peer_protocol_exchanges", schema="federation")
