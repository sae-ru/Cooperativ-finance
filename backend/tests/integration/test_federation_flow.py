"""Two-node federation lifecycle, preserved conflicts, and database evidence guards."""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError

from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.modules.federation.application.demo import DEMO_NODE_CODE
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    FederationPaperForm,
    InboxEvent,
    NodeBond,
    NodeExposure,
    NodeResponsibleParty,
    NodeTrustContract,
    OfflineEpoch,
    SyncConflict,
    SyncPackage,
    SyncReceipt,
)
from cooperative_clearing.modules.journal.application.service import verify_journal
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.modules.node.infrastructure.models import NodeProfile
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database


@pytest.mark.integration
async def test_demo_federation_is_idempotent_bounded_and_preserves_both_histories() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"federation-flow-{suffix}",
        blob_root=Path(f"/tmp/federation-flow-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            event_count = int(
                await session.scalar(select(func.count()).select_from(SignedEvent)) or 0
            )
        await seed_demo(settings)
        async with database.session() as session:
            assert (
                int(await session.scalar(select(func.count()).select_from(SignedEvent)) or 0)
                == event_count
            )
            node = (
                await session.execute(
                    select(ExternalNode).where(ExternalNode.node_code == DEMO_NODE_CODE.lower())
                )
            ).scalar_one()
            contract = (
                await session.execute(
                    select(NodeTrustContract).where(NodeTrustContract.node_id == node.id)
                )
            ).scalar_one()
            responsibilities = list(
                (
                    await session.execute(
                        select(NodeResponsibleParty).where(NodeResponsibleParty.node_id == node.id)
                    )
                ).scalars()
            )
            bond = (
                await session.execute(select(NodeBond).where(NodeBond.node_id == node.id))
            ).scalar_one()
            exposure = (
                await session.execute(
                    select(NodeExposure).where(
                        NodeExposure.node_id == node.id,
                        NodeExposure.capability == "TEST_EXCHANGE",
                        NodeExposure.unit == "DEMO",
                    )
                )
            ).scalar_one()
            epoch = (
                await session.execute(
                    select(OfflineEpoch).where(OfflineEpoch.external_node_id == node.id)
                )
            ).scalar_one()
            paper_form = (
                await session.execute(
                    select(FederationPaperForm).where(
                        FederationPaperForm.external_node_id == node.id
                    )
                )
            ).scalar_one()
            packages = list(
                (
                    await session.execute(
                        select(SyncPackage)
                        .where(SyncPackage.peer_node_id == node.id)
                        .order_by(SyncPackage.sequence_first)
                    )
                ).scalars()
            )
            conflict = (
                await session.execute(
                    select(SyncConflict).where(SyncConflict.package_id == packages[1].id)
                )
            ).scalar_one()
            histories = list(
                (
                    await session.execute(
                        select(InboxEvent)
                        .where(InboxEvent.source_node_id == node.id)
                        .order_by(InboxEvent.local_sequence)
                    )
                ).scalars()
            )
            receipts = list(
                (
                    await session.execute(
                        select(SyncReceipt).where(
                            SyncReceipt.package_id.in_([item.id for item in packages])
                        )
                    )
                ).scalars()
            )
            profile = (
                await session.execute(
                    select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
                )
            ).scalar_one()
            integrity = await verify_journal(session, profile.id)

            assert node.status == "ACTIVE"
            assert node.trust_level == "STANDARD"
            assert contract.status == "ACTIVE"
            assert contract.liability_terms["ordinary_member_shares_excluded"] is True
            assert {item.role_code for item in responsibilities} == {
                "OWNER_SIGNATORY",
                "TECHNICAL_CUSTODIAN",
                "SECURITY_ADMINISTRATOR",
                "BUSINESS_OPERATOR",
                "NODE_AUDITOR",
            }
            assert all(item.status == "ACTIVE" for item in responsibilities)
            assert bond.maximum_loss == bond.amount - bond.protected_amount
            assert exposure.current_amount + exposure.reserved_amount <= 100
            assert epoch.status == "OPEN"
            assert paper_form.status == "RECORDED"
            assert paper_form.serial_number == "DEMO-FED-PAPER-001"
            assert paper_form.recorded_by_member_id != paper_form.issued_by_member_id
            assert paper_form.payload_hash is not None
            assert paper_form.evidence_ids
            assert [item.status for item in packages] == ["APPLIED", "APPLIED"]
            assert conflict.status == "RESOLVED"
            assert conflict.decision == "KEEP_LOCAL"
            assert len(histories) == 2
            assert histories[0].aggregate_id == histories[1].aggregate_id
            assert histories[0].aggregate_version == histories[1].aggregate_version == 1
            assert histories[0].event_hash != histories[1].event_hash
            assert [item.status for item in histories] == ["APPLIED", "REJECTED"]
            assert len(receipts) == 2
            assert integrity.ok is True

        async with database.session() as session:
            receipt_id = await session.scalar(select(SyncReceipt.id).limit(1))
            assert receipt_id is not None
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(SyncReceipt)
                    .where(SyncReceipt.id == receipt_id)
                    .values(receipt_hash="sha256:" + "0" * 64)
                )
            await session.rollback()

        async with database.session() as session:
            paper_id = await session.scalar(select(FederationPaperForm.id).limit(1))
            assert paper_id is not None
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(FederationPaperForm)
                    .where(FederationPaperForm.id == paper_id)
                    .values(payload_hash="sha256:" + "0" * 64)
                )
            await session.rollback()

        async with database.session() as session:
            inbox_id = await session.scalar(select(InboxEvent.id).limit(1))
            assert inbox_id is not None
            with pytest.raises(DBAPIError):
                await session.execute(delete(InboxEvent).where(InboxEvent.id == inbox_id))
            await session.rollback()
    finally:
        await database.dispose()
