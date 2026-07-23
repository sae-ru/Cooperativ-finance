from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cooperative_clearing.api.dependencies import get_principal
from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.clearing.infrastructure.models import ClearingCycle
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database


@pytest.mark.integration
async def test_clearing_api_exposes_proof_and_limits_participant_visibility() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"clearing-api-{suffix}",
        blob_root=Path(f"/tmp/clearing-api-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    cooperative_id = stable_id("cooperative", settings.node_code)
    anna_id = stable_id("member", "demo-member-anna")
    auditor = Principal(
        user_id=stable_id("bootstrap-user", "auditor"),
        session_id=uuid4(),
        login="auditor",
        member_id=stable_id("member", "demo-member-pavel"),
        must_change_password=False,
        roles=(
            RoleGrant(
                stable_id("bootstrap-role", "auditor:AUDITOR"),
                RoleCode.AUDITOR,
                None,
            ),
            RoleGrant(
                stable_id("demo-role", "auditor:CLEARING_FINALIZER"),
                RoleCode.CLEARING_FINALIZER,
                cooperative_id,
            ),
        ),
    )
    participant = Principal(
        user_id=stable_id("bootstrap-user", "registrar"),
        session_id=uuid4(),
        login="participant",
        member_id=anna_id,
        must_change_password=False,
        roles=(),
    )
    try:
        async with database.session() as session:
            cycle = (
                await session.execute(
                    select(ClearingCycle).where(ClearingCycle.cycle_code == "DEMO-WEEK-2035-01")
                )
            ).scalar_one()

        app = create_app(settings, manage_runtime=False)
        app.state.database = database

        async def as_auditor() -> Principal:
            return auditor

        app.dependency_overrides[get_principal] = as_auditor
        with TestClient(app) as client:
            policies = client.get("/api/v1/clearing/policies")
            cycles = client.get("/api/v1/clearing/cycles")
            snapshot = client.get(f"/api/v1/clearing/cycles/{cycle.id}/input")
            entries = client.get(f"/api/v1/clearing/cycles/{cycle.id}/entries")
            positions = client.get(f"/api/v1/clearing/cycles/{cycle.id}/positions")
            approvals = client.get(f"/api/v1/clearing/cycles/{cycle.id}/approvals")
            disputes = client.get(f"/api/v1/clearing/cycles/{cycle.id}/disputes")
            proof = client.get(f"/api/v1/clearing/cycles/{cycle.id}/proof")
            statements = client.get(f"/api/v1/clearing/cycles/{cycle.id}/statements/{anna_id}")
            accounting = client.get(f"/api/v1/clearing/cycles/{cycle.id}/accounting-export")

            assert policies.status_code == 200 and policies.json()["data"]
            assert cycles.status_code == 200
            assert str(cycle.id) in {item["id"] for item in cycles.json()["data"]}
            assert snapshot.status_code == 200
            assert snapshot.json()["data"]["input_hash"] == cycle.input_hash
            assert entries.status_code == 200 and len(entries.json()["data"]) == 2
            assert positions.status_code == 200 and len(positions.json()["data"]) == 2
            assert approvals.status_code == 200 and len(approvals.json()["data"]) == 1
            assert disputes.status_code == 200 and disputes.json()["data"] == []
            assert proof.status_code == 200
            assert statements.status_code == 200 and len(statements.json()["data"]) == 1
            assert accounting.status_code == 200

            verification = client.post(
                "/api/v1/clearing/proofs/verify",
                json={"proof": proof.json()["data"]["proof_payload"]},
            )
            assert verification.status_code == 200
            assert verification.json()["data"]["valid"] is True
            assert verification.json()["data"]["proof_hash"] == proof.json()["data"]["proof_hash"]

        async def as_participant() -> Principal:
            return participant

        app.dependency_overrides[get_principal] = as_participant
        with TestClient(app) as client:
            cycles = client.get("/api/v1/clearing/cycles")
            entries = client.get(f"/api/v1/clearing/cycles/{cycle.id}/entries")
            positions = client.get(f"/api/v1/clearing/cycles/{cycle.id}/positions")
            statements = client.get(f"/api/v1/clearing/cycles/{cycle.id}/statements/{anna_id}")
            hidden_input = client.get(f"/api/v1/clearing/cycles/{cycle.id}/input")
            hidden_proof = client.get(f"/api/v1/clearing/cycles/{cycle.id}/proof")
            hidden_accounting = client.get(f"/api/v1/clearing/cycles/{cycle.id}/accounting-export")

            assert cycles.status_code == 200
            assert str(cycle.id) in {item["id"] for item in cycles.json()["data"]}
            assert entries.status_code == 200 and len(entries.json()["data"]) == 2
            assert positions.status_code == 200 and len(positions.json()["data"]) == 1
            assert statements.status_code == 200 and len(statements.json()["data"]) == 1
            assert hidden_input.status_code == 403
            assert hidden_proof.status_code == 403
            assert hidden_accounting.status_code == 403
    finally:
        await database.dispose()
