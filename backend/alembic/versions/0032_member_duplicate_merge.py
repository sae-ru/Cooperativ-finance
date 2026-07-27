"""Add controlled duplicate-member merge cases.

Revision ID: 0032_member_duplicate_merge
Revises: 0031_service_client_lifecycle
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_member_duplicate_merge"
down_revision: str | None = "0031_service_client_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_members_status_allowed", "members", schema="identity", type_="check")
    op.add_column(
        "members",
        sa.Column("merged_into_member_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="identity",
    )
    op.create_foreign_key(
        "fk_members_merged_into_member_id_members",
        "members",
        "members",
        ["merged_into_member_id"],
        ["id"],
        source_schema="identity",
        referent_schema="identity",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_members_status_allowed",
        "members",
        "status IN ('APPLICANT','PENDING_VERIFICATION','LIMITED','ACTIVE',"
        "'SUSPENDED','REJECTED','EXITED','MERGED')",
        schema="identity",
    )
    op.create_check_constraint(
        "ck_members_merge_link_consistent",
        "members",
        "(status = 'MERGED') = (merged_into_member_id IS NOT NULL) "
        "AND (merged_into_member_id IS NULL OR merged_into_member_id <> id)",
        schema="identity",
    )
    op.create_index(
        "ix_members_merged_into_member_id",
        "members",
        ["merged_into_member_id"],
        schema="identity",
    )

    op.create_table(
        "member_merge_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("survivor_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_expected_version", sa.Integer(), nullable=False),
        sa.Column("survivor_expected_version", sa.Integer(), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column(
            "blocker_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "source_member_id <> survivor_member_id",
            name="ck_member_merge_cases_distinct_members",
        ),
        sa.CheckConstraint(
            "source_expected_version >= 1 AND survivor_expected_version >= 1",
            name="ck_member_merge_cases_member_versions_positive",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array' AND jsonb_array_length(evidence_refs) >= 1",
            name="ck_member_merge_cases_evidence_nonempty_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(blocker_summary) = 'object'",
            name="ck_member_merge_cases_blockers_object",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_REVIEW','BLOCKED','APPROVED','REJECTED','EXPIRED')",
            name="ck_member_merge_cases_status_allowed",
        ),
        sa.CheckConstraint(
            "decided_by_user_id IS NULL OR decided_by_user_id <> requested_by_user_id",
            name="ck_member_merge_cases_independent_reviewer",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_member_merge_cases_expiry_after_creation",
        ),
        sa.CheckConstraint("version >= 1", name="ck_member_merge_cases_version_positive"),
        sa.ForeignKeyConstraint(
            ["cooperative_id"], ["identity.cooperatives.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["source_member_id"], ["identity.members.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["survivor_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema="identity",
    )
    op.create_index(
        "ix_member_merge_cases_cooperative_status_created",
        "member_merge_cases",
        ["cooperative_id", "status", "created_at"],
        schema="identity",
    )
    op.create_index(
        "ix_member_merge_cases_survivor_member_id",
        "member_merge_cases",
        ["survivor_member_id"],
        schema="identity",
    )
    op.create_index(
        "uq_member_merge_cases_pending_source",
        "member_merge_cases",
        ["source_member_id"],
        unique=True,
        schema="identity",
        postgresql_where=sa.text("status = 'PENDING_REVIEW'"),
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION identity.member_merge_external_blockers(p_member_id uuid)
        RETURNS jsonb
        LANGUAGE plpgsql
        AS $$
        DECLARE
            ref record;
            ref_count bigint;
            blockers jsonb := '{}'::jsonb;
        BEGIN
            FOR ref IN
                SELECT
                    source_ns.nspname AS schema_name,
                    source_table.relname AS table_name,
                    source_column.attname AS column_name
                FROM pg_constraint AS fk
                JOIN pg_class AS source_table ON source_table.oid = fk.conrelid
                JOIN pg_namespace AS source_ns ON source_ns.oid = source_table.relnamespace
                JOIN LATERAL unnest(fk.conkey) WITH ORDINALITY AS key_column(attnum, ordinality)
                    ON true
                JOIN pg_attribute AS source_column
                    ON source_column.attrelid = source_table.oid
                    AND source_column.attnum = key_column.attnum
                WHERE fk.contype = 'f'
                  AND fk.confrelid = 'identity.members'::regclass
                  AND cardinality(fk.conkey) = 1
                  AND NOT (
                    source_ns.nspname = 'identity'
                    AND source_table.relname IN (
                        'member_identifiers',
                        'member_import_rows',
                        'member_merge_cases',
                        'memberships',
                        'participant_addresses',
                        'users'
                    )
                  )
                ORDER BY source_ns.nspname, source_table.relname, source_column.attname
            LOOP
                EXECUTE format(
                    'SELECT count(*) FROM %I.%I WHERE %I = $1',
                    ref.schema_name,
                    ref.table_name,
                    ref.column_name
                )
                INTO ref_count
                USING p_member_id;
                IF ref_count > 0 THEN
                    blockers := blockers || jsonb_build_object(
                        format('%s.%s.%s', ref.schema_name, ref.table_name, ref.column_name),
                        ref_count
                    );
                END IF;
            END LOOP;
            RETURN blockers;
        END;
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM identity.member_merge_cases)
               OR EXISTS (SELECT 1 FROM identity.members WHERE status = 'MERGED')
            THEN
                RAISE EXCEPTION
                    'Cannot downgrade 0032_member_duplicate_merge with merge history present';
            END IF;
        END;
        $$;
        """
    )
    op.execute("DROP FUNCTION identity.member_merge_external_blockers(uuid)")
    op.drop_index(
        "uq_member_merge_cases_pending_source",
        table_name="member_merge_cases",
        schema="identity",
    )
    op.drop_index(
        "ix_member_merge_cases_survivor_member_id",
        table_name="member_merge_cases",
        schema="identity",
    )
    op.drop_index(
        "ix_member_merge_cases_cooperative_status_created",
        table_name="member_merge_cases",
        schema="identity",
    )
    op.drop_table("member_merge_cases", schema="identity")
    op.drop_index("ix_members_merged_into_member_id", table_name="members", schema="identity")
    op.drop_constraint(
        "ck_members_merge_link_consistent", "members", schema="identity", type_="check"
    )
    op.drop_constraint(
        "fk_members_merged_into_member_id_members",
        "members",
        schema="identity",
        type_="foreignkey",
    )
    op.drop_constraint("ck_members_status_allowed", "members", schema="identity", type_="check")
    op.drop_column("members", "merged_into_member_id", schema="identity")
    op.create_check_constraint(
        "ck_members_status_allowed",
        "members",
        "status IN ('APPLICANT','PENDING_VERIFICATION','LIMITED','ACTIVE',"
        "'SUSPENDED','REJECTED','EXITED')",
        schema="identity",
    )
