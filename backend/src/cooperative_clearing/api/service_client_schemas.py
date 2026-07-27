"""API contracts for scoped service clients and machine authentication."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from cooperative_clearing.modules.identity.domain.types import (
    ServiceClientRequestOperation,
    ServiceClientRequestStatus,
    ServiceClientStatus,
    ServiceScope,
)


class ServiceClientConfigRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=200)
    technical_contact_name: str = Field(min_length=2, max_length=200)
    technical_contact_email: str = Field(min_length=5, max_length=254)
    scopes: list[ServiceScope] = Field(min_length=1, max_length=8)
    network_allowlist: list[str] = Field(min_length=1, max_length=32)
    rate_limit_per_minute: int = Field(ge=1, le=6000)
    expires_at: datetime


class ServiceClientChangeRequest(BaseModel):
    owner_cooperative_id: UUID
    operation: ServiceClientRequestOperation
    service_client_id: UUID | None = None
    config: ServiceClientConfigRequest | None = None
    expected_client_version: int | None = Field(default=None, ge=1)
    reason_code: str = Field(min_length=2, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")


class ServiceClientDecisionRequest(BaseModel):
    approve: bool
    reason_code: str = Field(min_length=2, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    expected_version: int = Field(ge=1)


class ServiceClientProtectiveRequest(BaseModel):
    reason_code: str = Field(min_length=2, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    expected_version: int = Field(ge=1)


class ServiceClientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    client_code: str
    owner_cooperative_id: UUID
    display_name: str
    technical_contact_name: str
    technical_contact_email: str
    scopes: list[ServiceScope]
    network_allowlist: list[str]
    rate_limit_per_minute: int
    status: ServiceClientStatus
    effective_status: Literal["ACTIVE", "SUSPENDED", "REVOKED", "EXPIRED"]
    expires_at: datetime
    registered_by_user_id: UUID
    approved_by_user_id: UUID
    created_at: datetime
    updated_at: datetime
    suspended_at: datetime | None
    revoked_at: datetime | None
    version: int


class ServiceClientRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    service_client_id: UUID | None
    owner_cooperative_id: UUID
    operation: ServiceClientRequestOperation
    proposed_config: dict[str, object] | None
    expected_client_version: int | None
    reason_code: str
    status: ServiceClientRequestStatus
    requested_by_user_id: UUID
    decided_by_user_id: UUID | None
    decision_reason_code: str | None
    issued_credential_id: UUID | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    version: int


class ServiceClientCollection(BaseModel):
    data: list[ServiceClientResponse]
    request_id: str


class ServiceClientRequestCollection(BaseModel):
    data: list[ServiceClientRequestResponse]
    request_id: str


class ServiceClientCommandResponse(BaseModel):
    event_id: UUID
    object_id: UUID
    replayed: bool = False


class ServiceClientCommandEnvelope(BaseModel):
    data: ServiceClientCommandResponse
    request_id: str


class ServiceClientDecisionResponse(ServiceClientCommandResponse):
    service_client_id: UUID | None
    client_code: str | None
    credential_secret: str | None
    credential_expires_at: datetime | None


class ServiceClientDecisionEnvelope(BaseModel):
    data: ServiceClientDecisionResponse
    request_id: str


class ServiceTokenRequest(BaseModel):
    client_id: str = Field(min_length=5, max_length=63)
    client_secret: SecretStr


class ServiceTokenResponse(BaseModel):
    token_type: Literal["Bearer"] = "Bearer"
    access_token: str
    access_expires_at: datetime
    service_client_id: UUID
    client_id: str
    owner_cooperative_id: UUID
    scopes: list[ServiceScope]


class ServiceTokenEnvelope(BaseModel):
    data: ServiceTokenResponse
    request_id: str


class ServiceContextResponse(BaseModel):
    service_client_id: UUID
    client_id: str
    owner_cooperative_id: UUID
    scopes: list[ServiceScope]
    source_ip: str


class ServiceContextEnvelope(BaseModel):
    data: ServiceContextResponse
    request_id: str
