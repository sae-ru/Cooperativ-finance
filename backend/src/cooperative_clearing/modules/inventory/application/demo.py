# ruff: noqa: RUF001
"""Idempotent inventory demo flow using production application services."""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.inventory.application.catalog import CatalogService
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.application.service import InventoryService
from cooperative_clearing.modules.inventory.domain.types import QualityDecision
from cooperative_clearing.shared.core.config import Settings


@dataclass(frozen=True, slots=True)
class DemoCatalog:
    unit_id: UUID
    product_id: UUID
    warehouse_a_id: UUID
    warehouse_b_id: UUID


async def seed_demo_catalog(session: AsyncSession, settings: Settings) -> DemoCatalog:
    cooperative_id = stable_id("cooperative", settings.node_code)
    registrar = _principal(
        "registrar",
        "demo-member-anna",
        cooperative_id,
        (
            ("bootstrap-role", "registrar:COOPERATIVE_ADMIN", RoleCode.COOPERATIVE_ADMIN),
            ("demo-role", "registrar:WAREHOUSE_CUSTODIAN", RoleCode.WAREHOUSE_CUSTODIAN),
        ),
    )
    service = CatalogService(settings)
    unit = await service.create_unit(
        session,
        principal=registrar,
        cooperative_id=cooperative_id,
        code="KG",
        name="Килограмм",
        symbol="кг",
        dimension="MASS",
        decimal_scale=2,
        idempotency_key="demo-catalog-unit-kg-v1",
        request_id=None,
    )
    product = await service.create_product(
        session,
        principal=registrar,
        cooperative_id=cooperative_id,
        sku="CABBAGE-FRESH",
        name="Капуста свежая",
        description="Продовольственная капуста для демонстрационного складского потока",
        default_unit_id=unit.object_id,
        quantity_tolerance=Decimal("0.10"),
        requires_evidence=True,
        shelf_life_required=False,
        idempotency_key="demo-catalog-product-cabbage-v1",
        request_id=None,
    )
    warehouse_a = await service.create_warehouse(
        session,
        principal=registrar,
        cooperative_id=cooperative_id,
        code="DEMO-WH-A",
        name="Основной склад",
        address_text="Демонстрационная площадка, корпус A",
        storage_conditions="Сухое помещение, температура от 2 до 8 градусов",
        idempotency_key="demo-warehouse-a-v1",
        request_id=None,
    )
    warehouse_b = await service.create_warehouse(
        session,
        principal=registrar,
        cooperative_id=cooperative_id,
        code="DEMO-WH-B",
        name="Резервный склад",
        address_text="Демонстрационная площадка, корпус B",
        storage_conditions="Сухое помещение, температура от 2 до 8 градусов",
        idempotency_key="demo-warehouse-b-v1",
        request_id=None,
    )
    return DemoCatalog(
        unit_id=unit.object_id,
        product_id=product.object_id,
        warehouse_a_id=warehouse_a.object_id,
        warehouse_b_id=warehouse_b.object_id,
    )


