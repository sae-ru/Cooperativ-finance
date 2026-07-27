from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cooperative_clearing.main import create_app
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    RoleAssignment,
    ServiceClient,
    ServiceClientAccessToken,
    ServiceClientCredential,
    ServiceClientRequest,
    UserAccount,
)
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import PasswordService
from cooperative_clearing.shared.infrastructure.database import Database

PASSWORD = "Service-client-test-2026!"


def login(client: TestClient, login_value: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"login": login_value, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def enable_step_up(client: TestClient, headers: dict[str, str]) -> None:
    enrollment = client.post(
        "/api/v1/auth/totp/enrollment",
        headers=headers,
        json={"current_password": PASSWORD},
    )
    assert enrollment.status_code == 201, enrollment.text
    code = pyotp.TOTP(enrollment.json()["data"]["secret"]).now()
    confirmation = client.post(
        "/api/v1/auth/totp/enrollment/confirm",
        headers=headers,
        json={"code": code},
    )
    assert confirmation.status_code == 200, confirmation.text


@pytest.mark.integration
async def test_service_client_dual_control_token_scope_and_revocation() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(service_name=f"service-client-{suffix}")
    database = Database.from_settings(settings)
    cooperative_id = uuid4()
    requester_user_id = uuid4()
    security_user_id = uuid4()
    requester_member_id = uuid4()
    security_member_id = uuid4()
    requester_login = f"service-owner-{suffix}"
    security_login = f"service-security-{suffix}"
    try:
        async with database.session() as session:
            passwords = PasswordService()
            session.add(
                Cooperative(
                    id=cooperative_id,
                    code=f"service-{suffix}",
                    name="Service client cooperative",
                    status="ACTIVE",
                )
            )
            session.add_all(
                [
                    Member(
                        id=requester_member_id,
                        display_name="Service owner",
                        registered_by_cooperative_id=cooperative_id,
                        status="ACTIVE",
                    ),
                    Member(
                        id=security_member_id,
                        display_name="Service security approver",
                        registered_by_cooperative_id=cooperative_id,
                        status="ACTIVE",
                    ),
                    UserAccount(
                        id=requester_user_id,
                        login=requester_login,
                        password_hash=passwords.hash(PASSWORD),
                        member_id=requester_member_id,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                    UserAccount(
                        id=security_user_id,
                        login=security_login,
                        password_hash=passwords.hash(PASSWORD),
                        member_id=security_member_id,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    RoleAssignment(
                        id=uuid4(),
                        user_id=requester_user_id,
                        role_code="COOPERATIVE_ADMIN",
                        cooperative_id=cooperative_id,
                        status="ACTIVE",
                        approved_by_user_id=security_user_id,
                        approved_at=datetime.now(UTC),
                    ),
                    RoleAssignment(
                        id=uuid4(),
                        user_id=security_user_id,
                        role_code="SECURITY_ADMIN",
                        cooperative_id=None,
                        status="ACTIVE",
                        approved_by_user_id=requester_user_id,
                        approved_at=datetime.now(UTC),
                    ),
                ]
            )
            await session.commit()
    finally:
        await database.dispose()

    with TestClient(create_app(settings), client=("127.0.0.1", 45000)) as client:
        requester_headers = login(client, requester_login)
        security_headers = login(client, security_login)
        enable_step_up(client, security_headers)

        request_payload = {
            "owner_cooperative_id": str(cooperative_id),
            "operation": "CREATE",
            "service_client_id": None,
            "expected_client_version": None,
            "reason_code": "ERP_CATALOG_INTEGRATION",
            "config": {
                "display_name": "Farm ERP bridge",
                "technical_contact_name": "Ivan Operator",
                "technical_contact_email": "ops@example.org",
                "scopes": ["catalog:read"],
                "network_allowlist": ["127.0.0.1/32"],
                "rate_limit_per_minute": 20,
                "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            },
        }
        created_request = client.post(
            "/api/v1/admin/service-client-requests",
            headers={**requester_headers, "Idempotency-Key": str(uuid4())},
            json=request_payload,
        )
        assert created_request.status_code == 201, created_request.text
        change_request_id = created_request.json()["data"]["object_id"]

        own_decision = client.post(
            f"/api/v1/admin/service-client-requests/{change_request_id}/decision",
            headers={**requester_headers, "Idempotency-Key": str(uuid4())},
            json={
                "approve": True,
                "reason_code": "SELF_REVIEW",
                "expected_version": 1,
            },
        )
        assert own_decision.status_code == 403

        approved = client.post(
            f"/api/v1/admin/service-client-requests/{change_request_id}/decision",
            headers={**security_headers, "Idempotency-Key": str(uuid4())},
            json={
                "approve": True,
                "reason_code": "INDEPENDENT_SECURITY_REVIEW",
                "expected_version": 1,
            },
        )
        assert approved.status_code == 201, approved.text
        decision = approved.json()["data"]
        service_client_id = decision["service_client_id"]
        client_code = decision["client_code"]
        client_secret = decision["credential_secret"]
        assert client_code.startswith("svc_")
        assert client_secret.startswith("ccs_")
        assert client_secret not in approved.request.content.decode("utf-8")

        listed = client.get("/api/v1/admin/service-clients", headers=requester_headers)
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()["data"]] == [service_client_id]

        token_response = client.post(
            "/api/v1/service-auth/token",
            json={"client_id": client_code, "client_secret": client_secret},
        )
        assert token_response.status_code == 200, token_response.text
        service_token = token_response.json()["data"]["access_token"]
        service_headers = {"Authorization": f"Bearer {service_token}"}
        context = client.get("/api/v1/service/context", headers=service_headers)
        assert context.status_code == 200, context.text
        assert context.json()["data"]["owner_cooperative_id"] == str(cooperative_id)
        assert context.json()["data"]["scopes"] == ["catalog:read"]

        human_endpoint = client.get("/api/v1/auth/me", headers=service_headers)
        assert human_endpoint.status_code == 401

        service_version = listed.json()["data"][0]["version"]
        revoked = client.post(
            f"/api/v1/admin/service-clients/{service_client_id}/revoke",
            headers={**security_headers, "Idempotency-Key": str(uuid4())},
            json={
                "reason_code": "CREDENTIAL_COMPROMISE",
                "expected_version": service_version,
            },
        )
        assert revoked.status_code == 201, revoked.text
        assert client.get("/api/v1/service/context", headers=service_headers).status_code == 401
        assert client.get("/api/v1/auth/me", headers=requester_headers).status_code == 200
        assert (
            client.post(
                "/api/v1/service-auth/token",
                json={"client_id": client_code, "client_secret": client_secret},
            ).status_code
            == 403
        )

    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            stored_client = await session.get(ServiceClient, service_client_id)
            assert stored_client is not None and stored_client.status == "REVOKED"
            assert not hasattr(stored_client, "client_secret")
            credential = (
                await session.execute(
                    select(ServiceClientCredential).where(
                        ServiceClientCredential.service_client_id == service_client_id
                    )
                )
            ).scalar_one()
            assert credential.status == "REVOKED"
            assert credential.secret_hash != client_secret
            request_row = await session.get(ServiceClientRequest, change_request_id)
            assert request_row is not None and request_row.status == "APPROVED"
            tokens = list(
                (
                    await session.execute(
                        select(ServiceClientAccessToken).where(
                            ServiceClientAccessToken.service_client_id == service_client_id
                        )
                    )
                ).scalars()
            )
            assert tokens and all(item.status == "REVOKED" for item in tokens)
            event_types = set(
                (
                    await session.execute(
                        select(SignedEvent.event_type).where(
                            SignedEvent.aggregate_id.in_([service_client_id, change_request_id])
                        )
                    )
                ).scalars()
            )
            assert {
                "identity.service_client_change_requested",
                "identity.service_client_registered",
                "identity.service_client_revoked",
            }.issubset(event_types)
    finally:
        await database.dispose()
