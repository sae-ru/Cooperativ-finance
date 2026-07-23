"""Operator API for accountable inter-node clearing and recovery."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.modules.audit.infrastructure.repository import (
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.clearing.domain.engine import RoundingMode
from cooperative_clearing.modules.federation.application.clearing_coordinator import (
    CoordinatorResult,
    FederatedClearingCoordinator,
)
from cooperative_clearing.modules.federation.application.common import federation_actor
from cooperative_clearing.modules.federation.application.inter_node_clearing import (
    InterNodeClearingService,
)
from cooperative_clearing.modules.federation.domain.federated_clearing import (
    FederatedClearingPolicy,
)
from cooperative_clearing.modules.federation.domain.types import federation_error
from cooperative_clearing.modules.federation.infrastructure.clearing_models import (
    FederatedClearingCycle,
    FederatedClearingPolicyRecord,
    FederatedClearingProof,
    FederatedClearingProposal,
    FederatedCommitCertificate,
    FederatedInputSnapshot,
    InterNodeObligation,
    NodeApplyReceipt,
    NodeClearingApproval,
    NodePrepareReceipt,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/federated-clearing", tags=["federated-clearing"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]

READ_ROLES = {
    RoleCode.CLEARING_OPERATOR,
    RoleCode.CLEARING_CONTROLLER,
    RoleCode.CLEARING_FINALIZER,
    RoleCode.RISK_ADMIN,
    RoleCode.NODE_BUSINESS_OPERATOR,
    RoleCode.NODE_AUDITOR,
    RoleCode.AUDITOR,
    RoleCode.SECURITY_ADMIN,
}
POLICY_ROLES = {RoleCode.CLEARING_FINALIZER, RoleCode.RISK_ADMIN}
OPERATOR_ROLES = {RoleCode.CLEARING_OPERATOR, RoleCode.NODE_BUSINESS_OPERATOR}
CONTROLLER_ROLES = {RoleCode.CLEARING_CONTROLLER}
FINALIZER_ROLES = {RoleCode.CLEARING_FINALIZER}


class PolicyCreateRequest(BaseModel):
    policy_code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    policy_version: int = Field(ge=1)
    valuation_unit: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    decimal_scale: int = Field(default=2, ge=0, le=12)
    rounding_mode: RoundingMode = RoundingMode.DOWN
    minimum_operation: Decimal = Field(default=Decimal("0.01"), ge=0, decimal_places=12)
    max_iterations: int = Field(default=10_000, ge=1, le=100_000)
    max_cycle_length: int = Field(default=8, ge=3, le=12)
    prepare_ttl_seconds: int = Field(default=900, ge=30, le=86_400)


class ObligationCreateRequest(BaseModel):
    obligation_id: UUID | None = None
    debtor_node_code: str = Field(min_length=3, max_length=63)
    creditor_node_code: str = Field(min_length=3, max_length=63)
    unit_code: str = Field(min_length=1, max_length=32)
    amount: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    source_reference: str = Field(min_length=1, max_length=200)
    source_event_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    liquidity_class: str = Field(default="UNASSESSED", min_length=1, max_length=32)


class CycleCreateRequest(BaseModel):
    cycle_id: UUID | None = None
    cycle_code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    policy_id: UUID
    period_start: datetime
    period_end: datetime
    participant_node_codes: list[str] = Field(min_length=2, max_length=100)


class ReleaseRequest(BaseModel):
    expired: bool = False


class CommandResponse(BaseModel):
    cycle_id: UUID | None = None
    object_id: UUID | None = None
    event_id: UUID | None = None
    status: str
    replayed: bool = False
    nodes: list[dict[str, str]] = Field(default_factory=list)


class FederatedClearingPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    policy_code: str
    policy_version: int
    valuation_unit: str
    algorithm_id: str
    algorithm_version: str
    decimal_scale: int
    rounding_mode: str
    minimum_operation: Decimal
    max_iterations: int
    max_cycle_length: int
    prepare_ttl_seconds: int
    policy_hash: str
    status: str
    created_by_member_id: UUID
    created_event_id: UUID
    created_at: datetime
    version: int


class ObligationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    home_node_code: str
    debtor_node_code: str
    creditor_node_code: str
    unit_code: str
    original_amount: Decimal
    outstanding_amount: Decimal
    cleared_amount: Decimal
    source_reference: str
    source_event_hash: str
    liquidity_class: str
    status: str
    prepared_cycle_id: UUID | None
    prepared_input_hash: str | None
    prepared_until: datetime | None
    created_event_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int


class CycleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cycle_code: str
    coordinator_node_code: str
    policy_id: UUID
    period_start: datetime
    period_end: datetime
    status: str
    participant_node_codes: list[str]
    affected_node_codes: list[str]
    input_hash: str | None
    result_hash: str | None
    certificate_hash: str | None
    created_by_member_id: UUID
    created_event_id: UUID
    created_at: datetime
    updated_at: datetime
    prepared_at: datetime | None
    certified_at: datetime | None
    reconciled_at: datetime | None
    version: int


class CycleEvidenceResponse(BaseModel):
    cycle: CycleResponse
    snapshots: list[dict[str, Any]]
    prepare_receipts: list[dict[str, Any]]
    proposal: dict[str, Any] | None
    approvals: list[dict[str, Any]]
    certificate: dict[str, Any] | None
    apply_receipts: list[dict[str, Any]]
    proof: dict[str, Any] | None


class CommandEnvelope(BaseModel):
    data: CommandResponse
    request_id: str


class PolicyCollection(BaseModel):
    data: list[FederatedClearingPolicyResponse]
    request_id: str


class ObligationCollection(BaseModel):
    data: list[ObligationResponse]
    request_id: str


class CycleCollection(BaseModel):
    data: list[CycleResponse]
    request_id: str


class CycleEvidenceEnvelope(BaseModel):
    data: CycleEvidenceResponse
    request_id: str


CommandAction = Callable[[AsyncSession], Awaitable[dict[str, object]]]


async def _run_command(
    *,
    database: DatabaseDependency,
    principal: Principal,
    idempotency_key: str,
    operation: str,
    payload: object,
    action: CommandAction,
) -> CommandEnvelope:
    async with database.session() as session:
        record = await IdempotencyRepository(session).begin(
            actor_user_id=principal.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_payload_hash(payload),
        )
        if record.status == "COMPLETED":
            stored = dict(record.response_payload or {})
            stored["replayed"] = True
            return CommandEnvelope(
                data=CommandResponse.model_validate(stored), request_id=get_request_id()
            )
        try:
            result = await action(session)
            command = CommandResponse.model_validate({**result, "replayed": False})
            response = command.model_dump(mode="json")
            IdempotencyRepository.complete(record, response_status=201, response_payload=response)
            await session.commit()
            return CommandEnvelope(data=command, request_id=get_request_id())
        except IntegrityError as exc:
            await session.rollback()
            raise federation_error("FEDERATED_CONCURRENT_WRITE", 409) from exc


@router.post("/policies", response_model=CommandEnvelope, status_code=201)
async def create_policy(
    payload: PolicyCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> dict[str, object]:
        actor = await federation_actor(session, principal, POLICY_ROLES)
        row = await InterNodeClearingService(settings).create_policy(
            session,
            user_id=principal.user_id,
            actor=actor,
            policy_code=payload.policy_code,
            valuation_unit=payload.valuation_unit,
            policy=FederatedClearingPolicy(
                policy_version=payload.policy_version,
                decimal_scale=payload.decimal_scale,
                rounding_mode=payload.rounding_mode,
                minimum_operation=payload.minimum_operation,
                max_iterations=payload.max_iterations,
                max_cycle_length=payload.max_cycle_length,
                prepare_ttl_seconds=payload.prepare_ttl_seconds,
            ),
        )
        return {
            "object_id": row.id,
            "event_id": row.created_event_id,
            "status": row.status,
        }

    return await _run_command(
        database=database,
        principal=principal,
        idempotency_key=idempotency_key,
        operation="federated-clearing.policy.create",
        payload=payload.model_dump(mode="json"),
        action=action,
    )


@router.post("/obligations", response_model=CommandEnvelope, status_code=201)
async def create_obligation(
    payload: ObligationCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> dict[str, object]:
        actor = await federation_actor(session, principal, OPERATOR_ROLES)
        row = await InterNodeClearingService(settings).register_obligation(
            session,
            actor=actor,
            home_node_code=settings.node_code,
            debtor_node_code=payload.debtor_node_code,
            creditor_node_code=payload.creditor_node_code,
            unit_code=payload.unit_code,
            amount=payload.amount,
            source_reference=payload.source_reference,
            source_event_hash=payload.source_event_hash,
            liquidity_class=payload.liquidity_class,
            obligation_id=payload.obligation_id,
        )
        return {
            "object_id": row.id,
            "event_id": row.created_event_id,
            "status": row.status,
        }

    return await _run_command(
        database=database,
        principal=principal,
        idempotency_key=idempotency_key,
        operation="federated-clearing.obligation.create",
        payload=payload.model_dump(mode="json"),
        action=action,
    )


@router.post("/cycles", response_model=CommandEnvelope, status_code=201)
async def create_cycle(
    payload: CycleCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    cycle_id = payload.cycle_id or uuid4()

    async def action(session: AsyncSession) -> dict[str, object]:
        actor = await federation_actor(session, principal, OPERATOR_ROLES)
        policy = await session.get(FederatedClearingPolicyRecord, payload.policy_id)
        if policy is None or policy.status != "ACTIVE":
            raise federation_error("FEDERATED_POLICY_NOT_FOUND", 404)
        row = await InterNodeClearingService(settings).create_cycle(
            session,
            user_id=principal.user_id,
            actor=actor,
            cycle_id=cycle_id,
            cycle_code=payload.cycle_code,
            coordinator_node_code=settings.node_code,
            policy=policy,
            period_start=payload.period_start,
            period_end=payload.period_end,
            participant_node_codes=tuple(payload.participant_node_codes),
        )
        return {
            "cycle_id": row.id,
            "object_id": row.id,
            "event_id": row.created_event_id,
            "status": row.status,
        }

    command_payload = payload.model_dump(mode="json")
    return await _run_command(
        database=database,
        principal=principal,
        idempotency_key=idempotency_key,
        operation="federated-clearing.cycle.create",
        payload=command_payload,
        action=action,
    )


def _coordinator_endpoint(
    path: str,
    *,
    roles: set[RoleCode],
) -> Callable[..., Awaitable[CommandEnvelope]]:
    async def endpoint(
        cycle_id: UUID,
        idempotency_key: IdempotencyKey,
        principal: PrincipalDependency,
        database: DatabaseDependency,
        settings: SettingsDependency,
    ) -> CommandEnvelope:
        async def action(session: AsyncSession) -> dict[str, object]:
            actor = await federation_actor(session, principal, roles)
            coordinator = FederatedClearingCoordinator(settings)
            if path == "snapshots":
                result = await coordinator.collect_snapshots(
                    session, cycle_id=cycle_id, actor=actor
                )
            elif path == "prepare":
                result = await coordinator.prepare_nodes(session, cycle_id=cycle_id, actor=actor)
            elif path == "proposal":
                result = await coordinator.publish_proposal(session, cycle_id=cycle_id)
            elif path == "approvals":
                result = await coordinator.collect_approvals(
                    session, cycle_id=cycle_id, actor=actor
                )
            elif path == "commit":
                result = await coordinator.certify_and_apply(
                    session, cycle_id=cycle_id, actor=actor
                )
            elif path == "recover":
                result = await coordinator.recover(session, cycle_id=cycle_id, actor=actor)
            else:
                raise federation_error("FEDERATED_OPERATION_INVALID", 500)
            return _coordinator_response(result)

        return await _run_command(
            database=database,
            principal=principal,
            idempotency_key=idempotency_key,
            operation=f"federated-clearing.cycle.{path}",
            payload={"cycle_id": str(cycle_id)},
            action=action,
        )

    return endpoint


router.add_api_route(
    "/cycles/{cycle_id}/snapshots/collect",
    _coordinator_endpoint("snapshots", roles=OPERATOR_ROLES),
    methods=["POST"],
    response_model=CommandEnvelope,
    status_code=201,
)
router.add_api_route(
    "/cycles/{cycle_id}/prepare",
    _coordinator_endpoint("prepare", roles=OPERATOR_ROLES),
    methods=["POST"],
    response_model=CommandEnvelope,
    status_code=201,
)
router.add_api_route(
    "/cycles/{cycle_id}/proposal",
    _coordinator_endpoint("proposal", roles=OPERATOR_ROLES),
    methods=["POST"],
    response_model=CommandEnvelope,
    status_code=201,
)
router.add_api_route(
    "/cycles/{cycle_id}/approvals/collect",
    _coordinator_endpoint("approvals", roles=OPERATOR_ROLES),
    methods=["POST"],
    response_model=CommandEnvelope,
    status_code=201,
)
router.add_api_route(
    "/cycles/{cycle_id}/commit",
    _coordinator_endpoint("commit", roles=FINALIZER_ROLES),
    methods=["POST"],
    response_model=CommandEnvelope,
    status_code=201,
)
router.add_api_route(
    "/cycles/{cycle_id}/recover",
    _coordinator_endpoint("recover", roles=FINALIZER_ROLES),
    methods=["POST"],
    response_model=CommandEnvelope,
    status_code=201,
)


@router.post("/cycles/{cycle_id}/approvals/local", response_model=CommandEnvelope, status_code=201)
async def approve_local(
    cycle_id: UUID,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> dict[str, object]:
        actor = await federation_actor(session, principal, CONTROLLER_ROLES)
        row = await InterNodeClearingService(settings).approve_local(
            session, cycle_id=cycle_id, actor=actor
        )
        return {
            "cycle_id": cycle_id,
            "object_id": row.id,
            "event_id": row.accepted_event_id,
            "status": "APPROVED",
        }

    return await _run_command(
        database=database,
        principal=principal,
        idempotency_key=idempotency_key,
        operation="federated-clearing.cycle.approve-local",
        payload={"cycle_id": str(cycle_id)},
        action=action,
    )


@router.post("/cycles/{cycle_id}/release", response_model=CommandEnvelope, status_code=201)
async def release_cycle(
    cycle_id: UUID,
    payload: ReleaseRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async def action(session: AsyncSession) -> dict[str, object]:
        await federation_actor(session, principal, FINALIZER_ROLES)
        result = await FederatedClearingCoordinator(settings).release(
            session, cycle_id=cycle_id, expired=payload.expired
        )
        return _coordinator_response(result)

    return await _run_command(
        database=database,
        principal=principal,
        idempotency_key=idempotency_key,
        operation="federated-clearing.cycle.release",
        payload={"cycle_id": str(cycle_id), **payload.model_dump(mode="json")},
        action=action,
    )


@router.get("/policies", response_model=PolicyCollection)
async def list_policies(
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> PolicyCollection:
    _require_read(principal)
    async with database.session() as session:
        rows = list(
            (
                await session.execute(
                    select(FederatedClearingPolicyRecord).order_by(
                        FederatedClearingPolicyRecord.policy_code,
                        FederatedClearingPolicyRecord.policy_version.desc(),
                    )
                )
            ).scalars()
        )
    return PolicyCollection(
        data=[FederatedClearingPolicyResponse.model_validate(row) for row in rows],
        request_id=get_request_id(),
    )


@router.get("/obligations", response_model=ObligationCollection)
async def list_obligations(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: Annotated[str | None, Query(max_length=24)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ObligationCollection:
    _require_read(principal)
    query = select(InterNodeObligation)
    if status is not None:
        query = query.where(InterNodeObligation.status == status.upper())
    query = query.order_by(InterNodeObligation.updated_at.desc()).limit(limit)
    async with database.session() as session:
        rows = list((await session.execute(query)).scalars())
    return ObligationCollection(
        data=[ObligationResponse.model_validate(row) for row in rows],
        request_id=get_request_id(),
    )


@router.get("/cycles", response_model=CycleCollection)
async def list_cycles(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> CycleCollection:
    _require_read(principal)
    query = select(FederatedClearingCycle)
    if status is not None:
        query = query.where(FederatedClearingCycle.status == status.upper())
    query = query.order_by(FederatedClearingCycle.updated_at.desc()).limit(limit)
    async with database.session() as session:
        rows = list((await session.execute(query)).scalars())
    return CycleCollection(
        data=[CycleResponse.model_validate(row) for row in rows], request_id=get_request_id()
    )


@router.get("/cycles/{cycle_id}", response_model=CycleEvidenceEnvelope)
async def cycle_evidence(
    cycle_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CycleEvidenceEnvelope:
    _require_read(principal)
    async with database.session() as session:
        cycle = await session.get(FederatedClearingCycle, cycle_id)
        if cycle is None:
            raise federation_error("FEDERATED_CYCLE_NOT_FOUND", 404)
        snapshots = await _rows(session, FederatedInputSnapshot, cycle_id)
        prepares = await _rows(session, NodePrepareReceipt, cycle_id)
        approvals = await _rows(session, NodeClearingApproval, cycle_id)
        applies = await _rows(session, NodeApplyReceipt, cycle_id)
        proposal = await _single(session, FederatedClearingProposal, cycle_id)
        certificate = await _single(session, FederatedCommitCertificate, cycle_id)
        proof = await _single(session, FederatedClearingProof, cycle_id)
        return CycleEvidenceEnvelope(
            data=CycleEvidenceResponse(
                cycle=CycleResponse.model_validate(cycle),
                snapshots=[_artifact_view(row, "snapshot") for row in snapshots],
                prepare_receipts=[_artifact_view(row, "prepare") for row in prepares],
                proposal=(_artifact_view(proposal, "proposal") if proposal is not None else None),
                approvals=[_artifact_view(row, "approval") for row in approvals],
                certificate=(
                    _artifact_view(certificate, "certificate") if certificate is not None else None
                ),
                apply_receipts=[_artifact_view(row, "apply") for row in applies],
                proof=_artifact_view(proof, "proof") if proof is not None else None,
            ),
            request_id=get_request_id(),
        )


def _coordinator_response(result: CoordinatorResult) -> dict[str, object]:
    return {
        "cycle_id": result.cycle_id,
        "object_id": result.cycle_id,
        "status": result.status,
        "nodes": [
            {
                "node_code": item.node_code,
                "phase": item.phase,
                "result_code": item.result_code,
            }
            for item in result.nodes
        ],
    }


def _require_read(principal: Principal) -> None:
    if not principal.has_role(READ_ROLES):
        raise federation_error("AUTHORIZATION_DENIED", 403)


async def _rows(
    session: AsyncSession,
    model: Any,
    cycle_id: UUID,
) -> list[Any]:
    return list(
        (
            await session.execute(
                select(model).where(model.cycle_id == cycle_id).order_by(model.id)
            )
        ).scalars()
    )


async def _single(session: AsyncSession, model: Any, cycle_id: UUID) -> Any | None:
    return (
        await session.execute(select(model).where(model.cycle_id == cycle_id))
    ).scalar_one_or_none()


def _artifact_view(row: Any, kind: str) -> dict[str, Any]:
    if kind == "snapshot":
        return {
            "node_code": row.node_code,
            "payload": row.snapshot_payload,
            "hash": row.snapshot_hash,
            "signer_fingerprint": row.signer_fingerprint,
            "expires_at": row.expires_at,
        }
    if kind == "prepare":
        return {
            "node_code": row.node_code,
            "payload": row.receipt_payload,
            "hash": row.receipt_hash,
            "signer_fingerprint": row.signer_fingerprint,
            "expires_at": row.expires_at,
        }
    if kind == "proposal":
        return {
            "payload": row.proposal_payload,
            "hash": row.result_hash,
            "signer_fingerprint": row.signer_fingerprint,
        }
    if kind == "approval":
        return {
            "node_code": row.node_code,
            "payload": row.approval_payload,
            "hash": row.approval_hash,
            "signer_fingerprint": row.signer_fingerprint,
            "approved_at": row.approved_at,
        }
    if kind == "certificate":
        return {
            "payload": row.certificate_payload,
            "hash": row.certificate_hash,
            "signer_fingerprint": row.signer_fingerprint,
            "certified_at": row.certified_at,
        }
    if kind == "apply":
        return {
            "node_code": row.node_code,
            "payload": row.receipt_payload,
            "hash": row.receipt_hash,
            "signer_fingerprint": row.signer_fingerprint,
            "applied_at": row.applied_at,
        }
    if kind == "proof":
        return {"payload": row.proof_payload, "hash": row.proof_hash}
    raise DomainError(
        code="FEDERATED_ARTIFACT_VIEW_INVALID",
        message_key="errors.federation.artifact_view_invalid",
        status_code=500,
    )
