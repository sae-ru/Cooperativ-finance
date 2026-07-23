"""Role-scoped API for deterministic local clearing cycles."""

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

from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.identity_schemas import CommandEnvelope
from cooperative_clearing.api.identity_schemas import CommandResult as ApiCommandResult
from cooperative_clearing.modules.clearing.application.common import ClearingCommandResult
from cooperative_clearing.modules.clearing.application.service import ClearingService
from cooperative_clearing.modules.clearing.domain.engine import RoundingMode, clearing_error
from cooperative_clearing.modules.clearing.domain.verifier import verify_proof_payload
from cooperative_clearing.modules.clearing.infrastructure.models import (
    ClearingAccountingExport,
    ClearingApproval,
    ClearingCycle,
    ClearingDispute,
    ClearingEntry,
    ClearingInputSnapshot,
    ClearingPolicy,
    ClearingPosition,
    ClearingProof,
    ClearingStatement,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/clearing", tags=["local-clearing"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]

GLOBAL_READ_ROLES = {RoleCode.AUDITOR, RoleCode.SECURITY_ADMIN}
SCOPED_ADMIN_ROLES = {
    RoleCode.COOPERATIVE_ADMIN,
    RoleCode.RISK_ADMIN,
    RoleCode.CLEARING_OPERATOR,
    RoleCode.CLEARING_CONTROLLER,
    RoleCode.CLEARING_FINALIZER,
}


class PolicyProposeRequest(BaseModel):
    cooperative_id: UUID
    valuation_unit_id: UUID
    decimal_scale: int = Field(ge=0, le=12)
    rounding_mode: RoundingMode = RoundingMode.DOWN
    minimum_operation: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    max_iterations: int = Field(ge=1, le=100_000)
    max_cycle_length: int = Field(ge=3, le=12)
    dispute_window_seconds: int = Field(ge=0, le=2_592_000)
    required_approvals: int = Field(ge=1, le=3)
    liquidity_order: list[str] = Field(min_length=1, max_length=16)


class VersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class CycleCreateRequest(BaseModel):
    cooperative_id: UUID
    policy_id: UUID
    cycle_code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    period_start: datetime
    period_end: datetime


class PreviewApprovalRequest(BaseModel):
    expected_version: int = Field(ge=1)
    input_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    result_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class DisputeOpenRequest(BaseModel):
    entry_id: UUID
    reason_code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    statement: str = Field(min_length=2, max_length=4000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=1)


class DisputeDecisionRequest(BaseModel):
    decision: Literal["UPHOLD", "REJECT"]
    resolution_notes: str = Field(min_length=2, max_length=4000)
    expected_version: int = Field(ge=1)
    expected_cycle_version: int = Field(ge=1)


class FinalizeRequest(BaseModel):
    expected_version: int = Field(ge=1)
    result_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ProofVerifyRequest(BaseModel):
    proof: dict[str, object]


class ClearingPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    policy_version: int
    valuation_unit_id: UUID
    algorithm_id: str
    algorithm_version: str
    decimal_scale: int
    rounding_mode: str
    minimum_operation: Decimal
    max_iterations: int
    max_cycle_length: int
    dispute_window_seconds: int
    required_approvals: int
    liquidity_order: list[str]
    terms_hash: str
    status: str
    proposed_by_member_id: UUID
    approved_by_member_id: UUID | None
    created_at: datetime
    approved_at: datetime | None
    version: int


class CycleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    policy_id: UUID
    cycle_code: str
    period_start: datetime
    period_end: datetime
    status: str
    collected_count: int
    input_hash: str | None
    parameters_hash: str | None
    result_hash: str | None
    dispute_until: datetime | None
    created_by_member_id: UUID
    created_event_id: UUID
    previewed_at: datetime | None
    finalized_at: datetime | None
    reconciled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class SnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cycle_id: UUID
    input_version: int
    policy_version: int
    ordered_payload: dict[str, object]
    input_hash: str
    frozen_by_member_id: UUID
    frozen_event_id: UUID
    frozen_at: datetime


class EntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cycle_id: UUID
    obligation_id: UUID
    debtor_member_id: UUID
    creditor_member_id: UUID
    unit_id: UUID
    obligation_version: int
    amount_before: Decimal
    cleared_amount: Decimal
    amount_after: Decimal
    inclusion_status: str
    exclusion_reason: str | None
    allocations: list[dict[str, object]]
    created_at: datetime


class PositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cycle_id: UUID
    member_id: UUID
    unit_id: UUID
    incoming_before: Decimal
    outgoing_before: Decimal
    incoming_cleared: Decimal
    outgoing_cleared: Decimal
    incoming_after: Decimal
    outgoing_after: Decimal
    net_before: Decimal
    net_after: Decimal


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cycle_id: UUID
    approval_type: str
    input_hash: str
    result_hash: str
    member_id: UUID
    role_assignment_id: UUID
    event_id: UUID
    approved_at: datetime


class DisputeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cycle_id: UUID
    entry_id: UUID
    reason_code: str
    statement: str
    evidence_refs: list[dict[str, object]]
    status: str
    opened_by_member_id: UUID
    opened_event_id: UUID
    resolution_notes: str | None
    resolved_by_member_id: UUID | None
    resolution_event_id: UUID | None
    created_at: datetime
    resolved_at: datetime | None
    version: int


class ProofResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cycle_id: UUID
    proof_payload: dict[str, object]
    proof_hash: str
    finalized_event_id: UUID
    node_event_hash: str
    created_at: datetime


class StatementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cycle_id: UUID
    member_id: UUID
    unit_id: UUID
    statement_payload: dict[str, object]
    statement_hash: str
    created_event_id: UUID
    created_at: datetime


class AccountingExportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cycle_id: UUID
    export_payload: dict[str, object]
    package_hash: str
    created_event_id: UUID
    created_at: datetime


class VerificationResponse(BaseModel):
    valid: bool
    input_hash: str
    parameters_hash: str
    result_hash: str
    proof_hash: str


class VerificationEnvelope(BaseModel):
    data: VerificationResponse
    request_id: str


class Collection[T](BaseModel):
    data: list[T]
    request_id: str


class ObjectEnvelope[T](BaseModel):
    data: T
    request_id: str


CommandAction = Callable[[AsyncSession], Awaitable[ClearingCommandResult]]


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


def _has_global_role(principal: Principal) -> bool:
    return any(
        grant.role in GLOBAL_READ_ROLES and grant.cooperative_id is None
        for grant in principal.roles
    )


def _admin_scopes(principal: Principal) -> set[UUID] | None:
    _require_readable(principal)
    if _has_global_role(principal):
        return None
    return {
        grant.cooperative_id
        for grant in principal.roles
        if grant.role in SCOPED_ADMIN_ROLES and grant.cooperative_id is not None
    }


def _cycle_filter(principal: Principal) -> ColumnElement[bool] | None:
    scopes = _admin_scopes(principal)
    if scopes is None:
        return None
    conditions: list[ColumnElement[bool]] = []
    if scopes:
        conditions.append(ClearingCycle.cooperative_id.in_(scopes))
    if principal.member_id is not None:
        participant_cycles = select(ClearingEntry.cycle_id).where(
            or_(
                ClearingEntry.debtor_member_id == principal.member_id,
                ClearingEntry.creditor_member_id == principal.member_id,
            )
        )
        conditions.append(ClearingCycle.id.in_(participant_cycles))
        conditions.append(ClearingCycle.created_by_member_id == principal.member_id)
    return or_(*conditions) if conditions else false()


async def _visible_cycle(
    session: AsyncSession, principal: Principal, cycle_id: UUID, *, admin_only: bool
) -> ClearingCycle:
    condition = _cycle_filter(principal)
    statement = select(ClearingCycle).where(ClearingCycle.id == cycle_id)
    if condition is not None:
        statement = statement.where(condition)
    cycle = (await session.execute(statement)).scalar_one_or_none()
    if cycle is None:
        raise clearing_error("CLEARING_CYCLE_NOT_FOUND", 404)
    if admin_only:
        scopes = _admin_scopes(principal)
        if scopes is not None and cycle.cooperative_id not in scopes:
            raise clearing_error("AUTHORIZATION_DENIED", 403)
    return cycle


def _command(result: ClearingCommandResult) -> CommandEnvelope:
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
            raise clearing_error("CONFLICT", 409) from exc
    return _command(result)


@router.get("/policies", response_model=Collection[ClearingPolicyResponse])
async def list_policies(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[ClearingPolicyResponse]:
    scopes = _admin_scopes(principal)
    statement = select(ClearingPolicy).order_by(
        ClearingPolicy.cooperative_id, ClearingPolicy.policy_version.desc()
    )
    if scopes is not None:
        statement = (
            statement.where(ClearingPolicy.cooperative_id.in_(scopes))
            if scopes
            else statement.where(false())
        )
    if status:
        statement = statement.where(ClearingPolicy.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/cycles", response_model=Collection[CycleResponse])
async def list_cycles(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=32),
) -> Collection[CycleResponse]:
    condition = _cycle_filter(principal)
    statement = select(ClearingCycle).order_by(ClearingCycle.created_at.desc(), ClearingCycle.id)
    if condition is not None:
        statement = statement.where(condition)
    if status:
        statement = statement.where(ClearingCycle.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/cycles/{cycle_id}/input", response_model=ObjectEnvelope[SnapshotResponse])
async def get_input(
    cycle_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> ObjectEnvelope[SnapshotResponse]:
    async with database.session() as session:
        await _visible_cycle(session, principal, cycle_id, admin_only=True)
        item = (
            await session.execute(
                select(ClearingInputSnapshot).where(ClearingInputSnapshot.cycle_id == cycle_id)
            )
        ).scalar_one_or_none()
    if item is None:
        raise clearing_error("CLEARING_SNAPSHOT_NOT_FOUND", 404)
    return ObjectEnvelope(data=item, request_id=get_request_id())


@router.get("/cycles/{cycle_id}/entries", response_model=Collection[EntryResponse])
async def list_entries(
    cycle_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> Collection[EntryResponse]:
    async with database.session() as session:
        cycle = await _visible_cycle(session, principal, cycle_id, admin_only=False)
        scopes = _admin_scopes(principal)
        statement = select(ClearingEntry).where(ClearingEntry.cycle_id == cycle.id)
        if scopes is not None and cycle.cooperative_id not in scopes:
            if principal.member_id is None:
                statement = statement.where(false())
            else:
                statement = statement.where(
                    or_(
                        ClearingEntry.debtor_member_id == principal.member_id,
                        ClearingEntry.creditor_member_id == principal.member_id,
                    )
                )
        items = list(
            (await session.execute(statement.order_by(ClearingEntry.obligation_id))).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/cycles/{cycle_id}/positions", response_model=Collection[PositionResponse])
async def list_positions(
    cycle_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> Collection[PositionResponse]:
    async with database.session() as session:
        cycle = await _visible_cycle(session, principal, cycle_id, admin_only=False)
        scopes = _admin_scopes(principal)
        statement = select(ClearingPosition).where(ClearingPosition.cycle_id == cycle.id)
        if scopes is not None and cycle.cooperative_id not in scopes:
            statement = statement.where(ClearingPosition.member_id == principal.member_id)
        items = list(
            (await session.execute(statement.order_by(ClearingPosition.member_id))).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/cycles/{cycle_id}/approvals", response_model=Collection[ApprovalResponse])
async def list_approvals(
    cycle_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> Collection[ApprovalResponse]:
    async with database.session() as session:
        await _visible_cycle(session, principal, cycle_id, admin_only=True)
        items = list(
            (
                await session.execute(
                    select(ClearingApproval)
                    .where(ClearingApproval.cycle_id == cycle_id)
                    .order_by(ClearingApproval.approved_at)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/cycles/{cycle_id}/disputes", response_model=Collection[DisputeResponse])
async def list_disputes(
    cycle_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> Collection[DisputeResponse]:
    async with database.session() as session:
        cycle = await _visible_cycle(session, principal, cycle_id, admin_only=False)
        scopes = _admin_scopes(principal)
        statement = select(ClearingDispute).where(ClearingDispute.cycle_id == cycle.id)
        if scopes is not None and cycle.cooperative_id not in scopes:
            statement = statement.where(ClearingDispute.opened_by_member_id == principal.member_id)
        items = list(
            (await session.execute(statement.order_by(ClearingDispute.created_at))).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/cycles/{cycle_id}/proof", response_model=ObjectEnvelope[ProofResponse])
async def get_proof(
    cycle_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> ObjectEnvelope[ProofResponse]:
    async with database.session() as session:
        await _visible_cycle(session, principal, cycle_id, admin_only=True)
        item = (
            await session.execute(select(ClearingProof).where(ClearingProof.cycle_id == cycle_id))
        ).scalar_one_or_none()
    if item is None:
        raise clearing_error("CLEARING_PROOF_NOT_FOUND", 404)
    return ObjectEnvelope(data=item, request_id=get_request_id())


@router.get(
    "/cycles/{cycle_id}/statements/{member_id}",
    response_model=Collection[StatementResponse],
)
async def get_statements(
    cycle_id: UUID,
    member_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> Collection[StatementResponse]:
    async with database.session() as session:
        cycle = await _visible_cycle(session, principal, cycle_id, admin_only=False)
        scopes = _admin_scopes(principal)
        if (
            scopes is not None
            and cycle.cooperative_id not in scopes
            and principal.member_id != member_id
        ):
            raise clearing_error("AUTHORIZATION_DENIED", 403)
        items = list(
            (
                await session.execute(
                    select(ClearingStatement)
                    .where(
                        ClearingStatement.cycle_id == cycle_id,
                        ClearingStatement.member_id == member_id,
                    )
                    .order_by(ClearingStatement.unit_id)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get(
    "/cycles/{cycle_id}/accounting-export",
    response_model=ObjectEnvelope[AccountingExportResponse],
)
async def get_accounting_export(
    cycle_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> ObjectEnvelope[AccountingExportResponse]:
    async with database.session() as session:
        await _visible_cycle(session, principal, cycle_id, admin_only=True)
        item = (
            await session.execute(
                select(ClearingAccountingExport).where(
                    ClearingAccountingExport.cycle_id == cycle_id
                )
            )
        ).scalar_one_or_none()
    if item is None:
        raise clearing_error("ACCOUNTING_EXPORT_NOT_FOUND", 404)
    return ObjectEnvelope(data=item, request_id=get_request_id())


@router.post("/proofs/verify", response_model=VerificationEnvelope)
async def verify_proof(
    payload: ProofVerifyRequest,
    principal: PrincipalDependency,
) -> VerificationEnvelope:
    _require_readable(principal)
    result = verify_proof_payload(payload.proof)
    return VerificationEnvelope(data=VerificationResponse(**result), request_id=get_request_id())


@router.post("/policies", response_model=CommandEnvelope, status_code=201)
async def propose_policy(
    payload: PolicyProposeRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> ClearingCommandResult:
        return await ClearingService(settings).propose_policy(
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
    payload: VersionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> ClearingCommandResult:
        return await ClearingService(settings).approve_policy(
            session,
            principal=principal,
            policy_id=policy_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/cycles", response_model=CommandEnvelope, status_code=201)
async def create_cycle(
    payload: CycleCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> ClearingCommandResult:
        return await ClearingService(settings).create_cycle(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


def _cycle_version_command(method_name: str) -> Callable[..., Awaitable[CommandEnvelope]]:
    async def endpoint(
        cycle_id: UUID,
        payload: VersionRequest,
        idempotency_key: IdempotencyKey,
        principal: PrincipalDependency,
        database: DatabaseDependency,
        settings: SettingsDependency,
    ) -> CommandEnvelope:
        async def action(session: AsyncSession) -> ClearingCommandResult:
            method = getattr(ClearingService(settings), method_name)
            result: ClearingCommandResult = await method(
                session,
                principal=principal,
                cycle_id=cycle_id,
                expected_version=payload.expected_version,
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            return result

        return await _commit_command(database, action)

    endpoint.__name__ = method_name
    endpoint.__qualname__ = method_name
    return endpoint


router.post("/cycles/{cycle_id}/collect", response_model=CommandEnvelope, status_code=201)(
    _cycle_version_command("collect")
)
router.post("/cycles/{cycle_id}/freeze-input", response_model=CommandEnvelope, status_code=201)(
    _cycle_version_command("freeze_input")
)
router.post("/cycles/{cycle_id}/preview", response_model=CommandEnvelope, status_code=201)(
    _cycle_version_command("preview")
)
router.post("/cycles/{cycle_id}/ready", response_model=CommandEnvelope, status_code=201)(
    _cycle_version_command("mark_ready")
)
router.post("/cycles/{cycle_id}/reconcile", response_model=CommandEnvelope, status_code=201)(
    _cycle_version_command("reconcile")
)


@router.post("/cycles/{cycle_id}/approvals", response_model=CommandEnvelope, status_code=201)
async def approve_preview(
    cycle_id: UUID,
    payload: PreviewApprovalRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> ClearingCommandResult:
        return await ClearingService(settings).approve_preview(
            session,
            principal=principal,
            cycle_id=cycle_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/cycles/{cycle_id}/disputes", response_model=CommandEnvelope, status_code=201)
async def open_dispute(
    cycle_id: UUID,
    payload: DisputeOpenRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> ClearingCommandResult:
        return await ClearingService(settings).open_dispute(
            session,
            principal=principal,
            cycle_id=cycle_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/disputes/{dispute_id}/decision", response_model=CommandEnvelope, status_code=201)
async def decide_dispute(
    dispute_id: UUID,
    payload: DisputeDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> ClearingCommandResult:
        return await ClearingService(settings).decide_dispute(
            session,
            principal=principal,
            dispute_id=dispute_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/cycles/{cycle_id}/finalize", response_model=CommandEnvelope, status_code=201)
async def finalize(
    cycle_id: UUID,
    payload: FinalizeRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> ClearingCommandResult:
        return await ClearingService(settings).finalize(
            session,
            principal=principal,
            cycle_id=cycle_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)
