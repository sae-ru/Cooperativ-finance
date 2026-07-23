"""Federated catalog, logistics quote, and purchase reservation API."""

import base64
import binascii
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.identity_schemas import CommandEnvelope
from cooperative_clearing.api.identity_schemas import CommandResult as ApiCommandResult
from cooperative_clearing.modules.federation.application.common import FederationCommandResult
from cooperative_clearing.modules.federation.application.discovery import (
    ArtifactVerification,
    DiscoveryService,
    SearchCandidate,
)
from cooperative_clearing.modules.federation.application.peer_protocol import (
    PeerDiscoveryClient,
    PeerFanoutStatus,
)
from cooperative_clearing.modules.federation.application.peer_reservations import (
    PeerReservationClient,
)
from cooperative_clearing.modules.federation.domain.discovery import CostStatus, SearchMode
from cooperative_clearing.modules.federation.domain.types import federation_error
from cooperative_clearing.modules.federation.infrastructure.discovery_models import (
    FederatedOffer,
    LogisticsQuote,
    OfferIndexSnapshot,
    PurchaseIntent,
    ReservationReceipt,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/federation", tags=["federated-discovery"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]
AUDIT_ROLES = {RoleCode.NODE_AUDITOR, RoleCode.AUDITOR, RoleCode.SECURITY_ADMIN}


class OfferPublishRequest(BaseModel):
    offer_id: UUID = Field(default_factory=uuid4)
    offer_version: int = Field(default=1, ge=1)
    external_node_id: UUID | None = None
    seller_ref: str = Field(min_length=1, max_length=160)
    product_code: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=2000)
    quality_grade: str = Field(min_length=1, max_length=80)
    certificate_refs: list[str] = Field(default_factory=list, max_length=50)
    quantity_available: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    quantity_is_band: bool = False
    unit_code: str = Field(min_length=1, max_length=32)
    unit_scale: int = Field(ge=0, le=12)
    minimum_batch: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    divisible: bool = True
    origin_region: str = Field(min_length=1, max_length=200)
    origin_precision: str = Field(pattern=r"^(EXACT|DISTRICT|REGION)$")
    availability_from: AwareDatetime
    availability_until: AwareDatetime
    fulfillment_deadline: AwareDatetime
    unit_price: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    mandatory_fee_per_unit: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    valuation_unit: str = Field(min_length=1, max_length=32)
    price_policy_version: str = Field(min_length=1, max_length=80)
    handling_requirements: dict[str, object] = Field(default_factory=dict)
    counterparty_policy: dict[str, object] = Field(default_factory=dict)
    geography_policy: dict[str, object] = Field(default_factory=dict)
    guarantee_terms: dict[str, object] = Field(default_factory=dict)
    source_mode: SearchMode = SearchMode.DIRECT
    node_sequence: int = Field(ge=1)
    signed_at: AwareDatetime
    valid_until: AwareDatetime
    external_signature_base64: str | None = Field(default=None, min_length=80, max_length=200)


class OfferIndexPublishRequest(BaseModel):
    external_node_id: UUID | None = None
    source_mode: SearchMode
    node_sequence: int = Field(ge=1)
    ordered_offer_hashes: list[str] = Field(max_length=10000)
    signed_at: AwareDatetime
    valid_until: AwareDatetime
    external_signature_base64: str | None = Field(default=None, min_length=80, max_length=200)


class OfferRevokeRequest(BaseModel):
    offer_id: UUID
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class LogisticsQuoteRequest(BaseModel):
    quote_id: UUID = Field(default_factory=uuid4)
    quote_version: int = Field(default=1, ge=1)
    offer_record_id: UUID
    external_node_id: UUID | None = None
    carrier_ref: str = Field(min_length=1, max_length=160)
    destination_region: str = Field(min_length=1, max_length=200)
    route_legs: list[object] = Field(min_length=1, max_length=50)
    custody_transfers: int = Field(ge=0, le=100)
    capacity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    cost_components: dict[str, Decimal] = Field(max_length=20)
    cost_status: CostStatus
    delivery_from: AwareDatetime
    delivery_until: AwareDatetime
    liability_limit: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    bond_ref: str | None = Field(default=None, max_length=160)
    assumptions: list[str] = Field(default_factory=list, max_length=50)
    signed_at: AwareDatetime
    valid_until: AwareDatetime
    external_signature_base64: str | None = Field(default=None, min_length=80, max_length=200)


