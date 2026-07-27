"""Contained exit and succession review for economically referenced members."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.audit.infrastructure.repository import (
    AuditRepository,
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.identity.domain.types import (
    MemberContinuityCaseStatus,
    MemberContinuityCaseType,
    MembershipStatus,
    MemberStatus,
    Principal,
    RoleCode,
    RoleGrantSource,
    UserStatus,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    AuthSession,
    Cooperative,
    Member,
    MemberContinuityCase,
    MemberIdentifier,
    MemberImportRow,
    Membership,
    ParticipantAddress,
    UserAccount,
)
from cooperative_clearing.modules.journal.application.service import (
    ActorClaim,
    SignedJournalService,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError

REQUEST_ROLES = frozenset({RoleCode.MEMBER_REGISTRAR, RoleCode.COOPERATIVE_ADMIN})
REVIEW_ROLES = frozenset({RoleCode.SECURITY_ADMIN})
READ_ROLES = frozenset(
    {
        RoleCode.MEMBER_REGISTRAR,
        RoleCode.COOPERATIVE_ADMIN,
        RoleCode.SECURITY_ADMIN,
        RoleCode.AUDITOR,
    }
)
ELIGIBLE_MEMBER_STATUSES = frozenset(
    {MemberStatus.ACTIVE.value, MemberStatus.LIMITED.value, MemberStatus.SUSPENDED.value}
)
REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,199}")
REASON_PATTERN = re.compile(r"[A-Z0-9_.-]{2,100}")
REFERENCE_GROUPS = {
    "assets": "assets_rights",
    "exchange": "deals_clearing_logistics",
    "risk": "responsibility_shares",
    "solidarity": "solidarity_crisis",
    "trust": "trust_disputes",
    "federation": "federation",
    "journal": "signed_history",
}


@dataclass(frozen=True, slots=True)
class MemberContinuityCommandResult:
    event_id: UUID
    object_id: UUID
    status: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    id: UUID
    previous_status: str
    contained_version: int


def normalize_evidence_refs(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(item.strip() for item in values if item.strip()))
    if not 1 <= len(normalized) <= 10 or any(
        REFERENCE_PATTERN.fullmatch(item) is None for item in normalized
    ):
        raise _error("MEMBER_CONTINUITY_EVIDENCE_INVALID", 422)
    return normalized


def normalize_reason(value: str) -> str:
    normalized = value.strip().upper()
    if REASON_PATTERN.fullmatch(normalized) is None:
        raise _error("MEMBER_CONTINUITY_REASON_INVALID", 422)
    return normalized


def contained_status(case_type: MemberContinuityCaseType) -> MemberStatus:
    if case_type is MemberContinuityCaseType.VOLUNTARY_EXIT:
        return MemberStatus.EXIT_PENDING
    return MemberStatus.DECEASED_OR_INCAPACITATED


def group_external_references(raw: dict[str, object], identity_count: int) -> dict[str, object]:
    groups: dict[str, int] = {}
    if identity_count:
        groups["identity_registry"] = identity_count
    for reference, raw_count in raw.items():
        try:
            count = int(str(raw_count))
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        schema = reference.partition(".")[0]
        group = REFERENCE_GROUPS.get(schema, "other")
        groups[group] = groups.get(group, 0) + count
    ordered = {key: groups[key] for key in sorted(groups)}
    return {"groups": ordered, "total_references": sum(ordered.values())}


class MemberContinuityService:
    def __init__(self, settings: Settings) -> None:
        self.journal = SignedJournalService(settings)

    async def request_case(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        member_id: UUID,
        case_type: MemberContinuityCaseType,
        expected_member_version: int,
        evidence_refs: list[str] | tuple[str, ...],
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> MemberContinuityCommandResult:
        self._require_role(principal, REQUEST_ROLES, cooperative_id)
        evidence = normalize_evidence_refs(evidence_refs)
        reason = normalize_reason(reason_code)
        payload = {
            "cooperative_id": cooperative_id,
            "member_id": member_id,
            "case_type": case_type.value,
            "expected_member_version": expected_member_version,
            "evidence_refs": evidence,
            "reason_code": reason,
        }
        record, replay = await self._begin(
            session, principal, "MEMBER_CONTINUITY_REQUEST", idempotency_key, payload
        )
        if replay is not None:
            return replay

        cooperative = await session.get(Cooperative, cooperative_id)
        if cooperative is None:
            raise _error("COOPERATIVE_NOT_FOUND", 404)
        if cooperative.status != "ACTIVE":
            raise _error("MEMBER_CONTINUITY_COOPERATIVE_INACTIVE", 409)
        member = await session.get(Member, member_id, with_for_update=True)
        if member is None:
            raise _error("MEMBER_NOT_FOUND", 404)
        if member.registered_by_cooperative_id != cooperative_id:
            raise _error("MEMBER_CONTINUITY_CROSS_COOPERATIVE_UNSUPPORTED", 409)
        if member.version != expected_member_version:
            raise _version_conflict(member.version)
        if member.status not in ELIGIBLE_MEMBER_STATUSES:
            raise _error("MEMBER_CONTINUITY_MEMBER_INELIGIBLE", 409)
        existing = (
            await session.execute(
                select(MemberContinuityCase.id).where(
                    MemberContinuityCase.member_id == member.id,
                    MemberContinuityCase.status == MemberContinuityCaseStatus.PENDING_REVIEW.value,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise _error("MEMBER_CONTINUITY_CASE_ALREADY_PENDING", 409)

        now = datetime.now(UTC)
        users = list(
            (
                await session.execute(
                    select(UserAccount)
                    .where(
                        UserAccount.member_id == member.id,
                        UserAccount.status == UserStatus.ACTIVE.value,
                    )
                    .order_by(UserAccount.id)
                    .with_for_update()
                )
            ).scalars()
        )
        memberships = list(
            (
                await session.execute(
                    select(Membership)
                    .where(
                        Membership.member_id == member.id,
                        Membership.status == MembershipStatus.ACTIVE.value,
                    )
                    .order_by(Membership.id)
                    .with_for_update()
                )
            ).scalars()
        )
        reference_summary = await self._reference_summary(session, member_id=member.id)
        previous_member_status = member.status
        member.status = contained_status(case_type).value
        member.updated_at = now
        member.version += 1

        user_snapshot: list[dict[str, object]] = []
        for user in users:
            user.status = UserStatus.DISABLED.value
            user.updated_at = now
            user.version += 1
            user_snapshot.append(
                {
                    "id": str(user.id),
                    "previous_status": UserStatus.ACTIVE.value,
                    "contained_version": user.version,
                }
            )
        if users:
            await session.execute(
                update(AuthSession)
                .where(
                    AuthSession.user_id.in_([item.id for item in users]),
                    AuthSession.status == "ACTIVE",
                )
                .values(status="REVOKED", revoked_at=now, last_seen_at=now)
            )

        membership_snapshot: list[dict[str, object]] = []
        for membership in memberships:
            membership.status = MembershipStatus.SUSPENDED.value
            membership.updated_at = now
            membership.version += 1
            membership_snapshot.append(
                {
                    "id": str(membership.id),
                    "previous_status": MembershipStatus.ACTIVE.value,
                    "contained_version": membership.version,
                }
            )

        continuity_case = MemberContinuityCase(
            id=uuid4(),
            cooperative_id=cooperative_id,
            member_id=member.id,
            case_type=case_type.value,
            previous_member_status=previous_member_status,
            contained_member_version=member.version,
            access_snapshot={"users": user_snapshot, "memberships": membership_snapshot},
            reference_summary=reference_summary,
            review_blockers=[],
            evidence_refs=list(evidence),
            reason_code=reason,
            status=MemberContinuityCaseStatus.PENDING_REVIEW.value,
            requested_by_user_id=principal.user_id,
        )
        session.add(continuity_case)
        event = await self.journal.append(
            session,
            event_type="identity.member_continuity_requested",
            aggregate_type="member_continuity_case",
            aggregate_id=continuity_case.id,
            aggregate_version=1,
            actor=self._actor(principal, cooperative_id, REQUEST_ROLES),
            payload={
                "member_id": str(member.id),
                "case_type": case_type.value,
                "status": continuity_case.status,
                "contained_member_status": member.status,
                "disabled_user_count": len(user_snapshot),
                "suspended_membership_count": len(membership_snapshot),
                "reference_summary": reference_summary,
                "evidence_refs": list(evidence),
            },
        )
        await AuditRepository(session).record(
            action="MEMBER_CONTINUITY_REQUESTED",
            object_type="MemberContinuityCase",
            object_id=continuity_case.id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            reason_code=reason,
            request_id=request_id,
            payload={
                "member_id": str(member.id),
                "case_type": case_type.value,
                "disabled_user_count": len(user_snapshot),
                "suspended_membership_count": len(membership_snapshot),
                "signed_event_id": str(event.event_id),
            },
        )
        return self._complete(record, event.event_id, continuity_case.id, continuity_case.status)

    async def decide_case(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        continuity_case_id: UUID,
        approve: bool,
        expected_version: int,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> MemberContinuityCommandResult:
        reason = normalize_reason(reason_code)
        record, replay = await self._begin(
            session,
            principal,
            "MEMBER_CONTINUITY_DECISION",
            idempotency_key,
            {
                "continuity_case_id": continuity_case_id,
                "approve": approve,
                "expected_version": expected_version,
                "reason_code": reason,
            },
        )
        if replay is not None:
            return replay
        continuity_case = await session.get(
            MemberContinuityCase, continuity_case_id, with_for_update=True
        )
        if continuity_case is None:
            raise _error("MEMBER_CONTINUITY_CASE_NOT_FOUND", 404)
        self._require_role(principal, REVIEW_ROLES, continuity_case.cooperative_id)
        if continuity_case.requested_by_user_id == principal.user_id:
            raise _error("MEMBER_CONTINUITY_INDEPENDENT_REVIEW_REQUIRED", 409)
        if continuity_case.version != expected_version:
            raise _version_conflict(continuity_case.version)
        if continuity_case.status != MemberContinuityCaseStatus.PENDING_REVIEW.value:
            raise _error("MEMBER_CONTINUITY_CASE_NOT_PENDING", 409)

        member = await session.get(Member, continuity_case.member_id, with_for_update=True)
        users_snapshot = _snapshot_records(continuity_case.access_snapshot, "users")
        memberships_snapshot = _snapshot_records(continuity_case.access_snapshot, "memberships")
        users = await self._locked_users(session, users_snapshot)
        memberships = await self._locked_memberships(session, memberships_snapshot)
        blockers = self._state_blockers(
            continuity_case,
            member,
            users_snapshot,
            users,
            memberships_snapshot,
            memberships,
        )
        now = datetime.now(UTC)
        if blockers:
            continuity_case.status = MemberContinuityCaseStatus.BLOCKED.value
            continuity_case.review_blockers = blockers
            event_type = "identity.member_continuity_blocked"
        elif approve:
            assert member is not None
            if continuity_case.case_type == MemberContinuityCaseType.DEATH_OR_INCAPACITY.value:
                member.status = MemberStatus.SUCCESSION_REVIEW.value
            else:
                member.status = MemberStatus.EXIT_PENDING.value
            member.updated_at = now
            member.version += 1
            continuity_case.status = MemberContinuityCaseStatus.CONFIRMED.value
            continuity_case.review_blockers = []
            event_type = "identity.member_continuity_confirmed"
        else:
            assert member is not None
            member.status = continuity_case.previous_member_status
            member.updated_at = now
            member.version += 1
            for user, snapshot in zip(users, users_snapshot, strict=True):
                user.status = snapshot.previous_status
                user.updated_at = now
                user.version += 1
            for membership, snapshot in zip(memberships, memberships_snapshot, strict=True):
                membership.status = snapshot.previous_status
                membership.updated_at = now
                membership.version += 1
            continuity_case.status = MemberContinuityCaseStatus.REJECTED.value
            continuity_case.review_blockers = []
            event_type = "identity.member_continuity_rejected"

        continuity_case.decided_by_user_id = principal.user_id
        continuity_case.decision_reason_code = reason
        continuity_case.decided_at = now
        continuity_case.updated_at = now
        continuity_case.version += 1
        event = await self.journal.append(
            session,
            event_type=event_type,
            aggregate_type="member_continuity_case",
            aggregate_id=continuity_case.id,
            aggregate_version=continuity_case.version,
            actor=self._actor(principal, continuity_case.cooperative_id, REVIEW_ROLES),
            payload={
                "member_id": str(continuity_case.member_id),
                "case_type": continuity_case.case_type,
                "status": continuity_case.status,
                "approved": approve,
                "review_blockers": blockers,
                "sessions_restored": False,
                "reference_summary": continuity_case.reference_summary,
            },
        )
        await AuditRepository(session).record(
            action=f"MEMBER_CONTINUITY_{continuity_case.status}",
            object_type="MemberContinuityCase",
            object_id=continuity_case.id,
            cooperative_id=continuity_case.cooperative_id,
            actor_user_id=principal.user_id,
            outcome=(
                "FAILURE"
                if continuity_case.status == MemberContinuityCaseStatus.BLOCKED.value
                else "SUCCESS"
            ),
            reason_code=reason,
            request_id=request_id,
            payload={
                "member_id": str(continuity_case.member_id),
                "case_type": continuity_case.case_type,
                "review_blockers": blockers,
                "signed_event_id": str(event.event_id),
            },
        )
        return self._complete(record, event.event_id, continuity_case.id, continuity_case.status)

    @staticmethod
    async def _reference_summary(
        session: AsyncSession,
        *,
        member_id: UUID,
    ) -> dict[str, object]:
        raw = (
            await session.execute(
                text("SELECT identity.member_merge_external_blockers(:member_id)"),
                {"member_id": member_id},
            )
        ).scalar_one()
        user_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(UserAccount)
                    .where(UserAccount.member_id == member_id)
                )
            ).scalar_one()
        )
        membership_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Membership)
                    .where(Membership.member_id == member_id)
                )
            ).scalar_one()
        )
        identifier_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(MemberIdentifier)
                    .where(MemberIdentifier.member_id == member_id)
                )
            ).scalar_one()
        )
        address_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ParticipantAddress)
                    .where(ParticipantAddress.member_id == member_id)
                )
            ).scalar_one()
        )
        import_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(MemberImportRow)
                    .where(
                        (MemberImportRow.candidate_member_id == member_id)
                        | (MemberImportRow.created_member_id == member_id)
                    )
                )
            ).scalar_one()
        )
        identity_count = (
            user_count + membership_count + identifier_count + address_count + import_count
        )
        return group_external_references(dict(raw or {}), identity_count)

    @staticmethod
    async def _locked_users(
        session: AsyncSession, snapshot: list[SnapshotRecord]
    ) -> list[UserAccount]:
        if not snapshot:
            return []
        rows = list(
            (
                await session.execute(
                    select(UserAccount)
                    .where(UserAccount.id.in_([item.id for item in snapshot]))
                    .order_by(UserAccount.id)
                    .with_for_update()
                )
            ).scalars()
        )
        return rows

    @staticmethod
    async def _locked_memberships(
        session: AsyncSession, snapshot: list[SnapshotRecord]
    ) -> list[Membership]:
        if not snapshot:
            return []
        rows = list(
            (
                await session.execute(
                    select(Membership)
                    .where(Membership.id.in_([item.id for item in snapshot]))
                    .order_by(Membership.id)
                    .with_for_update()
                )
            ).scalars()
        )
        return rows

    @staticmethod
    def _state_blockers(
        continuity_case: MemberContinuityCase,
        member: Member | None,
        users_snapshot: list[SnapshotRecord],
        users: list[UserAccount],
        memberships_snapshot: list[SnapshotRecord],
        memberships: list[Membership],
    ) -> list[str]:
        blockers: set[str] = set()
        expected_status = contained_status(
            MemberContinuityCaseType(continuity_case.case_type)
        ).value
        if member is None:
            blockers.add("MEMBER_MISSING")
        elif member.version != continuity_case.contained_member_version:
            blockers.add("MEMBER_VERSION_CHANGED")
        elif member.status != expected_status:
            blockers.add("MEMBER_STATUS_CHANGED")
        blockers.update(_record_blockers("USER", users_snapshot, users, UserStatus.DISABLED.value))
        blockers.update(
            _record_blockers(
                "MEMBERSHIP",
                memberships_snapshot,
                memberships,
                MembershipStatus.SUSPENDED.value,
            )
        )
        return sorted(blockers)

    @staticmethod
    def _require_role(
        principal: Principal, roles: frozenset[RoleCode], cooperative_id: UUID
    ) -> None:
        if principal.must_change_password:
            raise _error("PASSWORD_CHANGE_REQUIRED", 403, "errors.auth.password_change_required")
        if not principal.has_permanent_role(set(roles), cooperative_id):
            raise _error("PERMANENT_MEMBER_CONTINUITY_ROLE_REQUIRED", 403)
        if principal.member_id is None:
            raise _error("PERSONAL_ACTOR_REQUIRED", 403)

    @staticmethod
    def _actor(
        principal: Principal, cooperative_id: UUID, roles: frozenset[RoleCode]
    ) -> ActorClaim:
        if principal.member_id is None:
            raise _error("PERSONAL_ACTOR_REQUIRED", 403)
        for grant in principal.roles:
            if (
                grant.source is RoleGrantSource.ASSIGNMENT
                and grant.role in roles
                and grant.cooperative_id in {None, cooperative_id}
            ):
                return ActorClaim(
                    person_id=principal.member_id,
                    organization_id=cooperative_id,
                    role_assignment_id=grant.assignment_id,
                )
        raise _error("PERMANENT_MEMBER_CONTINUITY_ROLE_REQUIRED", 403)

    @staticmethod
    async def _begin(
        session: AsyncSession,
        principal: Principal,
        operation: str,
        idempotency_key: str,
        payload: object,
    ) -> tuple[IdempotencyRecord, MemberContinuityCommandResult | None]:
        record = await IdempotencyRepository(session).begin(
            actor_user_id=principal.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_payload_hash(payload),
        )
        if record.status == "COMPLETED":
            stored = record.response_payload or {}
            return record, MemberContinuityCommandResult(
                event_id=UUID(str(stored["event_id"])),
                object_id=UUID(str(stored["object_id"])),
                status=str(stored["status"]),
                replayed=True,
            )
        return record, None

    @staticmethod
    def _complete(
        record: IdempotencyRecord, event_id: UUID, object_id: UUID, status: str
    ) -> MemberContinuityCommandResult:
        IdempotencyRepository.complete(
            record,
            response_status=201,
            response_payload={
                "event_id": str(event_id),
                "object_id": str(object_id),
                "status": status,
            },
        )
        return MemberContinuityCommandResult(event_id=event_id, object_id=object_id, status=status)


def _snapshot_records(snapshot: dict[str, object], key: str) -> list[SnapshotRecord]:
    raw_records = snapshot.get(key)
    if not isinstance(raw_records, list):
        raise _error("MEMBER_CONTINUITY_SNAPSHOT_INVALID", 409)
    records: list[SnapshotRecord] = []
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise _error("MEMBER_CONTINUITY_SNAPSHOT_INVALID", 409)
        try:
            record_id = UUID(str(raw["id"]))
            previous_status = str(raw["previous_status"])
            contained_version = int(raw["contained_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise _error("MEMBER_CONTINUITY_SNAPSHOT_INVALID", 409) from exc
        records.append(
            SnapshotRecord(
                id=record_id,
                previous_status=previous_status,
                contained_version=contained_version,
            )
        )
    return sorted(records, key=lambda item: item.id)


def _record_blockers(
    prefix: str,
    snapshot: list[SnapshotRecord],
    rows: list[UserAccount] | list[Membership],
    expected_status: str,
) -> set[str]:
    blockers: set[str] = set()
    by_id = {item.id: item for item in rows}
    if len(by_id) != len(snapshot):
        blockers.add(f"{prefix}_MISSING")
    for expected in snapshot:
        current = by_id.get(expected.id)
        if current is None:
            continue
        if current.version != expected.contained_version:
            blockers.add(f"{prefix}_VERSION_CHANGED")
        if current.status != expected_status:
            blockers.add(f"{prefix}_STATUS_CHANGED")
    return blockers


def _version_conflict(current_version: int) -> DomainError:
    return DomainError(
        code="VERSION_CONFLICT",
        message_key="errors.request.version_conflict",
        parameters={"current_version": current_version},
        status_code=409,
    )


def _error(code: str, status_code: int, message_key: str | None = None) -> DomainError:
    return DomainError(
        code=code,
        message_key=message_key or f"errors.identity.{code.lower()}",
        status_code=status_code,
    )
