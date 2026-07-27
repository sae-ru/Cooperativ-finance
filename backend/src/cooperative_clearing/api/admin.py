"""Administrative API for clients, memberships, roles, sessions, and audit."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cooperative_clearing.api.auth import _request_uuid
from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.identity_schemas import (
    AccountRecoveryCollection,
    AccountRecoveryCreateRequest,
    AdminOverviewResponse,
    AuditEntryResponse,
    BreakGlassCollection,
    BreakGlassCreateRequest,
    CommandEnvelope,
    CooperativeCreateRequest,
    CooperativeResponse,
    MemberCreateRequest,
    MemberResponse,
    MembershipCreateRequest,
    MembershipResponse,
    MemberTransitionRequest,
    OverviewEnvelope,
    RoleApprovalRequest,
    RoleAssignmentRequest,
    RoleAssignmentResponse,
    SecurityDecisionRequest,
    SessionAdminResponse,
    UserCreateRequest,
    UserResponse,
)
from cooperative_clearing.api.identity_schemas import (
    CommandResult as ApiCommandResult,
)
from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.application.admin import (
    CommandResult,
    IdentityAdminService,
    admin_overview,
)
from cooperative_clearing.modules.identity.application.security import (
    RECOVERY_CONTROL_ROLES,
    IdentitySecurityService,
    SecurityCommandResult,
    expire_security_workflows,
    require_step_up,
)
from cooperative_clearing.modules.identity.domain.types import (
    RoleCode,
    RoleGrantSource,
    require_permanent_role,
    require_role,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    AccountRecoveryRequest,
    AuthSession,
    BreakGlassGrant,
    Cooperative,
    Member,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/admin", tags=["administration"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]

ADMIN_READ_ROLES = {
    RoleCode.MEMBER_REGISTRAR,
    RoleCode.COOPERATIVE_ADMIN,
    RoleCode.DATA_STEWARD,
    RoleCode.RISK_ADMIN,
    RoleCode.SECURITY_ADMIN,
    RoleCode.NODE_REGISTRAR,
    RoleCode.RIGHTS_OPERATOR,
    RoleCode.AUDITOR,
}


class CooperativeCollection(BaseModel):
    data: list[CooperativeResponse]
    request_id: str


class MemberCollection(BaseModel):
    data: list[MemberResponse]
    request_id: str


class MembershipCollection(BaseModel):
    data: list[MembershipResponse]
    request_id: str


class UserCollection(BaseModel):
    data: list[UserResponse]
    request_id: str


class RoleCollection(BaseModel):
    data: list[RoleAssignmentResponse]
    request_id: str


class SessionCollection(BaseModel):
    data: list[SessionAdminResponse]
    request_id: str


class AuditCollection(BaseModel):
    data: list[AuditEntryResponse]
    request_id: str


class ReasonRequest(BaseModel):
    reason_code: str = Field(min_length=2, max_length=100)


def _command(result: CommandResult | SecurityCommandResult) -> CommandEnvelope:
    return CommandEnvelope(
        data=ApiCommandResult(
            event_id=result.event_id,
            object_id=result.object_id,
            replayed=result.replayed,
        ),
        request_id=get_request_id(),
    )


def _map_integrity_error(exc: IntegrityError) -> DomainError:
    return DomainError(
        code="RESOURCE_CONFLICT",
        message_key="errors.request.resource_conflict",
        status_code=409,
    )


@router.get("/overview", response_model=OverviewEnvelope)
async def overview(
    principal: PrincipalDependency, database: DatabaseDependency
) -> OverviewEnvelope:
    require_role(principal, ADMIN_READ_ROLES)
    async with database.session() as session:
        data = await admin_overview(session)
    return OverviewEnvelope(data=AdminOverviewResponse(**data), request_id=get_request_id())


@router.get("/cooperatives", response_model=CooperativeCollection)
async def list_cooperatives(
    principal: PrincipalDependency, database: DatabaseDependency
) -> CooperativeCollection:
    require_role(principal, ADMIN_READ_ROLES)
    statement = select(Cooperative).order_by(Cooperative.name)
    has_global_scope = any(
        grant.role in ADMIN_READ_ROLES and grant.cooperative_id is None for grant in principal.roles
    )
    if not has_global_scope:
        cooperative_ids = {
            grant.cooperative_id
            for grant in principal.roles
            if grant.role in ADMIN_READ_ROLES and grant.cooperative_id is not None
        }
        statement = statement.where(Cooperative.id.in_(cooperative_ids))
    async with database.session() as session:
        result = await session.execute(statement)
        items = list(result.scalars())
    return CooperativeCollection(data=items, request_id=get_request_id())


@router.post("/cooperatives", response_model=CommandEnvelope, status_code=201)
async def create_cooperative(
    payload: CooperativeCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    require_role(principal, {RoleCode.NODE_REGISTRAR, RoleCode.SECURITY_ADMIN})
    async with database.session() as session:
        await require_step_up(
            session,
            principal,
            operation="COOPERATIVE_CREATE",
            emergency_roles=frozenset({RoleCode.SECURITY_ADMIN}),
            request_id=_request_uuid(),
        )
        try:
            result = await IdentityAdminService().create_cooperative(
                session,
                principal=principal,
                idempotency_key=idempotency_key,
                code=payload.code,
                name=payload.name,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _map_integrity_error(exc) from exc
    return _command(result)


@router.get("/members", response_model=MemberCollection)
async def list_members(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
) -> MemberCollection:
    require_role(principal, ADMIN_READ_ROLES)
    statement = select(Member).order_by(Member.created_at.desc(), Member.id).limit(limit)
    if status:
        statement = statement.where(Member.status == status.upper())
    async with database.session() as session:
        result = await session.execute(statement)
        items = list(result.scalars())
    return MemberCollection(data=items, request_id=get_request_id())


@router.post("/members", response_model=CommandEnvelope, status_code=201)
async def create_member(
    payload: MemberCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    require_role(principal, {RoleCode.MEMBER_REGISTRAR})
    identifier_value = (
        payload.identifier_value.get_secret_value() if payload.identifier_value else None
    )
    async with database.session() as session:
        try:
            result = await IdentityAdminService().create_member(
                session,
                principal=principal,
                idempotency_key=idempotency_key,
                display_name=payload.display_name,
                identifier_type=payload.identifier_type,
                identifier_value=identifier_value,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _map_integrity_error(exc) from exc
    return _command(result)


@router.post("/members/{member_id}/transitions", response_model=CommandEnvelope)
async def transition_member(
    member_id: UUID,
    payload: MemberTransitionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    require_role(principal, {RoleCode.MEMBER_REGISTRAR, RoleCode.RISK_ADMIN})
    async with database.session() as session:
        result = await IdentityAdminService().transition_member(
            session,
            principal=principal,
            member_id=member_id,
            target=payload.target_status,
            reason_code=payload.reason_code,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )
        await session.commit()
    return _command(result)


@router.get("/memberships", response_model=MembershipCollection)
async def list_memberships(
    principal: PrincipalDependency, database: DatabaseDependency
) -> MembershipCollection:
    require_role(principal, ADMIN_READ_ROLES)
    async with database.session() as session:
        result = await session.execute(
            select(Membership).order_by(Membership.created_at.desc(), Membership.id)
        )
        items = list(result.scalars())
    return MembershipCollection(data=items, request_id=get_request_id())


@router.post("/memberships", response_model=CommandEnvelope, status_code=201)
async def create_membership(
    payload: MembershipCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    require_role(
        principal,
        {RoleCode.COOPERATIVE_ADMIN, RoleCode.MEMBER_REGISTRAR},
        payload.cooperative_id,
    )
    async with database.session() as session:
        try:
            result = await IdentityAdminService().create_membership(
                session,
                principal=principal,
                cooperative_id=payload.cooperative_id,
                member_id=payload.member_id,
                member_number=payload.member_number,
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _map_integrity_error(exc) from exc
    return _command(result)


@router.get("/users", response_model=UserCollection)
async def list_users(
    principal: PrincipalDependency, database: DatabaseDependency
) -> UserCollection:
    require_role(principal, {RoleCode.SECURITY_ADMIN, RoleCode.AUDITOR})
    async with database.session() as session:
        result = await session.execute(select(UserAccount).order_by(UserAccount.login))
        items = list(result.scalars())
    return UserCollection(data=items, request_id=get_request_id())


@router.post("/users", response_model=CommandEnvelope, status_code=201)
async def create_user(
    payload: UserCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    require_role(principal, {RoleCode.SECURITY_ADMIN})
    async with database.session() as session:
        try:
            result = await IdentityAdminService().create_user(
                session,
                principal=principal,
                login=payload.login,
                temporary_password=payload.temporary_password.get_secret_value(),
                member_id=payload.member_id,
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _map_integrity_error(exc) from exc
    return _command(result)


@router.get("/roles", response_model=RoleCollection)
async def list_roles(
    principal: PrincipalDependency, database: DatabaseDependency
) -> RoleCollection:
    require_role(principal, {RoleCode.SECURITY_ADMIN, RoleCode.AUDITOR, RoleCode.COOPERATIVE_ADMIN})
    async with database.session() as session:
        result = await session.execute(
            select(RoleAssignment)
            .where(RoleAssignment.source == RoleGrantSource.ASSIGNMENT.value)
            .order_by(RoleAssignment.created_at.desc())
        )
        items = list(result.scalars())
    return RoleCollection(data=items, request_id=get_request_id())


@router.post("/roles", response_model=CommandEnvelope, status_code=201)
async def assign_role(
    payload: RoleAssignmentRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    if payload.role in {
        RoleCode.SECURITY_ADMIN,
        RoleCode.NODE_REGISTRAR,
        RoleCode.AUDITOR,
        RoleCode.ARBITRATOR,
    }:
        require_permanent_role(principal, {RoleCode.SECURITY_ADMIN})
    else:
        require_permanent_role(
            principal,
            {RoleCode.SECURITY_ADMIN, RoleCode.COOPERATIVE_ADMIN},
            payload.cooperative_id,
        )
    async with database.session() as session:
        await require_step_up(
            session,
            principal,
            operation="ROLE_ASSIGN",
            emergency_roles=frozenset({RoleCode.SECURITY_ADMIN}),
            request_id=_request_uuid(),
        )
        try:
            result = await IdentityAdminService().assign_role(
                session,
                principal=principal,
                user_id=payload.user_id,
                role=payload.role,
                cooperative_id=payload.cooperative_id,
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _map_integrity_error(exc) from exc
    return _command(result)


@router.post("/roles/{assignment_id}/decision", response_model=CommandEnvelope)
async def decide_role(
    assignment_id: UUID,
    payload: RoleApprovalRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    require_permanent_role(principal, {RoleCode.SECURITY_ADMIN, RoleCode.AUDITOR})
    async with database.session() as session:
        await require_step_up(
            session,
            principal,
            operation="ROLE_DECIDE",
            emergency_roles=frozenset({RoleCode.SECURITY_ADMIN}),
            request_id=_request_uuid(),
        )
        result = await IdentityAdminService().decide_role(
            session,
            principal=principal,
            assignment_id=assignment_id,
            approve=payload.approve,
            reason_code=payload.reason_code,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )
        await session.commit()
    return _command(result)


@router.post("/roles/{assignment_id}/revoke", response_model=CommandEnvelope)
async def revoke_role(
    assignment_id: UUID,
    payload: ReasonRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    require_permanent_role(principal, {RoleCode.SECURITY_ADMIN})
    async with database.session() as session:
        await require_step_up(
            session,
            principal,
            operation="ROLE_REVOKE",
            emergency_roles=frozenset({RoleCode.SECURITY_ADMIN}),
            request_id=_request_uuid(),
        )
        result = await IdentityAdminService().revoke_role(
            session,
            principal=principal,
            assignment_id=assignment_id,
            reason_code=payload.reason_code,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )
        await session.commit()
    return _command(result)


@router.get("/sessions", response_model=SessionCollection)
async def list_sessions(
    principal: PrincipalDependency, database: DatabaseDependency
) -> SessionCollection:
    require_role(principal, {RoleCode.SECURITY_ADMIN, RoleCode.AUDITOR})
    async with database.session() as session:
        result = await session.execute(
            select(AuthSession).order_by(AuthSession.created_at.desc()).limit(500)
        )
        items = list(result.scalars())
    return SessionCollection(data=items, request_id=get_request_id())


@router.post("/sessions/{session_id}/revoke", response_model=CommandEnvelope)
async def revoke_session(
    session_id: UUID,
    payload: ReasonRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    require_role(principal, {RoleCode.SECURITY_ADMIN})
    async with database.session() as session:
        await require_step_up(
            session,
            principal,
            operation="SESSION_REVOKE",
            emergency_roles=frozenset({RoleCode.SECURITY_ADMIN}),
            request_id=_request_uuid(),
        )
        result = await IdentityAdminService().revoke_session(
            session,
            principal=principal,
            auth_session_id=session_id,
            reason_code=payload.reason_code,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )
        await session.commit()
    return _command(result)


@router.get("/audit", response_model=AuditCollection)
async def list_audit(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=100, ge=1, le=500),
) -> AuditCollection:
    require_role(principal, {RoleCode.AUDITOR, RoleCode.SECURITY_ADMIN})
    async with database.session() as session:
        items = await AuditRepository(session).list_recent(limit=limit)
    return AuditCollection(data=items, request_id=get_request_id())


@router.get("/account-recoveries", response_model=AccountRecoveryCollection)
async def list_account_recoveries(
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> AccountRecoveryCollection:
    require_role(principal, set(RECOVERY_CONTROL_ROLES))
    async with database.session() as session:
        await expire_security_workflows(session)
        result = await session.execute(
            select(AccountRecoveryRequest).order_by(
                AccountRecoveryRequest.created_at.desc(), AccountRecoveryRequest.id
            )
        )
        items = list(result.scalars())
        await session.commit()
    return AccountRecoveryCollection(data=items, request_id=get_request_id())


@router.post("/account-recoveries", response_model=CommandEnvelope, status_code=201)
async def request_account_recovery(
    payload: AccountRecoveryCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    require_role(principal, set(RECOVERY_CONTROL_ROLES))
    async with database.session() as session:
        await require_step_up(
            session,
            principal,
            operation="ACCOUNT_RECOVERY_REQUEST",
            emergency_roles=frozenset({RoleCode.SECURITY_ADMIN, RoleCode.NODE_SECURITY_ADMIN}),
            request_id=_request_uuid(),
        )
        try:
            result = await IdentitySecurityService(settings).request_account_recovery(
                session,
                principal=principal,
                target_user_id=payload.target_user_id,
                temporary_password=payload.temporary_password.get_secret_value(),
                reason_code=payload.reason_code,
                evidence_id=payload.evidence_id,
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _map_integrity_error(exc) from exc
    return _command(result)


@router.post(
    "/account-recoveries/{recovery_id}/decision",
    response_model=CommandEnvelope,
)
async def decide_account_recovery(
    recovery_id: UUID,
    payload: SecurityDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    require_role(principal, set(RECOVERY_CONTROL_ROLES))
    async with database.session() as session:
        await require_step_up(
            session,
            principal,
            operation="ACCOUNT_RECOVERY_DECIDE",
            emergency_roles=frozenset({RoleCode.SECURITY_ADMIN, RoleCode.NODE_SECURITY_ADMIN}),
            request_id=_request_uuid(),
        )
        result = await IdentitySecurityService(settings).decide_account_recovery(
            session,
            principal=principal,
            recovery_id=recovery_id,
            approve=payload.approve,
            reason_code=payload.reason_code,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )
        await session.commit()
    return _command(result)


@router.get("/break-glass", response_model=BreakGlassCollection)
async def list_break_glass(
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> BreakGlassCollection:
    require_role(principal, set(RECOVERY_CONTROL_ROLES))
    async with database.session() as session:
        await expire_security_workflows(session)
        result = await session.execute(
            select(BreakGlassGrant).order_by(BreakGlassGrant.created_at.desc(), BreakGlassGrant.id)
        )
        items = list(result.scalars())
        await session.commit()
    return BreakGlassCollection(data=items, request_id=get_request_id())


@router.post("/break-glass", response_model=CommandEnvelope, status_code=201)
async def request_break_glass(
    payload: BreakGlassCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    require_role(principal, set(RECOVERY_CONTROL_ROLES))
    async with database.session() as session:
        await require_step_up(
            session,
            principal,
            operation="BREAK_GLASS_REQUEST",
            emergency_roles=frozenset({RoleCode.SECURITY_ADMIN, RoleCode.NODE_SECURITY_ADMIN}),
            request_id=_request_uuid(),
        )
        try:
            result = await IdentitySecurityService(settings).request_break_glass(
                session,
                principal=principal,
                target_user_id=payload.target_user_id,
                role=payload.role,
                cooperative_id=payload.cooperative_id,
                duration_minutes=payload.duration_minutes,
                reason_code=payload.reason_code,
                evidence_id=payload.evidence_id,
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _map_integrity_error(exc) from exc
    return _command(result)


@router.post("/break-glass/{grant_id}/decision", response_model=CommandEnvelope)
async def decide_break_glass(
    grant_id: UUID,
    payload: SecurityDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    require_role(principal, set(RECOVERY_CONTROL_ROLES))
    async with database.session() as session:
        await require_step_up(
            session,
            principal,
            operation="BREAK_GLASS_DECIDE",
            emergency_roles=frozenset({RoleCode.SECURITY_ADMIN, RoleCode.NODE_SECURITY_ADMIN}),
            request_id=_request_uuid(),
        )
        result = await IdentitySecurityService(settings).decide_break_glass(
            session,
            principal=principal,
            grant_id=grant_id,
            approve=payload.approve,
            reason_code=payload.reason_code,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )
        await session.commit()
    return _command(result)


@router.post("/break-glass/{grant_id}/revoke", response_model=CommandEnvelope)
async def revoke_break_glass(
    grant_id: UUID,
    payload: ReasonRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    require_role(principal, set(RECOVERY_CONTROL_ROLES))
    async with database.session() as session:
        await require_step_up(
            session,
            principal,
            operation="BREAK_GLASS_REVOKE",
            emergency_roles=frozenset({RoleCode.SECURITY_ADMIN, RoleCode.NODE_SECURITY_ADMIN}),
            request_id=_request_uuid(),
        )
        result = await IdentitySecurityService(settings).revoke_break_glass(
            session,
            principal=principal,
            grant_id=grant_id,
            reason_code=payload.reason_code,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )
        await session.commit()
    return _command(result)
