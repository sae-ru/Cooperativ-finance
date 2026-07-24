"""Link local marketplace purchases to exchange deals.

Revision ID: 0020_purchase_deal_bridge
Revises: 0019_exchange_participant
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_purchase_deal_bridge"
down_revision: str | None = "0019_exchange_participant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deals",
        sa.Column("source_purchase_intent_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="exchange",
    )
    op.create_foreign_key(
        op.f("fk_deals_source_purchase_intent_id_purchase_intents"),
        "deals",
        "purchase_intents",
        ["source_purchase_intent_id"],
        ["id"],
        source_schema="exchange",
        referent_schema="federation",
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        op.f("uq_deals_source_purchase_intent_id"),
        "deals",
        ["source_purchase_intent_id"],
        schema="exchange",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_deals_source_purchase_intent_id"),
        "deals",
        schema="exchange",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_deals_source_purchase_intent_id_purchase_intents"),
        "deals",
        schema="exchange",
        type_="foreignkey",
    )
    op.drop_column("deals", "source_purchase_intent_id", schema="exchange")