class SearchRequest(BaseModel):
    mode: SearchMode = SearchMode.DIRECT
    product_code: str = Field(min_length=1, max_length=80)
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    unit_code: str = Field(min_length=1, max_length=32)
    valuation_unit: str = Field(min_length=1, max_length=32)
    destination_region: str = Field(min_length=1, max_length=200)
    maximum_age_seconds: int = Field(default=3600, ge=1, le=604800)
    trusted_node_codes: list[str] = Field(default_factory=list, max_length=100)
    required_certificates: list[str] = Field(default_factory=list, max_length=50)
    quality_minimum: str | None = Field(default=None, max_length=80)
    maximum_goods_cost: Decimal | None = Field(default=None, ge=0, max_digits=38, decimal_places=12)
    maximum_landed_cost: Decimal | None = Field(
        default=None, ge=0, max_digits=38, decimal_places=12
    )
    latest_delivery: AwareDatetime | None = None
    top_k: int = Field(default=20, ge=1, le=100)


class VerificationRequest(BaseModel):
    live: bool = True
    maximum_age_seconds: int = Field(default=3600, ge=1, le=604800)


class PurchaseIntentCreateRequest(BaseModel):
    offer_record_id: UUID
    quote_record_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    destination_region: str = Field(min_length=1, max_length=200)
    max_landed_cost: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    expires_at: AwareDatetime


class ReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: UUID = Field(default_factory=uuid4)
    expires_at: AwareDatetime


class PurchaseCommitRequest(BaseModel):
    summary_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_version: int = Field(ge=1)


class PurchaseCancelRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    expected_version: int = Field(ge=1)


class OfferView(BaseModel):
    record_id: UUID
    offer_id: UUID
    offer_version: int
    home_node_code: str
    seller_ref: str
    product_code: str
    description: str
    quality_grade: str
    certificate_refs: list[str]
    quantity_available: Decimal
    quantity_is_band: bool
    unit_code: str
    unit_scale: int
    minimum_batch: Decimal
    divisible: bool
    origin_region: str
    origin_precision: str
    availability_from: datetime
    availability_until: datetime
    fulfillment_deadline: datetime
    unit_price: Decimal
    mandatory_fee_per_unit: Decimal
    valuation_unit: str
    price_policy_version: str
    handling_requirements: dict[str, object]
    counterparty_policy: dict[str, object]
    geography_policy: dict[str, object]
    guarantee_terms: dict[str, object]
    source_mode: str
    node_sequence: int
    signed_at: datetime
    valid_until: datetime
    signer_fingerprint: str
    payload_hash: str


class QuoteView(BaseModel):
    record_id: UUID
    quote_id: UUID
    quote_version: int
    home_node_code: str
    carrier_ref: str
    destination_region: str
    route_legs: list[object]
    custody_transfers: int
    capacity: Decimal
    unit_code: str
    cost_components: dict[str, object]
    valuation_unit: str
    cost_status: str
    delivery_from: datetime
    delivery_until: datetime
    liability_limit: Decimal
    bond_ref: str | None
    assumptions: list[str]
    signed_at: datetime
    valid_until: datetime
    signer_fingerprint: str


class SearchCandidateView(BaseModel):
    offer: OfferView
    quote: QuoteView | None
    freshness: str
    signature_verified: bool
    goods_cost: Decimal
    logistics_cost: Decimal | None
    mandatory_cost: Decimal | None
    landed_cost: Decimal | None
    cost_status: str | None


