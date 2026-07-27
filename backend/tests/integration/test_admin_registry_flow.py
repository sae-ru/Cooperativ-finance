from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cooperative_clearing.main import create_app
from cooperative_clearing.modules.identity.application.admin import IdentityAdminService
from cooperative_clearing.modules.identity.application.authentication import AuthenticationService
from cooperative_clearing.modules.identity.domain.types import (
    CooperativeStatus,
    MembershipStatus,
    Principal,
    RoleCode,
    RoleGrant,
    UserStatus,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    AuthSession,
    Cooperative,
    Member,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import PasswordService
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database


def principal(user_id: UUID, role: RoleCode, cooperative_id: UUID | None = None) -> Principal:
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        login=f"registry-{user_id}",
        member_id=None,
        must_change_password=False,
        roles=(
            RoleGrant(
                assignment_id=uuid4(),
                role=role,
                cooperative_id=cooperative_id,
            ),
        ),
    )


@pytest.mark.integration
async def test_registry_lifecycles_and_account_disable_are_transactional() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(service_name=f"admin-registry-{suffix}")
    database = Database.from_settings(settings)
    passwords = PasswordService()
    service = IdentityAdminService(passwords)
    cooperative_id = uuid4()
    member_id = uuid4()
    membership_id = uuid4()
    security_id = uuid4()
    registrar_id = uuid4()
    target_id = uuid4()
    target_password = "registry-target-password"
    security = principal(security_id, RoleCode.SECURITY_ADMIN)
    registrar = principal(registrar_id, RoleCode.MEMBER_REGISTRAR, cooperative_id)

    try:
        async with database.session() as session:
            session.add_all(
                [
                    Cooperative(
                        id=cooperative_id,
                        code=f"registry-{suffix}",
                        name="Registry lifecycle cooperative",
                        status="ACTIVE",
                    ),
                    Member(
                        id=member_id,
                        display_name="Registry lifecycle member",
                        registered_by_cooperative_id=cooperative_id,
                        status="ACTIVE",
                    ),
                    UserAccount(
                        id=security_id,
                        login=f"registry-security-{suffix}",
                        password_hash=passwords.hash("registry-security-password"),
                        member_id=None,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                    UserAccount(
                        id=registrar_id,
                        login=f"registry-registrar-{suffix}",
                        password_hash=passwords.hash("registry-registrar-password"),
                        member_id=None,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                    UserAccount(
                        id=target_id,
                        login=f"registry-target-{suffix}",
                        password_hash=passwords.hash(target_password),
                        member_id=None,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                ]
            )
            session.add(
                Membership(
                    id=membership_id,
                    cooperative_id=cooperative_id,
                    member_id=member_id,
                    member_number=f"R-{suffix}",
                    status="ACTIVE",
                )
            )
            await session.commit()

        async with database.session() as session:
            await service.transition_cooperative(
                session,
                principal=security,
                cooperative_id=cooperative_id,
                target=CooperativeStatus.SUSPENDED,
                reason_code="LIFECYCLE_TEST",
                expected_version=1,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            cooperative = await session.get(Cooperative, cooperative_id)
            assert cooperative is not None and cooperative.status == "SUSPENDED"
            await service.transition_cooperative(
                session,
                principal=security,
                cooperative_id=cooperative_id,
                target=CooperativeStatus.ACTIVE,
                reason_code="LIFECYCLE_TEST",
                expected_version=cooperative.version,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        for target in (
            MembershipStatus.SUSPENDED,
            MembershipStatus.ACTIVE,
            MembershipStatus.ENDED,
        ):
            async with database.session() as session:
                membership = await session.get(Membership, membership_id)
                assert membership is not None
                await service.transition_membership(
                    session,
                    principal=registrar,
                    membership_id=membership_id,
                    target=target,
                    reason_code="LIFECYCLE_TEST",
                    expected_version=membership.version,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
                await session.commit()
        async with database.session() as session:
            membership = await session.get(Membership, membership_id)
            assert membership is not None
            assert membership.status == "ENDED" and membership.ended_at is not None

        authentication = AuthenticationService(settings, passwords)
        async with database.session() as session:
            issued = await authentication.login(
                session,
                login=f"registry-target-{suffix}",
                password=target_password,
                client_ip="127.0.0.1",
                user_agent="pytest",
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            target = await session.get(UserAccount, target_id)
            assert target is not None
            await service.transition_user(
                session,
                principal=security,
                user_id=target_id,
                target=UserStatus.DISABLED,
                reason_code="SECURITY_TEST",
                expected_version=target.version,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            target = await session.get(UserAccount, target_id)
            sessions = list(
                (
                    await session.execute(
                        select(AuthSession).where(AuthSession.user_id == target_id)
                    )
                ).scalars()
            )
            assert target is not None and target.status == "DISABLED"
            assert sessions and all(item.status == "REVOKED" for item in sessions)
            with pytest.raises(DomainError):
                await authentication.principal_for_access(session, issued.access_token)

        async with database.session() as session:
            with pytest.raises(DomainError) as self_disable:
                await service.transition_user(
                    session,
                    principal=security,
                    user_id=security_id,
                    target=UserStatus.DISABLED,
                    reason_code="INVALID_SELF_DISABLE",
                    expected_version=1,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            assert self_disable.value.code == "SELF_ACCOUNT_DISABLE_FORBIDDEN"
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_scoped_registrar_sees_only_its_identity_registry() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(service_name=f"admin-scope-{suffix}")
    database = Database.from_settings(settings)
    passwords = PasswordService()
    cooperative_a = uuid4()
    cooperative_b = uuid4()
    member_a = uuid4()
    member_b = uuid4()
    registrar_id = uuid4()
    foreign_user_id = uuid4()
    registrar_login = f"scope-registrar-{suffix}"
    registrar_password = "scope-registrar-password"

    try:
        async with database.session() as session:
            session.add_all(
                [
                    Cooperative(
                        id=cooperative_a,
                        code=f"scope-a-{suffix}",
                        name="Scoped cooperative A",
                        status="ACTIVE",
                    ),
                    Cooperative(
                        id=cooperative_b,
                        code=f"scope-b-{suffix}",
                        name="Scoped cooperative B",
                        status="ACTIVE",
                    ),
                    Member(
                        id=member_a,
                        display_name="Scoped member A",
                        registered_by_cooperative_id=cooperative_a,
                        status="ACTIVE",
                    ),
                    Member(
                        id=member_b,
                        display_name="Scoped member B",
                        registered_by_cooperative_id=cooperative_b,
                        status="ACTIVE",
                    ),
                    Membership(
                        id=uuid4(),
                        cooperative_id=cooperative_a,
                        member_id=member_a,
                        member_number=f"A-{suffix}",
                        status="ACTIVE",
                    ),
                    Membership(
                        id=uuid4(),
                        cooperative_id=cooperative_b,
                        member_id=member_b,
                        member_number=f"B-{suffix}",
                        status="ACTIVE",
                    ),
                    UserAccount(
                        id=registrar_id,
                        login=registrar_login,
                        password_hash=passwords.hash(registrar_password),
                        member_id=member_a,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                    UserAccount(
                        id=foreign_user_id,
                        login=f"scope-foreign-{suffix}",
                        password_hash=passwords.hash("scope-foreign-password"),
                        member_id=member_b,
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
                        user_id=registrar_id,
                        role_code=role_code,
                        cooperative_id=cooperative_a,
                        status="ACTIVE",
                        granted_by_user_id=None,
                        approved_by_user_id=None,
                    )
                    for role_code in ("MEMBER_REGISTRAR", "AUDITOR", "SECURITY_ADMIN")
                ]
            )
            await session.commit()
    finally:
        await database.dispose()

    with TestClient(create_app(settings)) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login": registrar_login, "password": registrar_password},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

        cooperatives = client.get("/api/v1/admin/cooperatives", headers=headers)
        members = client.get("/api/v1/admin/members?limit=500", headers=headers)
        memberships = client.get("/api/v1/admin/memberships", headers=headers)
        users = client.get("/api/v1/admin/users", headers=headers)
        overview = client.get("/api/v1/admin/overview", headers=headers)
        assert (
            cooperatives.status_code
            == members.status_code
            == memberships.status_code
            == users.status_code
            == 200
        )
        assert {item["id"] for item in cooperatives.json()["data"]} == {str(cooperative_a)}
        assert {item["id"] for item in members.json()["data"]} == {str(member_a)}
        assert {item["cooperative_id"] for item in memberships.json()["data"]} == {
            str(cooperative_a)
        }
        assert {item["id"] for item in users.json()["data"]} == {str(registrar_id)}
        assert overview.json()["data"]["members"] == 1
        assert overview.json()["data"]["cooperatives"] == 1
        assert overview.json()["data"]["users"] == 1

        created = client.post(
            "/api/v1/admin/members",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "cooperative_id": str(cooperative_a),
                "display_name": "New scoped member",
            },
        )
        assert created.status_code == 201
        legacy_request = client.post(
            "/api/v1/admin/members",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"display_name": "Legacy client scoped member"},
        )
        assert legacy_request.status_code == 201
        denied = client.post(
            "/api/v1/admin/members",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "cooperative_id": str(cooperative_b),
                "display_name": "Forbidden foreign member",
            },
        )
        assert denied.status_code == 403

        denied_cooperative = client.post(
            f"/api/v1/admin/cooperatives/{cooperative_b}/transitions",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "target_status": "SUSPENDED",
                "reason_code": "FOREIGN_SCOPE_TEST",
                "expected_version": 1,
            },
        )
        denied_member_transition = client.post(
            f"/api/v1/admin/members/{member_b}/transitions",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "target_status": "SUSPENDED",
                "reason_code": "FOREIGN_SCOPE_TEST",
                "expected_version": 1,
            },
        )
        denied_user_create = client.post(
            "/api/v1/admin/users",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "login": f"forbidden-user-{suffix}",
                "temporary_password": "forbidden-user-password",
                "member_id": str(member_b),
            },
        )
        denied_user_transition = client.post(
            f"/api/v1/admin/users/{foreign_user_id}/transitions",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "target_status": "DISABLED",
                "reason_code": "FOREIGN_SCOPE_TEST",
                "expected_version": 1,
            },
        )
        assert denied_cooperative.status_code == 403
        assert denied_member_transition.status_code == 403
        assert denied_user_create.status_code == 403
        assert denied_user_transition.status_code == 403
