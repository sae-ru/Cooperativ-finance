import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from cooperative_clearing.api.dependencies import get_principal
from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.exchange.infrastructure.models import Obligation
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.modules.risk.infrastructure.models import (
    ExposureCommitment,
    ShareContribution,
)
from cooperative_clearing.modules.solidarity.application.demo import seed_demo_solidarity
from cooperative_clearing.modules.solidarity.infrastructure.models import (
    AidAllocation,
    AidApplication,
    AidCampaign,
    AidDelivery,
    AllocationApproval,
    CampaignReport,
    Contribution,
    Pledge,
    SolidarityComplaint,
)
from cooperative_clearing.modules.trust.infrastructure.models import ReputationEvent
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database


def principal(login: str, member_key: str, *roles: tuple[RoleCode, str | None]) -> Principal:
    return Principal(
        user_id=(
            stable_id("demo-user", "nina-arbitrator")
            if login == "demo-arbitrator"
            else stable_id("bootstrap-user", login)
        ),
        session_id=uuid4(),
        login=login,
        member_id=stable_id("member", member_key),
        must_change_password=False,
        roles=tuple(
            RoleGrant(
                stable_id(
                    "bootstrap-role" if role == RoleCode.AUDITOR else "demo-role",
                    f"{login}:{role.value}",
                ),
                role,
                cooperative_id,
            )
            for role, cooperative_id in roles
        ),
    )


