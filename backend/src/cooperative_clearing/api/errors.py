"""Central mapping from typed failures to safe API envelopes."""

import logging
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from cooperative_clearing.api.schemas import ErrorDetail, ErrorEnvelope, FieldError
from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.domain.types import Principal
from cooperative_clearing.shared.core.logging import event_fields
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database

logger = logging.getLogger(__name__)


def _response(detail: ErrorDetail, status_code: int) -> JSONResponse:
    envelope = ErrorEnvelope(error=detail, request_id=get_request_id())
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


async def _record_authorization_denial(request: Request, exc: DomainError) -> None:
    principal = getattr(request.state, "principal", None)
    database = getattr(request.app.state, "database", None)
    if not isinstance(principal, Principal) or not isinstance(database, Database):
        return
    try:
        request_id = UUID(get_request_id())
    except ValueError:
        request_id = None
    route = request.scope.get("route")
    route_path = str(getattr(route, "path", request.url.path))
    try:
        async with database.session() as session:
            await AuditRepository(session).record(
                action="AUTHORIZATION_DECISION",
                object_type="ApiRoute",
                actor_user_id=principal.user_id,
                outcome="DENIED",
                reason_code=exc.code,
                request_id=request_id,
                payload={"route": route_path, "method": request.method},
            )
            await session.commit()
    except Exception:
        logger.exception(
            "authorization_audit_failed",
            extra=event_fields(result_code="AUDIT_WRITE_FAILED"),
        )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        if exc.status_code == 403:
            await _record_authorization_denial(request, exc)
        return _response(
            ErrorDetail(
                code=exc.code,
                message_key=exc.message_key,
                parameters=exc.parameters,
                field_errors=[FieldError(**item) for item in exc.field_errors],
                retryable=exc.retryable,
            ),
            exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            FieldError(
                field=".".join(str(part) for part in error["loc"] if part != "body"),
                code=str(error["type"]),
            )
            for error in exc.errors()
        ]
        return _response(
            ErrorDetail(
                code="REQUEST_VALIDATION_FAILED",
                message_key="errors.request.validation_failed",
                field_errors=fields,
            ),
            422,
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        code_by_status = {
            401: "AUTHENTICATION_REQUIRED",
            403: "AUTHORIZATION_DENIED",
            404: "HTTP_NOT_FOUND",
            405: "HTTP_METHOD_NOT_ALLOWED",
            429: "RATE_LIMIT_EXCEEDED",
        }
        code = code_by_status.get(exc.status_code, "HTTP_REQUEST_REJECTED")
        return _response(
            ErrorDetail(code=code, message_key=f"errors.http.{code.lower()}"),
            exc.status_code,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unexpected_request_failure",
            extra=event_fields(result_code="INTERNAL_ERROR"),
        )
        return _response(
            ErrorDetail(
                code="INTERNAL_ERROR",
                message_key="errors.system.internal_error",
                retryable=False,
            ),
            500,
        )
