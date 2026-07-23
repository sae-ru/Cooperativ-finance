"""One-time separated-duty identity bootstrap and deterministic demo fixtures."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.secrets import read_text_secret
from cooperative_clearing.shared.core.security import PasswordService

BOOTSTRAP_LOGINS = ("registrar", "security", "auditor")


def stable_id(kind: str, value: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"cooperative-clearing:{kind}:{value}")


async def bootstrap_identity(session: AsyncSession, settings: Settings) -> bool:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('cooperative-clearing:identity-bootstrap'))")
    )
    changed = False
    cooperative_id = stable_id("cooperative", settings.node_code)
    cooperative = (
        await session.execute(select(Cooperative).where(Cooperative.code == settings.node_code))
    ).scalar_one_or_none()
    if cooperative is None:
        cooperative = Cooperative(
            id=cooperative_id,
            code=settings.node_code,
            name=settings.node_display_name,
            status="ACTIVE",
        )
        session.add(cooperative)
        await session.flush()
        changed = True
    elif cooperative.id != cooperative_id:
        raise RuntimeError("bootstrap cooperative identity collision")

    account_specs = (
        (
            "registrar",
            settings.bootstrap_registrar_password_file,
            (
                ("MEMBER_REGISTRAR", cooperative_id),
                ("COOPERATIVE_ADMIN", cooperative_id),
                ("NODE_REGISTRAR", None),
                ("NODE_BUSINESS_OPERATOR", None),
            ),
        ),
        (
            "security",
            settings.bootstrap_security_password_file,
            (
                ("SECURITY_ADMIN", None),
                ("NODE_SECURITY_ADMIN", None),
                ("NODE_TECHNICAL_CUSTODIAN", None),
            ),
        ),
        (
            "auditor",
            settings.bootstrap_auditor_password_file,
            (("AUDITOR", None), ("NODE_AUDITOR", None)),
        ),
    )
    passwords = PasswordService()
    for login, secret_path, roles in account_specs:
        expected_user_id = stable_id("bootstrap-user", login)
        user = (
            await session.execute(select(UserAccount).where(UserAccount.login == login))
        ).scalar_one_or_none()
        if user is None:
            user = UserAccount(
                id=expected_user_id,
                login=login,
                password_hash=passwords.hash(read_text_secret(secret_path)),
                member_id=None,
                status="ACTIVE",
                must_change_password=True,
            )
            session.add(user)
            await session.flush()
            changed = True
            await AuditRepository(session).record(
                action="BOOTSTRAP_USER_CREATED",
                object_type="UserAccount",
                object_id=expected_user_id,
                outcome="SUCCESS",
                payload={"login": login, "bootstrap_exception": True},
            )
        elif user.id != expected_user_id:
            raise RuntimeError(f"bootstrap user identity collision: {login}")

        for role_code, role_cooperative_id in roles:
            role_id = stable_id("bootstrap-role", f"{login}:{role_code}")
            role = await session.get(RoleAssignment, role_id)
            if role is None:
                session.add(
                    RoleAssignment(
                        id=role_id,
                        user_id=expected_user_id,
                        role_code=role_code,
                        cooperative_id=role_cooperative_id,
                        status="ACTIVE",
                        granted_by_user_id=None,
                        approved_by_user_id=None,
                        approved_at=datetime.now(UTC),
                    )
                )
                changed = True
            elif (
                role.user_id != expected_user_id
                or role.role_code != role_code
                or role.cooperative_id != role_cooperative_id
            ):
                raise RuntimeError(f"bootstrap role identity collision: {login}:{role_code}")
    return changed


async def seed_demo_identity(session: AsyncSession, settings: Settings) -> None:
    cooperative_id = stable_id("cooperative", settings.node_code)
    cooperative_statement = insert(Cooperative).values(
        id=cooperative_id,
        code=settings.node_code,
        name=settings.node_display_name,
        status="ACTIVE",
    )
    await session.execute(
        cooperative_statement.on_conflict_do_update(
            index_elements=[Cooperative.code],
            set_={"name": cooperative_statement.excluded.name, "updated_at": datetime.now(UTC)},
        )
    )

    members = (
        ("demo-member-anna", "Anna Petrova", "ACTIVE", "D-0001"),
        ("demo-member-boris", "Boris Sokolov", "PENDING_VERIFICATION", "D-0002"),
        ("demo-member-elena", "Elena Volkova", "ACTIVE", "D-0003"),
        ("demo-member-mikhail", "Mikhail Orlov", "SUSPENDED", "D-0004"),
        ("demo-member-pavel", "Pavel Lebedev", "ACTIVE", "D-0005"),
        ("demo-member-nina", "Nina Smirnova", "ACTIVE", "D-0006"),
    )
    for key, display_name, status, member_number in members:
        member_id = stable_id("member", key)
        member_statement = insert(Member).values(
            id=member_id,
            display_name=display_name,
            status=status,
        )
        await session.execute(
            member_statement.on_conflict_do_update(
                index_elements=[Member.id],
                set_={
                    "display_name": member_statement.excluded.display_name,
                    "status": member_statement.excluded.status,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        membership_statement = insert(Membership).values(
            id=stable_id("membership", key),
            cooperative_id=cooperative_id,
            member_id=member_id,
            member_number=member_number,
            status="ACTIVE" if status == "ACTIVE" else "PENDING",
            joined_at=datetime.now(UTC) if status == "ACTIVE" else None,
        )
        await session.execute(
            membership_statement.on_conflict_do_update(
                index_elements=[Membership.id],
                set_={
                    "status": membership_statement.excluded.status,
                    "joined_at": membership_statement.excluded.joined_at,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
    operator_links = (
        ("registrar", "demo-member-anna"),
        ("auditor", "demo-member-pavel"),
        ("security", "demo-member-elena"),
    )
    for login, member_key in operator_links:
        await session.execute(
            update(UserAccount)
            .where(UserAccount.id == stable_id("bootstrap-user", login))
            .values(member_id=stable_id("member", member_key), updated_at=datetime.now(UTC))
        )

    demo_arbitrator_user_id = stable_id("demo-user", "nina-arbitrator")
    demo_arbitrator = insert(UserAccount).values(
        id=demo_arbitrator_user_id,
        login="demo-arbitrator",
        password_hash=PasswordService().hash(str(uuid4())),
        member_id=stable_id("member", "demo-member-nina"),
        status="ACTIVE",
        must_change_password=True,
    )
    await session.execute(demo_arbitrator.on_conflict_do_nothing(index_elements=[UserAccount.id]))

    demo_roles = (
        ("security", "RISK_ADMIN"),
        ("security", "DATA_STEWARD"),
        ("security", "WAREHOUSE_CUSTODIAN"),
        ("security", "LOGISTICS_OPERATOR"),
        ("registrar", "WAREHOUSE_CUSTODIAN"),
        ("registrar", "RIGHTS_OPERATOR"),
        ("auditor", "INVENTORY_CONTROLLER"),
        ("registrar", "CLEARING_OPERATOR"),
        ("security", "CLEARING_CONTROLLER"),
        ("auditor", "CLEARING_FINALIZER"),
        ("security", "SOLIDARITY_OPERATOR"),
        ("auditor", "SOLIDARITY_CONTROLLER"),
        ("registrar", "SOLIDARITY_CONTROLLER"),
        ("security", "CRISIS_OPERATOR"),
        ("auditor", "CRISIS_CONTROLLER"),
        ("registrar", "CRISIS_CONTROLLER"),
    )
    for login, role_code in demo_roles:
        role_statement = insert(RoleAssignment).values(
            id=stable_id("demo-role", f"{login}:{role_code}"),
            user_id=stable_id("bootstrap-user", login),
            role_code=role_code,
            cooperative_id=cooperative_id,
            status="ACTIVE",
            granted_by_user_id=stable_id("bootstrap-user", "registrar"),
            approved_by_user_id=stable_id("bootstrap-user", "auditor"),
            approved_at=datetime.now(UTC),
        )
        await session.execute(
            role_statement.on_conflict_do_update(
                index_elements=[RoleAssignment.id],
                set_={"status": "ACTIVE", "cooperative_id": cooperative_id},
            )
        )

    privileged_demo_roles = (
        ("security", stable_id("bootstrap-user", "security")),
        ("demo-arbitrator", demo_arbitrator_user_id),
    )
    for login, user_id in privileged_demo_roles:
        role_statement = insert(RoleAssignment).values(
            id=stable_id("demo-role", f"{login}:ARBITRATOR"),
            user_id=user_id,
            role_code="ARBITRATOR",
            cooperative_id=None,
            status="ACTIVE",
            granted_by_user_id=stable_id("bootstrap-user", "registrar"),
            approved_by_user_id=stable_id("bootstrap-user", "auditor"),
            approved_at=datetime.now(UTC),
        )
        await session.execute(
            role_statement.on_conflict_do_update(
                index_elements=[RoleAssignment.id],
                set_={"status": "ACTIVE", "cooperative_id": None},
            )
        )
