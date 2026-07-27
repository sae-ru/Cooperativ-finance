"""API contracts for emergency physical custody continuity."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cooperative_clearing.modules.inventory.domain.types import (
    CustodyContinuityItemStatus,
    CustodyContinuityStatus,
)


class CustodyContinuityCreateRequest(BaseModel):
    member_continuity_case_id: UUID
    source_assignment_id: UUID
    expected_source_assignment_version: int = Field(ge=1)
    target_role_assignment_id: UUID
    handover_place: str = Field(min_length=1, max_length=500)
    temporary_valid_until: datetime
    evidence_refs: list[str] = Field(min_length=1, max_length=10)


class CustodyContinuityAttestRequest(BaseModel):
    actual_quantity: Decimal
    condition_notes: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_case_version: int = Field(ge=1)
    expected_item_version: int = Field(ge=1)


class CustodyContinuityDecisionRequest(BaseModel):
    approve: bool
    expected_version: int = Field(ge=1)
    reason_code: str = Field(min_length=2, max_length=100)


class CustodyContinuityCandidateDecisionRequest(BaseModel):
    accept: bool
    expected_version: int = Field(ge=1)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)
    reason_code: str = Field(min_length=2, max_length=100)


class CustodyContinuityItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lot_id: UUID
    lot_number: str
    product_name: str
    unit_symbol: str
    lot_version: int
    expected_quantity: Decimal
    actual_quantity: Decimal | None
    status: CustodyContinuityItemStatus
    condition_notes: str | None
    evidence_ids: list[str]
    attested_by_user_id: UUID | None
    attested_at: datetime | None
    version: int


class CustodyContinuityCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    member_continuity_case_id: UUID
    source_member_id: UUID
    source_member_name: str
    warehouse_id: UUID
    warehouse_name: str
    source_assignment_id: UUID
    source_assignment_version: int
    target_member_id: UUID
    target_member_name: str
    target_role_assignment_id: UUID
    target_assignment_id: UUID | None
    handover_place: str
    temporary_valid_until: datetime
    evidence_refs: list[str]
    blocked_reasons: list[str]
    status: CustodyContinuityStatus
    requested_by_user_id: UUID
    decided_by_user_id: UUID | None
    accepted_by_user_id: UUID | None
    decision_reason_code: str | None
    created_at: datetime
    inventory_completed_at: datetime | None
    decided_at: datetime | None
    accepted_at: datetime | None
    updated_at: datetime
    version: int
    items: list[CustodyContinuityItemResponse]


class CustodyContinuitySourceResponse(BaseModel):
    member_continuity_case_id: UUID
    cooperative_id: UUID
    source_assignment_id: UUID
    source_assignment_version: int
    source_member_id: UUID
    source_member_name: str
    warehouse_id: UUID
    warehouse_name: str
    lot_count: int


class CustodyContinuityCandidateResponse(BaseModel):
    role_assignment_id: UUID
    user_id: UUID
    member_id: UUID
    display_name: str


class CustodyContinuityCaseCollection(BaseModel):
    data: list[CustodyContinuityCaseResponse]
    request_id: str


class CustodyContinuitySourceCollection(BaseModel):
    data: list[CustodyContinuitySourceResponse]
    request_id: str


class CustodyContinuityCandidateCollection(BaseModel):
    data: list[CustodyContinuityCandidateResponse]
    request_id: str


class CustodyContinuityCommandResponse(BaseModel):
    event_id: UUID
    object_id: UUID
    status: CustodyContinuityStatus
    replayed: bool


class CustodyContinuityCommandEnvelope(BaseModel):
    data: CustodyContinuityCommandResponse
    request_id: str
