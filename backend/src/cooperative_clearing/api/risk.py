"""Bounded share exposure, guarantees, and liability API."""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ColumnElement, false, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.identity_schemas import CommandEnvelope
from cooperative_clearing.api.identity_schemas import CommandResult as ApiCommandResult
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.risk.application.common import RiskCommandResult
from cooperative_clearing.modules.risk.application.compensation import CompensationService
from cooperative_clearing.modules.risk.application.service import RiskService
from cooperative_clearing.modules.risk.domain.types import (
    CommitmentType,
    ExposurePreview,
    FaultClass,
    ShareContour,
    risk_error,
)
from cooperative_clearing.modules.risk.infrastructure.models import (
    CompensationTransfer,
    ExposureCommitment,
    LiabilityCase,
    RelatedPartyLink,
    RiskPolicy,
    ShareAccount,
    ShareContribution,
)
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/risk", tags=["bounded-risk"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]

GLOBAL_READ_ROLES = {RoleCode.AUDITOR, RoleCode.SECURITY_ADMIN}
SCOPED_READ_ROLES = {RoleCode.COOPERATIVE_ADMIN, RoleCode.RISK_ADMIN}


class PolicyProposeRequest(BaseModel):
    cooperative_id: UUID
    denomination: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    max_member_exposure: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    max_related_exposure: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    max_guarantee_chain_depth: int = Field(ge=1, le=20)
    protected_amount_rule: str = Field(min_length=2, max_length=4000)
    related_party_rule: str = Field(min_length=2, max_length=4000)
    approval_reference: str = Field(min_length=2, max_length=300)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class PolicyApprovalRequest(BaseModel):
    terms_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_version: int = Field(ge=1)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class AccountOpenRequest(BaseModel):
    policy_id: UUID
    member_id: UUID
    contour: ShareContour
    opening_balance: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    protected_amount: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    source_reference: str = Field(min_length=2, max_length=300)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class ContributionRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    source_reference: str = Field(min_length=2, max_length=300)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=1)


class RelatedLinkProposeRequest(BaseModel):
    cooperative_id: UUID
    member_a_id: UUID
    member_b_id: UUID
    relation_type: Literal["HOUSEHOLD", "CONTROL", "RELATED"]
    source_statement: str = Field(min_length=2, max_length=4000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class RelatedLinkDecisionRequest(BaseModel):
    approve: bool
    decision_notes: str = Field(min_length=2, max_length=2000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=1)


class ExposurePreviewRequest(BaseModel):
    account_id: UUID
    policy_id: UUID
    commitment_type: CommitmentType
    amount_reserved: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    max_loss: Decimal = Field(gt=0, max_digits=38, decimal_places=12)


class CommitmentProposeRequest(BaseModel):
    account_id: UUID
    policy_id: UUID
    commitment_type: CommitmentType
    risk_type: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    risk_id: UUID
    debtor_member_id: UUID | None = None
    beneficiary_member_id: UUID | None = None
    role_assignment_id: UUID | None = None
    amount_reserved: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    max_loss: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    coverage_ratio: Decimal = Field(gt=0, le=1, max_digits=7, decimal_places=6)
    starts_at: datetime
    expires_at: datetime
    release_condition: str = Field(min_length=2, max_length=4000)
    trigger_conditions: str = Field(min_length=2, max_length=4000)
    exclusions: str = Field(min_length=2, max_length=4000)


class CommitmentAcceptanceRequest(BaseModel):
    terms_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_version: int = Field(ge=1)


class CommitmentReleaseRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=2000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=1)


class LiabilityOpenRequest(BaseModel):
    commitment_id: UUID
    incident_reference: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    affected_amount: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    facts: str = Field(min_length=2, max_length=8000)
    causal_graph: dict[str, object]
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class LiabilityAssessmentRequest(BaseModel):
    fault_class: FaultClass
    assessed_loss: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    rationale: str = Field(min_length=2, max_length=8000)
    appeal_until: datetime
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=1)


