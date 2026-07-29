"""Enforce atomic signed-event delivery records.

Revision ID: 0038_atomic_event_outbox
Revises: 0037_actor_assurance
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_atomic_event_outbox"
down_revision: str | None = "0037_actor_assurance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM journal.signed_events AS event
            WHERE (
              SELECT count(*)
              FROM journal.event_signatures AS signature
              WHERE signature.event_id = event.event_id
                AND signature.signature_scope = 'NODE'
            ) <> 1
            OR (
              SELECT count(*)
              FROM journal.outbox_messages AS message
              WHERE message.event_id = event.event_id
            ) <> 1
            OR NOT EXISTS (
              SELECT 1
              FROM journal.event_signatures AS signature
              JOIN node.key_records AS key ON key.id = signature.key_id
              WHERE signature.event_id = event.event_id
                AND signature.signature_scope = 'NODE'
                AND signature.algorithm = 'Ed25519'
                AND key.node_id = event.node_id
                AND key.purpose = 'NODE_SIGNING'
            )
            OR NOT EXISTS (
              SELECT 1
              FROM journal.outbox_messages AS message
              WHERE message.event_id = event.event_id
                AND message.topic = 'journal.event.committed'
                AND message.payload ->> 'event_id' = event.event_id::text
                AND message.payload ->> 'event_type' = event.event_type
                AND message.payload ->> 'event_hash' = event.event_hash
                AND message.payload ->> 'node_id' = event.node_id::text
                AND message.payload ->> 'local_sequence' = event.local_sequence::text
            )
          )
          THEN
            RAISE EXCEPTION
              'EVENT_DELIVERY_PRECHECK_FAILED: incomplete signed event history';
          END IF;
        END;
        $$;
        """
    )
    op.create_index(
        "uq_event_signatures_node_event",
        "event_signatures",
        ["event_id"],
        unique=True,
        schema="journal",
        postgresql_where=sa.text("signature_scope = 'NODE'"),
    )
    op.create_unique_constraint(
        "uq_outbox_event",
        "outbox_messages",
        ["event_id"],
        schema="journal",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION journal.enforce_event_delivery_atomicity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
          node_signature_count integer;
          valid_node_signature_count integer;
          outbox_count integer;
          valid_outbox_count integer;
        BEGIN
          SELECT count(*),
                 count(*) FILTER (
                   WHERE signature.algorithm = 'Ed25519'
                     AND key.node_id = NEW.node_id
                     AND key.purpose = 'NODE_SIGNING'
                 )
          INTO node_signature_count, valid_node_signature_count
          FROM journal.event_signatures AS signature
          LEFT JOIN node.key_records AS key ON key.id = signature.key_id
          WHERE signature.event_id = NEW.event_id
            AND signature.signature_scope = 'NODE';

          SELECT count(*),
                 count(*) FILTER (
                   WHERE message.topic = 'journal.event.committed'
                     AND message.payload ->> 'event_id' = NEW.event_id::text
                     AND message.payload ->> 'event_type' = NEW.event_type
                     AND message.payload ->> 'event_hash' = NEW.event_hash
                     AND message.payload ->> 'node_id' = NEW.node_id::text
                     AND message.payload ->> 'local_sequence' = NEW.local_sequence::text
                 )
          INTO outbox_count, valid_outbox_count
          FROM journal.outbox_messages AS message
          WHERE message.event_id = NEW.event_id;

          IF node_signature_count <> 1
             OR valid_node_signature_count <> 1
             OR outbox_count <> 1
             OR valid_outbox_count <> 1
          THEN
            RAISE EXCEPTION
              'EVENT_DELIVERY_ATOMICITY_VIOLATION: signature and outbox must commit with event'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER trg_signed_event_delivery_atomicity
          AFTER INSERT ON journal.signed_events
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW
          EXECUTE FUNCTION journal.enforce_event_delivery_atomicity();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_signed_event_delivery_atomicity ON journal.signed_events"
    )
    op.execute("DROP FUNCTION IF EXISTS journal.enforce_event_delivery_atomicity")
    op.drop_constraint(
        "uq_outbox_event",
        "outbox_messages",
        schema="journal",
        type_="unique",
    )
    op.drop_index(
        "uq_event_signatures_node_event",
        table_name="event_signatures",
        schema="journal",
    )
