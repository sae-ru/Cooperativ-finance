"""Safe operator-facing status endpoints."""

from fastapi import APIRouter

from cooperative_clearing.api.dependencies import DatabaseDependency, SettingsDependency
from cooperative_clearing.api.schemas import (
    ComponentCheckResponse,
    NodeSummary,
    NoticeResponse,
    ReleaseSummary,
    SystemStatusData,
    SystemStatusEnvelope,
    WorkerSummary,
)
from cooperative_clearing.modules.node.application.status import GetSystemStatus
from cooperative_clearing.shared.core.request_context import get_request_id

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/status", response_model=SystemStatusEnvelope)
async def get_system_status(
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> SystemStatusEnvelope:
    view = await GetSystemStatus(database=database, settings=settings).execute()
    return SystemStatusEnvelope(
        data=SystemStatusData(
            status=view.status,
            node=NodeSummary(
                id=view.node_id,
                code=view.node_code,
                display_name=view.display_name,
                environment=view.environment,
                demo_data_loaded=view.demo_data_loaded,
            ),
            release=ReleaseSummary(
                version=view.release,
                schema_revision=view.schema_revision,
            ),
            checks=[
                ComponentCheckResponse(name=item.name, status=item.status, code=item.code)
                for item in view.checks
            ],
            worker=WorkerSummary(
                status=view.worker_status,
                last_seen_at=view.worker_last_seen_at,
            ),
            notices=[
                NoticeResponse(
                    code=item.code,
                    severity=item.severity,
                    message_key=item.message_key,
                    parameters=item.parameters,
                    created_at=item.created_at,
                )
                for item in view.notices
            ],
        ),
        request_id=get_request_id(),
    )
