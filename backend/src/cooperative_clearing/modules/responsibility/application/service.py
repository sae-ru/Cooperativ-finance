"""Commands for proposed, independently approved, and personally accepted responsibility."""

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.audit.infrastructure.repository import (
    AuditRepository,
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.journal.application.service import (
    ActorClaim,
    SignedJournalService,
)
from cooperative_clearing.modules.journal.domain.crypto import (
    canonicalize,
    sha256_ref,
    utc_timestamp,
)
from cooperative_clearing.modules.responsibility.domain.types import (
    ApprovalDecision,
    ResponsibilityStatus,
    ensure_can_accept,
    ensure_can_decide,
)
from cooperative_clearing.modules.responsibility.infrastructure.models import (
    ResponsibilityApproval,
    ResponsibilityAssignment,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError

PROPOSER_ROLES = {RoleCode.RISK_ADMIN, RoleCode.COOPERATIVE_ADMIN}
APPROVER_ROLES = {RoleCode.RISK_ADMIN, RoleCode.AUDITOR}


@dataclass(frozen=True, slots=True)
class ResponsibilityCommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class CanonicalPreview:
    profile: str
    canonical_json: str
    summary_hash: str


class ResponsibilityService:
    def __init__(
        self,
        settings: Settings,
        journal: SignedJournalService | None = None,
    ) -> None:
        self.journal = journal or SignedJournalService(settings)

    async def propose(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        member_id: UUID,
        role_assignment_id: UUID,
        subject_type: str,
        subject_id: UUID,
        scope: str,
        max_exposure: Decimal,
        exposure_unit: str,
        valid_until: datetime | None,
        expected_summary_hash: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ResponsibilityCommandResult:
        subject_type = _bounded_text(subject_type, "SUBJECT_TYPE_INVALID", 80)
        scope = _bounded_text(scope, "RESPONSIBILITY_SCOPE_INVALID", 200)
        exposure_unit = _bounded_text(exposure_unit, "EXPOSURE_UNIT_INVALID", 32).upper()
        normalized_exposure = _exposure(max_exposure)
        now = datetime.now(UTC)
        if valid_until is not None and valid_until.astimezone(UTC) <= now:
            raise _error("RESPONSIBILITY_EXPIRY_INVALID", 422)
        command = assignment_summary(
            cooperative_id=cooperative_id,
            member_id=member_id,
            role_assignment_id=role_assignment_id,
            subject_type=subject_type,
            subject_id=subject_id,
            scope=scope,
            max_exposure=normalized_exposure,
            exposure_unit=exposure_unit,
            valid_until=valid_until,
        )
        preview = canonical_preview(command)
        if not hmac.compare_digest(preview.summary_hash, expected_summary_hash):
            raise _error("CANONICAL_SUMMARY_CHANGED", 409)
        record, replay = await self._begin(
            session, principal, "RESPONSIBILITY_PROPOSE", idempotency_key, command
        )
        if replay is not None:
            return replay

        cooperative = await session.get(Cooperative, cooperative_id)
        member = await session.get(Member, member_id)
        role = await session.get(RoleAssignment, role_assignment_id)
        if cooperative is None or cooperative.status != "ACTIVE":
            raise _error("COOPERATIVE_NOT_ACTIVE", 409)
        if member is None:
            raise _error("MEMBER_NOT_FOUND", 404)
        if member.status not in {"ACTIVE", "LIMITED"}:
            raise _error("RESPONSIBLE_MEMBER_NOT_ELIGIBLE", 409)
        if role is None or role.status != "ACTIVE":
            raise _error("RESPONSIBLE_ROLE_NOT_ACTIVE", 409)
        if role.cooperative_id not in {None, cooperative_id}:
            raise _error("RESPONSIBLE_ROLE_SCOPE_MISMATCH", 409)
        target_user = await session.get(UserAccount, role.user_id)
        if target_user is None or target_user.status != "ACTIVE":
            raise _error("RESPONSIBLE_USER_NOT_ACTIVE", 409)
        if target_user.member_id != member_id:
            raise _error("RESPONSIBLE_ROLE_MEMBER_MISMATCH", 409)

        actor = _actor_claim(principal, cooperative_id, PROPOSER_ROLES)
        assignment_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="responsibility.assignment_started",
            aggregate_type="responsibility_assignment",
            aggregate_id=assignment_id,
            aggregate_version=1,
            actor=actor,
            payload={**command, "status": ResponsibilityStatus.PENDING_APPROVAL.value},
        )
        session.add(
            ResponsibilityAssignment(
                id=assignment_id,
                cooperative_id=cooperative_id,
                member_id=member_id,
                role_assignment_id=role_assignment_id,
                subject_type=subject_type,
                subject_id=subject_id,
                scope=scope,
                max_exposure=normalized_exposure,
                exposure_unit=exposure_unit,
                valid_from=now,
                valid_until=valid_until,
                status=ResponsibilityStatus.PENDING_APPROVAL.value,
                created_by_user_id=principal.user_id,
                created_event_id=event.event_id,
            )
        )
        await AuditRepository(session).record(
            action="RESPONSIBILITY_PROPOSED",
            object_type="ResponsibilityAssignment",
            object_id=assignment_id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={
                "subject_type": subject_type,
                "subject_id": str(subject_id),
                "target_member_id": str(member_id),
                "signed_event_id": str(event.event_id),
            },
        )
        return self._complete(record, event.event_id, assignment_id)

    async def decide(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        assignment_id: UUID,
        decision: ApprovalDecision,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ResponsibilityCommandResult:
        reason_code = _bounded_text(reason_code, "REASON_CODE_INVALID", 100).upper()
        payload = {
            "assignment_id": str(assignment_id),
            "decision": decision.value,
            "reason_code": reason_code,
        }
        record, replay = await self._begin(
            session, principal, "RESPONSIBILITY_DECIDE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        assignment = await session.get(
            ResponsibilityAssignment, assignment_id, with_for_update=True
        )
        if assignment is None:
            raise _error("RESPONSIBILITY_NOT_FOUND", 404)
        ensure_can_decide(ResponsibilityStatus(assignment.status))
        target_role = await session.get(RoleAssignment, assignment.role_assignment_id)
        if target_role is None:
            raise _error("RESPONSIBLE_ROLE_NOT_FOUND", 409)
        if principal.user_id in {assignment.created_by_user_id, target_role.user_id}:
            raise _error("INDEPENDENT_APPROVER_REQUIRED", 403)

        actor = _actor_claim(principal, assignment.cooperative_id, APPROVER_ROLES)
        approved = decision is ApprovalDecision.APPROVE
        event_type = (
            "responsibility.assignment_approved"
            if approved
            else "responsibility.assignment_rejected"
        )
        event = await self.journal.append(
            session,
            event_type=event_type,
            aggregate_type="responsibility_assignment",
            aggregate_id=assignment.id,
            aggregate_version=assignment.version + 1,
            actor=actor,
            payload={
                **payload,
                "target_member_id": str(assignment.member_id),
                "target_role_assignment_id": str(assignment.role_assignment_id),
            },
        )
        decided_at = datetime.now(UTC)
        assignment.status = (
            ResponsibilityStatus.PENDING_ACCEPTANCE.value
            if approved
            else ResponsibilityStatus.REJECTED.value
        )
        assignment.approved_by_user_id = principal.user_id
        assignment.approved_at = decided_at
        assignment.approved_event_id = event.event_id
        assignment.version += 1
        session.add(
            ResponsibilityApproval(
                id=uuid4(),
                assignment_id=assignment.id,
                decision=decision.value,
                reason_code=reason_code,
                decided_by_user_id=principal.user_id,
                event_id=event.event_id,
                decided_at=decided_at,
            )
        )
        await AuditRepository(session).record(
            action="RESPONSIBILITY_APPROVED" if approved else "RESPONSIBILITY_REJECTED",
            object_type="ResponsibilityAssignment",
            object_id=assignment.id,
            cooperative_id=assignment.cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            reason_code=reason_code,
            request_id=request_id,
            payload={"signed_event_id": str(event.event_id)},
        )
        return self._complete(record, event.event_id, assignment.id)

    async def accept(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        assignment_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ResponsibilityCommandResult:
        payload = {"assignment_id": str(assignment_id), "expected_version": expected_version}
        record, replay = await self._begin(
            session, principal, "RESPONSIBILITY_ACCEPT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        assignment = await session.get(
            ResponsibilityAssignment, assignment_id, with_for_update=True
        )
        if assignment is None:
            raise _error("RESPONSIBILITY_NOT_FOUND", 404)
        ensure_can_accept(ResponsibilityStatus(assignment.status))
        if assignment.version != expected_version:
            raise DomainError(
                code="VERSION_CONFLICT",
                message_key="errors.request.version_conflict",
                parameters={"current_version": assignment.version},
                status_code=409,
            )
        target_role = await session.get(RoleAssignment, assignment.role_assignment_id)
        if target_role is None or target_role.status != "ACTIVE":
            raise _error("RESPONSIBLE_ROLE_NOT_ACTIVE", 409)
        if target_role.user_id != principal.user_id or principal.member_id != assignment.member_id:
            raise _error("RESPONSIBILITY_TARGET_REQUIRED", 403)
        if principal.must_change_password:
            raise _error("PASSWORD_CHANGE_REQUIRED", 403)
        actor = _actor_claim(
            principal,
            assignment.cooperative_id,
            {RoleCode(target_role.role_code)},
            exact_assignment_id=target_role.id,
        )
        event = await self.journal.append(
            session,
            event_type="responsibility.assignment_accepted",
            aggregate_type="responsibility_assignment",
            aggregate_id=assignment.id,
            aggregate_version=assignment.version + 1,
            actor=actor,
            payload={
                "assignment_id": str(assignment.id),
                "subject_type": assignment.subject_type,
                "subject_id": str(assignment.subject_id),
                "scope": assignment.scope,
                "max_exposure": _decimal_text(assignment.max_exposure),
                "exposure_unit": assignment.exposure_unit,
                "accepted_by_member_id": str(assignment.member_id),
            },
        )
        accepted_at = datetime.now(UTC)
        assignment.status = ResponsibilityStatus.ACTIVE.value
        assignment.accepted_by_user_id = principal.user_id
        assignment.accepted_at = accepted_at
        assignment.accepted_event_id = event.event_id
        assignment.version += 1
        await AuditRepository(session).record(
            action="RESPONSIBILITY_ACCEPTED",
            object_type="ResponsibilityAssignment",
            object_id=assignment.id,
            cooperative_id=assignment.cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"signed_event_id": str(event.event_id)},
        )
        return self._complete(record, event.event_id, assignment.id)

    @staticmethod
    async def _begin(
        session: AsyncSession,
        principal: Principal,
        operation: str,
        idempotency_key: str,
        payload: object,
    ) -> tuple[IdempotencyRecord, ResponsibilityCommandResult | None]:
        record = await IdempotencyRepository(session).begin(
            actor_user_id=principal.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_payload_hash(payload),
        )
        if record.status == "COMPLETED":
            stored = record.response_payload or {}
            return record, ResponsibilityCommandResult(
                event_id=UUID(str(stored["event_id"])),
                object_id=UUID(str(stored["object_id"])),
                replayed=True,
            )
        return record, None

    @staticmethod
    def _complete(
        record: IdempotencyRecord, event_id: UUID, object_id: UUID
    ) -> ResponsibilityCommandResult:
        IdempotencyRepository.complete(
            record,
            response_status=201,
            response_payload={"event_id": str(event_id), "object_id": str(object_id)},
        )
        return ResponsibilityCommandResult(event_id, object_id, False)


def assignment_summary(
    *,
    cooperative_id: UUID,
    member_id: UUID,
    role_assignment_id: UUID,
    subject_type: str,
    subject_id: UUID,
    scope: str,
    max_exposure: Decimal,
    exposure_unit: str,
    valid_until: datetime | None,
) -> dict[str, object]:
    return {
        "command": "responsibility.propose_assignment",
        "summary_version": 1,
        "cooperative_id": str(cooperative_id),
        "member_id": str(member_id),
        "role_assignment_id": str(role_assignment_id),
        "subject": {"type": subject_type, "id": str(subject_id)},
        "scope": scope,
        "max_exposure": _decimal_text(max_exposure),
        "exposure_unit": exposure_unit,
        "valid_until": utc_timestamp(valid_until) if valid_until is not None else None,
    }


def canonical_preview(summary: dict[str, object]) -> CanonicalPreview:
    canonical = canonicalize(summary)
    return CanonicalPreview("RFC8785-JCS-1", canonical.decode("utf-8"), sha256_ref(canonical))


def _actor_claim(
    principal: Principal,
    cooperative_id: UUID,
    roles: set[RoleCode],
    *,
    exact_assignment_id: UUID | None = None,
) -> ActorClaim:
    if principal.member_id is None:
        raise _error("PHYSICAL_ACTOR_REQUIRED", 403)
    for grant in principal.roles:
        if (
            grant.role in roles
            and grant.cooperative_id in {None, cooperative_id}
            and (exact_assignment_id is None or grant.assignment_id == exact_assignment_id)
        ):
            return ActorClaim(
                person_id=principal.member_id,
                organization_id=cooperative_id,
                role_assignment_id=grant.assignment_id,
            )
    raise _error("AUTHORIZATION_DENIED", 403)


def _exposure(value: Decimal) -> Decimal:
    if not value.is_finite() or value < 0 or value > Decimal("9999999999999999.9999"):
        raise _error("MAX_EXPOSURE_INVALID", 422)
    return value.quantize(Decimal("0.0001"))


def _decimal_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0001")), "f")


def _bounded_text(value: str, code: str, limit: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise _error(code, 422)
    return normalized


def _error(code: str, status_code: int) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.responsibility.{code.lower()}",
        status_code=status_code,
    )