async def seeded_solidarity_database(prefix: str) -> tuple[Settings, Database]:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"{prefix}-{suffix}",
        blob_root=Path(f"/tmp/{prefix}-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    return settings, Database.from_settings(settings)


@pytest.mark.integration
async def test_demo_solidarity_flow_is_reconciled_idempotent_and_creates_no_debt_or_score() -> None:
    settings, database = await seeded_solidarity_database("solidarity-flow")
    try:
        async with database.session() as session:
            campaign = (
                await session.execute(
                    select(AidCampaign).where(AidCampaign.campaign_code == "DEMO-AID-001")
                )
            ).scalar_one()
            pledge = (
                await session.execute(select(Pledge).where(Pledge.campaign_id == campaign.id))
            ).scalar_one()
            contribution = (
                await session.execute(
                    select(Contribution).where(Contribution.campaign_id == campaign.id)
                )
            ).scalar_one()
            application = (
                await session.execute(
                    select(AidApplication).where(AidApplication.campaign_id == campaign.id)
                )
            ).scalar_one()
            allocation = (
                await session.execute(
                    select(AidAllocation).where(AidAllocation.campaign_id == campaign.id)
                )
            ).scalar_one()
            complaint = (
                await session.execute(
                    select(SolidarityComplaint).where(
                        SolidarityComplaint.campaign_id == campaign.id
                    )
                )
            ).scalar_one()
            delivery = (
                await session.execute(
                    select(AidDelivery).where(AidDelivery.allocation_id == allocation.id)
                )
            ).scalar_one()
            report = (
                await session.execute(
                    select(CampaignReport).where(CampaignReport.campaign_id == campaign.id)
                )
            ).scalar_one()
            solidarity_events = set(
                (
                    await session.execute(
                        select(SignedEvent.event_id).where(
                            SignedEvent.event_type.like("solidarity.%")
                        )
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
            before_counts = (
                int(await session.scalar(select(func.count()).select_from(CampaignReport)) or 0),
                int(
                    await session.scalar(
                        select(func.count())
                        .select_from(SignedEvent)
                        .where(SignedEvent.event_type.like("solidarity.%"))
                    )
                    or 0
                ),
            )

        assert campaign.status == "CLOSED" and campaign.version == 3
        assert pledge.status == "FULFILLED"
        assert contribution.status == "VERIFIED"
        assert application.status == "CLOSED"
        assert allocation.status == "DELIVERED" and allocation.version == 5
        assert complaint.status == "RESOLVED"
        assert delivery.attestor_kind == "RECIPIENT"
        assert report.contribution_count == 1
        assert report.allocation_count == 1
        assert report.delivery_count == 1
        assert report.complaint_count == 1
        assert report.bucket_totals == [
            {
                "contribution_form": "GOODS",
                "unit_code": "KG",
                "verified": "10.000000000000",
                "delivered": "10.000000000000",
                "residue": "0E-12",
            }
        ]
        assert str(stable_id("member", "demo-member-nina")) not in json.dumps(report.bucket_totals)
        assert solidarity_events
        assert solidarity_events.isdisjoint(obligation_events)
        assert solidarity_events.isdisjoint(share_events)
        assert solidarity_events.isdisjoint(commitment_events)
        assert {str(value) for value in solidarity_events}.isdisjoint(reputation_sources)

        async with database.session() as session:
            await seed_demo_solidarity(session, settings)
            await session.commit()
        async with database.session() as session:
            after_counts = (
                int(await session.scalar(select(func.count()).select_from(CampaignReport)) or 0),
                int(
                    await session.scalar(
                        select(func.count())
                        .select_from(SignedEvent)
                        .where(SignedEvent.event_type.like("solidarity.%"))
                    )
                    or 0
                ),
            )
        assert after_counts == before_counts
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_solidarity_proofs_are_database_immutable() -> None:
    _, database = await seeded_solidarity_database("solidarity-immutable")
    try:
        for model, values in (
            (AllocationApproval, {"conflict_statement": "rewritten"}),
            (AidDelivery, {"acknowledgement": "rewritten"}),
            (CampaignReport, {"contribution_count": 99}),
        ):
            async with database.session() as session:
                row_id = await session.scalar(select(model.id).limit(1))
                assert row_id is not None
                with pytest.raises(DBAPIError):
                    await session.execute(update(model).where(model.id == row_id).values(**values))
                await session.rollback()
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_solidarity_api_preserves_recipient_privacy_and_role_workspaces() -> None:
    settings, database = await seeded_solidarity_database("solidarity-api")
    cooperative_id = stable_id("cooperative", settings.node_code)
    controller = principal(
        "auditor",
        "demo-member-pavel",
        (RoleCode.AUDITOR, None),
        (RoleCode.SOLIDARITY_CONTROLLER, cooperative_id),
    )
    recipient = principal("demo-arbitrator", "demo-member-nina", (RoleCode.ARBITRATOR, None))
    unrelated = principal("unrelated", "demo-member-boris")
    try:
        async with database.session() as session:
            campaign_id = await session.scalar(
                select(AidCampaign.id).where(AidCampaign.campaign_code == "DEMO-AID-001")
            )
        assert campaign_id is not None
        campaign_params = {"campaign_id": str(campaign_id)}
        app = create_app(settings, manage_runtime=False)
        app.state.database = database

        async def as_controller() -> Principal:
            return controller

        app.dependency_overrides[get_principal] = as_controller
        with TestClient(app) as client:
            workspace = client.get("/api/v1/solidarity/workspaces/controller")
            applications = client.get("/api/v1/solidarity/applications", params=campaign_params)
            reports = client.get("/api/v1/solidarity/reports", params=campaign_params)
            assert workspace.status_code == 200
            assert applications.status_code == 200 and len(applications.json()["data"]) == 1
            assert reports.status_code == 200 and len(reports.json()["data"]) == 1
            assert "private_evidence_refs" not in applications.json()["data"][0]
            assert "recipient_member_id" not in reports.json()["data"][0]

        async def as_recipient() -> Principal:
            return recipient

        app.dependency_overrides[get_principal] = as_recipient
        with TestClient(app) as client:
            applications = client.get("/api/v1/solidarity/applications", params=campaign_params)
            allocations = client.get("/api/v1/solidarity/allocations", params=campaign_params)
            deliveries = client.get("/api/v1/solidarity/deliveries", params=campaign_params)
            contributions = client.get("/api/v1/solidarity/contributions", params=campaign_params)
            forbidden_workspace = client.get("/api/v1/solidarity/workspaces/controller")
            assert len(applications.json()["data"]) == 1
            assert len(allocations.json()["data"]) == 1
            assert len(deliveries.json()["data"]) == 1
            assert contributions.json()["data"] == []
            assert forbidden_workspace.status_code == 403

        async def as_unrelated() -> Principal:
            return unrelated

        app.dependency_overrides[get_principal] = as_unrelated
        with TestClient(app) as client:
            assert (
                client.get("/api/v1/solidarity/applications", params=campaign_params).json()["data"]
                == []
            )
            assert (
                client.get("/api/v1/solidarity/allocations", params=campaign_params).json()["data"]
                == []
            )
            assert (
                client.get("/api/v1/solidarity/complaints", params=campaign_params).json()["data"]
                == []
            )
    finally:
        await database.dispose()
