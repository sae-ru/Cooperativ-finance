"""SQLAlchemy models for backed commodity rights and redemption."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from cooperative_clearing.shared.infrastructure.orm import Base


class LotBalance(Base):
    __tablename__ = "lot_balances"
    __table_args__ = (
        CheckConstraint(
            "verified_quantity >= 0 AND available_quantity >= 0 AND reserved_quantity >= 0 "
            "AND rights_issued_quantity >= 0 AND redeemed_quantity >= 0 "
            "AND quarantined_quantity >= 0 AND backing_shortfall_quantity >= 0",
            name="quantities_nonnegative",
        ),
        CheckConstraint(
            "available_quantity + reserved_quantity + rights_issued_quantity + "
            "quarantined_quantity = verified_quantity + backing_shortfall_quantity",
            name="allocation_matches_backing",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        {"schema": "assets"},
    )

    lot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("assets.inventory_lots.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    verified_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    available_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    reserved_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    rights_issued_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    redeemed_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    quarantined_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    backing_shortfall_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class InventoryReservation(Base):
    __tablename__ = "inventory_reservations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "status IN ('ACTIVE','CONSUMED','RELEASED','EXPIRED')", name="status_allowed"
        ),
        Index("ix_inventory_reservations_lot_status", "lot_id", "status"),
        Index(
            "uq_inventory_reservation_active_purpose",
            "purpose_type",
            "purpose_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    lot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.inventory_lots.id", ondelete="RESTRICT")
    )
    purpose_type: Mapped[str] = mapped_column(String(40), nullable=False)
    purpose_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    completed_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CommodityRight(Base):
    __tablename__ = "commodity_rights"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "status IN ('ISSUED','TRANSFERRED','REDEMPTION_PENDING','FROZEN','REDEEMED',"
            "'EXPIRED','CANCELLED_BY_COMPENSATION')",
            name="status_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_commodity_rights_owner_status", "owner_member_id", "status"),
        Index("ix_commodity_rights_lot_status", "lot_id", "status"),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    lot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.inventory_lots.id", ondelete="RESTRICT")
    )
    owner_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    original_owner_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    unit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.units_of_measure.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    redeem_warehouse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.warehouses.id", ondelete="RESTRICT")
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reservation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("assets.inventory_reservations.id", ondelete="RESTRICT"),
        unique=True,
    )
    issued_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    issued_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    issued_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    issued_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    frozen_previous_status: Mapped[str | None] = mapped_column(String(32))
    freeze_reason: Mapped[str | None] = mapped_column(String(500))
    frozen_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    frozen_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    redeemed_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class RightTransfer(Base):
    __tablename__ = "commodity_right_transfers"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        Index("ix_right_transfers_right_created", "right_id", "created_at"),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    right_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.commodity_rights.id", ondelete="RESTRICT")
    )
    from_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    to_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    performed_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class RightRedemption(Base):
    __tablename__ = "commodity_right_redemptions"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("status IN ('REQUESTED','COMPLETED','CANCELLED')", name="status_allowed"),
        Index("ix_right_redemptions_status_created", "status", "requested_at"),
        Index(
            "uq_right_redemption_open",
            "right_id",
            unique=True,
            postgresql_where=text("status = 'REQUESTED'"),
        ),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    right_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.commodity_rights.id", ondelete="RESTRICT")
    )
    lot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.inventory_lots.id", ondelete="RESTRICT")
    )
    owner_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    warehouse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.warehouses.id", ondelete="RESTRICT")
    )
    custodian_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("risk.responsibility_assignments.id", ondelete="RESTRICT"),
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    fulfilled_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    requested_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    completed_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
