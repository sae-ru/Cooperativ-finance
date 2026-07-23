"""Identity and administration API contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from cooperative_clearing.modules.identity.domain.types import (
    AssignmentStatus,
    CooperativeStatus,
    MembershipStatus,
    MemberStatus,
    RoleCode,
    UserStatus,
)


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=120)
    password: SecretStr


class ChangePasswordRequest(BaseModel):
    current_password: SecretStr
    new_password: SecretStr


class RoleGrantResponse(BaseModel):
    assignment_id: UUID
    role: RoleCode
    cooperative_id: UUID | None


class PrincipalResponse(BaseModel):
    user_id: UUID
    login: str
    member_id: UUID | None
    must_change_password: bool
    roles: list[RoleGrantResponse]


class SessionResponse(BaseModel):
    token_type: Literal["Bearer"] = "Bearer"
    access_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    principal: PrincipalResponse


class PrincipalEnvelope(BaseModel):
    data: PrincipalResponse
    request_id: str


class SessionEnvelope(BaseModel):
    data: SessionResponse
    request_id: str


class CommandResult(BaseModel):
    event_id: UUID
    object_id: UUID
    replayed: bool = False


class CommandEnvelope(BaseModel):
    data: CommandResult
    request_id: str


class CooperativeCreateRequest(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    name: str = Field(min_length=2, max_length=200)


class CooperativeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    status: CooperativeStatus
    created_at: datetime
    version: int


class MemberCreateRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=200)
    identifier_type: str | None = Field(default=None, max_length=40)
    identifier_value: SecretStr | None = None


class MemberTransitionRequest(BaseModel):
    target_status: MemberStatus
    reason_code: str = Field(min_length=2, max_length=100)
    expected_version: int = Field(ge=1)


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    status: MemberStatus
    created_at: datetime
    updated_at: datetime
    version: int


class MembershipCreateRequest(BaseModel):
    cooperative_id: UUID
    member_id: UUID
    member_number: str = Field(min_length=1, max_length=63)


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    member_id: UUID
    member_number: str
    status: MembershipStatus
    joined_at: datetime | None
    version: int


class UserCreateRequest(BaseModel):
    login: str = Field(min_length=1, max_length=120)
    temporary_password: SecretStr
    member_id: UUID | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    login: str
    member_id: UUID | None
    status: UserStatus
    must_change_password: bool
    locked_until: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    version: int


class RoleAssignmentRequest(BaseModel):
    user_id: UUID
    role: RoleCode
    cooperative_id: UUID | None = None


class RoleApprovalRequest(BaseModel):
    approve: bool
    reason_code: str = Field(min_length=2, max_length=100)


class RoleAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    role_code: RoleCode
    cooperative_id: UUID | None
    status: AssignmentStatus
    granted_by_user_id: UUID | None
    approved_by_user_id: UUID | None
    created_at: datetime
    version: int


class SessionAdminResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    status: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    created_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None


class AuditEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    occurred_at: datetime
    actor_user_id: UUID | None
    action: str
    object_type: str
    object_id: UUID | None
    cooperative_id: UUID | None
    request_id: UUID | None
    outcome: str
    reason_code: str | None
    payload: dict[str, object]


class AdminOverviewResponse(BaseModel):
    members: int
    active_members: int
    cooperatives: int
    users: int
    active_sessions: int
    pending_role_approvals: int


class ListEnvelope(BaseModel):
    data: list[dict[str, object]]
    request_id: str


class OverviewEnvelope(BaseModel):
    data: AdminOverviewResponse
    request_id: str
