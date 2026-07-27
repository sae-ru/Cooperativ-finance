"""Administrative identity commands with audit and idempotency."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import false, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.audit.infrastructure.repository import (
    AuditRepository,
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.identity.domain.types import (
    PRIVILEGED_ROLES,
    AssignmentStatus,
    CooperativeStatus,
    MembershipStatus,
    MemberStatus,
    Principal,
    RoleCode,
    RoleGrantSource,
    UserStatus,
    ensure_cooperative_transition,
    ensure_member_transition,
    ensure_membership_transition,
    ensure_user_transition,
    normalize_login,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    AuthSession,
    Cooperative,
    Member,
    MemberIdentifier,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.trust.application.enforcement import (
    ROLE_ASSIGNMENT_CREATE,
    require_member_action_allowed,
)
from cooperative_clearing.shared.core.security import PasswordService, private_value_hash
from cooperative_clearing.shared.domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class CommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool


class IdentityAdminService:
    def __init__(self, passwords: PasswordService | None = None) -> None:
        self.passwords = passwords or PasswordService()

    async def create_cooperative(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        idempotency_key: str,
        code: str,
        name: str,
        request_id: UUID | None,
    ) -> CommandResult:
        normalized_code = code.strip().lower()
        payload = {"code": normalized_code, "name": name.strip()}
        record, replay = await self._begin(
            session, principal, "COOPERATIVE_CREATE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        cooperative = Cooperative(
            id=uuid4(), code=normalized_code, name=name.strip(), status="ACTIVE"
        )
        session.add(cooperative)
        event_id = await AuditRepository(session).record(
            action="COOPERATIVE_CREATED",
            object_type="Cooperative",
            object_id=cooperative.id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
        )
        return self._complete(record, event_id, cooperative.id)

    async def transition_cooperative(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        target: CooperativeStatus,
        reason_code: str,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CommandResult:
        payload = {
            "cooperative_id": cooperative_id,
            "target": target.value,
            "reason_code": reason_code,
            "expected_version": expected_version,
        }
        record, replay = await self._begin(
            session, principal, "COOPERATIVE_TRANSITION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        cooperative = await session.get(Cooperative, cooperative_id, with_for_update=True)
        if cooperative is None:
            raise self._not_found("COOPERATIVE_NOT_FOUND")
        if cooperative.version != expected_version:
            raise DomainError(
                code="VERSION_CONFLICT",
                message_key="errors.request.version_conflict",
                parameters={"current_version": cooperative.version},
                status_code=409,
            )
        current = CooperativeStatus(cooperative.status)
        ensure_cooperative_transition(current, target)
        cooperative.status = target.value
        cooperative.updated_at = datetime.now(UTC)
        cooperative.version += 1
        event_id = await AuditRepository(session).record(
            action="COOPERATIVE_STATUS_CHANGED",
            object_type="Cooperative",
            object_id=cooperative.id,
            cooperative_id=cooperative.id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            reason_code=reason_code,
            payload={"from": current.value, "to": target.value},
        )
        return self._complete(record, event_id, cooperative.id)
    async def create_member(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        idempotency_key: str,
        display_name: str,
        identifier_type: str | None,
        identifier_value: str | None,
        request_id: UUID | None,
    ) -> CommandResult:
        if (identifier_type is None) != (identifier_value is None):
            raise DomainError(
                code="MEMBER_IDENTIFIER_INCOMPLETE",
                message_key="errors.identity.member_identifier_incomplete",
                status_code=422,
            )
        value_hash = private_value_hash(identifier_value) if identifier_value else None
        safe_payload = {
            "cooperative_id": cooperative_id,
            "display_name": display_name.strip(),
            "identifier_type": identifier_type,
            "identifier_hash": value_hash,
        }
        record, replay = await self._begin(
            session, principal, "MEMBER_CREATE", idempotency_key, safe_payload
        )
        if replay is not None:
            return replay
        cooperative = await session.get(Cooperative, cooperative_id)
        if cooperative is None:
            raise self._not_found("COOPERATIVE_NOT_FOUND")
        if cooperative.status != CooperativeStatus.ACTIVE.value:
            raise DomainError(
                code="COOPERATIVE_NOT_ACTIVE",
                message_key="errors.identity.cooperative_not_active",
                status_code=409,
            )
        if identifier_type and value_hash:
            duplicate = await session.scalar(
                select(func.count())
                .select_from(MemberIdentifier)
                .where(
                    MemberIdentifier.identifier_type == identifier_type,
                    MemberIdentifier.value_hash == value_hash,
                )
            )
            if duplicate:
                raise DomainError(
                    code="MEMBER_IDENTIFIER_EXISTS",
                    message_key="errors.identity.member_identifier_exists",
                    status_code=409,
                )
        member = Member(
            id=uuid4(),
            display_name=display_name.strip(),
            registered_by_cooperative_id=cooperative_id,
            status="APPLICANT",
        )
        session.add(member)
        if identifier_type and value_hash:
            session.add(
                MemberIdentifier(
                    id=uuid4(),
                    member_id=member.id,
                    identifier_type=identifier_type,
                    value_hash=value_hash,
                )
            )
        event_id = await AuditRepository(session).record(
            action="MEMBER_CREATED",
            object_type="Member",
            object_id=member.id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"status": "APPLICANT", "identifier_type": identifier_type or "NONE"},
        )
        return self._complete(record, event_id, member.id)

    async def transition_member(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        member_id: UUID,
        target: MemberStatus,
        reason_code: str,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CommandResult:
        payload = {
            "member_id": member_id,
            "target": target.value,
            "reason_code": reason_code,
            "expected_version": expected_version,
        }
        record, replay = await self._begin(
            session, principal, "MEMBER_TRANSITION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        member = await session.get(Member, member_id, with_for_update=True)
        if member is None:
            raise self._not_found("MEMBER_NOT_FOUND")
        if member.version != expected_version:
            raise DomainError(
                code="VERSION_CONFLICT",
                message_key="errors.request.version_conflict",
                parameters={"current_version": member.version},
                status_code=409,
            )
        current = MemberStatus(member.status)
        ensure_member_transition(current, target)
        member.status = target.value
        member.updated_at = datetime.now(UTC)
        member.version += 1
        event_id = await AuditRepository(session).record(
            action="MEMBER_STATUS_CHANGED",
            object_type="Member",
            object_id=member.id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            reason_code=reason_code,
            payload={"from": current.value, "to": target.value},
        )
        return self._complete(record, event_id, member.id)

    async def create_membership(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        member_id: UUID,
        member_number: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CommandResult:
        payload = {
            "cooperative_id": cooperative_id,
            "member_id": member_id,
            "member_number": member_number.strip(),
        }
        record, replay = await self._begin(
            session, principal, "MEMBERSHIP_CREATE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        cooperative = await session.get(Cooperative, cooperative_id)
        member = await session.get(Member, member_id)
        if cooperative is None:
            raise self._not_found("COOPERATIVE_NOT_FOUND")
        if member is None:
            raise self._not_found("MEMBER_NOT_FOUND")
        active = member.status == "ACTIVE" and cooperative.status == "ACTIVE"
        membership = Membership(
            id=uuid4(),
            cooperative_id=cooperative_id,
            member_id=member_id,
            member_number=member_number.strip(),
            status="ACTIVE" if active else "PENDING",
            joined_at=datetime.now(UTC) if active else None,
        )
        session.add(membership)
        event_id = await AuditRepository(session).record(
            action="MEMBERSHIP_CREATED",
            object_type="Membership",
            object_id=membership.id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"status": membership.status, "member_id": str(member_id)},
        )
        return self._complete(record, event_id, membership.id)

    async def transition_membership(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        membership_id: UUID,
        target: MembershipStatus,
        reason_code: str,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CommandResult:
        payload = {
            "membership_id": membership_id,
            "target": target.value,
            "reason_code": reason_code,
            "expected_version": expected_version,
        }
        record, replay = await self._begin(
            session, principal, "MEMBERSHIP_TRANSITION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        membership = await session.get(Membership, membership_id, with_for_update=True)
        if membership is None:
            raise self._not_found("MEMBERSHIP_NOT_FOUND")
        if membership.version != expected_version:
            raise DomainError(
                code="VERSION_CONFLICT",
                message_key="errors.request.version_conflict",
                parameters={"current_version": membership.version},
                status_code=409,
            )
        current = MembershipStatus(membership.status)
        ensure_membership_transition(current, target)
        if target is MembershipStatus.ACTIVE:
            cooperative = await session.get(Cooperative, membership.cooperative_id)
            member = await session.get(Member, membership.member_id)
            if cooperative is None or cooperative.status != CooperativeStatus.ACTIVE.value:
                raise DomainError(
                    code="COOPERATIVE_NOT_ACTIVE",
                    message_key="errors.identity.cooperative_not_active",
                    status_code=409,
                )
            if member is None or member.status != MemberStatus.ACTIVE.value:
                raise DomainError(
                    code="MEMBER_NOT_ACTIVE",
                    message_key="errors.identity.member_not_active",
                    status_code=409,
                )
        now = datetime.now(UTC)
        membership.status = target.value
        if target is MembershipStatus.ACTIVE and membership.joined_at is None:
            membership.joined_at = now
        if target is MembershipStatus.ENDED:
            membership.ended_at = now
        membership.updated_at = now
        membership.version += 1
        event_id = await AuditRepository(session).record(
            action="MEMBERSHIP_STATUS_CHANGED",
            object_type="Membership",
            object_id=membership.id,
            cooperative_id=membership.cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            reason_code=reason_code,
            payload={
                "from": current.value,
                "to": target.value,
                "member_id": str(membership.member_id),
            },
        )
        return self._complete(record, event_id, membership.id)
    async def create_user(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        login: str,
        temporary_password: str,
        member_id: UUID | None,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CommandResult:
        normalized = normalize_login(login)
        payload = {"login": normalized, "member_id": member_id}
        record, replay = await self._begin(
            session, principal, "USER_CREATE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        if member_id is not None and await session.get(Member, member_id) is None:
            raise self._not_found("MEMBER_NOT_FOUND")
        user = UserAccount(
            id=uuid4(),
            login=normalized,
            password_hash=self.passwords.hash(temporary_password),
            member_id=member_id,
            status="ACTIVE",
            must_change_password=True,
        )
        session.add(user)
        event_id = await AuditRepository(session).record(
            action="USER_CREATED",
            object_type="UserAccount",
            object_id=user.id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
        )
        return self._complete(record, event_id, user.id)

    async def transition_user(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        user_id: UUID,
        target: UserStatus,
        reason_code: str,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CommandResult:
        if user_id == principal.user_id and target is UserStatus.DISABLED:
            raise DomainError(
                code="SELF_ACCOUNT_DISABLE_FORBIDDEN",
                message_key="errors.identity.self_account_disable_forbidden",
                status_code=403,
            )
        payload = {
            "user_id": user_id,
            "target": target.value,
            "reason_code": reason_code,
            "expected_version": expected_version,
        }
        record, replay = await self._begin(
            session, principal, "USER_TRANSITION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        user = await session.get(UserAccount, user_id, with_for_update=True)
        if user is None:
            raise self._not_found("USER_NOT_FOUND")
        if user.version != expected_version:
            raise DomainError(
                code="VERSION_CONFLICT",
                message_key="errors.request.version_conflict",
                parameters={"current_version": user.version},
                status_code=409,
            )
        current = UserStatus(user.status)
        ensure_user_transition(current, target)
        now = datetime.now(UTC)
        user.status = target.value
        user.updated_at = now
        user.version += 1
        if target is UserStatus.DISABLED:
            await session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user.id, AuthSession.status == "ACTIVE")
                .values(status="REVOKED", revoked_at=now, last_seen_at=now)
            )
        else:
            user.failed_login_attempts = 0
            user.locked_until = None
        event_id = await AuditRepository(session).record(
            action="USER_STATUS_CHANGED",
            object_type="UserAccount",
            object_id=user.id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            reason_code=reason_code,
            payload={
                "from": current.value,
                "to": target.value,
                "active_sessions_revoked": target is UserStatus.DISABLED,
            },
        )
        return self._complete(record, event_id, user.id)
    async def assign_role(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        user_id: UUID,
        role: RoleCode,
        cooperative_id: UUID | None,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CommandResult:
        if principal.user_id == user_id:
            raise DomainError(
                code="SELF_ROLE_ASSIGNMENT_FORBIDDEN",
                message_key="errors.identity.self_role_assignment_forbidden",
                status_code=403,
            )
        if role in PRIVILEGED_ROLES and cooperative_id is not None:
            raise DomainError(
                code="PRIVILEGED_ROLE_SCOPE_INVALID",
                message_key="errors.identity.privileged_role_scope_invalid",
                status_code=422,
            )
        if role not in PRIVILEGED_ROLES and cooperative_id is None:
            raise DomainError(
                code="COOPERATIVE_SCOPE_REQUIRED",
                message_key="errors.identity.cooperative_scope_required",
                status_code=422,
            )
        payload = {"user_id": user_id, "role": role.value, "cooperative_id": cooperative_id}
        record, replay = await self._begin(
            session, principal, "ROLE_ASSIGN", idempotency_key, payload
        )
        if replay is not None:
            return replay
        target_user = await session.get(UserAccount, user_id)
        if target_user is None:
            raise self._not_found("USER_NOT_FOUND")
        if cooperative_id is not None and await session.get(Cooperative, cooperative_id) is None:
            raise self._not_found("COOPERATIVE_NOT_FOUND")
        if target_user.member_id is not None:
            await require_member_action_allowed(
                session,
                cooperative_id=cooperative_id,
                member_ids={target_user.member_id},
                action=ROLE_ASSIGNMENT_CREATE,
                target_role=role.value,
            )
        status = (
            AssignmentStatus.PENDING_APPROVAL
            if role in PRIVILEGED_ROLES
            else AssignmentStatus.ACTIVE
        )
        assignment = RoleAssignment(
            id=uuid4(),
            user_id=user_id,
            role_code=role.value,
            cooperative_id=cooperative_id,
            status=status.value,
            granted_by_user_id=principal.user_id,
            approved_by_user_id=None,
        )
        session.add(assignment)
        event_id = await AuditRepository(session).record(
            action="ROLE_ASSIGNMENT_REQUESTED" if role in PRIVILEGED_ROLES else "ROLE_ASSIGNED",
            object_type="RoleAssignment",
            object_id=assignment.id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"target_user_id": str(user_id), "role": role.value, "status": status.value},
        )
        return self._complete(record, event_id, assignment.id)

    async def decide_role(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        assignment_id: UUID,
        approve: bool,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CommandResult:
        payload = {"assignment_id": assignment_id, "approve": approve, "reason": reason_code}
        record, replay = await self._begin(
            session, principal, "ROLE_DECIDE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        assignment = await session.get(RoleAssignment, assignment_id, with_for_update=True)
        if assignment is None:
            raise self._not_found("ROLE_ASSIGNMENT_NOT_FOUND")
        if assignment.source != RoleGrantSource.ASSIGNMENT.value:
            raise DomainError(
                code="BREAK_GLASS_MANAGED_SEPARATELY",
                message_key="errors.identity.break_glass_managed_separately",
                status_code=409,
            )
        if assignment.status != "PENDING_APPROVAL":
            raise DomainError(
                code="ROLE_ASSIGNMENT_NOT_PENDING",
                message_key="errors.identity.role_assignment_not_pending",
                status_code=409,
            )
        if principal.user_id in {assignment.user_id, assignment.granted_by_user_id}:
            raise DomainError(
                code="INDEPENDENT_APPROVER_REQUIRED",
                message_key="errors.identity.independent_approver_required",
                status_code=403,
            )
        assignment.status = "ACTIVE" if approve else "REJECTED"
        assignment.approved_by_user_id = principal.user_id
        assignment.approved_at = datetime.now(UTC)
        assignment.version += 1
        event_id = await AuditRepository(session).record(
            action="ROLE_ASSIGNMENT_APPROVED" if approve else "ROLE_ASSIGNMENT_REJECTED",
            object_type="RoleAssignment",
            object_id=assignment.id,
            cooperative_id=assignment.cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            reason_code=reason_code,
            request_id=request_id,
            payload={"target_user_id": str(assignment.user_id), "role": assignment.role_code},
        )
        return self._complete(record, event_id, assignment.id)

    async def revoke_role(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        assignment_id: UUID,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CommandResult:
        payload = {"assignment_id": assignment_id, "reason": reason_code}
        record, replay = await self._begin(
            session, principal, "ROLE_REVOKE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        assignment = await session.get(RoleAssignment, assignment_id, with_for_update=True)
        if assignment is None:
            raise self._not_found("ROLE_ASSIGNMENT_NOT_FOUND")
        if assignment.source != RoleGrantSource.ASSIGNMENT.value:
            raise DomainError(
                code="BREAK_GLASS_MANAGED_SEPARATELY",
                message_key="errors.identity.break_glass_managed_separately",
                status_code=409,
            )
        if assignment.status != "ACTIVE":
            raise DomainError(
                code="ROLE_ASSIGNMENT_NOT_ACTIVE",
                message_key="errors.identity.role_assignment_not_active",
                status_code=409,
            )
        assignment.status = "REVOKED"
        assignment.revoked_at = datetime.now(UTC)
        assignment.version += 1
        event_id = await AuditRepository(session).record(
            action="ROLE_REVOKED",
            object_type="RoleAssignment",
            object_id=assignment.id,
            cooperative_id=assignment.cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            reason_code=reason_code,
            request_id=request_id,
            payload={"target_user_id": str(assignment.user_id), "role": assignment.role_code},
        )
        return self._complete(record, event_id, assignment.id)

    async def revoke_session(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        auth_session_id: UUID,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CommandResult:
        payload = {"session_id": auth_session_id, "reason": reason_code}
        record, replay = await self._begin(
            session, principal, "SESSION_REVOKE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        auth_session = await session.get(AuthSession, auth_session_id, with_for_update=True)
        if auth_session is None:
            raise self._not_found("AUTH_SESSION_NOT_FOUND")
        if auth_session.status == "ACTIVE":
            auth_session.status = "REVOKED"
            auth_session.revoked_at = datetime.now(UTC)
        event_id = await AuditRepository(session).record(
            action="AUTH_SESSION_REVOKED",
            object_type="AuthSession",
            object_id=auth_session.id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            reason_code=reason_code,
            request_id=request_id,
            payload={"target_user_id": str(auth_session.user_id)},
        )
        return self._complete(record, event_id, auth_session.id)

    @staticmethod
    async def _begin(
        session: AsyncSession,
        principal: Principal,
        operation: str,
        idempotency_key: str,
        payload: object,
    ) -> tuple[IdempotencyRecord, CommandResult | None]:
        record = await IdempotencyRepository(session).begin(
            actor_user_id=principal.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_payload_hash(payload),
        )
        if record.status == "COMPLETED":
            stored = record.response_payload or {}
            return record, CommandResult(
                event_id=UUID(str(stored["event_id"])),
                object_id=UUID(str(stored["object_id"])),
                replayed=True,
            )
        return record, None

    @staticmethod
    def _complete(record: IdempotencyRecord, event_id: UUID, object_id: UUID) -> CommandResult:
        IdempotencyRepository.complete(
            record,
            response_status=201,
            response_payload={"event_id": str(event_id), "object_id": str(object_id)},
        )
        return CommandResult(event_id=event_id, object_id=object_id, replayed=False)

    @staticmethod
    def _not_found(code: str) -> DomainError:
        return DomainError(
            code=code,
            message_key=f"errors.identity.{code.lower()}",
            status_code=404,
        )


async def admin_overview(
    session: AsyncSession, cooperative_ids: set[UUID] | None = None
) -> dict[str, int]:
    async def count(model: type[object], *conditions: ColumnElement[bool]) -> int:
        statement = select(func.count()).select_from(model)
        for condition in conditions:
            statement = statement.where(condition)
        return int((await session.execute(statement)).scalar_one())

    if cooperative_ids is None:
        return {
            "members": await count(Member),
            "active_members": await count(Member, Member.status == "ACTIVE"),
            "cooperatives": await count(Cooperative),
            "users": await count(UserAccount),
            "active_sessions": await count(AuthSession, AuthSession.status == "ACTIVE"),
            "pending_role_approvals": await count(
                RoleAssignment,
                RoleAssignment.status == "PENDING_APPROVAL",
                RoleAssignment.source == RoleGrantSource.ASSIGNMENT.value,
            ),
        }

    member_condition: ColumnElement[bool]
    if cooperative_ids:
        membership_members = select(Membership.member_id).where(
            Membership.cooperative_id.in_(cooperative_ids)
        )
        member_condition = or_(
            Member.registered_by_cooperative_id.in_(cooperative_ids),
            Member.id.in_(membership_members),
        )
    else:
        member_condition = false()
    scoped_member_ids = select(Member.id).where(member_condition)
    scoped_user_ids = select(UserAccount.id).where(
        UserAccount.member_id.in_(scoped_member_ids)
    )
    active_sessions = int(
        (
            await session.execute(
                select(func.count())
                .select_from(AuthSession)
                .where(
                    AuthSession.status == "ACTIVE",
                    AuthSession.user_id.in_(scoped_user_ids),
                )
            )
        ).scalar_one()
    )
    return {
        "members": await count(Member, member_condition),
        "active_members": await count(
            Member, member_condition, Member.status == MemberStatus.ACTIVE.value
        ),
        "cooperatives": await count(Cooperative, Cooperative.id.in_(cooperative_ids)),
        "users": await count(UserAccount, UserAccount.id.in_(scoped_user_ids)),
        "active_sessions": active_sessions,
        "pending_role_approvals": await count(
            RoleAssignment,
            RoleAssignment.status == AssignmentStatus.PENDING_APPROVAL.value,
            RoleAssignment.source == RoleGrantSource.ASSIGNMENT.value,
            RoleAssignment.cooperative_id.in_(cooperative_ids),
        ),
    }