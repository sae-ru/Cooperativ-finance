"""Require signed events for participant address mutations.

Revision ID: 0039_participant_address_events
Revises: 0038_atomic_event_outbox
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039_participant_address_events"
down_revision: str | None = "0038_atomic_event_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "participant_addresses",
        sa.Column(
            "event_tracking_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        schema="identity",
    )
    op.add_column(
        "participant_addresses",
        sa.Column("last_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="identity",
    )
    op.execute(
        "UPDATE identity.participant_addresses SET event_tracking_required = false"
    )
    op.create_foreign_key(
        op.f("fk_participant_addresses_last_event_id_signed_events"),
        "participant_addresses",
        "signed_events",
        ["last_event_id"],
        ["event_id"],
        source_schema="identity",
        referent_schema="journal",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index(
        "ix_participant_addresses_last_event_id",
        "participant_addresses",
        ["last_event_id"],
        schema="identity",
    )
    op.create_check_constraint(
        "tracked_address_has_event",
        "participant_addresses",
        "NOT event_tracking_required OR last_event_id IS NOT NULL",
        schema="identity",
    )
    op.execute(
        """
        CREATE FUNCTION identity.enforce_participant_address_event_link()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.event_tracking_required AND NEW.last_event_id IS NULL THEN
              RAISE EXCEPTION 'PARTICIPANT_ADDRESS_EVENT_REQUIRED'
                USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
          END IF;

          IF OLD.event_tracking_required AND NOT NEW.event_tracking_required THEN
            RAISE EXCEPTION 'PARTICIPANT_ADDRESS_TRACKING_CANNOT_BE_DISABLED'
              USING ERRCODE = '23514';
          END IF;

          IF ROW(
               NEW.member_id,
               NEW.cooperative_id,
               NEW.label,
               NEW.purpose,
               NEW.region_code,
               NEW.address_text,
               NEW.contact_name,
               NEW.contact_phone,
               NEW.instructions,
               NEW.is_default_pickup,
               NEW.is_default_delivery,
               NEW.status,
               NEW.version
             ) IS DISTINCT FROM ROW(
               OLD.member_id,
               OLD.cooperative_id,
               OLD.label,
               OLD.purpose,
               OLD.region_code,
               OLD.address_text,
               OLD.contact_name,
               OLD.contact_phone,
               OLD.instructions,
               OLD.is_default_pickup,
               OLD.is_default_delivery,
               OLD.status,
               OLD.version
             )
          THEN
            IF NEW.last_event_id IS NULL
               OR NEW.last_event_id IS NOT DISTINCT FROM OLD.last_event_id
            THEN
              RAISE EXCEPTION 'PARTICIPANT_ADDRESS_NEW_EVENT_REQUIRED'
                USING ERRCODE = '23514';
            END IF;
            NEW.event_tracking_required := true;
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_participant_address_event_link
          BEFORE INSERT OR UPDATE ON identity.participant_addresses
          FOR EACH ROW
          EXECUTE FUNCTION identity.enforce_participant_address_event_link();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_participant_address_event_link "
        "ON identity.participant_addresses"
    )
    op.execute("DROP FUNCTION IF EXISTS identity.enforce_participant_address_event_link")
    op.drop_constraint(
        "tracked_address_has_event",
        "participant_addresses",
        schema="identity",
        type_="check",
    )
    op.drop_index(
        "ix_participant_addresses_last_event_id",
        table_name="participant_addresses",
        schema="identity",
    )
    op.drop_constraint(
        op.f("fk_participant_addresses_last_event_id_signed_events"),
        "participant_addresses",
        schema="identity",
        type_="foreignkey",
    )
    op.drop_column("participant_addresses", "last_event_id", schema="identity")
    op.drop_column(
        "participant_addresses", "event_tracking_required", schema="identity"
    )