class CompensationAuthorizeRequest(BaseModel):
    trust_case_id: UUID
    trust_decision_id: UUID
    destination_account_id: UUID
    amount: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    rationale: str = Field(min_length=2, max_length=8000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_liability_version: int = Field(ge=1)
    expected_source_account_version: int = Field(ge=1)
    expected_destination_account_version: int = Field(ge=1)
    expected_commitment_version: int = Field(ge=1)


class CompensationAcceptRequest(BaseModel):
    expected_version: int = Field(ge=1)


class CompensationVoidRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=4000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=1)

class PolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    policy_version: int
    denomination: str
    max_member_exposure: Decimal
    max_related_exposure: Decimal
    max_guarantee_chain_depth: int
    terms_hash: str
    terms_payload: dict[str, object]
    status: str
    proposed_by_member_id: UUID
    proposed_event_id: UUID
    approved_by_member_id: UUID | None
    approved_event_id: UUID | None
    created_at: datetime
    approved_at: datetime | None
    version: int


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    member_id: UUID
    opening_policy_id: UUID
    contour: str
    denomination: str
    balance: Decimal
    protected_amount: Decimal
    executed_not_settled: Decimal
    status: str
    created_event_id: UUID
    last_event_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int


class ContributionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account_id: UUID
    amount: Decimal
    entry_type: str
    source_reference: str
    recorded_by_user_id: UUID
    event_id: UUID
    created_at: datetime


class RelatedLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    member_a_id: UUID
    member_b_id: UUID
    relation_type: str
    source_statement: str
    status: str
    proposed_by_member_id: UUID
    proposed_event_id: UUID
    decided_by_member_id: UUID | None
    decision_event_id: UUID | None
    created_at: datetime
    decided_at: datetime | None
    version: int


class CommitmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    policy_id: UUID
    account_id: UUID
    owner_member_id: UUID
    commitment_type: str
    risk_type: str
    risk_id: UUID
    debtor_member_id: UUID | None
    beneficiary_member_id: UUID | None
    role_assignment_id: UUID | None
    amount_reserved: Decimal
    max_loss: Decimal
    executed_amount: Decimal
    coverage_ratio: Decimal
    starts_at: datetime
    expires_at: datetime
    release_condition: str
    trigger_conditions: str
    exclusions: str
    terms_hash: str
    terms_payload: dict[str, object]
    status: str
    proposed_by_member_id: UUID
    proposed_event_id: UUID
    accepted_by_user_id: UUID | None
    accepted_event_id: UUID | None
    released_event_id: UUID | None
    release_reason: str | None
    created_at: datetime
    accepted_at: datetime | None
    released_at: datetime | None
    version: int


class LiabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    commitment_id: UUID
    incident_reference: str
    responsible_member_id: UUID
    affected_amount: Decimal
    facts: str
    causal_graph: dict[str, object]
    status: str
    opened_by_member_id: UUID
    opened_event_id: UUID
    fault_class: str | None
    assessed_loss: Decimal | None
    coverage_summary: dict[str, object] | None
    assessment_rationale: str | None
    assessed_by_member_id: UUID | None
    assessed_event_id: UUID | None
    appeal_until: datetime | None
    created_at: datetime
    assessed_at: datetime | None
    version: int


class CompensationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    liability_case_id: UUID
    trust_case_id: UUID
    trust_decision_id: UUID
    commitment_id: UUID
    source_account_id: UUID
    destination_account_id: UUID
    responsible_member_id: UUID
    recipient_member_id: UUID
    amount: Decimal
    denomination: str
    rationale: str
    status: str
    authorized_by_member_id: UUID
    authorized_event_id: UUID
    accepted_by_member_id: UUID | None
    accepted_event_id: UUID | None
    voided_by_member_id: UUID | None
    voided_event_id: UUID | None
    void_reason: str | None
    source_balance_before: Decimal | None
    source_balance_after: Decimal | None
    destination_balance_before: Decimal | None
    destination_balance_after: Decimal | None
    authorized_at: datetime
    accepted_at: datetime | None
    voided_at: datetime | None
    updated_at: datetime
    version: int

class ExposurePreviewResponse(BaseModel):
    account_available_before: Decimal
    account_available_after: Decimal
    member_exposure_before: Decimal
    member_exposure_after: Decimal
    related_exposure_before: Decimal
    related_exposure_after: Decimal
    max_member_exposure: Decimal
    max_related_exposure: Decimal
    allowed: bool
    reason_code: str | None


