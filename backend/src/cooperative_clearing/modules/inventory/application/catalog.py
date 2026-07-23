"""Signed catalog commands for units, products, and warehouses."""

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import Cooperative
from cooperative_clearing.modules.inventory.application.common import (
    InventoryCommandResult,
    actor_claim,
    begin_command,
    bounded_text,
    complete_command,
    inventory_error,
)
from cooperative_clearing.modules.inventory.domain.types import (
    decimal_text,
    ensure_unit_scale,
    exact_quantity,
)
from cooperative_clearing.modules.inventory.infrastructure.models import (
    Product,
    UnitOfMeasure,
    Warehouse,
)
from cooperative_clearing.modules.journal.application.service import SignedJournalService
from cooperative_clearing.shared.core.config import Settings

CATALOG_WRITE_ROLES = {RoleCode.DATA_STEWARD, RoleCode.COOPERATIVE_ADMIN}


class CatalogService:
    def __init__(self, settings: Settings) -> None:
        self.journal = SignedJournalService(settings)

    async def create_unit(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        code: str,
        name: str,
        symbol: str,
        dimension: str,
        decimal_scale: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        normalized_code = bounded_text(code, "UNIT_CODE_INVALID", 32).upper()
        normalized_name = bounded_text(name, "UNIT_NAME_INVALID", 120)
        normalized_symbol = bounded_text(symbol, "UNIT_SYMBOL_INVALID", 24)
        normalized_dimension = bounded_text(dimension, "UNIT_DIMENSION_INVALID", 40).upper()
        if not 0 <= decimal_scale <= 12:
            raise inventory_error("UNIT_SCALE_INVALID")
        payload = {
            "cooperative_id": str(cooperative_id),
            "code": normalized_code,
            "name": normalized_name,
            "symbol": normalized_symbol,
            "dimension": normalized_dimension,
            "decimal_scale": decimal_scale,
        }
        record, replay = await begin_command(
            session, principal, "CATALOG_CREATE_UNIT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        await self._active_cooperative(session, cooperative_id)
        actor = actor_claim(principal, cooperative_id, CATALOG_WRITE_ROLES)
        unit_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="catalog.unit_created",
            aggregate_type="unit_of_measure",
            aggregate_id=unit_id,
            aggregate_version=1,
            actor=actor,
            payload=payload,
        )
        session.add(
            UnitOfMeasure(
                id=unit_id,
                cooperative_id=cooperative_id,
                code=normalized_code,
                name=normalized_name,
                symbol=normalized_symbol,
                dimension=normalized_dimension,
                decimal_scale=decimal_scale,
                status="ACTIVE",
                created_by_user_id=principal.user_id,
                created_event_id=event.event_id,
            )
        )
        await self._audit(
            session,
            principal,
            cooperative_id,
            "UNIT_CREATED",
            "UnitOfMeasure",
            unit_id,
            event.event_id,
            request_id,
        )
        return complete_command(record, event.event_id, unit_id)

    async def create_product(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        sku: str,
        name: str,
        description: str,
        default_unit_id: UUID,
        quantity_tolerance: Decimal,
        requires_evidence: bool,
        shelf_life_required: bool,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        normalized_sku = bounded_text(sku, "PRODUCT_SKU_INVALID", 63).upper()
        normalized_name = bounded_text(name, "PRODUCT_NAME_INVALID", 200)
        normalized_description = " ".join(description.split())
        if len(normalized_description) > 2000:
            raise inventory_error("PRODUCT_DESCRIPTION_INVALID")
        tolerance = exact_quantity(quantity_tolerance, allow_zero=True)
        payload = {
            "cooperative_id": str(cooperative_id),
            "sku": normalized_sku,
            "name": normalized_name,
            "description": normalized_description,
            "default_unit_id": str(default_unit_id),
            "quantity_tolerance": decimal_text(tolerance),
            "requires_evidence": requires_evidence,
            "shelf_life_required": shelf_life_required,
        }
        record, replay = await begin_command(
            session, principal, "CATALOG_CREATE_PRODUCT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        await self._active_cooperative(session, cooperative_id)
        unit = await session.get(UnitOfMeasure, default_unit_id)
        if unit is None or unit.cooperative_id != cooperative_id or unit.status != "ACTIVE":
            raise inventory_error("PRODUCT_UNIT_NOT_ACTIVE", 409)
        ensure_unit_scale(tolerance, unit.decimal_scale)
        actor = actor_claim(principal, cooperative_id, CATALOG_WRITE_ROLES)
        product_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="catalog.product_created",
            aggregate_type="product",
            aggregate_id=product_id,
            aggregate_version=1,
            actor=actor,
            payload=payload,
        )
        session.add(
            Product(
                id=product_id,
                cooperative_id=cooperative_id,
                sku=normalized_sku,
                name=normalized_name,
                description=normalized_description,
                default_unit_id=default_unit_id,
                quantity_tolerance=tolerance,
                requires_evidence=requires_evidence,
                shelf_life_required=shelf_life_required,
                status="ACTIVE",
                created_by_user_id=principal.user_id,
                created_event_id=event.event_id,
            )
        )
        await self._audit(
            session,
            principal,
            cooperative_id,
            "PRODUCT_CREATED",
            "Product",
            product_id,
            event.event_id,
            request_id,
        )
        return complete_command(record, event.event_id, product_id)

    async def create_warehouse(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        code: str,
        name: str,
        address_text: str,
        storage_conditions: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        normalized_code = bounded_text(code, "WAREHOUSE_CODE_INVALID", 63).upper()
        normalized_name = bounded_text(name, "WAREHOUSE_NAME_INVALID", 200)
        normalized_address = bounded_text(address_text, "WAREHOUSE_ADDRESS_INVALID", 500)
        normalized_conditions = bounded_text(
            storage_conditions, "WAREHOUSE_CONDITIONS_INVALID", 500
        )
        payload: dict[str, object] = {
            "cooperative_id": str(cooperative_id),
            "code": normalized_code,
            "name": normalized_name,
            "address_text": normalized_address,
            "storage_conditions": normalized_conditions,
        }
        record, replay = await begin_command(
            session, principal, "INVENTORY_CREATE_WAREHOUSE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        await self._active_cooperative(session, cooperative_id)
        actor = actor_claim(principal, cooperative_id, CATALOG_WRITE_ROLES)
        warehouse_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="inventory.warehouse_created",
            aggregate_type="warehouse",
            aggregate_id=warehouse_id,
            aggregate_version=1,
            actor=actor,
            payload=payload,
        )
        session.add(
            Warehouse(
                id=warehouse_id,
                cooperative_id=cooperative_id,
                code=normalized_code,
                name=normalized_name,
                address_text=normalized_address,
                storage_conditions=normalized_conditions,
                status="ACTIVE",
                created_by_user_id=principal.user_id,
                created_event_id=event.event_id,
            )
        )
        await self._audit(
            session,
            principal,
            cooperative_id,
            "WAREHOUSE_CREATED",
            "Warehouse",
            warehouse_id,
            event.event_id,
            request_id,
        )
        return complete_command(record, event.event_id, warehouse_id)

    @staticmethod
    async def _active_cooperative(session: AsyncSession, cooperative_id: UUID) -> None:
        cooperative = await session.get(Cooperative, cooperative_id)
        if cooperative is None or cooperative.status != "ACTIVE":
            raise inventory_error("COOPERATIVE_NOT_ACTIVE", 409)

    @staticmethod
    async def _audit(
        session: AsyncSession,
        principal: Principal,
        cooperative_id: UUID,
        action: str,
        object_type: str,
        object_id: UUID,
        event_id: UUID,
        request_id: UUID | None,
    ) -> None:
        await AuditRepository(session).record(
            action=action,
            object_type=object_type,
            object_id=object_id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"signed_event_id": str(event_id)},
        )
