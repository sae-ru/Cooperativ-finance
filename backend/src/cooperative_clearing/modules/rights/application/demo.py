# ruff: noqa: RUF001
"""Deterministic commodity-right demo built through production commands."""

import hashlib
from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.inventory.application.demo import DemoCatalog
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.infrastructure.models import InventoryLot
from cooperative_clearing.modules.rights.application.service import CommodityRightsService
from cooperative_clearing.modules.rights.infrastructure.models import (
    CommodityRight,
    LotBalance,
)
from cooperative_clearing.shared.core.config import Settings


async def seed_demo_rights(
    session: AsyncSession,
    settings: Settings,
    *,
    catalog: DemoCatalog,
) -> None:
    cooperative_id = stable_id("cooperative", settings.node_code)
    registrar = _principal(
        "registrar",
        "demo-member-anna",
        cooperative_id,
        (
            ("bootstrap-role", "registrar:COOPERATIVE_ADMIN", RoleCode.COOPERATIVE_ADMIN),
            ("demo-role", "registrar:WAREHOUSE_CUSTODIAN", RoleCode.WAREHOUSE_CUSTODIAN),
            ("demo-role", "registrar:RIGHTS_OPERATOR", RoleCode.RIGHTS_OPERATOR),
        ),
    )
    auditor = _principal(
        "auditor",
        "demo-member-boris",
        cooperative_id,
        (("bootstrap-role", "auditor:AUDITOR", RoleCode.AUDITOR),),
        global_scope=True,
    )
    lot = (
        await session.execute(
            select(InventoryLot).where(
                InventoryLot.cooperative_id == cooperative_id,
                InventoryLot.lot_number == "DEMO-CABBAGE-001",
            )
        )
    ).scalar_one()
    balance = await session.get(LotBalance, lot.id)
    if balance is None:
        raise RuntimeError("demo verified lot balance is unavailable")
    rights = CommodityRightsService(settings)
    first = await rights.issue(
        session,
        principal=registrar,
        lot_id=lot.id,
        owner_member_id=stable_id("member", "demo-member-elena"),
        quantity=Decimal("25.00"),
        redeem_warehouse_id=catalog.warehouse_b_id,
        valid_until=None,
        expected_balance_version=1,
        idempotency_key="demo-right-pending-issue-v1",
        request_id=None,
    )
    transfer_evidence = await _evidence(
        session,
        settings,
        registrar,
        cooperative_id,
        "demo-right-transfer-v1",
        "Заявление владельца о передаче товарного права на 25.00 кг.",
    )
    await rights.transfer(
        session,
        principal=registrar,
        right_id=first.object_id,
        from_member_id=stable_id("member", "demo-member-elena"),
        to_member_id=stable_id("member", "demo-member-anna"),
        evidence_ids=[transfer_evidence],
        expected_version=1,
        idempotency_key="demo-right-pending-transfer-v1",
        request_id=None,
    )
    await rights.request_redemption(
        session,
        principal=registrar,
        right_id=first.object_id,
        owner_member_id=stable_id("member", "demo-member-anna"),
        expected_version=2,
        idempotency_key="demo-right-pending-redemption-v1",
        request_id=None,
    )

    frozen = await rights.issue(
        session,
        principal=registrar,
        lot_id=lot.id,
        owner_member_id=stable_id("member", "demo-member-anna"),
        quantity=Decimal("10.00"),
        redeem_warehouse_id=catalog.warehouse_b_id,
        valid_until=None,
        expected_balance_version=2,
        idempotency_key="demo-right-frozen-issue-v1",
        request_id=None,
    )
    await rights.freeze(
        session,
        principal=auditor,
        right_id=frozen.object_id,
        reason_code="DEMO_REVIEW",
        decision_reference="Плановая демонстрация защитной заморозки",
        expected_version=1,
        idempotency_key="demo-right-frozen-command-v1",
        request_id=None,
    )

    redeemed = await rights.issue(
        session,
        principal=registrar,
        lot_id=lot.id,
        owner_member_id=stable_id("member", "demo-member-anna"),
        quantity=Decimal("5.00"),
        redeem_warehouse_id=catalog.warehouse_b_id,
        valid_until=None,
        expected_balance_version=3,
        idempotency_key="demo-right-redeemed-issue-v1",
        request_id=None,
    )
    request = await rights.request_redemption(
        session,
        principal=registrar,
        right_id=redeemed.object_id,
        owner_member_id=stable_id("member", "demo-member-anna"),
        expected_version=1,
        idempotency_key="demo-right-redeemed-request-v1",
        request_id=None,
    )
    redemption_evidence = await _evidence(
        session,
        settings,
        registrar,
        cooperative_id,
        "demo-right-redemption-v1",
        "Акт фактической выдачи 5.00 кг владельцу товарного права.",
    )
    await rights.complete_redemption(
        session,
        principal=registrar,
        redemption_id=request.object_id,
        evidence_ids=[redemption_evidence],
        expected_right_version=2,
        idempotency_key="demo-right-redeemed-complete-v1",
        request_id=None,
    )

    created = list(
        (
            await session.execute(
                select(CommodityRight).where(
                    CommodityRight.id.in_([first.object_id, frozen.object_id, redeemed.object_id])
                )
            )
        ).scalars()
    )
    if len(created) != 3:
        raise RuntimeError("demo commodity rights were not created")


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
