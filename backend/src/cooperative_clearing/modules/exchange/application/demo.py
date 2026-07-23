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
    LogisticsOrder,
    Obligation,
)
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.inventory.application.demo import DemoCatalog
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
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
    proposed = await exchange.propose_deal(
        session,
        principal=registrar,
        cooperative_id=cooperative_id,
        title="Demo cabbage delivery",
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
        idempotency_key="demo-exchange-deal-propose-v2",
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
        idempotency_key="demo-exchange-deal-confirm-registrar-v2",
        request_id=None,
    )
    await exchange.confirm_deal(
        session,
        principal=security,
        deal_id=deal.id,
        terms_version=1,
        terms_hash=deal.terms_hash,
        expected_version=2,
        idempotency_key="demo-exchange-deal-confirm-security-v2",
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
        idempotency_key="demo-exchange-logistics-offer-v2",
        request_id=None,
    )
    pickup_evidence = await _evidence(
        session,
        settings,
        security,
        cooperative_id,
        "demo-exchange-pickup-v2",
        "Pickup act for eight kilograms of cabbage.",
    )
    delivery_evidence = await _evidence(
        session,
        settings,
        security,
        cooperative_id,
        "demo-exchange-delivery-v2",
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
            idempotency_key=f"demo-exchange-logistics-{action}-v2",
            request_id=None,
        )
    fulfillment_evidence = await _evidence(
        session,
        settings,
        registrar,
        cooperative_id,
        "demo-exchange-fulfillment-v2",
        "Debtor fulfillment act for eight kilograms of cabbage.",
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
        evidence_ids=[fulfillment_evidence],
        expected_version=1,
        idempotency_key="demo-exchange-fulfillment-submit-v2",
        request_id=None,
    )
    acceptance_evidence = await _evidence(
        session,
        settings,
        security,
        cooperative_id,
        "demo-exchange-acceptance-v2",
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
        idempotency_key="demo-exchange-fulfillment-accept-v2",
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
