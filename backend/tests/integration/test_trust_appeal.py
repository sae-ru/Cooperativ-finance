from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from cooperative_clearing.api.dependencies import get_principal
from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.modules.trust.infrastructure.models import (
    Appeal,
    ArbitrationDecision,
    ProtectiveMeasure,
    RehabilitationPlan,
    ReputationEvent,
    Sanction,
    TrustCase,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database


def principal(login: str, role: RoleCode | None, member_key: str) -> Principal:
    roles = (
        (
            RoleGrant(
                stable_id(
                    "bootstrap-role" if role == RoleCode.AUDITOR else "demo-role",
                    f"{login}:{role.value}",
                ),
                role,
                None,
            ),
        )
        if role is not None
        else ()
    )
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=uuid4(),
        login=login,
        member_id=stable_id("member", member_key),
        must_change_password=False,
        roles=roles,
    )


async def seeded_trust_database(prefix: str) -> tuple[Settings, Database]:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"{prefix}-{suffix}",
        blob_root=Path(f"/tmp/{prefix}-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    return settings, Database.from_settings(settings)


@pytest.mark.integration
async def test_overturned_demo_appeal_corrects_consequences_without_erasing_history() -> None:
    _, database = await seeded_trust_database("trust-appeal")
    try:
        async with database.session() as session:
            case = (
                await session.execute(
                    select(TrustCase).where(TrustCase.case_reference == "DEMO-TRUST-APPEAL-001")
                )
            ).scalar_one()
            appeal = (
                await session.execute(select(Appeal).where(Appeal.case_id == case.id))
            ).scalar_one()
            measure = (
                await session.execute(
                    select(ProtectiveMeasure).where(ProtectiveMeasure.case_id == case.id)
                )
            ).scalar_one()
            sanction = (
                await session.execute(select(Sanction).where(Sanction.case_id == case.id))
            ).scalar_one()
            plan = (
                await session.execute(
                    select(RehabilitationPlan).where(RehabilitationPlan.case_id == case.id)
                )
            ).scalar_one()
            events = list(
                (
                    await session.execute(
                        select(ReputationEvent)
                        .where(ReputationEvent.case_id == case.id)
                        .order_by(ReputationEvent.created_at, ReputationEvent.id)
                    )
                ).scalars()
            )
            decisions = list(
                (
                    await session.execute(
                        select(ArbitrationDecision)
                        .where(ArbitrationDecision.case_id == case.id)
                        .order_by(
                            ArbitrationDecision.stage,
                            ArbitrationDecision.decision_round,
                        )
                    )
                ).scalars()
            )
            aggregate_ids = {
                case.id,
                appeal.id,
                measure.id,
                sanction.id,
                plan.id,
                *(item.id for item in events),
                *(item.id for item in decisions),
            }
            signed_events = list(
                (
                    await session.execute(
                        select(SignedEvent).where(
                            SignedEvent.aggregate_id.in_(aggregate_ids)
                        )
                    )
                ).scalars()
            )

        assert case.status == "CLOSED" and case.version == 7
        assert appeal.status == "DECIDED" and appeal.outcome == "OVERTURNED"
        assert measure.status == "REVOKED"
        assert sanction.status == "REVOKED"
        assert plan.status == "CANCELLED"
        by_class = {item.classification: item for item in events}
        assert len(events) == 2
        assert by_class["BREACH"].status == "DISPUTED"
        assert by_class["CORRECTION"].status == "ACTIVE"
        assert by_class["CORRECTION"].corrects_event_id == by_class["BREACH"].id
        assert [(item.stage, item.outcome) for item in decisions] == [
            ("APPEAL", "OVERTURNED"),
            ("ORIGINAL", "SUBSTANTIATED"),
        ]
        assurance_by_type = {
            item.event_type: item.payload["_command_assurance"]
            for item in signed_events
            if "_command_assurance" in item.payload
        }
        expected_types = {
            "disputes.dispute_opened",
            "disputes.decision_issued",
            "sanctions.protective_measure_imposed",
            "sanctions.protective_measure_revoked",
            "sanctions.sanction_proposed",
            "sanctions.sanction_revoked",
            "appeals.appeal_submitted",
            "appeals.appeal_decided",
            "reputation.event_recorded",
            "reputation.event_corrected",
            "rehabilitation.plan_created",
            "rehabilitation.plan_cancelled",
        }
        if assurance_by_type:
            assert expected_types <= assurance_by_type.keys()
            assert all(
                assurance["format"] == "critical-command-assurance-v2"
                for assurance in assurance_by_type.values()
            )
            assert assurance_by_type["sanctions.protective_measure_imposed"]["exposure"][
                "effect"
            ] == "HOLD"
            assert assurance_by_type["reputation.event_corrected"]["exposure"][
                "category"
            ] == "REPUTATION"
            for event_type in expected_types:
                assert str(case.subject_member_id) in {
                    party["reference"]
                    for party in assurance_by_type[event_type]["next_responsible"]
                }
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_decisions_and_reputation_facts_are_database_immutable() -> None:
    _, database = await seeded_trust_database("trust-immutable")
    try:
        async with database.session() as session:
            decision_id = await session.scalar(select(ArbitrationDecision.id).limit(1))
            assert decision_id is not None
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(ArbitrationDecision)
                    .where(ArbitrationDecision.id == decision_id)
                    .values(reasoning="rewritten history")
                )
            await session.rollback()

        async with database.session() as session:
            event_id = await session.scalar(select(ReputationEvent.id).limit(1))
            assert event_id is not None
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(ReputationEvent).where(ReputationEvent.id == event_id).values(severity=5)
                )
            await session.rollback()
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_trust_api_limits_participants_and_exposes_role_workspaces() -> None:
    settings, database = await seeded_trust_database("trust-api")
    auditor = principal("auditor", RoleCode.AUDITOR, "demo-member-pavel")
    arbitrator = principal("demo-arbitrator", RoleCode.ARBITRATOR, "demo-member-nina")
    participant = principal("registrar", None, "demo-member-anna")
    unrelated = principal("unrelated", None, "demo-member-boris")
    anna_id = stable_id("member", "demo-member-anna")
    try:
        async with database.session() as session:
            case = (
                await session.execute(
                    select(TrustCase).where(TrustCase.case_reference == "DEMO-TRUST-APPEAL-001")
                )
            ).scalar_one()

        app = create_app(settings, manage_runtime=False)
        app.state.database = database

        async def current() -> Principal:
            return auditor

        app.dependency_overrides[get_principal] = current
        with TestClient(app) as client:
            workspace = client.get("/api/v1/trust/workspaces/auditor")
            profile = client.get(f"/api/v1/trust/reputation/profiles/{anna_id}")
            assert workspace.status_code == 200
            assert len(workspace.json()["data"]["disputed_reputation_events"]) == 1
            assert profile.status_code == 200
            context = profile.json()["data"]["contexts"][0]
            assert context["context"] == "OBLIGATION"
            assert context["confirmed_breaches"] == 0
            assert context["corrections"] == 1

        async def as_arbitrator() -> Principal:
            return arbitrator

        app.dependency_overrides[get_principal] = as_arbitrator
        with TestClient(app) as client:
            workspace = client.get("/api/v1/trust/workspaces/arbitrator")
            assert workspace.status_code == 200
            assert workspace.json()["data"]["submitted_appeals"] == []

        async def as_participant() -> Principal:
            return participant

        app.dependency_overrides[get_principal] = as_participant
        with TestClient(app) as client:
            cases = client.get("/api/v1/trust/cases")
            own_case = client.get(f"/api/v1/trust/cases/{case.id}")
            own_profile = client.get(f"/api/v1/trust/reputation/profiles/{anna_id}")
            forbidden_workspace = client.get("/api/v1/trust/workspaces/arbitrator")
            visible_case_references = {
                item["case_reference"] for item in cases.json()["data"]
            }
            assert cases.status_code == 200
            assert visible_case_references == {
                "DEMO-TRUST-APPEAL-001",
                "DEMO-COMPENSATION-APPEAL-001",
            }
            assert own_case.status_code == 200
            assert own_profile.status_code == 200
            assert forbidden_workspace.status_code == 403

        async def as_unrelated() -> Principal:
            return unrelated

        app.dependency_overrides[get_principal] = as_unrelated
        with TestClient(app) as client:
            assert client.get("/api/v1/trust/cases").json()["data"] == []
            assert client.get(f"/api/v1/trust/cases/{case.id}").status_code == 404
            assert client.get(f"/api/v1/trust/reputation/profiles/{anna_id}").status_code == 404
    finally:
        await database.dispose()
