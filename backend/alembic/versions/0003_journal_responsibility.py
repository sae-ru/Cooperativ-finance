"""Add node keys, signed journal, outbox, and personal responsibility."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_journal_responsibility"
down_revision: str | None = "0002_identity_and_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS risk")
    op.create_table(
        "key_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("algorithm", sa.String(length=20), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint", sa.String(length=71), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("algorithm IN ('Ed25519')", name="ck_key_records_algorithm_allowed"),
        sa.CheckConstraint("purpose IN ('NODE_SIGNING')", name="ck_key_records_purpose_allowed"),
        sa.CheckConstraint(
            "status IN ('ACTIVE','ROTATING','RETIRED','SUSPENDED','REVOKED','COMPROMISED')",
            name="ck_key_records_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["node.node_profiles.id"],
            name="fk_key_records_node_id_node_profiles",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_key_records"),
        sa.UniqueConstraint("fingerprint", name="uq_key_records_fingerprint"),
        schema="node",
    )
    op.create_index(
        "ix_key_records_node_status", "key_records", ["node_id", "status"], schema="node"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_key_records_active_node_purpose "
        "ON node.key_records (node_id, purpose) WHERE status = 'ACTIVE'"
    )

    op.create_table(
        "node_chain_states",
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("next_sequence", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_event_hash", sa.String(length=71), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "next_sequence >= 1", name="ck_node_chain_states_next_sequence_positive"
        ),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["node.node_profiles.id"],
            name="fk_node_chain_states_node_id_node_profiles",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("node_id", name="pk_node_chain_states"),
        schema="journal",
    )
    op.create_table(
        "signed_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("protocol_version", sa.String(length=20), nullable=False),
        sa.Column("canonicalization_profile", sa.String(length=40), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("local_sequence", sa.BigInteger(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("actor_person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_role_assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("previous_event_hash", sa.String(length=71), nullable=True),
        sa.Column("payload_hash", sa.String(length=71), nullable=False),
        sa.Column("event_hash", sa.String(length=71), nullable=False),
        sa.Column("canonical_envelope", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "aggregate_version >= 1", name="ck_signed_events_aggregate_version_positive"
        ),
        sa.CheckConstraint("local_sequence >= 1", name="ck_signed_events_local_sequence_positive"),
        sa.CheckConstraint("schema_version >= 1", name="ck_signed_events_schema_version_positive"),
        sa.ForeignKeyConstraint(
            ["actor_organization_id"],
            ["identity.cooperatives.id"],
            name="fk_signed_events_actor_organization_id_cooperatives",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_person_id"],
            ["identity.members.id"],
            name="fk_signed_events_actor_person_id_members",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_role_assignment_id"],
            ["identity.role_assignments.id"],
            name="fk_signed_events_actor_role_assignment_id_role_assignments",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["node.node_profiles.id"],
            name="fk_signed_events_node_id_node_profiles",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_signed_events"),
        sa.UniqueConstraint("event_hash", name="uq_signed_events_event_hash"),
        sa.UniqueConstraint("node_id", "local_sequence", name="uq_signed_event_node_sequence"),
        schema="journal",
    )
    op.create_index(
        "ix_signed_events_aggregate",
        "signed_events",
        ["aggregate_type", "aggregate_id", "local_sequence"],
        schema="journal",
    )
    op.create_index(
        "ix_signed_events_occurred",
        "signed_events",
        ["occurred_at", "event_id"],
        schema="journal",
    )
    op.create_table(
        "event_signatures",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signature_scope", sa.String(length=16), nullable=False),
        sa.Column("algorithm", sa.String(length=20), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.Column(
            "signed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "signature_scope IN ('NODE','OPERATOR')",
            name="ck_event_signatures_scope_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["journal.signed_events.event_id"],
            name="fk_event_signatures_event_id_signed_events",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["key_id"],
            ["node.key_records.id"],
            name="fk_event_signatures_key_id_key_records",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_event_signatures"),
        sa.UniqueConstraint("event_id", "key_id", "signature_scope", name="uq_event_signature"),
        schema="journal",
    )
    op.create_table(
        "outbox_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(length=120), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_owner", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_outbox_messages_attempt_count_nonnegative"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','PROCESSING','PUBLISHED','QUARANTINED')",
            name="ck_outbox_messages_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["journal.signed_events.event_id"],
            name="fk_outbox_messages_event_id_signed_events",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_messages"),
        sa.UniqueConstraint("event_id", "topic", name="uq_outbox_event_topic"),
        schema="journal",
    )
    op.create_index(
        "ix_outbox_ready",
        "outbox_messages",
        ["status", "available_at", "id"],
        schema="journal",
    )
    op.create_table(
        "consumer_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consumer_name", sa.String(length=100), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["journal.signed_events.event_id"],
            name="fk_consumer_receipts_event_id_signed_events",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_consumer_receipts"),
        sa.UniqueConstraint("event_id", "consumer_name", name="uq_consumer_event"),
        schema="journal",
    )

    op.create_table(
        "responsibility_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_type", sa.String(length=80), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=200), nullable=False),
        sa.Column("max_exposure", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("exposure_unit", sa.String(length=32), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "max_exposure >= 0",
            name="ck_responsibility_assignments_max_exposure_nonnegative",
        ),
        sa.CheckConstraint(
            "length(trim(scope)) > 0",
            name="ck_responsibility_assignments_scope_not_blank",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_APPROVAL','PENDING_ACCEPTANCE','ACTIVE','REJECTED','RELEASED')",
            name="ck_responsibility_assignments_status_allowed",
        ),
        sa.CheckConstraint(
            "length(trim(subject_type)) > 0",
            name="ck_responsibility_assignments_subject_type_not_blank",
        ),
        sa.CheckConstraint("version >= 1", name="ck_responsibility_assignments_version_positive"),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["identity.users.id"],
            name="fk_responsibility_assignments_accepted_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_event_id"],
            ["journal.signed_events.event_id"],
            name="fk_responsibility_assignments_accepted_event_id_signed_events",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["identity.users.id"],
            name="fk_responsibility_assignments_approved_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_event_id"],
            ["journal.signed_events.event_id"],
            name="fk_responsibility_assignments_approved_event_id_signed_events",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cooperative_id"],
            ["identity.cooperatives.id"],
            name="fk_responsibility_assignments_cooperative_id_cooperatives",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["identity.users.id"],
            name="fk_responsibility_assignments_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_event_id"],
            ["journal.signed_events.event_id"],
            name="fk_responsibility_assignments_created_event_id_signed_events",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["identity.members.id"],
            name="fk_responsibility_assignments_member_id_members",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_assignment_id"],
            ["identity.role_assignments.id"],
            name="fk_resp_assignments_role_assignment",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_responsibility_assignments"),
        schema="risk",
    )
    op.create_index(
        "ix_responsibility_member_status",
        "responsibility_assignments",
        ["member_id", "status"],
        schema="risk",
    )
    op.create_index(
        "ix_responsibility_subject",
        "responsibility_assignments",
        ["subject_type", "subject_id", "created_at"],
        schema="risk",
    )
    op.create_index(
        "uq_responsibility_open_subject_person_scope",
        "responsibility_assignments",
        ["subject_type", "subject_id", "member_id", "scope"],
        unique=True,
        schema="risk",
        postgresql_where=sa.text("status IN ('PENDING_APPROVAL','PENDING_ACCEPTANCE','ACTIVE')"),
    )
    op.create_table(
        "responsibility_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('APPROVE','REJECT')",
            name="ck_responsibility_approvals_decision_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["risk.responsibility_assignments.id"],
            name="fk_resp_approvals_assignment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["identity.users.id"],
            name="fk_responsibility_approvals_decided_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["journal.signed_events.event_id"],
            name="fk_responsibility_approvals_event_id_signed_events",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_responsibility_approvals"),
        sa.UniqueConstraint("assignment_id", name="uq_responsibility_approval_assignment"),
        schema="risk",
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION journal.prevent_evidence_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'evidence record is append-only';
        END;
        $$;
        CREATE TRIGGER trg_signed_events_append_only
          BEFORE UPDATE OR DELETE ON journal.signed_events
          FOR EACH ROW EXECUTE FUNCTION journal.prevent_evidence_mutation();
        CREATE TRIGGER trg_event_signatures_append_only
          BEFORE UPDATE OR DELETE ON journal.event_signatures
          FOR EACH ROW EXECUTE FUNCTION journal.prevent_evidence_mutation();
        CREATE TRIGGER trg_consumer_receipts_append_only
          BEFORE UPDATE OR DELETE ON journal.consumer_receipts
          FOR EACH ROW EXECUTE FUNCTION journal.prevent_evidence_mutation();
        CREATE TRIGGER trg_responsibility_approvals_append_only
          BEFORE UPDATE OR DELETE ON risk.responsibility_approvals
          FOR EACH ROW EXECUTE FUNCTION journal.prevent_evidence_mutation();
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coop_app') THEN
            GRANT USAGE ON SCHEMA risk TO coop_app;
            GRANT SELECT, INSERT ON node.key_records TO coop_app;
            GRANT SELECT, INSERT, UPDATE ON journal.node_chain_states TO coop_app;
            GRANT SELECT, INSERT ON journal.signed_events TO coop_app;
            GRANT SELECT, INSERT ON journal.event_signatures TO coop_app;
            GRANT SELECT, INSERT, UPDATE ON journal.outbox_messages TO coop_app;
            GRANT SELECT, INSERT ON journal.consumer_receipts TO coop_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON risk.responsibility_assignments TO coop_app;
            GRANT SELECT, INSERT ON risk.responsibility_approvals TO coop_app;
            ALTER DEFAULT PRIVILEGES IN SCHEMA risk
              GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO coop_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_responsibility_approvals_append_only "
        "ON risk.responsibility_approvals"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_consumer_receipts_append_only ON journal.consumer_receipts"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_event_signatures_append_only ON journal.event_signatures"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_signed_events_append_only ON journal.signed_events")
    op.execute("DROP FUNCTION IF EXISTS journal.prevent_evidence_mutation")
    op.drop_table("responsibility_approvals", schema="risk")
    op.drop_index(
        "uq_responsibility_open_subject_person_scope",
        table_name="responsibility_assignments",
        schema="risk",
    )
    op.drop_index(
        "ix_responsibility_subject",
        table_name="responsibility_assignments",
        schema="risk",
    )
    op.drop_index(
        "ix_responsibility_member_status",
        table_name="responsibility_assignments",
        schema="risk",
    )
    op.drop_table("responsibility_assignments", schema="risk")
    op.drop_table("consumer_receipts", schema="journal")
    op.drop_index("ix_outbox_ready", table_name="outbox_messages", schema="journal")
    op.drop_table("outbox_messages", schema="journal")
    op.drop_table("event_signatures", schema="journal")
    op.drop_index("ix_signed_events_occurred", table_name="signed_events", schema="journal")
    op.drop_index("ix_signed_events_aggregate", table_name="signed_events", schema="journal")
    op.drop_table("signed_events", schema="journal")
    op.drop_table("node_chain_states", schema="journal")
    op.execute("DROP INDEX IF EXISTS node.uq_key_records_active_node_purpose")
    op.drop_index("ix_key_records_node_status", table_name="key_records", schema="node")
    op.drop_table("key_records", schema="node")
    op.execute("DROP SCHEMA IF EXISTS risk")
