from uuid import UUID, uuid4

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cooperative_clearing.main import create_app
from cooperative_clearing.modules.audit.infrastructure.models import AuditEntry
from cooperative_clearing.modules.identity.infrastructure.models import (
    AuthenticationFactor,
    Member,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.journal.infrastructure.models import EventSignature, SignedEvent
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import PasswordService
from cooperative_clearing.shared.infrastructure.database import Database

PASSWORD = "".join(("Identity-security-", "2026!"))
TARGET_PASSWORD = "Target-security-2026!"
RECOVERED_PASSWORD = "Recovered-security-2026!"


def auth_headers(client: TestClient, login: str, password: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"login": login, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def enroll_totp(client: TestClient, headers: dict[str, str], password: str) -> tuple[str, str]:
    enrollment = client.post(
        "/api/v1/auth/totp/enrollment",
        headers=headers,
        json={"current_password": password},
    )
    assert enrollment.status_code == 201, enrollment.text
    secret = enrollment.json()["data"]["secret"]
    code = pyotp.TOTP(secret).now()
    confirmation = client.post(
        "/api/v1/auth/totp/enrollment/confirm",
        headers=headers,
        json={"code": code},
    )
    assert confirmation.status_code == 200, confirmation.text
    return secret, code


@pytest.mark.integration
async def test_totp_recovery_and_break_glass_end_to_end() -> None:
    settings = Settings(service_name="identity-security-integration")
    database = Database.from_settings(settings)
    requester_id, approver_id, target_id, emergency_id = (uuid4() for _ in range(4))
    requester_member_id, approver_member_id, target_member_id, emergency_member_id = (
        uuid4() for _ in range(4)
    )
    requester_login = f"security-requester-{uuid4()}"
    approver_login = f"security-approver-{uuid4()}"
    target_login = f"security-target-{uuid4()}"
    emergency_login = f"security-emergency-{uuid4()}"
    try:
        async with database.session() as session:
            password_service = PasswordService()
            for user_id, member_id, login, password in (
                (requester_id, requester_member_id, requester_login, PASSWORD),
                (approver_id, approver_member_id, approver_login, PASSWORD),
                (target_id, target_member_id, target_login, TARGET_PASSWORD),
                (emergency_id, emergency_member_id, emergency_login, TARGET_PASSWORD),
            ):
                session.add(
                    Member(
                        id=member_id,
                        display_name=login,
                        status="ACTIVE",
                    )
                )
                session.add(
                    UserAccount(
                        id=user_id,
                        login=login,
                        password_hash=password_service.hash(password),
                        member_id=member_id,
                        status="ACTIVE",
                        must_change_password=False,
                    )
                )
            await session.flush()
            session.add_all(
                [
                    RoleAssignment(
                        id=uuid4(),
                        user_id=requester_id,
                        role_code="SECURITY_ADMIN",
                        cooperative_id=None,
                        status="ACTIVE",
                    ),
                    RoleAssignment(
                        id=uuid4(),
                        user_id=approver_id,
                        role_code="AUDITOR",
                        cooperative_id=None,
                        status="ACTIVE",
                    ),
                ]
            )
            await session.commit()
    finally:
        await database.dispose()

    with TestClient(create_app(settings)) as client:
        requester_headers = auth_headers(client, requester_login, PASSWORD)
        denied = client.post(
            "/api/v1/admin/cooperatives",
            headers={**requester_headers, "Idempotency-Key": str(uuid4())},
            json={"code": f"step-up-{uuid4().hex[:10]}", "name": "Denied without step-up"},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "STEP_UP_REQUIRED"

        _, requester_code = enroll_totp(client, requester_headers, PASSWORD)
        replay = client.post(
            "/api/v1/auth/step-up/totp",
            headers=requester_headers,
            json={"code": requester_code},
        )
        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "TOTP_INVALID_OR_REPLAYED"

        cooperative = client.post(
            "/api/v1/admin/cooperatives",
            headers={**requester_headers, "Idempotency-Key": str(uuid4())},
            json={
                "code": f"step-up-{uuid4().hex[:10]}",
                "name": "Step-up integration cooperative",
            },
        )
        assert cooperative.status_code == 201, cooperative.text
        cooperative_id = cooperative.json()["data"]["object_id"]

        approver_headers = auth_headers(client, approver_login, PASSWORD)
        enroll_totp(client, approver_headers, PASSWORD)
        target_headers = auth_headers(client, target_login, TARGET_PASSWORD)
        enroll_totp(client, target_headers, TARGET_PASSWORD)
        target_state = client.get("/api/v1/auth/security", headers=target_headers)
        assert target_state.json()["data"]["totp_enabled"] is True

        recovery = client.post(
            "/api/v1/admin/account-recoveries",
            headers={**requester_headers, "Idempotency-Key": str(uuid4())},
            json={
                "target_user_id": str(target_id),
                "temporary_password": RECOVERED_PASSWORD,
                "reason_code": "LOST_AUTHENTICATOR",
                "evidence_id": "paper-act-recovery-001",
            },
        )
        assert recovery.status_code == 201, recovery.text
        recovery_id = recovery.json()["data"]["object_id"]

        self_decision = client.post(
            f"/api/v1/admin/account-recoveries/{recovery_id}/decision",
            headers={**requester_headers, "Idempotency-Key": str(uuid4())},
            json={"approve": True, "reason_code": "SELF_APPROVAL_ATTEMPT"},
        )
        assert self_decision.status_code == 409
        assert self_decision.json()["error"]["code"] == "INDEPENDENT_APPROVAL_REQUIRED"

        approved = client.post(
            f"/api/v1/admin/account-recoveries/{recovery_id}/decision",
            headers={**approver_headers, "Idempotency-Key": str(uuid4())},
            json={"approve": True, "reason_code": "INDEPENDENT_RECOVERY_REVIEW"},
        )
        assert approved.status_code == 200, approved.text
        assert client.get("/api/v1/auth/me", headers=target_headers).status_code == 401

        recovered_headers = auth_headers(client, target_login, RECOVERED_PASSWORD)
        recovered_me = client.get("/api/v1/auth/me", headers=recovered_headers)
        assert recovered_me.json()["data"]["must_change_password"] is True
        recovered_state = client.get("/api/v1/auth/security", headers=recovered_headers)
        assert recovered_state.json()["data"]["totp_enabled"] is False

        emergency_headers = auth_headers(client, emergency_login, TARGET_PASSWORD)
        grant = client.post(
            "/api/v1/admin/break-glass",
            headers={**requester_headers, "Idempotency-Key": str(uuid4())},
            json={
                "target_user_id": str(emergency_id),
                "role": "SECURITY_ADMIN",
                "cooperative_id": cooperative_id,
                "duration_minutes": 15,
                "reason_code": "PRIMARY_OPERATOR_UNAVAILABLE",
                "evidence_id": "incident-break-glass-001",
            },
        )
        assert grant.status_code == 201, grant.text
        grant_id = grant.json()["data"]["object_id"]

        activated = client.post(
            f"/api/v1/admin/break-glass/{grant_id}/decision",
            headers={**approver_headers, "Idempotency-Key": str(uuid4())},
            json={"approve": True, "reason_code": "INCIDENT_CONFIRMED"},
        )
        assert activated.status_code == 200, activated.text

        emergency_me = client.get("/api/v1/auth/me", headers=emergency_headers)
        emergency_roles = emergency_me.json()["data"]["roles"]
        assert any(
            role["role"] == "SECURITY_ADMIN"
            and role["source"] == "BREAK_GLASS"
            and role["expires_at"] is not None
            for role in emergency_roles
        )
        assert client.get("/api/v1/admin/overview", headers=emergency_headers).status_code == 200
        delegated_role = client.post(
            "/api/v1/admin/roles",
            headers={**emergency_headers, "Idempotency-Key": str(uuid4())},
            json={
                "user_id": str(emergency_id),
                "role": "SECURITY_ADMIN",
                "cooperative_id": None,
            },
        )
        assert delegated_role.status_code == 403
        assert delegated_role.json()["error"]["code"] == "PERMANENT_ROLE_REQUIRED"

        revoked = client.post(
            f"/api/v1/admin/break-glass/{grant_id}/revoke",
            headers={**requester_headers, "Idempotency-Key": str(uuid4())},
            json={"reason_code": "PRIMARY_OPERATOR_RESTORED"},
        )
        assert revoked.status_code == 200, revoked.text
        assert client.get("/api/v1/admin/overview", headers=emergency_headers).status_code == 403

    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            factor_states = list(
                (
                    await session.execute(
                        select(AuthenticationFactor.status).where(
                            AuthenticationFactor.user_id == target_id
                        )
                    )
                ).scalars()
            )
            assert factor_states and set(factor_states) == {"DISABLED"}
            authority = await session.get(RoleAssignment, UUID(grant_id))
            assert authority is not None
            assert authority.source == "BREAK_GLASS"
            assert authority.status == "REVOKED"
            signed_events = list(
                (
                    await session.execute(
                        select(SignedEvent).where(
                            SignedEvent.aggregate_id.in_((UUID(recovery_id), UUID(grant_id)))
                        )
                    )
                ).scalars()
            )
            assert {item.event_type for item in signed_events} == {
                "identity.account_recovery_requested",
                "identity.account_recovery_executed",
                "identity.break_glass_requested",
                "identity.break_glass_activated",
                "identity.break_glass_revoked",
            }
            assurances = {
                item.event_type: item.payload["_command_assurance"]
                for item in signed_events
            }
            assert all(
                item["format"] == "critical-command-assurance-v2"
                for item in assurances.values()
            )
            assert assurances["identity.account_recovery_requested"]["exposure"][
                "category"
            ] == "IDENTITY"
            assert assurances["identity.account_recovery_requested"]["next_responsible"][
                0
            ]["kind"] == "NODE"
            assert assurances["identity.account_recovery_executed"]["next_responsible"][
                0
            ]["reference"] == str(target_member_id)
            assert assurances["identity.break_glass_requested"]["next_responsible"][0][
                "reference"
            ] == cooperative_id
            assert assurances["identity.break_glass_activated"]["next_responsible"][0][
                "reference"
            ] == str(emergency_member_id)
            assert assurances["identity.break_glass_revoked"]["next_responsible"][0][
                "reference"
            ] == cooperative_id
            signature_count = len(
                list(
                    (
                        await session.execute(
                            select(EventSignature).where(
                                EventSignature.event_id.in_(item.event_id for item in signed_events)
                            )
                        )
                    ).scalars()
                )
            )
            assert signature_count == len(signed_events)
            audit_actions = set(
                (
                    await session.execute(
                        select(AuditEntry.action).where(AuditEntry.actor_user_id == emergency_id)
                    )
                ).scalars()
            )
            assert "BREAK_GLASS_ACCESS_USED" in audit_actions
    finally:
        await database.dispose()
