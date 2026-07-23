import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.modules.clearing.application.service import ClearingService
from cooperative_clearing.modules.clearing.domain.engine import RoundingMode
from cooperative_clearing.modules.clearing.infrastructure.models import (
    ClearingCycle,
    ClearingEntry,
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
from cooperative_clearing.modules.inventory.infrastructure.models import UnitOfMeasure
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database


def principal(
    login: str,
    member_id: UUID,
    cooperative_id: UUID,
    roles: tuple[tuple[str, str, RoleCode], ...],
) -> Principal:
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=uuid4(),
        login=login,
        member_id=member_id,
        must_change_password=False,
        roles=tuple(
            RoleGrant(
                stable_id(kind, value),
                role,
                None if role is RoleCode.AUDITOR else cooperative_id,
            )
            for kind, value, role in roles
        ),
    )


async def create_confirmed_deal(
    database: Database,
    settings: Settings,
    *,
    operator: Principal,
    counterparty: Principal,
    cooperative_id: UUID,
    unit_id: UUID,
    debtor_id: UUID,
    creditor_id: UUID,
    amount: Decimal,
    suffix: str,
) -> UUID:
    service = ExchangeService(settings)
    async with database.session() as session:
        result = await service.propose_deal(
            session,
            principal=operator,
            cooperative_id=cooperative_id,
            title=f"Concurrent clearing {suffix}",
            obligations=(
                ObligationDraft(
                    debtor_member_id=debtor_id,
                    creditor_member_id=creditor_id,
                    subject_type="OTHER",
                    subject_id=None,
                    description=f"Concurrent obligation {suffix}",
                    quality_criteria="Confirmed exact valuation obligation",
                    fulfillment_place="Local cooperative ledger",
                    due_at=datetime(2035, 1, 7, 18, 0, tzinfo=UTC),
                    unit_id=unit_id,
                    quantity=amount,
                    partial_allowed=True,
                    evidence_required=False,
                    confirmation_method="Both parties confirm canonical terms",
                    substitute_policy="No substitution",
                    valuation_source="Approved clearing policy",
                    liquidity_class="B",
                    clearing_allowed=True,
                ),
            ),
            idempotency_key=f"concurrency-{suffix}-propose",
            request_id=uuid4(),
        )
        deal = await session.get(Deal, result.object_id)
        assert deal is not None
        parties = {operator.member_id: operator, counterparty.member_id: counterparty}
        for expected_version, member_id in enumerate((debtor_id, creditor_id), start=1):
            actor = parties[member_id]
            await service.confirm_deal(
                session,
                principal=actor,
                deal_id=deal.id,
                terms_version=deal.terms_version,
                terms_hash=deal.terms_hash,
                expected_version=expected_version,
                idempotency_key=f"concurrency-{suffix}-confirm-{member_id}",
                request_id=uuid4(),
            )
        obligation_id = (
            await session.execute(select(Obligation.id).where(Obligation.deal_id == deal.id))
        ).scalar_one()
        await session.commit()
    return obligation_id


