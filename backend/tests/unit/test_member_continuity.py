from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.api.member_continuity import _case_view, _scope
from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.identity.application.member_continuity import (
    REQUEST_ROLES,
    MemberContinuityCommandResult,
    MemberContinuityService,
    SnapshotRecord,
    _record_blockers,
    _snapshot_records,
    _version_conflict,
    contained_status,
    group_external_references,
    normalize_evidence_refs,
    normalize_reason,
)
from cooperative_clearing.modules.identity.domain.types import (
    MemberContinuityCaseType,
    MembershipStatus,
    MemberStatus,
    Principal,
    RoleCode,
    RoleGrant,
    RoleGrantSource,
    UserStatus,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    MemberContinuityCase,
    Membership,
    UserAccount,
)
from cooperative_clearing.shared.domain.errors import DomainError


def principal(
    role: RoleCode = RoleCode.MEMBER_REGISTRAR,
    *,
    cooperative_id: UUID | None = None,
    personal: bool = True,
    source: RoleGrantSource = RoleGrantSource.ASSIGNMENT,
    must_change_password: bool = False,
) -> Principal:
    return Principal(
        user_id=uuid4(),
        session_id=uuid4(),
        login="continuity-operator",
        member_id=uuid4() if personal else None,
        must_change_password=must_change_password,
        roles=(
            RoleGrant(
                assignment_id=uuid4(),
                role=role,
                cooperative_id=cooperative_id,
                source=source,
            ),
        ),
    )


def continuity_case(**changes: object) -> MemberContinuityCase:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "id": uuid4(),
        "cooperative_id": uuid4(),
        "member_id": uuid4(),
        "case_type": MemberContinuityCaseType.VOLUNTARY_EXIT.value,
        "previous_member_status": MemberStatus.ACTIVE.value,
        "contained_member_version": 2,
        "access_snapshot": {"users": [], "memberships": []},
        "reference_summary": {"groups": {}, "total_references": 0},
        "review_blockers": [],
        "evidence_refs": ["case:test-1"],
        "reason_code": "MEMBER_REQUEST_RECEIVED",
        "status": "PENDING_REVIEW",
        "requested_by_user_id": uuid4(),
        "decided_by_user_id": None,
        "decision_reason_code": None,
        "created_at": now,
        "decided_at": None,
        "updated_at": now,
        "version": 1,
    }
    values.update(changes)
    return MemberContinuityCase(**values)


def test_contained_status_is_case_specific() -> None:
    assert contained_status(MemberContinuityCaseType.VOLUNTARY_EXIT) is MemberStatus.EXIT_PENDING
    assert (
        contained_status(MemberContinuityCaseType.DEATH_OR_INCAPACITY)
        is MemberStatus.DECEASED_OR_INCAPACITATED
    )


def test_reference_summary_groups_storage_names() -> None:
    summary = group_external_references(
        {
            "assets.lots.owner_member_id": 2,
            "exchange.deals.buyer_member_id": 3,
            "journal.signed_events.actor_person_id": 5,
            "unexpected.table.member_id": 7,
        },
        identity_count=4,
    )

    assert summary == {
        "groups": {
            "assets_rights": 2,
            "deals_clearing_logistics": 3,
            "identity_registry": 4,
            "other": 7,
            "signed_history": 5,
        },
        "total_references": 21,
    }
    groups = summary["groups"]
    assert isinstance(groups, dict)
    assert "." not in "".join(str(key) for key in groups)


def test_reference_summary_ignores_invalid_and_empty_counts() -> None:
    summary = group_external_references(
        {
            "assets.zero": 0,
            "exchange.negative": -2,
            "risk.invalid": object(),
            "solidarity.text": "4",
        },
        identity_count=0,
    )

    assert summary == {
        "groups": {"solidarity_crisis": 4},
        "total_references": 4,
    }


def test_continuity_input_normalization_is_bounded() -> None:
    assert normalize_evidence_refs([" case:exit-1 ", "case:exit-1"]) == ("case:exit-1",)
    assert normalize_reason(" board_confirmed ") == "BOARD_CONFIRMED"

    with pytest.raises(DomainError) as evidence_error:
        normalize_evidence_refs([str(uuid4()), "contains spaces"])
    assert evidence_error.value.code == "MEMBER_CONTINUITY_EVIDENCE_INVALID"

    with pytest.raises(DomainError) as empty_evidence:
        normalize_evidence_refs([])
    assert empty_evidence.value.code == "MEMBER_CONTINUITY_EVIDENCE_INVALID"

    with pytest.raises(DomainError) as reason_error:
        normalize_reason("free form reason")
    assert reason_error.value.code == "MEMBER_CONTINUITY_REASON_INVALID"


@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"users": "not-a-list"},
        {"users": ["not-an-object"]},
        {"users": [{}]},
        {"users": [{"id": "not-a-uuid", "previous_status": "ACTIVE", "contained_version": 2}]},
    ],
)
def test_snapshot_parser_fails_closed(snapshot: dict[str, object]) -> None:
    with pytest.raises(DomainError) as failure:
        _snapshot_records(snapshot, "users")
    assert failure.value.code == "MEMBER_CONTINUITY_SNAPSHOT_INVALID"