class PeerStatusView(BaseModel):
    node_code: str
    status: str
    result_code: str
    imported_offers: int
    imported_quotes: int


class SearchResponse(BaseModel):
    data: list[SearchCandidateView]
    mode: SearchMode
    peer_statuses: list[PeerStatusView] = Field(default_factory=list)
    ranking_version: str = "LANDED_COST_V1"
    request_id: str


class VerificationView(BaseModel):
    valid: bool
    freshness: str
    home_node_code: str
    signer_fingerprint: str
    valid_until: datetime


class VerificationEnvelope(BaseModel):
    data: VerificationView
    request_id: str


class ObjectCollection(BaseModel):
    data: list[dict[str, Any]]
    request_id: str


CommandAction = Callable[[AsyncSession], Awaitable[FederationCommandResult]]


def _request_uuid() -> UUID | None:
    try:
        return UUID(get_request_id())
    except ValueError:
        return None


def _signature(value: str | None) -> bytes | None:
    if value is None:
        return None
    try:
        result = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise federation_error("SIGNATURE_ENCODING_INVALID", 422) from exc
    if len(result) != 64:
        raise federation_error("SIGNATURE_LENGTH_INVALID", 422)
    return result


def _member_access(principal: Principal) -> None:
    if principal.must_change_password:
        raise DomainError(
            code="PASSWORD_CHANGE_REQUIRED",
            message_key="errors.auth.password_change_required",
            status_code=403,
        )
    if principal.member_id is None:
        raise federation_error("PERSONAL_ACTOR_REQUIRED", 403)


def _command(result: FederationCommandResult) -> CommandEnvelope:
    return CommandEnvelope(
        data=ApiCommandResult(
            event_id=result.event_id,
            object_id=result.object_id,
            replayed=result.replayed,
        ),
        request_id=get_request_id(),
    )


async def _commit(database: DatabaseDependency, action: CommandAction) -> CommandEnvelope:
    async with database.session() as session:
        try:
            result = await action(session)
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise federation_error("CONFLICT") from exc
    return _command(result)


def _offer_view(offer: FederatedOffer) -> OfferView:
    return OfferView(
        record_id=offer.id,
        offer_id=offer.offer_id,
        offer_version=offer.offer_version,
        home_node_code=offer.home_node_code,
        seller_ref=offer.seller_ref,
        product_code=offer.product_code,
        description=offer.description,
        quality_grade=offer.quality_grade,
        certificate_refs=offer.certificate_refs,
        quantity_available=offer.quantity_available,
        quantity_is_band=offer.quantity_is_band,
        unit_code=offer.unit_code,
        unit_scale=offer.unit_scale,
        minimum_batch=offer.minimum_batch,
        divisible=offer.divisible,
        origin_region=offer.origin_region,
        origin_precision=offer.origin_precision,
        availability_from=offer.availability_from,
        availability_until=offer.availability_until,
        fulfillment_deadline=offer.fulfillment_deadline,
        unit_price=offer.unit_price,
        mandatory_fee_per_unit=offer.mandatory_fee_per_unit,
        valuation_unit=offer.valuation_unit,
        price_policy_version=offer.price_policy_version,
        handling_requirements=offer.handling_requirements,
        counterparty_policy=offer.counterparty_policy,
        geography_policy=offer.geography_policy,
        guarantee_terms=offer.guarantee_terms,
        source_mode=offer.source_mode,
        node_sequence=offer.node_sequence,
        signed_at=offer.signed_at,
        valid_until=offer.valid_until,
        signer_fingerprint=offer.signer_fingerprint,
        payload_hash=offer.payload_hash,
    )


