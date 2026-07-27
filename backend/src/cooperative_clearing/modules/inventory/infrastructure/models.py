"""SQLAlchemy models for catalog, physical stock, evidence, and custody."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from cooperative_clearing.shared.infrastructure.orm import Base


class UnitOfMeasure(Base):
    __tablename__ = "units_of_measure"
    __table_args__ = (
        UniqueConstraint("cooperative_id", "code", name="uq_unit_cooperative_code"),
        CheckConstraint("decimal_scale BETWEEN 0 AND 12", name="decimal_scale_allowed"),
        CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="status_allowed"),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    symbol: Mapped[str] = mapped_column(String(24), nullable=False)
    dimension: Mapped[str] = mapped_column(String(40), nullable=False)
    decimal_scale: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ACTIVE'"))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("cooperative_id", "sku", name="uq_product_cooperative_sku"),
        CheckConstraint("quantity_tolerance >= 0", name="tolerance_nonnegative"),
        CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="status_allowed"),
        Index("ix_products_cooperative_status", "cooperative_id", "status"),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    sku: Mapped[str] = mapped_column(String(63), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    default_unit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.units_of_measure.id", ondelete="RESTRICT")
    )
    quantity_tolerance: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, server_default=text("0")
    )
    requires_evidence: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    shelf_life_required: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ACTIVE'"))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Warehouse(Base):
    __tablename__ = "warehouses"
    __table_args__ = (
        UniqueConstraint("cooperative_id", "code", name="uq_warehouse_cooperative_code"),
        CheckConstraint("status IN ('ACTIVE','SUSPENDED','CLOSED')", name="status_allowed"),
        Index("ix_warehouses_cooperative_status", "cooperative_id", "status"),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    code: Mapped[str] = mapped_column(String(63), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address_text: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_conditions: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ACTIVE'"))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class EvidenceBlob(Base):
    __tablename__ = "evidence_blobs"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','READY','FAILED')", name="status_allowed"),
        CheckConstraint("expected_size >= 0 AND expected_size <= 26214400", name="size_allowed"),
        CheckConstraint("expected_sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        Index("ix_evidence_cooperative_status", "cooperative_id", "status"),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    expected_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    access_scope: Mapped[str] = mapped_column(String(40), nullable=False)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(300))
    encryption_algorithm: Mapped[str | None] = mapped_column(String(40))
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
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InventoryLot(Base):
    __tablename__ = "inventory_lots"
    __table_args__ = (
        CheckConstraint("declared_quantity > 0", name="declared_quantity_positive"),
        CheckConstraint(
            "current_quantity IS NULL OR current_quantity >= 0",
            name="current_nonnegative",
        ),
        CheckConstraint(
            "status IN ('PENDING_VERIFICATION','VERIFIED','DISPUTED','FROZEN','LOST','DEPLETED')",
            name="status_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint("cooperative_id", "lot_number", name="uq_inventory_lot_number"),
        Index("ix_inventory_lots_product_status", "product_id", "status"),
        Index("ix_inventory_lots_warehouse_status", "warehouse_id", "status"),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    lot_number: Mapped[str] = mapped_column(String(100), nullable=False)
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.products.id", ondelete="RESTRICT")
    )
    warehouse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.warehouses.id", ondelete="RESTRICT")
    )
    owner_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    unit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.units_of_measure.id", ondelete="RESTRICT")
    )
    declared_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    current_quantity: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    declared_quality: Mapped[str] = mapped_column(String(200), nullable=False)
    verified_quality: Mapped[str | None] = mapped_column(String(200))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storage_conditions: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    received_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    received_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    received_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    custodian_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("risk.responsibility_assignments.id", ondelete="RESTRICT"),
    )
    continuity_hold_case_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("assets.custody_continuity_cases.id", ondelete="RESTRICT"),
        index=True,
    )
    registered_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    verified_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class StockAttestation(Base):
    __tablename__ = "stock_attestations"
    __table_args__ = (
        UniqueConstraint("lot_id", name="uq_stock_attestation_lot"),
        CheckConstraint("measured_quantity >= 0", name="measured_nonnegative"),
        CheckConstraint(
            "quantity_decision IN ('MATCH','WITHIN_TOLERANCE','DISCREPANCY')",
            name="quantity_decision_allowed",
        ),
        CheckConstraint(
            "quality_decision IN ('ACCEPTED','CONDITIONAL','REJECTED')",
            name="quality_decision_allowed",
        ),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    lot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.inventory_lots.id", ondelete="RESTRICT")
    )
    measured_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    variance: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    quantity_decision: Mapped[str] = mapped_column(String(24), nullable=False)
    quality_decision: Mapped[str] = mapped_column(String(24), nullable=False)
    verified_quality: Mapped[str] = mapped_column(String(200), nullable=False)
    measurements: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    notes: Mapped[str] = mapped_column(String(1000), nullable=False)
    attested_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    attested_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    attested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class QualityInspection(Base):
    __tablename__ = "quality_inspections"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('ACCEPTED','CONDITIONAL','REJECTED')", name="decision_allowed"
        ),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    lot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.inventory_lots.id", ondelete="RESTRICT")
    )
    attestation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("assets.stock_attestations.id", ondelete="RESTRICT"),
        unique=True,
    )
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    quality_grade: Mapped[str] = mapped_column(String(200), nullable=False)
    measurements: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        CheckConstraint(
            "movement_type IN ('ATTESTED_RECEIPT','DISCREPANCY_ADJUSTMENT','RIGHT_REDEMPTION')",
            name="type_allowed",
        ),
        CheckConstraint("resulting_quantity >= 0", name="resulting_nonnegative"),
        UniqueConstraint("event_id", name="uq_inventory_movement_event"),
        Index("ix_inventory_movements_lot_created", "lot_id", "created_at"),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    lot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.inventory_lots.id", ondelete="RESTRICT")
    )
    movement_type: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    resulting_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    performed_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class InventoryDiscrepancy(Base):
    __tablename__ = "inventory_discrepancies"
    __table_args__ = (
        CheckConstraint(
            "expected_quantity >= 0 AND actual_quantity >= 0",
            name="quantities_nonnegative",
        ),
        CheckConstraint("status IN ('OPEN','RESOLVED','DISMISSED')", name="status_allowed"),
        Index("ix_inventory_discrepancies_lot_status", "lot_id", "status"),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    lot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.inventory_lots.id", ondelete="RESTRICT")
    )
    expected_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    actual_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    variance: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    recorded_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CustodyContinuityCase(Base):
    __tablename__ = "custody_continuity_cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('INVENTORY_PENDING','PENDING_APPROVAL','PENDING_ACCEPTANCE',"
            "'ACCEPTED','REJECTED','BLOCKED')",
            name="status_allowed",
        ),
        CheckConstraint("source_assignment_version >= 1", name="source_version_positive"),
        CheckConstraint("target_member_id <> source_member_id", name="different_target_member"),
        CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array' AND jsonb_array_length(evidence_refs) >= 1",
            name="evidence_nonempty_array",
        ),
        CheckConstraint("jsonb_typeof(blocked_reasons) = 'array'", name="blockers_array"),
        CheckConstraint("temporary_valid_until > created_at", name="temporary_period_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_custody_continuity_cases_cooperative_status_created",
            "cooperative_id",
            "status",
            "created_at",
        ),
        Index(
            "uq_custody_continuity_cases_open_source",
            "source_assignment_id",
            unique=True,
            postgresql_where=text(
                "status IN ('INVENTORY_PENDING','PENDING_APPROVAL','PENDING_ACCEPTANCE','BLOCKED')"
            ),
        ),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    cooperative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.cooperatives.id", ondelete="RESTRICT")
    )
    member_continuity_case_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.member_continuity_cases.id", ondelete="RESTRICT"),
    )
    source_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    warehouse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.warehouses.id", ondelete="RESTRICT")
    )
    source_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("risk.responsibility_assignments.id", ondelete="RESTRICT"),
    )
    source_assignment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    target_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    target_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("risk.responsibility_assignments.id", ondelete="RESTRICT"),
    )
    handover_place: Mapped[str] = mapped_column(String(500), nullable=False)
    temporary_valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    blocked_reasons: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    accepted_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    decision_reason_code: Mapped[str | None] = mapped_column(String(100))
    requested_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    decided_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    accepted_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    inventory_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class CustodyContinuityItem(Base):
    __tablename__ = "custody_continuity_items"
    __table_args__ = (
        UniqueConstraint("case_id", "lot_id", name="uq_custody_continuity_item_lot"),
        CheckConstraint("lot_version >= 1", name="lot_version_positive"),
        CheckConstraint("expected_quantity >= 0", name="expected_quantity_nonnegative"),
        CheckConstraint(
            "actual_quantity IS NULL OR actual_quantity >= 0",
            name="actual_quantity_nonnegative",
        ),
        CheckConstraint(
            "status IN ('PENDING','MATCH','DISCREPANCY')",
            name="status_allowed",
        ),
        CheckConstraint("jsonb_typeof(evidence_ids) = 'array'", name="evidence_array"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_custody_continuity_items_case_status", "case_id", "status", "lot_id"),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("assets.custody_continuity_cases.id", ondelete="RESTRICT"),
    )
    lot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.inventory_lots.id", ondelete="RESTRICT")
    )
    lot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    actual_quantity: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    condition_notes: Mapped[str | None] = mapped_column(String(1000))
    evidence_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    attested_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class CustodyTransfer(Base):
    __tablename__ = "custody_transfers"
    __table_args__ = (
        CheckConstraint("status IN ('OFFERED','ACCEPTED','CANCELLED')", name="status_allowed"),
        Index("ix_custody_transfers_lot_status", "lot_id", "status"),
        Index(
            "uq_custody_transfer_open_lot",
            "lot_id",
            unique=True,
            postgresql_where=text("status = 'OFFERED'"),
        ),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    lot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.inventory_lots.id", ondelete="RESTRICT")
    )
    from_warehouse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.warehouses.id", ondelete="RESTRICT")
    )
    to_warehouse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.warehouses.id", ondelete="RESTRICT")
    )
    from_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("risk.responsibility_assignments.id", ondelete="RESTRICT")
    )
    to_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("risk.responsibility_assignments.id", ondelete="RESTRICT")
    )
    place: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    offered_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    accepted_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    offered_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    accepted_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    offered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceLink(Base):
    __tablename__ = "evidence_links"
    __table_args__ = (
        UniqueConstraint("evidence_id", "event_id", name="uq_evidence_link_event"),
        Index("ix_evidence_links_subject", "subject_type", "subject_id"),
        {"schema": "assets"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.evidence_blobs.id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