def test_snapshot_parser_sorts_valid_records() -> None:
    later = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    earlier = UUID("00000000-0000-0000-0000-000000000001")
    records = _snapshot_records(
        {
            "users": [
                {
                    "id": str(later),
                    "previous_status": UserStatus.ACTIVE.value,
                    "contained_version": 3,
                },
                {
                    "id": str(earlier),
                    "previous_status": UserStatus.ACTIVE.value,
                    "contained_version": 2,
                },
            ]
        },
        "users",
    )
    assert [item.id for item in records] == [earlier, later]


def test_record_and_member_state_blockers_are_exhaustive() -> None:
    cooperative_id = uuid4()
    member_id = uuid4()
    user_id = uuid4()
    missing_user_id = uuid4()
    membership_id = uuid4()
    missing_membership_id = uuid4()
    case = continuity_case(
        cooperative_id=cooperative_id,
        member_id=member_id,
        contained_member_version=2,
    )
    member = Member(
        id=member_id,
        display_name="Contained member",
        registered_by_cooperative_id=cooperative_id,
        status=MemberStatus.EXIT_PENDING.value,
        version=2,
    )
    user_snapshot = [
        SnapshotRecord(user_id, UserStatus.ACTIVE.value, 2),
        SnapshotRecord(missing_user_id, UserStatus.ACTIVE.value, 2),
    ]
    membership_snapshot = [
        SnapshotRecord(membership_id, MembershipStatus.ACTIVE.value, 2),
        SnapshotRecord(missing_membership_id, MembershipStatus.ACTIVE.value, 2),
    ]
    user = UserAccount(
        id=user_id,
        login="changed-user",
        password_hash="hash",
        member_id=member_id,
        status=UserStatus.ACTIVE.value,
        must_change_password=False,
        version=3,
    )
    membership = Membership(
        id=membership_id,
        cooperative_id=cooperative_id,
        member_id=member_id,
        member_number="M-1",
        status=MembershipStatus.ACTIVE.value,
        version=3,
    )

    assert MemberContinuityService._state_blockers(
        case,
        member,
        user_snapshot,
        [user],
        membership_snapshot,
        [membership],
    ) == [
        "MEMBERSHIP_MISSING",
        "MEMBERSHIP_STATUS_CHANGED",
        "MEMBERSHIP_VERSION_CHANGED",
        "USER_MISSING",
        "USER_STATUS_CHANGED",
        "USER_VERSION_CHANGED",
    ]
    assert MemberContinuityService._state_blockers(case, None, [], [], [], []) == [
        "MEMBER_MISSING"
    ]
    member.version = 3
    assert MemberContinuityService._state_blockers(case, member, [], [], [], []) == [
        "MEMBER_VERSION_CHANGED"
    ]
    member.version = 2
    member.status = MemberStatus.SUSPENDED.value
    assert MemberContinuityService._state_blockers(case, member, [], [], [], []) == [
        "MEMBER_STATUS_CHANGED"
    ]


def test_record_blockers_accept_an_exact_snapshot() -> None:
    user_id = uuid4()
    snapshot = [SnapshotRecord(user_id, UserStatus.ACTIVE.value, 2)]
    user = UserAccount(
        id=user_id,
        login="exact-user",
        password_hash="hash",
        status=UserStatus.DISABLED.value,
        must_change_password=False,
        version=2,
    )
    assert _record_blockers("USER", snapshot, [user], UserStatus.DISABLED.value) == set()


def test_continuity_roles_require_permanent_personal_actor() -> None:
    cooperative_id = uuid4()
    actor = principal(cooperative_id=cooperative_id)
    MemberContinuityService._require_role(actor, REQUEST_ROLES, cooperative_id)
    claim = MemberContinuityService._actor(actor, cooperative_id, REQUEST_ROLES)
    assert claim.person_id == actor.member_id
    assert claim.organization_id == cooperative_id
    assert claim.role_assignment_id == actor.roles[0].assignment_id

    with pytest.raises(DomainError) as password_change:
        MemberContinuityService._require_role(
            principal(cooperative_id=cooperative_id, must_change_password=True),
            REQUEST_ROLES,
            cooperative_id,
        )
    assert password_change.value.code == "PASSWORD_CHANGE_REQUIRED"

    with pytest.raises(DomainError) as temporary:
        MemberContinuityService._require_role(
            principal(
                cooperative_id=cooperative_id,
                source=RoleGrantSource.BREAK_GLASS,
            ),
            REQUEST_ROLES,
            cooperative_id,
        )
    assert temporary.value.code == "PERMANENT_MEMBER_CONTINUITY_ROLE_REQUIRED"

    technical = principal(cooperative_id=cooperative_id, personal=False)
    with pytest.raises(DomainError) as personal:
        MemberContinuityService._require_role(technical, REQUEST_ROLES, cooperative_id)
    assert personal.value.code == "PERSONAL_ACTOR_REQUIRED"
    with pytest.raises(DomainError) as actor_personal:
        MemberContinuityService._actor(technical, cooperative_id, REQUEST_ROLES)
    assert actor_personal.value.code == "PERSONAL_ACTOR_REQUIRED"

    wrong_role = principal(RoleCode.AUDITOR, cooperative_id=cooperative_id)
    with pytest.raises(DomainError) as actor_role:
        MemberContinuityService._actor(wrong_role, cooperative_id, REQUEST_ROLES)
    assert actor_role.value.code == "PERMANENT_MEMBER_CONTINUITY_ROLE_REQUIRED"


