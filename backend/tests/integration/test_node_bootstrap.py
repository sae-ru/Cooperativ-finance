from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from cooperative_clearing.cli import initialize_node, seed_demo, verify_local_journal
from cooperative_clearing.modules.clearing.domain.verifier import verify_proof_payload
from cooperative_clearing.modules.clearing.infrastructure.models import (
    ClearingAccountingExport,
    ClearingCycle,
    ClearingEntry,
    ClearingPolicy,
    ClearingProof,
    ClearingStatement,
)
from cooperative_clearing.modules.exchange.infrastructure.models import Obligation
from cooperative_clearing.modules.federation.infrastructure.discovery_models import FederatedOffer
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.modules.node.application.status import GetSystemStatus
from cooperative_clearing.modules.node.infrastructure.repository import NodeRepository
from cooperative_clearing.modules.risk.infrastructure.models import (
    ExposureCommitment,
    RiskPolicy,
    ShareAccount,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database


async def demo_snapshot(
    database: Database, settings: Settings
) -> tuple[int, int, int, int, int, int, int, int, int]:
    cooperative_id = stable_id("cooperative", settings.node_code)
    async with database.session() as session:
        events = (await session.execute(select(func.count()).select_from(SignedEvent))).scalar_one()
        policies = (
            await session.execute(
                select(func.count())
                .select_from(RiskPolicy)
                .where(
                    RiskPolicy.cooperative_id == cooperative_id,
                    RiskPolicy.denomination == "DEMO_SHARE",
                )
            )
        ).scalar_one()
        accounts = (
            await session.execute(
                select(func.count())
                .select_from(ShareAccount)
                .where(
                    ShareAccount.cooperative_id == cooperative_id,
                    ShareAccount.denomination == "DEMO_SHARE",
                )
            )
        ).scalar_one()
        commitments = (
            await session.execute(
                select(func.count())
                .select_from(ExposureCommitment)
                .where(
                    ExposureCommitment.cooperative_id == cooperative_id,
                    ExposureCommitment.risk_type == "DEMO_DELIVERY",
                )
            )
        ).scalar_one()
        clearing_policies = (
            await session.execute(
                select(func.count())
                .select_from(ClearingPolicy)
                .where(
                    ClearingPolicy.cooperative_id == cooperative_id,
                    ClearingPolicy.status == "ACTIVE",
                )
            )
        ).scalar_one()
        cycle = (
            await session.execute(
                select(ClearingCycle).where(
                    ClearingCycle.cooperative_id == cooperative_id,
                    ClearingCycle.cycle_code == "DEMO-WEEK-2035-01",
                )
            )
        ).scalar_one()
        clearing_entries = (
            await session.execute(
                select(func.count())
                .select_from(ClearingEntry)
                .where(ClearingEntry.cycle_id == cycle.id)
            )
        ).scalar_one()
        proofs = (
            await session.execute(
                select(func.count())
                .select_from(ClearingProof)
                .where(ClearingProof.cycle_id == cycle.id)
            )
        ).scalar_one()
        statements = (
            await session.execute(
                select(func.count())
                .select_from(ClearingStatement)
                .where(ClearingStatement.cycle_id == cycle.id)
            )
        ).scalar_one()
        exports = (
            await session.execute(
                select(func.count())
                .select_from(ClearingAccountingExport)
                .where(ClearingAccountingExport.cycle_id == cycle.id)
            )
        ).scalar_one()
    assert cycle.status == "RECONCILED"
    assert cycle.version == 8
    return (
        int(events),
        int(policies),
        int(accounts),
        int(commitments),
        int(clearing_policies),
        int(clearing_entries),
        int(proofs),
        int(statements),
        int(exports),
    )


@pytest.mark.integration
async def test_node_initialization_and_demo_seed_are_idempotent() -> None:
    settings = Settings(service_name="integration-test")
    assert settings.demo_data_enabled is True

    await initialize_node(settings)
    await initialize_node(settings)
    await seed_demo(settings)

    database = Database.from_settings(settings)
    try:
        before = await demo_snapshot(database, settings)
        await seed_demo(settings)
        after = await demo_snapshot(database, settings)

        async with database.session() as session:
            local_nails = await session.scalar(
                select(func.count())
                .select_from(FederatedOffer)
                .where(
                    FederatedOffer.product_code == "NAIL.STEEL.100MM",
                    FederatedOffer.external_node_id.is_(None),
                    FederatedOffer.status == "ACTIVE",
                )
            )
            assert local_nails == 1
            repository = NodeRepository(session)
            await repository.record_worker_heartbeat(
                worker_name="outbox-worker",
                instance_id=uuid4(),
                release=settings.release,
            )
            await session.commit()

        status = await GetSystemStatus(database=database, settings=settings).execute()
    finally:
        await database.dispose()

    journal_report = await verify_local_journal(settings)

    assert status.status == "OPERATIONAL"
    assert status.demo_data_loaded is True
    assert status.worker_status == "RUNNING"
    assert {notice.code for notice in status.notices} == {
        "DEMO_BACKUP_DRILL_PENDING",
        "DEMO_POLICY_REVIEW_SCHEDULED",
    }
    assert journal_report.ok is True
    assert journal_report.checked_events > 0
    assert journal_report.last_sequence == journal_report.checked_events
    assert before == after
    assert before[1:] == (1, 2, 1, 1, 2, 1, 2, 1)


@pytest.mark.integration
async def test_finalized_clearing_proof_is_valid_and_append_only() -> None:
    settings = Settings(service_name="clearing-proof-integration")
    await seed_demo(settings)
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            cycle = (
                await session.execute(
                    select(ClearingCycle).where(ClearingCycle.cycle_code == "DEMO-WEEK-2035-01")
                )
            ).scalar_one()
            proof = (
                await session.execute(
                    select(ClearingProof).where(ClearingProof.cycle_id == cycle.id)
                )
            ).scalar_one()
            verification = verify_proof_payload(proof.proof_payload)
            assert verification["valid"] is True
            assert verification["proof_hash"] == proof.proof_hash

        async with database.session() as session:
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(ClearingProof)
                    .where(ClearingProof.id == proof.id)
                    .values(proof_hash="sha256:" + "0" * 64)
                )
                await session.commit()
            await session.rollback()

        async with database.session() as session:
            obligation_id = await session.scalar(
                select(ClearingEntry.obligation_id)
                .where(ClearingEntry.cycle_id == cycle.id)
                .limit(1)
            )
            assert obligation_id is not None
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(Obligation)
                    .where(Obligation.id == obligation_id)
                    .values(quantity_cleared=-1)
                )
                await session.commit()
            await session.rollback()
    finally:
        await database.dispose()
