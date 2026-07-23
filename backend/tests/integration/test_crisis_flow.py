"""End-to-end crisis drill and persistence invariants."""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.modules.crisis.infrastructure.models import (
    CrisisMandate,
    CrisisPaperForm,
    CrisisReport,
    CrisisReview,
    RationingAllocation,
    RationingPlan,
    RationingRule,
    RationIssuance,
    ReserveSnapshot,
    ReserveTarget,
)
from cooperative_clearing.modules.exchange.infrastructure.models import Obligation
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.modules.risk.infrastructure.models import (
    ExposureCommitment,
    ShareContribution,
)
from cooperative_clearing.modules.trust.infrastructure.models import ReputationEvent
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database


async def seeded_crisis_database(prefix: str) -> tuple[Settings, Database]:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"{prefix}-{suffix}", blob_root=Path(f"/tmp/{prefix}-{suffix}")
    )
    await initialize_node(settings)
    await seed_demo(settings)
    return settings, Database.from_settings(settings)


@pytest.mark.integration
async def test_demo_crisis_drill_is_bounded_reconciled_idempotent_and_non_credit() -> None:
    settings, database = await seeded_crisis_database("crisis-flow")
    try:
        async with database.session() as session:
            mandate = (
                await session.execute(
                    select(CrisisMandate).where(CrisisMandate.mandate_code == "DEMO-CRISIS-001")
                )
            ).scalar_one()
            target = (
                (
                    await session.execute(
                        select(ReserveTarget)
                        .where(
                            ReserveTarget.resource_code == "CABBAGE",
                            ReserveTarget.cooperative_id == mandate.cooperative_id,
                        )
                        .order_by(ReserveTarget.policy_version.desc())
                    )
                )
                .scalars()
                .first()
            )
            assert target is not None
            snapshot = (
                await session.execute(
                    select(ReserveSnapshot).where(ReserveSnapshot.target_id == target.id)
                )
            ).scalar_one()
            rule = (
                await session.execute(
                    select(RationingRule).where(RationingRule.mandate_id == mandate.id)
                )
            ).scalar_one()
            plan = (
                await session.execute(select(RationingPlan).where(RationingPlan.rule_id == rule.id))
            ).scalar_one()
            allocation = (
                await session.execute(
                    select(RationingAllocation).where(RationingAllocation.plan_id == plan.id)
                )
            ).scalar_one()
            issuance = (
                await session.execute(
                    select(RationIssuance).where(RationIssuance.allocation_id == allocation.id)
                )
            ).scalar_one()
            form = (
                await session.execute(
                    select(CrisisPaperForm).where(CrisisPaperForm.mandate_id == mandate.id)
                )
            ).scalar_one()
            reviews = list(
                (
                    await session.execute(
                        select(CrisisReview)
                        .where(CrisisReview.mandate_id == mandate.id)
                        .order_by(CrisisReview.decision_round)
                    )
                ).scalars()
            )
            report = (
                await session.execute(
                    select(CrisisReport).where(CrisisReport.mandate_id == mandate.id)
                )
            ).scalar_one()
            crisis_events = set(
                (
                    await session.execute(
                        select(SignedEvent.event_id).where(SignedEvent.event_type.like("crisis.%"))
                    )
                ).scalars()
            )
            obligation_events = set(
                (await session.execute(select(Obligation.created_event_id))).scalars()
            )
            share_events = set(
                (await session.execute(select(ShareContribution.event_id))).scalars()
            )
            commitment_events = set(
                (await session.execute(select(ExposureCommitment.proposed_event_id))).scalars()
            )
            reputation_sources = {
                event_id
                for source_ids in (
                    await session.execute(select(ReputationEvent.source_event_ids))
                ).scalars()
                for event_id in source_ids
            }
            before = (
                int(await session.scalar(select(func.count()).select_from(CrisisReport)) or 0),
                int(
                    await session.scalar(
                        select(func.count())
                        .select_from(SignedEvent)
                        .where(SignedEvent.event_type.like("crisis.%"))
                    )
                    or 0
                ),
            )

        assert target.status == "ACTIVE" and target.version == 2
        assert snapshot.available_quantity == 50 and snapshot.reserve_level == "WARNING"
        assert mandate.status == "CLOSED" and mandate.version == 4
        assert rule.status == "ACTIVE" and rule.version == 2
        assert plan.status == "CONFIRMED" and plan.version == 2
        assert allocation.status == "ISSUED" and allocation.quantity == 5
        assert issuance.quantity == 5
        assert form.status == "RECORDED" and form.payload_hash is not None
        assert [item.decision for item in reviews] == ["CONTINUE", "CLOSE"]
        assert report.report_payload["ration_issuance_count"] == 1
        assert report.report_payload["paper_form_count"] == 1
        assert crisis_events
        assert crisis_events.isdisjoint(obligation_events)
        assert crisis_events.isdisjoint(share_events)
        assert crisis_events.isdisjoint(commitment_events)
        assert crisis_events.isdisjoint(reputation_sources)

        await seed_demo(settings)
        async with database.session() as session:
            after = (
                int(await session.scalar(select(func.count()).select_from(CrisisReport)) or 0),
                int(
                    await session.scalar(
                        select(func.count())
                        .select_from(SignedEvent)
                        .where(SignedEvent.event_type.like("crisis.%"))
                    )
                    or 0
                ),
            )
        assert after == before

        async with database.session() as session:
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(ReserveSnapshot)
                    .where(ReserveSnapshot.id == snapshot.id)
                    .values(confidence="0.1")
                )
                await session.flush()
            await session.rollback()
    finally:
        await database.dispose()
