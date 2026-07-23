"""Request identity and redacted completion logging."""

import logging
import time
from uuid import UUID, uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from cooperative_clearing.shared.core.logging import event_fields
from cooperative_clearing.shared.core.metrics import request_metrics
from cooperative_clearing.shared.core.request_context import reset_request_id, set_request_id

logger = logging.getLogger(__name__)


def normalize_request_id(raw_value: str | None) -> str:
    if raw_value:
        try:
            return str(UUID(raw_value))
        except ValueError:
            pass
    return str(uuid4())


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = normalize_request_id(request.headers.get("X-Request-ID"))
        token = set_request_id(request_id)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            duration_seconds = time.perf_counter() - started
            request_metrics.observe(
                method=request.method,
                route=route_path,
                status_code=status_code,
                duration_seconds=duration_seconds,
            )
            logger.info(
                "request_completed",
                extra=event_fields(
                    route=route_path,
                    method=request.method,
                    status_code=status_code,
                    duration_ms=round(duration_seconds * 1000, 3),
                    result_code="OK" if status_code < 400 else "ERROR",
                ),
            )
            reset_request_id(token)
