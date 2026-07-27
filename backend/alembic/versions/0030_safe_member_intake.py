"""Add duplicate-aware staging imports for member intake.

Revision ID: 0030_safe_member_intake
Revises: 0029_identity_registry_scope
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_safe_member_intake"
down_revision: str | None = "0029_identity_registry_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "member_import_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=200), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("ready_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("invalid_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("applied_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_reason_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("previewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "status IN ('STAGED','PREVIEWED','APPROVED','REJECTED','APPLIED')",
            name="status_allowed",
        ),
        sa.CheckConstraint("row_count >= 1", name="row_count_positive"),
        sa.CheckConstraint(
            "ready_count >= 0 AND invalid_count >= 0 AND duplicate_count >= 0 "
            "AND applied_count >= 0",
            name="counts_nonnegative",
        ),
        sa.CheckConstraint(
            "ready_count + invalid_count + duplicate_count <= row_count",
            name="preview_counts_bounded",
        ),
        sa.CheckConstraint("applied_count <= ready_count", name="applied_count_bounded"),
        sa.CheckConstraint(
            "reviewed_by_user_id IS NULL OR reviewed_by_user_id <> created_by_user_id",
            name="independent_reviewer",
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.ForeignKeyConstraint(
            ["cooperative_id"], ["identity.cooperatives.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="identity",
    )
    op.create_index(
        "ix_member_import_batches_cooperative_status_created",
        "member_import_batches",
        ["cooperative_id", "status", "created_at"],
        schema="identity",
    )

    op.create_table(
        "member_import_rows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("identifier_type", sa.Text(), nullable=True),
        sa.Column("identifier_hash", sa.String(length=64), nullable=True),
        sa.Column("source_row_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("match_basis", sa.String(length=40), nullable=True),
        sa.Column("candidate_member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("row_number >= 1", name="row_number_positive"),
        sa.CheckConstraint(
            "status IN ('STAGED','READY','INVALID','DUPLICATE','APPLIED')",
            name="status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["identity.member_import_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "row_number", name="uq_member_import_row_number"),
        schema="identity",
    )
    op.create_index(
        "ix_member_import_rows_batch_status",
        "member_import_rows",
        ["batch_id", "status", "row_number"],
        schema="identity",
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON identity.member_import_batches, "
        "identity.member_import_rows TO coop_app"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_member_import_rows_batch_status",
        table_name="member_import_rows",
        schema="identity",
    )
    op.drop_table("member_import_rows", schema="identity")
    op.drop_index(
        "ix_member_import_batches_cooperative_status_created",
        table_name="member_import_batches",
        schema="identity",
    )
    op.drop_table("member_import_batches", schema="identity")