async def seed_demo_inventory(
    session: AsyncSession,
    settings: Settings,
    *,
    catalog: DemoCatalog,
    custody_a_id: UUID,
    custody_b_id: UUID,
) -> None:
    cooperative_id = stable_id("cooperative", settings.node_code)
    security = _principal(
        "security",
        "demo-member-elena",
        cooperative_id,
        (
            ("demo-role", "security:DATA_STEWARD", RoleCode.DATA_STEWARD),
            ("demo-role", "security:WAREHOUSE_CUSTODIAN", RoleCode.WAREHOUSE_CUSTODIAN),
        ),
    )
    registrar = _principal(
        "registrar",
        "demo-member-anna",
        cooperative_id,
        (
            ("bootstrap-role", "registrar:COOPERATIVE_ADMIN", RoleCode.COOPERATIVE_ADMIN),
            ("demo-role", "registrar:WAREHOUSE_CUSTODIAN", RoleCode.WAREHOUSE_CUSTODIAN),
        ),
    )
    auditor = _principal(
        "auditor",
        "demo-member-boris",
        cooperative_id,
        (("bootstrap-role", "auditor:AUDITOR", RoleCode.AUDITOR),),
        global_scope=True,
    )
    inventory = InventoryService(settings)

    receipt_evidence = await _evidence(
        session,
        settings,
        security,
        cooperative_id,
        "demo-receipt-v1",
        "Акт приёмки: заявлено 120.00 кг капусты на основном складе.",
    )
    verified_evidence = await _evidence(
        session,
        settings,
        auditor,
        cooperative_id,
        "demo-attestation-verified-v1",
        "Независимое измерение: 119.95 кг, качество принято.",
    )
    lot_verified = await inventory.register_lot(
        session,
        principal=security,
        cooperative_id=cooperative_id,
        lot_number="DEMO-CABBAGE-001",
        product_id=catalog.product_id,
        warehouse_id=catalog.warehouse_a_id,
        owner_member_id=stable_id("member", "demo-member-anna"),
        declared_quantity=Decimal("120.00"),
        unit_id=catalog.unit_id,
        declared_quality="Сорт 1",
        expires_at=None,
        storage_conditions="Температура от 2 до 8 градусов",
        custodian_assignment_id=custody_a_id,
        evidence_ids=[receipt_evidence],
        idempotency_key="demo-lot-verified-register-v1",
        request_id=None,
    )
    await inventory.attest_lot(
        session,
        principal=auditor,
        lot_id=lot_verified.object_id,
        measured_quantity=Decimal("119.95"),
        quality_decision=QualityDecision.ACCEPTED,
        verified_quality="Сорт 1",
        measurements={"вес": "119.95 кг", "температура": "4 градусов"},
        notes="Количество в пределах утверждённого допуска",
        evidence_ids=[verified_evidence],
        expected_version=1,
        idempotency_key="demo-lot-verified-attest-v1",
        request_id=None,
    )
    transfer = await inventory.offer_custody(
        session,
        principal=security,
        lot_id=lot_verified.object_id,
        to_warehouse_id=catalog.warehouse_b_id,
        to_assignment_id=custody_b_id,
        place="Приёмные ворота резервного склада",
        notes="Пломба цела, партия передана без изменения количества",
        evidence_ids=[],
        expected_version=3,
        idempotency_key="demo-custody-offer-v1",
        request_id=None,
    )
    custody_evidence = await _evidence(
        session,
        settings,
        registrar,
        cooperative_id,
        "demo-custody-accept-v1",
        "Акт передачи сохранности: партия принята резервным складом.",
    )
    await inventory.accept_custody(
        session,
        principal=registrar,
        transfer_id=transfer.object_id,
        evidence_ids=[custody_evidence],
        expected_lot_version=4,
        idempotency_key="demo-custody-accept-command-v1",
        request_id=None,
    )

    discrepancy_evidence = await _evidence(
        session,
        settings,
        auditor,
        cooperative_id,
        "demo-attestation-discrepancy-v1",
        "Независимое измерение: вместо 80.00 кг обнаружено 75.00 кг.",
    )
    lot_disputed = await inventory.register_lot(
        session,
        principal=security,
        cooperative_id=cooperative_id,
        lot_number="DEMO-CABBAGE-002",
        product_id=catalog.product_id,
        warehouse_id=catalog.warehouse_a_id,
        owner_member_id=stable_id("member", "demo-member-elena"),
        declared_quantity=Decimal("80.00"),
        unit_id=catalog.unit_id,
        declared_quality="Сорт 1",
        expires_at=None,
        storage_conditions="Температура от 2 до 8 градусов",
        custodian_assignment_id=custody_a_id,
        evidence_ids=[],
        idempotency_key="demo-lot-disputed-register-v1",
        request_id=None,
    )
    await inventory.attest_lot(
        session,
        principal=auditor,
        lot_id=lot_disputed.object_id,
        measured_quantity=Decimal("75.00"),
        quality_decision=QualityDecision.ACCEPTED,
        verified_quality="Сорт 1",
        measurements={"вес": "75.00 кг", "температура": "5 градусов"},
        notes="Расхождение превышает допуск, партия изолирована",
        evidence_ids=[discrepancy_evidence],
        expected_version=1,
        idempotency_key="demo-lot-disputed-attest-v1",
        request_id=None,
    )
    await inventory.register_lot(
        session,
        principal=security,
        cooperative_id=cooperative_id,
        lot_number="DEMO-CABBAGE-003",
        product_id=catalog.product_id,
        warehouse_id=catalog.warehouse_a_id,
        owner_member_id=stable_id("member", "demo-member-anna"),
        declared_quantity=Decimal("40.00"),
        unit_id=catalog.unit_id,
        declared_quality="Сорт 2",
        expires_at=None,
        storage_conditions="Температура от 2 до 8 градусов",
        custodian_assignment_id=custody_a_id,
        evidence_ids=[],
        idempotency_key="demo-lot-pending-register-v1",
        request_id=None,
    )


async def _evidence(
    session: AsyncSession,
    settings: Settings,
    principal: Principal,
    cooperative_id: UUID,
    key: str,
    text: str,
) -> UUID:
    content = text.encode("utf-8")
    service = EvidenceService(settings)
    intent = await service.create_intent(
        session,
        principal=principal,
        cooperative_id=cooperative_id,
        expected_sha256=hashlib.sha256(content).hexdigest(),
        expected_size=len(content),
        mime_type="text/plain",
        kind="ACT",
        original_name=f"{key}.txt",
        access_scope="COOPERATIVE",
        retention_until=None,
        idempotency_key=f"{key}-intent",
        request_id=None,
    )

    async def chunks() -> AsyncIterator[bytes]:
        yield content

    await service.store_content(
        session,
        principal=principal,
        evidence_id=intent.object_id,
        chunks=chunks(),
        request_id=None,
    )
    return intent.object_id


def _principal(
    login: str,
    member_key: str,
    cooperative_id: UUID,
    roles: tuple[tuple[str, str, RoleCode], ...],
    *,
    global_scope: bool = False,
) -> Principal:
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=stable_id("demo-session", login),
        login=login,
        member_id=stable_id("member", member_key),
        must_change_password=False,
        roles=tuple(
            RoleGrant(
                stable_id(id_kind, id_value),
                role,
                None if global_scope else cooperative_id,
            )
            for id_kind, id_value, role in roles
        ),
    )
