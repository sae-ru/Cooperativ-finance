"""commodity rights and exact lot balances

Revision ID: 0005_commodity_rights
Revises: 0004_inventory_vertical_flow
Create Date: 2026-07-20 18:05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_commodity_rights"
down_revision: str | None = "0004_inventory_vertical_flow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_role_assignments_role_allowed",
        "role_assignments",
        schema="identity",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_role_assignments_role_allowed"),
        "role_assignments",
        "role_code IN ('MEMBER_REGISTRAR','COOPERATIVE_ADMIN','DATA_STEWARD',"
        "'WAREHOUSE_CUSTODIAN','INVENTORY_CONTROLLER','LOGISTICS_OPERATOR','RIGHTS_OPERATOR',"
        "'RISK_ADMIN','SECURITY_ADMIN','NODE_REGISTRAR','AUDITOR')",
        schema="identity",
    )
    op.drop_constraint(
        op.f("ck_inventory_movements_type_allowed"),
        "inventory_movements",
        schema="assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_inventory_movements_type_allowed"),
        "inventory_movements",
        "movement_type IN ('ATTESTED_RECEIPT','DISCREPANCY_ADJUSTMENT','RIGHT_REDEMPTION')",
        schema="assets",
    )
    op.create_table(
        "lot_balances",
        sa.Column("lot_id", sa.UUID(), nullable=False),
        sa.Column("verified_quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("available_quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("reserved_quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("rights_issued_quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("redeemed_quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("quarantined_quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("backing_shortfall_quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "available_quantity + reserved_quantity + rights_issued_quantity + "
            "quarantined_quantity = verified_quantity + backing_shortfall_quantity",
            name=op.f("ck_lot_balances_allocation_matches_backing"),
        ),
        sa.CheckConstraint(
            "verified_quantity >= 0 AND available_quantity >= 0 AND reserved_quantity >= 0 "
            "AND rights_issued_quantity >= 0 AND redeemed_quantity >= 0 "
            "AND quarantined_quantity >= 0 AND backing_shortfall_quantity >= 0",
            name=op.f("ck_lot_balances_quantities_nonnegative"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_lot_balances_version_positive")),
        sa.ForeignKeyConstraint(
            ["lot_id"],
            ["assets.inventory_lots.id"],
            name=op.f("fk_lot_balances_lot_id_inventory_lots"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("lot_id", name=op.f("pk_lot_balances")),
        schema="assets",
    )
    op.create_table(
        "inventory_reservations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=False),
        sa.Column("purpose_type", sa.String(40), nullable=False),
        sa.Column("purpose_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("created_event_id", sa.UUID(), nullable=False),
        sa.Column("completed_event_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_inventory_reservations_quantity_positive")
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','CONSUMED','RELEASED','EXPIRED')",
            name=op.f("ck_inventory_reservations_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["identity.users.id"],
            name=op.f("fk_inventory_reservations_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_event_id"],
            ["journal.signed_events.event_id"],
            name=op.f("fk_inventory_reservations_created_event_id_signed_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["completed_event_id"],
            ["journal.signed_events.event_id"],
            name=op.f("fk_inventory_reservations_completed_event_id_signed_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"],
            ["assets.inventory_lots.id"],
            name=op.f("fk_inventory_reservations_lot_id_inventory_lots"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_reservations")),
        schema="assets",
    )
    op.create_index(
        "ix_inventory_reservations_lot_status",
        "inventory_reservations",
        ["lot_id", "status"],
        schema="assets",
    )
    op.create_index(
        "uq_inventory_reservation_active_purpose",
        "inventory_reservations",
        ["purpose_type", "purpose_id"],
        unique=True,
        schema="assets",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "commodity_rights",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("cooperative_id", sa.UUID(), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=False),
        sa.Column("owner_member_id", sa.UUID(), nullable=False),
        sa.Column("original_owner_member_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("unit_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("redeem_warehouse_id", sa.UUID(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reservation_id", sa.UUID(), nullable=False),
        sa.Column("issued_by_user_id", sa.UUID(), nullable=False),
        sa.Column("issued_by_member_id", sa.UUID(), nullable=False),
        sa.Column("issued_role_assignment_id", sa.UUID(), nullable=False),
        sa.Column("issued_event_id", sa.UUID(), nullable=False),
        sa.Column("frozen_previous_status", sa.String(32), nullable=True),
        sa.Column("freeze_reason", sa.String(500), nullable=True),
        sa.Column("frozen_by_user_id", sa.UUID(), nullable=True),
        sa.Column("frozen_event_id", sa.UUID(), nullable=True),
        sa.Column("redeemed_event_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_commodity_rights_quantity_positive")),
        sa.CheckConstraint(
            "status IN ('ISSUED','TRANSFERRED','REDEMPTION_PENDING','FROZEN','REDEEMED',"
            "'EXPIRED','CANCELLED_BY_COMPENSATION')",
            name=op.f("ck_commodity_rights_status_allowed"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_commodity_rights_version_positive")),
        sa.ForeignKeyConstraint(
            ["cooperative_id"], ["identity.cooperatives.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["lot_id"], ["assets.inventory_lots.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_member_id"], ["identity.members.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["original_owner_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["unit_id"], ["assets.units_of_measure.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["redeem_warehouse_id"], ["assets.warehouses.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"], ["assets.inventory_reservations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["issued_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["issued_by_member_id"], ["identity.members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["issued_role_assignment_id"], ["identity.role_assignments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["issued_event_id"], ["journal.signed_events.event_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["frozen_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["frozen_event_id"], ["journal.signed_events.event_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["redeemed_event_id"], ["journal.signed_events.event_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commodity_rights")),
        sa.UniqueConstraint("reservation_id", name=op.f("uq_commodity_rights_reservation_id")),
        schema="assets",
    )
    op.create_index(
        "ix_commodity_rights_owner_status",
        "commodity_rights",
        ["owner_member_id", "status"],
        schema="assets",
    )
    op.create_index(
        "ix_commodity_rights_lot_status", "commodity_rights", ["lot_id", "status"], schema="assets"
    )
    op.create_table(
        "commodity_right_transfers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("right_id", sa.UUID(), nullable=False),
        sa.Column("from_member_id", sa.UUID(), nullable=False),
        sa.Column("to_member_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("performed_by_user_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_commodity_right_transfers_quantity_positive")
        ),
        sa.ForeignKeyConstraint(["right_id"], ["assets.commodity_rights.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["from_member_id"], ["identity.members.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["to_member_id"], ["identity.members.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["performed_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["journal.signed_events.event_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commodity_right_transfers")),
        sa.UniqueConstraint("event_id", name=op.f("uq_commodity_right_transfers_event_id")),
        schema="assets",
    )
    op.create_index(
        "ix_right_transfers_right_created",
        "commodity_right_transfers",
        ["right_id", "created_at"],
        schema="assets",
    )
    op.create_table(
        "commodity_right_redemptions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("right_id", sa.UUID(), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=False),
        sa.Column("owner_member_id", sa.UUID(), nullable=False),
        sa.Column("warehouse_id", sa.UUID(), nullable=False),
        sa.Column("custodian_assignment_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=False),
        sa.Column("fulfilled_by_user_id", sa.UUID(), nullable=True),
        sa.Column("requested_event_id", sa.UUID(), nullable=False),
        sa.Column("completed_event_id", sa.UUID(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_commodity_right_redemptions_quantity_positive")
        ),
        sa.CheckConstraint(
            "status IN ('REQUESTED','COMPLETED','CANCELLED')",
            name=op.f("ck_commodity_right_redemptions_status_allowed"),
        ),
        sa.ForeignKeyConstraint(["right_id"], ["assets.commodity_rights.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["lot_id"], ["assets.inventory_lots.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_member_id"], ["identity.members.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["assets.warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["custodian_assignment_id"], ["risk.responsibility_assignments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["fulfilled_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_event_id"], ["journal.signed_events.event_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["completed_event_id"], ["journal.signed_events.event_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commodity_right_redemptions")),
        schema="assets",
    )
    op.create_index(
        "ix_right_redemptions_status_created",
        "commodity_right_redemptions",
        ["status", "requested_at"],
        schema="assets",
    )
    op.create_index(
        "uq_right_redemption_open",
        "commodity_right_redemptions",
        ["right_id"],
        unique=True,
        schema="assets",
        postgresql_where=sa.text("status = 'REQUESTED'"),
    )
    op.execute(
        """
        INSERT INTO assets.lot_balances (
          lot_id, verified_quantity, available_quantity, reserved_quantity,
          rights_issued_quantity, redeemed_quantity, quarantined_quantity,
          backing_shortfall_quantity, version
        )
        SELECT id, current_quantity, current_quantity, 0, 0, 0, 0, 0, 1
        FROM assets.inventory_lots
        WHERE status = 'VERIFIED' AND current_quantity IS NOT NULL
        ON CONFLICT (lot_id) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_commodity_right_transfers_append_only
          BEFORE UPDATE OR DELETE ON assets.commodity_right_transfers
          FOR EACH ROW EXECUTE FUNCTION assets.prevent_inventory_evidence_mutation();
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coop_app') THEN
            GRANT SELECT, INSERT, UPDATE ON assets.lot_balances TO coop_app;
            GRANT SELECT, INSERT, UPDATE ON assets.inventory_reservations TO coop_app;
            GRANT SELECT, INSERT, UPDATE ON assets.commodity_rights TO coop_app;
            GRANT SELECT, INSERT ON assets.commodity_right_transfers TO coop_app;
            GRANT SELECT, INSERT, UPDATE ON assets.commodity_right_redemptions TO coop_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_index(
        "uq_right_redemption_open", table_name="commodity_right_redemptions", schema="assets"
    )
    op.drop_index(
        "ix_right_redemptions_status_created",
        table_name="commodity_right_redemptions",
        schema="assets",
    )
    op.drop_table("commodity_right_redemptions", schema="assets")
    op.drop_index(
        "ix_right_transfers_right_created", table_name="commodity_right_transfers", schema="assets"
    )
    op.drop_table("commodity_right_transfers", schema="assets")
    op.drop_index("ix_commodity_rights_lot_status", table_name="commodity_rights", schema="assets")
    op.drop_index(
        "ix_commodity_rights_owner_status", table_name="commodity_rights", schema="assets"
    )
    op.drop_table("commodity_rights", schema="assets")
    op.drop_index(
        "uq_inventory_reservation_active_purpose",
        table_name="inventory_reservations",
        schema="assets",
    )
    op.drop_index(
        "ix_inventory_reservations_lot_status", table_name="inventory_reservations", schema="assets"
    )
    op.drop_table("inventory_reservations", schema="assets")
    op.drop_table("lot_balances", schema="assets")
    op.drop_constraint(
        op.f("ck_inventory_movements_type_allowed"),
        "inventory_movements",
        schema="assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_inventory_movements_type_allowed"),
        "inventory_movements",
        "movement_type IN ('ATTESTED_RECEIPT','DISCREPANCY_ADJUSTMENT')",
        schema="assets",
    )
    op.drop_constraint(
        op.f("ck_role_assignments_role_allowed"),
        "role_assignments",
        schema="identity",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_role_assignments_role_allowed"),
        "role_assignments",
        "role_code IN ('MEMBER_REGISTRAR','COOPERATIVE_ADMIN','DATA_STEWARD',"
        "'WAREHOUSE_CUSTODIAN','INVENTORY_CONTROLLER','LOGISTICS_OPERATOR',"
        "'RISK_ADMIN','SECURITY_ADMIN','NODE_REGISTRAR','AUDITOR')",
        schema="identity",
    )
