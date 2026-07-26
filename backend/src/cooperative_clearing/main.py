"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from cooperative_clearing import __version__
from cooperative_clearing.api.admin import router as admin_router
from cooperative_clearing.api.antifraud import router as antifraud_router
from cooperative_clearing.api.auth import router as auth_router
from cooperative_clearing.api.clearing import router as clearing_router
from cooperative_clearing.api.crisis import router as crisis_router
from cooperative_clearing.api.discovery import router as discovery_router
from cooperative_clearing.api.errors import register_error_handlers
from cooperative_clearing.api.exchange import router as exchange_router
from cooperative_clearing.api.federation import router as federation_router
from cooperative_clearing.api.health import router as health_router
from cooperative_clearing.api.inter_node_clearing import (
    router as inter_node_clearing_router,
)
from cooperative_clearing.api.inventory import router as inventory_router
from cooperative_clearing.api.middleware import RequestContextMiddleware
from cooperative_clearing.api.operations import router as operations_router
from cooperative_clearing.api.participant import router as participant_router
from cooperative_clearing.api.peer import router as peer_router
from cooperative_clearing.api.responsibility import journal_router
from cooperative_clearing.api.responsibility import router as responsibility_router
from cooperative_clearing.api.responsibility_candidates import (
    router as responsibility_candidates_router,
)
from cooperative_clearing.api.rights import router as rights_router
from cooperative_clearing.api.risk import router as risk_router
from cooperative_clearing.api.solidarity import router as solidarity_router
from cooperative_clearing.api.system import router as system_router
from cooperative_clearing.api.trust import router as trust_router
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.logging import configure_logging
from cooperative_clearing.shared.infrastructure.database import Database


def create_app(settings: Settings | None = None, *, manage_runtime: bool = True) -> FastAPI:
    resolved_settings = settings or Settings()
    configure_logging(
        level=resolved_settings.log_level,
        service=resolved_settings.service_name,
        release=resolved_settings.release,
        node_id=resolved_settings.node_code,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not manage_runtime:
            yield
            return
        database = Database.from_settings(resolved_settings)
        app.state.database = database
        try:
            yield
        finally:
            await database.dispose()

    app = FastAPI(
        title="Cooperative Clearing API",
        version=__version__,
        docs_url="/api/docs" if resolved_settings.environment.value in {"dev", "test"} else None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=resolved_settings.allowed_hosts)
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(antifraud_router)
    app.include_router(clearing_router)
    app.include_router(crisis_router)
    app.include_router(discovery_router)
    app.include_router(exchange_router)
    app.include_router(federation_router)
    app.include_router(inter_node_clearing_router)
    app.include_router(inventory_router)
    app.include_router(operations_router)
    app.include_router(peer_router)
    app.include_router(participant_router)
    app.include_router(rights_router)
    app.include_router(responsibility_router)
    app.include_router(responsibility_candidates_router)
    app.include_router(risk_router)
    app.include_router(solidarity_router)
    app.include_router(journal_router)
    app.include_router(system_router)
    app.include_router(trust_router)
    return app


app = create_app()
