"""API contracts for controlled duplicate-member merge cases."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cooperative_clearing.modules.identity.domain.types import MemberMergeCaseStatus


class MemberMergeCreateRequest(BaseModel):
    cooperative_id: UUID
    source_member_id: UUID
    survivor_member_id: UUID
    source_expected_version: int = Field(ge=1)
    survivor_expected_version: int = Field(ge=1)
    evidence_refs: list[str] = Field(min_length=1, max_length=10)
    reason_code: str = Field(min_length=2, max_length=100)


class MemberMergeDecisionRequest(BaseModel):
    approve: bool
    expected_version: int = Field(ge=1)
    reason_code: str = Field(min_length=2, max_length=100)


class MemberMergeCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    source_member_id: UUID
    survivor_member_id: UUID
    source_expected_version: int
    survivor_expected_version: int
    evidence_refs: list[str]
    reason_code: str
    blocker_summary: dict[str, object]
    status: MemberMergeCaseStatus
    requested_by_user_id: UUID
    decided_by_user_id: UUID | None
    decision_reason_code: str | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    updated_at: datetime
    version: int


class MemberMergeCaseCollection(BaseModel):
    data: list[MemberMergeCaseResponse]
    request_id: str


class MemberMergeCommandResponse(BaseModel):
    event_id: UUID
    object_id: UUID
    status: MemberMergeCaseStatus
    replayed: bool


class MemberMergeCommandEnvelope(BaseModel):
    data: MemberMergeCommandResponse
    request_id: str
