"""API for verified reserves, bounded crisis mandates, rationing, and paper forms."""

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
from cooperative_clearing.modules.crisis.application.common import CrisisCommandResult
from cooperative_clearing.modules.crisis.application.service import CrisisService
from cooperative_clearing.modules.crisis.domain.types import (
    CrisisCapability,
    CrisisType,
    QualityStatus,
    RationFormula,
    crisis_error,
)
from cooperative_clearing.modules.crisis.infrastructure.models import (
    CrisisMandate,
    CrisisPaperForm,
    CrisisReport,
    CrisisReview,
    RationingAllocation,
    RationingPlan,
    RationingRule,
    RationIssuance,
    ReserveSnapshot,
    ReserveTarget,
)
from cooperative_clearing.modules.identity.application.security import require_step_up
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/crisis", tags=["crisis-reserves"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]
READ_ROLES = {
    RoleCode.CRISIS_OPERATOR,
    RoleCode.CRISIS_CONTROLLER,
    RoleCode.INVENTORY_CONTROLLER,
    RoleCode.AUDITOR,
    RoleCode.SECURITY_ADMIN,
    RoleCode.COOPERATIVE_ADMIN,
}


class VersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class ReserveTargetCreateRequest(BaseModel):
    cooperative_id: UUID
    resource_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    resource_name: str = Field(min_length=2, max_length=200)
    unit_code: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9._-]+$")
    target_quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    critical_minimum: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    warning_coverage_days: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    critical_coverage_days: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    max_snapshot_age_hours: int = Field(ge=1, le=720)
    terms: dict[str, object]


class ReserveSnapshotCreateRequest(BaseModel):
    target_id: UUID
    physical_verified_quantity: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    committed_quantity: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    consumption_rate_per_day: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    expiring_quantity: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    quality_status: QualityStatus
    confidence: Decimal = Field(ge=0, le=1, max_digits=8, decimal_places=7)
    observed_at: datetime
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class MandateCreateRequest(BaseModel):
    cooperative_id: UUID
    mandate_code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    crisis_type: CrisisType
    scope_payload: dict[str, object]
    capabilities: list[CrisisCapability] = Field(min_length=1, max_length=9)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    rationale: str = Field(min_length=2, max_length=5_000)
    exit_criteria: str = Field(min_length=2, max_length=5_000)
    safe_state: str = Field(min_length=2, max_length=5_000)
    starts_at: datetime
    review_at: datetime
    expires_at: datetime
    maximum_end_at: datetime


class MandateActivateRequest(VersionRequest):
    terms_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class MandateReviewRequest(VersionRequest):
    decision: Literal["CONTINUE", "EXTEND"]
    facts_payload: dict[str, object]
    rationale: str = Field(min_length=2, max_length=5_000)
    new_review_at: datetime | None = None
    new_expires_at: datetime | None = None


class MandateCloseRequest(VersionRequest):
    reconciliation_note: str = Field(min_length=2, max_length=5_000)
    corrective_actions: list[str] = Field(default_factory=list, max_length=100)


class RationingRuleCreateRequest(BaseModel):
    mandate_id: UUID
    target_id: UUID
    formula: RationFormula
    eligibility_policy: dict[str, object]
    protected_minimum: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    maximum_per_member: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    period_hours: int = Field(ge=1, le=720)


class RationingRuleApproveRequest(VersionRequest):
    terms_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class EligibleMemberRequest(BaseModel):
    member_id: UUID
    weight: int = Field(default=1, ge=1, le=100)


class RationingPreviewRequest(BaseModel):
    eligible_members: list[EligibleMemberRequest] = Field(min_length=1, max_length=10_000)


class RationingConfirmRequest(VersionRequest):
    allocations_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class RationingCancelRequest(VersionRequest):
    rationale: str = Field(min_length=2, max_length=5_000)