def _quote_view(quote: LogisticsQuote) -> QuoteView:
    return QuoteView(
        record_id=quote.id,
        quote_id=quote.quote_id,
        quote_version=quote.quote_version,
        home_node_code=quote.home_node_code,
        carrier_ref=quote.carrier_ref,
        destination_region=quote.destination_region,
        route_legs=quote.route_legs,
        custody_transfers=quote.custody_transfers,
        capacity=quote.capacity,
        unit_code=quote.unit_code,
        cost_components=quote.cost_components,
        valuation_unit=quote.valuation_unit,
        cost_status=quote.cost_status,
        delivery_from=quote.delivery_from,
        delivery_until=quote.delivery_until,
        liability_limit=quote.liability_limit,
        bond_ref=quote.bond_ref,
        assumptions=quote.assumptions,
        signed_at=quote.signed_at,
        valid_until=quote.valid_until,
        signer_fingerprint=quote.signer_fingerprint,
    )


def _candidate_view(candidate: SearchCandidate) -> SearchCandidateView:
    return SearchCandidateView(
        offer=_offer_view(candidate.offer),
        quote=_quote_view(candidate.quote) if candidate.quote else None,
        freshness=candidate.freshness.value,
        signature_verified=candidate.signature_verified,
        goods_cost=candidate.goods_cost,
        logistics_cost=candidate.logistics_cost,
        mandatory_cost=candidate.mandatory_cost,
        landed_cost=candidate.landed_cost,
        cost_status=candidate.cost_status.value if candidate.cost_status else None,
    )


def _verification_view(result: ArtifactVerification) -> VerificationEnvelope:
    return VerificationEnvelope(
        data=VerificationView(
            valid=result.valid,
            freshness=result.freshness.value,
            home_node_code=result.home_node_code,
            signer_fingerprint=result.signer_fingerprint,
            valid_until=result.valid_until,
        ),
        request_id=get_request_id(),
    )


def _index_view(snapshot: OfferIndexSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "external_node_id": snapshot.external_node_id,
        "home_node_code": snapshot.home_node_code,
        "source_mode": snapshot.source_mode,
        "node_sequence": snapshot.node_sequence,
        "offer_count": len(snapshot.ordered_offer_hashes),
        "ordered_offer_hashes": snapshot.ordered_offer_hashes,
        "checkpoint_hash": snapshot.checkpoint_hash,
        "signed_at": snapshot.signed_at,
        "valid_until": snapshot.valid_until,
        "signer_fingerprint": snapshot.signer_fingerprint,
        "created_at": snapshot.created_at,
    }


def _intent_view(intent: PurchaseIntent) -> dict[str, Any]:
    return {
        "id": intent.id,
        "buyer_node_code": intent.buyer_node_code,
        "buyer_member_id": intent.buyer_member_id,
        "offer_record_id": intent.offer_record_id,
        "quote_record_id": intent.quote_record_id,
        "quantity": intent.quantity,
        "unit_code": intent.unit_code,
        "destination_region": intent.destination_region,
        "max_landed_cost": intent.max_landed_cost,
        "landed_cost_breakdown": intent.landed_cost_breakdown,
        "cost_status": intent.cost_status,
        "summary_hash": intent.summary_hash,
        "status": intent.status,
        "commit_request_hash": intent.commit_request_hash,
        "commit_expected_version": (
            intent.commit_request_payload.get("expected_version")
            if intent.commit_request_payload is not None
            else None
        ),
        "cancellation_expected_version": (
            intent.version - 1 if intent.status == "CANCELLING" else None
        ),
        "created_at": intent.created_at,
        "expires_at": intent.expires_at,
        "committed_at": intent.committed_at,
        "closed_at": intent.closed_at,
        "version": intent.version,
    }


def _receipt_view(receipt: ReservationReceipt) -> dict[str, Any]:
    return {
        "id": receipt.id,
        "intent_id": receipt.intent_id,
        "kind": receipt.kind,
        "resource_ref": receipt.resource_ref,
        "home_node_code": receipt.home_node_code,
        "amount": receipt.amount,
        "unit_code": receipt.unit_code,
        "status": receipt.status,
        "receipt_hash": receipt.receipt_hash,
        "signer_fingerprint": receipt.signer_fingerprint,
        "remote_commit_hash": receipt.remote_commit_hash,
        "remote_commit_signer_fingerprint": receipt.remote_commit_signer_fingerprint,
        "remote_release_hash": receipt.remote_release_hash,
        "remote_release_signer_fingerprint": receipt.remote_release_signer_fingerprint,
        "created_at": receipt.created_at,
        "expires_at": receipt.expires_at,
        "closed_at": receipt.closed_at,
        "version": receipt.version,
    }


@router.post("/offers/publish", response_model=CommandEnvelope, status_code=201)
async def publish_offer(
    payload: OfferPublishRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: DiscoveryService(settings).publish_offer(
            session,
            principal=principal,
            **payload.model_dump(exclude={"external_signature_base64"}),
            external_signature=_signature(payload.external_signature_base64),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/catalog/indexes", response_model=CommandEnvelope, status_code=201)
async def publish_offer_index(
    payload: OfferIndexPublishRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: DiscoveryService(settings).publish_offer_index(
            session,
            principal=principal,
            **payload.model_dump(exclude={"external_signature_base64"}),
            external_signature=_signature(payload.external_signature_base64),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.get("/catalog/indexes", response_model=ObjectCollection)
async def list_offer_indexes(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=100, ge=1, le=500),
) -> ObjectCollection:
    _member_access(principal)
    async with database.session() as session:
        snapshots = list(
            (
                await session.execute(
                    select(OfferIndexSnapshot)
                    .order_by(OfferIndexSnapshot.created_at.desc())
                    .limit(limit)
                )
            ).scalars()
        )
    return ObjectCollection(
        data=[_index_view(snapshot) for snapshot in snapshots], request_id=get_request_id()
    )


@router.post("/offers/revoke", response_model=CommandEnvelope, status_code=201)
async def revoke_offer(
    payload: OfferRevokeRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: DiscoveryService(settings).revoke_offer(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/logistics/quotes", response_model=CommandEnvelope, status_code=201)
async def issue_logistics_quote(
    payload: LogisticsQuoteRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: DiscoveryService(settings).issue_logistics_quote(
            session,
            principal=principal,
            **payload.model_dump(exclude={"external_signature_base64"}),
            external_signature=_signature(payload.external_signature_base64),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/catalog/search", response_model=SearchResponse)
async def search_catalog(
    payload: SearchRequest,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> SearchResponse:
    _member_access(principal)
    peer_statuses: tuple[PeerFanoutStatus, ...] = ()
    async with database.session() as session:
        if payload.mode is SearchMode.DIRECT:
            fanout = await PeerDiscoveryClient(settings).refresh_direct_catalog(
                session,
                principal=principal,
                payload={
                    "product_code": payload.product_code,
                    "quantity": format(payload.quantity, "f"),
                    "unit_code": payload.unit_code,
                    "valuation_unit": payload.valuation_unit,
                    "destination_region": payload.destination_region,
                    "required_certificates": payload.required_certificates,
                    "quality_grade": payload.quality_minimum,
                    "maximum_goods_cost": (
                        format(payload.maximum_goods_cost, "f")
                        if payload.maximum_goods_cost is not None
                        else None
                    ),
                    "latest_delivery": (
                        payload.latest_delivery.isoformat()
                        if payload.latest_delivery is not None
                        else None
                    ),
                    "top_k": payload.top_k,
                },
            )
            peer_statuses = fanout.statuses
        results = await DiscoveryService(settings).search(session, **payload.model_dump())
        await session.commit()
    return SearchResponse(
        data=[_candidate_view(candidate) for candidate in results],
        mode=payload.mode,
        peer_statuses=[
            PeerStatusView(
                node_code=status.node_code,
                status=status.status,
                result_code=status.result_code,
                imported_offers=status.imported_offers,
                imported_quotes=status.imported_quotes,
            )
            for status in peer_statuses
        ],
        request_id=get_request_id(),
    )


@router.post("/catalog/offers/{record_id}/verify", response_model=VerificationEnvelope)
async def verify_offer(
    record_id: UUID,
    payload: VerificationRequest,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> VerificationEnvelope:
    _member_access(principal)
    async with database.session() as session:
        offer = await session.get(FederatedOffer, record_id)
        if offer is None:
            raise federation_error("OFFER_NOT_FOUND", 404)
        result = await DiscoveryService(settings).verify_offer(
            session,
            offer,
            live=payload.live,
            maximum_age_seconds=payload.maximum_age_seconds,
        )
        if payload.live and result.valid and offer.external_node_id is None:
            offer.last_verified_at = datetime.now().astimezone()
            await session.commit()
    return _verification_view(result)


@router.post("/logistics/quotes/{record_id}/verify", response_model=VerificationEnvelope)
async def verify_logistics_quote(
    record_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> VerificationEnvelope:
    _member_access(principal)
    async with database.session() as session:
        quote = await session.get(LogisticsQuote, record_id)
        if quote is None:
            raise federation_error("LOGISTICS_QUOTE_NOT_FOUND", 404)
        result = await DiscoveryService(settings).verify_quote(session, quote)
        if result.valid and quote.external_node_id is None:
            quote.last_verified_at = datetime.now().astimezone()
            await session.commit()
    return _verification_view(result)


@router.post("/purchase-intents", response_model=CommandEnvelope, status_code=201)
async def create_purchase_intent(
    payload: PurchaseIntentCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    _member_access(principal)
    return await _commit(
        database,
        lambda session: DiscoveryService(settings).create_purchase_intent(
            session,
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


async def _reserve(
    *,
    intent_id: UUID,
    kind: str,
    payload: ReservationRequest,
    idempotency_key: str,
    principal: Principal,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    _member_access(principal)

    async def action(session: AsyncSession) -> FederationCommandResult:
        service = DiscoveryService(settings)
        plan = await service.remote_reservation_plan(
            session,
            principal=principal,
            intent_id=intent_id,
            kind=kind,
            receipt_id=payload.receipt_id,
            expires_at=payload.expires_at,
        )
        evidence = (
            await PeerReservationClient(settings).reserve(
                session,
                node_id=plan.external_node_id,
                kind=kind,
                payload=plan.payload,
            )
            if plan is not None
            else None
        )
        return await service.reserve(
            session,
            principal=principal,
            intent_id=intent_id,
            kind=kind,
            receipt_id=payload.receipt_id,
            expires_at=payload.expires_at,
            external_signature=evidence.signature if evidence is not None else None,
            external_receipt_payload=evidence.payload if evidence is not None else None,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit(database, action)


@router.post(
    "/purchase-intents/{intent_id}/reserve-goods",
    response_model=CommandEnvelope,
    status_code=201,
)
async def reserve_goods(
    intent_id: UUID,
    payload: ReservationRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _reserve(
        intent_id=intent_id,
        kind="GOODS",
        payload=payload,
        idempotency_key=idempotency_key,
        principal=principal,
        database=database,
        settings=settings,
    )


@router.post(
    "/purchase-intents/{intent_id}/reserve-logistics",
    response_model=CommandEnvelope,
    status_code=201,
)
async def reserve_logistics(
    intent_id: UUID,
    payload: ReservationRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _reserve(
        intent_id=intent_id,
        kind="LOGISTICS",
        payload=payload,
        idempotency_key=idempotency_key,
        principal=principal,
        database=database,
        settings=settings,
    )


@router.post(
    "/purchase-intents/{intent_id}/commit",
    response_model=CommandEnvelope,
    status_code=201,
)
async def commit_purchase(
    intent_id: UUID,
    payload: PurchaseCommitRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    _member_access(principal)
    service = DiscoveryService(settings)
    await _commit(
        database,
        lambda session: service.begin_commit(
            session,
            principal=principal,
            intent_id=intent_id,
            **payload.model_dump(),
            request_id=_request_uuid(),
        ),
    )
    async with database.session() as session:
        try:
            plans = await service.remote_commit_plans(
                session,
                principal=principal,
                intent_id=intent_id,
            )
            client = PeerReservationClient(settings)
            for plan in plans:
                evidence = await client.commit(
                    session,
                    node_id=plan.external_node_id,
                    kind=plan.kind,
                    payload=plan.payload,
                )
                await service.record_remote_commit(
                    session,
                    principal=principal,
                    intent_id=intent_id,
                    receipt_id=plan.receipt_id,
                    evidence_payload=evidence.payload,
                    evidence_hash=evidence.evidence_hash,
                    evidence_signature=evidence.signature,
                    signer_fingerprint=evidence.signer_fingerprint,
                )
            await session.commit()
        except DomainError:
            await session.commit()
            raise
    return await _commit(
        database,
        lambda session: service.commit(
            session,
            principal=principal,
            intent_id=intent_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/purchase-intents/{intent_id}/cancel",
    response_model=CommandEnvelope,
    status_code=201,
)
async def cancel_purchase(
    intent_id: UUID,
    payload: PurchaseCancelRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    _member_access(principal)
    service = DiscoveryService(settings)
    await _commit(
        database,
        lambda session: service.begin_cancel(
            session,
            principal=principal,
            intent_id=intent_id,
            **payload.model_dump(),
            request_id=_request_uuid(),
        ),
    )
    async with database.session() as session:
        try:
            plans = await service.remote_release_plans(
                session,
                principal=principal,
                intent_id=intent_id,
            )
            client = PeerReservationClient(settings)
            for plan in plans:
                evidence = await client.release(
                    session,
                    node_id=plan.external_node_id,
                    kind=plan.kind,
                    payload=plan.payload,
                )
                await service.record_remote_release(
                    session,
                    principal=principal,
                    intent_id=intent_id,
                    receipt_id=plan.receipt_id,
                    evidence_payload=evidence.payload,
                    evidence_hash=evidence.evidence_hash,
                    evidence_signature=evidence.signature,
                    signer_fingerprint=evidence.signer_fingerprint,
                )
            await session.commit()
        except DomainError:
            await session.commit()
            raise
    return await _commit(
        database,
        lambda session: service.cancel(
            session,
            principal=principal,
            intent_id=intent_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.get("/purchase-intents", response_model=ObjectCollection)
async def list_purchase_intents(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=100, ge=1, le=500),
) -> ObjectCollection:
    _member_access(principal)
    statement = select(PurchaseIntent).order_by(PurchaseIntent.created_at.desc()).limit(limit)
    if not principal.has_role(AUDIT_ROLES):
        statement = statement.where(PurchaseIntent.buyer_member_id == principal.member_id)
    async with database.session() as session:
        intents = list((await session.execute(statement)).scalars())
    return ObjectCollection(
        data=[_intent_view(intent) for intent in intents], request_id=get_request_id()
    )


@router.get("/purchase-intents/{intent_id}/receipts", response_model=ObjectCollection)
async def list_purchase_receipts(
    intent_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> ObjectCollection:
    _member_access(principal)
    async with database.session() as session:
        intent = await session.get(PurchaseIntent, intent_id)
        if intent is None:
            raise federation_error("PURCHASE_INTENT_NOT_FOUND", 404)
        if intent.buyer_member_id != principal.member_id and not principal.has_role(AUDIT_ROLES):
            raise federation_error("PURCHASE_BUYER_MISMATCH", 403)
        receipts = list(
            (
                await session.execute(
                    select(ReservationReceipt)
                    .where(ReservationReceipt.intent_id == intent_id)
                    .order_by(ReservationReceipt.kind)
                )
            ).scalars()
        )
    return ObjectCollection(
        data=[_receipt_view(receipt) for receipt in receipts], request_id=get_request_id()
    )