class ExposurePreviewEnvelope(BaseModel):
    data: ExposurePreviewResponse
    request_id: str


class Collection[T](BaseModel):
    data: list[T]
    request_id: str


CommandAction = Callable[[AsyncSession], Awaitable[RiskCommandResult]]


def _request_uuid() -> UUID | None:
    try:
        return UUID(get_request_id())
    except ValueError:
        return None


def _require_readable(principal: Principal) -> None:
    if principal.must_change_password:
        raise DomainError(
            code="PASSWORD_CHANGE_REQUIRED",
            message_key="errors.auth.password_change_required",
            status_code=403,
        )


def _has_global_role(principal: Principal, roles: set[RoleCode]) -> bool:
    return any(grant.role in roles and grant.cooperative_id is None for grant in principal.roles)


def _admin_scopes(principal: Principal) -> set[UUID] | None:
    _require_readable(principal)
    if _has_global_role(principal, GLOBAL_READ_ROLES | SCOPED_READ_ROLES):
        return None
    return {
        grant.cooperative_id
        for grant in principal.roles
        if grant.role in SCOPED_READ_ROLES and grant.cooperative_id is not None
    }


def _policy_filter(principal: Principal) -> ColumnElement[bool] | None:
    _require_readable(principal)
    if _has_global_role(principal, GLOBAL_READ_ROLES | SCOPED_READ_ROLES):
        return None
    cooperative_ids = {
        grant.cooperative_id for grant in principal.roles if grant.cooperative_id is not None
    }
    return RiskPolicy.cooperative_id.in_(cooperative_ids) if cooperative_ids else false()


def _account_filter(principal: Principal) -> ColumnElement[bool] | None:
    scopes = _admin_scopes(principal)
    if scopes is None:
        return None
    conditions: list[ColumnElement[bool]] = []
    if scopes:
        conditions.append(ShareAccount.cooperative_id.in_(scopes))
    if principal.member_id is not None:
        conditions.append(ShareAccount.member_id == principal.member_id)
    return or_(*conditions) if conditions else false()


def _commitment_filter(principal: Principal) -> ColumnElement[bool] | None:
    scopes = _admin_scopes(principal)
    if scopes is None:
        return None
    conditions: list[ColumnElement[bool]] = []
    if scopes:
        conditions.append(ExposureCommitment.cooperative_id.in_(scopes))
    if principal.member_id is not None:
        member_id = principal.member_id
        conditions.extend(
            [
                ExposureCommitment.owner_member_id == member_id,
                ExposureCommitment.debtor_member_id == member_id,
                ExposureCommitment.beneficiary_member_id == member_id,
            ]
        )
    return or_(*conditions) if conditions else false()


def _related_filter(principal: Principal) -> ColumnElement[bool] | None:
    scopes = _admin_scopes(principal)
    if scopes is None:
        return None
    conditions: list[ColumnElement[bool]] = []
    if scopes:
        conditions.append(RelatedPartyLink.cooperative_id.in_(scopes))
    if principal.member_id is not None:
        conditions.extend(
            [
                RelatedPartyLink.member_a_id == principal.member_id,
                RelatedPartyLink.member_b_id == principal.member_id,
            ]
        )
    return or_(*conditions) if conditions else false()


def _liability_filter(principal: Principal) -> ColumnElement[bool] | None:
    scopes = _admin_scopes(principal)
    if scopes is None:
        return None
    conditions: list[ColumnElement[bool]] = []
    if scopes:
        conditions.append(LiabilityCase.cooperative_id.in_(scopes))
    if principal.member_id is not None:
        conditions.append(LiabilityCase.responsible_member_id == principal.member_id)
    return or_(*conditions) if conditions else false()


