"""Typed errors crossing the domain-to-API boundary."""

from collections.abc import Mapping
from typing import Any


class DomainError(Exception):
    """Expected business failure with a stable machine-readable code."""

    def __init__(
        self,
        *,
        code: str,
        message_key: str,
        parameters: Mapping[str, Any] | None = None,
        field_errors: tuple[Mapping[str, str], ...] = (),
        retryable: bool = False,
        status_code: int = 422,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.message_key = message_key
        self.parameters = dict(parameters or {})
        self.field_errors = field_errors
        self.retryable = retryable
        self.status_code = status_code


class ServiceNotReadyError(DomainError):
    def __init__(self, code: str = "SERVICE_NOT_READY") -> None:
        super().__init__(
            code=code,
            message_key="errors.system.service_not_ready",
            retryable=True,
            status_code=503,
        )
