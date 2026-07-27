"""Conservative, dual-control merging of confirmed duplicate members."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    MemberMergeCaseStatus,
    MemberStatus,
    Principal,
    RoleCode,
    RoleGrantSource,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    MemberIdentifier,
    MemberMergeCase,
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

REQUEST_ROLES = frozenset({RoleCode.DATA_STEWARD, RoleCode.MEMBER_REGISTRAR})
REVIEW_ROLES = frozenset({RoleCode.SECURITY_ADMIN})
READ_ROLES = frozenset(
    {
        RoleCode.DATA_STEWARD,
        RoleCode.MEMBER_REGISTRAR,
        RoleCode.SECURITY_ADMIN,
        RoleCode.AUDITOR,
    }
)
CASE_TTL = timedelta(hours=24)
REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,199}")
REASON_PATTERN = re.compile(r"[A-Z0-9_.-]{2,100}")
INELIGIBLE_MEMBER_STATUSES = frozenset(
    {
        MemberStatus.MERGED.value,
        MemberStatus.REJECTED.value,
        MemberStatus.EXITED.value,
        MemberStatus.EXIT_PENDING.value,
        MemberStatus.DECEASED_OR_INCAPACITATED.value,
        MemberStatus.SUCCESSION_REVIEW.value,
        MemberStatus.CLOSED.value,
    }
)


@dataclass(frozen=True, slots=True)
class MemberMergeCommandResult:
    event_id: UUID
    object_id: UUID
    status: str
    replayed: bool = False


def normalize_evidence_refs(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(item.strip() for item in values if item.strip()))
    if not 1 <= len(normalized) <= 10 or any(
        REFERENCE_PATTERN.fullmatch(item) is None for item in normalized
    ):
        raise _error("MEMBER_MERGE_EVIDENCE_INVALID", 422)
    return normalized


def _normalize_reason(value: str) -> str:
    normalized = value.strip().upper()
    if REASON_PATTERN.fullmatch(normalized) is None:
        raise _error("MEMBER_MERGE_REASON_INVALID", 422)
    return normalized


async def member_merge_blockers(
    session: AsyncSession,
    *,
    source_member_id: UUID,
    survivor_member_id: UUID,
) -> dict[str, object]:
    raw_references = (
        await session.execute(
            text("SELECT identity.member_merge_external_blockers(:member_id)"),
            {"member_id": source_member_id},
        )
    ).scalar_one()
    references = dict(raw_references or {})
    codes: set[str] = set()

    account_member_ids = set(
        (
            await session.execute(
                select(UserAccount.member_id).where(
                    UserAccount.member_id.in_([source_member_id, survivor_member_id])
                )
            )
        ).scalars()
    )
    if source_member_id in account_member_ids and survivor_member_id in account_member_ids:
        codes.add("IDENTITY_ACCOUNT_CONFLICT")

    source_cooperatives = set(
        (
            await session.execute(
                select(Membership.cooperative_id).where(Membership.member_id == source_member_id)
            )
        ).scalars()
    )
    survivor_cooperatives = set(
        (
            await session.execute(
                select(Membership.cooperative_id).where(Membership.member_id == survivor_member_id)
            )
        ).scalars()
    )
    if source_cooperatives & survivor_cooperatives:
        codes.add("IDENTITY_MEMBERSHIP_CONFLICT")

    source_addresses = list(
        (
            await session.execute(
                select(
                    ParticipantAddress.cooperative_id,
                    func.lower(ParticipantAddress.label),
                    ParticipantAddress.is_default_pickup,
                    ParticipantAddress.is_default_delivery,
                ).where(
                    ParticipantAddress.member_id == source_member_id,
                    ParticipantAddress.status == "ACTIVE",
                )
            )
        ).tuples()
    )
    survivor_addresses = list(
        (
            await session.execute(
                select(
                    ParticipantAddress.cooperative_id,
                    func.lower(ParticipantAddress.label),
                    ParticipantAddress.is_default_pickup,
                    ParticipantAddress.is_default_delivery,
                ).where(
                    ParticipantAddress.member_id == survivor_member_id,
                    ParticipantAddress.status == "ACTIVE",
                )
            )
        ).tuples()
    )
    source_labels = {(item[0], item[1]) for item in source_addresses}
    survivor_labels = {(item[0], item[1]) for item in survivor_addresses}
    if source_labels & survivor_labels:
        codes.add("IDENTITY_ADDRESS_CONFLICT")
    if any(item[2] for item in source_addresses) and any(item[2] for item in survivor_addresses):
        codes.add("IDENTITY_DEFAULT_PICKUP_CONFLICT")
    if any(item[3] for item in source_addresses) and any(item[3] for item in survivor_addresses):
        codes.add("IDENTITY_DEFAULT_DELIVERY_CONFLICT")
    return {"codes": sorted(codes), "references": references}


def has_member_merge_blockers(summary: dict[str, object]) -> bool:
    return bool(summary.get("codes") or summary.get("references"))


class MemberMergeService:
    def __init__(self, settings: Settings) -> None:
        self.journal = SignedJournalService(settings)

    async def request_merge(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        source_member_id: UUID,
        survivor_member_id: UUID,
        source_expected_version: int,
        survivor_expected_version: int,
        evidence_refs: list[str] | tuple[str, ...],
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> MemberMergeCommandResult:
        self._require_role(principal, REQUEST_ROLES, cooperative_id)
        normalized_evidence = normalize_evidence_refs(evidence_refs)
        reason = _normalize_reason(reason_code)
        payload = {
            "cooperative_id": cooperative_id,
            "source_member_id": source_member_id,
            "survivor_member_id": survivor_member_id,
            "source_expected_version": source_expected_version,
            "survivor_expected_version": survivor_expected_version,
            "evidence_refs": normalized_evidence,
            "reason_code": reason,
        }
        record, replay = await self._begin(
            session, principal, "MEMBER_MERGE_REQUEST", idempotency_key, payload
        )
        if replay is not None:
            return replay
        if source_member_id == survivor_member_id:
            raise _error("MEMBER_MERGE_SAME_MEMBER", 422)
        cooperative = await session.get(Cooperative, cooperative_id)
        if cooperative is None:
            raise _error("COOPERATIVE_NOT_FOUND", 404)
        if cooperative.status != "ACTIVE":
            raise _error("MEMBER_MERGE_COOPERATIVE_INACTIVE", 409)
        source, survivor = await self._locked_members(session, source_member_id, survivor_member_id)
        self._validate_pair(source, survivor, cooperative_id)
        if (
            source.version != source_expected_version
            or survivor.version != survivor_expected_version
        ):
            raise _version_conflict(source.version, survivor.version)

        now = datetime.now(UTC)
        await self._expire_previous_request(
            session,
            principal=principal,
            cooperative_id=cooperative_id,
            source_member_id=source.id,
            now=now,
            request_id=request_id,
        )
        blockers = await member_merge_blockers(
            session,
            source_member_id=source.id,
            survivor_member_id=survivor.id,
        )
        blocked = has_member_merge_blockers(blockers)
        merge_case = MemberMergeCase(
            id=uuid4(),
            cooperative_id=cooperative_id,
            source_member_id=source.id,
            survivor_member_id=survivor.id,
            source_expected_version=source.version,
            survivor_expected_version=survivor.version,
            evidence_refs=list(normalized_evidence),
            reason_code=reason,
            blocker_summary=blockers,
            status=(
                MemberMergeCaseStatus.BLOCKED.value
                if blocked
                else MemberMergeCaseStatus.PENDING_REVIEW.value
            ),
            requested_by_user_id=principal.user_id,
            expires_at=now + CASE_TTL,
        )
        session.add(merge_case)
        event_type = (
            "identity.duplicate_merge_blocked" if blocked else "identity.duplicate_merge_requested"
        )
        event = await self.journal.append(
            session,
            event_type=event_type,
            aggregate_type="member_merge_case",
            aggregate_id=merge_case.id,
            aggregate_version=1,
            actor=self._actor(principal, cooperative_id, REQUEST_ROLES),
            payload={
                "source_member_id": str(source.id),
                "survivor_member_id": str(survivor.id),
                "status": merge_case.status,
                "evidence_refs": list(normalized_evidence),
                "blocker_summary": blockers,
                "expires_at": merge_case.expires_at.isoformat(),
            },
        )
        await AuditRepository(session).record(
            action="MEMBER_MERGE_BLOCKED" if blocked else "MEMBER_MERGE_REQUESTED",
            object_type="MemberMergeCase",
            object_id=merge_case.id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="FAILURE" if blocked else "SUCCESS",
            reason_code=reason,
            request_id=request_id,
            payload={
                "source_member_id": str(source.id),
                "survivor_member_id": str(survivor.id),
                "blocker_summary": blockers,
                "signed_event_id": str(event.event_id),
            },
        )
        return self._complete(record, event.event_id, merge_case.id, merge_case.status)

    async def decide_merge(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        merge_case_id: UUID,
        approve: bool,
        expected_version: int,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> MemberMergeCommandResult:
        reason = _normalize_reason(reason_code)
        record, replay = await self._begin(
            session,
            principal,
            "MEMBER_MERGE_DECISION",
            idempotency_key,
            {
                "merge_case_id": merge_case_id,
                "approve": approve,
                "expected_version": expected_version,
                "reason_code": reason,
            },
        )
        if replay is not None:
            return replay
        merge_case = await session.get(MemberMergeCase, merge_case_id, with_for_update=True)
        if merge_case is None:
            raise _error("MEMBER_MERGE_CASE_NOT_FOUND", 404)
        self._require_role(principal, REVIEW_ROLES, merge_case.cooperative_id)
        if merge_case.requested_by_user_id == principal.user_id:
            raise _error("MEMBER_MERGE_INDEPENDENT_REVIEW_REQUIRED", 409)
        if merge_case.version != expected_version:
            raise _case_version_conflict(merge_case.version)
        if merge_case.status != MemberMergeCaseStatus.PENDING_REVIEW.value:
            raise _error("MEMBER_MERGE_CASE_NOT_PENDING", 409)

        now = datetime.now(UTC)
        event_type = "identity.duplicate_merge_rejected"
        if merge_case.expires_at <= now:
            merge_case.status = MemberMergeCaseStatus.EXPIRED.value
            reason = "REQUEST_EXPIRED"
            event_type = "identity.duplicate_merge_expired"
        elif not approve:
            merge_case.status = MemberMergeCaseStatus.REJECTED.value
        else:
            source, survivor = await self._locked_members(
                session,
                merge_case.source_member_id,
                merge_case.survivor_member_id,
            )
            version_changed = (
                source.version != merge_case.source_expected_version
                or survivor.version != merge_case.survivor_expected_version
            )
            if (
                source.registered_by_cooperative_id != merge_case.cooperative_id
                or survivor.registered_by_cooperative_id != merge_case.cooperative_id
            ):
                raise _error("MEMBER_MERGE_CROSS_COOPERATIVE_UNSUPPORTED", 409)
            status_changed = (
                source.status in INELIGIBLE_MEMBER_STATUSES
                or survivor.status in INELIGIBLE_MEMBER_STATUSES
            )
            blockers = await member_merge_blockers(
                session,
                source_member_id=source.id,
                survivor_member_id=survivor.id,
            )
            if version_changed or status_changed:
                raw_codes = blockers.get("codes")
                blocker_codes = (
                    {str(item) for item in raw_codes} if isinstance(raw_codes, list) else set()
                )
                if version_changed:
                    blocker_codes.add("MEMBER_VERSION_CHANGED")
                if status_changed:
                    blocker_codes.add("MEMBER_STATUS_CHANGED")
                blockers["codes"] = sorted(blocker_codes)
            if has_member_merge_blockers(blockers):
                merge_case.status = MemberMergeCaseStatus.BLOCKED.value
                merge_case.blocker_summary = blockers
                reason = "BLOCKERS_DETECTED_AT_REVIEW"
                event_type = "identity.duplicate_merge_blocked"
            else:
                await self._apply_merge(session, source, survivor, now)
                merge_case.status = MemberMergeCaseStatus.APPROVED.value
                merge_case.blocker_summary = {"codes": [], "references": {}}
                event_type = "identity.duplicate_merge_decided"

        merge_case.decided_by_user_id = principal.user_id
        merge_case.decision_reason_code = reason
        merge_case.decided_at = now
        merge_case.updated_at = now
        merge_case.version += 1
        event = await self.journal.append(
            session,
            event_type=event_type,
            aggregate_type="member_merge_case",
            aggregate_id=merge_case.id,
            aggregate_version=merge_case.version,
            actor=self._actor(principal, merge_case.cooperative_id, REVIEW_ROLES),
            payload={
                "source_member_id": str(merge_case.source_member_id),
                "survivor_member_id": str(merge_case.survivor_member_id),
                "status": merge_case.status,
                "approved": merge_case.status == MemberMergeCaseStatus.APPROVED.value,
                "mapping": {str(merge_case.source_member_id): str(merge_case.survivor_member_id)},
                "blocker_summary": merge_case.blocker_summary,
            },
        )
        await AuditRepository(session).record(
            action=f"MEMBER_MERGE_{merge_case.status}",
            object_type="MemberMergeCase",
            object_id=merge_case.id,
            cooperative_id=merge_case.cooperative_id,
            actor_user_id=principal.user_id,
            outcome=(
                "SUCCESS"
                if merge_case.status
                in {
                    MemberMergeCaseStatus.APPROVED.value,
                    MemberMergeCaseStatus.REJECTED.value,
                }
                else "FAILURE"
            ),
            reason_code=reason,
            request_id=request_id,
            payload={
                "source_member_id": str(merge_case.source_member_id),
                "survivor_member_id": str(merge_case.survivor_member_id),
                "blocker_summary": merge_case.blocker_summary,
                "signed_event_id": str(event.event_id),
            },
        )
        return self._complete(
            record,
            event.event_id,
            merge_case.id,
            merge_case.status,
        )

    async def _expire_previous_request(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        source_member_id: UUID,
        now: datetime,
        request_id: UUID | None,
    ) -> None:
        previous = (
            await session.execute(
                select(MemberMergeCase)
                .where(
                    MemberMergeCase.source_member_id == source_member_id,
                    MemberMergeCase.status == MemberMergeCaseStatus.PENDING_REVIEW.value,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if previous is None:
            return
        if previous.expires_at > now:
            raise _error("MEMBER_MERGE_CASE_ALREADY_PENDING", 409)
        previous.status = MemberMergeCaseStatus.EXPIRED.value
        previous.decision_reason_code = "REQUEST_EXPIRED"
        previous.decided_at = now
        previous.updated_at = now
        previous.version += 1
        event = await self.journal.append(
            session,
            event_type="identity.duplicate_merge_expired",
            aggregate_type="member_merge_case",
            aggregate_id=previous.id,
            aggregate_version=previous.version,
            actor=self._actor(principal, cooperative_id, REQUEST_ROLES),
            payload={
                "source_member_id": str(previous.source_member_id),
                "survivor_member_id": str(previous.survivor_member_id),
                "status": previous.status,
                "superseded_by_new_request": True,
            },
        )
        await AuditRepository(session).record(
            action="MEMBER_MERGE_EXPIRED",
            object_type="MemberMergeCase",
            object_id=previous.id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="FAILURE",
            reason_code="REQUEST_EXPIRED",
            request_id=request_id,
            payload={"signed_event_id": str(event.event_id)},
        )

    @staticmethod
    async def _apply_merge(
        session: AsyncSession,
        source: Member,
        survivor: Member,
        now: datetime,
    ) -> None:
        await session.execute(
            update(MemberIdentifier)
            .where(MemberIdentifier.member_id == source.id)
            .values(member_id=survivor.id)
        )
        await session.execute(
            update(Membership)
            .where(Membership.member_id == source.id)
            .values(
                member_id=survivor.id,
                updated_at=now,
                version=Membership.version + 1,
            )
        )
        await session.execute(
            update(ParticipantAddress)
            .where(ParticipantAddress.member_id == source.id)
            .values(
                member_id=survivor.id,
                updated_at=now,
                version=ParticipantAddress.version + 1,
            )
        )
        await session.execute(
            update(UserAccount)
            .where(UserAccount.member_id == source.id)
            .values(
                member_id=survivor.id,
                updated_at=now,
                version=UserAccount.version + 1,
            )
        )
        source.status = MemberStatus.MERGED.value
        source.merged_into_member_id = survivor.id
        source.updated_at = now
        source.version += 1
        survivor.updated_at = now
        survivor.version += 1

    @staticmethod
    async def _locked_members(
        session: AsyncSession,
        source_member_id: UUID,
        survivor_member_id: UUID,
    ) -> tuple[Member, Member]:
        rows = list(
            (
                await session.execute(
                    select(Member)
                    .where(Member.id.in_([source_member_id, survivor_member_id]))
                    .order_by(Member.id)
                    .with_for_update()
                )
            ).scalars()
        )
        by_id = {member.id: member for member in rows}
        source = by_id.get(source_member_id)
        survivor = by_id.get(survivor_member_id)
        if source is None or survivor is None:
            raise _error("MEMBER_NOT_FOUND", 404)
        return source, survivor

    @staticmethod
    def _validate_pair(source: Member, survivor: Member, cooperative_id: UUID) -> None:
        if (
            source.registered_by_cooperative_id != cooperative_id
            or survivor.registered_by_cooperative_id != cooperative_id
        ):
            raise _error("MEMBER_MERGE_CROSS_COOPERATIVE_UNSUPPORTED", 409)
        if source.status in INELIGIBLE_MEMBER_STATUSES:
            raise _error("MEMBER_MERGE_SOURCE_INELIGIBLE", 409)
        if survivor.status in INELIGIBLE_MEMBER_STATUSES:
            raise _error("MEMBER_MERGE_SURVIVOR_INELIGIBLE", 409)

    @staticmethod
    def _require_role(
        principal: Principal,
        roles: frozenset[RoleCode],
        cooperative_id: UUID,
    ) -> None:
        if principal.must_change_password:
            raise _error("PASSWORD_CHANGE_REQUIRED", 403, "errors.auth.password_change_required")
        if not principal.has_permanent_role(set(roles), cooperative_id):
            raise _error("PERMANENT_MEMBER_MERGE_ROLE_REQUIRED", 403)
        if principal.member_id is None:
            raise _error("PERSONAL_ACTOR_REQUIRED", 403)

    @staticmethod
    def _actor(
        principal: Principal,
        cooperative_id: UUID,
        roles: frozenset[RoleCode],
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
        raise _error("PERMANENT_MEMBER_MERGE_ROLE_REQUIRED", 403)

    @staticmethod
    async def _begin(
        session: AsyncSession,
        principal: Principal,
        operation: str,
        idempotency_key: str,
        payload: object,
    ) -> tuple[IdempotencyRecord, MemberMergeCommandResult | None]:
        record = await IdempotencyRepository(session).begin(
            actor_user_id=principal.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_payload_hash(payload),
        )
        if record.status == "COMPLETED":
            stored = record.response_payload or {}
            return record, MemberMergeCommandResult(
                event_id=UUID(str(stored["event_id"])),
                object_id=UUID(str(stored["object_id"])),
                status=str(stored["status"]),
                replayed=True,
            )
        return record, None

    @staticmethod
    def _complete(
        record: IdempotencyRecord,
        event_id: UUID,
        object_id: UUID,
        status: str,
    ) -> MemberMergeCommandResult:
        IdempotencyRepository.complete(
            record,
            response_status=201,
            response_payload={
                "event_id": str(event_id),
                "object_id": str(object_id),
                "status": status,
            },
        )
        return MemberMergeCommandResult(
            event_id=event_id,
            object_id=object_id,
            status=status,
        )


def _version_conflict(source_version: int, survivor_version: int) -> DomainError:
    return DomainError(
        code="MEMBER_MERGE_VERSION_CONFLICT",
        message_key="errors.request.version_conflict",
        parameters={
            "source_version": source_version,
            "survivor_version": survivor_version,
        },
        status_code=409,
    )


def _case_version_conflict(current_version: int) -> DomainError:
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
