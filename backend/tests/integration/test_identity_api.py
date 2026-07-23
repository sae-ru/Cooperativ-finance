from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from cooperative_clearing.main import create_app
from cooperative_clearing.modules.identity.infrastructure.models import (
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import PasswordService
from cooperative_clearing.shared.infrastructure.database import Database


@pytest.mark.integration
async def test_authenticated_admin_api_flow() -> None:
    settings = Settings(service_name="identity-api-integration")
    database = Database.from_settings(settings)
    password = "integration-api-password"
    login = f"api-security-{uuid4()}"
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
                    role_code="SECURITY_ADMIN",
                    cooperative_id=None,
                    status="ACTIVE",
                    granted_by_user_id=None,
                    approved_by_user_id=None,
                )
            )
            await session.commit()
    finally:
        await database.dispose()

    with TestClient(create_app(settings)) as client:
        anonymous = client.get("/api/v1/admin/overview")
        assert anonymous.status_code == 401

        login_response = client.post(
            "/api/v1/auth/login", json={"login": login, "password": password}
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}
        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["data"]["login"] == login

        csrf_token = client.cookies.get("coop_csrf")
        assert csrf_token
        refresh = client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf_token})
        assert refresh.status_code == 200
        rotated_access = refresh.json()["data"]["access_token"]
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
        headers = {"Authorization": f"Bearer {rotated_access}"}

        assert client.get("/api/v1/admin/overview", headers=headers).status_code == 200
        assert client.get("/api/v1/admin/cooperatives", headers=headers).status_code == 200

        cooperative_key = str(uuid4())
        cooperative_code = f"api-{uuid4().hex[:12]}"
        created = client.post(
            "/api/v1/admin/cooperatives",
            headers={**headers, "Idempotency-Key": cooperative_key},
            json={"code": cooperative_code, "name": "API integration cooperative"},
        )
        assert created.status_code == 201
        replay = client.post(
            "/api/v1/admin/cooperatives",
            headers={**headers, "Idempotency-Key": cooperative_key},
            json={"code": cooperative_code, "name": "API integration cooperative"},
        )
        assert replay.status_code == 201
        assert replay.json()["data"]["replayed"] is True

        target_login = f"api-target-{uuid4()}"
        created_user = client.post(
            "/api/v1/admin/users",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "login": target_login,
                "temporary_password": "temporary-api-password",
                "member_id": None,
            },
        )
        assert created_user.status_code == 201
        target_user_id = created_user.json()["data"]["object_id"]
        requested_role = client.post(
            "/api/v1/admin/roles",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"user_id": target_user_id, "role": "AUDITOR", "cooperative_id": None},
        )
        assert requested_role.status_code == 201

        roles = client.get("/api/v1/admin/roles", headers=headers)
        assert roles.status_code == 200
        assert any(
            item["id"] == requested_role.json()["data"]["object_id"]
            and item["status"] == "PENDING_APPROVAL"
            for item in roles.json()["data"]
        )
        assert client.get("/api/v1/admin/users", headers=headers).status_code == 200
        assert client.get("/api/v1/admin/sessions", headers=headers).status_code == 200
        assert client.get("/api/v1/admin/audit", headers=headers).status_code == 200

        logout = client.post("/api/v1/auth/logout", headers=headers)
        assert logout.status_code == 204
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
