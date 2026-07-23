"""Role-scoped API for disputes, sanctions, appeals, and contextual reliability."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ColumnElement, false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.identity_schemas import CommandEnvelope
from cooperative_clearing.api.identity_schemas import CommandResult as ApiCommandResult
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, require_role
from cooperative_clearing.modules.trust.application.common import TrustCommandResult
from cooperative_clearing.modules.trust.application.service import (
    RehabilitationStepDraft,
    TrustService,
)
from cooperative_clearing.modules.trust.domain.types import (
    AppealOutcome,
    ConflictAssessment,
    DecisionOutcome,
    FaultClass,
    ReliabilityEventFact,
    ReputationClassification,
    ReputationContext,
    ReputationStatus,
    build_reliability_profile,
    trust_error,
)
from cooperative_clearing.modules.trust.infrastructure.models import (
    Appeal,
    ArbitrationDecision,
    ConflictDeclaration,
    ProtectiveMeasure,
    RehabilitationPlan,
    RehabilitationStep,
    ReputationEvent,
    Sanction,
    TrustCase,
    TrustPolicy,
)
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/trust", tags=["disputes-and-trust"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]

GLOBAL_READ_ROLES = {RoleCode.AUDITOR, RoleCode.ARBITRATOR, RoleCode.SECURITY_ADMIN}
SCOPED_READ_ROLES = {
    RoleCode.COOPERATIVE_ADMIN,
    RoleCode.RISK_ADMIN,
    RoleCode.AUDITOR,
    RoleCode.ARBITRATOR,
}


class PolicyProposeRequest(BaseModel):
    cooperative_id: UUID
    semantic_version: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9._-]+$")
    appeal_window_seconds: int = Field(ge=0, le=2_592_000)
    max_protective_seconds: int = Field(ge=1, le=2_592_000)
    panel_quorum: int = Field(ge=1, le=9)
    terms: dict[str, object]


class VersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class CaseOpenRequest(BaseModel):
    cooperative_id: UUID
    case_reference: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    subject_member_id: UUID
    claimant_member_id: UUID
    source_type: Literal[
        "LIABILITY", "EXCHANGE", "CLEARING", "INVENTORY", "RIGHTS", "NODE", "OTHER"
    ]
    source_reference: str = Field(min_length=1, max_length=120)
    source_event_ids: list[UUID] = Field(min_length=1, max_length=100)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    summary: str = Field(min_length=2, max_length=240)
    facts: str = Field(min_length=2, max_length=20_000)
    requested_outcome: str = Field(min_length=2, max_length=5_000)
    confidentiality: Literal["NORMAL", "RESTRICTED"] = "NORMAL"


class TrustCaseResponseRequest(BaseModel):
    expected_version: int = Field(ge=1)
    response_text: str = Field(min_length=2, max_length=20_000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)


class CaseReadyRequest(BaseModel):
    expected_version: int = Field(ge=1)
    review_note: str = Field(min_length=2, max_length=5_000)


class ConflictRequest(BaseModel):
    stage: Literal["ORIGINAL", "APPEAL", "REHABILITATION"]
    assessment: ConflictAssessment
    relationship: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=2, max_length=5_000)


class ProtectiveMeasureRequest(BaseModel):
    expected_case_version: int = Field(ge=1)
    measure_type: Literal[
        "ADDITIONAL_REVIEW", "LIMIT_SCOPE", "SUSPEND_ROLE", "SUSPEND_KEY", "BLOCK_NEW_GUARANTEES"
    ]
    scope: dict[str, object]
    rationale: str = Field(min_length=2, max_length=5_000)
    expires_at: datetime
    review_at: datetime


class LiftMeasureRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=5_000)


class OriginalDecisionRequest(BaseModel):
    expected_case_version: int = Field(ge=1)
    outcome: Literal["SUBSTANTIATED", "PARTLY_SUBSTANTIATED", "UNSUBSTANTIATED"]
    standard_of_proof: str = Field(min_length=2, max_length=120)
    fault_class: FaultClass | None = None
    causal_findings: dict[str, object]
    established_loss: Decimal | None = Field(default=None, ge=0, max_digits=38, decimal_places=12)
    reasoning: str = Field(min_length=2, max_length=20_000)
    consequence_spec: dict[str, object]
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class SanctionProposeRequest(BaseModel):
    measure_type: Literal[
        "WARNING",
        "TRAINING",
        "ADDITIONAL_REVIEW",
        "LIMIT_SCOPE",
        "SUSPEND_ROLE",
        "BLOCK_NEW_GUARANTEES",
        "TERMINATE_ROLE",
    ]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    scope: dict[str, object]
    rationale: str = Field(min_length=2, max_length=5_000)
    starts_at: datetime
    expires_at: datetime | None = None
    review_at: datetime | None = None


class ReputationRecordRequest(BaseModel):
    context: ReputationContext
    classification: Literal["FULFILLED", "BREACH", "SELF_REPORTED_ERROR", "REHABILITATION"]
    severity: int = Field(ge=0, le=5)
    confidence: Decimal = Field(ge=0, le=1, max_digits=5, decimal_places=4)
    observation_start: datetime
    observation_end: datetime
    source_event_ids: list[UUID] = Field(default_factory=list, max_length=100)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    visibility: Literal["PARTICIPANT", "COOPERATIVE", "RESTRICTED"]


class AppealSubmitRequest(BaseModel):
    original_decision_id: UUID
    sanction_id: UUID | None = None
    expected_case_version: int = Field(ge=1)
    grounds: str = Field(min_length=2, max_length=20_000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class AppealDecisionRequest(BaseModel):
    expected_case_version: int = Field(ge=1)
    outcome: AppealOutcome
    standard_of_proof: str = Field(min_length=2, max_length=120)
    causal_findings: dict[str, object]
    reasoning: str = Field(min_length=2, max_length=20_000)
    consequence_spec: dict[str, object]
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class RehabilitationStepRequest(BaseModel):
    description: str = Field(min_length=2, max_length=5_000)
    completion_criterion: str = Field(min_length=2, max_length=5_000)


class RehabilitationPlanRequest(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    completion_criteria: dict[str, object]
    starts_at: datetime
    due_at: datetime
    steps: list[RehabilitationStepRequest] = Field(min_length=1, max_length=50)


class RehabilitationStepCompleteRequest(BaseModel):
    expected_plan_version: int = Field(ge=1)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class RehabilitationCloseRequest(BaseModel):
    expected_version: int = Field(ge=1)
    context: ReputationContext
    closure_reason: str = Field(min_length=2, max_length=5_000)


class TrustPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    policy_version: int
    policy_code: str
    semantic_version: str
    appeal_window_seconds: int
    max_protective_seconds: int
    panel_quorum: int
    terms_hash: str
    status: str
    proposed_by_member_id: UUID
    approved_by_member_id: UUID | None
    created_at: datetime
    approved_at: datetime | None
    version: int


class TrustCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    policy_id: UUID
    case_reference: str
    subject_member_id: UUID
    claimant_member_id: UUID
    source_type: str
    source_reference: str
    source_event_ids: list[str]
    evidence_refs: list[dict[str, object]]
    summary: str
    facts: str
    requested_outcome: str
    confidentiality: str
    status: str
    opened_by_member_id: UUID
    opened_event_id: UUID
    response_text: str | None
    response_evidence_refs: list[dict[str, object]] | None
    response_event_id: UUID | None
    opened_at: datetime
    responded_at: datetime | None
    original_decision_at: datetime | None
    appeal_until: datetime | None
    closed_at: datetime | None
    version: int


class TrustConflictResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    case_id: UUID
    stage: str
    member_id: UUID
    role_assignment_id: UUID
    assessment: str
    relationship: str
    rationale: str
    event_id: UUID
    declared_at: datetime


class TrustMeasureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    case_id: UUID
    subject_member_id: UUID
    measure_type: str
    scope: dict[str, object]
    rationale: str
    status: str
    starts_at: datetime
    expires_at: datetime
    review_at: datetime
    imposed_by_member_id: UUID
    imposed_event_id: UUID
    lifted_by_member_id: UUID | None
    lifted_event_id: UUID | None
    lift_reason: str | None
    version: int


class TrustDecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    case_id: UUID
    stage: str
    decision_round: int
    related_object_id: UUID | None
    outcome: str
    standard_of_proof: str
    fault_class: str | None
    causal_findings: dict[str, object]
    established_loss: Decimal | None
    reasoning: str
    consequence_spec: dict[str, object]
    evidence_refs: list[dict[str, object]]
    panel_snapshot: list[dict[str, object]]
    policy_version: str
    issued_by_member_id: UUID
    issued_event_id: UUID
    issued_at: datetime


class TrustSanctionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    case_id: UUID
    decision_id: UUID
    subject_member_id: UUID
    measure_type: str
    severity: str
    scope: dict[str, object]
    rationale: str
    status: str
    starts_at: datetime
    expires_at: datetime | None
    review_at: datetime | None
    appeal_until: datetime
    proposed_by_member_id: UUID
    proposed_event_id: UUID
    finalized_by_member_id: UUID | None
    finalized_event_id: UUID | None
    revoked_event_id: UUID | None
    revocation_reason: str | None
    version: int


class TrustAppealResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    case_id: UUID
    original_decision_id: UUID
    sanction_id: UUID | None
    appellant_member_id: UUID
    grounds: str
    evidence_refs: list[dict[str, object]]
    status: str
    submitted_event_id: UUID
    appeal_decision_id: UUID | None
    outcome: str | None
    decided_event_id: UUID | None
    submitted_at: datetime
    decided_at: datetime | None


class TrustReputationEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    case_id: UUID | None
    decision_id: UUID | None
    subject_member_id: UUID
    context: str
    classification: str
    severity: int
    confidence: Decimal
    observation_start: datetime
    observation_end: datetime
    source_event_ids: list[str]
    appeal_state: str
    status: str
    visibility: str
    policy_version: str
    corrects_event_id: UUID | None
    recorded_event_id: UUID
    created_at: datetime


class TrustRehabilitationStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    sequence: int
    description: str
    completion_criterion: str
    status: str
    evidence_refs: list[dict[str, object]]
    completed_by_member_id: UUID | None
    completed_event_id: UUID | None
    completed_at: datetime | None


class TrustRehabilitationPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    case_id: UUID
    decision_id: UUID
    subject_member_id: UUID
    title: str
    completion_criteria: dict[str, object]
    status: str
    starts_at: datetime
    due_at: datetime
    created_by_member_id: UUID
    created_event_id: UUID
    closed_by_member_id: UUID | None
    closed_event_id: UUID | None
    closure_reason: str | None
    created_at: datetime
    closed_at: datetime | None
    version: int


class TrustContextProfileResponse(BaseModel):
    context: ReputationContext
    confirmed_fulfillments: int
    confirmed_breaches: int
    self_reported_errors: int
    rehabilitation_events: int
    disputed_events: int
    voided_events: int
    corrections: int
    sample_count: int
    confidence_min: Decimal | None
    confidence_max: Decimal | None
    last_observation: datetime | None
    source_event_ids: list[UUID]


class TrustReliabilityProfileResponse(BaseModel):
    subject_member_id: UUID
    contexts: list[TrustContextProfileResponse]
    active_measures: int
    active_sanctions: int
    rehabilitation_active: int
    generated_at: datetime


class ArbitratorWorkspaceResponse(BaseModel):
    ready_cases: list[TrustCaseResponse]
    submitted_appeals: list[TrustAppealResponse]
    active_measures: list[TrustMeasureResponse]


class AuditorWorkspaceResponse(BaseModel):
    cases_needing_review: list[TrustCaseResponse]
    active_measures: list[TrustMeasureResponse]
    disputed_reputation_events: list[TrustReputationEventResponse]
    active_rehabilitation_plans: list[TrustRehabilitationPlanResponse]


class Collection[T](BaseModel):
    data: list[T]
    request_id: str


class ObjectEnvelope[T](BaseModel):
    data: T
    request_id: str


CommandAction = Callable[[AsyncSession], Awaitable[TrustCommandResult]]


def _request_uuid() -> UUID | None:
    try:
        return UUID(get_request_id())
    except ValueError:
        return None


def _command(result: TrustCommandResult) -> CommandEnvelope:
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
            raise trust_error("CONFLICT") from exc
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


def _scopes(principal: Principal) -> set[UUID] | None:
    _require_readable(principal)
    if _global_reader(principal):
        return None
    return {
        grant.cooperative_id
        for grant in principal.roles
        if grant.role in SCOPED_READ_ROLES and grant.cooperative_id is not None
    }


def _case_filter(principal: Principal) -> ColumnElement[bool] | None:
    scopes = _scopes(principal)
    if scopes is None:
        return None
    conditions: list[ColumnElement[bool]] = []
    if scopes:
        conditions.append(TrustCase.cooperative_id.in_(scopes))
    if principal.member_id is not None:
        conditions.extend(
            (
                TrustCase.subject_member_id == principal.member_id,
                TrustCase.claimant_member_id == principal.member_id,
                TrustCase.opened_by_member_id == principal.member_id,
            )
        )
    return or_(*conditions) if conditions else false()


def _role_scopes(principal: Principal, role: RoleCode) -> set[UUID] | None:
    require_role(principal, {role})
    grants = [grant for grant in principal.roles if grant.role == role]
    if any(grant.cooperative_id is None for grant in grants):
        return None
    return {grant.cooperative_id for grant in grants if grant.cooperative_id is not None}


async def _visible_case(
    session: AsyncSession, principal: Principal, case_id: UUID, *, staff_only: bool = False
) -> TrustCase:
    condition = _case_filter(principal)
    statement = select(TrustCase).where(TrustCase.id == case_id)
    if condition is not None:
        statement = statement.where(condition)
    item = await session.scalar(statement)
    if item is None:
        raise trust_error("CASE_NOT_FOUND", 404)
    if staff_only:
        scopes = _scopes(principal)
        if scopes is not None and item.cooperative_id not in scopes:
            raise trust_error("AUTHORIZATION_DENIED", 403)
    return item


@router.get("/workspaces/arbitrator", response_model=ObjectEnvelope[ArbitratorWorkspaceResponse])
async def get_arbitrator_workspace(
    principal: PrincipalDependency, database: DatabaseDependency
) -> ObjectEnvelope[ArbitratorWorkspaceResponse]:
    scopes = _role_scopes(principal, RoleCode.ARBITRATOR)
    cases_statement = select(TrustCase).where(TrustCase.status == "READY_FOR_DECISION")
    appeals_statement = (
        select(Appeal)
        .join(TrustCase, TrustCase.id == Appeal.case_id)
        .where(Appeal.status == "SUBMITTED")
    )
    measures_statement = (
        select(ProtectiveMeasure)
        .join(TrustCase, TrustCase.id == ProtectiveMeasure.case_id)
        .where(ProtectiveMeasure.status == "ACTIVE")
    )
    if scopes is not None:
        scope_filter = TrustCase.cooperative_id.in_(scopes) if scopes else false()
        cases_statement = cases_statement.where(scope_filter)
        appeals_statement = appeals_statement.where(scope_filter)
        measures_statement = measures_statement.where(scope_filter)
    async with database.session() as session:
        ready_cases = list(
            (
                await session.execute(
                    cases_statement.order_by(TrustCase.opened_at, TrustCase.id).limit(500)
                )
            ).scalars()
        )
        submitted_appeals = list(
            (
                await session.execute(
                    appeals_statement.order_by(Appeal.submitted_at, Appeal.id).limit(500)
                )
            ).scalars()
        )
        active_measures = list(
            (
                await session.execute(
                    measures_statement.order_by(
                        ProtectiveMeasure.review_at, ProtectiveMeasure.id
                    ).limit(500)
                )
            ).scalars()
        )
    return ObjectEnvelope(
        data=ArbitratorWorkspaceResponse(
            ready_cases=ready_cases,
            submitted_appeals=submitted_appeals,
            active_measures=active_measures,
        ),
        request_id=get_request_id(),
    )


@router.get("/workspaces/auditor", response_model=ObjectEnvelope[AuditorWorkspaceResponse])
async def get_auditor_workspace(
    principal: PrincipalDependency, database: DatabaseDependency
) -> ObjectEnvelope[AuditorWorkspaceResponse]:
    scopes = _role_scopes(principal, RoleCode.AUDITOR)
    cases_statement = select(TrustCase).where(
        TrustCase.status.in_(("OPEN", "RESPONSE_RECEIVED", "REMANDED"))
    )
    measures_statement = (
        select(ProtectiveMeasure)
        .join(TrustCase, TrustCase.id == ProtectiveMeasure.case_id)
        .where(ProtectiveMeasure.status == "ACTIVE")
    )
    reputation_statement = select(ReputationEvent).where(ReputationEvent.status == "DISPUTED")
    rehabilitation_statement = (
        select(RehabilitationPlan)
        .join(TrustCase, TrustCase.id == RehabilitationPlan.case_id)
        .where(RehabilitationPlan.status == "ACTIVE")
    )
    if scopes is not None:
        scope_filter = TrustCase.cooperative_id.in_(scopes) if scopes else false()
        cases_statement = cases_statement.where(scope_filter)
        measures_statement = measures_statement.where(scope_filter)
        rehabilitation_statement = rehabilitation_statement.where(scope_filter)
        reputation_statement = reputation_statement.where(
            ReputationEvent.cooperative_id.in_(scopes) if scopes else false()
        )
    async with database.session() as session:
        cases = list(
            (
                await session.execute(
                    cases_statement.order_by(TrustCase.opened_at, TrustCase.id).limit(500)
                )
            ).scalars()
        )
        measures = list(
            (
                await session.execute(
                    measures_statement.order_by(
                        ProtectiveMeasure.review_at, ProtectiveMeasure.id
                    ).limit(500)
                )
            ).scalars()
        )
        events = list(
            (
                await session.execute(
                    reputation_statement.order_by(
                        ReputationEvent.created_at, ReputationEvent.id
                    ).limit(1000)
                )
            ).scalars()
        )
        plans = list(
            (
                await session.execute(
                    rehabilitation_statement.order_by(
                        RehabilitationPlan.due_at, RehabilitationPlan.id
                    ).limit(500)
                )
            ).scalars()
        )
    return ObjectEnvelope(
        data=AuditorWorkspaceResponse(
            cases_needing_review=cases,
            active_measures=measures,
            disputed_reputation_events=events,
            active_rehabilitation_plans=plans,
        ),
        request_id=get_request_id(),
    )


@router.get("/policies", response_model=Collection[TrustPolicyResponse])
async def list_policies(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[TrustPolicyResponse]:
    scopes = _scopes(principal)
    statement = select(TrustPolicy).order_by(
        TrustPolicy.cooperative_id, TrustPolicy.policy_version.desc()
    )
    if scopes is not None:
        statement = (
            statement.where(TrustPolicy.cooperative_id.in_(scopes))
            if scopes
            else statement.where(false())
        )
    if status:
        statement = statement.where(TrustPolicy.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/cases", response_model=Collection[TrustCaseResponse])
async def list_cases(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=24),
) -> Collection[TrustCaseResponse]:
    condition = _case_filter(principal)
    statement = select(TrustCase).order_by(TrustCase.opened_at.desc(), TrustCase.id)
    if condition is not None:
        statement = statement.where(condition)
    if status:
        statement = statement.where(TrustCase.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/cases/{case_id}", response_model=ObjectEnvelope[TrustCaseResponse])
async def get_case(
    case_id: UUID, principal: PrincipalDependency, database: DatabaseDependency
) -> ObjectEnvelope[TrustCaseResponse]:
    async with database.session() as session:
        item = await _visible_case(session, principal, case_id)
    return ObjectEnvelope(data=item, request_id=get_request_id())


@router.get("/cases/{case_id}/conflicts", response_model=Collection[TrustConflictResponse])
async def list_conflicts(
    case_id: UUID, principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[TrustConflictResponse]:
    async with database.session() as session:
        await _visible_case(session, principal, case_id)
        items = list(
            (
                await session.execute(
                    select(ConflictDeclaration)
                    .where(ConflictDeclaration.case_id == case_id)
                    .order_by(ConflictDeclaration.declared_at)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/cases/{case_id}/measures", response_model=Collection[TrustMeasureResponse])
async def list_measures(
    case_id: UUID, principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[TrustMeasureResponse]:
    async with database.session() as session:
        await _visible_case(session, principal, case_id)
        items = list(
            (
                await session.execute(
                    select(ProtectiveMeasure)
                    .where(ProtectiveMeasure.case_id == case_id)
                    .order_by(ProtectiveMeasure.created_at)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/cases/{case_id}/decisions", response_model=Collection[TrustDecisionResponse])
async def list_decisions(
    case_id: UUID, principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[TrustDecisionResponse]:
    async with database.session() as session:
        await _visible_case(session, principal, case_id)
        items = list(
            (
                await session.execute(
                    select(ArbitrationDecision)
                    .where(ArbitrationDecision.case_id == case_id)
                    .order_by(ArbitrationDecision.issued_at)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/cases/{case_id}/appeals", response_model=Collection[TrustAppealResponse])
async def list_appeals(
    case_id: UUID, principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[TrustAppealResponse]:
    async with database.session() as session:
        await _visible_case(session, principal, case_id)
        items = list(
            (
                await session.execute(
                    select(Appeal).where(Appeal.case_id == case_id).order_by(Appeal.submitted_at)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/sanctions", response_model=Collection[TrustSanctionResponse])
async def list_sanctions(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=24),
) -> Collection[TrustSanctionResponse]:
    condition = _case_filter(principal)
    statement = select(Sanction).join(TrustCase, TrustCase.id == Sanction.case_id)
    if condition is not None:
        statement = statement.where(condition)
    if status:
        statement = statement.where(Sanction.status == status.upper())
    async with database.session() as session:
        items = list(
            (
                await session.execute(statement.order_by(Sanction.created_at.desc()).limit(500))
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/appeals", response_model=Collection[TrustAppealResponse])
async def list_all_appeals(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=24),
) -> Collection[TrustAppealResponse]:
    condition = _case_filter(principal)
    statement = select(Appeal).join(TrustCase, TrustCase.id == Appeal.case_id)
    if condition is not None:
        statement = statement.where(condition)
    if status:
        statement = statement.where(Appeal.status == status.upper())
    async with database.session() as session:
        items = list(
            (
                await session.execute(statement.order_by(Appeal.submitted_at.desc()).limit(500))
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/reputation/events", response_model=Collection[TrustReputationEventResponse])
async def list_reputation_events(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    subject_member_id: UUID | None = None,
) -> Collection[TrustReputationEventResponse]:
    _require_readable(principal)
    statement = select(ReputationEvent)
    scopes = _scopes(principal)
    if scopes is not None:
        own = (
            ReputationEvent.subject_member_id == principal.member_id
            if principal.member_id
            else false()
        )
        scoped = ReputationEvent.cooperative_id.in_(scopes) if scopes else false()
        statement = statement.where(or_(own, scoped))
    if subject_member_id:
        statement = statement.where(ReputationEvent.subject_member_id == subject_member_id)
    async with database.session() as session:
        items = list(
            (
                await session.execute(statement.order_by(ReputationEvent.created_at).limit(1000))
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get(
    "/reputation/profiles/{member_id}",
    response_model=ObjectEnvelope[TrustReliabilityProfileResponse],
)
async def get_reliability_profile(
    member_id: UUID, principal: PrincipalDependency, database: DatabaseDependency
) -> ObjectEnvelope[TrustReliabilityProfileResponse]:
    scopes = _scopes(principal)
    async with database.session() as session:
        statement = select(ReputationEvent).where(ReputationEvent.subject_member_id == member_id)
        if scopes is not None and principal.member_id != member_id:
            statement = (
                statement.where(ReputationEvent.cooperative_id.in_(scopes))
                if scopes
                else statement.where(false())
            )
        events = list(
            (await session.execute(statement.order_by(ReputationEvent.created_at))).scalars()
        )

        if not events and principal.member_id != member_id and scopes is not None:
            raise trust_error("PROFILE_NOT_FOUND", 404)
        measure_count = await session.scalar(
            select(func.count())
            .select_from(ProtectiveMeasure)
            .where(
                ProtectiveMeasure.subject_member_id == member_id,
                ProtectiveMeasure.status == "ACTIVE",
            )
        )
        sanction_count = await session.scalar(
            select(func.count())
            .select_from(Sanction)
            .where(Sanction.subject_member_id == member_id, Sanction.status == "ACTIVE")
        )
        rehab_count = await session.scalar(
            select(func.count())
            .select_from(RehabilitationPlan)
            .where(
                RehabilitationPlan.subject_member_id == member_id,
                RehabilitationPlan.status == "ACTIVE",
            )
        )
    facts = [
        ReliabilityEventFact(
            event_id=item.id,
            context=ReputationContext(item.context),
            classification=ReputationClassification(item.classification),
            severity=item.severity,
            confidence=item.confidence,
            status=ReputationStatus(item.status),
            appeal_state=item.appeal_state,
            observation_end=item.observation_end,
        )
        for item in events
    ]
    contexts = [
        TrustContextProfileResponse(
            context=item.context,
            confirmed_fulfillments=item.confirmed_fulfillments,
            confirmed_breaches=item.confirmed_breaches,
            self_reported_errors=item.self_reported_errors,
            rehabilitation_events=item.rehabilitation_events,
            disputed_events=item.disputed_events,
            voided_events=item.voided_events,
            corrections=item.corrections,
            sample_count=item.sample_count,
            confidence_min=item.confidence_min,
            confidence_max=item.confidence_max,
            last_observation=item.last_observation,
            source_event_ids=list(item.source_event_ids),
        )
        for item in build_reliability_profile(facts)
    ]

    profile = TrustReliabilityProfileResponse(
        subject_member_id=member_id,
        contexts=contexts,
        active_measures=int(measure_count or 0),
        active_sanctions=int(sanction_count or 0),
        rehabilitation_active=int(rehab_count or 0),
        generated_at=datetime.now(UTC),
    )
    return ObjectEnvelope(data=profile, request_id=get_request_id())


@router.get("/rehabilitation-plans", response_model=Collection[TrustRehabilitationPlanResponse])
async def list_rehabilitation_plans(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[TrustRehabilitationPlanResponse]:
    condition = _case_filter(principal)
    statement = select(RehabilitationPlan).join(
        TrustCase, TrustCase.id == RehabilitationPlan.case_id
    )
    if condition is not None:
        statement = statement.where(condition)
    if status:
        statement = statement.where(RehabilitationPlan.status == status.upper())
    async with database.session() as session:
        items = list(
            (
                await session.execute(
                    statement.order_by(RehabilitationPlan.created_at.desc()).limit(500)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get(
    "/rehabilitation-plans/{plan_id}/steps",
    response_model=Collection[TrustRehabilitationStepResponse],
)
async def list_rehabilitation_steps(
    plan_id: UUID, principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[TrustRehabilitationStepResponse]:
    async with database.session() as session:
        plan = await session.get(RehabilitationPlan, plan_id)
        if plan is None:
            raise trust_error("REHABILITATION_PLAN_NOT_FOUND", 404)
        await _visible_case(session, principal, plan.case_id)
        items = list(
            (
                await session.execute(
                    select(RehabilitationStep)
                    .where(RehabilitationStep.plan_id == plan_id)
                    .order_by(RehabilitationStep.sequence)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.post("/policies", response_model=CommandEnvelope, status_code=201)
async def propose_policy(
    payload: PolicyProposeRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).propose_policy(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/policies/{policy_id}/approval", response_model=CommandEnvelope)
async def approve_policy(
    policy_id: UUID,
    payload: VersionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).approve_policy(
            session,
            principal=principal,
            policy_id=policy_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/cases", response_model=CommandEnvelope, status_code=201)
async def open_case(
    payload: CaseOpenRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).open_case(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/cases/{case_id}/responses", response_model=CommandEnvelope)
async def record_response(
    case_id: UUID,
    payload: TrustCaseResponseRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).record_response(
            session,
            principal=principal,
            case_id=case_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/cases/{case_id}/ready", response_model=CommandEnvelope)
async def mark_case_ready(
    case_id: UUID,
    payload: CaseReadyRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).mark_case_ready(
            session,
            principal=principal,
            case_id=case_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/cases/{case_id}/conflicts", response_model=CommandEnvelope, status_code=201)
async def declare_conflict(
    case_id: UUID,
    payload: ConflictRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).declare_conflict(
            session,
            principal=principal,
            case_id=case_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/cases/{case_id}/protective-measures", response_model=CommandEnvelope, status_code=201
)
async def impose_protective_measure(
    case_id: UUID,
    payload: ProtectiveMeasureRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).impose_protective_measure(
            session,
            principal=principal,
            case_id=case_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/protective-measures/{measure_id}/lift", response_model=CommandEnvelope)
async def lift_protective_measure(
    measure_id: UUID,
    payload: LiftMeasureRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).lift_protective_measure(
            session,
            principal=principal,
            measure_id=measure_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/cases/{case_id}/decisions", response_model=CommandEnvelope, status_code=201)
async def issue_original_decision(
    case_id: UUID,
    payload: OriginalDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    values = payload.model_dump()
    values["outcome"] = DecisionOutcome(payload.outcome)
    return await _commit(
        database,
        lambda session: TrustService(settings).issue_original_decision(
            session,
            principal=principal,
            case_id=case_id,
            **values,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/decisions/{decision_id}/sanctions", response_model=CommandEnvelope, status_code=201)
async def propose_sanction(
    decision_id: UUID,
    payload: SanctionProposeRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).propose_sanction(
            session,
            principal=principal,
            decision_id=decision_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/decisions/{decision_id}/reputation-events", response_model=CommandEnvelope, status_code=201
)
async def record_reputation_event(
    decision_id: UUID,
    payload: ReputationRecordRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    values = payload.model_dump()
    values["classification"] = ReputationClassification(payload.classification)
    return await _commit(
        database,
        lambda session: TrustService(settings).record_reputation_event(
            session,
            principal=principal,
            decision_id=decision_id,
            **values,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/cases/{case_id}/appeals", response_model=CommandEnvelope, status_code=201)
async def submit_appeal(
    case_id: UUID,
    payload: AppealSubmitRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).submit_appeal(
            session,
            principal=principal,
            case_id=case_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/appeals/{appeal_id}/decision", response_model=CommandEnvelope, status_code=201)
async def decide_appeal(
    appeal_id: UUID,
    payload: AppealDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).decide_appeal(
            session,
            principal=principal,
            appeal_id=appeal_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/sanctions/{sanction_id}/finalize", response_model=CommandEnvelope)
async def finalize_unappealed_sanction(
    sanction_id: UUID,
    payload: VersionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).finalize_unappealed_sanction(
            session,
            principal=principal,
            sanction_id=sanction_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/decisions/{decision_id}/rehabilitation-plans", response_model=CommandEnvelope, status_code=201
)
async def create_rehabilitation_plan(
    decision_id: UUID,
    payload: RehabilitationPlanRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).create_rehabilitation_plan(
            session,
            principal=principal,
            decision_id=decision_id,
            title=payload.title,
            completion_criteria=payload.completion_criteria,
            starts_at=payload.starts_at,
            due_at=payload.due_at,
            steps=tuple(
                RehabilitationStepDraft(item.description, item.completion_criterion)
                for item in payload.steps
            ),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/rehabilitation-plans/{plan_id}/steps/{step_id}/complete", response_model=CommandEnvelope
)
async def complete_rehabilitation_step(
    plan_id: UUID,
    step_id: UUID,
    payload: RehabilitationStepCompleteRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).complete_rehabilitation_step(
            session,
            principal=principal,
            plan_id=plan_id,
            step_id=step_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/rehabilitation-plans/{plan_id}/close", response_model=CommandEnvelope)
async def close_rehabilitation_plan(
    plan_id: UUID,
    payload: RehabilitationCloseRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: TrustService(settings).close_rehabilitation_plan(
            session,
            principal=principal,
            plan_id=plan_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )
