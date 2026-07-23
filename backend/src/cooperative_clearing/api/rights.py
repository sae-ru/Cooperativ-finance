"""Role-scoped API for backed commodity rights."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.identity_schemas import CommandEnvelope
from cooperative_clearing.api.identity_schemas import CommandResult as ApiCommandResult
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, require_role
from cooperative_clearing.modules.identity.infrastructure.models import Member
from cooperative_clearing.modules.inventory.application.common import InventoryCommandResult
from cooperative_clearing.modules.inventory.infrastructure.models import InventoryLot
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.modules.rights.application.service import (
    FREEZE_ROLES,
    ISSUE_ROLES,
    REDEMPTION_ROLES,
    CommodityRightsService,
)
from cooperative_clearing.modules.rights.infrastructure.models import (
    CommodityRight,
    InventoryReservation,
    LotBalance,
    RightRedemption,
    RightTransfer,
)
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/rights", tags=["commodity-rights"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]

RIGHTS_READ_ROLES = (
    ISSUE_ROLES
    | FREEZE_ROLES
    | REDEMPTION_ROLES
    | {
        RoleCode.SECURITY_ADMIN,
    }
)
GLOBAL_RIGHTS_READ_ROLES = {RoleCode.AUDITOR, RoleCode.SECURITY_ADMIN}


class IssueRightRequest(BaseModel):
    lot_id: UUID
    owner_member_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    redeem_warehouse_id: UUID
    valid_until: datetime | None = None
    expected_balance_version: int = Field(ge=1)


class TransferRightRequest(BaseModel):
    from_member_id: UUID
    to_member_id: UUID
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=1)


class FreezeRightRequest(BaseModel):
    reason_code: str = Field(min_length=2, max_length=100)
    decision_reference: str = Field(min_length=2, max_length=500)
    expected_version: int = Field(ge=1)


class UnfreezeRightRequest(BaseModel):
    decision_reference: str = Field(min_length=2, max_length=500)
    expected_version: int = Field(ge=1)


class RequestRedemptionRequest(BaseModel):
    owner_member_id: UUID
    expected_version: int = Field(ge=1)


class CompleteRedemptionRequest(BaseModel):
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_right_version: int = Field(ge=1)


class LotBalanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    lot_id: UUID
    verified_quantity: Decimal
    available_quantity: Decimal
    reserved_quantity: Decimal
    rights_issued_quantity: Decimal
    redeemed_quantity: Decimal
    quarantined_quantity: Decimal
    backing_shortfall_quantity: Decimal
    version: int
    updated_at: datetime


class CommodityRightResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cooperative_id: UUID
    lot_id: UUID
    owner_member_id: UUID
    original_owner_member_id: UUID
    quantity: Decimal
    unit_id: UUID
    status: str
    redeem_warehouse_id: UUID
    valid_until: datetime | None
    reservation_id: UUID
    issued_by_member_id: UUID
    issued_role_assignment_id: UUID
    issued_event_id: UUID
    frozen_previous_status: str | None
    freeze_reason: str | None
    frozen_event_id: UUID | None
    redeemed_event_id: UUID | None
    created_at: datetime
    updated_at: datetime
    version: int


class ReservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    lot_id: UUID
    purpose_type: str
    purpose_id: UUID
    quantity: Decimal
    status: str
    expires_at: datetime | None
    created_event_id: UUID
    completed_event_id: UUID | None
    created_at: datetime


class TransferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    right_id: UUID
    from_member_id: UUID
    to_member_id: UUID
    quantity: Decimal
    performed_by_user_id: UUID
    event_id: UUID
    created_at: datetime


class RedemptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    right_id: UUID
    lot_id: UUID
    owner_member_id: UUID
    warehouse_id: UUID
    custodian_assignment_id: UUID
    quantity: Decimal
    status: str
    requested_by_user_id: UUID
    fulfilled_by_user_id: UUID | None
    requested_event_id: UUID
    completed_event_id: UUID | None
    requested_at: datetime
    completed_at: datetime | None


class HistoryEvent(BaseModel):
    event_id: UUID
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    local_sequence: int
    occurred_at: datetime
    event_hash: str
    payload: dict[str, object]


class Collection[T](BaseModel):
    data: list[T]
    request_id: str


class RightProofResponse(BaseModel):
    proof_hash: str
    right: CommodityRightResponse
    balance: LotBalanceResponse
    lot_number: str
    lot_status: str
    current_quantity: Decimal | None
    original_owner_name: str
    current_owner_name: str
    reservation: ReservationResponse
    transfers: list[TransferResponse]
    redemption: RedemptionResponse | None
    signed_events: list[HistoryEvent]
    generated_at: datetime


def _command(result: InventoryCommandResult) -> CommandEnvelope:
    return CommandEnvelope(
        data=ApiCommandResult(
            event_id=result.event_id, object_id=result.object_id, replayed=result.replayed
        ),
        request_id=get_request_id(),
    )


def _request_uuid() -> UUID | None:
    try:
        return UUID(get_request_id())
    except ValueError:
        return None


def _scoped_cooperatives(principal: Principal) -> set[UUID] | None:
    require_role(principal, RIGHTS_READ_ROLES)
    if any(
        grant.role in GLOBAL_RIGHTS_READ_ROLES and grant.cooperative_id is None
        for grant in principal.roles
    ):
        return None
    scoped = {
        grant.cooperative_id
        for grant in principal.roles
        if grant.role in RIGHTS_READ_ROLES and grant.cooperative_id is not None
    }
    if not scoped:
        raise DomainError(
            code="AUTHORIZATION_DENIED",
            message_key="errors.auth.authorization_denied",
            status_code=403,
        )
    return scoped


async def _commit_command(database: DatabaseDependency, action: object) -> CommandEnvelope:
    async with database.session() as session:
        try:
            result = await action(session)  # type: ignore[operator]
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise DomainError(
                code="RIGHTS_CONFLICT",
                message_key="errors.rights.conflict",
                status_code=409,
            ) from exc
    return _command(result)


@router.get("/balances", response_model=Collection[LotBalanceResponse])
async def list_balances(
    principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[LotBalanceResponse]:
    scoped = _scoped_cooperatives(principal)
    statement = (
        select(LotBalance)
        .join(InventoryLot, InventoryLot.id == LotBalance.lot_id)
        .order_by(LotBalance.updated_at.desc(), LotBalance.lot_id)
    )
    if scoped is not None:
        statement = statement.where(InventoryLot.cooperative_id.in_(scoped))
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("", response_model=Collection[CommodityRightResponse])
async def list_rights(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=32),
    owner_member_id: UUID | None = None,
) -> Collection[CommodityRightResponse]:
    scoped = _scoped_cooperatives(principal)
    statement = select(CommodityRight).order_by(CommodityRight.created_at.desc(), CommodityRight.id)
    if scoped is not None:
        statement = statement.where(CommodityRight.cooperative_id.in_(scoped))
    if status is not None:
        statement = statement.where(CommodityRight.status == status.upper())
    if owner_member_id is not None:
        statement = statement.where(CommodityRight.owner_member_id == owner_member_id)
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/redemptions", response_model=Collection[RedemptionResponse])
async def list_redemptions(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[RedemptionResponse]:
    scoped = _scoped_cooperatives(principal)
    statement = (
        select(RightRedemption)
        .join(CommodityRight, CommodityRight.id == RightRedemption.right_id)
        .order_by(RightRedemption.requested_at.desc(), RightRedemption.id)
    )
    if scoped is not None:
        statement = statement.where(CommodityRight.cooperative_id.in_(scoped))
    if status is not None:
        statement = statement.where(RightRedemption.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.post("", response_model=CommandEnvelope, status_code=201)
async def issue_right(
    payload: IssueRightRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, ISSUE_ROLES)

    async def action(session: object) -> InventoryCommandResult:
        return await CommodityRightsService(settings).issue(
            session,  # type: ignore[arg-type]
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/{right_id}/transfer", response_model=CommandEnvelope)
async def transfer_right(
    right_id: UUID,
    payload: TransferRightRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, ISSUE_ROLES)

    async def action(session: object) -> InventoryCommandResult:
        return await CommodityRightsService(settings).transfer(
            session,  # type: ignore[arg-type]
            principal=principal,
            right_id=right_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/{right_id}/freeze", response_model=CommandEnvelope)
async def freeze_right(
    right_id: UUID,
    payload: FreezeRightRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, FREEZE_ROLES)

    async def action(session: object) -> InventoryCommandResult:
        return await CommodityRightsService(settings).freeze(
            session,  # type: ignore[arg-type]
            principal=principal,
            right_id=right_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/{right_id}/unfreeze", response_model=CommandEnvelope)
async def unfreeze_right(
    right_id: UUID,
    payload: UnfreezeRightRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, FREEZE_ROLES)

    async def action(session: object) -> InventoryCommandResult:
        return await CommodityRightsService(settings).unfreeze(
            session,  # type: ignore[arg-type]
            principal=principal,
            right_id=right_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/{right_id}/redemptions", response_model=CommandEnvelope, status_code=201)
async def request_redemption(
    right_id: UUID,
    payload: RequestRedemptionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, ISSUE_ROLES)

    async def action(session: object) -> InventoryCommandResult:
        return await CommodityRightsService(settings).request_redemption(
            session,  # type: ignore[arg-type]
            principal=principal,
            right_id=right_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/redemptions/{redemption_id}/complete", response_model=CommandEnvelope)
async def complete_redemption(
    redemption_id: UUID,
    payload: CompleteRedemptionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, REDEMPTION_ROLES)

    async def action(session: object) -> InventoryCommandResult:
        return await CommodityRightsService(settings).complete_redemption(
            session,  # type: ignore[arg-type]
            principal=principal,
            redemption_id=redemption_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.get("/{right_id}/proof", response_model=RightProofResponse)
async def right_proof(
    right_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> RightProofResponse:
    scoped = _scoped_cooperatives(principal)
    async with database.session() as session:
        right = await session.get(CommodityRight, right_id)
        if right is None or (scoped is not None and right.cooperative_id not in scoped):
            raise DomainError(
                code="RIGHT_NOT_FOUND",
                message_key="errors.rights.right_not_found",
                status_code=404,
            )
        lot = await session.get(InventoryLot, right.lot_id)
        balance = await session.get(LotBalance, right.lot_id)
        reservation = await session.get(InventoryReservation, right.reservation_id)
        original_owner = await session.get(Member, right.original_owner_member_id)
        current_owner = await session.get(Member, right.owner_member_id)
        if (
            lot is None
            or balance is None
            or reservation is None
            or original_owner is None
            or current_owner is None
        ):
            raise DomainError(
                code="RIGHT_PROOF_INCOMPLETE",
                message_key="errors.rights.right_proof_incomplete",
                status_code=409,
            )
        transfers = list(
            (
                await session.execute(
                    select(RightTransfer)
                    .where(RightTransfer.right_id == right.id)
                    .order_by(RightTransfer.created_at, RightTransfer.id)
                )
            ).scalars()
        )
        redemption = (
            await session.execute(
                select(RightRedemption)
                .where(RightRedemption.right_id == right.id)
                .order_by(RightRedemption.requested_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        events = list(
            (
                await session.execute(
                    select(SignedEvent)
                    .where(
                        or_(
                            (SignedEvent.aggregate_type == "inventory_lot")
                            & (SignedEvent.aggregate_id == right.lot_id),
                            (SignedEvent.aggregate_type == "lot_balance")
                            & (SignedEvent.aggregate_id == right.lot_id),
                            (SignedEvent.aggregate_type == "commodity_right")
                            & (SignedEvent.aggregate_id == right.id),
                        )
                    )
                    .order_by(SignedEvent.local_sequence)
                )
            ).scalars()
        )
    history = [
        HistoryEvent(
            event_id=item.event_id,
            event_type=item.event_type,
            aggregate_type=item.aggregate_type,
            aggregate_id=item.aggregate_id,
            aggregate_version=item.aggregate_version,
            local_sequence=item.local_sequence,
            occurred_at=item.occurred_at,
            event_hash=item.event_hash,
            payload=item.payload,
        )
        for item in events
    ]
    proof_payload = {
        "right_id": str(right.id),
        "lot_id": str(right.lot_id),
        "reservation_id": str(right.reservation_id),
        "current_owner_member_id": str(right.owner_member_id),
        "status": right.status,
        "quantity": str(right.quantity),
        "event_hashes": [item.event_hash for item in events],
    }
    proof_hash = hashlib.sha256(
        json.dumps(proof_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return RightProofResponse(
        proof_hash=proof_hash,
        right=CommodityRightResponse.model_validate(right),
        balance=LotBalanceResponse.model_validate(balance),
        lot_number=lot.lot_number,
        lot_status=lot.status,
        current_quantity=lot.current_quantity,
        original_owner_name=original_owner.display_name,
        current_owner_name=current_owner.display_name,
        reservation=ReservationResponse.model_validate(reservation),
        transfers=[TransferResponse.model_validate(item) for item in transfers],
        redemption=(
            RedemptionResponse.model_validate(redemption) if redemption is not None else None
        ),
        signed_events=history,
        generated_at=datetime.now(UTC),
    )
