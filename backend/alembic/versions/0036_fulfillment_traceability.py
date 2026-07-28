"""Add immutable product-fulfillment provenance.

Revision ID: 0036_fulfillment_traceability
Revises: 0035_bounded_compensation
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_fulfillment_traceability"
down_revision: str | None = "0035_bounded_compensation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fulfillment_provenance",
        sa.Column("fulfillment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("redemption_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("right_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_owner_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("intended_recipient_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("linked_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name=op.f("ck_fulfillment_provenance_quantity_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["fulfillment_id"], ["exchange.fulfillments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["cooperative_id"], ["identity.cooperatives.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["redemption_id"],
            ["assets.commodity_right_redemptions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["right_id"], ["assets.commodity_rights.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"], ["assets.inventory_lots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["assets.products.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_owner_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["intended_recipient_member_id"],
            ["identity.members.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["linked_event_id"], ["journal.signed_events.event_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("fulfillment_id"),
        sa.UniqueConstraint(
            "redemption_id",
            name=op.f("uq_fulfillment_provenance_redemption"),
        ),
        sa.UniqueConstraint(
            "linked_event_id",
            name=op.f("uq_fulfillment_provenance_event"),
        ),
        schema="exchange",
    )
    op.create_index(
        op.f("ix_fulfillment_provenance_lot_created"),
        "fulfillment_provenance",
        ["lot_id", "created_at"],
        schema="exchange",
    )
    op.create_index(
        op.f("ix_fulfillment_provenance_right_created"),
        "fulfillment_provenance",
        ["right_id", "created_at"],
        schema="exchange",
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coop_app') THEN
            GRANT SELECT, INSERT ON exchange.fulfillment_provenance TO coop_app;
            REVOKE UPDATE, DELETE ON exchange.fulfillment_provenance FROM coop_app;
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
          IF EXISTS (SELECT 1 FROM exchange.fulfillment_provenance)
          THEN
            RAISE EXCEPTION
              'Cannot downgrade 0036_fulfillment_traceability with provenance history present';
          END IF;
        END;
        $$;
        """
    )
    op.drop_index(
        op.f("ix_fulfillment_provenance_right_created"),
        table_name="fulfillment_provenance",
        schema="exchange",
    )
    op.drop_index(
        op.f("ix_fulfillment_provenance_lot_created"),
        table_name="fulfillment_provenance",
        schema="exchange",
    )
    op.drop_table("fulfillment_provenance", schema="exchange")
