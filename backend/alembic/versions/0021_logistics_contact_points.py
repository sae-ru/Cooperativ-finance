"""Add private pickup and delivery contact points.

Revision ID: 0021_logistics_contacts
Revises: 0020_purchase_deal_bridge
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_logistics_contacts"
down_revision: str | None = "0020_purchase_deal_bridge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name, column in (
        ("pickup_address_text", sa.String(length=500)),
        ("pickup_contact_name", sa.String(length=200)),
        ("pickup_contact_phone", sa.String(length=80)),
        ("pickup_instructions", sa.Text()),
    ):
        op.add_column(
            "federated_offers",
            sa.Column(name, column, nullable=True),
            schema="federation",
        )

    for name, column in (
        ("delivery_address_text", sa.String(length=500)),
        ("delivery_contact_name", sa.String(length=200)),
        ("delivery_contact_phone", sa.String(length=80)),
        ("delivery_instructions", sa.Text()),
    ):
        op.add_column(
            "purchase_intents",
            sa.Column(name, column, nullable=True),
            schema="federation",
        )

    for name, column in (
        ("origin_contact_name", sa.String(length=200)),
        ("origin_contact_phone", sa.String(length=80)),
        ("origin_instructions", sa.Text()),
        ("destination_contact_name", sa.String(length=200)),
        ("destination_contact_phone", sa.String(length=80)),
        ("destination_instructions", sa.Text()),
    ):
        op.add_column(
            "logistics_orders",
            sa.Column(name, column, nullable=True),
            schema="exchange",
        )


def downgrade() -> None:
    for name in (
        "destination_instructions",
        "destination_contact_phone",
        "destination_contact_name",
        "origin_instructions",
        "origin_contact_phone",
        "origin_contact_name",
    ):
        op.drop_column("logistics_orders", name, schema="exchange")

    for name in (
        "delivery_instructions",
        "delivery_contact_phone",
        "delivery_contact_name",
        "delivery_address_text",
    ):
        op.drop_column("purchase_intents", name, schema="federation")

    for name in (
        "pickup_instructions",
        "pickup_contact_phone",
        "pickup_contact_name",
        "pickup_address_text",
    ):
        op.drop_column("federated_offers", name, schema="federation")
