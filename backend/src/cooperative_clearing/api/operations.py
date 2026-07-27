"""Protected local operational diagnostics and metrics."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field, SecretStr
from starlette.concurrency import run_in_threadpool

from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, require_role
from cooperative_clearing.modules.operations.application.diagnostics import (
    EXCLUDED_CATEGORIES,
    INCLUDED_FILES,
    build_encrypted_artifact,
)
from cooperative_clearing.modules.operations.application.readiness import (
    GetHostReadiness,
    HostReadiness,
    readiness_metrics,
    readiness_payload,
)
from cooperative_clearing.modules.operations.application.status import (
    GetOperationalSnapshot,
    OperationalSnapshot,
    snapshot_metrics,
    snapshot_payload,
)
from cooperative_clearing.shared.core.metrics import request_metrics
from cooperative_clearing.shared.core.request_context import get_request_id

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])
OPERATIONS_ROLES = {RoleCode.COOPERATIVE_ADMIN, RoleCode.SECURITY_ADMIN, RoleCode.AUDITOR}


class OperationalSnapshotResponse(BaseModel):
    generated_at: datetime
    schema_revision: str
    signed_events: int
    outbox_pending: int
    outbox_quarantined: int
    active_sessions: int
    open_trust_cases: int
    submitted_appeals: int
    open_sync_conflicts: int
    open_node_incidents: int
    pending_key_rotations: int
    open_offline_epochs: int
    issued_federation_forms: int
    active_federated_prepares: int
    pending_federated_applies: int
    expired_federated_prepares: int
    active_crisis_mandates: int
    issued_crisis_forms: int


class OperationalSnapshotEnvelope(BaseModel):
    data: OperationalSnapshotResponse
    request_id: str


class HostCheckResponse(BaseModel):
    name: str
    status: str
    code: str
    observed_at: datetime
    metrics: dict[str, int | str | bool | None]


class HostReadinessResponse(BaseModel):
    generated_at: datetime
    status: str
    checks: list[HostCheckResponse]


class HostReadinessEnvelope(BaseModel):
    data: HostReadinessResponse
    request_id: str


class DiagnosticPlanResponse(BaseModel):
    included: list[str]
    excluded: list[str]
    encryption: str


class DiagnosticPlanEnvelope(BaseModel):
    data: DiagnosticPlanResponse
    request_id: str


class DiagnosticBundleRequest(BaseModel):
    passphrase: SecretStr = Field(min_length=16, max_length=128)


def _authorize(principal: Principal) -> None:
    require_role(principal, OPERATIONS_ROLES)


def _request_uuid() -> UUID | None:
    try:
        return UUID(get_request_id())
    except ValueError:
        return None


def _response(snapshot: OperationalSnapshot) -> OperationalSnapshotResponse:
    return OperationalSnapshotResponse.model_validate(snapshot, from_attributes=True)


def _readiness_response(readiness: HostReadiness) -> HostReadinessResponse:
    return HostReadinessResponse(
        generated_at=readiness.generated_at,
        status=readiness.status,
        checks=[
            HostCheckResponse(
                name=check.name,
                status=check.status,
                code=check.code,
                observed_at=check.observed_at,
                metrics=check.metrics,
            )
            for check in readiness.checks
        ],
    )


@router.get("/snapshot", response_model=OperationalSnapshotEnvelope)
async def operational_snapshot(
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> OperationalSnapshotEnvelope:
    _authorize(principal)
    snapshot = await GetOperationalSnapshot(database).execute()
    return OperationalSnapshotEnvelope(data=_response(snapshot), request_id=get_request_id())


@router.get("/host-readiness", response_model=HostReadinessEnvelope)
async def host_readiness(
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> HostReadinessEnvelope:
    _authorize(principal)
    readiness = await GetHostReadiness(database, settings).execute()
    return HostReadinessEnvelope(
        data=_readiness_response(readiness),
        request_id=get_request_id(),
    )


@router.get("/diagnostic-bundle/plan", response_model=DiagnosticPlanEnvelope)
async def diagnostic_bundle_plan(
    principal: PrincipalDependency,
) -> DiagnosticPlanEnvelope:
    _authorize(principal)
    return DiagnosticPlanEnvelope(
        data=DiagnosticPlanResponse(
            included=list(INCLUDED_FILES),
            excluded=list(EXCLUDED_CATEGORIES),
            encryption="AES-256-GCM+scrypt",
        ),
        request_id=get_request_id(),
    )


@router.post("/diagnostic-bundle", response_class=Response)
async def diagnostic_bundle(
    payload: DiagnosticBundleRequest,
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> Response:
    _authorize(principal)
    snapshot = await GetOperationalSnapshot(database).execute()
    readiness = await GetHostReadiness(database, settings).execute()
    generated_at = datetime.now(UTC)
    artifact = await run_in_threadpool(
        build_encrypted_artifact,
        node_code=settings.node_code,
        release=settings.release,
        generated_at=generated_at,
        operations=snapshot_payload(snapshot),
        host_readiness=readiness_payload(readiness),
        metrics=snapshot_metrics(snapshot) + readiness_metrics(readiness),
        passphrase=payload.passphrase.get_secret_value(),
    )
    async with database.session() as session:
        await AuditRepository(session).record(
            action="DIAGNOSTIC_BUNDLE_EXPORTED",
            object_type="DiagnosticBundle",
            actor_user_id=principal.user_id,
            request_id=_request_uuid(),
            outcome="SUCCESS",
            payload={
                "format": "cooperative-clearing-diagnostic-encrypted-v1",
                "bytes": len(artifact.payload),
                "sha256": hashlib.sha256(artifact.payload).hexdigest(),
            },
        )
        await session.commit()
    return Response(
        content=artifact.payload,
        media_type=artifact.content_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/metrics", response_class=Response)
async def metrics(
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> Response:
    _authorize(principal)
    snapshot = await GetOperationalSnapshot(database).execute()
    readiness = await GetHostReadiness(database, settings).execute()
    body = (
        request_metrics.render_prometheus(
            release=settings.release,
            node_code=settings.node_code,
        )
        + snapshot_metrics(snapshot)
        + readiness_metrics(readiness)
    )
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")
