"""One-time separated-duty identity bootstrap and deterministic demo fixtures."""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.repository import (
    AuditRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    MemberImportBatch,
    MemberImportRow,
    MemberMergeCase,
    Membership,
    ParticipantAddress,
    RoleAssignment,
    ServiceClient,
    ServiceClientCredential,
    ServiceClientRequest,
    UserAccount,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.secrets import read_text_secret
from cooperative_clearing.shared.core.security import PasswordService, token_hash

BOOTSTRAP_LOGINS = ("registrar", "security", "auditor")
DEMO_MEMBER_LOGIN = "farmer"
DEMO_MEMBER_PASSWORD = "CoopDemo-Farmer-2026!"


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
        ("demo-member-ivan", "Ivan Milkman", "ACTIVE", "D-0007"),
    )
    for key, display_name, status, member_number in members:
        member_id = stable_id("member", key)
        member_statement = insert(Member).values(
            id=member_id,
            display_name=display_name,
            registered_by_cooperative_id=cooperative_id,
            status=status,
        )
        await session.execute(
            member_statement.on_conflict_do_update(
                index_elements=[Member.id],
                set_={
                    "display_name": member_statement.excluded.display_name,
                    "registered_by_cooperative_id": cooperative_id,
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

    duplicate_member_id = stable_id("member", "demo-member-anna-duplicate")
    duplicate_member = insert(Member).values(
        id=duplicate_member_id,
        display_name="Anna Petrova (duplicate record)",
        registered_by_cooperative_id=cooperative_id,
        status="PENDING_VERIFICATION",
    )
    await session.execute(duplicate_member.on_conflict_do_nothing(index_elements=[Member.id]))
    demo_merge_case = insert(MemberMergeCase).values(
        id=stable_id("member-merge-case", "demo-anna-duplicate"),
        cooperative_id=cooperative_id,
        source_member_id=duplicate_member_id,
        survivor_member_id=stable_id("member", "demo-member-anna"),
        source_expected_version=1,
        survivor_expected_version=1,
        evidence_refs=["case:demo-duplicate-anna", f"sha256:{'d' * 64}"],
        reason_code="DEMO_CONFIRMED_DUPLICATE",
        blocker_summary={"codes": [], "references": {}},
        status="PENDING_REVIEW",
        requested_by_user_id=stable_id("bootstrap-user", "registrar"),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    await session.execute(
        demo_merge_case.on_conflict_do_nothing(index_elements=[MemberMergeCase.id])
    )

    demo_member_user_id = stable_id("demo-user", DEMO_MEMBER_LOGIN)
    demo_member = insert(UserAccount).values(
        id=demo_member_user_id,
        login=DEMO_MEMBER_LOGIN,
        password_hash=PasswordService().hash(DEMO_MEMBER_PASSWORD),
        member_id=stable_id("member", "demo-member-ivan"),
        status="ACTIVE",
        must_change_password=False,
    )
    await session.execute(demo_member.on_conflict_do_nothing(index_elements=[UserAccount.id]))
    demo_member_role = insert(RoleAssignment).values(
        id=stable_id("demo-role", f"{DEMO_MEMBER_LOGIN}:EXCHANGE_PARTICIPANT"),
        user_id=demo_member_user_id,
        role_code="EXCHANGE_PARTICIPANT",
        cooperative_id=cooperative_id,
        status="ACTIVE",
        granted_by_user_id=stable_id("bootstrap-user", "registrar"),
        approved_by_user_id=stable_id("bootstrap-user", "auditor"),
        approved_at=datetime.now(UTC),
    )
    await session.execute(
        demo_member_role.on_conflict_do_update(
            index_elements=[RoleAssignment.id],
            set_={"status": "ACTIVE", "cooperative_id": cooperative_id},
        )
    )

    demo_addresses = (
        (
            "farm",
            "Ферма",
            "BOTH",
            "EAST-DISTRICT",
            "Тверская область, деревня Берёзовка, Ферма 7",
            "Иван",
            "+7 900 555-01-07",
            "Въезд через зелёные ворота, позвонить за 30 минут",
            True,
            True,
        ),
        (
            "home",
            "Дом",
            "DELIVERY",
            "EAST-DISTRICT",
            "Тверская область, деревня Берёзовка, дом 12",
            "Иван",
            "+7 900 555-01-07",
            "Оставить у крыльца только после звонка",  # noqa: RUF001
            False,
            False,
        ),
        (
            "warehouse",
            "Склад",
            "PICKUP",
            "EAST-DISTRICT",
            "Тверская область, посёлок Восточный, Склад 3",
            "Иван",
            "+7 900 555-01-07",
            "Погрузка со стороны рампы",  # noqa: RUF001
            False,
            False,
        ),
    )
    for (
        key,
        label,
        purpose,
        region_code,
        address_text,
        contact_name,
        contact_phone,
        instructions,
        is_default_pickup,
        is_default_delivery,
    ) in demo_addresses:
        address_statement = insert(ParticipantAddress).values(
            id=stable_id("participant-address", f"{DEMO_MEMBER_LOGIN}:{key}"),
            member_id=stable_id("member", "demo-member-ivan"),
            cooperative_id=cooperative_id,
            label=label,
            purpose=purpose,
            region_code=region_code,
            address_text=address_text,
            contact_name=contact_name,
            contact_phone=contact_phone,
            instructions=instructions,
            is_default_pickup=is_default_pickup,
            is_default_delivery=is_default_delivery,
            status="ACTIVE",
        )
        await session.execute(
            address_statement.on_conflict_do_update(
                index_elements=[ParticipantAddress.id],
                set_={
                    "label": address_statement.excluded.label,
                    "purpose": address_statement.excluded.purpose,
                    "region_code": address_statement.excluded.region_code,
                    "address_text": address_statement.excluded.address_text,
                    "contact_name": address_statement.excluded.contact_name,
                    "contact_phone": address_statement.excluded.contact_phone,
                    "instructions": address_statement.excluded.instructions,
                    "is_default_pickup": address_statement.excluded.is_default_pickup,
                    "is_default_delivery": address_statement.excluded.is_default_delivery,
                    "status": "ACTIVE",
                    "updated_at": datetime.now(UTC),
                },
            )
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
    now = datetime.now(UTC)
    service_client_id = stable_id("service-client", "demo-catalog-bridge")
    service_credential_id = stable_id("service-client-credential", "demo-catalog-bridge:initial")
    demo_secret = f"ccs_{service_credential_id.hex}_demoServiceCredentialForLocalTestingOnly2026ABC"
    service_client = insert(ServiceClient).values(
        id=service_client_id,
        client_code="svc_demo_catalog_bridge",
        owner_cooperative_id=cooperative_id,
        display_name="Demo catalog bridge",
        technical_contact_name="Demo integration operator",
        technical_contact_email="integration@example.test",
        scopes=["catalog:read", "clearing:accounting:read"],
        network_allowlist=["127.0.0.1/32"],
        rate_limit_per_minute=60,
        status="ACTIVE",
        expires_at=now + timedelta(days=180),
        registered_by_user_id=stable_id("bootstrap-user", "registrar"),
        approved_by_user_id=stable_id("bootstrap-user", "security"),
    )
    await session.execute(service_client.on_conflict_do_nothing(index_elements=[ServiceClient.id]))
    service_credential = insert(ServiceClientCredential).values(
        id=service_credential_id,
        service_client_id=service_client_id,
        secret_hash=token_hash(demo_secret),
        secret_prefix=demo_secret[:24],
        status="ACTIVE",
        issued_by_user_id=stable_id("bootstrap-user", "security"),
        created_at=now,
        expires_at=now + timedelta(days=180),
    )
    await session.execute(
        service_credential.on_conflict_do_nothing(index_elements=[ServiceClientCredential.id])
    )
    service_request = insert(ServiceClientRequest).values(
        id=stable_id("service-client-request", "demo-catalog-bridge:rotate"),
        service_client_id=service_client_id,
        owner_cooperative_id=cooperative_id,
        operation="ROTATE",
        proposed_config=None,
        expected_client_version=1,
        reason_code="DEMO_SECRET_ROTATION",
        status="PENDING",
        requested_by_user_id=stable_id("bootstrap-user", "registrar"),
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    await session.execute(
        service_request.on_conflict_do_nothing(index_elements=[ServiceClientRequest.id])
    )

    import_batch_id = stable_id("member-import-batch", "demo-intake")
    import_rows = (
        (1, "Sofia Green", "READY", None, None, None),
        (
            2,
            "Anna Petrova",
            "DUPLICATE",
            "DUPLICATE_EXISTING_NAME",
            "NORMALIZED_NAME",
            stable_id("member", "demo-member-anna"),
        ),
        (3, "X", "INVALID", "IMPORT_ROW_NAME_INVALID", None, None),
    )
    source_sha256 = request_payload_hash(
        [{"row_number": row[0], "display_name": row[1]} for row in import_rows]
    )
    import_batch = insert(MemberImportBatch).values(
        id=import_batch_id,
        cooperative_id=cooperative_id,
        source_name="demo-member-intake.csv",
        source_sha256=source_sha256,
        status="PREVIEWED",
        row_count=len(import_rows),
        ready_count=1,
        invalid_count=1,
        duplicate_count=1,
        applied_count=0,
        created_by_user_id=stable_id("bootstrap-user", "registrar"),
        previewed_at=datetime.now(UTC),
    )
    await session.execute(
        import_batch.on_conflict_do_nothing(index_elements=[MemberImportBatch.id])
    )
    for row_number, display_name, status, error_code, match_basis, candidate_id in import_rows:
        import_row = insert(MemberImportRow).values(
            id=stable_id("member-import-row", f"demo-intake:{row_number}"),
            batch_id=import_batch_id,
            row_number=row_number,
            display_name=display_name,
            identifier_type=None,
            identifier_hash=None,
            source_row_hash=request_payload_hash(
                {"row_number": row_number, "display_name": display_name}
            ),
            status=status,
            error_code=error_code,
            match_basis=match_basis,
            candidate_member_id=candidate_id,
        )
        await session.execute(
            import_row.on_conflict_do_nothing(index_elements=[MemberImportRow.id])
        )