class RationIssueRequest(BaseModel):
    acknowledgement: str = Field(min_length=2, max_length=5_000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class PaperFormIssueRequest(BaseModel):
    mandate_id: UUID
    serial_number: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    form_type: Literal["RESERVE_SNAPSHOT", "RATION_ISSUANCE", "INCIDENT", "EXCEPTION"]
    assigned_to_member_id: UUID
    expires_at: datetime


class PaperFormRecordRequest(BaseModel):
    checksum: str = Field(pattern=r"^[0-9A-Fa-f]{8}$")
    payload: dict[str, object]


class ReserveTargetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cooperative_id: UUID
    resource_code: str
    resource_name: str
    unit_code: str
    target_quantity: Decimal
    critical_minimum: Decimal
    warning_coverage_days: Decimal
    critical_coverage_days: Decimal
    max_snapshot_age_hours: int
    policy_version: int
    terms_hash: str
    status: str
    proposed_by_member_id: UUID
    approved_by_member_id: UUID | None
    created_at: datetime
    approved_at: datetime | None
    version: int


class ReserveSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    target_id: UUID
    physical_verified_quantity: Decimal
    committed_quantity: Decimal
    available_quantity: Decimal
    consumption_rate_per_day: Decimal
    coverage_days: Decimal | None
    expiring_quantity: Decimal
    quality_status: str
    confidence: Decimal
    reserve_level: str
    observed_at: datetime
    snapshot_hash: str
    recorded_by_member_id: UUID
    created_at: datetime


class MandateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cooperative_id: UUID
    mandate_code: str
    crisis_type: str
    scope_payload: dict[str, object]
    capabilities: list[str]
    rationale: str
    exit_criteria: str
    safe_state: str
    policy_version: int
    starts_at: datetime
    review_at: datetime
    expires_at: datetime
    maximum_end_at: datetime
    terms_hash: str
    status: str
    effective_status: str | None = None
    proposed_by_member_id: UUID
    activated_by_member_id: UUID | None
    closed_by_member_id: UUID | None
    created_at: datetime
    activated_at: datetime | None
    closed_at: datetime | None
    version: int


class ReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    mandate_id: UUID
    decision_round: int
    decision: str
    facts_payload: dict[str, object]
    rationale: str
    previous_review_at: datetime
    previous_expires_at: datetime
    new_review_at: datetime | None
    new_expires_at: datetime | None
    reviewer_member_id: UUID
    created_at: datetime


class RationingRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    mandate_id: UUID
    target_id: UUID
    policy_version: int
    formula: str
    eligibility_policy: dict[str, object]
    protected_minimum: Decimal
    maximum_per_member: Decimal
    period_hours: int
    terms_hash: str
    status: str
    proposed_by_member_id: UUID
    approved_by_member_id: UUID | None
    created_at: datetime
    approved_at: datetime | None
    version: int


class RationingPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    rule_id: UUID
    snapshot_id: UUID
    available_input: Decimal
    eligible_count: int
    total_allocated: Decimal
    input_hash: str
    allocations_hash: str
    status: str
    expires_at: datetime
    proposed_by_member_id: UUID
    confirmed_by_member_id: UUID | None
    created_at: datetime
    confirmed_at: datetime | None
    version: int


class AllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    plan_id: UUID
    member_id: UUID
    weight: int
    quantity: Decimal
    status: str
    created_at: datetime
    issued_at: datetime | None


class IssuanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    allocation_id: UUID
    quantity: Decimal
    acknowledgement: str
    issued_by_member_id: UUID
    created_at: datetime


class PaperFormResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cooperative_id: UUID
    mandate_id: UUID
    serial_number: str
    checksum: str
    form_type: str
    assigned_to_member_id: UUID
    status: str
    issued_at: datetime
    expires_at: datetime
    payload_hash: str | None
    issued_by_member_id: UUID
    recorded_by_member_id: UUID | None
    recorded_at: datetime | None


class ReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    mandate_id: UUID
    report_payload: dict[str, object]
    report_hash: str
    generated_at: datetime


class OperatorWorkspaceResponse(BaseModel):
    active_targets: list[ReserveTargetResponse]
    active_mandates: list[MandateResponse]
    active_rules: list[RationingRuleResponse]
    confirmed_plans: list[RationingPlanResponse]
    issued_forms: list[PaperFormResponse]


class ControllerWorkspaceResponse(BaseModel):
    draft_targets: list[ReserveTargetResponse]
    draft_mandates: list[MandateResponse]
    due_reviews: list[MandateResponse]
    draft_rules: list[RationingRuleResponse]
    previewed_plans: list[RationingPlanResponse]
    issued_forms: list[PaperFormResponse]


class Collection[T](BaseModel):
    data: list[T]
    request_id: str


class ObjectEnvelope[T](BaseModel):
    data: T
    request_id: str


CommandAction = Callable[[AsyncSession], Awaitable[CrisisCommandResult]]


def _request_uuid() -> UUID | None:
    try:
        return UUID(get_request_id())
    except ValueError:
        return None


def _command(result: CrisisCommandResult) -> CommandEnvelope:
    return CommandEnvelope(
        data=ApiCommandResult(
            event_id=result.event_id, object_id=result.object_id, replayed=result.replayed
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
            raise crisis_error("CONFLICT") from exc
    return _command(result)


def _read_scopes(principal: Principal) -> set[UUID] | None:
    if principal.must_change_password:
        raise DomainError(
            code="PASSWORD_CHANGE_REQUIRED",
            message_key="errors.auth.password_change_required",
            status_code=403,
        )
    grants = [grant for grant in principal.roles if grant.role in READ_ROLES]
    if any(grant.cooperative_id is None for grant in grants):
        return None
    return {grant.cooperative_id for grant in grants if grant.cooperative_id is not None}


def _scope_condition(
    principal: Principal, column: InstrumentedAttribute[UUID]
) -> ColumnElement[bool] | None:
    scopes = _read_scopes(principal)
    if scopes is None:
        return None
    return column.in_(scopes) if scopes else false()


def _private_condition(
    principal: Principal,
    cooperative_column: InstrumentedAttribute[UUID],
    owner_column: InstrumentedAttribute[UUID],
) -> ColumnElement[bool] | None:
    scopes = _read_scopes(principal)
    conditions: list[ColumnElement[bool]] = []
    if scopes is None:
        return None
    if scopes:
        conditions.append(cooperative_column.in_(scopes))
    if principal.member_id is not None:
        conditions.append(owner_column == principal.member_id)
    return or_(*conditions) if conditions else false()


def _mandate_response(item: CrisisMandate) -> MandateResponse:
    effective = (
        "EXPIRED"
        if item.status == "ACTIVE" and datetime.now(item.expires_at.tzinfo) >= item.expires_at
        else item.status
    )
    return MandateResponse.model_validate(item).model_copy(update={"effective_status": effective})


@router.get("/reserve-targets", response_model=Collection[ReserveTargetResponse])
async def list_reserve_targets(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[ReserveTargetResponse]:
    condition = _scope_condition(principal, ReserveTarget.cooperative_id)
    statement = select(ReserveTarget).order_by(ReserveTarget.created_at.desc())
    if condition is not None:
        statement = statement.where(condition)
    if status:
        statement = statement.where(ReserveTarget.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/reserve-snapshots", response_model=Collection[ReserveSnapshotResponse])
async def list_reserve_snapshots(
    principal: PrincipalDependency, database: DatabaseDependency, target_id: UUID | None = None
) -> Collection[ReserveSnapshotResponse]:
    condition = _scope_condition(principal, ReserveTarget.cooperative_id)
    statement = (
        select(ReserveSnapshot)
        .join(ReserveTarget, ReserveTarget.id == ReserveSnapshot.target_id)
        .order_by(ReserveSnapshot.observed_at.desc())
    )
    if condition is not None:
        statement = statement.where(condition)
    if target_id:
        statement = statement.where(ReserveSnapshot.target_id == target_id)
    async with database.session() as session:
        items = list((await session.execute(statement.limit(1_000))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/mandates", response_model=Collection[MandateResponse])
async def list_mandates(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[MandateResponse]:
    condition = _scope_condition(principal, CrisisMandate.cooperative_id)
    statement = select(CrisisMandate).order_by(CrisisMandate.created_at.desc())
    if condition is not None:
        statement = statement.where(condition)
    if status:
        statement = statement.where(CrisisMandate.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=[_mandate_response(item) for item in items], request_id=get_request_id())


@router.get("/reviews", response_model=Collection[ReviewResponse])
async def list_reviews(
    principal: PrincipalDependency, database: DatabaseDependency, mandate_id: UUID | None = None
) -> Collection[ReviewResponse]:
    condition = _scope_condition(principal, CrisisMandate.cooperative_id)
    statement = (
        select(CrisisReview)
        .join(CrisisMandate, CrisisMandate.id == CrisisReview.mandate_id)
        .order_by(CrisisReview.created_at.desc())
    )
    if condition is not None:
        statement = statement.where(condition)
    if mandate_id:
        statement = statement.where(CrisisReview.mandate_id == mandate_id)
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/rationing-rules", response_model=Collection[RationingRuleResponse])
async def list_rationing_rules(
    principal: PrincipalDependency, database: DatabaseDependency, mandate_id: UUID | None = None
) -> Collection[RationingRuleResponse]:
    condition = _scope_condition(principal, CrisisMandate.cooperative_id)
    statement = (
        select(RationingRule)
        .join(CrisisMandate, CrisisMandate.id == RationingRule.mandate_id)
        .order_by(RationingRule.created_at.desc())
    )
    if condition is not None:
        statement = statement.where(condition)
    if mandate_id:
        statement = statement.where(RationingRule.mandate_id == mandate_id)
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/rationing-plans", response_model=Collection[RationingPlanResponse])
async def list_rationing_plans(
    principal: PrincipalDependency, database: DatabaseDependency, rule_id: UUID | None = None
) -> Collection[RationingPlanResponse]:
    condition = _scope_condition(principal, CrisisMandate.cooperative_id)
    statement = (
        select(RationingPlan)
        .join(RationingRule, RationingRule.id == RationingPlan.rule_id)
        .join(CrisisMandate, CrisisMandate.id == RationingRule.mandate_id)
        .order_by(RationingPlan.created_at.desc())
    )
    if condition is not None:
        statement = statement.where(condition)
    if rule_id:
        statement = statement.where(RationingPlan.rule_id == rule_id)
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/rationing-allocations", response_model=Collection[AllocationResponse])
async def list_rationing_allocations(
    principal: PrincipalDependency, database: DatabaseDependency, plan_id: UUID | None = None
) -> Collection[AllocationResponse]:
    condition = _private_condition(
        principal, CrisisMandate.cooperative_id, RationingAllocation.member_id
    )
    statement = (
        select(RationingAllocation)
        .join(RationingPlan, RationingPlan.id == RationingAllocation.plan_id)
        .join(RationingRule, RationingRule.id == RationingPlan.rule_id)
        .join(CrisisMandate, CrisisMandate.id == RationingRule.mandate_id)
        .order_by(RationingAllocation.created_at.desc())
    )
    if condition is not None:
        statement = statement.where(condition)
    if plan_id:
        statement = statement.where(RationingAllocation.plan_id == plan_id)
    async with database.session() as session:
        items = list((await session.execute(statement.limit(10_000))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/ration-issuances", response_model=Collection[IssuanceResponse])
async def list_ration_issuances(
    principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[IssuanceResponse]:
    condition = _private_condition(
        principal, CrisisMandate.cooperative_id, RationingAllocation.member_id
    )
    statement = (
        select(RationIssuance)
        .join(RationingAllocation, RationingAllocation.id == RationIssuance.allocation_id)
        .join(RationingPlan, RationingPlan.id == RationingAllocation.plan_id)
        .join(RationingRule, RationingRule.id == RationingPlan.rule_id)
        .join(CrisisMandate, CrisisMandate.id == RationingRule.mandate_id)
        .order_by(RationIssuance.created_at.desc())
    )
    if condition is not None:
        statement = statement.where(condition)
    async with database.session() as session:
        items = list((await session.execute(statement.limit(10_000))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/paper-forms", response_model=Collection[PaperFormResponse])
async def list_paper_forms(
    principal: PrincipalDependency, database: DatabaseDependency, mandate_id: UUID | None = None
) -> Collection[PaperFormResponse]:
    condition = _private_condition(
        principal, CrisisPaperForm.cooperative_id, CrisisPaperForm.assigned_to_member_id
    )
    statement = select(CrisisPaperForm).order_by(CrisisPaperForm.issued_at.desc())
    if condition is not None:
        statement = statement.where(condition)
    if mandate_id:
        statement = statement.where(CrisisPaperForm.mandate_id == mandate_id)
    async with database.session() as session:
        items = list((await session.execute(statement.limit(2_000))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/reports", response_model=Collection[ReportResponse])
async def list_reports(
    principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[ReportResponse]:
    condition = _scope_condition(principal, CrisisMandate.cooperative_id)
    statement = (
        select(CrisisReport)
        .join(CrisisMandate, CrisisMandate.id == CrisisReport.mandate_id)
        .order_by(CrisisReport.generated_at.desc())
    )
    if condition is not None:
        statement = statement.where(condition)
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/workspaces/operator", response_model=ObjectEnvelope[OperatorWorkspaceResponse])
async def get_operator_workspace(
    principal: PrincipalDependency, database: DatabaseDependency
) -> ObjectEnvelope[OperatorWorkspaceResponse]:
    condition = _scope_condition(principal, CrisisMandate.cooperative_id)
    target_statement = select(ReserveTarget).where(ReserveTarget.status == "ACTIVE")
    target_condition = _scope_condition(principal, ReserveTarget.cooperative_id)
    if target_condition is not None:
        target_statement = target_statement.where(target_condition)
    async with database.session() as session:
        targets = list((await session.execute(target_statement.limit(500))).scalars())
        mandate_statement = select(CrisisMandate).where(CrisisMandate.status == "ACTIVE")
        if condition is not None:
            mandate_statement = mandate_statement.where(condition)
        mandates = list((await session.execute(mandate_statement.limit(100))).scalars())
        mandate_ids = [item.id for item in mandates]
        rules = (
            list(
                (
                    await session.execute(
                        select(RationingRule)
                        .where(
                            RationingRule.mandate_id.in_(mandate_ids),
                            RationingRule.status == "ACTIVE",
                        )
                        .limit(500)
                    )
                ).scalars()
            )
            if mandate_ids
            else []
        )
        rule_ids = [item.id for item in rules]
        plans = (
            list(
                (
                    await session.execute(
                        select(RationingPlan)
                        .where(
                            RationingPlan.rule_id.in_(rule_ids), RationingPlan.status == "CONFIRMED"
                        )
                        .limit(500)
                    )
                ).scalars()
            )
            if rule_ids
            else []
        )
        forms = (
            list(
                (
                    await session.execute(
                        select(CrisisPaperForm)
                        .where(
                            CrisisPaperForm.mandate_id.in_(mandate_ids),
                            CrisisPaperForm.status == "ISSUED",
                        )
                        .limit(500)
                    )
                ).scalars()
            )
            if mandate_ids
            else []
        )
    return ObjectEnvelope(
        data=OperatorWorkspaceResponse(
            active_targets=targets,
            active_mandates=[_mandate_response(item) for item in mandates],
            active_rules=rules,
            confirmed_plans=plans,
            issued_forms=forms,
        ),
        request_id=get_request_id(),
    )


@router.get("/workspaces/controller", response_model=ObjectEnvelope[ControllerWorkspaceResponse])
async def get_controller_workspace(
    principal: PrincipalDependency, database: DatabaseDependency
) -> ObjectEnvelope[ControllerWorkspaceResponse]:
    condition = _scope_condition(principal, CrisisMandate.cooperative_id)
    now = datetime.now().astimezone()
    async with database.session() as session:
        target_statement = select(ReserveTarget).where(ReserveTarget.status == "DRAFT")
        mandate_statement = select(CrisisMandate).where(CrisisMandate.status == "DRAFT")
        due_statement = select(CrisisMandate).where(
            CrisisMandate.status == "ACTIVE", CrisisMandate.review_at <= now
        )
        visible_mandates_statement = select(CrisisMandate.id)
        if condition is not None:
            target_scopes = _scope_condition(principal, ReserveTarget.cooperative_id)
            if target_scopes is not None:
                target_statement = target_statement.where(target_scopes)
            mandate_statement = mandate_statement.where(condition)
            due_statement = due_statement.where(condition)
            visible_mandates_statement = visible_mandates_statement.where(condition)
        targets = list((await session.execute(target_statement.limit(500))).scalars())
        mandates = list((await session.execute(mandate_statement.limit(500))).scalars())
        due = list((await session.execute(due_statement.limit(500))).scalars())
        mandate_ids = list(
            (await session.execute(visible_mandates_statement.limit(5000))).scalars()
        )
        rules = (
            list(
                (
                    await session.execute(
                        select(RationingRule)
                        .where(
                            RationingRule.mandate_id.in_(mandate_ids),
                            RationingRule.status == "DRAFT",
                        )
                        .limit(500)
                    )
                ).scalars()
            )
            if mandate_ids
            else []
        )
        all_rule_ids = (
            list(
                (
                    await session.execute(
                        select(RationingRule.id).where(RationingRule.mandate_id.in_(mandate_ids))
                    )
                ).scalars()
            )
            if mandate_ids
            else []
        )
        plans = (
            list(
                (
                    await session.execute(
                        select(RationingPlan)
                        .where(
                            RationingPlan.rule_id.in_(all_rule_ids),
                            RationingPlan.status == "PREVIEWED",
                        )
                        .limit(500)
                    )
                ).scalars()
            )
            if all_rule_ids
            else []
        )
        forms = (
            list(
                (
                    await session.execute(
                        select(CrisisPaperForm)
                        .where(
                            CrisisPaperForm.mandate_id.in_(mandate_ids),
                            CrisisPaperForm.status == "ISSUED",
                        )
                        .limit(500)
                    )
                ).scalars()
            )
            if mandate_ids
            else []
        )
    return ObjectEnvelope(
        data=ControllerWorkspaceResponse(
            draft_targets=targets,
            draft_mandates=[_mandate_response(item) for item in mandates],
            due_reviews=[_mandate_response(item) for item in due],
            draft_rules=rules,
            previewed_plans=plans,
            issued_forms=forms,
        ),
        request_id=get_request_id(),
    )


@router.post("/reserve-targets", response_model=CommandEnvelope, status_code=201)
async def propose_reserve_target(
    payload: ReserveTargetCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: CrisisService(settings).propose_reserve_target(
            session,
            principal=principal,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post(
    "/reserve-targets/{target_id}/approval", response_model=CommandEnvelope, status_code=201
)
async def approve_reserve_target(
    target_id: UUID,
    payload: VersionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: CrisisService(settings).approve_reserve_target(
            session,
            principal=principal,
            target_id=target_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/reserve-snapshots", response_model=CommandEnvelope, status_code=201)
async def record_reserve_snapshot(
    payload: ReserveSnapshotCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: CrisisService(settings).record_reserve_snapshot(
            session,
            principal=principal,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post("/mandates", response_model=CommandEnvelope, status_code=201)
async def propose_mandate(
    payload: MandateCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: CrisisService(settings).propose_mandate(
            session,
            principal=principal,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post("/mandates/{mandate_id}/activation", response_model=CommandEnvelope, status_code=201)
async def activate_mandate(
    mandate_id: UUID,
    payload: MandateActivateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> CrisisCommandResult:
        await require_step_up(
            session,
            principal,
            operation="CRISIS_MANDATE_ACTIVATE",
            emergency_roles=frozenset({RoleCode.CRISIS_CONTROLLER}),
            request_id=_request_uuid(),
        )
        return await CrisisService(settings).activate_mandate(
            session,
            principal=principal,
            mandate_id=mandate_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        )

    return await _commit(database, action)


@router.post("/mandates/{mandate_id}/review", response_model=CommandEnvelope, status_code=201)
async def review_mandate(
    mandate_id: UUID,
    payload: MandateReviewRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: CrisisService(settings).review_mandate(
            session,
            principal=principal,
            mandate_id=mandate_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post("/mandates/{mandate_id}/close", response_model=CommandEnvelope, status_code=201)
async def close_mandate(
    mandate_id: UUID,
    payload: MandateCloseRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> CrisisCommandResult:
        await require_step_up(
            session,
            principal,
            operation="CRISIS_MANDATE_CLOSE",
            emergency_roles=frozenset({RoleCode.CRISIS_CONTROLLER}),
            request_id=_request_uuid(),
        )
        return await CrisisService(settings).close_mandate(
            session,
            principal=principal,
            mandate_id=mandate_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        )

    return await _commit(database, action)


@router.post("/mandates/{mandate_id}/expire", response_model=CommandEnvelope, status_code=201)
async def expire_mandate(
    mandate_id: UUID,
    payload: MandateCloseRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: CrisisService(settings).close_mandate(
            session,
            principal=principal,
            mandate_id=mandate_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            expired=True,
            **payload.model_dump(),
        ),
    )


@router.post("/rationing-rules", response_model=CommandEnvelope, status_code=201)
async def propose_rationing_rule(
    payload: RationingRuleCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: CrisisService(settings).propose_rationing_rule(
            session,
            principal=principal,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post("/rationing-rules/{rule_id}/approval", response_model=CommandEnvelope, status_code=201)
async def approve_rationing_rule(
    rule_id: UUID,
    payload: RationingRuleApproveRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: CrisisService(settings).approve_rationing_rule(
            session,
            principal=principal,
            rule_id=rule_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post("/rationing-rules/{rule_id}/previews", response_model=CommandEnvelope, status_code=201)
async def preview_rationing_plan(
    rule_id: UUID,
    payload: RationingPreviewRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    eligible = [(item.member_id, item.weight) for item in payload.eligible_members]
    return await _commit(
        database,
        lambda session: CrisisService(settings).preview_rationing_plan(
            session,
            principal=principal,
            rule_id=rule_id,
            eligible_members=eligible,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/rationing-plans/{plan_id}/confirmation", response_model=CommandEnvelope, status_code=201
)
async def confirm_rationing_plan(
    plan_id: UUID,
    payload: RationingConfirmRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> CrisisCommandResult:
        await require_step_up(
            session,
            principal,
            operation="CRISIS_RATIONING_CONFIRM",
            emergency_roles=frozenset({RoleCode.CRISIS_CONTROLLER}),
            request_id=_request_uuid(),
        )
        return await CrisisService(settings).confirm_rationing_plan(
            session,
            principal=principal,
            plan_id=plan_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        )

    return await _commit(database, action)


@router.post("/rationing-plans/{plan_id}/cancel", response_model=CommandEnvelope, status_code=201)
async def cancel_rationing_plan(
    plan_id: UUID,
    payload: RationingCancelRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: CrisisService(settings).cancel_rationing_plan(
            session,
            principal=principal,
            plan_id=plan_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post(
    "/rationing-allocations/{allocation_id}/issuance",
    response_model=CommandEnvelope,
    status_code=201,
)
async def issue_ration(
    allocation_id: UUID,
    payload: RationIssueRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: CrisisService(settings).issue_ration(
            session,
            principal=principal,
            allocation_id=allocation_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post("/paper-forms", response_model=CommandEnvelope, status_code=201)
async def issue_paper_form(
    payload: PaperFormIssueRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: CrisisService(settings).issue_paper_form(
            session,
            principal=principal,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post("/paper-forms/{form_id}/record", response_model=CommandEnvelope, status_code=201)
async def record_paper_form(
    form_id: UUID,
    payload: PaperFormRecordRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: CrisisService(settings).record_paper_form(
            session,
            principal=principal,
            form_id=form_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )
