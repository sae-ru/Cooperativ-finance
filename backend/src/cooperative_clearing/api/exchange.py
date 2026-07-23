"""Participant-scoped API for local deals, obligations, and logistics."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ColumnElement, false, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.identity_schemas import CommandEnvelope
from cooperative_clearing.api.identity_schemas import CommandResult as ApiCommandResult
from cooperative_clearing.modules.exchange.application.common import ExchangeCommandResult
from cooperative_clearing.modules.exchange.application.service import (
    DEAL_OPERATOR_ROLES,
    DISPUTE_RESOLVER_ROLES,
    LOGISTICS_ROLES,
    OVERDUE_ROLES,
    ExchangeService,
    ObligationDraft,
)
from cooperative_clearing.modules.exchange.infrastructure.models import (
    AcceptanceRecord,
    Deal,
    DealConfirmation,
    DealParty,
    DealTermsVersion,
    Fulfillment,
    LogisticsOrder,
    Obligation,
    ObligationDispute,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, require_role
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/exchange", tags=["exchange"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]

READ_ADMIN_ROLES = (
    DEAL_OPERATOR_ROLES
    | LOGISTICS_ROLES
    | OVERDUE_ROLES
    | {
        RoleCode.SECURITY_ADMIN,
    }
)
GLOBAL_READ_ROLES = {RoleCode.AUDITOR, RoleCode.SECURITY_ADMIN}


class ObligationDraftRequest(BaseModel):
    debtor_member_id: UUID
    creditor_member_id: UUID
    subject_type: str = Field(min_length=2, max_length=32)
    subject_id: UUID | None = None
    description: str = Field(min_length=2, max_length=4000)
    quality_criteria: str = Field(min_length=2, max_length=4000)
    fulfillment_place: str = Field(min_length=2, max_length=500)
    due_at: datetime
    unit_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    partial_allowed: bool = True
    evidence_required: bool = True
    confirmation_method: str = Field(min_length=2, max_length=200)
    substitute_policy: str = Field(min_length=2, max_length=4000)
    valuation_source: str = Field(min_length=2, max_length=300)
    liquidity_class: str = Field(default="UNASSESSED", min_length=1, max_length=16)
    clearing_allowed: bool = False


class DealProposeRequest(BaseModel):
    cooperative_id: UUID
    title: str = Field(min_length=2, max_length=200)
    obligations: list[ObligationDraftRequest] = Field(min_length=1, max_length=20)


class DealReviseRequest(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    obligations: list[ObligationDraftRequest] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=1)


class DealConfirmRequest(BaseModel):
    terms_version: int = Field(ge=1)
    terms_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_version: int = Field(ge=1)


class FulfillmentSubmitRequest(BaseModel):
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    quality_claim: str = Field(min_length=2, max_length=2000)
    location_text: str = Field(min_length=2, max_length=500)
    performed_at: datetime
    logistics_order_id: UUID | None = None
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)
    expected_version: int = Field(ge=1)


class FulfillmentAcceptRequest(BaseModel):
    accepted_quantity: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    quality_status: str = Field(min_length=2, max_length=200)
    notes: str = Field(min_length=2, max_length=2000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_fulfillment_version: int = Field(ge=1)
    expected_obligation_version: int = Field(ge=1)


class DisputeOpenRequest(BaseModel):
    fulfillment_id: UUID | None = None
    reason_code: str = Field(min_length=2, max_length=80)
    statement: str = Field(min_length=2, max_length=4000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=1)


class DisputeResolveRequest(BaseModel):
    resolution_action: Literal[
        "REJECT_CLAIM",
        "CONTINUE_PERFORMANCE",
        "DEFAULT_OBLIGATION",
        "CLOSE_OBLIGATION",
    ]
    resolution_notes: str = Field(min_length=2, max_length=4000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=1)


class OverdueScanRequest(BaseModel):
    cooperative_id: UUID
    as_of: datetime


class LogisticsOrderCreateRequest(BaseModel):
    carrier_member_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    origin_text: str = Field(min_length=2, max_length=500)
    destination_text: str = Field(min_length=2, max_length=500)
    pickup_due_at: datetime
    delivery_due_at: datetime
    expected_obligation_version: int = Field(ge=1)


class LogisticsTransitionRequest(BaseModel):
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)
    expected_version: int = Field(ge=1)


class DealResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cooperative_id: UUID
    title: str
    status: str
    terms_version: int
    terms_hash: str
    proposed_by_member_id: UUID
    proposed_event_id: UUID
    confirmed_event_id: UUID | None
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class DealPartyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    deal_id: UUID
    terms_version: int
    terms_hash: str
    member_id: UUID
    created_event_id: UUID
    created_at: datetime


class DealConfirmationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    deal_id: UUID
    terms_version: int
    terms_hash: str
    member_id: UUID
    role_assignment_id: UUID
    event_id: UUID
    confirmed_at: datetime


class ObligationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    deal_id: UUID
    cooperative_id: UUID
    sequence_no: int
    terms_version: int
    debtor_member_id: UUID
    creditor_member_id: UUID
    subject_type: str
    subject_id: UUID | None
    description: str
    quality_criteria: str
    fulfillment_place: str
    due_at: datetime
    unit_id: UUID
    quantity_total: Decimal
    quantity_submitted: Decimal
    quantity_fulfilled: Decimal
    quantity_cleared: Decimal
    clearing_allowed: bool
    partial_allowed: bool
    evidence_required: bool
    confirmation_method: str
    substitute_policy: str
    valuation_source: str
    liquidity_class: str
    status: str
    created_event_id: UUID
    last_event_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int


class FulfillmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    obligation_id: UUID
    logistics_order_id: UUID | None
    quantity: Decimal
    accepted_quantity: Decimal
    quality_claim: str
    location_text: str
    performed_at: datetime
    status: str
    performed_by_member_id: UUID
    submitted_event_id: UUID
    accepted_event_id: UUID | None
    created_at: datetime
    updated_at: datetime
    version: int


class AcceptanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    fulfillment_id: UUID
    accepted_quantity: Decimal
    decision: str
    quality_status: str
    notes: str
    accepted_by_member_id: UUID
    event_id: UUID
    created_at: datetime


class LogisticsOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    obligation_id: UUID
    cooperative_id: UUID
    carrier_member_id: UUID
    quantity: Decimal
    unit_id: UUID
    origin_text: str
    destination_text: str
    pickup_due_at: datetime
    delivery_due_at: datetime
    status: str
    carrier_user_id: UUID | None
    offered_event_id: UUID
    accepted_event_id: UUID | None
    pickup_event_id: UUID | None
    delivered_event_id: UUID | None
    accepted_at: datetime | None
    picked_up_at: datetime | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class DisputeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    obligation_id: UUID
    fulfillment_id: UUID | None
    reason_code: str
    statement: str
    status: str
    previous_obligation_status: str
    previous_fulfillment_status: str | None
    opened_by_member_id: UUID
    event_id: UUID
    resolution_action: str | None
    resolution_notes: str | None
    resolved_by_member_id: UUID | None
    resolution_event_id: UUID | None
    created_at: datetime
    resolved_at: datetime | None
    version: int


class DealDetailResponse(BaseModel):
    deal: DealResponse
    terms: dict[str, object]
    parties: list[DealPartyResponse]
    confirmations: list[DealConfirmationResponse]
    obligations: list[ObligationResponse]


class Collection[T](BaseModel):
    data: list[T]
    request_id: str


CommandAction = Callable[[AsyncSession], Awaitable[ExchangeCommandResult]]


def _request_uuid() -> UUID | None:
    try:
        return UUID(get_request_id())
    except ValueError:
        return None


def _require_readable(principal: Principal) -> None:
    if principal.must_change_password:
        raise DomainError(
            code="PASSWORD_CHANGE_REQUIRED",
            message_key="errors.auth.password_change_required",
            status_code=403,
        )


def _admin_scopes(principal: Principal) -> set[UUID] | None:
    _require_readable(principal)
    if any(
        grant.role in GLOBAL_READ_ROLES and grant.cooperative_id is None
        for grant in principal.roles
    ):
        return None
    return {
        grant.cooperative_id
        for grant in principal.roles
        if grant.role in READ_ADMIN_ROLES and grant.cooperative_id is not None
    }


def _deal_filter(principal: Principal) -> ColumnElement[bool] | None:
    scopes = _admin_scopes(principal)
    if scopes is None:
        return None
    conditions: list[ColumnElement[bool]] = []
    if scopes:
        conditions.append(Deal.cooperative_id.in_(scopes))
    if principal.member_id is not None:
        member_id = principal.member_id
        current_party_deals = (
            select(DealParty.deal_id)
            .where(
                DealParty.member_id == member_id,
                DealParty.terms_version == Deal.terms_version,
            )
            .correlate(Deal)
        )
        carrier_deals = (
            select(Obligation.deal_id)
            .join(LogisticsOrder, LogisticsOrder.obligation_id == Obligation.id)
            .where(LogisticsOrder.carrier_member_id == member_id)
        )
        conditions.extend(
            [
                Deal.proposed_by_member_id == member_id,
                Deal.id.in_(current_party_deals),
                Deal.id.in_(carrier_deals),
            ]
        )
    return or_(*conditions) if conditions else false()


def _obligation_filter(principal: Principal) -> ColumnElement[bool] | None:
    scopes = _admin_scopes(principal)
    if scopes is None:
        return None
    conditions: list[ColumnElement[bool]] = []
    if scopes:
        conditions.append(Obligation.cooperative_id.in_(scopes))
    if principal.member_id is not None:
        member_id = principal.member_id
        carrier_obligations = select(LogisticsOrder.obligation_id).where(
            LogisticsOrder.carrier_member_id == member_id
        )
        conditions.extend(
            [
                Obligation.debtor_member_id == member_id,
                Obligation.creditor_member_id == member_id,
                Obligation.id.in_(carrier_obligations),
            ]
        )
    return or_(*conditions) if conditions else false()


def _command(result: ExchangeCommandResult) -> CommandEnvelope:
    return CommandEnvelope(
        data=ApiCommandResult(
            event_id=result.event_id,
            object_id=result.object_id,
            replayed=result.replayed,
        ),
        request_id=get_request_id(),
    )


async def _commit_command(database: DatabaseDependency, action: CommandAction) -> CommandEnvelope:
    async with database.session() as session:
        try:
            result = await action(session)
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise DomainError(
                code="EXCHANGE_CONFLICT",
                message_key="errors.exchange.conflict",
                status_code=409,
            ) from exc
    return _command(result)


@router.get("/deals", response_model=Collection[DealResponse])
async def list_deals(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=32),
) -> Collection[DealResponse]:
    condition = _deal_filter(principal)
    statement = select(Deal).order_by(Deal.updated_at.desc(), Deal.id)
    if condition is not None:
        statement = statement.where(condition)
    if status is not None:
        statement = statement.where(Deal.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/deals/{deal_id}", response_model=DealDetailResponse)
async def deal_detail(
    deal_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> DealDetailResponse:
    condition = _deal_filter(principal)
    statement = select(Deal).where(Deal.id == deal_id)
    if condition is not None:
        statement = statement.where(condition)
    async with database.session() as session:
        deal = (await session.execute(statement)).scalar_one_or_none()
        if deal is None:
            raise DomainError(
                code="DEAL_NOT_FOUND",
                message_key="errors.exchange.deal_not_found",
                status_code=404,
            )
        terms = (
            await session.execute(
                select(DealTermsVersion).where(
                    DealTermsVersion.deal_id == deal.id,
                    DealTermsVersion.terms_version == deal.terms_version,
                )
            )
        ).scalar_one()
        parties = list(
            (
                await session.execute(
                    select(DealParty)
                    .where(
                        DealParty.deal_id == deal.id,
                        DealParty.terms_version == deal.terms_version,
                    )
                    .order_by(DealParty.member_id)
                )
            ).scalars()
        )
        confirmations = list(
            (
                await session.execute(
                    select(DealConfirmation)
                    .where(
                        DealConfirmation.deal_id == deal.id,
                        DealConfirmation.terms_version == deal.terms_version,
                    )
                    .order_by(DealConfirmation.confirmed_at, DealConfirmation.id)
                )
            ).scalars()
        )
        obligations = list(
            (
                await session.execute(
                    select(Obligation)
                    .where(Obligation.deal_id == deal.id)
                    .order_by(Obligation.sequence_no)
                )
            ).scalars()
        )
    return DealDetailResponse(
        deal=DealResponse.model_validate(deal),
        terms=terms.terms_payload,
        parties=[DealPartyResponse.model_validate(item) for item in parties],
        confirmations=[DealConfirmationResponse.model_validate(item) for item in confirmations],
        obligations=[ObligationResponse.model_validate(item) for item in obligations],
    )


@router.get("/obligations", response_model=Collection[ObligationResponse])
async def list_obligations(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=32),
) -> Collection[ObligationResponse]:
    condition = _obligation_filter(principal)
    statement = select(Obligation).order_by(Obligation.due_at, Obligation.id)
    if condition is not None:
        statement = statement.where(condition)
    if status is not None:
        statement = statement.where(Obligation.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get(
    "/obligations/{obligation_id}/fulfillments",
    response_model=Collection[FulfillmentResponse],
)
async def list_fulfillments(
    obligation_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> Collection[FulfillmentResponse]:
    condition = _obligation_filter(principal)
    visible = select(Obligation.id).where(Obligation.id == obligation_id)
    if condition is not None:
        visible = visible.where(condition)
    async with database.session() as session:
        if (await session.execute(visible)).scalar_one_or_none() is None:
            raise DomainError(
                code="OBLIGATION_NOT_FOUND",
                message_key="errors.exchange.obligation_not_found",
                status_code=404,
            )
        items = list(
            (
                await session.execute(
                    select(Fulfillment)
                    .where(Fulfillment.obligation_id == obligation_id)
                    .order_by(Fulfillment.created_at, Fulfillment.id)
                )
            ).scalars()
        )
    return Collection(data=items, request_id=get_request_id())


@router.get("/acceptances", response_model=Collection[AcceptanceResponse])
async def list_acceptances(
    principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[AcceptanceResponse]:
    condition = _obligation_filter(principal)
    statement = (
        select(AcceptanceRecord)
        .join(Fulfillment, Fulfillment.id == AcceptanceRecord.fulfillment_id)
        .join(Obligation, Obligation.id == Fulfillment.obligation_id)
        .order_by(AcceptanceRecord.created_at.desc(), AcceptanceRecord.id)
    )
    if condition is not None:
        statement = statement.where(condition)
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/logistics-orders", response_model=Collection[LogisticsOrderResponse])
async def list_logistics_orders(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=24),
) -> Collection[LogisticsOrderResponse]:
    condition = _obligation_filter(principal)
    statement = (
        select(LogisticsOrder)
        .join(Obligation, Obligation.id == LogisticsOrder.obligation_id)
        .order_by(LogisticsOrder.delivery_due_at, LogisticsOrder.id)
    )
    if condition is not None:
        statement = statement.where(condition)
    if status is not None:
        statement = statement.where(LogisticsOrder.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/disputes", response_model=Collection[DisputeResponse])
async def list_disputes(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=16),
) -> Collection[DisputeResponse]:
    condition = _obligation_filter(principal)
    statement = (
        select(ObligationDispute)
        .join(Obligation, Obligation.id == ObligationDispute.obligation_id)
        .order_by(ObligationDispute.created_at.desc(), ObligationDispute.id)
    )
    if condition is not None:
        statement = statement.where(condition)
    if status is not None:
        statement = statement.where(ObligationDispute.status == status.upper())
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.post("/deals", response_model=CommandEnvelope, status_code=201)
async def propose_deal(
    payload: DealProposeRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, DEAL_OPERATOR_ROLES, payload.cooperative_id)
    drafts = [ObligationDraft(**item.model_dump()) for item in payload.obligations]

    async def action(session: AsyncSession) -> ExchangeCommandResult:
        return await ExchangeService(settings).propose_deal(
            session,
            principal=principal,
            cooperative_id=payload.cooperative_id,
            title=payload.title,
            obligations=drafts,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.put("/deals/{deal_id}/terms", response_model=CommandEnvelope)
async def revise_deal(
    deal_id: UUID,
    payload: DealReviseRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, DEAL_OPERATOR_ROLES)
    drafts = [ObligationDraft(**item.model_dump()) for item in payload.obligations]

    async def action(session: AsyncSession) -> ExchangeCommandResult:
        return await ExchangeService(settings).revise_deal(
            session,
            principal=principal,
            deal_id=deal_id,
            title=payload.title,
            obligations=drafts,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/deals/{deal_id}/confirmations", response_model=CommandEnvelope, status_code=201)
async def confirm_deal(
    deal_id: UUID,
    payload: DealConfirmRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    _require_readable(principal)

    async def action(session: AsyncSession) -> ExchangeCommandResult:
        return await ExchangeService(settings).confirm_deal(
            session,
            principal=principal,
            deal_id=deal_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/obligations/{obligation_id}/fulfillments",
    response_model=CommandEnvelope,
    status_code=201,
)
async def submit_fulfillment(
    obligation_id: UUID,
    payload: FulfillmentSubmitRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    _require_readable(principal)

    async def action(session: AsyncSession) -> ExchangeCommandResult:
        return await ExchangeService(settings).submit_fulfillment(
            session,
            principal=principal,
            obligation_id=obligation_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/fulfillments/{fulfillment_id}/acceptance",
    response_model=CommandEnvelope,
    status_code=201,
)
async def accept_fulfillment(
    fulfillment_id: UUID,
    payload: FulfillmentAcceptRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    _require_readable(principal)

    async def action(session: AsyncSession) -> ExchangeCommandResult:
        return await ExchangeService(settings).accept_fulfillment(
            session,
            principal=principal,
            fulfillment_id=fulfillment_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/obligations/{obligation_id}/disputes",
    response_model=CommandEnvelope,
    status_code=201,
)
async def open_dispute(
    obligation_id: UUID,
    payload: DisputeOpenRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    _require_readable(principal)

    async def action(session: AsyncSession) -> ExchangeCommandResult:
        return await ExchangeService(settings).open_dispute(
            session,
            principal=principal,
            obligation_id=obligation_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/disputes/{dispute_id}/resolution",
    response_model=CommandEnvelope,
    status_code=201,
)
async def resolve_dispute(
    dispute_id: UUID,
    payload: DisputeResolveRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, DISPUTE_RESOLVER_ROLES)

    async def action(session: AsyncSession) -> ExchangeCommandResult:
        return await ExchangeService(settings).resolve_dispute(
            session,
            principal=principal,
            dispute_id=dispute_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/overdue-scans", response_model=CommandEnvelope, status_code=201)
async def mark_overdue(
    payload: OverdueScanRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, OVERDUE_ROLES, payload.cooperative_id)

    async def action(session: AsyncSession) -> ExchangeCommandResult:
        return await ExchangeService(settings).mark_overdue(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/obligations/{obligation_id}/logistics-orders",
    response_model=CommandEnvelope,
    status_code=201,
)
async def create_logistics_order(
    obligation_id: UUID,
    payload: LogisticsOrderCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, DEAL_OPERATOR_ROLES)

    async def action(session: AsyncSession) -> ExchangeCommandResult:
        return await ExchangeService(settings).create_logistics_order(
            session,
            principal=principal,
            obligation_id=obligation_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post(
    "/logistics-orders/{order_id}/{action}",
    response_model=CommandEnvelope,
)
async def transition_logistics_order(
    order_id: UUID,
    action: Literal["accept", "pickup", "deliver"],
    payload: LogisticsTransitionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, LOGISTICS_ROLES)

    async def command(session: AsyncSession) -> ExchangeCommandResult:
        return await ExchangeService(settings).transition_logistics_order(
            session,
            principal=principal,
            order_id=order_id,
            action=action,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, command)