def test_continuity_list_scope_is_explicit() -> None:
    cooperative_id = uuid4()
    assert _scope(principal(cooperative_id=cooperative_id)) == {cooperative_id}
    assert _scope(principal(RoleCode.SECURITY_ADMIN, cooperative_id=None)) is None

    with pytest.raises(DomainError) as password_change:
        _scope(principal(cooperative_id=cooperative_id, must_change_password=True))
    assert password_change.value.code == "AUTHORIZATION_DENIED"

    with pytest.raises(DomainError) as no_scope:
        _scope(principal(RoleCode.EXCHANGE_PARTICIPANT, cooperative_id=cooperative_id))
    assert no_scope.value.code == "AUTHORIZATION_DENIED"


def test_case_view_counts_only_valid_snapshot_lists() -> None:
    case = continuity_case(
        access_snapshot={
            "users": [{"id": str(uuid4())}],
            "memberships": "corrupt",
        }
    )
    view = _case_view(case)
    assert view.disabled_user_count == 1
    assert view.suspended_membership_count == 0


@pytest.mark.asyncio
async def test_empty_snapshot_does_not_query_access_rows() -> None:
    empty_session = cast(AsyncSession, SimpleNamespace())
    assert await MemberContinuityService._locked_users(empty_session, []) == []
    assert await MemberContinuityService._locked_memberships(empty_session, []) == []


@pytest.mark.asyncio
async def test_reference_summary_counts_all_identity_links() -> None:
    class ScalarResult:
        def __init__(self, value: object) -> None:
            self.value = value

        def scalar_one(self) -> object:
            return self.value

    class FakeSession:
        def __init__(self) -> None:
            self.results = iter(
                [
                    {"assets.lots.owner_member_id": 2},
                    1,
                    2,
                    3,
                    4,
                    5,
                ]
            )

        async def execute(self, *_args: object, **_kwargs: object) -> ScalarResult:
            return ScalarResult(next(self.results))

    summary = await MemberContinuityService._reference_summary(
        cast(AsyncSession, FakeSession()),
        member_id=uuid4(),
    )
    assert summary == {
        "groups": {"assets_rights": 2, "identity_registry": 15},
        "total_references": 17,
    }


@pytest.mark.asyncio
async def test_idempotency_begin_replays_completed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    event_id = uuid4()
    object_id = uuid4()
    record = IdempotencyRecord(
        id=uuid4(),
        actor_user_id=uuid4(),
        operation="MEMBER_CONTINUITY_REQUEST",
        idempotency_key="continuity-test-key",
        request_hash="a" * 64,
        status="COMPLETED",
        response_status=201,
        response_payload={
            "event_id": str(event_id),
            "object_id": str(object_id),
            "status": "PENDING_REVIEW",
        },
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    class FakeRepository:
        def __init__(self, _session: object) -> None:
            pass

        async def begin(self, **_kwargs: object) -> IdempotencyRecord:
            return record

    monkeypatch.setattr(
        "cooperative_clearing.modules.identity.application.member_continuity.IdempotencyRepository",
        FakeRepository,
    )
    _, replay = await MemberContinuityService._begin(
        cast(AsyncSession, SimpleNamespace()),
        principal(),
        "MEMBER_CONTINUITY_REQUEST",
        "continuity-test-key",
        {"member_id": str(object_id)},
    )
    assert replay == MemberContinuityCommandResult(
        event_id=event_id,
        object_id=object_id,
        status="PENDING_REVIEW",
        replayed=True,
    )


def test_complete_and_version_conflict_keep_machine_details() -> None:
    record = IdempotencyRecord(
        id=uuid4(),
        actor_user_id=uuid4(),
        operation="MEMBER_CONTINUITY_DECISION",
        idempotency_key="continuity-complete-key",
        request_hash="b" * 64,
        status="PROCESSING",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    event_id = uuid4()
    object_id = uuid4()
    result = MemberContinuityService._complete(record, event_id, object_id, "CONFIRMED")
    assert result == MemberContinuityCommandResult(event_id, object_id, "CONFIRMED")
    assert record.status == "COMPLETED"
    assert record.response_status == 201

    conflict = _version_conflict(7)
    assert conflict.code == "VERSION_CONFLICT"
    assert conflict.parameters == {"current_version": 7}