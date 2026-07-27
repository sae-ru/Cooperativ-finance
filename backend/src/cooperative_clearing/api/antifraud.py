"""Explainable anti-fraud scans, holds, and independent review API."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ColumnElement, func, select
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
from cooperative_clearing.modules.identity.application.security import require_step_up
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.risk.application.antifraud import AntifraudService
from cooperative_clearing.modules.risk.application.common import RiskCommandResult
from cooperative_clearing.modules.risk.domain.antifraud_catalog import (
    ALGORITHM_VERSION,
    CALIBRATION_DATASET_VERSION,
    rule_manifest_hash,
    rule_manifest_payload,
)
from cooperative_clearing.modules.risk.domain.types import AntifraudSignalStatus, risk_error
from cooperative_clearing.modules.risk.infrastructure.models import (
    AntifraudScan,
    AntifraudSignal,
)
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/antifraud", tags=["anti-fraud"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]
READ_ROLES = {RoleCode.RISK_ADMIN, RoleCode.AUDITOR, RoleCode.SECURITY_ADMIN}


class ScanRequest(BaseModel):
    cooperative_id: UUID
    lookback_hours: int = Field(default=168, ge=1, le=2160)


class ReviewRequest(BaseModel):
    expected_version: int = Field(ge=1)


class DecisionRequest(BaseModel):
    decision: Literal["CLEARED", "CONFIRMED"]
    rationale: str = Field(min_length=2, max_length=8000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=1)


class ScanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    algorithm_version: str
    rule_manifest_hash: str
    calibration_dataset_version: str
    lookback_hours: int
    input_cutoff: datetime
    finding_count: int
    result_summary: dict[str, object]
    initiated_by_member_id: UUID
    completed_event_id: UUID
    created_at: datetime


class SignalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    scan_id: UUID
    rule_code: str
    rule_version: int
    subject_type: str
    subject_id: UUID
    severity: str
    automation_action: str
    status: str
    reason_key: str
    observed_data: dict[str, object]
    threshold_data: dict[str, object]
    occurrence_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    detected_by_member_id: UUID
    detected_event_id: UUID
    reviewer_member_id: UUID | None
    review_started_event_id: UUID | None
    decision_event_id: UUID | None
    decision_rationale: str | None
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None
    version: int


class OverviewResponse(BaseModel):
    cooperative_count: int
    signal_count: int
    active_hold_count: int
    by_status: dict[str, int]
    by_severity: dict[str, int]
    latest_scan_at: datetime | None


class OverviewEnvelope(BaseModel):
    data: OverviewResponse
    request_id: str


class RuleResponse(BaseModel):
    code: str
    rule_version: int
    requirement_key: str
    severity: str
    action: str
    data_sources: list[str]
    calibration_dataset_version: str
    engineering_case_count: int
    pilot_false_positive_rate: str | None
    production_approved: bool


class RuleCatalogResponse(BaseModel):
    algorithm_version: str
    manifest_hash: str
    calibration_dataset_version: str
    calibration_scope: Literal["SYNTHETIC_REGRESSION"]
    requirement_count: int
    rule_count: int
    production_approved: bool
    rules: list[RuleResponse]


class RuleCatalogEnvelope(BaseModel):
    data: RuleCatalogResponse
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


def _scope_condition(
    principal: Principal,
    column: InstrumentedAttribute[UUID],
) -> ColumnElement[bool] | None:
    if principal.must_change_password:
        raise DomainError(
            code="PASSWORD_CHANGE_REQUIRED",
            message_key="errors.auth.password_change_required",
            status_code=403,
        )
    grants = [grant for grant in principal.roles if grant.role in READ_ROLES]
    if not grants:
        raise risk_error("AUTHORIZATION_DENIED", 403)
    if any(grant.cooperative_id is None for grant in grants):
        return None
    cooperative_ids = {grant.cooperative_id for grant in grants if grant.cooperative_id is not None}
    return column.in_(cooperative_ids)


def _command(result: RiskCommandResult) -> CommandEnvelope:
    return CommandEnvelope(
        data=ApiCommandResult(
            event_id=result.event_id,
            object_id=result.object_id,
            replayed=result.replayed,
        ),
        request_id=get_request_id(),
    )


@router.get("/rules", response_model=RuleCatalogEnvelope)
async def rule_catalog(principal: PrincipalDependency) -> RuleCatalogEnvelope:
    _scope_condition(principal, AntifraudSignal.cooperative_id)
    manifest = rule_manifest_payload()
    return RuleCatalogEnvelope(
        data=RuleCatalogResponse(
            algorithm_version=ALGORITHM_VERSION,
            manifest_hash=rule_manifest_hash(),
            calibration_dataset_version=CALIBRATION_DATASET_VERSION,
            calibration_scope="SYNTHETIC_REGRESSION",
            requirement_count=len({str(item["requirement_key"]) for item in manifest}),
            rule_count=len(manifest),
            production_approved=False,
            rules=[RuleResponse.model_validate(item) for item in manifest],
        ),
        request_id=get_request_id(),
    )


async def _commit_command(
    database: DatabaseDependency,
    action: CommandAction,
) -> CommandEnvelope:
    async with database.session() as session:
        try:
            result = await action(session)
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise risk_error("ANTIFRAUD_CONFLICT", 409) from exc
    return _command(result)


@router.get("/overview", response_model=OverviewEnvelope)
async def overview(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    cooperative_id: UUID | None = None,
) -> OverviewEnvelope:
    condition = _scope_condition(principal, AntifraudSignal.cooperative_id)
    base_conditions: list[ColumnElement[bool]] = []
    if condition is not None:
        base_conditions.append(condition)
    if cooperative_id is not None:
        base_conditions.append(AntifraudSignal.cooperative_id == cooperative_id)
    async with database.session() as session:
        status_rows = list(
            (
                await session.execute(
                    select(AntifraudSignal.status, func.count(AntifraudSignal.id))
                    .where(*base_conditions)
                    .group_by(AntifraudSignal.status)
                )
            ).all()
        )
        severity_rows = list(
            (
                await session.execute(
                    select(AntifraudSignal.severity, func.count(AntifraudSignal.id))
                    .where(*base_conditions)
                    .group_by(AntifraudSignal.severity)
                )
            ).all()
        )
        active_hold_count = int(
            (
                await session.execute(
                    select(func.count(AntifraudSignal.id)).where(
                        *base_conditions,
                        AntifraudSignal.automation_action == "HOLD",
                        AntifraudSignal.status.in_({"OPEN", "IN_REVIEW", "CONFIRMED"}),
                    )
                )
            ).scalar_one()
        )
        cooperative_count = int(
            (
                await session.execute(
                    select(func.count(func.distinct(AntifraudSignal.cooperative_id))).where(
                        *base_conditions
                    )
                )
            ).scalar_one()
        )
        scan_condition = _scope_condition(principal, AntifraudScan.cooperative_id)
        scan_conditions: list[ColumnElement[bool]] = []
        if scan_condition is not None:
            scan_conditions.append(scan_condition)
        if cooperative_id is not None:
            scan_conditions.append(AntifraudScan.cooperative_id == cooperative_id)
        latest_scan_at = (
            await session.execute(
                select(func.max(AntifraudScan.created_at)).where(*scan_conditions)
            )
        ).scalar_one()
    by_status = {str(key): int(value) for key, value in status_rows}
    by_severity = {str(key): int(value) for key, value in severity_rows}
    return OverviewEnvelope(
        data=OverviewResponse(
            cooperative_count=cooperative_count,
            signal_count=sum(by_status.values()),
            active_hold_count=active_hold_count,
            by_status=by_status,
            by_severity=by_severity,
            latest_scan_at=latest_scan_at,
        ),
        request_id=get_request_id(),
    )


@router.get("/scans", response_model=Collection[ScanResponse])
async def list_scans(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    cooperative_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> Collection[ScanResponse]:
    condition = _scope_condition(principal, AntifraudScan.cooperative_id)
    statement = select(AntifraudScan).order_by(AntifraudScan.created_at.desc(), AntifraudScan.id)
    if condition is not None:
        statement = statement.where(condition)
    if cooperative_id is not None:
        statement = statement.where(AntifraudScan.cooperative_id == cooperative_id)
    async with database.session() as session:
        rows = list((await session.execute(statement.limit(limit))).scalars())
    return Collection(data=rows, request_id=get_request_id())


@router.get("/signals", response_model=Collection[SignalResponse])
async def list_signals(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    cooperative_id: UUID | None = None,
    status: str | None = Query(default=None, max_length=16),
    severity: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=200, ge=1, le=500),
) -> Collection[SignalResponse]:
    condition = _scope_condition(principal, AntifraudSignal.cooperative_id)
    statement = select(AntifraudSignal).order_by(
        AntifraudSignal.last_seen_at.desc(), AntifraudSignal.id
    )
    if condition is not None:
        statement = statement.where(condition)
    if cooperative_id is not None:
        statement = statement.where(AntifraudSignal.cooperative_id == cooperative_id)
    if status is not None:
        statement = statement.where(AntifraudSignal.status == status.upper())
    if severity is not None:
        statement = statement.where(AntifraudSignal.severity == severity.upper())
    async with database.session() as session:
        rows = list((await session.execute(statement.limit(limit))).scalars())
    return Collection(data=rows, request_id=get_request_id())


@router.post("/scans", response_model=CommandEnvelope, status_code=201)
async def run_scan(
    payload: ScanRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await AntifraudService(settings).scan(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/signals/{signal_id}/review",
    response_model=CommandEnvelope,
    status_code=201,
)
async def begin_review(
    signal_id: UUID,
    payload: ReviewRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        return await AntifraudService(settings).begin_review(
            session,
            principal=principal,
            signal_id=signal_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/signals/{signal_id}/decision",
    response_model=CommandEnvelope,
    status_code=201,
)
async def decide_signal(
    signal_id: UUID,
    payload: DecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> RiskCommandResult:
        await require_step_up(
            session,
            principal,
            operation="ANTIFRAUD_DECIDE",
            emergency_roles=frozenset({RoleCode.SECURITY_ADMIN}),
            request_id=_request_uuid(),
        )
        return await AntifraudService(settings).decide(
            session,
            principal=principal,
            signal_id=signal_id,
            decision=AntifraudSignalStatus(payload.decision),
            rationale=payload.rationale,
            evidence_ids=payload.evidence_ids,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)
