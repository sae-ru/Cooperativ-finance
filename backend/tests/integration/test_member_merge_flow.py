from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from cooperative_clearing.main import create_app
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    MemberIdentifier,
    MemberMergeCase,
    Membership,
    ParticipantAddress,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import PasswordService
from cooperative_clearing.shared.infrastructure.database import Database

PASSWORD = "Member-merge-test-2026!"


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
async def test_member_merge_moves_identity_and_preserves_source_history() -> None:
    suffix = uuid4().hex[:12]
    member_identifier_hash = sha256(suffix.encode("ascii")).hexdigest()
    pickup_address = f"Test pickup road {suffix}"
    settings = Settings(service_name=f"member-merge-{suffix}")
    database = Database.from_settings(settings)
    cooperative_id = uuid4()
    requester_member_id = uuid4()
    reviewer_member_id = uuid4()
    source_member_id = uuid4()
    survivor_member_id = uuid4()
    requester_user_id = uuid4()
    reviewer_user_id = uuid4()
    survivor_user_id = uuid4()
    requester_login = f"merge-steward-{suffix}"
    reviewer_login = f"merge-security-{suffix}"
    try:
        async with database.session() as session:
            passwords = PasswordService()
            session.add(
                Cooperative(
                    id=cooperative_id,
                    code=f"merge-{suffix}",
                    name="Duplicate merge cooperative",
                    status="ACTIVE",
                )
            )
            session.add_all(
                [
                    Member(
                        id=requester_member_id,
                        display_name="Identity steward",
                        registered_by_cooperative_id=cooperative_id,
                        status="ACTIVE",
                    ),
                    Member(
                        id=reviewer_member_id,
                        display_name="Security reviewer",
                        registered_by_cooperative_id=cooperative_id,
                        status="ACTIVE",
                    ),
                    Member(
                        id=source_member_id,
                        display_name="Farm duplicate",
                        registered_by_cooperative_id=cooperative_id,
                        status="LIMITED",
                    ),
                    Member(
                        id=survivor_member_id,
                        display_name="Farm verified",
                        registered_by_cooperative_id=cooperative_id,
                        status="ACTIVE",
                    ),
                ]
            )
            session.add_all(
                [
                    UserAccount(
                        id=requester_user_id,
                        login=requester_login,
                        password_hash=passwords.hash(PASSWORD),
                        member_id=requester_member_id,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                    UserAccount(
                        id=reviewer_user_id,
                        login=reviewer_login,
                        password_hash=passwords.hash(PASSWORD),
                        member_id=reviewer_member_id,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                    UserAccount(
                        id=survivor_user_id,
                        login=f"merge-survivor-{suffix}",
                        password_hash=passwords.hash(PASSWORD),
                        member_id=survivor_member_id,
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
                        role_code="DATA_STEWARD",
                        cooperative_id=cooperative_id,
                        status="ACTIVE",
                        approved_by_user_id=reviewer_user_id,
                        approved_at=datetime.now(UTC),
                    ),
                    RoleAssignment(
                        id=uuid4(),
                        user_id=requester_user_id,
                        role_code="SECURITY_ADMIN",
                        cooperative_id=cooperative_id,
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
                    MemberIdentifier(
                        id=uuid4(),
                        member_id=source_member_id,
                        identifier_type="MEMBERSHIP_LEGACY",
                        value_hash=member_identifier_hash,
                    ),
                    Membership(
                        id=uuid4(),
                        cooperative_id=cooperative_id,
                        member_id=source_member_id,
                        member_number=f"OLD-{suffix}",
                        status="ACTIVE",
                        joined_at=datetime.now(UTC),
                    ),
                    ParticipantAddress(
                        id=uuid4(),
                        member_id=source_member_id,
                        cooperative_id=cooperative_id,
                        label="Main farm",
                        purpose="BOTH",
                        region_code="test-region",
                        address_text=pickup_address,
                        contact_name="Farm contact",
                        contact_phone="+10000000000",
                        is_default_pickup=True,
                        is_default_delivery=True,
                        status="ACTIVE",
                    ),
                ]
            )
            await session.commit()
    finally:
        await database.dispose()

    with TestClient(create_app(settings), client=("127.0.0.1", 45100)) as client:
        requester_headers = login(client, requester_login)
        reviewer_headers = login(client, reviewer_login)
        enable_step_up(client, requester_headers)
        enable_step_up(client, reviewer_headers)
        payload = {
            "cooperative_id": str(cooperative_id),
            "source_member_id": str(source_member_id),
            "survivor_member_id": str(survivor_member_id),
            "source_expected_version": 1,
            "survivor_expected_version": 1,
            "evidence_refs": [f"case:duplicate-{suffix}", f"sha256:{'b' * 64}"],
            "reason_code": "CONFIRMED_DUPLICATE",
        }
        idempotency_key = str(uuid4())
        created = client.post(
            "/api/v1/admin/member-merge-cases",
            headers={**requester_headers, "Idempotency-Key": idempotency_key},
            json=payload,
        )
        assert created.status_code == 201, created.text
        assert created.json()["data"]["status"] == "PENDING_REVIEW"
        merge_case_id = created.json()["data"]["object_id"]

        replay = client.post(
            "/api/v1/admin/member-merge-cases",
            headers={**requester_headers, "Idempotency-Key": idempotency_key},
            json=payload,
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["data"]["replayed"] is True
        assert replay.json()["data"]["object_id"] == merge_case_id

        own_review = client.post(
            f"/api/v1/admin/member-merge-cases/{merge_case_id}/decision",
            headers={**requester_headers, "Idempotency-Key": str(uuid4())},
            json={"approve": True, "expected_version": 1, "reason_code": "SELF_REVIEW"},
        )
        assert own_review.status_code == 409, own_review.text
        assert own_review.json()["error"]["code"] == "MEMBER_MERGE_INDEPENDENT_REVIEW_REQUIRED"

        approved = client.post(
            f"/api/v1/admin/member-merge-cases/{merge_case_id}/decision",
            headers={**reviewer_headers, "Idempotency-Key": str(uuid4())},
            json={
                "approve": True,
                "expected_version": 1,
                "reason_code": "INDEPENDENT_SECURITY_REVIEW",
            },
        )
        assert approved.status_code == 201, approved.text
        assert approved.json()["data"]["status"] == "APPROVED"

        listed = client.get("/api/v1/admin/member-merge-cases", headers=reviewer_headers)
        assert listed.status_code == 200, listed.text
        stored_view = next(item for item in listed.json()["data"] if item["id"] == merge_case_id)
        assert stored_view["status"] == "APPROVED"
        assert stored_view["decided_by_user_id"] == str(reviewer_user_id)

    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            source = await session.get(Member, source_member_id)
            survivor = await session.get(Member, survivor_member_id)
            merge_case = await session.get(MemberMergeCase, UUID(merge_case_id))
            assert source is not None and source.status == "MERGED"
            assert source.merged_into_member_id == survivor_member_id
            assert source.version == 2
            assert survivor is not None and survivor.version == 2
            assert merge_case is not None and merge_case.status == "APPROVED"
            assert (
                await session.execute(
                    select(MemberIdentifier.member_id).where(
                        MemberIdentifier.identifier_type == "MEMBERSHIP_LEGACY",
                        MemberIdentifier.value_hash == member_identifier_hash,
                    )
                )
            ).scalar_one() == survivor_member_id
            assert (
                await session.execute(
                    select(Membership.member_id).where(Membership.member_number == f"OLD-{suffix}")
                )
            ).scalar_one() == survivor_member_id
            assert (
                await session.execute(
                    select(ParticipantAddress.member_id).where(
                        ParticipantAddress.address_text == pickup_address
                    )
                )
            ).scalar_one() == survivor_member_id
            assert (
                await session.execute(
                    select(UserAccount.member_id).where(UserAccount.id == survivor_user_id)
                )
            ).scalar_one() == survivor_member_id
            event_types = set(
                (
                    await session.execute(
                        select(SignedEvent.event_type).where(
                            SignedEvent.aggregate_id == UUID(merge_case_id)
                        )
                    )
                ).scalars()
            )
            assert {
                "identity.duplicate_merge_requested",
                "identity.duplicate_merge_decided",
            }.issubset(event_types)
            requester_blockers = (
                await session.execute(
                    text("SELECT identity.member_merge_external_blockers(:member_id)"),
                    {"member_id": requester_member_id},
                )
            ).scalar_one()
            assert requester_blockers["journal.signed_events.actor_person_id"] >= 1
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_member_merge_blocks_conflicting_accounts_and_default_addresses() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(service_name=f"member-merge-blocked-{suffix}")
    database = Database.from_settings(settings)
    cooperative_id = uuid4()
    steward_member_id = uuid4()
    steward_user_id = uuid4()
    source_member_id = uuid4()
    survivor_member_id = uuid4()
    steward_login = f"blocked-steward-{suffix}"
    try:
        async with database.session() as session:
            passwords = PasswordService()
            session.add(
                Cooperative(
                    id=cooperative_id,
                    code=f"blocked-{suffix}",
                    name="Blocked merge cooperative",
                    status="ACTIVE",
                )
            )
            session.add_all(
                [
                    Member(
                        id=steward_member_id,
                        display_name="Blocked case steward",
                        registered_by_cooperative_id=cooperative_id,
                        status="ACTIVE",
                    ),
                    Member(
                        id=source_member_id,
                        display_name="Duplicate with login",
                        registered_by_cooperative_id=cooperative_id,
                        status="ACTIVE",
                    ),
                    Member(
                        id=survivor_member_id,
                        display_name="Survivor with login",
                        registered_by_cooperative_id=cooperative_id,
                        status="ACTIVE",
                    ),
                ]
            )
            session.add_all(
                [
                    UserAccount(
                        id=steward_user_id,
                        login=steward_login,
                        password_hash=passwords.hash(PASSWORD),
                        member_id=steward_member_id,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                    UserAccount(
                        id=uuid4(),
                        login=f"blocked-source-{suffix}",
                        password_hash=passwords.hash(PASSWORD),
                        member_id=source_member_id,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                    UserAccount(
                        id=uuid4(),
                        login=f"blocked-survivor-{suffix}",
                        password_hash=passwords.hash(PASSWORD),
                        member_id=survivor_member_id,
                        status="ACTIVE",
                        must_change_password=False,
                    ),
                ]
            )
            session.add_all(
                [
                    ParticipantAddress(
                        id=uuid4(),
                        member_id=source_member_id,
                        cooperative_id=cooperative_id,
                        label="Source farm",
                        purpose="BOTH",
                        region_code="test-region",
                        address_text=f"Source pickup road {suffix}",
                        contact_name="Source contact",
                        contact_phone="+10000000001",
                        is_default_pickup=True,
                        is_default_delivery=True,
                        status="ACTIVE",
                    ),
                    ParticipantAddress(
                        id=uuid4(),
                        member_id=survivor_member_id,
                        cooperative_id=cooperative_id,
                        label="Survivor farm",
                        purpose="BOTH",
                        region_code="test-region",
                        address_text=f"Survivor pickup road {suffix}",
                        contact_name="Survivor contact",
                        contact_phone="+10000000002",
                        is_default_pickup=True,
                        is_default_delivery=True,
                        status="ACTIVE",
                    ),
                ]
            )
            await session.flush()
            session.add(
                RoleAssignment(
                    id=uuid4(),
                    user_id=steward_user_id,
                    role_code="DATA_STEWARD",
                    cooperative_id=cooperative_id,
                    status="ACTIVE",
                    approved_by_user_id=steward_user_id,
                    approved_at=datetime.now(UTC),
                )
            )
            await session.commit()
    finally:
        await database.dispose()

    with TestClient(create_app(settings), client=("127.0.0.1", 45110)) as client:
        headers = login(client, steward_login)
        response = client.post(
            "/api/v1/admin/member-merge-cases",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "cooperative_id": str(cooperative_id),
                "source_member_id": str(source_member_id),
                "survivor_member_id": str(survivor_member_id),
                "source_expected_version": 1,
                "survivor_expected_version": 1,
                "evidence_refs": [f"case:duplicate-{suffix}"],
                "reason_code": "CONFIRMED_DUPLICATE",
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["data"]["status"] == "BLOCKED"
        merge_case_id = response.json()["data"]["object_id"]

    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            merge_case = await session.get(MemberMergeCase, UUID(merge_case_id))
            assert merge_case is not None
            assert merge_case.blocker_summary["codes"] == [
                "IDENTITY_ACCOUNT_CONFLICT",
                "IDENTITY_DEFAULT_DELIVERY_CONFLICT",
                "IDENTITY_DEFAULT_PICKUP_CONFLICT",
            ]
            source = await session.get(Member, source_member_id)
            assert source is not None and source.status == "ACTIVE"
            assert source.merged_into_member_id is None
    finally:
        await database.dispose()
