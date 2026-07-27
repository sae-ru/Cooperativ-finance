"""Add emergency physical custody continuity workflow.

Revision ID: 0034_custody_continuity
Revises: 0033_member_continuity
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034_custody_continuity"
down_revision: str | None = "0033_member_continuity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custody_continuity_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "member_continuity_case_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("source_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_assignment_version", sa.Integer(), nullable=False),
        sa.Column("target_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_role_assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_assignment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("handover_place", sa.String(length=500), nullable=False),
        sa.Column("temporary_valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "blocked_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_reason_code", sa.String(length=100), nullable=True),
        sa.Column("requested_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("inventory_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "status IN ('INVENTORY_PENDING','PENDING_APPROVAL','PENDING_ACCEPTANCE',"
            "'ACCEPTED','REJECTED','BLOCKED')",
            name="ck_custody_continuity_cases_status_allowed",
        ),
        sa.CheckConstraint(
            "source_assignment_version >= 1",
            name="ck_custody_continuity_cases_source_version_positive",
        ),
        sa.CheckConstraint(
            "target_member_id <> source_member_id",
            name="ck_custody_continuity_cases_different_target_member",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array' "
            "AND jsonb_array_length(evidence_refs) >= 1",
            name="ck_custody_continuity_cases_evidence_nonempty_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(blocked_reasons) = 'array'",
            name="ck_custody_continuity_cases_blockers_array",
        ),
        sa.CheckConstraint(
            "temporary_valid_until > created_at",
            name="ck_custody_continuity_cases_temporary_period_valid",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_custody_continuity_cases_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["cooperative_id"], ["identity.cooperatives.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["member_continuity_case_id"],
            ["identity.member_continuity_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"], ["assets.warehouses.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_assignment_id"],
            ["risk.responsibility_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_role_assignment_id"],
            ["identity.role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_assignment_id"],
            ["risk.responsibility_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_event_id"],
            ["journal.signed_events.event_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_event_id"],
            ["journal.signed_events.event_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_event_id"],
            ["journal.signed_events.event_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="assets",
    )
    op.create_index(
        "ix_custody_continuity_cases_cooperative_status_created",
        "custody_continuity_cases",
        ["cooperative_id", "status", "created_at"],
        schema="assets",
    )
    op.create_index(
        "uq_custody_continuity_cases_open_source",
        "custody_continuity_cases",
        ["source_assignment_id"],
        unique=True,
        schema="assets",
        postgresql_where=sa.text(
            "status IN ('INVENTORY_PENDING','PENDING_APPROVAL',"
            "'PENDING_ACCEPTANCE','BLOCKED')"
        ),
    )

    op.add_column(
        "inventory_lots",
        sa.Column("continuity_hold_case_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="assets",
    )
    op.create_foreign_key(
        "fk_inventory_lots_continuity_hold_case_id",
        "inventory_lots",
        "custody_continuity_cases",
        ["continuity_hold_case_id"],
        ["id"],
        source_schema="assets",
        referent_schema="assets",
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_inventory_lots_continuity_hold_case_id",
        "inventory_lots",
        ["continuity_hold_case_id"],
        schema="assets",
    )

    op.create_table(
        "custody_continuity_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lot_version", sa.Integer(), nullable=False),
        sa.Column("expected_quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("actual_quantity", sa.Numeric(38, 12), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("condition_notes", sa.String(length=1000), nullable=True),
        sa.Column(
            "evidence_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("attested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "lot_version >= 1",
            name="ck_custody_continuity_items_lot_version_positive",
        ),
        sa.CheckConstraint(
            "expected_quantity >= 0",
            name="ck_custody_continuity_items_expected_quantity_nonnegative",
        ),
        sa.CheckConstraint(
            "actual_quantity IS NULL OR actual_quantity >= 0",
            name="ck_custody_continuity_items_actual_quantity_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','MATCH','DISCREPANCY')",
            name="ck_custody_continuity_items_status_allowed",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_ids) = 'array'",
            name="ck_custody_continuity_items_evidence_array",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_custody_continuity_items_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["assets.custody_continuity_cases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"], ["assets.inventory_lots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["attested_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["journal.signed_events.event_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "case_id", "lot_id", name="uq_custody_continuity_item_lot"
        ),
        schema="assets",
    )
    op.create_index(
        "ix_custody_continuity_items_case_status",
        "custody_continuity_items",
        ["case_id", "status", "lot_id"],
        schema="assets",
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coop_app') THEN
            GRANT SELECT, INSERT, UPDATE
              ON assets.custody_continuity_cases,
                 assets.custody_continuity_items
              TO coop_app;
            REVOKE DELETE
              ON assets.custody_continuity_cases,
                 assets.custody_continuity_items
              FROM coop_app;
          END IF;
        END;
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM assets.custody_continuity_cases)
            THEN
                RAISE EXCEPTION
                    'Cannot downgrade 0034_custody_continuity with custody history present';
            END IF;
        END;
        $$;
        """
    )
    op.drop_index(
        "ix_custody_continuity_items_case_status",
        table_name="custody_continuity_items",
        schema="assets",
    )
    op.drop_table("custody_continuity_items", schema="assets")
    op.drop_index(
        "ix_inventory_lots_continuity_hold_case_id",
        table_name="inventory_lots",
        schema="assets",
    )
    op.drop_constraint(
        "fk_inventory_lots_continuity_hold_case_id",
        "inventory_lots",
        schema="assets",
        type_="foreignkey",
    )
    op.drop_column("inventory_lots", "continuity_hold_case_id", schema="assets")
    op.drop_index(
        "uq_custody_continuity_cases_open_source",
        table_name="custody_continuity_cases",
        schema="assets",
    )
    op.drop_index(
        "ix_custody_continuity_cases_cooperative_status_created",
        table_name="custody_continuity_cases",
        schema="assets",
    )
    op.drop_table("custody_continuity_cases", schema="assets")
