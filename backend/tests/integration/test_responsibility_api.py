from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from cooperative_clearing.cli import initialize_node
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import PasswordService
from cooperative_clearing.shared.infrastructure.database import Database


async def create_api_actors(
    settings: Settings,
) -> tuple[str, str, str, str, str, str, str]:
    database = Database.from_settings(settings)
    cooperative_id = uuid4()
    target_member_id = uuid4()
    password = "".join(("responsibility-api-", "password"))
    password_hash = PasswordService().hash(password)
    logins = tuple(f"responsibility-api-{name}-{uuid4()}" for name in ("risk", "audit", "target"))
    try:
        async with database.session() as session:
            session.add(
                Cooperative(
                    id=cooperative_id,
                    code=f"responsibility-{cooperative_id.hex[:12]}",
                    name="Responsibility API cooperative",
                    status="ACTIVE",
                )
            )
            members = [uuid4(), uuid4(), target_member_id]
            for member_id, name in zip(members, ("Risk", "Auditor", "Target"), strict=True):
                session.add(Member(id=member_id, display_name=name, status="ACTIVE"))
            await session.flush()
            users = [uuid4(), uuid4(), uuid4()]
            for user_id, member_id, login in zip(users, members, logins, strict=True):
                session.add(
                    UserAccount(
                        id=user_id,
                        login=login,
                        password_hash=password_hash,
                        member_id=member_id,
                        status="ACTIVE",
                        must_change_password=False,
                    )
                )
            await session.flush()
            roles = (
                (users[0], "RISK_ADMIN", cooperative_id),
                (users[1], "AUDITOR", None),
                (users[2], "DATA_STEWARD", cooperative_id),
            )
            target_role_id = uuid4()
            for index, (user_id, role_code, scope) in enumerate(roles):
                session.add(
                    RoleAssignment(
                        id=target_role_id if index == 2 else uuid4(),
                        user_id=user_id,
                        role_code=role_code,
                        cooperative_id=scope,
                        status="ACTIVE",
                        granted_by_user_id=None,
                        approved_by_user_id=None,
                    )
                )
            await session.commit()
    finally:
        await database.dispose()
    return (
        str(cooperative_id),
        str(target_member_id),
        str(target_role_id),
        password,
        logins[0],
        logins[1],
        logins[2],
    )


def login(client: TestClient, login_value: str, password: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"login": login_value, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


@pytest.mark.integration
async def test_responsibility_api_preview_dual_control_and_node_journal_scope() -> None:
    settings = Settings(service_name="responsibility-api-integration")
    await initialize_node(settings)
    (
        cooperative_id,
        target_member_id,
        target_role_id,
        password,
        risk_login,
        auditor_login,
        target_login,
    ) = await create_api_actors(settings)
    payload = {
        "cooperative_id": cooperative_id,
        "member_id": target_member_id,
        "role_assignment_id": target_role_id,
        "subject_type": "warehouse_zone",
        "subject_id": str(uuid4()),
        "scope": "Custody and discrepancy reporting",
        "max_exposure": "250.0000",
        "exposure_unit": "SHARE_UNIT",
        "valid_until": None,
    }

    with TestClient(create_app(settings)) as client:
        risk_headers = login(client, risk_login, password)
        cooperatives = client.get("/api/v1/admin/cooperatives", headers=risk_headers)
        assert cooperatives.status_code == 200
        assert [item["id"] for item in cooperatives.json()["data"]] == [cooperative_id]
        candidates = client.get(
            f"/api/v1/responsibility/candidates?cooperative_id={cooperative_id}",
            headers=risk_headers,
        )
        assert candidates.status_code == 200
        assert target_role_id in {item["role_assignment_id"] for item in candidates.json()["data"]}

        preview = client.post("/api/v1/responsibility/preview", headers=risk_headers, json=payload)
        assert preview.status_code == 200
        summary_hash = preview.json()["data"]["summary_hash"]

        missing_preview = client.post(
            "/api/v1/responsibility/assignments",
            headers={**risk_headers, "Idempotency-Key": str(uuid4())},
            json=payload,
        )
        assert missing_preview.status_code == 422
        assert missing_preview.json()["error"]["code"] == "CANONICAL_PREVIEW_REQUIRED"

        changed = client.post(
            "/api/v1/responsibility/assignments",
            headers={**risk_headers, "Idempotency-Key": str(uuid4())},
            json={**payload, "expected_summary_hash": f"sha256:{'0' * 64}"},
        )
        assert changed.status_code == 409
        assert changed.json()["error"]["code"] == "CANONICAL_SUMMARY_CHANGED"

        proposed = client.post(
            "/api/v1/responsibility/assignments",
            headers={**risk_headers, "Idempotency-Key": str(uuid4())},
            json={**payload, "expected_summary_hash": summary_hash},
        )
        assert proposed.status_code == 201
        assignment_id = proposed.json()["data"]["object_id"]
        assert client.get("/api/v1/journal/events", headers=risk_headers).status_code == 403

        auditor_headers = login(client, auditor_login, password)
        decision = client.post(
            f"/api/v1/responsibility/assignments/{assignment_id}/decision",
            headers={**auditor_headers, "Idempotency-Key": str(uuid4())},
            json={"decision": "APPROVE", "reason_code": "INDEPENDENT_REVIEW"},
        )
        assert decision.status_code == 200

        target_headers = login(client, target_login, password)
        listed = client.get("/api/v1/responsibility/assignments", headers=target_headers)
        assert listed.status_code == 200
        target_assignment = next(
            item for item in listed.json()["data"] if item["id"] == assignment_id
        )
        accepted = client.post(
            f"/api/v1/responsibility/assignments/{assignment_id}/accept",
            headers={**target_headers, "Idempotency-Key": str(uuid4())},
            json={"expected_version": target_assignment["version"]},
        )
        assert accepted.status_code == 200

        integrity = client.get("/api/v1/journal/integrity", headers=auditor_headers)
        assert integrity.status_code == 200
        assert integrity.json()["data"]["ok"] is True
        events = client.get("/api/v1/journal/events", headers=auditor_headers)
        assert events.status_code == 200
        assignment_events = [
            item for item in events.json()["data"] if item["aggregate_id"] == assignment_id
        ]
        assert [item["aggregate_version"] for item in reversed(assignment_events)] == [1, 2, 3]
