from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from cooperative_clearing.main import create_app
from cooperative_clearing.modules.identity.infrastructure.models import (
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.metrics import request_metrics
from cooperative_clearing.shared.core.security import PasswordService
from cooperative_clearing.shared.infrastructure.database import Database


@pytest.mark.integration
async def test_protected_operational_snapshot_and_metrics() -> None:
    settings = Settings(service_name="operations-integration")
    database = Database.from_settings(settings)
    password = "operations-integration-password"
    login = f"operations-auditor-{uuid4()}"
    user_id = uuid4()
    try:
        async with database.session() as session:
            session.add(
                UserAccount(
                    id=user_id,
                    login=login,
                    password_hash=PasswordService().hash(password),
                    member_id=None,
                    status="ACTIVE",
                    must_change_password=False,
                )
            )
            await session.flush()
            session.add(
                RoleAssignment(
                    id=uuid4(),
                    user_id=user_id,
                    role_code="AUDITOR",
                    cooperative_id=None,
                    status="ACTIVE",
                    granted_by_user_id=None,
                    approved_by_user_id=None,
                )
            )
            await session.commit()
    finally:
        await database.dispose()

    request_metrics.reset()
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/operations/snapshot").status_code == 401
        login_response = client.post(
            "/api/v1/auth/login",
            json={"login": login, "password": password},
        )
        assert login_response.status_code == 200
        headers = {"Authorization": f"Bearer {login_response.json()['data']['access_token']}"}

        snapshot = client.get("/api/v1/operations/snapshot", headers=headers)
        assert snapshot.status_code == 200
        data = snapshot.json()["data"]
        assert data["schema_revision"] == "0018_inter_node_clearing"
        assert data["signed_events"] >= 0
        assert data["active_sessions"] >= 1
        assert data["outbox_quarantined"] >= 0

        metrics = client.get("/api/v1/operations/metrics", headers=headers)
        assert metrics.status_code == 200
        assert metrics.headers["content-type"].startswith("text/plain")
        assert (
            f'coop_build_info{{node="{settings.node_code}",release="{settings.release}"}} 1'
            in metrics.text
        )
        assert 'route="/api/v1/operations/snapshot"' in metrics.text
        assert 'coop_operational_records{kind="active_sessions"}' in metrics.text