@pytest.mark.integration
async def test_concurrent_finalization_applies_clearing_exactly_once() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"clearing-concurrency-{suffix}",
        blob_root=Path(f"/tmp/clearing-concurrency-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    cooperative_id = stable_id("cooperative", settings.node_code)
    anna_id = stable_id("member", "demo-member-anna")
    pavel_id = stable_id("member", "demo-member-pavel")
    operator = principal(
        "registrar",
        anna_id,
        cooperative_id,
        (
            ("bootstrap-role", "registrar:COOPERATIVE_ADMIN", RoleCode.COOPERATIVE_ADMIN),
            ("demo-role", "registrar:CLEARING_OPERATOR", RoleCode.CLEARING_OPERATOR),
        ),
    )
    controller = principal(
        "security",
        stable_id("member", "demo-member-elena"),
        cooperative_id,
        (("demo-role", "security:CLEARING_CONTROLLER", RoleCode.CLEARING_CONTROLLER),),
    )
    finalizer = principal(
        "auditor",
        pavel_id,
        cooperative_id,
        (
            ("bootstrap-role", "auditor:AUDITOR", RoleCode.AUDITOR),
            ("demo-role", "auditor:CLEARING_FINALIZER", RoleCode.CLEARING_FINALIZER),
        ),
    )
    service = ClearingService(settings)
    try:
        async with database.session() as session:
            unit = (
                await session.execute(
                    select(UnitOfMeasure).where(UnitOfMeasure.code == "DEMO_SHARE")
                )
            ).scalar_one()
            proposed = await service.propose_policy(
                session,
                principal=operator,
                cooperative_id=cooperative_id,
                valuation_unit_id=unit.id,
                decimal_scale=2,
                rounding_mode=RoundingMode.DOWN,
                minimum_operation=Decimal("0.01"),
                max_iterations=1000,
                max_cycle_length=6,
                dispute_window_seconds=0,
                required_approvals=1,
                liquidity_order=("A", "B", "C", "D", "E", "UNASSESSED"),
                idempotency_key=f"concurrency-{suffix}-policy-propose",
                request_id=uuid4(),
            )
            await service.approve_policy(
                session,
                principal=controller,
                policy_id=proposed.object_id,
                expected_version=1,
                idempotency_key=f"concurrency-{suffix}-policy-approve",
                request_id=uuid4(),
            )
            policy = await session.get(ClearingPolicy, proposed.object_id)
            assert policy is not None and policy.status == "ACTIVE"
            await session.commit()
        forward_id = await create_confirmed_deal(
            database,
            settings,
            operator=operator,
            counterparty=finalizer,
            cooperative_id=cooperative_id,
            unit_id=unit.id,
            debtor_id=anna_id,
            creditor_id=pavel_id,
            amount=Decimal("12.00"),
            suffix=f"{suffix}-forward",
        )
        reverse_id = await create_confirmed_deal(
            database,
            settings,
            operator=operator,
            counterparty=finalizer,
            cooperative_id=cooperative_id,
            unit_id=unit.id,
            debtor_id=pavel_id,
            creditor_id=anna_id,
            amount=Decimal("9.00"),
            suffix=f"{suffix}-reverse",
        )

        async with database.session() as session:
            created = await service.create_cycle(
                session,
                principal=operator,
                cooperative_id=cooperative_id,
                policy_id=policy.id,
                cycle_code=f"CONCURRENT-{suffix}",
                period_start=datetime(2035, 1, 1, tzinfo=UTC),
                period_end=datetime(2035, 1, 8, tzinfo=UTC),
                idempotency_key=f"concurrency-{suffix}-cycle",
                request_id=uuid4(),
            )
            cycle_id = created.object_id
            await service.collect(
                session,
                principal=operator,
                cycle_id=cycle_id,
                expected_version=1,
                idempotency_key=f"concurrency-{suffix}-collect",
                request_id=uuid4(),
            )
            await service.freeze_input(
                session,
                principal=operator,
                cycle_id=cycle_id,
                expected_version=2,
                idempotency_key=f"concurrency-{suffix}-freeze",
                request_id=uuid4(),
            )
            await service.preview(
                session,
                principal=operator,
                cycle_id=cycle_id,
                expected_version=3,
                idempotency_key=f"concurrency-{suffix}-preview",
                request_id=uuid4(),
            )
            cycle = await session.get(ClearingCycle, cycle_id)
            assert cycle is not None and cycle.input_hash and cycle.result_hash
            expected_cleared = {
                item.obligation_id: item.cleared_amount
                for item in (
                    await session.execute(
                        select(ClearingEntry).where(
                            ClearingEntry.cycle_id == cycle_id,
                            ClearingEntry.obligation_id.in_([forward_id, reverse_id]),
                        )
                    )
                ).scalars()
            }
            await service.approve_preview(
                session,
                principal=controller,
                cycle_id=cycle_id,
                expected_version=4,
                input_hash=cycle.input_hash,
                result_hash=cycle.result_hash,
                idempotency_key=f"concurrency-{suffix}-approve",
                request_id=uuid4(),
            )
            await service.mark_ready(
                session,
                principal=finalizer,
                cycle_id=cycle_id,
                expected_version=5,
                idempotency_key=f"concurrency-{suffix}-ready",
                request_id=uuid4(),
            )
            result_hash = cycle.result_hash
            await session.commit()

        async def finalize(attempt: str) -> str:
            async with database.session() as session:
                try:
                    await ClearingService(settings).finalize(
                        session,
                        principal=finalizer,
                        cycle_id=cycle_id,
                        expected_version=6,
                        result_hash=result_hash,
                        idempotency_key=f"concurrency-{suffix}-finalize-{attempt}",
                        request_id=uuid4(),
                    )
                    await session.commit()
                    return "COMMITTED"
                except DomainError as exc:
                    await session.rollback()
                    return exc.code

        outcomes = await asyncio.gather(finalize("a"), finalize("b"))
        assert outcomes.count("COMMITTED") == 1
        assert outcomes.count("VERSION_CONFLICT") == 1

        async with database.session() as session:
            cycle = await session.get(ClearingCycle, cycle_id)
            proof_count = await session.scalar(
                select(func.count())
                .select_from(ClearingProof)
                .where(ClearingProof.cycle_id == cycle_id)
            )
            obligations = list(
                (
                    await session.execute(
                        select(Obligation).where(Obligation.id.in_([forward_id, reverse_id]))
                    )
                ).scalars()
            )
        assert cycle is not None and cycle.status == "FINALIZED" and cycle.version == 7
        assert proof_count == 1
        assert set(expected_cleared) == {forward_id, reverse_id}
        assert sum(expected_cleared.values(), start=Decimal("0")) > 0
        assert {item.id: item.quantity_cleared for item in obligations} == expected_cleared
    finally:
        await database.dispose()
