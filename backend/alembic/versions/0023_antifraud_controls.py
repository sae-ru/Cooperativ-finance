"""Add deterministic anti-fraud scans and independently reviewed signals.

Revision ID: 0023_antifraud_controls
Revises: 0022_participant_addresses
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_antifraud_controls"
down_revision: str | None = "0022_participant_addresses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "antifraud_scans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False),
        sa.Column("lookback_hours", sa.Integer(), nullable=False),
        sa.Column("input_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("initiated_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("initiated_by_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "initiated_role_assignment_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("completed_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "lookback_hours BETWEEN 1 AND 2160", name="lookback_bounded"
        ),
        sa.CheckConstraint("finding_count >= 0", name="finding_count_nonnegative"),
        sa.ForeignKeyConstraint(
            ["cooperative_id"], ["identity.cooperatives.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["completed_event_id"],
            ["journal.signed_events.event_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["initiated_role_assignment_id"],
            ["identity.role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("completed_event_id"),
        schema="risk",
    )
    op.create_index(
        "ix_antifraud_scans_cooperative_created",
        "antifraud_scans",
        ["cooperative_id", "created_at"],
        schema="risk",
    )
    op.create_table(
        "antifraud_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_code", sa.String(length=64), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("automation_action", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason_key", sa.String(length=160), nullable=False),
        sa.Column("observed_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("threshold_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dedupe_key", sa.String(length=71), nullable=False),
        sa.Column(
            "occurrence_count", sa.Integer(), server_default="1", nullable=False
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detected_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("detected_by_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "detected_role_assignment_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("detected_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewer_member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "reviewer_role_assignment_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "review_started_event_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("decision_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_rationale", sa.Text(), nullable=True),
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
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "automation_action IN ('WARN','HOLD')", name="action_allowed"
        ),
        sa.CheckConstraint(
            "dedupe_key ~ '^sha256:[0-9a-f]{64}$'", name="dedupe_key_sha256"
        ),
        sa.CheckConstraint(
            "last_seen_at >= first_seen_at", name="seen_period_valid"
        ),
        sa.CheckConstraint("occurrence_count >= 1", name="occurrence_count_positive"),
        sa.CheckConstraint(
            "reviewer_member_id IS NULL OR reviewer_member_id <> detected_by_member_id",
            name="reviewer_independent",
        ),
        sa.CheckConstraint(
            "(status = 'OPEN' AND reviewer_user_id IS NULL "
            "AND reviewer_member_id IS NULL AND reviewer_role_assignment_id IS NULL "
            "AND review_started_event_id IS NULL AND decision_event_id IS NULL) OR "
            "(status = 'IN_REVIEW' AND reviewer_user_id IS NOT NULL "
            "AND reviewer_member_id IS NOT NULL "
            "AND reviewer_role_assignment_id IS NOT NULL "
            "AND review_started_event_id IS NOT NULL "
            "AND decision_event_id IS NULL) OR "
            "(status IN ('CLEARED','CONFIRMED') AND reviewer_user_id IS NOT NULL "
            "AND reviewer_member_id IS NOT NULL "
            "AND reviewer_role_assignment_id IS NOT NULL "
            "AND review_started_event_id IS NOT NULL "
            "AND decision_event_id IS NOT NULL "
            "AND decision_rationale IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="review_lifecycle_consistent",
        ),
        sa.CheckConstraint(
            "severity IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="severity_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','IN_REVIEW','CLEARED','CONFIRMED')",
            name="status_allowed",
        ),
        sa.CheckConstraint(
            "subject_type IN ('MEMBER','OFFER','LOGISTICS_QUOTE',"
            "'PURCHASE_INTENT','SHARE_ACCOUNT','EXPOSURE_COMMITMENT')",
            name="subject_type_allowed",
        ),
        sa.CheckConstraint(
            "rule_version >= 1 AND version >= 1", name="versions_positive"
        ),
        sa.ForeignKeyConstraint(
            ["cooperative_id"], ["identity.cooperatives.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"], ["risk.antifraud_scans.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["detected_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["detected_by_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["detected_role_assignment_id"],
            ["identity.role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["detected_event_id"],
            ["journal.signed_events.event_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_role_assignment_id"],
            ["identity.role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["review_started_event_id"],
            ["journal.signed_events.event_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decision_event_id"],
            ["journal.signed_events.event_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("detected_event_id"),
        sa.UniqueConstraint("review_started_event_id"),
        sa.UniqueConstraint("decision_event_id"),
        schema="risk",
    )
    op.create_index(
        "ix_antifraud_signals_cooperative_status",
        "antifraud_signals",
        ["cooperative_id", "status", "severity", "last_seen_at"],
        schema="risk",
    )
    op.create_index(
        "ix_antifraud_signals_subject",
        "antifraud_signals",
        ["subject_type", "subject_id"],
        schema="risk",
    )
    op.create_index(
        "uq_antifraud_signals_active_subject",
        "antifraud_signals",
        ["cooperative_id", "rule_code", "subject_type", "subject_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('OPEN','IN_REVIEW','CONFIRMED')"),
        schema="risk",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION risk.protect_antifraud_detection()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF ROW(
                OLD.cooperative_id, OLD.scan_id, OLD.rule_code, OLD.rule_version,
                OLD.subject_type, OLD.subject_id, OLD.severity, OLD.automation_action,
                OLD.reason_key, OLD.observed_data, OLD.threshold_data, OLD.dedupe_key,
                OLD.first_seen_at, OLD.detected_by_user_id, OLD.detected_by_member_id,
                OLD.detected_role_assignment_id, OLD.detected_event_id
            ) IS DISTINCT FROM ROW(
                NEW.cooperative_id, NEW.scan_id, NEW.rule_code, NEW.rule_version,
                NEW.subject_type, NEW.subject_id, NEW.severity, NEW.automation_action,
                NEW.reason_key, NEW.observed_data, NEW.threshold_data, NEW.dedupe_key,
                NEW.first_seen_at, NEW.detected_by_user_id, NEW.detected_by_member_id,
                NEW.detected_role_assignment_id, NEW.detected_event_id
            ) THEN
                RAISE EXCEPTION 'antifraud detection facts are immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_protect_antifraud_detection
        BEFORE UPDATE ON risk.antifraud_signals
        FOR EACH ROW EXECUTE FUNCTION risk.protect_antifraud_detection();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_protect_antifraud_detection "
        "ON risk.antifraud_signals"
    )
    op.execute("DROP FUNCTION IF EXISTS risk.protect_antifraud_detection()")
    op.drop_index(
        "uq_antifraud_signals_active_subject",
        table_name="antifraud_signals",
        schema="risk",
    )
    op.drop_index(
        "ix_antifraud_signals_subject",
        table_name="antifraud_signals",
        schema="risk",
    )
    op.drop_index(
        "ix_antifraud_signals_cooperative_status",
        table_name="antifraud_signals",
        schema="risk",
    )
    op.drop_table("antifraud_signals", schema="risk")
    op.drop_index(
        "ix_antifraud_scans_cooperative_created",
        table_name="antifraud_scans",
        schema="risk",
    )
    op.drop_table("antifraud_scans", schema="risk")
