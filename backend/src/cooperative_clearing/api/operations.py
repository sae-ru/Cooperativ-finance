"""Protected local operational diagnostics and metrics."""

from datetime import datetime

from fastapi import APIRouter, Response
from pydantic import BaseModel

from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, require_role
from cooperative_clearing.modules.operations.application.status import (
    GetOperationalSnapshot,
    OperationalSnapshot,
    snapshot_metrics,
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
    active_crisis_mandates: int
    issued_crisis_forms: int


class OperationalSnapshotEnvelope(BaseModel):
    data: OperationalSnapshotResponse
    request_id: str


def _authorize(principal: Principal) -> None:
    require_role(principal, OPERATIONS_ROLES)


def _response(snapshot: OperationalSnapshot) -> OperationalSnapshotResponse:
    return OperationalSnapshotResponse.model_validate(snapshot, from_attributes=True)


@router.get("/snapshot", response_model=OperationalSnapshotEnvelope)
async def operational_snapshot(
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> OperationalSnapshotEnvelope:
    _authorize(principal)
    snapshot = await GetOperationalSnapshot(database).execute()
    return OperationalSnapshotEnvelope(data=_response(snapshot), request_id=get_request_id())


@router.get("/metrics", response_class=Response)
async def metrics(
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> Response:
    _authorize(principal)
    snapshot = await GetOperationalSnapshot(database).execute()
    body = request_metrics.render_prometheus(
        release=settings.release,
        node_code=settings.node_code,
    ) + snapshot_metrics(snapshot)
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")
