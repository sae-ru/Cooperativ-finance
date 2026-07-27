"""API contracts for contained member exit and succession review."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cooperative_clearing.modules.identity.domain.types import (
    MemberContinuityCaseStatus,
    MemberContinuityCaseType,
    MemberStatus,
)


class MemberContinuityCreateRequest(BaseModel):
    cooperative_id: UUID
    member_id: UUID
    case_type: MemberContinuityCaseType
    expected_member_version: int = Field(ge=1)
    evidence_refs: list[str] = Field(min_length=1, max_length=10)
    reason_code: str = Field(min_length=2, max_length=100)


class MemberContinuityDecisionRequest(BaseModel):
    approve: bool
    expected_version: int = Field(ge=1)
    reason_code: str = Field(min_length=2, max_length=100)


class MemberContinuityCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    member_id: UUID
    case_type: MemberContinuityCaseType
    previous_member_status: MemberStatus
    contained_member_version: int
    reference_summary: dict[str, object]
    review_blockers: list[str]
    evidence_refs: list[str]
    reason_code: str
    status: MemberContinuityCaseStatus
    requested_by_user_id: UUID
    decided_by_user_id: UUID | None
    decision_reason_code: str | None
    disabled_user_count: int = 0
    suspended_membership_count: int = 0
    created_at: datetime
    decided_at: datetime | None
    updated_at: datetime
    version: int


class MemberContinuityCaseCollection(BaseModel):
    data: list[MemberContinuityCaseResponse]
    request_id: str


class MemberContinuityCommandResponse(BaseModel):
    event_id: UUID
    object_id: UUID
    status: MemberContinuityCaseStatus
    replayed: bool


class MemberContinuityCommandEnvelope(BaseModel):
    data: MemberContinuityCommandResponse
    request_id: str
