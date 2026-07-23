"""Liveness and readiness endpoints."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cooperative_clearing.api.dependencies import DatabaseDependency, SettingsDependency
from cooperative_clearing.api.schemas import ComponentCheckResponse, HealthResponse
from cooperative_clearing.modules.node.application.readiness import ReadinessProbe
from cooperative_clearing.shared.core.request_context import get_request_id

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def live(settings: SettingsDependency) -> HealthResponse:
    return HealthResponse(
        status="LIVE",
        release=settings.release,
        request_id=get_request_id(),
    )


@router.get("/ready", response_model=HealthResponse)
async def ready(
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> JSONResponse:
    checks = await ReadinessProbe(database=database, settings=settings).run()
    is_ready = all(check.status == "UP" for check in checks)
    response = HealthResponse(
        status="READY" if is_ready else "NOT_READY",
        release=settings.release,
        request_id=get_request_id(),
        checks=[
            ComponentCheckResponse(name=item.name, status=item.status, code=item.code)
            for item in checks
        ],
    )
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content=response.model_dump(mode="json"),
    )
