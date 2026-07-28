"""Deterministic local-exchange demo built through production commands."""

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.exchange.application.service import (
    ExchangeService,
    ObligationDraft,
)
from cooperative_clearing.modules.exchange.infrastructure.models import (
    Deal,
    Fulfillment,
    FulfillmentProvenance,
    LogisticsOrder,
    Obligation,
)
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.inventory.application.demo import DemoCatalog
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.infrastructure.models import InventoryLot
from cooperative_clearing.modules.rights.application.service import CommodityRightsService
from cooperative_clearing.modules.rights.infrastructure.models import LotBalance
from cooperative_clearing.shared.core.config import Settings


async def seed_demo_exchange(
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
    security = _principal(
        "security",
        "demo-member-elena",
        cooperative_id,
        (
            ("bootstrap-role", "security:SECURITY_ADMIN", RoleCode.SECURITY_ADMIN),
            ("demo-role", "security:DATA_STEWARD", RoleCode.DATA_STEWARD),
            ("demo-role", "security:LOGISTICS_OPERATOR", RoleCode.LOGISTICS_OPERATOR),
        ),
    )
    registrar_member_id = stable_id("member", "demo-member-anna")
    security_member_id = stable_id("member", "demo-member-elena")
    exchange = ExchangeService(settings)
    lot = (
        await session.execute(
            select(InventoryLot).where(
                InventoryLot.cooperative_id == cooperative_id,
                InventoryLot.lot_number == "DEMO-CABBAGE-001",
            )
        )
    ).scalar_one()
    legacy_fulfillment = (
        await session.execute(
            select(Fulfillment)
            .join(Obligation, Obligation.id == Fulfillment.obligation_id)
            .join(Deal, Deal.id == Obligation.deal_id)
            .where(
                Deal.title == "Demo cabbage delivery",
                Obligation.subject_type == "PRODUCT",
            )
            .order_by(Fulfillment.created_at, Fulfillment.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if (
        legacy_fulfillment is not None
        and await session.get(FulfillmentProvenance, legacy_fulfillment.id) is None
    ):
        legacy_source = await _redeemed_source(
            session,
            settings,
            registrar,
            cooperative_id,
            lot,
            catalog,
            quantity=legacy_fulfillment.quantity,
            expected_balance_version=5,
            key="demo-exchange-legacy-source-v1",
        )
        reconciliation_evidence = await _evidence(
            session,
            settings,
            registrar,
            cooperative_id,
            "demo-exchange-legacy-provenance-v1",
            "Signed reconciliation of the historical demo fulfillment to its physical lot.",
        )
        await exchange.reconcile_fulfillment_provenance(
            session,
            principal=registrar,
            fulfillment_id=legacy_fulfillment.id,
            source_redemption_id=legacy_source,
            rationale="Historical demo record reconciled from its signed warehouse release",
            evidence_ids=[reconciliation_evidence],
            idempotency_key="demo-exchange-legacy-provenance-v1",
            request_id=None,
        )
    proposed = await exchange.propose_deal(
        session,
        principal=registrar,
        cooperative_id=cooperative_id,
        title="Demo traced cabbage delivery",
        obligations=[
            ObligationDraft(
                debtor_member_id=registrar_member_id,
                creditor_member_id=security_member_id,
                subject_type="PRODUCT",
                subject_id=catalog.product_id,
                description="Deliver twenty kilograms of fresh cabbage",
                quality_criteria="Fresh, dry and suitable for food use",
                fulfillment_place="Reserve warehouse, receiving zone",
                due_at=datetime(2035, 1, 15, 12, 0, tzinfo=UTC),
                unit_id=catalog.unit_id,
                quantity=Decimal("20.00"),
                partial_allowed=True,
                evidence_required=True,
                confirmation_method="Independent receiving act",
                substitute_policy="Equivalent grade only after both parties confirm",
                valuation_source="No monetary valuation in local exchange slice",
                liquidity_class="UNASSESSED",
            )
        ],
        idempotency_key="demo-exchange-deal-propose-v3",
        request_id=None,
    )
    deal = await session.get(Deal, proposed.object_id)
    if deal is None:
        raise RuntimeError("demo deal was not created")
    await exchange.confirm_deal(
        session,
        principal=registrar,
        deal_id=deal.id,
        terms_version=1,
        terms_hash=deal.terms_hash,
        expected_version=1,
        idempotency_key="demo-exchange-deal-confirm-registrar-v3",
        request_id=None,
    )
    await exchange.confirm_deal(
        session,
        principal=security,
        deal_id=deal.id,
        terms_version=1,
        terms_hash=deal.terms_hash,
        expected_version=2,
        idempotency_key="demo-exchange-deal-confirm-security-v3",
        request_id=None,
    )
    obligation = (
        await session.execute(select(Obligation).where(Obligation.deal_id == deal.id))
    ).scalar_one()
    offered = await exchange.create_logistics_order(
        session,
        principal=registrar,
        obligation_id=obligation.id,
        carrier_member_id=security_member_id,
        quantity=Decimal("8.00"),
        origin_text="Main warehouse",
        destination_text="Reserve warehouse",
        pickup_due_at=datetime(2035, 1, 10, 9, 0, tzinfo=UTC),
        delivery_due_at=datetime(2035, 1, 10, 18, 0, tzinfo=UTC),
        expected_obligation_version=1,
        idempotency_key="demo-exchange-logistics-offer-v3",
        request_id=None,
    )
    pickup_evidence = await _evidence(
        session,
        settings,
        security,
        cooperative_id,
        "demo-exchange-pickup-v3",
        "Pickup act for eight kilograms of cabbage.",
    )
    delivery_evidence = await _evidence(
        session,
        settings,
        security,
        cooperative_id,
        "demo-exchange-delivery-v3",
        "Delivery act for eight kilograms of cabbage.",
    )
    for action, evidence_ids, version in (
        ("accept", [], 1),
        ("pickup", [pickup_evidence], 2),
        ("deliver", [delivery_evidence], 3),
    ):
        await exchange.transition_logistics_order(
            session,
            principal=security,
            order_id=offered.object_id,
            action=action,
            evidence_ids=evidence_ids,
            expected_version=version,
            idempotency_key=f"demo-exchange-logistics-{action}-v3",
            request_id=None,
        )
    fulfillment_evidence = await _evidence(
        session,
        settings,
        registrar,
        cooperative_id,
        "demo-exchange-fulfillment-v3",
        "Debtor fulfillment act for eight kilograms of cabbage.",
    )
    source_redemption_id = await _redeemed_source(
        session,
        settings,
        registrar,
        cooperative_id,
        lot,
        catalog,
        quantity=Decimal("8.00"),
        expected_balance_version=7 if legacy_fulfillment is not None else 5,
        key="demo-exchange-source-v3",
    )
    submitted = await exchange.submit_fulfillment(
        session,
        principal=registrar,
        obligation_id=obligation.id,
        quantity=Decimal("8.00"),
        quality_claim="Eight kilograms delivered in good condition",
        location_text="Reserve warehouse, receiving zone",
        performed_at=datetime(2035, 1, 10, 18, 0, tzinfo=UTC),
        logistics_order_id=offered.object_id,
        source_redemption_id=source_redemption_id,
        evidence_ids=[fulfillment_evidence],
        expected_version=1,
        idempotency_key="demo-exchange-fulfillment-submit-v3",
        request_id=None,
    )
    acceptance_evidence = await _evidence(
        session,
        settings,
        security,
        cooperative_id,
        "demo-exchange-acceptance-v3",
        "Creditor accepted six kilograms; two kilograms require replacement.",
    )
    await exchange.accept_fulfillment(
        session,
        principal=security,
        fulfillment_id=submitted.object_id,
        accepted_quantity=Decimal("6.00"),
        quality_status="Six kilograms accepted, two rejected",
        notes="Rejected remainder is released for replacement delivery",
        evidence_ids=[acceptance_evidence],
        expected_fulfillment_version=1,
        expected_obligation_version=2,
        idempotency_key="demo-exchange-fulfillment-accept-v3",
        request_id=None,
    )
    created_order = await session.get(LogisticsOrder, offered.object_id)
    created_fulfillment = await session.get(Fulfillment, submitted.object_id)
    if (
        created_order is None
        or created_order.status != "DELIVERED"
        or created_fulfillment is None
        or created_fulfillment.status != "PARTIALLY_ACCEPTED"
    ):
        raise RuntimeError("demo exchange flow was not completed")


async def _redeemed_source(
    session: AsyncSession,
    settings: Settings,
    principal: Principal,
    cooperative_id: UUID,
    lot: InventoryLot,
    catalog: DemoCatalog,
    *,
    quantity: Decimal,
    expected_balance_version: int,
    key: str,
) -> UUID:
    if principal.member_id is None:
        raise RuntimeError("demo source principal has no member")
    if await session.get(LotBalance, lot.id) is None:
        raise RuntimeError("demo lot balance is unavailable")
    rights = CommodityRightsService(settings)
    issued = await rights.issue(
        session,
        principal=principal,
        lot_id=lot.id,
        owner_member_id=principal.member_id,
        quantity=quantity,
        redeem_warehouse_id=catalog.warehouse_b_id,
        valid_until=None,
        expected_balance_version=expected_balance_version,
        idempotency_key=f"{key}-issue",
        request_id=None,
    )
    requested = await rights.request_redemption(
        session,
        principal=principal,
        right_id=issued.object_id,
        owner_member_id=principal.member_id,
        expected_version=1,
        idempotency_key=f"{key}-request",
        request_id=None,
    )
    release_evidence = await _evidence(
        session,
        settings,
        principal,
        cooperative_id,
        f"{key}-release",
        f"Warehouse release of {quantity} units from lot {lot.lot_number}.",
    )
    await rights.complete_redemption(
        session,
        principal=principal,
        redemption_id=requested.object_id,
        evidence_ids=[release_evidence],
        expected_right_version=2,
        idempotency_key=f"{key}-complete",
        request_id=None,
    )
    return requested.object_id

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
                None if role is RoleCode.SECURITY_ADMIN else cooperative_id,
            )
            for id_kind, id_value, role in roles
        ),
    )
