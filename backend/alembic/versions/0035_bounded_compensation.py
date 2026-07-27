"""Add bounded share compensation after final arbitration.

Revision ID: 0035_bounded_compensation
Revises: 0034_custody_continuity
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_bounded_compensation"
down_revision: str | None = "0034_custody_continuity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "exposure_commitments",
        sa.Column(
            "executed_amount",
            sa.Numeric(38, 12),
            server_default=sa.text("0"),
            nullable=False,
        ),
        schema="risk",
    )
    op.drop_constraint(
        op.f("ck_exposure_commitments_amounts_bounded"),
        "exposure_commitments",
        schema="risk",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_exposure_commitments_amounts_bounded"),
        "exposure_commitments",
        "amount_reserved > 0 AND max_loss > 0 AND max_loss <= amount_reserved "
        "AND executed_amount >= 0 AND executed_amount <= max_loss",
        schema="risk",
    )

    op.create_table(
        "compensation_transfers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("liability_case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trust_case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trust_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("commitment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("responsible_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(38, 12), nullable=False),
        sa.Column("denomination", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "authorization_evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("authorized_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("authorized_by_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "authorized_role_assignment_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("authorized_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_account_version_before", sa.Integer(), nullable=False),
        sa.Column(
            "destination_account_version_at_authorization",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("commitment_version_before", sa.Integer(), nullable=False),
        sa.Column("accepted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_by_member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "accepted_role_assignment_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("accepted_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("voided_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("voided_by_member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "voided_role_assignment_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("voided_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column(
            "void_evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("source_balance_before", sa.Numeric(38, 12), nullable=True),
        sa.Column("source_balance_after", sa.Numeric(38, 12), nullable=True),
        sa.Column("destination_balance_before", sa.Numeric(38, 12), nullable=True),
        sa.Column("destination_balance_after", sa.Numeric(38, 12), nullable=True),
        sa.Column(
            "authorized_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "amount > 0",
            name=op.f("ck_compensation_transfers_amount_positive"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_ACCEPTANCE','SETTLED','VOIDED')",
            name=op.f("ck_compensation_transfers_status_allowed"),
        ),
        sa.CheckConstraint(
            "source_account_id <> destination_account_id",
            name=op.f("ck_compensation_transfers_accounts_distinct"),
        ),
        sa.CheckConstraint(
            "responsible_member_id <> recipient_member_id",
            name=op.f("ck_compensation_transfers_members_distinct"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_compensation_transfers_version_positive"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(authorization_evidence_refs) = 'array' "
            "AND jsonb_array_length(authorization_evidence_refs) >= 1",
            name=op.f("ck_compensation_transfers_authorization_evidence_nonempty"),
        ),
        sa.CheckConstraint(
            "void_evidence_refs IS NULL OR jsonb_typeof(void_evidence_refs) = 'array'",
            name=op.f("ck_compensation_transfers_void_evidence_array"),
        ),
        sa.CheckConstraint(
            "(status = 'PENDING_ACCEPTANCE' AND accepted_event_id IS NULL "
            "AND voided_event_id IS NULL AND accepted_by_user_id IS NULL "
            "AND voided_by_user_id IS NULL AND source_balance_before IS NULL "
            "AND source_balance_after IS NULL AND destination_balance_before IS NULL "
            "AND destination_balance_after IS NULL) OR "
            "(status = 'SETTLED' AND accepted_event_id IS NOT NULL "
            "AND accepted_by_user_id IS NOT NULL AND accepted_by_member_id IS NOT NULL "
            "AND accepted_role_assignment_id IS NOT NULL AND accepted_at IS NOT NULL "
            "AND voided_event_id IS NULL AND source_balance_before IS NOT NULL "
            "AND source_balance_after IS NOT NULL AND destination_balance_before IS NOT NULL "
            "AND destination_balance_after IS NOT NULL) OR "
            "(status = 'VOIDED' AND voided_event_id IS NOT NULL "
            "AND voided_by_user_id IS NOT NULL AND voided_by_member_id IS NOT NULL "
            "AND voided_role_assignment_id IS NOT NULL AND voided_at IS NOT NULL "
            "AND accepted_event_id IS NULL AND source_balance_before IS NULL "
            "AND source_balance_after IS NULL AND destination_balance_before IS NULL "
            "AND destination_balance_after IS NULL)",
            name=op.f("ck_compensation_transfers_lifecycle_consistent"),
        ),
        sa.CheckConstraint(
            "status <> 'SETTLED' OR "
            "(source_balance_before - amount = source_balance_after "
            "AND destination_balance_before + amount = destination_balance_after)",
            name=op.f("ck_compensation_transfers_settlement_balanced"),
        ),
        sa.ForeignKeyConstraint(
            ["cooperative_id"], ["identity.cooperatives.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["liability_case_id"], ["risk.liability_cases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["trust_case_id"], ["trust.cases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["trust_decision_id"],
            ["trust.arbitration_decisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["commitment_id"], ["risk.exposure_commitments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_account_id"], ["risk.share_accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["destination_account_id"], ["risk.share_accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["responsible_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["recipient_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["authorized_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["authorized_by_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["authorized_role_assignment_id"],
            ["identity.role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["authorized_event_id"],
            ["journal.signed_events.event_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["accepted_role_assignment_id"],
            ["identity.role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_event_id"],
            ["journal.signed_events.event_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["voided_by_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["voided_role_assignment_id"],
            ["identity.role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voided_event_id"],
            ["journal.signed_events.event_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trust_decision_id",
            name=op.f("uq_compensation_transfers_trust_decision_id"),
        ),
        sa.UniqueConstraint(
            "authorized_event_id",
            name=op.f("uq_compensation_transfers_authorized_event_id"),
        ),
        sa.UniqueConstraint(
            "accepted_event_id",
            name=op.f("uq_compensation_transfers_accepted_event_id"),
        ),
        sa.UniqueConstraint(
            "voided_event_id",
            name=op.f("uq_compensation_transfers_voided_event_id"),
        ),
        schema="risk",
    )
    op.create_index(
        op.f("ix_compensation_transfers_cooperative_status"),
        "compensation_transfers",
        ["cooperative_id", "status"],
        schema="risk",
    )
    op.create_index(
        op.f("ix_compensation_transfers_recipient_status"),
        "compensation_transfers",
        ["recipient_member_id", "status"],
        schema="risk",
    )
    op.create_index(
        op.f("uq_compensation_transfers_active_case"),
        "compensation_transfers",
        ["liability_case_id"],
        unique=True,
        schema="risk",
        postgresql_where=sa.text("status IN ('PENDING_ACCEPTANCE','SETTLED')"),
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coop_app') THEN
            GRANT SELECT, INSERT, UPDATE ON risk.compensation_transfers TO coop_app;
            REVOKE DELETE ON risk.compensation_transfers FROM coop_app;
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
          IF EXISTS (SELECT 1 FROM risk.compensation_transfers)
             OR EXISTS (
               SELECT 1 FROM risk.exposure_commitments WHERE executed_amount <> 0
             )
          THEN
            RAISE EXCEPTION
              'Cannot downgrade 0035_bounded_compensation with compensation history present';
          END IF;
        END;
        $$;
        """
    )
    op.drop_index(
        op.f("uq_compensation_transfers_active_case"),
        table_name="compensation_transfers",
        schema="risk",
    )
    op.drop_index(
        op.f("ix_compensation_transfers_recipient_status"),
        table_name="compensation_transfers",
        schema="risk",
    )
    op.drop_index(
        op.f("ix_compensation_transfers_cooperative_status"),
        table_name="compensation_transfers",
        schema="risk",
    )
    op.drop_table("compensation_transfers", schema="risk")
    op.drop_constraint(
        op.f("ck_exposure_commitments_amounts_bounded"),
        "exposure_commitments",
        schema="risk",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_exposure_commitments_amounts_bounded"),
        "exposure_commitments",
        "amount_reserved > 0 AND max_loss > 0 AND max_loss <= amount_reserved",
        schema="risk",
    )
    op.drop_column("exposure_commitments", "executed_amount", schema="risk")
