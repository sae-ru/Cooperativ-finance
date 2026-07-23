"""add signed inter-node reservation and recovery evidence

Revision ID: 0017_peer_reservations
Revises: 0016_peer_protocol
Create Date: 2026-07-22 03:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_peer_reservations"
down_revision: str | None = "0016_peer_protocol"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_purchase_intents_status_allowed"),
        "purchase_intents",
        schema="federation",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_purchase_intents_status_allowed"),
        "purchase_intents",
        "status IN ('PREPARING','GOODS_RESERVED','PREPARED','COMMITTING','CANCELLING',"
        "'COMMITTED','COMPENSATED','CANCELLED','EXPIRED')",
        schema="federation",
    )
    for column in (
        sa.Column("commit_requested_event_id", sa.UUID(), nullable=True),
        sa.Column("commit_request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("commit_request_hash", sa.String(length=71), nullable=True),
        sa.Column("commit_request_signature", sa.LargeBinary(), nullable=True),
        sa.Column("commit_request_signer_fingerprint", sa.String(length=71), nullable=True),
        sa.Column("cancellation_requested_event_id", sa.UUID(), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("commit_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("purchase_intents", column, schema="federation")
    op.create_foreign_key(
        op.f("fk_purchase_intents_commit_requested_event_id_signed_events"),
        "purchase_intents",
        "signed_events",
        ["commit_requested_event_id"],
        ["event_id"],
        source_schema="federation",
        referent_schema="journal",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_purchase_intents_cancellation_requested_event_id_signed_events"),
        "purchase_intents",
        "signed_events",
        ["cancellation_requested_event_id"],
        ["event_id"],
        source_schema="federation",
        referent_schema="journal",
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        op.f("uq_purchase_intents_commit_requested_event_id"),
        "purchase_intents",
        ["commit_requested_event_id"],
        schema="federation",
    )
    op.create_unique_constraint(
        op.f("uq_purchase_intents_cancellation_requested_event_id"),
        "purchase_intents",
        ["cancellation_requested_event_id"],
        schema="federation",
    )
    op.create_check_constraint(
        op.f("ck_purchase_intents_commit_request_hash_sha256"),
        "purchase_intents",
        "commit_request_hash IS NULL OR commit_request_hash ~ '^sha256:[0-9a-f]{64}$'",
        schema="federation",
    )

    for column in (
        sa.Column("expiry_event_id", sa.UUID(), nullable=True),
        sa.Column("remote_commit_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("remote_commit_hash", sa.String(length=71), nullable=True),
        sa.Column("remote_commit_signature", sa.LargeBinary(), nullable=True),
        sa.Column("remote_commit_signer_fingerprint", sa.String(length=71), nullable=True),
        sa.Column("remote_release_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("remote_release_hash", sa.String(length=71), nullable=True),
        sa.Column("remote_release_signature", sa.LargeBinary(), nullable=True),
        sa.Column("remote_release_signer_fingerprint", sa.String(length=71), nullable=True),
    ):
        op.add_column("reservation_receipts", column, schema="federation")
    op.create_foreign_key(
        op.f("fk_reservation_receipts_expiry_event_id_signed_events"),
        "reservation_receipts",
        "signed_events",
        ["expiry_event_id"],
        ["event_id"],
        source_schema="federation",
        referent_schema="journal",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_reservation_receipts_remote_commit_hash_sha256"),
        "reservation_receipts",
        "remote_commit_hash IS NULL OR remote_commit_hash ~ '^sha256:[0-9a-f]{64}$'",
        schema="federation",
    )
    op.create_check_constraint(
        op.f("ck_reservation_receipts_remote_release_hash_sha256"),
        "reservation_receipts",
        "remote_release_hash IS NULL OR remote_release_hash ~ '^sha256:[0-9a-f]{64}$'",
        schema="federation",
    )

    op.create_table(
        "peer_resource_reservations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("buyer_node_id", sa.UUID(), nullable=False),
        sa.Column("buyer_intent_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("resource_ref", sa.String(length=200), nullable=False),
        sa.Column("offer_record_id", sa.UUID(), nullable=True),
        sa.Column("quote_record_id", sa.UUID(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=38, scale=12), nullable=False),
        sa.Column("unit_code", sa.String(length=32), nullable=False),
        sa.Column("capability", sa.String(length=40), nullable=False),
        sa.Column("exposure_amount", sa.Numeric(precision=38, scale=12), nullable=False),
        sa.Column("exposure_unit", sa.String(length=32), nullable=False),
        sa.Column("summary_hash", sa.String(length=71), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("receipt_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("receipt_hash", sa.String(length=71), nullable=False),
        sa.Column("receipt_signature", sa.LargeBinary(), nullable=False),
        sa.Column("signer_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("created_event_id", sa.UUID(), nullable=False),
        sa.Column("commit_event_id", sa.UUID(), nullable=True),
        sa.Column("release_event_id", sa.UUID(), nullable=True),
        sa.Column("expiry_event_id", sa.UUID(), nullable=True),
        sa.Column("commit_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("commit_hash", sa.String(length=71), nullable=True),
        sa.Column("commit_signature", sa.LargeBinary(), nullable=True),
        sa.Column("release_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("release_hash", sa.String(length=71), nullable=True),
        sa.Column("release_signature", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('GOODS','LOGISTICS')",
            name=op.f("ck_peer_resource_reservations_kind_allowed"),
        ),
        sa.CheckConstraint(
            "(kind = 'GOODS' AND offer_record_id IS NOT NULL AND quote_record_id IS NULL) OR "
            "(kind = 'LOGISTICS' AND offer_record_id IS NULL AND quote_record_id IS NOT NULL)",
            name=op.f("ck_peer_resource_reservations_resource_matches_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','COMMITTED','RELEASED','EXPIRED')",
            name=op.f("ck_peer_resource_reservations_status_allowed"),
        ),
        sa.CheckConstraint(
            "amount > 0 AND exposure_amount >= 0",
            name=op.f("ck_peer_resource_reservations_amount_positive"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_peer_resource_reservations_expiry_after_creation"),
        ),
        sa.CheckConstraint(
            "summary_hash ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_peer_resource_reservations_summary_hash_sha256"),
        ),
        sa.CheckConstraint(
            "receipt_hash ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_peer_resource_reservations_receipt_hash_sha256"),
        ),
        sa.CheckConstraint(
            "commit_hash IS NULL OR commit_hash ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_peer_resource_reservations_commit_hash_sha256"),
        ),
        sa.CheckConstraint(
            "release_hash IS NULL OR release_hash ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_peer_resource_reservations_release_hash_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["created_event_id"],
            ["journal.signed_events.event_id"],
            name=op.f("fk_peer_resource_reservations_created_event_id_signed_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["commit_event_id"],
            ["journal.signed_events.event_id"],
            name=op.f("fk_peer_resource_reservations_commit_event_id_signed_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_event_id"],
            ["journal.signed_events.event_id"],
            name=op.f("fk_peer_resource_reservations_release_event_id_signed_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["expiry_event_id"],
            ["journal.signed_events.event_id"],
            name=op.f("fk_peer_resource_reservations_expiry_event_id_signed_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_node_id"],
            ["federation.external_nodes.id"],
            name=op.f("fk_peer_resource_reservations_buyer_node_id_external_nodes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["offer_record_id"],
            ["federation.federated_offers.id"],
            name=op.f("fk_peer_resource_reservations_offer_record_id_federated_offers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quote_record_id"],
            ["federation.logistics_quotes.id"],
            name=op.f("fk_peer_resource_reservations_quote_record_id_logistics_quotes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_peer_resource_reservations")),
        sa.UniqueConstraint(
            "created_event_id",
            name=op.f("uq_peer_resource_reservations_created_event_id"),
        ),
        sa.UniqueConstraint(
            "commit_event_id",
            name=op.f("uq_peer_resource_reservations_commit_event_id"),
        ),
        sa.UniqueConstraint(
            "release_event_id",
            name=op.f("uq_peer_resource_reservations_release_event_id"),
        ),
        sa.UniqueConstraint(
            "expiry_event_id",
            name=op.f("uq_peer_resource_reservations_expiry_event_id"),
        ),
        sa.UniqueConstraint(
            "buyer_node_id",
            "buyer_intent_id",
            "kind",
            name="uq_peer_reservation_intent_kind",
        ),
        schema="federation",
    )
    op.create_index(
        "ix_peer_reservations_resource_status",
        "peer_resource_reservations",
        ["resource_ref", "status", "expires_at"],
        schema="federation",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION federation.protect_reservation_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'reservation evidence is append-only';
          END IF;
          IF TG_TABLE_NAME = 'reservation_receipts' THEN
            IF OLD.id IS DISTINCT FROM NEW.id OR
               OLD.intent_id IS DISTINCT FROM NEW.intent_id OR
               OLD.kind IS DISTINCT FROM NEW.kind OR
               OLD.resource_ref IS DISTINCT FROM NEW.resource_ref OR
               OLD.home_node_code IS DISTINCT FROM NEW.home_node_code OR
               OLD.amount IS DISTINCT FROM NEW.amount OR
               OLD.unit_code IS DISTINCT FROM NEW.unit_code OR
               OLD.receipt_payload IS DISTINCT FROM NEW.receipt_payload OR
               OLD.receipt_hash IS DISTINCT FROM NEW.receipt_hash OR
               OLD.node_signature IS DISTINCT FROM NEW.node_signature OR
               OLD.signer_fingerprint IS DISTINCT FROM NEW.signer_fingerprint OR
               OLD.created_event_id IS DISTINCT FROM NEW.created_event_id OR
               OLD.expires_at IS DISTINCT FROM NEW.expires_at OR
               (OLD.expiry_event_id IS NOT NULL AND
                OLD.expiry_event_id IS DISTINCT FROM NEW.expiry_event_id) OR
               (OLD.remote_commit_hash IS NOT NULL AND
                (OLD.remote_commit_payload, OLD.remote_commit_hash,
                 OLD.remote_commit_signature, OLD.remote_commit_signer_fingerprint)
                IS DISTINCT FROM
                (NEW.remote_commit_payload, NEW.remote_commit_hash,
                 NEW.remote_commit_signature, NEW.remote_commit_signer_fingerprint)) OR
               (OLD.remote_release_hash IS NOT NULL AND
                (OLD.remote_release_payload, OLD.remote_release_hash,
                 OLD.remote_release_signature, OLD.remote_release_signer_fingerprint)
                IS DISTINCT FROM
                (NEW.remote_release_payload, NEW.remote_release_hash,
                 NEW.remote_release_signature, NEW.remote_release_signer_fingerprint)) THEN
              RAISE EXCEPTION 'signed reservation receipt evidence is immutable';
            END IF;
            IF OLD.status = 'ACTIVE' AND NEW.status = 'EXPIRED' AND
               (NEW.expiry_event_id IS NULL OR NEW.expires_at > now()) THEN
              RAISE EXCEPTION 'reservation expiry evidence is required';
            END IF;
          ELSE
            IF OLD.id IS DISTINCT FROM NEW.id OR
               OLD.buyer_node_id IS DISTINCT FROM NEW.buyer_node_id OR
               OLD.buyer_intent_id IS DISTINCT FROM NEW.buyer_intent_id OR
               OLD.kind IS DISTINCT FROM NEW.kind OR
               OLD.resource_ref IS DISTINCT FROM NEW.resource_ref OR
               OLD.offer_record_id IS DISTINCT FROM NEW.offer_record_id OR
               OLD.quote_record_id IS DISTINCT FROM NEW.quote_record_id OR
               OLD.amount IS DISTINCT FROM NEW.amount OR
               OLD.unit_code IS DISTINCT FROM NEW.unit_code OR
               OLD.capability IS DISTINCT FROM NEW.capability OR
               OLD.exposure_amount IS DISTINCT FROM NEW.exposure_amount OR
               OLD.exposure_unit IS DISTINCT FROM NEW.exposure_unit OR
               OLD.summary_hash IS DISTINCT FROM NEW.summary_hash OR
               OLD.receipt_payload IS DISTINCT FROM NEW.receipt_payload OR
               OLD.receipt_hash IS DISTINCT FROM NEW.receipt_hash OR
               OLD.receipt_signature IS DISTINCT FROM NEW.receipt_signature OR
               OLD.signer_fingerprint IS DISTINCT FROM NEW.signer_fingerprint OR
               OLD.created_event_id IS DISTINCT FROM NEW.created_event_id OR
               OLD.expires_at IS DISTINCT FROM NEW.expires_at OR
               (OLD.expiry_event_id IS NOT NULL AND
                OLD.expiry_event_id IS DISTINCT FROM NEW.expiry_event_id) OR
               (OLD.commit_event_id IS NOT NULL AND
                OLD.commit_event_id IS DISTINCT FROM NEW.commit_event_id) OR
               (OLD.release_event_id IS NOT NULL AND
                OLD.release_event_id IS DISTINCT FROM NEW.release_event_id) OR
               (OLD.commit_hash IS NOT NULL AND
                (OLD.commit_payload, OLD.commit_hash, OLD.commit_signature)
                IS DISTINCT FROM (NEW.commit_payload, NEW.commit_hash, NEW.commit_signature)) OR
               (OLD.release_hash IS NOT NULL AND
                (OLD.release_payload, OLD.release_hash, OLD.release_signature)
                IS DISTINCT FROM (NEW.release_payload, NEW.release_hash, NEW.release_signature)) THEN
              RAISE EXCEPTION 'signed peer reservation evidence is immutable';
            END IF;
            IF OLD.status = 'ACTIVE' AND NEW.status = 'EXPIRED' AND
               (NEW.expiry_event_id IS NULL OR NEW.expires_at > now()) THEN
              RAISE EXCEPTION 'peer reservation expiry evidence is required';
            ELSIF OLD.status = 'ACTIVE' AND NEW.status = 'COMMITTED' AND
               (NEW.commit_hash IS NULL OR NEW.commit_event_id IS NULL) THEN
              RAISE EXCEPTION 'peer reservation commit evidence is required';
            ELSIF OLD.status IN ('ACTIVE','EXPIRED') AND NEW.status = 'RELEASED' AND
                  (NEW.release_hash IS NULL OR NEW.release_event_id IS NULL) THEN
              RAISE EXCEPTION 'peer reservation release evidence is required';
            ELSIF OLD.status IN ('COMMITTED','RELEASED') AND
                  OLD.status IS DISTINCT FROM NEW.status THEN
              RAISE EXCEPTION 'terminal peer reservation status is immutable';
            END IF;
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_reservation_receipts_evidence
          BEFORE UPDATE OR DELETE ON federation.reservation_receipts
          FOR EACH ROW EXECUTE FUNCTION federation.protect_reservation_evidence();
        CREATE TRIGGER trg_peer_resource_reservations_evidence
          BEFORE UPDATE OR DELETE ON federation.peer_resource_reservations
          FOR EACH ROW EXECUTE FUNCTION federation.protect_reservation_evidence();

        CREATE OR REPLACE FUNCTION federation.protect_purchase_intent_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'purchase intent evidence is append-only';
          END IF;
          IF OLD.id IS DISTINCT FROM NEW.id OR
             OLD.buyer_node_code IS DISTINCT FROM NEW.buyer_node_code OR
             OLD.buyer_user_id IS DISTINCT FROM NEW.buyer_user_id OR
             OLD.buyer_member_id IS DISTINCT FROM NEW.buyer_member_id OR
             OLD.buyer_role_assignment_id IS DISTINCT FROM NEW.buyer_role_assignment_id OR
             OLD.offer_record_id IS DISTINCT FROM NEW.offer_record_id OR
             OLD.quote_record_id IS DISTINCT FROM NEW.quote_record_id OR
             OLD.quantity IS DISTINCT FROM NEW.quantity OR
             OLD.unit_code IS DISTINCT FROM NEW.unit_code OR
             OLD.landed_cost_breakdown IS DISTINCT FROM NEW.landed_cost_breakdown OR
             OLD.summary_hash IS DISTINCT FROM NEW.summary_hash OR
             OLD.created_event_id IS DISTINCT FROM NEW.created_event_id OR
             OLD.expires_at IS DISTINCT FROM NEW.expires_at OR
             (OLD.commit_request_hash IS NOT NULL AND
              (OLD.commit_requested_event_id, OLD.commit_request_payload,
               OLD.commit_request_hash, OLD.commit_request_signature,
               OLD.commit_request_signer_fingerprint, OLD.commit_requested_at)
              IS DISTINCT FROM
              (NEW.commit_requested_event_id, NEW.commit_request_payload,
               NEW.commit_request_hash, NEW.commit_request_signature,
               NEW.commit_request_signer_fingerprint, NEW.commit_requested_at)) OR
             (OLD.cancellation_requested_event_id IS NOT NULL AND
              (OLD.cancellation_requested_event_id, OLD.cancellation_reason,
               OLD.cancellation_requested_at)
              IS DISTINCT FROM
              (NEW.cancellation_requested_event_id, NEW.cancellation_reason,
               NEW.cancellation_requested_at)) OR
             (OLD.committed_event_id IS NOT NULL AND
              OLD.committed_event_id IS DISTINCT FROM NEW.committed_event_id) OR
             (OLD.compensated_event_id IS NOT NULL AND
              OLD.compensated_event_id IS DISTINCT FROM NEW.compensated_event_id) OR
             (OLD.cancelled_event_id IS NOT NULL AND
              OLD.cancelled_event_id IS DISTINCT FROM NEW.cancelled_event_id) THEN
            RAISE EXCEPTION 'signed purchase intent evidence is immutable';
          END IF;
          IF NEW.status = 'COMMITTING' AND
             (NEW.commit_requested_event_id IS NULL OR
              NEW.commit_request_hash IS NULL OR
              NEW.commit_request_signature IS NULL) THEN
            RAISE EXCEPTION 'purchase commit request evidence is required';
          ELSIF NEW.status = 'CANCELLING' AND
                (NEW.cancellation_requested_event_id IS NULL OR
                 NEW.cancellation_reason IS NULL) THEN
            RAISE EXCEPTION 'purchase cancellation evidence is required';
          ELSIF OLD.status IN ('COMMITTED','COMPENSATED','CANCELLED','EXPIRED') AND
                OLD.status IS DISTINCT FROM NEW.status THEN
            RAISE EXCEPTION 'terminal purchase intent status is immutable';
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER trg_purchase_intents_evidence
          BEFORE UPDATE OR DELETE ON federation.purchase_intents
          FOR EACH ROW EXECUTE FUNCTION federation.protect_purchase_intent_evidence();

        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coop_app') THEN
            GRANT SELECT, INSERT, UPDATE ON federation.peer_resource_reservations TO coop_app;
            REVOKE DELETE ON federation.peer_resource_reservations FROM coop_app;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM federation.peer_resource_reservations) THEN
            RAISE EXCEPTION 'cannot downgrade 0017: peer reservation evidence would be lost';
          END IF;
        END $$;
        DROP TRIGGER IF EXISTS trg_reservation_receipts_evidence
          ON federation.reservation_receipts;
        DROP FUNCTION IF EXISTS federation.protect_purchase_intent_evidence CASCADE;
        DROP FUNCTION IF EXISTS federation.protect_reservation_evidence CASCADE;
        """
    )
    op.drop_index(
        "ix_peer_reservations_resource_status",
        table_name="peer_resource_reservations",
        schema="federation",
    )
    op.drop_table("peer_resource_reservations", schema="federation")
    op.drop_constraint(
        op.f("fk_reservation_receipts_expiry_event_id_signed_events"),
        "reservation_receipts",
        schema="federation",
        type_="foreignkey",
    )
    op.drop_column("reservation_receipts", "expiry_event_id", schema="federation")
    op.drop_constraint(
        op.f("ck_reservation_receipts_remote_release_hash_sha256"),
        "reservation_receipts",
        schema="federation",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_reservation_receipts_remote_commit_hash_sha256"),
        "reservation_receipts",
        schema="federation",
        type_="check",
    )
    for name in (
        "remote_release_signer_fingerprint",
        "remote_release_signature",
        "remote_release_hash",
        "remote_release_payload",
        "remote_commit_signer_fingerprint",
        "remote_commit_signature",
        "remote_commit_hash",
        "remote_commit_payload",
    ):
        op.drop_column("reservation_receipts", name, schema="federation")
    op.drop_constraint(
        op.f("ck_purchase_intents_commit_request_hash_sha256"),
        "purchase_intents",
        schema="federation",
        type_="check",
    )
    op.drop_constraint(
        op.f("uq_purchase_intents_cancellation_requested_event_id"),
        "purchase_intents",
        schema="federation",
        type_="unique",
    )
    op.drop_constraint(
        op.f("uq_purchase_intents_commit_requested_event_id"),
        "purchase_intents",
        schema="federation",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_purchase_intents_cancellation_requested_event_id_signed_events"),
        "purchase_intents",
        schema="federation",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_purchase_intents_commit_requested_event_id_signed_events"),
        "purchase_intents",
        schema="federation",
        type_="foreignkey",
    )
    for name in (
        "cancellation_requested_at",
        "commit_requested_at",
        "cancellation_reason",
        "cancellation_requested_event_id",
        "commit_request_signer_fingerprint",
        "commit_request_signature",
        "commit_request_hash",
        "commit_request_payload",
        "commit_requested_event_id",
    ):
        op.drop_column("purchase_intents", name, schema="federation")
    op.drop_constraint(
        op.f("ck_purchase_intents_status_allowed"),
        "purchase_intents",
        schema="federation",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_purchase_intents_status_allowed"),
        "purchase_intents",
        "status IN ('PREPARING','GOODS_RESERVED','PREPARED','COMMITTED',"
        "'COMPENSATED','CANCELLED','EXPIRED')",
        schema="federation",
    )
