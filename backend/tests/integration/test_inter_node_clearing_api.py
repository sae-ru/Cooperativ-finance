"""HTTP contract for accountable inter-node clearing administration."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from cooperative_clearing.api.dependencies import get_principal
from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.federation.application.demo import DEMO_NODE_CODE
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database


@pytest.mark.integration
async def test_federated_clearing_api_is_role_scoped_and_idempotent() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"federated-clearing-api-{suffix}",
        blob_root=Path(f"/tmp/federated-clearing-api-{suffix}"),
        demo_data_enabled=True,
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    finalizer = _principal(settings, "auditor", "demo-member-pavel", RoleCode.CLEARING_FINALIZER)
    operator = _principal(settings, "registrar", "demo-member-anna", RoleCode.CLEARING_OPERATOR)
    outsider = Principal(
        user_id=stable_id("bootstrap-user", "member"),
        session_id=uuid4(),
        login="member",
        member_id=stable_id("member", "demo-member-boris"),
        must_change_password=False,
        roles=(),
    )
    app = create_app(settings, manage_runtime=False)
    app.state.database = database

    async def as_finalizer() -> Principal:
        return finalizer

    async def as_operator() -> Principal:
        return operator

    async def as_outsider() -> Principal:
        return outsider

    try:
        app.dependency_overrides[get_principal] = as_finalizer
        unit_code = f"U{suffix.upper()}"
        policy_payload = {
            "policy_code": f"API-{suffix}",
            "policy_version": 1,
            "valuation_unit": unit_code,
            "decimal_scale": 2,
            "rounding_mode": "DOWN",
            "minimum_operation": "0.01",
            "max_iterations": 10000,
            "max_cycle_length": 8,
            "prepare_ttl_seconds": 900,
        }
        with TestClient(app) as client:
            created_policy = client.post(
                "/api/v1/federated-clearing/policies",
                headers={"Idempotency-Key": f"policy-{suffix}"},
                json=policy_payload,
            )
            replayed_policy = client.post(
                "/api/v1/federated-clearing/policies",
                headers={"Idempotency-Key": f"policy-{suffix}"},
                json=policy_payload,
            )

        assert created_policy.status_code == 201
        assert replayed_policy.status_code == 201
        policy_result = created_policy.json()["data"]
        replay_result = replayed_policy.json()["data"]
        assert replay_result["object_id"] == policy_result["object_id"]
        assert replay_result["event_id"] == policy_result["event_id"]
        assert replay_result["replayed"] is True

        app.dependency_overrides[get_principal] = as_operator
        now = datetime.now(UTC).replace(microsecond=0)
        peer_code = DEMO_NODE_CODE.lower()
        obligation_payload = {
            "debtor_node_code": settings.node_code,
            "creditor_node_code": peer_code,
            "unit_code": unit_code,
            "amount": "12.50",
            "source_reference": f"API-SUPPLY-{suffix}",
            "source_event_hash": payload_hash({"api_test": suffix}),
            "liquidity_class": "STANDARD",
        }
        cycle_payload = {
            "cycle_code": f"API-CYCLE-{suffix}",
            "policy_id": policy_result["object_id"],
            "period_start": (now - timedelta(days=1)).isoformat(),
            "period_end": (now + timedelta(minutes=1)).isoformat(),
            "participant_node_codes": [settings.node_code, peer_code],
        }
        with TestClient(app) as client:
            obligation = client.post(
                "/api/v1/federated-clearing/obligations",
                headers={"Idempotency-Key": f"obligation-{suffix}"},
                json=obligation_payload,
            )
            cycle = client.post(
                "/api/v1/federated-clearing/cycles",
                headers={"Idempotency-Key": f"cycle-{suffix}"},
                json=cycle_payload,
            )
            policies = client.get("/api/v1/federated-clearing/policies")
            obligations = client.get("/api/v1/federated-clearing/obligations")
            cycles = client.get("/api/v1/federated-clearing/cycles")

        assert obligation.status_code == 201
        assert cycle.status_code == 201
        cycle_id = cycle.json()["data"]["cycle_id"]
        assert policy_result["object_id"] in {item["id"] for item in policies.json()["data"]}
        assert obligation.json()["data"]["object_id"] in {
            item["id"] for item in obligations.json()["data"]
        }
        assert cycle_id in {item["id"] for item in cycles.json()["data"]}

        with TestClient(app) as client:
            evidence = client.get(f"/api/v1/federated-clearing/cycles/{cycle_id}")
        assert evidence.status_code == 200
        evidence_data = evidence.json()["data"]
        assert evidence_data["cycle"]["status"] == "DRAFT"
        assert evidence_data["snapshots"] == []
        assert evidence_data["certificate"] is None

        app.dependency_overrides[get_principal] = as_outsider
        with TestClient(app) as client:
            forbidden = client.get("/api/v1/federated-clearing/cycles")
            forbidden_command = client.post(
                "/api/v1/federated-clearing/obligations",
                headers={"Idempotency-Key": f"forbidden-{suffix}"},
                json=obligation_payload,
            )
        assert forbidden.status_code == 403
        assert forbidden_command.status_code == 403
    finally:
        await database.dispose()


def _principal(settings: Settings, login: str, member: str, role: RoleCode) -> Principal:
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=uuid4(),
        login=login,
        member_id=stable_id("member", member),
        must_change_password=False,
        roles=(
            RoleGrant(
                stable_id("demo-role", f"{login}:{role.value}"),
                role,
                stable_id("cooperative", settings.node_code),
            ),
        ),
    )
