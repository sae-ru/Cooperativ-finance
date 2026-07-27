from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from cooperative_clearing.modules.identity.application.admin import IdentityAdminService
from cooperative_clearing.modules.identity.application.intake import MemberIntakeService
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    MemberIdentifier,
    MemberImportBatch,
    MemberImportRow,
    UserAccount,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import PasswordService, private_value_hash
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database


def principal(user_id: UUID, role: RoleCode, cooperative_id: UUID) -> Principal:
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        login=f"intake-{user_id}",
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
async def test_staging_import_requires_independent_review_and_applies_only_ready_rows() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(service_name=f"member-intake-{suffix}")
    database = Database.from_settings(settings)
    passwords = PasswordService()
    cooperative_id = uuid4()
    existing_member_id = uuid4()
    registrar_id = uuid4()
    steward_id = uuid4()
    registrar = principal(registrar_id, RoleCode.MEMBER_REGISTRAR, cooperative_id)
    steward = principal(steward_id, RoleCode.DATA_STEWARD, cooperative_id)
    service = MemberIntakeService()
    ready_identifier = f"ready-{suffix}"
    existing_identifier = f"existing-{suffix}"
    csv_text = (
        "display_name,identifier_type,identifier_value\n"
        f"Ready Person,EXTERNAL_REFERENCE,{ready_identifier}\n"
        f"Anna Existing,EXTERNAL_REFERENCE,{existing_identifier}\n"
        "X,,\n"
        "Batch Name,,\n"
        "batch   name,,\n"
    )

    try:
        async with database.session() as session:
            session.add_all(
                [
                    Cooperative(
                        id=cooperative_id,
                        code=f"intake-{suffix}",
                        name="Member intake cooperative",
                        status="ACTIVE",
                    ),
                    Member(
                        id=existing_member_id,
                        display_name="Anna Existing",
                        registered_by_cooperative_id=cooperative_id,
                        status="ACTIVE",
                    ),
                    UserAccount(
                        id=registrar_id,
                        login=f"intake-registrar-{suffix}",
                        password_hash=passwords.hash("intake-registrar-password"),
                        member_id=None,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                    UserAccount(
                        id=steward_id,
                        login=f"intake-steward-{suffix}",
                        password_hash=passwords.hash("intake-steward-password"),
                        member_id=None,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                ]
            )
            session.add(
                MemberIdentifier(
                    id=uuid4(),
                    member_id=existing_member_id,
                    identifier_type="EXTERNAL_REFERENCE",
                    value_hash=private_value_hash(existing_identifier),
                )
            )
            await session.commit()

        stage_key = str(uuid4())
        async with database.session() as session:
            staged = await service.stage_import(
                session,
                principal=registrar,
                cooperative_id=cooperative_id,
                source_name="members.csv",
                csv_text=csv_text,
                idempotency_key=stage_key,
                request_id=uuid4(),
            )
            batch_id = staged.object_id
            await session.commit()
        async with database.session() as session:
            replayed = await service.stage_import(
                session,
                principal=registrar,
                cooperative_id=cooperative_id,
                source_name="members.csv",
                csv_text=csv_text,
                idempotency_key=stage_key,
                request_id=uuid4(),
            )
            assert replayed.replayed is True and replayed.object_id == batch_id

        async with database.session() as session:
            await service.preview_import(
                session,
                principal=registrar,
                batch_id=batch_id,
                expected_version=1,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            batch = await session.get(MemberImportBatch, batch_id)
            assert batch is not None
            assert (batch.ready_count, batch.invalid_count, batch.duplicate_count) == (1, 1, 3)
            rows = list(
                (
                    await session.execute(
                        select(MemberImportRow)
                        .where(MemberImportRow.batch_id == batch_id)
                        .order_by(MemberImportRow.row_number)
                    )
                ).scalars()
            )
            stored_hashes = {row.identifier_hash for row in rows if row.identifier_hash}
            assert ready_identifier not in stored_hashes
            assert rows[1].candidate_member_id == existing_member_id
            assert rows[2].error_code == "IMPORT_ROW_NAME_INVALID"

        async with database.session() as session:
            with pytest.raises(DomainError) as self_review:
                await service.decide_import(
                    session,
                    principal=registrar,
                    batch_id=batch_id,
                    approve=True,
                    reason_code="REVIEWED",
                    expected_version=2,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            assert self_review.value.code == "MEMBER_IMPORT_INDEPENDENT_REVIEW_REQUIRED"
            await session.rollback()

        async with database.session() as session:
            await service.decide_import(
                session,
                principal=steward,
                batch_id=batch_id,
                approve=True,
                reason_code="INDEPENDENT_REVIEW",
                expected_version=2,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            await service.apply_import(
                session,
                principal=registrar,
                batch_id=batch_id,
                expected_version=3,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            batch = await session.get(MemberImportBatch, batch_id)
            created = list(
                (
                    await session.execute(
                        select(Member).where(
                            Member.registered_by_cooperative_id == cooperative_id,
                            Member.display_name == "Ready Person",
                        )
                    )
                ).scalars()
            )
            assert batch is not None and batch.status == "APPLIED"
            assert batch.applied_count == 1 and len(created) == 1
    finally:
        await database.dispose()

@pytest.mark.integration
async def test_import_apply_rejects_a_stale_preview_without_partial_creation() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(service_name=f"member-intake-stale-{suffix}")
    database = Database.from_settings(settings)
    passwords = PasswordService()
    cooperative_id = uuid4()
    registrar_id = uuid4()
    steward_id = uuid4()
    registrar = principal(registrar_id, RoleCode.MEMBER_REGISTRAR, cooperative_id)
    steward = principal(steward_id, RoleCode.DATA_STEWARD, cooperative_id)
    service = MemberIntakeService()
    conflict_identifier = f"late-conflict-{suffix}"

    try:
        async with database.session() as session:
            session.add_all(
                [
                    Cooperative(
                        id=cooperative_id,
                        code=f"stale-{suffix}",
                        name="Stale preview cooperative",
                        status="ACTIVE",
                    ),
                    UserAccount(
                        id=registrar_id,
                        login=f"stale-registrar-{suffix}",
                        password_hash=passwords.hash("stale-registrar-password"),
                        member_id=None,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                    UserAccount(
                        id=steward_id,
                        login=f"stale-steward-{suffix}",
                        password_hash=passwords.hash("stale-steward-password"),
                        member_id=None,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                ]
            )
            await session.commit()

        async with database.session() as session:
            staged = await service.stage_import(
                session,
                principal=registrar,
                cooperative_id=cooperative_id,
                source_name="stale.csv",
                csv_text=(
                    "display_name,identifier_type,identifier_value\n"
                    f"Late Conflict,EXTERNAL_REFERENCE,{conflict_identifier}\n"
                ),
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            batch_id = staged.object_id
            await session.commit()
        async with database.session() as session:
            await service.preview_import(
                session,
                principal=registrar,
                batch_id=batch_id,
                expected_version=1,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            await service.decide_import(
                session,
                principal=steward,
                batch_id=batch_id,
                approve=True,
                reason_code="INDEPENDENT_REVIEW",
                expected_version=2,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        conflicting_member_id = uuid4()
        async with database.session() as session:
            session.add(
                Member(
                    id=conflicting_member_id,
                    display_name="Different person",
                    registered_by_cooperative_id=cooperative_id,
                    status="ACTIVE",
                )
            )
            session.add(
                MemberIdentifier(
                    id=uuid4(),
                    member_id=conflicting_member_id,
                    identifier_type="EXTERNAL_REFERENCE",
                    value_hash=private_value_hash(conflict_identifier),
                )
            )
            await session.commit()

        async with database.session() as session:
            with pytest.raises(DomainError) as stale:
                await service.apply_import(
                    session,
                    principal=registrar,
                    batch_id=batch_id,
                    expected_version=3,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            assert stale.value.code == "MEMBER_IMPORT_PREVIEW_STALE"
            await session.rollback()
        async with database.session() as session:
            batch = await session.get(MemberImportBatch, batch_id)
            created = list(
                (
                    await session.execute(
                        select(Member).where(
                            Member.registered_by_cooperative_id == cooperative_id,
                            Member.display_name == "Late Conflict",
                        )
                    )
                ).scalars()
            )
            assert batch is not None and batch.status == "APPROVED"
            assert batch.applied_count == 0 and created == []
    finally:
        await database.dispose()

@pytest.mark.integration
async def test_manual_member_creation_never_silently_ignores_name_duplicates() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(service_name=f"member-duplicate-{suffix}")
    database = Database.from_settings(settings)
    passwords = PasswordService()
    cooperative_id = uuid4()
    existing_id = uuid4()
    registrar_id = uuid4()
    registrar = principal(registrar_id, RoleCode.MEMBER_REGISTRAR, cooperative_id)
    service = IdentityAdminService(passwords)

    try:
        async with database.session() as session:
            session.add_all(
                [
                    Cooperative(
                        id=cooperative_id,
                        code=f"duplicate-{suffix}",
                        name="Duplicate review cooperative",
                        status="ACTIVE",
                    ),
                    Member(
                        id=existing_id,
                        display_name="Maria Farmer",
                        registered_by_cooperative_id=cooperative_id,
                        status="ACTIVE",
                    ),
                    UserAccount(
                        id=registrar_id,
                        login=f"duplicate-registrar-{suffix}",
                        password_hash=passwords.hash("duplicate-registrar-password"),
                        member_id=None,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                ]
            )
            await session.commit()

        async with database.session() as session:
            with pytest.raises(DomainError) as duplicate:
                await service.create_member(
                    session,
                    principal=registrar,
                    cooperative_id=cooperative_id,
                    display_name=" maria   farmer ",
                    identifier_type=None,
                    identifier_value=None,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            assert duplicate.value.code == "MEMBER_DUPLICATE_REVIEW_REQUIRED"
            await session.rollback()

        async with database.session() as session:
            created = await service.create_member(
                session,
                principal=registrar,
                cooperative_id=cooperative_id,
                display_name="Maria Farmer",
                identifier_type=None,
                identifier_value=None,
                duplicate_resolution_code="DISTINCT_PERSON_CONFIRMED",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        assert created.object_id != existing_id
    finally:
        await database.dispose()