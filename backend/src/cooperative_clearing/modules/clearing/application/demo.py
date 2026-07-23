"""Deterministic weekly clearing demo through production commands."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.clearing.application.service import ClearingService
from cooperative_clearing.modules.clearing.domain.engine import RoundingMode
from cooperative_clearing.modules.clearing.infrastructure.models import (
    ClearingCycle,
    ClearingPolicy,
    ClearingProof,
)
from cooperative_clearing.modules.exchange.application.service import (
    ExchangeService,
    ObligationDraft,
)
from cooperative_clearing.modules.exchange.infrastructure.models import Deal, Obligation
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.inventory.application.catalog import CatalogService
from cooperative_clearing.shared.core.config import Settings


async def seed_demo_clearing(session: AsyncSession, settings: Settings) -> None:
    cooperative_id = stable_id("cooperative", settings.node_code)
    anna_id = stable_id("member", "demo-member-anna")
    pavel_id = stable_id("member", "demo-member-pavel")
    operator = _principal(
        "registrar",
        anna_id,
        cooperative_id,
        (
            ("bootstrap-role", "registrar:COOPERATIVE_ADMIN", RoleCode.COOPERATIVE_ADMIN),
            ("demo-role", "registrar:CLEARING_OPERATOR", RoleCode.CLEARING_OPERATOR),
        ),
    )
    controller = _principal(
        "security",
        stable_id("member", "demo-member-elena"),
        cooperative_id,
        (("demo-role", "security:CLEARING_CONTROLLER", RoleCode.CLEARING_CONTROLLER),),
    )
    finalizer = _principal(
        "auditor",
        pavel_id,
        cooperative_id,
        (
            ("bootstrap-role", "auditor:AUDITOR", RoleCode.AUDITOR),
            ("demo-role", "auditor:CLEARING_FINALIZER", RoleCode.CLEARING_FINALIZER),
        ),
    )

    unit_result = await CatalogService(settings).create_unit(
        session,
        principal=operator,
        cooperative_id=cooperative_id,
        code="DEMO_SHARE",
        name="Demo clearing valuation unit",
        symbol="DS",
        dimension="VALUATION",
        decimal_scale=2,
        idempotency_key="demo-clearing-unit-v1",
        request_id=None,
    )
    unit_id = unit_result.object_id

    policy_result = await ClearingService(settings).propose_policy(
        session,
        principal=operator,
        cooperative_id=cooperative_id,
        valuation_unit_id=unit_id,
        decimal_scale=2,
        rounding_mode=RoundingMode.DOWN,
        minimum_operation=Decimal("0.01"),
        max_iterations=1000,
        max_cycle_length=6,
        dispute_window_seconds=0,
        required_approvals=1,
        liquidity_order=("A", "B", "C", "D", "E", "UNASSESSED"),
        idempotency_key="demo-clearing-policy-propose-v1",
        request_id=None,
    )
    policy = await session.get(ClearingPolicy, policy_result.object_id)
    if policy is None:
        raise RuntimeError("demo clearing policy was not created")
    await ClearingService(settings).approve_policy(
        session,
        principal=controller,
        policy_id=policy.id,
        expected_version=1,
        idempotency_key="demo-clearing-policy-approve-v1",
        request_id=None,
    )

    await _deal(
        session,
        settings,
        operator,
        finalizer,
        cooperative_id,
        unit_id,
        anna_id,
        pavel_id,
        "Anna to Pavel weekly obligation",
        Decimal("70.00"),
        "demo-clearing-deal-forward-v1",
    )
    await _deal(
        session,
        settings,
        operator,
        finalizer,
        cooperative_id,
        unit_id,
        pavel_id,
        anna_id,
        "Pavel to Anna weekly obligation",
        Decimal("50.00"),
        "demo-clearing-deal-reverse-v1",
    )

    clearing = ClearingService(settings)
    cycle_result = await clearing.create_cycle(
        session,
        principal=operator,
        cooperative_id=cooperative_id,
        policy_id=policy.id,
        cycle_code="DEMO-WEEK-2035-01",
        period_start=datetime(2035, 1, 1, tzinfo=UTC),
        period_end=datetime(2035, 1, 8, tzinfo=UTC),
        idempotency_key="demo-clearing-cycle-create-v1",
        request_id=None,
    )
    cycle_id = cycle_result.object_id
    await clearing.collect(
        session,
        principal=operator,
        cycle_id=cycle_id,
        expected_version=1,
        idempotency_key="demo-clearing-cycle-collect-v1",
        request_id=None,
    )
    await clearing.freeze_input(
        session,
        principal=operator,
        cycle_id=cycle_id,
        expected_version=2,
        idempotency_key="demo-clearing-cycle-freeze-v1",
        request_id=None,
    )
    await clearing.preview(
        session,
        principal=operator,
        cycle_id=cycle_id,
        expected_version=3,
        idempotency_key="demo-clearing-cycle-preview-v1",
        request_id=None,
    )
    cycle = await session.get(ClearingCycle, cycle_id)
    if cycle is None or cycle.input_hash is None or cycle.result_hash is None:
        raise RuntimeError("demo clearing preview was not created")
    await clearing.approve_preview(
        session,
        principal=controller,
        cycle_id=cycle_id,
        expected_version=4,
        input_hash=cycle.input_hash,
        result_hash=cycle.result_hash,
        idempotency_key="demo-clearing-cycle-approve-v1",
        request_id=None,
    )
    await clearing.mark_ready(
        session,
        principal=finalizer,
        cycle_id=cycle_id,
        expected_version=5,
        idempotency_key="demo-clearing-cycle-ready-v1",
        request_id=None,
    )
    await clearing.finalize(
        session,
        principal=finalizer,
        cycle_id=cycle_id,
        expected_version=6,
        result_hash=cycle.result_hash,
        idempotency_key="demo-clearing-cycle-finalize-v1",
        request_id=None,
    )
    await clearing.reconcile(
        session,
        principal=finalizer,
        cycle_id=cycle_id,
        expected_version=7,
        idempotency_key="demo-clearing-cycle-reconcile-v1",
        request_id=None,
    )
    proof = (
        await session.execute(select(ClearingProof).where(ClearingProof.cycle_id == cycle_id))
    ).scalar_one_or_none()
    if cycle.status != "RECONCILED" or proof is None:
        raise RuntimeError("demo clearing cycle was not reconciled")


async def _deal(
    session: AsyncSession,
    settings: Settings,
    operator: Principal,
    counterparty: Principal,
    cooperative_id: UUID,
    unit_id: UUID,
    debtor_id: UUID,
    creditor_id: UUID,
    title: str,
    amount: Decimal,
    key: str,
) -> None:
    exchange = ExchangeService(settings)
    result = await exchange.propose_deal(
        session,
        principal=operator,
        cooperative_id=cooperative_id,
        title=title,
        obligations=(
            ObligationDraft(
                debtor_member_id=debtor_id,
                creditor_member_id=creditor_id,
                subject_type="OTHER",
                subject_id=None,
                description=title,
                quality_criteria="Confirmed valuation obligation",
                fulfillment_place="Local cooperative ledger",
                due_at=datetime(2035, 1, 7, 18, 0, tzinfo=UTC),
                unit_id=unit_id,
                quantity=amount,
                partial_allowed=True,
                evidence_required=False,
                confirmation_method="Both parties confirm canonical deal terms",
                substitute_policy="No substitution",
                valuation_source="Demo approved clearing valuation policy",
                liquidity_class="B",
                clearing_allowed=True,
            ),
        ),
        idempotency_key=f"{key}-propose",
        request_id=None,
    )
    deal = await session.get(Deal, result.object_id)
    if deal is None:
        raise RuntimeError("demo clearing deal was not created")
    parties = {operator.member_id: operator, counterparty.member_id: counterparty}
    for expected_version, member_id in enumerate((debtor_id, creditor_id), start=1):
        principal = parties.get(member_id)
        if principal is None:
            raise RuntimeError("demo clearing party has no principal")
        await exchange.confirm_deal(
            session,
            principal=principal,
            deal_id=deal.id,
            terms_version=1,
            terms_hash=deal.terms_hash,
            expected_version=expected_version,
            idempotency_key=f"{key}-confirm-{member_id}",
            request_id=None,
        )
    obligation = (
        await session.execute(select(Obligation).where(Obligation.deal_id == deal.id))
    ).scalar_one_or_none()
    if obligation is None or not obligation.clearing_allowed:
        raise RuntimeError("demo clearing obligation was not activated")


def _principal(
    login: str,
    member_id: UUID,
    cooperative_id: UUID,
    roles: tuple[tuple[str, str, RoleCode], ...],
) -> Principal:
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=stable_id("demo-session", login),
        login=login,
        member_id=member_id,
        must_change_password=False,
        roles=tuple(
            RoleGrant(
                stable_id(id_kind, id_value),
                role,
                None if role is RoleCode.AUDITOR else cooperative_id,
            )
            for id_kind, id_value, role in roles
        ),
    )
