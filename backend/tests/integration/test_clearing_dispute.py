from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.modules.clearing.application.service import ClearingService
from cooperative_clearing.modules.clearing.domain.engine import RoundingMode
from cooperative_clearing.modules.clearing.infrastructure.models import (
    ClearingCycle,
    ClearingDispute,
    ClearingEntry,
    ClearingPolicy,
)
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import RoleCode
from cooperative_clearing.modules.inventory.infrastructure.models import UnitOfMeasure
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database
from tests.integration.test_clearing_concurrency import create_confirmed_deal, principal
from tests.integration.test_inventory_flow import evidence


@pytest.mark.integration
async def test_participant_dispute_requires_evidence_and_independent_decision() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"clearing-dispute-{suffix}",
        blob_root=Path(f"/tmp/clearing-dispute-{suffix}"),
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
                dispute_window_seconds=3600,
                required_approvals=1,
                liquidity_order=("A", "B", "C", "D", "E", "UNASSESSED"),
                idempotency_key=f"dispute-{suffix}-policy-propose",
                request_id=uuid4(),
            )
            await service.approve_policy(
                session,
                principal=controller,
                policy_id=proposed.object_id,
                expected_version=1,
                idempotency_key=f"dispute-{suffix}-policy-approve",
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
            amount=Decimal("20.00"),
            suffix=f"{suffix}-forward",
        )
        await create_confirmed_deal(
            database,
            settings,
            operator=operator,
            counterparty=finalizer,
            cooperative_id=cooperative_id,
            unit_id=unit.id,
            debtor_id=pavel_id,
            creditor_id=anna_id,
            amount=Decimal("15.00"),
            suffix=f"{suffix}-reverse",
        )

        async with database.session() as session:
            created = await service.create_cycle(
                session,
                principal=operator,
                cooperative_id=cooperative_id,
                policy_id=policy.id,
                cycle_code=f"DISPUTE-{suffix}",
                period_start=datetime(2035, 1, 1, tzinfo=UTC),
                period_end=datetime(2035, 1, 8, tzinfo=UTC),
                idempotency_key=f"dispute-{suffix}-cycle",
                request_id=uuid4(),
            )
            cycle_id = created.object_id
            await service.collect(
                session,
                principal=operator,
                cycle_id=cycle_id,
                expected_version=1,
                idempotency_key=f"dispute-{suffix}-collect",
                request_id=uuid4(),
            )
            await service.freeze_input(
                session,
                principal=operator,
                cycle_id=cycle_id,
                expected_version=2,
                idempotency_key=f"dispute-{suffix}-freeze",
                request_id=uuid4(),
            )
            await service.preview(
                session,
                principal=operator,
                cycle_id=cycle_id,
                expected_version=3,
                idempotency_key=f"dispute-{suffix}-preview",
                request_id=uuid4(),
            )
            cycle = await session.get(ClearingCycle, cycle_id)
            assert cycle is not None and cycle.input_hash and cycle.result_hash
            await service.approve_preview(
                session,
                principal=controller,
                cycle_id=cycle_id,
                expected_version=4,
                input_hash=cycle.input_hash,
                result_hash=cycle.result_hash,
                idempotency_key=f"dispute-{suffix}-preview-approval",
                request_id=uuid4(),
            )
            entry = (
                await session.execute(
                    select(ClearingEntry).where(
                        ClearingEntry.cycle_id == cycle_id,
                        ClearingEntry.obligation_id == forward_id,
                    )
                )
            ).scalar_one()
            await session.commit()

        evidence_id = await evidence(
            database,
            settings,
            operator,
            cooperative_id,
            b"participant disputes the exact clearing amount",
            f"clearing-dispute-{suffix}.txt",
        )
        async with database.session() as session:
            opened = await service.open_dispute(
                session,
                principal=operator,
                cycle_id=cycle_id,
                entry_id=entry.id,
                reason_code="AMOUNT_DISPUTED",
                statement="The participant requests independent review of the amount.",
                evidence_ids=[evidence_id],
                expected_version=5,
                idempotency_key=f"dispute-{suffix}-open",
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            dispute = await session.get(ClearingDispute, opened.object_id)
            cycle = await session.get(ClearingCycle, cycle_id)
            assert dispute is not None and cycle is not None
            assert dispute.status == "OPEN"
            assert dispute.evidence_refs[0]["evidence_id"] == str(evidence_id)
            assert cycle.status == "DISPUTED" and cycle.version == 6
            await service.decide_dispute(
                session,
                principal=controller,
                dispute_id=dispute.id,
                decision="REJECT",
                resolution_notes="Evidence was reviewed; the frozen amount is correct.",
                expected_version=1,
                expected_cycle_version=6,
                idempotency_key=f"dispute-{suffix}-decision",
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            dispute = await session.get(ClearingDispute, opened.object_id)
            cycle = await session.get(ClearingCycle, cycle_id)
        assert dispute is not None and dispute.status == "REJECTED" and dispute.version == 2
        assert cycle is not None and cycle.status == "DISPUTE_WINDOW" and cycle.version == 7
    finally:
        await database.dispose()
