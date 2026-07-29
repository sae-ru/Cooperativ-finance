from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from cooperative_clearing.cli import initialize_node
from cooperative_clearing.modules.identity.application.admin import IdentityAdminService
from cooperative_clearing.modules.identity.application.authentication import AuthenticationService
from cooperative_clearing.modules.identity.application.bootstrap import bootstrap_identity
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import PasswordService
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database


def make_principal(user_id: UUID, role: RoleCode, cooperative_id: UUID | None = None) -> Principal:
    member_id = uuid4()
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        login=f"test-{user_id}",
        member_id=member_id,
        must_change_password=False,
        roles=(RoleGrant(assignment_id=uuid4(), role=role, cooperative_id=cooperative_id),),
    )


@pytest.mark.integration
async def test_bootstrap_is_idempotent_and_separates_duties() -> None:
    settings = Settings(service_name="identity-integration")
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            unrelated_id = uuid4()
            session.add(
                UserAccount(
                    id=unrelated_id,
                    login=f"preexisting-{unrelated_id}",
                    password_hash=PasswordService().hash("preexisting-password-value"),
                    member_id=None,
                    status="ACTIVE",
                    must_change_password=False,
                )
            )
            # The dedicated test container can survive targeted local runs; the
            # postcondition and the second call define bootstrap idempotency.
            await bootstrap_identity(session, settings)
            await session.commit()
        async with database.session() as session:
            assert await bootstrap_identity(session, settings) is False
            users = (await session.execute(UserAccount.__table__.select())).all()
            assignments = (await session.execute(RoleAssignment.__table__.select())).all()
        assert {row.login for row in users}.issuperset({"registrar", "security", "auditor"})
        assert {row.role_code for row in assignments}.issuperset(
            {"MEMBER_REGISTRAR", "COOPERATIVE_ADMIN", "SECURITY_ADMIN", "AUDITOR"}
        )
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_authentication_rotation_revocation_and_lockout() -> None:
    settings = Settings(service_name="auth-integration", auth_max_failed_attempts=3)
    database = Database.from_settings(settings)
    password_service = PasswordService()
    service = AuthenticationService(settings, password_service)
    user_id = uuid4()
    login = f"auth-{user_id}"
    password = "".join(("integration-", "password-value"))
    try:
        async with database.session() as session:
            session.add(
                UserAccount(
                    id=user_id,
                    login=login,
                    password_hash=password_service.hash(password),
                    member_id=None,
                    status="ACTIVE",
                    must_change_password=False,
                )
            )
            await session.commit()

        for _ in range(3):
            async with database.session() as session:
                with pytest.raises(DomainError):
                    await service.login(
                        session,
                        login=login,
                        password="".join(("invalid-", "password-value")),
                        client_ip="127.0.0.1",
                        user_agent="pytest",
                        request_id=uuid4(),
                    )
                await session.commit()
        async with database.session() as session:
            locked = await session.get(UserAccount, user_id)
            assert locked is not None and locked.locked_until is not None
            locked.locked_until = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        async with database.session() as session:
            issued = await service.login(
                session,
                login=login,
                password=password,
                client_ip="127.0.0.1",
                user_agent="pytest",
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            assert (
                await service.principal_for_access(session, issued.access_token)
            ).user_id == user_id
            rotated = await service.refresh(
                session,
                refresh_token=issued.refresh_token,
                csrf_cookie=issued.csrf_token,
                csrf_header=issued.csrf_token,
                client_ip="127.0.0.1",
                user_agent="pytest-rotated",
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            with pytest.raises(DomainError):
                await service.principal_for_access(session, issued.access_token)
            principal = await service.principal_for_access(session, rotated.access_token)
            await service.logout(session, principal, uuid4())
            await session.commit()
        async with database.session() as session:
            with pytest.raises(DomainError):
                await service.principal_for_access(session, rotated.access_token)
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_admin_commands_are_idempotent_and_privileged_roles_need_approval() -> None:
    settings = Settings(service_name="admin-integration")
    await initialize_node(settings)
    database = Database.from_settings(settings)
    service = IdentityAdminService(settings=settings)
    security_id = uuid4()
    auditor_id = uuid4()
    target_id = uuid4()
    technical_target_id = uuid4()
    security = make_principal(security_id, RoleCode.SECURITY_ADMIN)
    auditor = make_principal(auditor_id, RoleCode.AUDITOR)
    target_member_id = uuid4()
    try:
        async with database.session() as session:
            for user_id, member_id, login in (
                (security_id, security.member_id, f"security-{security_id}"),
                (auditor_id, auditor.member_id, f"auditor-{auditor_id}"),
                (target_id, target_member_id, f"target-{target_id}"),
            ):
                assert member_id is not None
                session.add(Member(id=member_id, display_name=login, status="ACTIVE"))
                session.add(
                    UserAccount(
                        id=user_id,
                        login=login,
                        password_hash=PasswordService().hash("integration-password-value"),
                        member_id=member_id,
                        status="ACTIVE",
                        must_change_password=False,
                    )
                )
            session.add(
                UserAccount(
                    id=technical_target_id,
                    login=f"technical-{technical_target_id}",
                    password_hash=PasswordService().hash("integration-password-value"),
                    member_id=None,
                    status="ACTIVE",
                    must_change_password=False,
                )
            )
            await session.flush()
            session.add_all(
                [
                    RoleAssignment(
                        id=security.roles[0].assignment_id,
                        user_id=security_id,
                        role_code=RoleCode.SECURITY_ADMIN.value,
                        cooperative_id=None,
                        status="ACTIVE",
                    ),
                    RoleAssignment(
                        id=auditor.roles[0].assignment_id,
                        user_id=auditor_id,
                        role_code=RoleCode.AUDITOR.value,
                        cooperative_id=None,
                        status="ACTIVE",
                    ),
                ]
            )
            await session.commit()

        key = str(uuid4())
        code = f"test-{uuid4().hex[:12]}"
        async with database.session() as session:
            created = await service.create_cooperative(
                session,
                principal=security,
                idempotency_key=key,
                code=code,
                name="Integration cooperative",
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            replayed = await service.create_cooperative(
                session,
                principal=security,
                idempotency_key=key,
                code=code,
                name="Integration cooperative",
                request_id=uuid4(),
            )
            assert replayed.object_id == created.object_id
            assert replayed.replayed is True

        async with database.session() as session:
            requested = await service.assign_role(
                session,
                principal=security,
                user_id=target_id,
                role=RoleCode.AUDITOR,
                cooperative_id=None,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            assignment = await session.get(RoleAssignment, requested.object_id)
            assert assignment is not None and assignment.status == "PENDING_APPROVAL"
            approved = await service.decide_role(
                session,
                principal=auditor,
                assignment_id=assignment.id,
                approve=True,
                reason_code="DUAL_CONTROL_APPROVED",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
            assert approved.object_id == assignment.id
        async with database.session() as session:
            assignment = await session.get(RoleAssignment, requested.object_id)
            assert assignment is not None and assignment.status == "ACTIVE"
            await service.revoke_role(
                session,
                principal=security,
                assignment_id=assignment.id,
                reason_code="AUTHORITY_WITHDRAWN",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            assignment = await session.get(RoleAssignment, requested.object_id)
            assert assignment is not None and assignment.status == "REVOKED"
            events = list(
                (
                    await session.execute(
                        select(SignedEvent)
                        .where(SignedEvent.aggregate_id == assignment.id)
                        .order_by(SignedEvent.local_sequence)
                    )
                ).scalars()
            )
            assert [event.event_type for event in events] == [
                "identity.role_assignment_requested",
                "identity.role_assignment_approved",
                "identity.role_assignment_revoked",
            ]
            assurances = [event.payload["_command_assurance"] for event in events]
            assert all(
                assurance["format"] == "critical-command-assurance-v2"
                for assurance in assurances
            )
            assert assurances[0]["next_responsible"][0]["kind"] == "NODE"
            assert assurances[1]["next_responsible"][0]["reference"] == str(
                target_member_id
            )
            assert assurances[2]["next_responsible"][0]["kind"] == "NODE"

        async with database.session() as session:
            activated = await service.assign_role(
                session,
                principal=security,
                user_id=target_id,
                role=RoleCode.EXCHANGE_PARTICIPANT,
                cooperative_id=created.object_id,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            rejected_request = await service.assign_role(
                session,
                principal=security,
                user_id=target_id,
                role=RoleCode.ARBITRATOR,
                cooperative_id=None,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            rejected = await service.decide_role(
                session,
                principal=auditor,
                assignment_id=rejected_request.object_id,
                approve=False,
                reason_code="DUAL_CONTROL_REJECTED",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
            assert rejected.object_id == rejected_request.object_id
        async with database.session() as session:
            assignments = {
                item.id: item
                for item in (
                    await session.execute(
                        select(RoleAssignment).where(
                            RoleAssignment.id.in_(
                                (activated.object_id, rejected_request.object_id)
                            )
                        )
                    )
                ).scalars()
            }
            assert assignments[activated.object_id].status == "ACTIVE"
            assert assignments[rejected_request.object_id].status == "REJECTED"
            events = list(
                (
                    await session.execute(
                        select(SignedEvent).where(
                            SignedEvent.aggregate_id.in_(
                                (activated.object_id, rejected_request.object_id)
                            )
                        )
                    )
                ).scalars()
            )
            by_type = {
                event.event_type: event.payload["_command_assurance"]
                for event in events
            }
            assert by_type["identity.role_assignment_activated"]["next_responsible"][0][
                "reference"
            ] == str(target_member_id)
            assert by_type["identity.role_assignment_rejected"]["next_responsible"][0][
                "kind"
            ] == "NODE"

        with pytest.raises(DomainError) as technical_assignment:
            async with database.session() as session:
                await service.assign_role(
                    session,
                    principal=security,
                    user_id=technical_target_id,
                    role=RoleCode.AUDITOR,
                    cooperative_id=None,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
        assert technical_assignment.value.code == "PERSONAL_ACTOR_REQUIRED"

        with pytest.raises(DomainError) as self_assignment:
            async with database.session() as session:
                await service.assign_role(
                    session,
                    principal=security,
                    user_id=security_id,
                    role=RoleCode.AUDITOR,
                    cooperative_id=None,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
        assert self_assignment.value.code == "SELF_ROLE_ASSIGNMENT_FORBIDDEN"
    finally:
        await database.dispose()
