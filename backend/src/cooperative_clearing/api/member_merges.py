"""Administration endpoints for controlled duplicate-member merges."""

from dataclasses import asdict
from datetime import UTC, datetime
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
from cooperative_clearing.api.member_merge_schemas import (
    MemberMergeCaseCollection,
    MemberMergeCaseResponse,
    MemberMergeCommandEnvelope,
    MemberMergeCommandResponse,
    MemberMergeCreateRequest,
    MemberMergeDecisionRequest,
)
from cooperative_clearing.modules.identity.application.member_merges import (
    READ_ROLES,
    MemberMergeService,
)
from cooperative_clearing.modules.identity.application.security import require_step_up
from cooperative_clearing.modules.identity.domain.types import (
    MemberMergeCaseStatus,
    Principal,
    RoleCode,
)
from cooperative_clearing.modules.identity.infrastructure.models import MemberMergeCase
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/admin", tags=["member-merges"])
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


def _case_view(item: MemberMergeCase) -> MemberMergeCaseResponse:
    view = MemberMergeCaseResponse.model_validate(item)
    if view.status is MemberMergeCaseStatus.PENDING_REVIEW and view.expires_at <= datetime.now(UTC):
        return view.model_copy(update={"status": MemberMergeCaseStatus.EXPIRED})
    return view


@router.get("/member-merge-cases", response_model=MemberMergeCaseCollection)
async def list_member_merge_cases(
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> MemberMergeCaseCollection:
    scopes = _scope(principal)
    statement = select(MemberMergeCase).order_by(
        MemberMergeCase.created_at.desc(), MemberMergeCase.id
    )
    if scopes is not None:
        statement = statement.where(MemberMergeCase.cooperative_id.in_(scopes))
    async with database.session() as session:
        rows = list((await session.execute(statement)).scalars())
    return MemberMergeCaseCollection(
        data=[_case_view(item) for item in rows],
        request_id=get_request_id(),
    )


@router.post(
    "/member-merge-cases",
    response_model=MemberMergeCommandEnvelope,
    status_code=201,
)
async def request_member_merge(
    payload: MemberMergeCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> MemberMergeCommandEnvelope:
    async with database.session() as session:
        try:
            result = await MemberMergeService(settings).request_merge(
                session,
                principal=principal,
                cooperative_id=payload.cooperative_id,
                source_member_id=payload.source_member_id,
                survivor_member_id=payload.survivor_member_id,
                source_expected_version=payload.source_expected_version,
                survivor_expected_version=payload.survivor_expected_version,
                evidence_refs=payload.evidence_refs,
                reason_code=payload.reason_code,
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise DomainError(
                code="MEMBER_MERGE_CONFLICT",
                message_key="errors.identity.member_merge_conflict",
                status_code=409,
            ) from exc
    return MemberMergeCommandEnvelope(
        data=MemberMergeCommandResponse(**asdict(result)),
        request_id=get_request_id(),
    )


@router.post(
    "/member-merge-cases/{merge_case_id}/decision",
    response_model=MemberMergeCommandEnvelope,
    status_code=201,
)
async def decide_member_merge(
    merge_case_id: UUID,
    payload: MemberMergeDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> MemberMergeCommandEnvelope:
    async with database.session() as session:
        try:
            await require_step_up(
                session,
                principal,
                operation="MEMBER_MERGE_DECISION",
                request_id=_request_uuid(),
            )
            result = await MemberMergeService(settings).decide_merge(
                session,
                principal=principal,
                merge_case_id=merge_case_id,
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
                code="MEMBER_MERGE_CONFLICT",
                message_key="errors.identity.member_merge_conflict",
                status_code=409,
            ) from exc
    return MemberMergeCommandEnvelope(
        data=MemberMergeCommandResponse(**asdict(result)),
        request_id=get_request_id(),
    )