def _compensation_filter(principal: Principal) -> ColumnElement[bool] | None:
    scopes = _admin_scopes(principal)
    if scopes is None:
        return None
    conditions: list[ColumnElement[bool]] = []
    if scopes:
        conditions.append(CompensationTransfer.cooperative_id.in_(scopes))
    if principal.member_id is not None:
        conditions.extend(
            [
                CompensationTransfer.responsible_member_id == principal.member_id,
                CompensationTransfer.recipient_member_id == principal.member_id,
            ]
        )
    return or_(*conditions) if conditions else false()

def _command(result: RiskCommandResult) -> CommandEnvelope:
    return CommandEnvelope(
        data=ApiCommandResult(
            event_id=result.event_id,
            object_id=result.object_id,
            replayed=result.replayed,
        ),
        request_id=get_request_id(),
    )


async def _commit_command(database: DatabaseDependency, action: CommandAction) -> CommandEnvelope:
    async with database.session() as session:
        try:
            result = await action(session)
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise risk_error("CONFLICT", 409) from exc
    return _command(result)


@router.get("/policies", response_model=Collection[PolicyResponse])
async def list_policies(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[PolicyResponse]:
    condition = _policy_filter(principal)
    statement = select(RiskPolicy).order_by(
        RiskPolicy.cooperative_id, RiskPolicy.policy_version.desc()
    )
    if condition is not None:
        statement = statement.where(condition)
    if status is not None:
        statement = statement.where(RiskPolicy.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/accounts", response_model=Collection[AccountResponse])
async def list_accounts(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[AccountResponse]:
    condition = _account_filter(principal)
    statement = select(ShareAccount).order_by(ShareAccount.updated_at.desc(), ShareAccount.id)
    if condition is not None:
        statement = statement.where(condition)
    if status is not None:
        statement = statement.where(ShareAccount.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get(
    "/accounts/{account_id}/contributions",
    response_model=Collection[ContributionResponse],
)
async def list_contributions(
    account_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> Collection[ContributionResponse]:
    condition = _account_filter(principal)
    visible = select(ShareAccount.id).where(ShareAccount.id == account_id)
    if condition is not None:
        visible = visible.where(condition)
    async with database.session() as session:
        if (await session.execute(visible)).scalar_one_or_none() is None:
            raise risk_error("ACCOUNT_NOT_FOUND", 404)
        items = list(
            (
                await session.execute(
                    select(ShareContribution)
                    .where(ShareContribution.account_id == account_id)
                    .order_by(ShareContribution.created_at, ShareContribution.id)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/related-links", response_model=Collection[RelatedLinkResponse])
async def list_related_links(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=24),
) -> Collection[RelatedLinkResponse]:
    condition = _related_filter(principal)
    statement = select(RelatedPartyLink).order_by(
        RelatedPartyLink.created_at.desc(), RelatedPartyLink.id
    )
    if condition is not None:
        statement = statement.where(condition)
    if status is not None:
        statement = statement.where(RelatedPartyLink.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/commitments", response_model=Collection[CommitmentResponse])
async def list_commitments(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[CommitmentResponse]:
    condition = _commitment_filter(principal)
    statement = select(ExposureCommitment).order_by(
        ExposureCommitment.created_at.desc(), ExposureCommitment.id
    )
    if condition is not None:
        statement = statement.where(condition)
    if status is not None:
        statement = statement.where(ExposureCommitment.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/liability-cases", response_model=Collection[LiabilityResponse])
async def list_liability_cases(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[LiabilityResponse]:
    condition = _liability_filter(principal)
    statement = select(LiabilityCase).order_by(LiabilityCase.created_at.desc(), LiabilityCase.id)
    if condition is not None:
        statement = statement.where(condition)
    if status is not None:
        statement = statement.where(LiabilityCase.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/compensations", response_model=Collection[CompensationResponse])
async def list_compensations(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=24),
) -> Collection[CompensationResponse]:
    condition = _compensation_filter(principal)
    statement = select(CompensationTransfer).order_by(
        CompensationTransfer.authorized_at.desc(), CompensationTransfer.id
    )
    if condition is not None:
        statement = statement.where(condition)
    if status is not None:
        statement = statement.where(CompensationTransfer.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())

@router.post("/exposure-previews", response_model=ExposurePreviewEnvelope)
async def preview_exposure(
    payload: ExposurePreviewRequest,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> ExposurePreviewEnvelope:
    _require_readable(principal)
    async with database.session() as session:
        account = await session.get(ShareAccount, payload.account_id)
        if account is None:
            raise risk_error("ACCOUNT_NOT_FOUND", 404)
        scopes = _admin_scopes(principal)
        is_operator = scopes is None or account.cooperative_id in scopes
        if principal.member_id != account.member_id and not is_operator:
            raise risk_error("ACCOUNT_NOT_FOUND", 404)
        preview: ExposurePreview = await RiskService(settings).preview_commitment(
            session, **payload.model_dump()
        )
    return ExposurePreviewEnvelope(
        data=ExposurePreviewResponse(**asdict(preview)),
        request_id=get_request_id(),
    )


@router.post("/policies", response_model=CommandEnvelope, status_code=201)
async def propose_policy(
    payload: PolicyProposeRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await RiskService(settings).propose_policy(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/policies/{policy_id}/approval", response_model=CommandEnvelope, status_code=201)
async def approve_policy(
    policy_id: UUID,
    payload: PolicyApprovalRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await RiskService(settings).approve_policy(
            session,
            principal=principal,
            policy_id=policy_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/accounts", response_model=CommandEnvelope, status_code=201)
async def open_account(
    payload: AccountOpenRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await RiskService(settings).open_account(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/accounts/{account_id}/contributions",
    response_model=CommandEnvelope,
    status_code=201,
)
async def add_contribution(
    account_id: UUID,
    payload: ContributionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await RiskService(settings).add_contribution(
            session,
            principal=principal,
            account_id=account_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/related-links", response_model=CommandEnvelope, status_code=201)
async def propose_related_link(
    payload: RelatedLinkProposeRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await RiskService(settings).propose_related_link(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/related-links/{link_id}/decision",
    response_model=CommandEnvelope,
    status_code=201,
)
async def decide_related_link(
    link_id: UUID,
    payload: RelatedLinkDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await RiskService(settings).decide_related_link(
            session,
            principal=principal,
            link_id=link_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/commitments", response_model=CommandEnvelope, status_code=201)
async def propose_commitment(
    payload: CommitmentProposeRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await RiskService(settings).propose_commitment(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/commitments/{commitment_id}/acceptance",
    response_model=CommandEnvelope,
    status_code=201,
)
async def accept_commitment(
    commitment_id: UUID,
    payload: CommitmentAcceptanceRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await RiskService(settings).accept_commitment(
            session,
            principal=principal,
            commitment_id=commitment_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/commitments/{commitment_id}/release",
    response_model=CommandEnvelope,
    status_code=201,
)
async def release_commitment(
    commitment_id: UUID,
    payload: CommitmentReleaseRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await RiskService(settings).release_commitment(
            session,
            principal=principal,
            commitment_id=commitment_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/liability-cases", response_model=CommandEnvelope, status_code=201)
async def open_liability_case(
    payload: LiabilityOpenRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await RiskService(settings).open_liability_case(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/liability-cases/{case_id}/assessment",
    response_model=CommandEnvelope,
    status_code=201,
)
async def assess_liability_case(
    case_id: UUID,
    payload: LiabilityAssessmentRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await RiskService(settings).assess_liability_case(
            session,
            principal=principal,
            case_id=case_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)

@router.post(
    "/liability-cases/{case_id}/compensations",
    response_model=CommandEnvelope,
    status_code=201,
)
async def authorize_compensation(
    case_id: UUID,
    payload: CompensationAuthorizeRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await CompensationService(settings).authorize(
            session,
            principal=principal,
            liability_case_id=case_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/compensations/{transfer_id}/acceptance",
    response_model=CommandEnvelope,
    status_code=201,
)
async def accept_compensation(
    transfer_id: UUID,
    payload: CompensationAcceptRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await CompensationService(settings).accept(
            session,
            principal=principal,
            transfer_id=transfer_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/compensations/{transfer_id}/void",
    response_model=CommandEnvelope,
    status_code=201,
)
async def void_compensation(
    transfer_id: UUID,
    payload: CompensationVoidRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await CompensationService(settings).void(
            session,
            principal=principal,
            transfer_id=transfer_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)
