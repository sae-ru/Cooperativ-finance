"""Administration endpoints for contained member exit and succession review."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cooperative_clearing.api.auth import _request_uuid
from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.member_continuity_schemas import (
    MemberContinuityCaseCollection,
    MemberContinuityCaseResponse,
    MemberContinuityCommandEnvelope,
    MemberContinuityCommandResponse,
    MemberContinuityCreateRequest,
    MemberContinuityDecisionRequest,
)
from cooperative_clearing.modules.identity.application.member_continuity import (
    READ_ROLES,
    MemberContinuityService,
)
from cooperative_clearing.modules.identity.application.security import require_step_up
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import MemberContinuityCase
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/admin", tags=["member-continuity"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]


def _scope(principal: Principal) -> set[UUID] | None:
    if principal.must_change_password:
        raise DomainError(
            code="AUTHORIZATION_DENIED",
            message_key="errors.auth.authorization_denied",
            status_code=403,
        )
    if any(
        grant.role in {RoleCode.SECURITY_ADMIN, RoleCode.AUDITOR} and grant.cooperative_id is None
        for grant in principal.roles
    ):
        return None
    scopes = {
        grant.cooperative_id
        for grant in principal.roles
        if grant.role in READ_ROLES and grant.cooperative_id is not None
    }
    if not scopes:
        raise DomainError(
            code="AUTHORIZATION_DENIED",
            message_key="errors.auth.authorization_denied",
            status_code=403,
        )
    return scopes


def _case_view(item: MemberContinuityCase) -> MemberContinuityCaseResponse:
    users = item.access_snapshot.get("users")
    memberships = item.access_snapshot.get("memberships")
    return MemberContinuityCaseResponse.model_validate(item).model_copy(
        update={
            "disabled_user_count": len(users) if isinstance(users, list) else 0,
            "suspended_membership_count": (
                len(memberships) if isinstance(memberships, list) else 0
            ),
        }
    )


@router.get("/member-continuity-cases", response_model=MemberContinuityCaseCollection)
async def list_member_continuity_cases(
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> MemberContinuityCaseCollection:
    scopes = _scope(principal)
    statement = select(MemberContinuityCase).order_by(
        MemberContinuityCase.created_at.desc(), MemberContinuityCase.id
    )
    if scopes is not None:
        statement = statement.where(MemberContinuityCase.cooperative_id.in_(scopes))
    async with database.session() as session:
        rows = list((await session.execute(statement)).scalars())
    return MemberContinuityCaseCollection(
        data=[_case_view(item) for item in rows], request_id=get_request_id()
    )


@router.post(
    "/member-continuity-cases",
    response_model=MemberContinuityCommandEnvelope,
    status_code=201,
)
async def request_member_continuity(
    payload: MemberContinuityCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> MemberContinuityCommandEnvelope:
    async with database.session() as session:
        try:
            result = await MemberContinuityService(settings).request_case(
                session,
                principal=principal,
                cooperative_id=payload.cooperative_id,
                member_id=payload.member_id,
                case_type=payload.case_type,
                expected_member_version=payload.expected_member_version,
                evidence_refs=payload.evidence_refs,
                reason_code=payload.reason_code,
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise DomainError(
                code="MEMBER_CONTINUITY_CONFLICT",
                message_key="errors.identity.member_continuity_conflict",
                status_code=409,
            ) from exc
    return MemberContinuityCommandEnvelope(
        data=MemberContinuityCommandResponse(**asdict(result)), request_id=get_request_id()
    )


@router.post(
    "/member-continuity-cases/{continuity_case_id}/decision",
    response_model=MemberContinuityCommandEnvelope,
    status_code=201,
)
async def decide_member_continuity(
    continuity_case_id: UUID,
    payload: MemberContinuityDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> MemberContinuityCommandEnvelope:
    async with database.session() as session:
        try:
            await require_step_up(
                session,
                principal,
                operation="MEMBER_CONTINUITY_DECISION",
                request_id=_request_uuid(),
            )
            result = await MemberContinuityService(settings).decide_case(
                session,
                principal=principal,
                continuity_case_id=continuity_case_id,
                approve=payload.approve,
                expected_version=payload.expected_version,
                reason_code=payload.reason_code,
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise DomainError(
                code="MEMBER_CONTINUITY_CONFLICT",
                message_key="errors.identity.member_continuity_conflict",
                status_code=409,
            ) from exc
    return MemberContinuityCommandEnvelope(
        data=MemberContinuityCommandResponse(**asdict(result)), request_id=get_request_id()
    )
