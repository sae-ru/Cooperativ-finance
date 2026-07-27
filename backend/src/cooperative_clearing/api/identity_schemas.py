"""Identity and administration API contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from cooperative_clearing.modules.identity.domain.types import (
    AssignmentStatus,
    CooperativeStatus,
    MemberImportRowStatus,
    MemberImportStatus,
    MembershipStatus,
    MemberStatus,
    RoleCode,
    RoleGrantSource,
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
    source: RoleGrantSource = RoleGrantSource.ASSIGNMENT
    expires_at: datetime | None = None


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


class CooperativeTransitionRequest(BaseModel):
    target_status: CooperativeStatus
    reason_code: str = Field(min_length=2, max_length=100)
    expected_version: int = Field(ge=1)


class CooperativeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    status: CooperativeStatus
    created_at: datetime
    updated_at: datetime
    version: int


class MemberCreateRequest(BaseModel):
    cooperative_id: UUID | None = None
    display_name: str = Field(min_length=2, max_length=200)
    identifier_type: str | None = Field(default=None, max_length=40)
    identifier_value: SecretStr | None = None
    duplicate_resolution_code: str | None = Field(
        default=None, min_length=2, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$"
    )


class MemberDuplicateCheckRequest(BaseModel):
    cooperative_id: UUID
    display_name: str = Field(min_length=2, max_length=200)
    identifier_type: str | None = Field(default=None, max_length=40)
    identifier_value: SecretStr | None = None


class MemberDuplicateCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    member_id: UUID
    display_name: str
    registered_by_cooperative_id: UUID | None
    merged_into_member_id: UUID | None
    status: MemberStatus
    match_basis: Literal["EXACT_IDENTIFIER", "NORMALIZED_NAME"]


class MemberDuplicateCheckResponse(BaseModel):
    candidates: list[MemberDuplicateCandidateResponse]
    exact_identifier_match: bool
    normalized_name_match: bool


class MemberDuplicateCheckEnvelope(BaseModel):
    data: MemberDuplicateCheckResponse
    request_id: str


class MemberImportCreateRequest(BaseModel):
    cooperative_id: UUID
    source_name: str = Field(min_length=1, max_length=200)
    csv_text: SecretStr

    @field_validator("csv_text")
    @classmethod
    def csv_text_size(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) > 1_000_000:
            raise ValueError("CSV payload exceeds one megabyte")
        return value


class MemberImportCommandRequest(BaseModel):
    expected_version: int = Field(ge=1)


class MemberImportDecisionRequest(MemberImportCommandRequest):
    approve: bool
    reason_code: str = Field(min_length=2, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")


class MemberImportBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    source_name: str
    source_sha256: str
    status: MemberImportStatus
    row_count: int
    ready_count: int
    invalid_count: int
    duplicate_count: int
    applied_count: int
    created_by_user_id: UUID
    reviewed_by_user_id: UUID | None
    decision_reason_code: str | None
    created_at: datetime
    previewed_at: datetime | None
    reviewed_at: datetime | None
    applied_at: datetime | None
    updated_at: datetime
    version: int


class MemberImportRowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    batch_id: UUID
    row_number: int
    display_name: str
    identifier_type: str | None
    status: MemberImportRowStatus
    error_code: str | None
    match_basis: str | None
    candidate_member_id: UUID | None
    created_member_id: UUID | None
    created_at: datetime
    applied_at: datetime | None


class MemberTransitionRequest(BaseModel):
    target_status: MemberStatus
    reason_code: str = Field(min_length=2, max_length=100)
    expected_version: int = Field(ge=1)


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    registered_by_cooperative_id: UUID | None
    merged_into_member_id: UUID | None
    status: MemberStatus
    created_at: datetime
    updated_at: datetime
    version: int


class MembershipCreateRequest(BaseModel):
    cooperative_id: UUID
    member_id: UUID
    member_number: str = Field(min_length=1, max_length=63)


class MembershipTransitionRequest(BaseModel):
    target_status: MembershipStatus
    reason_code: str = Field(min_length=2, max_length=100)
    expected_version: int = Field(ge=1)


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    member_id: UUID
    member_number: str
    status: MembershipStatus
    joined_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class UserCreateRequest(BaseModel):
    login: str = Field(min_length=1, max_length=120)
    temporary_password: SecretStr
    member_id: UUID | None = None


class UserTransitionRequest(BaseModel):
    target_status: UserStatus
    reason_code: str = Field(min_length=2, max_length=100)
    expected_version: int = Field(ge=1)


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


class TotpEnrollmentRequest(BaseModel):
    current_password: SecretStr
    current_totp_code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")


class TotpEnrollmentResponse(BaseModel):
    factor_id: UUID
    secret: str
    provisioning_uri: str
    expires_at: datetime


class TotpConfirmationRequest(BaseModel):
    code: str = Field(pattern=r"^[0-9]{6}$")


class TotpDisableRequest(BaseModel):
    current_password: SecretStr
    code: str = Field(pattern=r"^[0-9]{6}$")
    reason_code: str = Field(min_length=2, max_length=100)


class StepUpResponse(BaseModel):
    method: Literal["TOTP"] = "TOTP"
    verified_at: datetime
    expires_at: datetime


class SecurityStateResponse(BaseModel):
    totp_enabled: bool
    totp_confirmed_at: datetime | None
    enrollment_pending: bool
    enrollment_expires_at: datetime | None
    step_up_active: bool
    step_up_method: str | None
    step_up_expires_at: datetime | None
    break_glass_grants: int


class SecurityStateEnvelope(BaseModel):
    data: SecurityStateResponse
    request_id: str


class TotpEnrollmentEnvelope(BaseModel):
    data: TotpEnrollmentResponse
    request_id: str


class StepUpEnvelope(BaseModel):
    data: StepUpResponse
    request_id: str


class AccountRecoveryCreateRequest(BaseModel):
    target_user_id: UUID
    temporary_password: SecretStr
    reason_code: str = Field(min_length=2, max_length=100)
    evidence_id: str = Field(min_length=2, max_length=200)


class SecurityDecisionRequest(BaseModel):
    approve: bool
    reason_code: str = Field(min_length=2, max_length=100)


class AccountRecoveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    target_user_id: UUID
    requested_by_user_id: UUID
    decided_by_user_id: UUID | None
    reason_code: str
    evidence_id: str
    status: str
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    version: int


class AccountRecoveryCollection(BaseModel):
    data: list[AccountRecoveryResponse]
    request_id: str


class BreakGlassCreateRequest(BaseModel):
    target_user_id: UUID
    role: RoleCode
    cooperative_id: UUID | None = None
    duration_minutes: int = Field(ge=15, le=240)
    reason_code: str = Field(min_length=2, max_length=100)
    evidence_id: str = Field(min_length=2, max_length=200)


class BreakGlassResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    target_user_id: UUID
    role_code: RoleCode
    cooperative_id: UUID | None
    requested_by_user_id: UUID
    approved_by_user_id: UUID | None
    revoked_by_user_id: UUID | None
    reason_code: str
    evidence_id: str
    requested_duration_minutes: int
    status: str
    created_at: datetime
    approved_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    version: int


class BreakGlassCollection(BaseModel):
    data: list[BreakGlassResponse]
    request_id: str
