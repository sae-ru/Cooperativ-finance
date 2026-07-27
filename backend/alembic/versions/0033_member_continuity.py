"""Add contained member exit and succession review workflow.

Revision ID: 0033_member_continuity
Revises: 0032_member_duplicate_merge
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_member_continuity"
down_revision: str | None = "0032_member_duplicate_merge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_members_status_allowed", "members", schema="identity", type_="check")
    op.create_check_constraint(
        "ck_members_status_allowed",
        "members",
        "status IN ('APPLICANT','PENDING_VERIFICATION','LIMITED','ACTIVE',"
        "'SUSPENDED','EXIT_PENDING','DECEASED_OR_INCAPACITATED',"
        "'SUCCESSION_REVIEW','CLOSED','REJECTED','EXITED','MERGED')",
        schema="identity",
    )
    op.create_table(
        "member_continuity_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_type", sa.String(length=32), nullable=False),
        sa.Column("previous_member_status", sa.String(length=32), nullable=False),
        sa.Column("contained_member_version", sa.Integer(), nullable=False),
        sa.Column("access_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reference_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "review_blockers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_reason_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "case_type IN ('VOLUNTARY_EXIT','DEATH_OR_INCAPACITY')",
            name="ck_member_continuity_cases_case_type_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_REVIEW','CONFIRMED','REJECTED','BLOCKED')",
            name="ck_member_continuity_cases_status_allowed",
        ),
        sa.CheckConstraint(
            "contained_member_version >= 2",
            name="ck_member_continuity_cases_member_version_valid",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(access_snapshot) = 'object'",
            name="ck_member_continuity_cases_access_snapshot_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reference_summary) = 'object'",
            name="ck_member_continuity_cases_reference_summary_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(review_blockers) = 'array'",
            name="ck_member_continuity_cases_review_blockers_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array' AND jsonb_array_length(evidence_refs) >= 1",
            name="ck_member_continuity_cases_evidence_nonempty_array",
        ),
        sa.CheckConstraint(
            "decided_by_user_id IS NULL OR decided_by_user_id <> requested_by_user_id",
            name="ck_member_continuity_cases_independent_reviewer",
        ),
        sa.CheckConstraint("version >= 1", name="ck_member_continuity_cases_version_positive"),
        sa.ForeignKeyConstraint(
            ["cooperative_id"], ["identity.cooperatives.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["member_id"], ["identity.members.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema="identity",
    )
    op.create_index(
        "ix_member_continuity_cases_cooperative_status_created",
        "member_continuity_cases",
        ["cooperative_id", "status", "created_at"],
        schema="identity",
    )
    op.create_index(
        "ix_member_continuity_cases_member_id",
        "member_continuity_cases",
        ["member_id"],
        schema="identity",
    )
    op.create_index(
        "uq_member_continuity_cases_pending_member",
        "member_continuity_cases",
        ["member_id"],
        unique=True,
        schema="identity",
        postgresql_where=sa.text("status = 'PENDING_REVIEW'"),
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM identity.member_continuity_cases)
               OR EXISTS (
                   SELECT 1 FROM identity.members
                   WHERE status IN (
                       'EXIT_PENDING','DECEASED_OR_INCAPACITATED','SUCCESSION_REVIEW','CLOSED'
                   )
               )
            THEN
                RAISE EXCEPTION
                    'Cannot downgrade 0033_member_continuity with continuity history present';
            END IF;
        END;
        $$;
        """
    )
    op.drop_index(
        "uq_member_continuity_cases_pending_member",
        table_name="member_continuity_cases",
        schema="identity",
    )
    op.drop_index(
        "ix_member_continuity_cases_member_id",
        table_name="member_continuity_cases",
        schema="identity",
    )
    op.drop_index(
        "ix_member_continuity_cases_cooperative_status_created",
        table_name="member_continuity_cases",
        schema="identity",
    )
    op.drop_table("member_continuity_cases", schema="identity")
    op.drop_constraint("ck_members_status_allowed", "members", schema="identity", type_="check")
    op.create_check_constraint(
        "ck_members_status_allowed",
        "members",
        "status IN ('APPLICANT','PENDING_VERIFICATION','LIMITED','ACTIVE',"
        "'SUSPENDED','REJECTED','EXITED','MERGED')",
        schema="identity",
    )
