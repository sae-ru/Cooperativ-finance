"""exchange dispute resolution

Revision ID: 0007_exchange_dispute_resolution
Revises: 0006_exchange_vertical_flow
Create Date: 2026-07-20 23:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_exchange_dispute_resolution"
down_revision: str | None = "0006_exchange_vertical_flow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "obligation_disputes",
        sa.Column("previous_obligation_status", sa.String(length=32), nullable=True),
        schema="exchange",
    )
    op.add_column(
        "obligation_disputes",
        sa.Column("previous_fulfillment_status", sa.String(length=32), nullable=True),
        schema="exchange",
    )
    op.add_column(
        "obligation_disputes",
        sa.Column("resolution_action", sa.String(length=32), nullable=True),
        schema="exchange",
    )
    op.add_column(
        "obligation_disputes",
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        schema="exchange",
    )
    op.add_column(
        "obligation_disputes",
        sa.Column("resolved_by_user_id", sa.UUID(), nullable=True),
        schema="exchange",
    )
    op.add_column(
        "obligation_disputes",
        sa.Column("resolved_by_member_id", sa.UUID(), nullable=True),
        schema="exchange",
    )
    op.add_column(
        "obligation_disputes",
        sa.Column("resolution_event_id", sa.UUID(), nullable=True),
        schema="exchange",
    )
    op.add_column(
        "obligation_disputes",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        schema="exchange",
    )
    op.add_column(
        "obligation_disputes",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        schema="exchange",
    )
    op.execute(
        """
        UPDATE exchange.obligation_disputes AS dispute
        SET previous_obligation_status = CASE
            WHEN obligation.quantity_fulfilled = obligation.quantity_total THEN 'FULFILLED'
            WHEN obligation.quantity_fulfilled > 0 THEN 'PARTIALLY_FULFILLED'
            WHEN obligation.due_at < now() THEN 'OVERDUE'
            ELSE 'ACTIVE'
        END
        FROM exchange.obligations AS obligation
        WHERE obligation.id = dispute.obligation_id
        """
    )
    op.execute(
        """
        UPDATE exchange.obligation_disputes AS dispute
        SET previous_fulfillment_status = CASE
            WHEN fulfillment.accepted_quantity = fulfillment.quantity THEN 'ACCEPTED'
            WHEN fulfillment.accepted_quantity > 0 THEN 'PARTIALLY_ACCEPTED'
            ELSE 'SUBMITTED'
        END
        FROM exchange.fulfillments AS fulfillment
        WHERE fulfillment.id = dispute.fulfillment_id
        """
    )
    op.alter_column(
        "obligation_disputes",
        "previous_obligation_status",
        existing_type=sa.String(length=32),
        nullable=False,
        schema="exchange",
    )
    op.create_check_constraint(
        op.f("ck_obligation_disputes_resolution_action_allowed"),
        "obligation_disputes",
        "resolution_action IS NULL OR resolution_action IN "
        "('REJECT_CLAIM','CONTINUE_PERFORMANCE','DEFAULT_OBLIGATION','CLOSE_OBLIGATION')",
        schema="exchange",
    )
    op.create_check_constraint(
        op.f("ck_obligation_disputes_resolution_consistent"),
        "obligation_disputes",
        "(status = 'OPEN' AND resolution_action IS NULL AND resolution_event_id IS NULL "
        "AND resolved_at IS NULL) OR (status IN ('RESOLVED','REJECTED') "
        "AND resolution_action IS NOT NULL AND resolution_event_id IS NOT NULL "
        "AND resolved_at IS NOT NULL)",
        schema="exchange",
    )
    op.create_check_constraint(
        op.f("ck_obligation_disputes_version_positive"),
        "obligation_disputes",
        "version >= 1",
        schema="exchange",
    )
    op.create_foreign_key(
        op.f("fk_obligation_disputes_resolved_by_user_id_users"),
        "obligation_disputes",
        "users",
        ["resolved_by_user_id"],
        ["id"],
        source_schema="exchange",
        referent_schema="identity",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_obligation_disputes_resolved_by_member_id_members"),
        "obligation_disputes",
        "members",
        ["resolved_by_member_id"],
        ["id"],
        source_schema="exchange",
        referent_schema="identity",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_obligation_disputes_resolution_event_id_signed_events"),
        "obligation_disputes",
        "signed_events",
        ["resolution_event_id"],
        ["event_id"],
        source_schema="exchange",
        referent_schema="journal",
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        op.f("uq_obligation_disputes_resolution_event_id"),
        "obligation_disputes",
        ["resolution_event_id"],
        schema="exchange",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM exchange.obligation_disputes
                WHERE status IN ('RESOLVED', 'REJECTED')
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0007: resolved dispute metadata would be lost';
            END IF;
        END
        $$;
        """
    )
    op.drop_constraint(
        op.f("uq_obligation_disputes_resolution_event_id"),
        "obligation_disputes",
        schema="exchange",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_obligation_disputes_resolution_event_id_signed_events"),
        "obligation_disputes",
        schema="exchange",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_obligation_disputes_resolved_by_member_id_members"),
        "obligation_disputes",
        schema="exchange",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_obligation_disputes_resolved_by_user_id_users"),
        "obligation_disputes",
        schema="exchange",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_obligation_disputes_version_positive"),
        "obligation_disputes",
        schema="exchange",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_obligation_disputes_resolution_consistent"),
        "obligation_disputes",
        schema="exchange",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_obligation_disputes_resolution_action_allowed"),
        "obligation_disputes",
        schema="exchange",
        type_="check",
    )
    for column in (
        "version",
        "resolved_at",
        "resolution_event_id",
        "resolved_by_member_id",
        "resolved_by_user_id",
        "resolution_notes",
        "resolution_action",
        "previous_fulfillment_status",
        "previous_obligation_status",
    ):
        op.drop_column("obligation_disputes", column, schema="exchange")
