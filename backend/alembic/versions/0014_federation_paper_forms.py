"""Add independently recorded federation paper forms.

Revision ID: 0014_federation_paper_forms
Revises: 0013_offline_nodes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_federation_paper_forms"
down_revision: str | None = "0013_offline_nodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_forms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("external_node_id", sa.Uuid(), nullable=False),
        sa.Column("epoch_id", sa.Uuid(), nullable=False),
        sa.Column("serial_number", sa.String(length=64), nullable=False),
        sa.Column("qr_reference", sa.String(length=220), nullable=False),
        sa.Column("checksum", sa.String(length=71), nullable=False),
        sa.Column("form_type", sa.String(length=32), nullable=False),
        sa.Column("form_version", sa.Integer(), nullable=False),
        sa.Column("participant_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("operation_constraints", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload_hash", sa.String(length=71), nullable=True),
        sa.Column("signatures", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evidence_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("issued_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("issued_by_member_id", sa.Uuid(), nullable=False),
        sa.Column("issued_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("issued_event_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_by_member_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_role_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_event_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("voided_by_member_id", sa.Uuid(), nullable=True),
        sa.Column("voided_event_id", sa.Uuid(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "form_type IN ('GOODS_TRANSFER','LOGISTICS_HANDOFF','SERVICE_ACCEPTANCE',"
            "'EMERGENCY_NODE_ACTION','EXCEPTION')",
            name=op.f("ck_paper_forms_type_allowed"),
        ),
        sa.CheckConstraint(
            "form_version BETWEEN 1 AND 100",
            name=op.f("ck_paper_forms_form_version_bounded"),
        ),
        sa.CheckConstraint(
            "status IN ('ISSUED','RECORDED','VOID','EXPIRED')",
            name=op.f("ck_paper_forms_status_allowed"),
        ),
        sa.CheckConstraint(
            "expires_at > issued_at", name=op.f("ck_paper_forms_expiry_after_issue")
        ),
        sa.CheckConstraint(
            "checksum ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_paper_forms_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "payload_hash IS NULL OR payload_hash ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_paper_forms_payload_hash_sha256"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_paper_forms_aggregate_version_positive")),
        sa.ForeignKeyConstraint(
            ["epoch_id"],
            ["federation.offline_epochs.id"],
            name=op.f("fk_paper_forms_epoch_id_offline_epochs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["external_node_id"],
            ["federation.external_nodes.id"],
            name=op.f("fk_paper_forms_external_node_id_external_nodes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["issued_by_member_id"],
            ["identity.members.id"],
            name=op.f("fk_paper_forms_issued_by_member_id_members"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["issued_by_user_id"],
            ["identity.users.id"],
            name=op.f("fk_paper_forms_issued_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["issued_event_id"],
            ["journal.signed_events.event_id"],
            name=op.f("fk_paper_forms_issued_event_id_signed_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["issued_role_assignment_id"],
            ["identity.role_assignments.id"],
            name=op.f("fk_paper_forms_issued_role_assignment_id_role_assignments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_member_id"],
            ["identity.members.id"],
            name=op.f("fk_paper_forms_recorded_by_member_id_members"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["identity.users.id"],
            name=op.f("fk_paper_forms_recorded_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_event_id"],
            ["journal.signed_events.event_id"],
            name=op.f("fk_paper_forms_recorded_event_id_signed_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_role_assignment_id"],
            ["identity.role_assignments.id"],
            name=op.f("fk_paper_forms_recorded_role_assignment_id_role_assignments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by_member_id"],
            ["identity.members.id"],
            name=op.f("fk_paper_forms_voided_by_member_id_members"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by_user_id"],
            ["identity.users.id"],
            name=op.f("fk_paper_forms_voided_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voided_event_id"],
            ["journal.signed_events.event_id"],
            name=op.f("fk_paper_forms_voided_event_id_signed_events"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_forms")),
        sa.UniqueConstraint(
            "external_node_id",
            "serial_number",
            name="uq_paper_form_node_serial",
        ),
        sa.UniqueConstraint("issued_event_id", name=op.f("uq_paper_forms_issued_event_id")),
        sa.UniqueConstraint("qr_reference", name="uq_paper_form_qr_reference"),
        sa.UniqueConstraint("recorded_event_id", name=op.f("uq_paper_forms_recorded_event_id")),
        sa.UniqueConstraint("voided_event_id", name=op.f("uq_paper_forms_voided_event_id")),
        schema="federation",
    )
    op.create_index(
        "ix_federation_paper_forms_epoch_status",
        "paper_forms",
        ["epoch_id", "status"],
        unique=False,
        schema="federation",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION federation.protect_paper_form_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'federation paper form evidence is immutable';
          END IF;
          IF OLD.external_node_id IS DISTINCT FROM NEW.external_node_id OR
             OLD.epoch_id IS DISTINCT FROM NEW.epoch_id OR
             OLD.serial_number IS DISTINCT FROM NEW.serial_number OR
             OLD.qr_reference IS DISTINCT FROM NEW.qr_reference OR
             OLD.checksum IS DISTINCT FROM NEW.checksum OR
             OLD.form_type IS DISTINCT FROM NEW.form_type OR
             OLD.form_version IS DISTINCT FROM NEW.form_version OR
             OLD.participant_refs IS DISTINCT FROM NEW.participant_refs OR
             OLD.operation_constraints IS DISTINCT FROM NEW.operation_constraints OR
             OLD.issued_at IS DISTINCT FROM NEW.issued_at OR
             OLD.expires_at IS DISTINCT FROM NEW.expires_at OR
             OLD.issued_by_user_id IS DISTINCT FROM NEW.issued_by_user_id OR
             OLD.issued_by_member_id IS DISTINCT FROM NEW.issued_by_member_id OR
             OLD.issued_role_assignment_id IS DISTINCT FROM NEW.issued_role_assignment_id OR
             OLD.issued_event_id IS DISTINCT FROM NEW.issued_event_id THEN
            RAISE EXCEPTION 'issued federation paper form evidence is immutable';
          END IF;
          IF OLD.recorded_event_id IS NOT NULL AND (
             OLD.payload IS DISTINCT FROM NEW.payload OR
             OLD.payload_hash IS DISTINCT FROM NEW.payload_hash OR
             OLD.signatures IS DISTINCT FROM NEW.signatures OR
             OLD.evidence_ids IS DISTINCT FROM NEW.evidence_ids OR
             OLD.recorded_by_user_id IS DISTINCT FROM NEW.recorded_by_user_id OR
             OLD.recorded_by_member_id IS DISTINCT FROM NEW.recorded_by_member_id OR
             OLD.recorded_role_assignment_id IS DISTINCT FROM NEW.recorded_role_assignment_id OR
             OLD.recorded_event_id IS DISTINCT FROM NEW.recorded_event_id OR
             OLD.recorded_at IS DISTINCT FROM NEW.recorded_at
          ) THEN
            RAISE EXCEPTION 'recorded federation paper form evidence is immutable';
          END IF;
          IF OLD.voided_event_id IS NOT NULL AND (
             OLD.voided_by_user_id IS DISTINCT FROM NEW.voided_by_user_id OR
             OLD.voided_by_member_id IS DISTINCT FROM NEW.voided_by_member_id OR
             OLD.voided_event_id IS DISTINCT FROM NEW.voided_event_id OR
             OLD.voided_at IS DISTINCT FROM NEW.voided_at OR
             OLD.void_reason IS DISTINCT FROM NEW.void_reason
          ) THEN
            RAISE EXCEPTION 'voided federation paper form evidence is immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_paper_forms_evidence_protected
          BEFORE UPDATE OR DELETE ON federation.paper_forms
          FOR EACH ROW EXECUTE FUNCTION federation.protect_paper_form_evidence();
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coop_app') THEN
            GRANT SELECT, INSERT, UPDATE ON federation.paper_forms TO coop_app;
            REVOKE DELETE ON federation.paper_forms FROM coop_app;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM federation.paper_forms) THEN
            RAISE EXCEPTION 'cannot downgrade 0014: federation paper-form evidence would be lost';
          END IF;
        END $$;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS federation.protect_paper_form_evidence CASCADE")
    op.drop_index(
        "ix_federation_paper_forms_epoch_status",
        table_name="paper_forms",
        schema="federation",
    )
    op.drop_table("paper_forms", schema="federation")
