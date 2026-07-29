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
from cooperative_clearing.modules.identity.application.intake import (
    acquire_member_intake_lock,
    find_member_duplicate_candidates,
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
from cooperative_clearing.modules.journal.application.service import (
    ActorClaim,
    SignedJournalService,
)
from cooperative_clearing.modules.journal.domain.assurance import (
    AccountabilityParty,
    AccountabilityPartyKind,
    CommandAssurance,
    ExposureCategory,
    ExposureClaim,
    ExposureEffect,
    actor_party,
    member_party,
    node_party,
)
from cooperative_clearing.modules.trust.application.enforcement import (
    ROLE_ASSIGNMENT_CREATE,
    require_member_action_allowed,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import PasswordService, private_value_hash
from cooperative_clearing.shared.domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class CommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool


ROLE_ADMIN_ACTOR_ROLES = frozenset(
    {RoleCode.SECURITY_ADMIN, RoleCode.COOPERATIVE_ADMIN, RoleCode.AUDITOR}
)


class IdentityAdminService:
    def __init__(
        self,
        passwords: PasswordService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.passwords = passwords or PasswordService()
        self.settings = settings or Settings()
        self.journal = SignedJournalService(self.settings)

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
        duplicate_resolution_code: str | None = None,
        request_id: UUID | None = None,
    ) -> CommandResult:
        if (identifier_type is None) != (identifier_value is None):
            raise DomainError(
                code="MEMBER_IDENTIFIER_INCOMPLETE",
                message_key="errors.identity.member_identifier_incomplete",
                status_code=422,
            )
        normalized_identifier_type = identifier_type.strip().upper() if identifier_type else None
        value_hash = private_value_hash(identifier_value) if identifier_value else None
        if duplicate_resolution_code is not None:
            duplicate_resolution_code = duplicate_resolution_code.strip().upper()
            if not 2 <= len(duplicate_resolution_code) <= 100:
                raise DomainError(
                    code="DUPLICATE_RESOLUTION_CODE_INVALID",
                    message_key="errors.identity.duplicate_resolution_code_invalid",
                    status_code=422,
                )
        safe_payload = {
            "cooperative_id": cooperative_id,
            "display_name": display_name.strip(),
            "identifier_type": normalized_identifier_type,
            "identifier_hash": value_hash,
            "duplicate_resolution_code": duplicate_resolution_code,
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
        await acquire_member_intake_lock(session, cooperative_id)
        duplicate_candidates = await find_member_duplicate_candidates(
            session,
            cooperative_id=cooperative_id,
            display_name=display_name,
            identifier_type=normalized_identifier_type,
            identifier_value=identifier_value,
        )
        if any(candidate.match_basis == "EXACT_IDENTIFIER" for candidate in duplicate_candidates):
            raise DomainError(
                code="MEMBER_IDENTIFIER_EXISTS",
                message_key="errors.identity.member_identifier_exists",
                status_code=409,
            )
        name_candidates = [
            candidate
            for candidate in duplicate_candidates
            if candidate.match_basis == "NORMALIZED_NAME"
        ]
        if name_candidates and not duplicate_resolution_code:
            raise DomainError(
                code="MEMBER_DUPLICATE_REVIEW_REQUIRED",
                message_key="errors.identity.member_duplicate_review_required",
                parameters={"candidate_count": len(name_candidates)},
                status_code=409,
            )
        member = Member(
            id=uuid4(),
            display_name=display_name.strip(),
            registered_by_cooperative_id=cooperative_id,
            status="APPLICANT",
        )
        session.add(member)
        if normalized_identifier_type and value_hash:
            session.add(
                MemberIdentifier(
                    id=uuid4(),
                    member_id=member.id,
                    identifier_type=normalized_identifier_type,
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
            reason_code=duplicate_resolution_code,
            payload={
                "status": "APPLICANT",
                "identifier_type": normalized_identifier_type or "NONE",
                "duplicate_candidate_ids": [
                    str(candidate.member_id) for candidate in name_candidates
                ],
            },
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
        now = datetime.now(UTC)
        member.status = target.value
        member.updated_at = now
        member.version += 1
        if target is MemberStatus.SUSPENDED:
            target_user_ids = select(UserAccount.id).where(UserAccount.member_id == member.id)
            await session.execute(
                update(AuthSession)
                .where(
                    AuthSession.user_id.in_(target_user_ids),
                    AuthSession.status == "ACTIVE",
                )
                .values(status="REVOKED", revoked_at=now, last_seen_at=now)
            )
            await session.execute(
                update(UserAccount)
                .where(
                    UserAccount.member_id == member.id,
                    UserAccount.status == UserStatus.ACTIVE.value,
                )
                .values(
                    status=UserStatus.DISABLED.value,
                    updated_at=now,
                    version=UserAccount.version + 1,
                )
            )
            await session.execute(
                update(Membership)
                .where(
                    Membership.member_id == member.id,
                    Membership.status == MembershipStatus.ACTIVE.value,
                )
                .values(
                    status=MembershipStatus.SUSPENDED.value,
                    updated_at=now,
                    version=Membership.version + 1,
                )
            )
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
        if member_id is not None:
            linked_member = await session.get(Member, member_id)
            if linked_member is None:
                raise self._not_found("MEMBER_NOT_FOUND")
            if linked_member.status != MemberStatus.ACTIVE.value:
                raise DomainError(
                    code="MEMBER_NOT_ACTIVE",
                    message_key="errors.identity.member_not_active",
                    status_code=409,
                )
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
        if target is UserStatus.ACTIVE and user.member_id is not None:
            member = await session.get(Member, user.member_id)
            if member is None or member.status != MemberStatus.ACTIVE.value:
                raise DomainError(
                    code="MEMBER_NOT_ACTIVE",
                    message_key="errors.identity.member_not_active",
                    status_code=409,
                )
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
        if target_user.status != UserStatus.ACTIVE.value:
            raise DomainError(
                code="USER_NOT_ACTIVE",
                message_key="errors.identity.user_not_active",
                status_code=409,
            )
        if target_user.member_id is None:
            raise DomainError(
                code="PERSONAL_ACTOR_REQUIRED",
                message_key="errors.identity.personal_actor_required",
                status_code=403,
            )
        target_member = await session.get(Member, target_user.member_id)
        if target_member is None or target_member.status != MemberStatus.ACTIVE.value:
            raise DomainError(
                code="MEMBER_NOT_ACTIVE",
                message_key="errors.identity.member_not_active",
                status_code=409,
            )
        if cooperative_id is not None and await session.get(Cooperative, cooperative_id) is None:
            raise self._not_found("COOPERATIVE_NOT_FOUND")
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
            approved_by_user_id=(
                principal.user_id if status is AssignmentStatus.ACTIVE else None
            ),
            approved_at=(
                datetime.now(UTC) if status is AssignmentStatus.ACTIVE else None
            ),
        )
        session.add(assignment)
        actor = self._role_actor(principal, cooperative_id)
        scope_party = self._role_scope_party(actor)
        target_party = self._role_target_party(target_user)
        signed_event = await self.journal.append(
            session,
            event_type=(
                "identity.role_assignment_requested"
                if status is AssignmentStatus.PENDING_APPROVAL
                else "identity.role_assignment_activated"
            ),
            aggregate_type="role_assignment",
            aggregate_id=assignment.id,
            aggregate_version=1,
            actor=actor,
            payload={
                "target_user_id": str(user_id),
                "target_member_id": str(target_user.member_id),
                "role": role.value,
                "cooperative_id": str(cooperative_id) if cooperative_id else None,
                "status": status.value,
            },
            assurance=CommandAssurance(
                on_behalf_of=scope_party,
                exposure=ExposureClaim(
                    category=ExposureCategory.AUTHORITY,
                    effect=(
                        ExposureEffect.REQUEST
                        if status is AssignmentStatus.PENDING_APPROVAL
                        else ExposureEffect.CREATE
                    ),
                    subject_type="role_assignment",
                    subject_id=assignment.id,
                    basis_refs=(role.value, record.request_hash),
                ),
                evidence_refs=self._role_evidence(record, principal),
                next_responsible=(
                    (scope_party,)
                    if status is AssignmentStatus.PENDING_APPROVAL
                    else (target_party,)
                ),
                attesters=(actor_party(actor),),
                approvers=(
                    (actor_party(actor),)
                    if status is AssignmentStatus.ACTIVE
                    else ()
                ),
            ),
        )
        await AuditRepository(session).record(
            action="ROLE_ASSIGNMENT_REQUESTED" if role in PRIVILEGED_ROLES else "ROLE_ASSIGNED",
            object_type="RoleAssignment",
            object_id=assignment.id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"target_user_id": str(user_id), "role": role.value, "status": status.value},
        )
        return self._complete(record, signed_event.event_id, assignment.id)

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
        target_user = await session.get(UserAccount, assignment.user_id)
        requester = (
            await session.get(UserAccount, assignment.granted_by_user_id)
            if assignment.granted_by_user_id is not None
            else None
        )
        if target_user is None or requester is None:
            raise self._not_found("USER_NOT_FOUND")
        target_party = self._role_target_party(target_user)
        requester_party = self._role_target_party(requester)
        assignment.status = "ACTIVE" if approve else "REJECTED"
        assignment.approved_by_user_id = principal.user_id
        assignment.approved_at = datetime.now(UTC)
        assignment.version += 1
        actor = self._role_actor(principal, assignment.cooperative_id)
        scope_party = self._role_scope_party(actor)
        signed_event = await self.journal.append(
            session,
            event_type=(
                "identity.role_assignment_approved"
                if approve
                else "identity.role_assignment_rejected"
            ),
            aggregate_type="role_assignment",
            aggregate_id=assignment.id,
            aggregate_version=assignment.version,
            actor=actor,
            payload={
                "target_user_id": str(assignment.user_id),
                "target_member_id": str(target_user.member_id),
                "role": assignment.role_code,
                "cooperative_id": (
                    str(assignment.cooperative_id)
                    if assignment.cooperative_id is not None
                    else None
                ),
                "reason_code": reason_code,
                "status": assignment.status,
            },
            assurance=CommandAssurance(
                on_behalf_of=scope_party,
                exposure=ExposureClaim(
                    category=ExposureCategory.AUTHORITY,
                    effect=(
                        ExposureEffect.APPROVE if approve else ExposureEffect.REJECT
                    ),
                    subject_type="role_assignment",
                    subject_id=assignment.id,
                    basis_refs=(assignment.role_code, reason_code, record.request_hash),
                ),
                evidence_refs=self._role_evidence(record, principal, reason_code),
                next_responsible=(target_party,) if approve else (scope_party,),
                attesters=(requester_party,),
                approvers=(actor_party(actor),),
            ),
        )
        await AuditRepository(session).record(
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
        return self._complete(record, signed_event.event_id, assignment.id)

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
        target_user = await session.get(UserAccount, assignment.user_id)
        if target_user is None:
            raise self._not_found("USER_NOT_FOUND")
        assignment.status = "REVOKED"
        assignment.revoked_at = datetime.now(UTC)
        assignment.version += 1
        actor = self._role_actor(principal, assignment.cooperative_id)
        scope_party = self._role_scope_party(actor)
        signed_event = await self.journal.append(
            session,
            event_type="identity.role_assignment_revoked",
            aggregate_type="role_assignment",
            aggregate_id=assignment.id,
            aggregate_version=assignment.version,
            actor=actor,
            payload={
                "target_user_id": str(assignment.user_id),
                "target_member_id": str(target_user.member_id),
                "role": assignment.role_code,
                "cooperative_id": (
                    str(assignment.cooperative_id)
                    if assignment.cooperative_id is not None
                    else None
                ),
                "reason_code": reason_code,
                "status": assignment.status,
            },
            assurance=CommandAssurance(
                on_behalf_of=scope_party,
                exposure=ExposureClaim(
                    category=ExposureCategory.AUTHORITY,
                    effect=ExposureEffect.REVOKE,
                    subject_type="role_assignment",
                    subject_id=assignment.id,
                    basis_refs=(assignment.role_code, reason_code, record.request_hash),
                ),
                evidence_refs=self._role_evidence(record, principal, reason_code),
                next_responsible=(scope_party,),
                approvers=(actor_party(actor),),
            ),
        )
        await AuditRepository(session).record(
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
        return self._complete(record, signed_event.event_id, assignment.id)

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

    def _role_actor(
        self,
        principal: Principal,
        cooperative_id: UUID | None,
    ) -> ActorClaim:
        if principal.member_id is None:
            raise DomainError(
                code="PERSONAL_ACTOR_REQUIRED",
                message_key="errors.identity.personal_actor_required",
                status_code=403,
            )
        for grant in principal.roles:
            if (
                grant.source is RoleGrantSource.ASSIGNMENT
                and grant.role in ROLE_ADMIN_ACTOR_ROLES
                and (cooperative_id is None or grant.cooperative_id in {None, cooperative_id})
            ):
                return ActorClaim(
                    person_id=principal.member_id,
                    organization_id=cooperative_id,
                    role_assignment_id=grant.assignment_id,
                )
        raise DomainError(
            code="PERMANENT_ROLE_REQUIRED",
            message_key="errors.identity.permanent_role_required",
            status_code=403,
        )

    def _role_scope_party(self, actor: ActorClaim) -> AccountabilityParty:
        if actor.organization_id is not None:
            return AccountabilityParty(
                kind=AccountabilityPartyKind.COOPERATIVE,
                reference=str(actor.organization_id),
            )
        return node_party(self.settings.node_code)

    @staticmethod
    def _role_target_party(user: UserAccount) -> AccountabilityParty:
        if user.member_id is None:
            raise DomainError(
                code="PERSONAL_ACTOR_REQUIRED",
                message_key="errors.identity.personal_actor_required",
                status_code=403,
            )
        return member_party(user.member_id)

    @staticmethod
    def _role_evidence(
        record: IdempotencyRecord,
        principal: Principal,
        reason_code: str | None = None,
    ) -> tuple[object, ...]:
        evidence: list[object] = [
            {"idempotency_record_id": str(record.id)},
            {"authenticated_session_id": str(principal.session_id)},
        ]
        if reason_code is not None:
            evidence.append({"reason_code": reason_code})
        return tuple(evidence)

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
    scoped_user_ids = select(UserAccount.id).where(UserAccount.member_id.in_(scoped_member_ids))
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
