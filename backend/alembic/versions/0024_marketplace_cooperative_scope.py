"""Persist cooperative ownership for marketplace records.

Revision ID: 0024_marketplace_scope
Revises: 0023_antifraud_controls
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_marketplace_scope"
down_revision: str | None = "0023_antifraud_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = (
    (
        "federated_offers",
        "ix_federated_offers_cooperative_product_status",
        ("cooperative_id", "product_code", "status"),
    ),
    (
        "logistics_quotes",
        "ix_logistics_quotes_cooperative_status",
        ("cooperative_id", "status", "valid_until"),
    ),
    (
        "purchase_intents",
        "ix_purchase_intents_cooperative_buyer_status",
        ("cooperative_id", "buyer_member_id", "status"),
    ),
)


def upgrade() -> None:
    for table_name, _index_name, _columns in TABLES:
        op.add_column(
            table_name,
            sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=True),
            schema="federation",
        )

    op.execute(
        """
        UPDATE federation.federated_offers AS item
        SET cooperative_id = event.actor_organization_id
        FROM journal.signed_events AS event
        WHERE event.event_id = item.published_event_id
          AND event.actor_organization_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE federation.logistics_quotes AS item
        SET cooperative_id = event.actor_organization_id
        FROM journal.signed_events AS event
        WHERE event.event_id = item.issued_event_id
          AND event.actor_organization_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE federation.purchase_intents AS item
        SET cooperative_id = event.actor_organization_id
        FROM journal.signed_events AS event
        WHERE event.event_id = item.created_event_id
          AND event.actor_organization_id IS NOT NULL
        """
    )

    op.execute(
        """
        UPDATE federation.federated_offers AS item
        SET cooperative_id = (
            SELECT membership.cooperative_id
            FROM identity.memberships AS membership
            WHERE membership.member_id = item.publisher_member_id
              AND membership.status = 'ACTIVE'
            ORDER BY membership.joined_at, membership.id
            LIMIT 1
        )
        WHERE item.cooperative_id IS NULL
          AND item.publisher_member_id IS NOT NULL
          AND (
              SELECT count(*)
              FROM identity.memberships AS membership_count
              WHERE membership_count.member_id = item.publisher_member_id
                AND membership_count.status = 'ACTIVE'
          ) = 1
        """
    )
    op.execute(
        """
        UPDATE federation.logistics_quotes AS item
        SET cooperative_id = (
            SELECT membership.cooperative_id
            FROM journal.signed_events AS event
            JOIN identity.memberships AS membership
              ON membership.member_id = event.actor_person_id
             AND membership.status = 'ACTIVE'
            WHERE event.event_id = item.issued_event_id
            ORDER BY membership.joined_at, membership.id
            LIMIT 1
        )
        WHERE item.cooperative_id IS NULL
          AND (
              SELECT count(*)
              FROM journal.signed_events AS event_count
              JOIN identity.memberships AS membership_count
                ON membership_count.member_id = event_count.actor_person_id
               AND membership_count.status = 'ACTIVE'
              WHERE event_count.event_id = item.issued_event_id
          ) = 1
        """
    )
    op.execute(
        """
        UPDATE federation.purchase_intents AS item
        SET cooperative_id = (
            SELECT membership.cooperative_id
            FROM identity.memberships AS membership
            WHERE membership.member_id = item.buyer_member_id
              AND membership.status = 'ACTIVE'
            ORDER BY membership.joined_at, membership.id
            LIMIT 1
        )
        WHERE item.cooperative_id IS NULL
          AND (
              SELECT count(*)
              FROM identity.memberships AS membership_count
              WHERE membership_count.member_id = item.buyer_member_id
                AND membership_count.status = 'ACTIVE'
          ) = 1
        """
    )

    default_cooperative = """
        SELECT cooperative.id
        FROM identity.cooperatives AS cooperative
        JOIN node.node_profiles AS node_profile
          ON node_profile.node_code = cooperative.code
        WHERE cooperative.status = 'ACTIVE'
        ORDER BY cooperative.created_at, cooperative.id
        LIMIT 1
    """
    for table_name, _index_name, _columns in TABLES:
        op.execute(
            f"""
            UPDATE federation.{table_name}
            SET cooperative_id = ({default_cooperative})
            WHERE cooperative_id IS NULL
            """
        )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM federation.federated_offers WHERE cooperative_id IS NULL
            ) OR EXISTS (
                SELECT 1 FROM federation.logistics_quotes WHERE cooperative_id IS NULL
            ) OR EXISTS (
                SELECT 1 FROM federation.purchase_intents WHERE cooperative_id IS NULL
            ) THEN
                RAISE EXCEPTION 'marketplace cooperative ownership backfill is incomplete';
            END IF;
        END;
        $$;
        """
    )

    for table_name, index_name, columns in TABLES:
        op.alter_column(
            table_name,
            "cooperative_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=False,
            schema="federation",
        )
        op.create_foreign_key(
            f"fk_{table_name}_cooperative_id",
            table_name,
            "cooperatives",
            ["cooperative_id"],
            ["id"],
            source_schema="federation",
            referent_schema="identity",
            ondelete="RESTRICT",
        )
        op.create_index(
            index_name,
            table_name,
            list(columns),
            schema="federation",
        )


def downgrade() -> None:
    for table_name, index_name, _columns in reversed(TABLES):
        op.drop_index(index_name, table_name=table_name, schema="federation")
        op.drop_constraint(
            f"fk_{table_name}_cooperative_id",
            table_name,
            schema="federation",
            type_="foreignkey",
        )
        op.drop_column(table_name, "cooperative_id", schema="federation")