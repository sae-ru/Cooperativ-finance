"""Privacy-aware API for voluntary aid without debt or reputation side effects."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ColumnElement, false, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.identity_schemas import CommandEnvelope
from cooperative_clearing.api.identity_schemas import CommandResult as ApiCommandResult
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, require_role
from cooperative_clearing.modules.solidarity.application.common import SolidarityCommandResult
from cooperative_clearing.modules.solidarity.application.service import SolidarityService
from cooperative_clearing.modules.solidarity.domain.types import (
    ContributionForm,
    DeliveryAttestorKind,
    NeedCategory,
    PrivacyScope,
    ResidueRule,
    solidarity_error,
)
from cooperative_clearing.modules.solidarity.infrastructure.models import (
    AidAllocation,
    AidApplication,
    AidCampaign,
    AidDelivery,
    AllocationApproval,
    CampaignReport,
    Contribution,
    Pledge,
    SolidarityComplaint,
    SolidarityFund,
)
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/solidarity", tags=["solidarity-aid"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]

GLOBAL_READ_ROLES = {RoleCode.AUDITOR, RoleCode.SECURITY_ADMIN}
STAFF_READ_ROLES = {
    RoleCode.COOPERATIVE_ADMIN,
    RoleCode.SOLIDARITY_OPERATOR,
    RoleCode.SOLIDARITY_CONTROLLER,
    RoleCode.AUDITOR,
    RoleCode.SECURITY_ADMIN,
}


class VersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class FundProposeRequest(BaseModel):
    cooperative_id: UUID
    fund_code: str = Field(min_length=2, max_length=48, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=200)
    purpose: str = Field(min_length=2, max_length=5_000)
    residue_rule: ResidueRule
    admin_expense_limit: Decimal = Field(ge=0, le=1, max_digits=8, decimal_places=7)
    terms: dict[str, object]


class CampaignCreateRequest(BaseModel):
    fund_id: UUID
    campaign_code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    title: str = Field(min_length=2, max_length=200)
    public_purpose: str = Field(min_length=2, max_length=5_000)
    eligibility_policy: dict[str, object]
    accepted_forms: list[ContributionForm] = Field(min_length=1, max_length=6)
    starts_at: datetime
    ends_at: datetime


class PledgeCreateRequest(BaseModel):
    donor_member_id: UUID
    contribution_form: ContributionForm
    unit_code: str = Field(min_length=1, max_length=24)
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    description: str = Field(min_length=1, max_length=2_000)
    expires_at: datetime


class ContributionReceiveRequest(BaseModel):
    campaign_id: UUID
    pledge_id: UUID | None = None
    donor_member_id: UUID
    contribution_form: ContributionForm
    unit_code: str = Field(min_length=1, max_length=24)
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    description: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)


class ContributionVerifyRequest(BaseModel):
    expected_version: int = Field(ge=1)
    accepted: bool
    verification_note: str = Field(min_length=2, max_length=5_000)


class ApplicationSubmitRequest(BaseModel):
    recipient_member_id: UUID
    need_category: NeedCategory
    requested_form: ContributionForm
    requested_unit_code: str = Field(min_length=1, max_length=24)
    requested_quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    privacy_scope: PrivacyScope = PrivacyScope.RESTRICTED
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)


class ApplicationReviewRequest(BaseModel):
    expected_version: int = Field(ge=1)
    eligible: bool
    eligibility_note: str = Field(min_length=2, max_length=5_000)


class AllocationProposeRequest(BaseModel):
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    public_summary: str = Field(min_length=2, max_length=240)
    rationale: str = Field(min_length=2, max_length=5_000)


class AllocationApproveRequest(BaseModel):
    expected_version: int = Field(ge=1)
    allocation_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    approved: bool
    conflict_statement: str = Field(min_length=2, max_length=5_000)


class DeliveryRecordRequest(BaseModel):
    expected_version: int = Field(ge=1)
    attestor_kind: DeliveryAttestorKind
    acknowledgement: str = Field(min_length=2, max_length=5_000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class ComplaintOpenRequest(BaseModel):
    allocation_id: UUID | None = None
    contribution_id: UUID | None = None
    category: Literal["ELIGIBILITY", "ALLOCATION", "DELIVERY", "CONTRIBUTION", "PRIVACY", "OTHER"]
    summary: str = Field(min_length=2, max_length=240)
    privacy_scope: PrivacyScope = PrivacyScope.RESTRICTED
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)


class ComplaintResolveRequest(BaseModel):
    expected_version: int = Field(ge=1)
    accepted: bool
    resolution_action: Literal["RESTORE_ALLOCATION", "CANCEL_ALLOCATION", "NOTE_ONLY"]
    resolution_note: str = Field(min_length=2, max_length=5_000)


class CampaignCloseRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reconciliation_note: str = Field(min_length=2, max_length=5_000)


class FundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    fund_code: str
    name: str
    purpose: str
    policy_version: int
    residue_rule: str
    admin_expense_limit: Decimal
    terms_hash: str
    status: str
    proposed_by_member_id: UUID
    approved_by_member_id: UUID | None
    created_at: datetime
    approved_at: datetime | None
    version: int


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    fund_id: UUID
    campaign_code: str
    title: str
    public_purpose: str
    accepted_forms: list[str]
    starts_at: datetime
    ends_at: datetime
    residue_rule: str
    terms_hash: str
    status: str
    created_by_member_id: UUID
    opened_by_member_id: UUID | None
    closed_by_member_id: UUID | None
    created_at: datetime
    opened_at: datetime | None
    closed_at: datetime | None
    version: int


class PledgeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: UUID
    donor_member_id: UUID
    contribution_form: str
    unit_code: str
    quantity: Decimal
    description: str
    status: str
    expires_at: datetime
    fulfilled_contribution_id: UUID | None
    created_at: datetime
    version: int


class SolidarityContributionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: UUID
    pledge_id: UUID | None
    donor_member_id: UUID
    contribution_form: str
    unit_code: str
    quantity: Decimal
    description: str
    status: str
    received_by_member_id: UUID
    verified_by_member_id: UUID | None
    verification_note: str | None
    received_at: datetime
    verified_at: datetime | None
    version: int


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: UUID
    recipient_member_id: UUID
    need_category: str
    requested_form: str
    requested_unit_code: str
    requested_quantity: Decimal
    privacy_scope: str
    status: str
    submitted_by_member_id: UUID
    reviewed_by_member_id: UUID | None
    eligibility_note: str | None
    submitted_at: datetime
    reviewed_at: datetime | None
    version: int


class AllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: UUID
    application_id: UUID
    recipient_member_id: UUID
    contribution_form: str
    unit_code: str
    quantity: Decimal
    public_summary: str
    rationale: str
    policy_terms_hash: str
    allocation_hash: str
    status: str
    proposed_by_member_id: UUID
    created_at: datetime
    version: int


class SolidarityApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    allocation_id: UUID
    decision: str
    allocation_hash: str
    conflict_statement: str
    decided_by_member_id: UUID
    decided_event_id: UUID
    decided_at: datetime


class DeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    allocation_id: UUID
    recipient_member_id: UUID
    attestor_kind: str
    attested_by_member_id: UUID
    acknowledgement: str
    delivered_event_id: UUID
    delivered_at: datetime


class ComplaintResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: UUID
    allocation_id: UUID | None
    contribution_id: UUID | None
    complainant_member_id: UUID
    category: str
    summary: str
    privacy_scope: str
    status: str
    resolved_by_member_id: UUID | None
    resolution_action: str | None
    resolution_note: str | None
    opened_at: datetime
    resolved_at: datetime | None
    version: int


class ReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: UUID
    cooperative_id: UUID
    bucket_totals: list[dict[str, object]]
    contribution_count: int
    allocation_count: int
    delivery_count: int
    complaint_count: int
    residue_rule: str
    responsibility_snapshot: list[dict[str, object]]
    report_hash: str
    generated_at: datetime


class BucketBalanceResponse(BaseModel):
    contribution_form: str
    unit_code: str
    verified: Decimal
    reserved_or_delivered: Decimal
    available: Decimal


class OperatorWorkspaceResponse(BaseModel):
    campaigns: list[CampaignResponse]
    verified_contributions: list[SolidarityContributionResponse]
    eligible_applications: list[ApplicationResponse]
    active_allocations: list[AllocationResponse]


class ControllerWorkspaceResponse(BaseModel):
    draft_funds: list[FundResponse]
    draft_campaigns: list[CampaignResponse]
    received_contributions: list[SolidarityContributionResponse]
    submitted_applications: list[ApplicationResponse]
    proposed_allocations: list[AllocationResponse]
    open_complaints: list[ComplaintResponse]


class Collection[T](BaseModel):
    data: list[T]
    request_id: str


class ObjectEnvelope[T](BaseModel):
    data: T
    request_id: str


CommandAction = Callable[[AsyncSession], Awaitable[SolidarityCommandResult]]


def _request_uuid() -> UUID | None:
    try:
        return UUID(get_request_id())
    except ValueError:
        return None


def _command(result: SolidarityCommandResult) -> CommandEnvelope:
    return CommandEnvelope(
        data=ApiCommandResult(
            event_id=result.event_id,
            object_id=result.object_id,
            replayed=result.replayed,
        ),
        request_id=get_request_id(),
    )


async def _commit(database: DatabaseDependency, action: CommandAction) -> CommandEnvelope:
    async with database.session() as session:
        try:
            result = await action(session)
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise solidarity_error("CONFLICT") from exc
    return _command(result)


def _require_readable(principal: Principal) -> None:
    if principal.must_change_password:
        raise DomainError(
            code="PASSWORD_CHANGE_REQUIRED",
            message_key="errors.auth.password_change_required",
            status_code=403,
        )


def _global_reader(principal: Principal) -> bool:
    return any(
        grant.role in GLOBAL_READ_ROLES and grant.cooperative_id is None
        for grant in principal.roles
    )


def _public_scopes(principal: Principal) -> set[UUID] | None:
    _require_readable(principal)
    if _global_reader(principal):
        return None
    scopes = {grant.cooperative_id for grant in principal.roles if grant.cooperative_id is not None}
    if not scopes and principal.member_id is not None:
        return None
    return scopes


def _staff_scopes(principal: Principal) -> set[UUID] | None:
    _require_readable(principal)
    if _global_reader(principal):
        return None
    return {
        grant.cooperative_id
        for grant in principal.roles
        if grant.role in STAFF_READ_ROLES and grant.cooperative_id is not None
    }


def _public_condition(
    principal: Principal, cooperative_column: InstrumentedAttribute[UUID]
) -> ColumnElement[bool] | None:
    scopes = _public_scopes(principal)
    if scopes is None:
        return None
    return cooperative_column.in_(scopes) if scopes else false()


def _private_condition(
    principal: Principal,
    cooperative_column: InstrumentedAttribute[UUID],
    owner_column: InstrumentedAttribute[UUID],
    *,
    staff_only: bool = False,
) -> ColumnElement[bool] | None:
    scopes = _staff_scopes(principal)
    if scopes is None:
        return None
    conditions: list[ColumnElement[bool]] = []
    if scopes:
        conditions.append(cooperative_column.in_(scopes))
    if not staff_only and principal.member_id is not None:
        conditions.append(owner_column == principal.member_id)
    return or_(*conditions) if conditions else false()


def _role_scopes(principal: Principal, role: RoleCode) -> set[UUID] | None:
    require_role(principal, {role})
    grants = [grant for grant in principal.roles if grant.role == role]
    if any(grant.cooperative_id is None for grant in grants):
        return None
    return {grant.cooperative_id for grant in grants if grant.cooperative_id is not None}


async def _visible_campaign(
    session: AsyncSession, principal: Principal, campaign_id: UUID
) -> AidCampaign:
    condition = _public_condition(principal, AidCampaign.cooperative_id)
    statement = select(AidCampaign).where(AidCampaign.id == campaign_id)
    if condition is not None:
        statement = statement.where(condition)
    campaign = await session.scalar(statement)
    if campaign is None:
        raise solidarity_error("CAMPAIGN_NOT_FOUND", 404)
    return campaign


async def _visible_allocation(
    session: AsyncSession, principal: Principal, allocation_id: UUID, *, staff_only: bool = False
) -> AidAllocation:
    condition = _private_condition(
        principal,
        AidCampaign.cooperative_id,
        AidAllocation.recipient_member_id,
        staff_only=staff_only,
    )
    statement = (
        select(AidAllocation)
        .join(AidCampaign, AidCampaign.id == AidAllocation.campaign_id)
        .where(AidAllocation.id == allocation_id)
    )
    if condition is not None:
        statement = statement.where(condition)
    allocation = await session.scalar(statement)
    if allocation is None:
        raise solidarity_error("ALLOCATION_NOT_FOUND", 404)
    return allocation


@router.get("/funds", response_model=Collection[FundResponse])
async def list_funds(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[FundResponse]:
    condition = _public_condition(principal, SolidarityFund.cooperative_id)
    statement = select(SolidarityFund).order_by(SolidarityFund.created_at.desc())
    if condition is not None:
        statement = statement.where(condition)
    if status:
        statement = statement.where(SolidarityFund.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/campaigns", response_model=Collection[CampaignResponse])
async def list_campaigns(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[CampaignResponse]:
    condition = _public_condition(principal, AidCampaign.cooperative_id)
    statement = select(AidCampaign).order_by(AidCampaign.created_at.desc())
    if condition is not None:
        statement = statement.where(condition)
    if status:
        statement = statement.where(AidCampaign.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/campaigns/{campaign_id}", response_model=ObjectEnvelope[CampaignResponse])
async def get_campaign(
    campaign_id: UUID, principal: PrincipalDependency, database: DatabaseDependency
) -> ObjectEnvelope[CampaignResponse]:
    async with database.session() as session:
        campaign = await _visible_campaign(session, principal, campaign_id)
    return ObjectEnvelope(data=campaign, request_id=get_request_id())


@router.get("/campaigns/{campaign_id}/balances", response_model=Collection[BucketBalanceResponse])
async def get_campaign_balances(
    campaign_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> Collection[BucketBalanceResponse]:
    async with database.session() as session:
        await _visible_campaign(session, principal, campaign_id)
        balances = await SolidarityService(settings).campaign_balances(session, campaign_id)
    data = [
        BucketBalanceResponse(
            contribution_form=item.bucket.contribution_form.value,
            unit_code=item.bucket.unit_code,
            verified=item.verified,
            reserved_or_delivered=item.reserved_or_delivered,
            available=item.available,
        )
        for item in balances
    ]
    return Collection(data=data, request_id=get_request_id())


@router.get("/pledges", response_model=Collection[PledgeResponse])
async def list_pledges(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    campaign_id: UUID | None = None,
) -> Collection[PledgeResponse]:
    condition = _private_condition(principal, AidCampaign.cooperative_id, Pledge.donor_member_id)
    statement = select(Pledge).join(AidCampaign, AidCampaign.id == Pledge.campaign_id)
    if condition is not None:
        statement = statement.where(condition)
    if campaign_id:
        statement = statement.where(Pledge.campaign_id == campaign_id)
    async with database.session() as session:
        items = list(
            (
                await session.execute(statement.order_by(Pledge.created_at.desc()).limit(500))
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/contributions", response_model=Collection[SolidarityContributionResponse])
async def list_contributions(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    campaign_id: UUID | None = None,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[SolidarityContributionResponse]:
    condition = _private_condition(
        principal, AidCampaign.cooperative_id, Contribution.donor_member_id
    )
    statement = select(Contribution).join(AidCampaign, AidCampaign.id == Contribution.campaign_id)
    if condition is not None:
        statement = statement.where(condition)
    if campaign_id:
        statement = statement.where(Contribution.campaign_id == campaign_id)
    if status:
        statement = statement.where(Contribution.status == status.upper())
    async with database.session() as session:
        items = list(
            (
                await session.execute(
                    statement.order_by(Contribution.received_at.desc()).limit(500)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/applications", response_model=Collection[ApplicationResponse])
async def list_applications(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    campaign_id: UUID | None = None,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[ApplicationResponse]:
    condition = _private_condition(
        principal, AidCampaign.cooperative_id, AidApplication.recipient_member_id
    )
    statement = select(AidApplication).join(
        AidCampaign, AidCampaign.id == AidApplication.campaign_id
    )
    if condition is not None:
        statement = statement.where(condition)
    if campaign_id:
        statement = statement.where(AidApplication.campaign_id == campaign_id)
    if status:
        statement = statement.where(AidApplication.status == status.upper())
    async with database.session() as session:
        items = list(
            (
                await session.execute(
                    statement.order_by(AidApplication.submitted_at.desc()).limit(500)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/allocations", response_model=Collection[AllocationResponse])
async def list_allocations(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    campaign_id: UUID | None = None,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[AllocationResponse]:
    condition = _private_condition(
        principal, AidCampaign.cooperative_id, AidAllocation.recipient_member_id
    )
    statement = select(AidAllocation).join(AidCampaign, AidCampaign.id == AidAllocation.campaign_id)
    if condition is not None:
        statement = statement.where(condition)
    if campaign_id:
        statement = statement.where(AidAllocation.campaign_id == campaign_id)
    if status:
        statement = statement.where(AidAllocation.status == status.upper())
    async with database.session() as session:
        items = list(
            (
                await session.execute(
                    statement.order_by(AidAllocation.created_at.desc()).limit(500)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get(
    "/allocations/{allocation_id}/approval",
    response_model=ObjectEnvelope[SolidarityApprovalResponse],
)
async def get_allocation_approval(
    allocation_id: UUID, principal: PrincipalDependency, database: DatabaseDependency
) -> ObjectEnvelope[SolidarityApprovalResponse]:
    async with database.session() as session:
        await _visible_allocation(session, principal, allocation_id, staff_only=True)
        approval = await session.scalar(
            select(AllocationApproval).where(AllocationApproval.allocation_id == allocation_id)
        )
    if approval is None:
        raise solidarity_error("ALLOCATION_APPROVAL_NOT_FOUND", 404)
    return ObjectEnvelope(data=approval, request_id=get_request_id())


@router.get("/deliveries", response_model=Collection[DeliveryResponse])
async def list_deliveries(
    principal: PrincipalDependency, database: DatabaseDependency, campaign_id: UUID | None = None
) -> Collection[DeliveryResponse]:
    condition = _private_condition(
        principal, AidCampaign.cooperative_id, AidDelivery.recipient_member_id
    )
    statement = (
        select(AidDelivery)
        .join(AidAllocation, AidAllocation.id == AidDelivery.allocation_id)
        .join(AidCampaign, AidCampaign.id == AidAllocation.campaign_id)
    )
    if condition is not None:
        statement = statement.where(condition)
    if campaign_id:
        statement = statement.where(AidAllocation.campaign_id == campaign_id)
    async with database.session() as session:
        items = list(
            (
                await session.execute(
                    statement.order_by(AidDelivery.delivered_at.desc()).limit(500)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/complaints", response_model=Collection[ComplaintResponse])
async def list_complaints(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    campaign_id: UUID | None = None,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[ComplaintResponse]:
    condition = _private_condition(
        principal, AidCampaign.cooperative_id, SolidarityComplaint.complainant_member_id
    )
    statement = select(SolidarityComplaint).join(
        AidCampaign, AidCampaign.id == SolidarityComplaint.campaign_id
    )
    if condition is not None:
        statement = statement.where(condition)
    if campaign_id:
        statement = statement.where(SolidarityComplaint.campaign_id == campaign_id)
    if status:
        statement = statement.where(SolidarityComplaint.status == status.upper())
    async with database.session() as session:
        items = list(
            (
                await session.execute(
                    statement.order_by(SolidarityComplaint.opened_at.desc()).limit(500)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/reports", response_model=Collection[ReportResponse])
async def list_reports(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    campaign_id: UUID | None = None,
) -> Collection[ReportResponse]:
    condition = _public_condition(principal, CampaignReport.cooperative_id)
    statement = select(CampaignReport)
    if condition is not None:
        statement = statement.where(condition)
    if campaign_id:
        statement = statement.where(CampaignReport.campaign_id == campaign_id)
    async with database.session() as session:
        items = list(
            (
                await session.execute(
                    statement.order_by(CampaignReport.generated_at.desc()).limit(500)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/workspaces/operator", response_model=ObjectEnvelope[OperatorWorkspaceResponse])
async def get_operator_workspace(
    principal: PrincipalDependency, database: DatabaseDependency
) -> ObjectEnvelope[OperatorWorkspaceResponse]:
    scopes = _role_scopes(principal, RoleCode.SOLIDARITY_OPERATOR)
    scope_condition = None if scopes is None else AidCampaign.cooperative_id.in_(scopes)
    campaign_statement = select(AidCampaign).where(AidCampaign.status == "OPEN")
    contribution_statement = (
        select(Contribution)
        .join(AidCampaign, AidCampaign.id == Contribution.campaign_id)
        .where(Contribution.status == "VERIFIED")
    )
    application_statement = (
        select(AidApplication)
        .join(AidCampaign, AidCampaign.id == AidApplication.campaign_id)
        .where(AidApplication.status == "ELIGIBLE")
    )
    allocation_statement = (
        select(AidAllocation)
        .join(AidCampaign, AidCampaign.id == AidAllocation.campaign_id)
        .where(AidAllocation.status.in_({"PROPOSED", "APPROVED", "SUSPENDED"}))
    )
    if scope_condition is not None:
        campaign_statement = campaign_statement.where(scope_condition)
        contribution_statement = contribution_statement.where(scope_condition)
        application_statement = application_statement.where(scope_condition)
        allocation_statement = allocation_statement.where(scope_condition)
    async with database.session() as session:
        campaigns = list((await session.execute(campaign_statement.limit(500))).scalars())
        contributions = list((await session.execute(contribution_statement.limit(500))).scalars())
        applications = list((await session.execute(application_statement.limit(500))).scalars())
        allocations = list((await session.execute(allocation_statement.limit(500))).scalars())
    return ObjectEnvelope(
        data=OperatorWorkspaceResponse(
            campaigns=campaigns,
            verified_contributions=contributions,
            eligible_applications=applications,
            active_allocations=allocations,
        ),
        request_id=get_request_id(),
    )


@router.get("/workspaces/controller", response_model=ObjectEnvelope[ControllerWorkspaceResponse])
async def get_controller_workspace(
    principal: PrincipalDependency, database: DatabaseDependency
) -> ObjectEnvelope[ControllerWorkspaceResponse]:
    scopes = _role_scopes(principal, RoleCode.SOLIDARITY_CONTROLLER)
    fund_scope = None if scopes is None else SolidarityFund.cooperative_id.in_(scopes)
    campaign_scope = None if scopes is None else AidCampaign.cooperative_id.in_(scopes)
    fund_statement = select(SolidarityFund).where(SolidarityFund.status == "DRAFT")
    campaign_statement = select(AidCampaign).where(AidCampaign.status == "DRAFT")
    contribution_statement = (
        select(Contribution)
        .join(AidCampaign, AidCampaign.id == Contribution.campaign_id)
        .where(Contribution.status == "RECEIVED")
    )
    application_statement = (
        select(AidApplication)
        .join(AidCampaign, AidCampaign.id == AidApplication.campaign_id)
        .where(AidApplication.status == "SUBMITTED")
    )
    allocation_statement = (
        select(AidAllocation)
        .join(AidCampaign, AidCampaign.id == AidAllocation.campaign_id)
        .where(AidAllocation.status == "PROPOSED")
    )
    complaint_statement = (
        select(SolidarityComplaint)
        .join(AidCampaign, AidCampaign.id == SolidarityComplaint.campaign_id)
        .where(SolidarityComplaint.status == "OPEN")
    )
    if fund_scope is not None and campaign_scope is not None:
        fund_statement = fund_statement.where(fund_scope)
        campaign_statement = campaign_statement.where(campaign_scope)
        contribution_statement = contribution_statement.where(campaign_scope)
        application_statement = application_statement.where(campaign_scope)
        allocation_statement = allocation_statement.where(campaign_scope)
        complaint_statement = complaint_statement.where(campaign_scope)
    async with database.session() as session:
        funds = list((await session.execute(fund_statement.limit(500))).scalars())
        campaigns = list((await session.execute(campaign_statement.limit(500))).scalars())
        contributions = list((await session.execute(contribution_statement.limit(500))).scalars())
        applications = list((await session.execute(application_statement.limit(500))).scalars())
        allocations = list((await session.execute(allocation_statement.limit(500))).scalars())
        complaints = list((await session.execute(complaint_statement.limit(500))).scalars())
    return ObjectEnvelope(
        data=ControllerWorkspaceResponse(
            draft_funds=funds,
            draft_campaigns=campaigns,
            received_contributions=contributions,
            submitted_applications=applications,
            proposed_allocations=allocations,
            open_complaints=complaints,
        ),
        request_id=get_request_id(),
    )


@router.post("/funds", response_model=CommandEnvelope, status_code=201)
async def propose_fund(
    payload: FundProposeRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).propose_fund(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post("/funds/{fund_id}/approval", response_model=CommandEnvelope, status_code=201)
async def approve_fund(
    fund_id: UUID,
    payload: VersionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).approve_fund(
            session,
            principal=principal,
            fund_id=fund_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post("/campaigns", response_model=CommandEnvelope, status_code=201)
async def create_campaign(
    payload: CampaignCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).create_campaign(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post("/campaigns/{campaign_id}/open", response_model=CommandEnvelope, status_code=201)
async def open_campaign(
    campaign_id: UUID,
    payload: VersionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).open_campaign(
            session,
            principal=principal,
            campaign_id=campaign_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post("/campaigns/{campaign_id}/pledges", response_model=CommandEnvelope, status_code=201)
async def create_pledge(
    campaign_id: UUID,
    payload: PledgeCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).create_pledge(
            session,
            principal=principal,
            campaign_id=campaign_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post("/contributions", response_model=CommandEnvelope, status_code=201)
async def receive_contribution(
    payload: ContributionReceiveRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).receive_contribution(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post(
    "/contributions/{contribution_id}/verification",
    response_model=CommandEnvelope,
    status_code=201,
)
async def verify_contribution(
    contribution_id: UUID,
    payload: ContributionVerifyRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).verify_contribution(
            session,
            principal=principal,
            contribution_id=contribution_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post(
    "/campaigns/{campaign_id}/applications", response_model=CommandEnvelope, status_code=201
)
async def submit_application(
    campaign_id: UUID,
    payload: ApplicationSubmitRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).submit_application(
            session,
            principal=principal,
            campaign_id=campaign_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post(
    "/applications/{application_id}/review", response_model=CommandEnvelope, status_code=201
)
async def review_application(
    application_id: UUID,
    payload: ApplicationReviewRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).review_application(
            session,
            principal=principal,
            application_id=application_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post(
    "/applications/{application_id}/allocations",
    response_model=CommandEnvelope,
    status_code=201,
)
async def propose_allocation(
    application_id: UUID,
    payload: AllocationProposeRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).propose_allocation(
            session,
            principal=principal,
            application_id=application_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post(
    "/allocations/{allocation_id}/approval", response_model=CommandEnvelope, status_code=201
)
async def approve_allocation(
    allocation_id: UUID,
    payload: AllocationApproveRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).approve_allocation(
            session,
            principal=principal,
            allocation_id=allocation_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post(
    "/allocations/{allocation_id}/delivery", response_model=CommandEnvelope, status_code=201
)
async def record_delivery(
    allocation_id: UUID,
    payload: DeliveryRecordRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).record_delivery(
            session,
            principal=principal,
            allocation_id=allocation_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post("/campaigns/{campaign_id}/complaints", response_model=CommandEnvelope, status_code=201)
async def open_complaint(
    campaign_id: UUID,
    payload: ComplaintOpenRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).open_complaint(
            session,
            principal=principal,
            campaign_id=campaign_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post(
    "/complaints/{complaint_id}/resolution", response_model=CommandEnvelope, status_code=201
)
async def resolve_complaint(
    complaint_id: UUID,
    payload: ComplaintResolveRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).resolve_complaint(
            session,
            principal=principal,
            complaint_id=complaint_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post("/campaigns/{campaign_id}/close", response_model=CommandEnvelope, status_code=201)
async def close_campaign(
    campaign_id: UUID,
    payload: CampaignCloseRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> SolidarityCommandResult:
        return await SolidarityService(settings).close_campaign(
            session,
            principal=principal,
            campaign_id=campaign_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)
