from datetime import UTC, datetime
from uuid import UUID, uuid4

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cooperative_clearing.main import create_app
from cooperative_clearing.modules.identity.infrastructure.models import (
    AuthSession,
    Cooperative,
    Member,
    MemberContinuityCase,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import PasswordService
from cooperative_clearing.shared.infrastructure.database import Database

PASSWORD = "Continuity-test-2026!"


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
    confirmation = client.post(
        "/api/v1/auth/totp/enrollment/confirm",
        headers=headers,
        json={"code": pyotp.TOTP(enrollment.json()["data"]["secret"]).now()},
    )
    assert confirmation.status_code == 200, confirmation.text


@pytest.mark.integration
async def test_member_continuity_contains_confirms_rejects_and_blocks_safely() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(service_name=f"member-continuity-{suffix}")
    database = Database.from_settings(settings)
    cooperative_id = uuid4()
    requester_member_id = uuid4()
    reviewer_member_id = uuid4()
    death_member_id = uuid4()
    exit_member_id = uuid4()
    changed_member_id = uuid4()
    requester_user_id = uuid4()
    reviewer_user_id = uuid4()
    death_user_id = uuid4()
    exit_user_id = uuid4()
    changed_user_id = uuid4()
    requester_login = f"continuity-registrar-{suffix}"
    reviewer_login = f"continuity-security-{suffix}"
    death_login = f"continuity-death-{suffix}"
    exit_login = f"continuity-exit-{suffix}"
    changed_login = f"continuity-changed-{suffix}"
    member_ids = [
        requester_member_id,
        reviewer_member_id,
        death_member_id,
        exit_member_id,
        changed_member_id,
    ]
    user_ids = [
        requester_user_id,
        reviewer_user_id,
        death_user_id,
        exit_user_id,
        changed_user_id,
    ]
    logins = [requester_login, reviewer_login, death_login, exit_login, changed_login]
    names = ["Registrar", "Security reviewer", "Estate member", "Exiting member", "Changed member"]
    try:
        async with database.session() as session:
            passwords = PasswordService()
            session.add(
                Cooperative(
                    id=cooperative_id,
                    code=f"continuity-{suffix}",
                    name="Continuity test cooperative",
                    status="ACTIVE",
                )
            )
            session.add_all(
                [
                    Member(
                        id=member_id,
                        display_name=name,
                        registered_by_cooperative_id=cooperative_id,
                        status="ACTIVE",
                    )
                    for member_id, name in zip(member_ids, names, strict=True)
                ]
            )
            session.add_all(
                [
                    UserAccount(
                        id=user_id,
                        login=login_value,
                        password_hash=passwords.hash(PASSWORD),
                        member_id=member_id,
                        status="ACTIVE",
                        must_change_password=False,
                    )
                    for user_id, login_value, member_id in zip(
                        user_ids, logins, member_ids, strict=True
                    )
                ]
            )
            await session.flush()
            session.add_all(
                [
                    RoleAssignment(
                        id=uuid4(),
                        user_id=requester_user_id,
                        role_code="MEMBER_REGISTRAR",
                        cooperative_id=cooperative_id,
                        status="ACTIVE",
                        approved_by_user_id=reviewer_user_id,
                        approved_at=datetime.now(UTC),
                    ),
                    RoleAssignment(
                        id=uuid4(),
                        user_id=requester_user_id,
                        role_code="SECURITY_ADMIN",
                        cooperative_id=None,
                        status="ACTIVE",
                        approved_by_user_id=reviewer_user_id,
                        approved_at=datetime.now(UTC),
                    ),
                    RoleAssignment(
                        id=uuid4(),
                        user_id=reviewer_user_id,
                        role_code="SECURITY_ADMIN",
                        cooperative_id=None,
                        status="ACTIVE",
                        approved_by_user_id=requester_user_id,
                        approved_at=datetime.now(UTC),
                    ),
                ]
            )
            session.add_all(
                [
                    Membership(
                        id=uuid4(),
                        cooperative_id=cooperative_id,
                        member_id=member_id,
                        member_number=f"CONT-{suffix}-{index}",
                        status="ACTIVE",
                        joined_at=datetime.now(UTC),
                    )
                    for index, member_id in enumerate(
                        [death_member_id, exit_member_id, changed_member_id], start=1
                    )
                ]
            )
            await session.commit()
    finally:
        await database.dispose()

    with TestClient(create_app(settings), client=("127.0.0.1", 45200)) as client:
        requester_headers = login(client, requester_login)
        reviewer_headers = login(client, reviewer_login)
        death_headers = login(client, death_login)
        exit_headers = login(client, exit_login)
        enable_step_up(client, requester_headers)
        enable_step_up(client, reviewer_headers)

        death_request_key = str(uuid4())
        death_payload = {
            "cooperative_id": str(cooperative_id),
            "member_id": str(death_member_id),
            "case_type": "DEATH_OR_INCAPACITY",
            "expected_member_version": 1,
            "evidence_refs": [f"registry:death-{suffix}"],
            "reason_code": "OFFICIAL_NOTICE_RECEIVED",
        }
        death_created = client.post(
            "/api/v1/admin/member-continuity-cases",
            headers={**requester_headers, "Idempotency-Key": death_request_key},
            json=death_payload,
        )
        assert death_created.status_code == 201, death_created.text
        assert death_created.json()["data"]["status"] == "PENDING_REVIEW"
        death_case_id = death_created.json()["data"]["object_id"]

        death_replayed = client.post(
            "/api/v1/admin/member-continuity-cases",
            headers={**requester_headers, "Idempotency-Key": death_request_key},
            json=death_payload,
        )
        assert death_replayed.status_code == 201, death_replayed.text
        assert death_replayed.json()["data"] == {
            **death_created.json()["data"],
            "replayed": True,
        }
        assert client.get("/api/v1/auth/me", headers=death_headers).status_code == 401
        denied_login = client.post(
            "/api/v1/auth/login", json={"login": death_login, "password": PASSWORD}
        )
        assert denied_login.status_code == 401

        own_review = client.post(
            f"/api/v1/admin/member-continuity-cases/{death_case_id}/decision",
            headers={**requester_headers, "Idempotency-Key": str(uuid4())},
            json={
                "approve": True,
                "expected_version": 1,
                "reason_code": "SELF_REVIEW_FORBIDDEN",
            },
        )
        assert own_review.status_code == 409, own_review.text
        assert own_review.json()["error"]["code"] == "MEMBER_CONTINUITY_INDEPENDENT_REVIEW_REQUIRED"

        death_approved = client.post(
            f"/api/v1/admin/member-continuity-cases/{death_case_id}/decision",
            headers={**reviewer_headers, "Idempotency-Key": str(uuid4())},
            json={
                "approve": True,
                "expected_version": 1,
                "reason_code": "INDEPENDENT_CONFIRMATION",
            },
        )
        assert death_approved.status_code == 201, death_approved.text
        assert death_approved.json()["data"]["status"] == "CONFIRMED"

        listed = client.get("/api/v1/admin/member-continuity-cases", headers=reviewer_headers)
        assert listed.status_code == 200, listed.text
        death_view = next(item for item in listed.json()["data"] if item["id"] == death_case_id)
        assert death_view["disabled_user_count"] == 1
        assert death_view["suspended_membership_count"] == 1
        assert death_view["reference_summary"]["groups"]["identity_registry"] >= 2
        assert "access_snapshot" not in death_view
        assert not any("." in key for key in death_view["reference_summary"]["groups"])

        exit_created = client.post(
            "/api/v1/admin/member-continuity-cases",
            headers={**requester_headers, "Idempotency-Key": str(uuid4())},
            json={
                "cooperative_id": str(cooperative_id),
                "member_id": str(exit_member_id),
                "case_type": "VOLUNTARY_EXIT",
                "expected_member_version": 1,
                "evidence_refs": [f"request:exit-{suffix}"],
                "reason_code": "MEMBER_REQUEST_RECEIVED",
            },
        )
        assert exit_created.status_code == 201, exit_created.text
        exit_case_id = exit_created.json()["data"]["object_id"]

        unsafe_reactivation = client.post(
            f"/api/v1/admin/users/{exit_user_id}/transitions",
            headers={**reviewer_headers, "Idempotency-Key": str(uuid4())},
            json={
                "target_status": "ACTIVE",
                "reason_code": "MUST_REMAIN_CONTAINED",
                "expected_version": 3,
            },
        )
        assert unsafe_reactivation.status_code == 409, unsafe_reactivation.text
        assert unsafe_reactivation.json()["error"]["code"] == "MEMBER_NOT_ACTIVE"

        exit_rejected = client.post(
            f"/api/v1/admin/member-continuity-cases/{exit_case_id}/decision",
            headers={**reviewer_headers, "Idempotency-Key": str(uuid4())},
            json={
                "approve": False,
                "expected_version": 1,
                "reason_code": "REQUEST_WITHDRAWN_VERIFIED",
            },
        )
        assert exit_rejected.status_code == 201, exit_rejected.text
        assert exit_rejected.json()["data"]["status"] == "REJECTED"
        assert client.get("/api/v1/auth/me", headers=exit_headers).status_code == 401
        assert login(client, exit_login)["Authorization"].startswith("Bearer ")

        changed_created = client.post(
            "/api/v1/admin/member-continuity-cases",
            headers={**requester_headers, "Idempotency-Key": str(uuid4())},
            json={
                "cooperative_id": str(cooperative_id),
                "member_id": str(changed_member_id),
                "case_type": "VOLUNTARY_EXIT",
                "expected_member_version": 1,
                "evidence_refs": [f"request:changed-{suffix}"],
                "reason_code": "MEMBER_REQUEST_RECEIVED",
            },
        )
        assert changed_created.status_code == 201, changed_created.text
        changed_case_id = changed_created.json()["data"]["object_id"]

        database = Database.from_settings(settings)
        try:
            async with database.session() as session:
                changed_user = await session.get(UserAccount, changed_user_id, with_for_update=True)
                assert changed_user is not None
                changed_user.version += 1
                changed_user.updated_at = datetime.now(UTC)
                await session.commit()
        finally:
            await database.dispose()

        blocked_rejection = client.post(
            f"/api/v1/admin/member-continuity-cases/{changed_case_id}/decision",
            headers={**reviewer_headers, "Idempotency-Key": str(uuid4())},
            json={
                "approve": False,
                "expected_version": 1,
                "reason_code": "STATE_CHANGED_DURING_REVIEW",
            },
        )
        assert blocked_rejection.status_code == 201, blocked_rejection.text
        assert blocked_rejection.json()["data"]["status"] == "BLOCKED"

    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            death_member = await session.get(Member, death_member_id)
            death_user = await session.get(UserAccount, death_user_id)
            exit_member = await session.get(Member, exit_member_id)
            exit_user = await session.get(UserAccount, exit_user_id)
            changed_member = await session.get(Member, changed_member_id)
            changed_user = await session.get(UserAccount, changed_user_id)
            assert death_member is not None and death_member.status == "SUCCESSION_REVIEW"
            assert death_user is not None and death_user.status == "DISABLED"
            assert exit_member is not None and exit_member.status == "ACTIVE"
            assert exit_user is not None and exit_user.status == "ACTIVE"
            assert changed_member is not None and changed_member.status == "EXIT_PENDING"
            assert changed_user is not None and changed_user.status == "DISABLED"
            target_memberships = list(
                (
                    await session.execute(
                        select(Membership).where(
                            Membership.member_id.in_(
                                [death_member_id, exit_member_id, changed_member_id]
                            )
                        )
                    )
                ).scalars()
            )
            statuses = {item.member_id: item.status for item in target_memberships}
            assert statuses[death_member_id] == "SUSPENDED"
            assert statuses[exit_member_id] == "ACTIVE"
            assert statuses[changed_member_id] == "SUSPENDED"
            old_exit_sessions = list(
                (
                    await session.execute(
                        select(AuthSession).where(AuthSession.user_id == exit_user_id)
                    )
                ).scalars()
            )
            assert any(item.status == "REVOKED" for item in old_exit_sessions)
            assert any(item.status == "ACTIVE" for item in old_exit_sessions)
            changed_case = await session.get(MemberContinuityCase, UUID(changed_case_id))
            assert changed_case is not None
            assert changed_case.review_blockers == ["USER_VERSION_CHANGED"]
            event_types = set(
                (
                    await session.execute(
                        select(SignedEvent.event_type).where(
                            SignedEvent.aggregate_id.in_(
                                [UUID(death_case_id), UUID(exit_case_id), UUID(changed_case_id)]
                            )
                        )
                    )
                ).scalars()
            )
            assert {
                "identity.member_continuity_requested",
                "identity.member_continuity_confirmed",
                "identity.member_continuity_rejected",
                "identity.member_continuity_blocked",
            }.issubset(event_types)
    finally:
        await database.dispose()
