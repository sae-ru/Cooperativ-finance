"""Plain-language member dashboard assembled from authoritative read models."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from cooperative_clearing.api.auth import _request_uuid
from cooperative_clearing.api.dependencies import DatabaseDependency, PrincipalDependency
from cooperative_clearing.api.identity_schemas import CommandEnvelope, CommandResult
from cooperative_clearing.modules.exchange.infrastructure.models import Deal, Obligation
from cooperative_clearing.modules.federation.infrastructure.discovery_models import (
    FederatedOffer,
    PurchaseIntent,
)
from cooperative_clearing.modules.identity.application.address_book import (
    AddressCommandResult,
    AddressValues,
    ParticipantAddressBookService,
)
from cooperative_clearing.modules.identity.domain.types import Principal
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    Membership,
    ParticipantAddress,
    UserAccount,
)
from cooperative_clearing.modules.inventory.infrastructure.models import UnitOfMeasure
from cooperative_clearing.modules.risk.infrastructure.models import (
    ExposureCommitment,
    RiskPolicy,
    ShareAccount,
    ShareContribution,
)
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/participant", tags=["participant"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]


class ParticipantEnvelope(BaseModel):
    data: dict[str, object]
    request_id: str


class ParticipantAddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    label: str
    purpose: Literal["PICKUP", "DELIVERY", "BOTH"]
    region_code: str
    address_text: str
    contact_name: str
    contact_phone: str
    instructions: str | None
    is_default_pickup: bool
    is_default_delivery: bool
    status: Literal["ACTIVE", "ARCHIVED"]
    created_at: datetime
    updated_at: datetime
    version: int


class ParticipantAddressCollection(BaseModel):
    data: list[ParticipantAddressResponse]
    request_id: str


class ParticipantAddressWriteRequest(BaseModel):
    cooperative_id: UUID
    label: str = Field(min_length=2, max_length=80)
    purpose: Literal["PICKUP", "DELIVERY", "BOTH"]
    region_code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,62}$")
    address_text: str = Field(min_length=5, max_length=500)
    contact_name: str = Field(min_length=2, max_length=200)
    contact_phone: str = Field(min_length=5, max_length=80)
    instructions: str | None = Field(default=None, max_length=1000)
    is_default_pickup: bool = False
    is_default_delivery: bool = False

    def values(self) -> AddressValues:
        return AddressValues(**self.model_dump(exclude={"expected_version"}))


class ParticipantAddressUpdateRequest(ParticipantAddressWriteRequest):
    expected_version: int = Field(ge=1)


class ParticipantAddressArchiveRequest(BaseModel):
    expected_version: int = Field(ge=1)


def _address_command(result: AddressCommandResult) -> CommandEnvelope:
    return CommandEnvelope(
        data=CommandResult(
            event_id=result.event_id,
            object_id=result.object_id,
            replayed=result.replayed,
        ),
        request_id=get_request_id(),
    )


def _address_conflict() -> DomainError:
    return DomainError(
        code="PARTICIPANT_ADDRESS_LABEL_CONFLICT",
        message_key="errors.identity.participant_address_label_conflict",
        status_code=409,
    )


def _require_member(principal: Principal) -> UUID:
    if principal.must_change_password:
        raise DomainError(
            code="PASSWORD_CHANGE_REQUIRED",
            message_key="errors.auth.password_change_required",
            status_code=403,
        )
    if principal.member_id is None:
        raise DomainError(
            code="PERSONAL_ACTOR_REQUIRED",
            message_key="errors.auth.authorization_denied",
            status_code=403,
        )
    return principal.member_id


def _decimal(value: Decimal) -> str:
    return format(value, "f")


@router.get("/addresses", response_model=ParticipantAddressCollection)
async def list_participant_addresses(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    include_archived: bool = Query(default=False),
) -> ParticipantAddressCollection:
    member_id = _require_member(principal)
    statement = (
        select(ParticipantAddress)
        .where(ParticipantAddress.member_id == member_id)
        .order_by(
            ParticipantAddress.status,
            ParticipantAddress.is_default_pickup.desc(),
            ParticipantAddress.is_default_delivery.desc(),
            ParticipantAddress.label,
        )
    )
    if not include_archived:
        statement = statement.where(ParticipantAddress.status == "ACTIVE")
    async with database.session() as session:
        items = list((await session.execute(statement)).scalars())
    return ParticipantAddressCollection(data=items, request_id=get_request_id())


@router.post("/addresses", response_model=CommandEnvelope, status_code=201)
async def create_participant_address(
    payload: ParticipantAddressWriteRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    member_id = _require_member(principal)
    async with database.session() as session:
        try:
            result = await ParticipantAddressBookService().create(
                session,
                principal=principal,
                member_id=member_id,
                values=payload.values(),
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _address_conflict() from exc
    return _address_command(result)


@router.put("/addresses/{address_id}", response_model=CommandEnvelope)
async def update_participant_address(
    address_id: UUID,
    payload: ParticipantAddressUpdateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    member_id = _require_member(principal)
    async with database.session() as session:
        try:
            result = await ParticipantAddressBookService().update(
                session,
                principal=principal,
                member_id=member_id,
                address_id=address_id,
                expected_version=payload.expected_version,
                values=payload.values(),
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _address_conflict() from exc
    return _address_command(result)


@router.post("/addresses/{address_id}/archive", response_model=CommandEnvelope)
async def archive_participant_address(
    address_id: UUID,
    payload: ParticipantAddressArchiveRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    member_id = _require_member(principal)
    async with database.session() as session:
        result = await ParticipantAddressBookService().archive(
            session,
            principal=principal,
            member_id=member_id,
            address_id=address_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )
        await session.commit()
    return _address_command(result)


@router.get("/dashboard", response_model=ParticipantEnvelope)
async def participant_dashboard(
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> ParticipantEnvelope:
    member_id = _require_member(principal)
    async with database.session() as session:
        member = await session.get(Member, member_id)
        user = await session.get(UserAccount, principal.user_id)
        if member is None or user is None or user.member_id != member.id:
            raise DomainError(
                code="MEMBER_PROFILE_NOT_FOUND",
                message_key="errors.auth.authorization_denied",
                status_code=404,
            )
        membership_rows = (
            await session.execute(
                select(Membership, Cooperative)
                .join(Cooperative, Cooperative.id == Membership.cooperative_id)
                .where(Membership.member_id == member_id)
                .order_by(Membership.status, Cooperative.name)
            )
        ).all()
        cooperative_ids = [membership.cooperative_id for membership, _ in membership_rows]

        accounts = list(
            (
                await session.execute(
                    select(ShareAccount)
                    .where(ShareAccount.member_id == member_id)
                    .order_by(ShareAccount.contour, ShareAccount.denomination)
                )
            ).scalars()
        )
        account_ids = [account.id for account in accounts]
        reserved_by_account: dict[UUID, Decimal] = {}
        if account_ids:
            reserved_by_account = {
                account_id: Decimal(amount)
                for account_id, amount in (
                    await session.execute(
                        select(
                            ExposureCommitment.account_id,
                            func.coalesce(func.sum(ExposureCommitment.amount_reserved), 0),
                        )
                        .where(
                            ExposureCommitment.account_id.in_(account_ids),
                            ExposureCommitment.status.in_(("PROPOSED", "ACTIVE")),
                        )
                        .group_by(ExposureCommitment.account_id)
                    )
                ).all()
            }
        contributions = (
            list(
                (
                    await session.execute(
                        select(ShareContribution)
                        .where(ShareContribution.account_id.in_(account_ids))
                        .order_by(ShareContribution.created_at.desc())
                    )
                ).scalars()
            )
            if account_ids
            else []
        )
        policies = (
            {
                policy.id: policy
                for policy in (
                    await session.execute(
                        select(RiskPolicy).where(
                            RiskPolicy.id.in_({account.opening_policy_id for account in accounts})
                        )
                    )
                ).scalars()
            }
            if accounts
            else {}
        )

        own_offers = list(
            (
                await session.execute(
                    select(FederatedOffer)
                    .where(FederatedOffer.publisher_member_id == member_id)
                    .order_by(FederatedOffer.created_at.desc(), FederatedOffer.id)
                    .limit(100)
                )
            ).scalars()
        )
        purchases = list(
            (
                await session.execute(
                    select(PurchaseIntent, FederatedOffer)
                    .join(FederatedOffer, FederatedOffer.id == PurchaseIntent.offer_record_id)
                    .where(PurchaseIntent.buyer_member_id == member_id)
                    .order_by(PurchaseIntent.created_at.desc())
                    .limit(100)
                )
            ).all()
        )
        sales = list(
            (
                await session.execute(
                    select(PurchaseIntent, FederatedOffer)
                    .join(FederatedOffer, FederatedOffer.id == PurchaseIntent.offer_record_id)
                    .where(FederatedOffer.publisher_member_id == member_id)
                    .order_by(PurchaseIntent.created_at.desc())
                    .limit(100)
                )
            ).all()
        )
        obligation_rows = list(
            (
                await session.execute(
                    select(Obligation, UnitOfMeasure, Deal)
                    .join(UnitOfMeasure, UnitOfMeasure.id == Obligation.unit_id)
                    .join(Deal, Deal.id == Obligation.deal_id)
                    .where(
                        or_(
                            Obligation.debtor_member_id == member_id,
                            Obligation.creditor_member_id == member_id,
                        )
                    )
                    .order_by(Obligation.due_at, Obligation.id)
                    .limit(200)
                )
            ).all()
        )
        commitments = list(
            (
                await session.execute(
                    select(ExposureCommitment)
                    .where(
                        ExposureCommitment.owner_member_id == member_id,
                        ExposureCommitment.status.in_(("PROPOSED", "ACTIVE")),
                    )
                    .order_by(ExposureCommitment.expires_at)
                )
            ).scalars()
        )

    contribution_sources: dict[UUID, list[dict[str, object]]] = {}
    for item in contributions:
        contribution_sources.setdefault(item.account_id, []).append(
            {
                "amount": _decimal(item.amount),
                "source_reference": item.source_reference,
                "event_id": str(item.event_id),
                "created_at": item.created_at,
            }
        )

    account_views: list[dict[str, object]] = []
    total_available = Decimal(0)
    total_balance = Decimal(0)
    total_reserved = Decimal(0)
    total_protected = Decimal(0)
    denominations = {account.denomination for account in accounts}
    for account in accounts:
        reserved = reserved_by_account.get(account.id, Decimal(0))
        available = (
            account.balance - account.protected_amount - reserved - account.executed_not_settled
        )
        policy = policies.get(account.opening_policy_id)
        account_views.append(
            {
                "id": str(account.id),
                "cooperative_id": str(account.cooperative_id),
                "contour": account.contour,
                "denomination": account.denomination,
                "balance": _decimal(account.balance),
                "available": _decimal(available),
                "protected": _decimal(account.protected_amount),
                "reserved": _decimal(reserved),
                "executed_not_settled": _decimal(account.executed_not_settled),
                "status": account.status,
                "policy": (
                    {
                        "id": str(policy.id),
                        "version": policy.policy_version,
                        "terms_hash": policy.terms_hash,
                        "approval_event_id": str(policy.approved_event_id)
                        if policy.approved_event_id
                        else None,
                        "approved_at": policy.approved_at,
                        "max_member_exposure": _decimal(policy.max_member_exposure),
                    }
                    if policy is not None
                    else None
                ),
                "sources": contribution_sources.get(account.id, []),
            }
        )
        total_balance += account.balance
        total_available += available
        total_reserved += reserved
        total_protected += account.protected_amount

    earned = Decimal(0)
    expected_incoming = Decimal(0)
    expected_outgoing = Decimal(0)
    obligation_views: list[dict[str, object]] = []
    for obligation, unit, deal in obligation_rows:
        settled = obligation.quantity_fulfilled + obligation.quantity_cleared
        outstanding = obligation.quantity_total - settled
        direction = "OWE" if obligation.debtor_member_id == member_id else "RECEIVE"
        if unit.dimension == "VALUATION":
            if direction == "RECEIVE":
                earned += settled
                expected_incoming += outstanding
            else:
                expected_outgoing += outstanding
        obligation_views.append(
            {
                "id": str(obligation.id),
                "deal_id": str(obligation.deal_id),
                "cooperative_id": str(obligation.cooperative_id),
                "debtor_member_id": str(obligation.debtor_member_id),
                "creditor_member_id": str(obligation.creditor_member_id),
                "source_purchase_intent_id": (
                    str(deal.source_purchase_intent_id)
                    if deal.source_purchase_intent_id is not None
                    else None
                ),
                "direction": direction,
                "subject_type": obligation.subject_type,
                "description": obligation.description,
                "quantity_total": _decimal(obligation.quantity_total),
                "quantity_submitted": _decimal(obligation.quantity_submitted),
                "quantity_fulfilled": _decimal(obligation.quantity_fulfilled),
                "quantity_cleared": _decimal(obligation.quantity_cleared),
                "unit_id": str(obligation.unit_id),
                "unit_code": unit.code,
                "unit_symbol": unit.symbol,
                "unit_dimension": unit.dimension,
                "due_at": obligation.due_at,
                "fulfillment_place": obligation.fulfillment_place,
                "partial_allowed": obligation.partial_allowed,
                "evidence_required": obligation.evidence_required,
                "status": obligation.status,
                "version": obligation.version,
                "valuation_source": obligation.valuation_source,
                "clearing_allowed": obligation.clearing_allowed,
            }
        )

    def offer_view(offer: FederatedOffer) -> dict[str, object]:
        return {
            "record_id": str(offer.id),
            "offer_id": str(offer.offer_id),
            "offer_version": offer.offer_version,
            "kind": str(offer.handling_requirements.get("offer_kind", "PRODUCT")).upper(),
            "has_image": bool(offer.handling_requirements.get("image_evidence_id")),
            "product_code": offer.product_code,
            "description": offer.description,
            "quantity_available": _decimal(offer.quantity_available),
            "unit_code": offer.unit_code,
            "minimum_batch": _decimal(offer.minimum_batch),
            "unit_price": _decimal(offer.unit_price),
            "valuation_unit": offer.valuation_unit,
            "price_policy_version": offer.price_policy_version,
            "origin_region": offer.origin_region,
            "pickup_address_text": offer.pickup_address_text,
            "pickup_contact_name": offer.pickup_contact_name,
            "pickup_contact_phone": offer.pickup_contact_phone,
            "pickup_instructions": offer.pickup_instructions,
            "status": offer.status,
            "availability_until": offer.availability_until,
            "created_at": offer.created_at,
            "payload_hash": offer.payload_hash,
        }

    return ParticipantEnvelope(
        data={
            "profile": {
                "member_id": str(member.id),
                "display_name": member.display_name,
                "member_status": member.status,
                "login": user.login,
                "last_login_at": user.last_login_at,
                "member_since": member.created_at,
            },
            "memberships": [
                {
                    "id": str(membership.id),
                    "cooperative_id": str(cooperative.id),
                    "cooperative_code": cooperative.code,
                    "cooperative_name": cooperative.name,
                    "cooperative_status": cooperative.status,
                    "member_number": membership.member_number,
                    "membership_status": membership.status,
                    "joined_at": membership.joined_at,
                }
                for membership, cooperative in membership_rows
            ],
            "shares": {
                "denomination": next(iter(denominations)) if len(denominations) == 1 else None,
                "total_balance": _decimal(total_balance),
                "available": _decimal(total_available),
                "protected": _decimal(total_protected),
                "reserved": _decimal(total_reserved),
                "accounts": account_views,
                "account_missing": not account_views,
            },
            "exchange_position": {
                "earned_settled": _decimal(earned),
                "expected_incoming": _decimal(expected_incoming),
                "expected_outgoing": _decimal(expected_outgoing),
            },
            "offers": [offer_view(offer) for offer in own_offers],
            "purchases": [
                {
                    "id": str(intent.id),
                    "status": intent.status,
                    "description": offer.description,
                    "quantity": _decimal(intent.quantity),
                    "unit_code": intent.unit_code,
                    "landed_cost": str(
                        intent.landed_cost_breakdown.get("landed_cost", intent.max_landed_cost)
                    ),
                    "created_at": intent.created_at,
                    "committed_at": intent.committed_at,
                }
                for intent, offer in purchases
            ],
            "sales": [
                {
                    "id": str(intent.id),
                    "status": intent.status,
                    "description": offer.description,
                    "quantity": _decimal(intent.quantity),
                    "unit_code": intent.unit_code,
                    "goods_value": str(intent.landed_cost_breakdown.get("goods_cost", "0")),
                    "delivery_address_text": intent.delivery_address_text,
                    "delivery_contact_name": intent.delivery_contact_name,
                    "delivery_contact_phone": intent.delivery_contact_phone,
                    "delivery_instructions": intent.delivery_instructions,
                    "created_at": intent.created_at,
                    "committed_at": intent.committed_at,
                }
                for intent, offer in sales
            ],
            "obligations": obligation_views,
            "commitments": [
                {
                    "id": str(item.id),
                    "type": item.commitment_type,
                    "risk_type": item.risk_type,
                    "amount_reserved": _decimal(item.amount_reserved),
                    "max_loss": _decimal(item.max_loss),
                    "status": item.status,
                    "expires_at": item.expires_at,
                    "release_condition": item.release_condition,
                }
                for item in commitments
            ],
            "generated_at": datetime.now().astimezone(),
            "cooperative_count": len(cooperative_ids),
        },
        request_id=get_request_id(),
    )
