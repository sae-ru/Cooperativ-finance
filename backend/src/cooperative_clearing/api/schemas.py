"""Shared response envelopes for the public API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FieldError(BaseModel):
    field: str
    code: str


class ErrorDetail(BaseModel):
    code: str
    message_key: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    field_errors: list[FieldError] = Field(default_factory=list)
    retryable: bool = False


class ErrorEnvelope(BaseModel):
    error: ErrorDetail
    request_id: str


class ComponentCheckResponse(BaseModel):
    name: str
    status: Literal["UP", "DOWN"]
    code: str


class HealthResponse(BaseModel):
    status: Literal["LIVE", "READY", "NOT_READY"]
    release: str
    request_id: str
    checks: list[ComponentCheckResponse] = Field(default_factory=list)


class NodeSummary(BaseModel):
    id: str
    code: str
    display_name: str
    environment: str
    demo_data_loaded: bool


class ReleaseSummary(BaseModel):
    version: str
    schema_revision: str


class WorkerSummary(BaseModel):
    status: Literal["STARTING", "RUNNING", "STALE"]
    last_seen_at: datetime | None


class NoticeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    severity: Literal["INFO", "WARNING", "CRITICAL"]
    message_key: str
    parameters: dict[str, Any]
    created_at: datetime


class SystemStatusData(BaseModel):
    status: Literal["OPERATIONAL", "DEGRADED"]
    node: NodeSummary
    release: ReleaseSummary
    checks: list[ComponentCheckResponse]
    worker: WorkerSummary
    notices: list[NoticeResponse]


class SystemStatusEnvelope(BaseModel):
    data: SystemStatusData
    request_id: str
