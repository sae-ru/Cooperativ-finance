"""Signed home-node holds and the outbound client for remote purchase resources."""

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.federation.domain.peer_protocol import (
    OPERATION_CAPABILITY,
    PeerOperation,
    PeerRequest,
    PeerResponse,
    validate_response_window,
)
from cooperative_clearing.modules.federation.domain.types import federation_error
from cooperative_clearing.modules.federation.infrastructure.discovery_models import (
    FederatedOffer,
    LogisticsQuote,
    PurchaseIntent,
    ReservationReceipt,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    NodeBilateralLimit,
    NodeCertificate,
    NodeExposure,
    NodeTrustContract,
)
from cooperative_clearing.modules.federation.infrastructure.peer_models import (
    PeerProtocolExchange,
)
from cooperative_clearing.modules.federation.infrastructure.peer_transport import (
    PeerTransport,
    UrllibPeerTransport,
)
from cooperative_clearing.modules.federation.infrastructure.reservation_models import (
    PeerResourceReservation,
)
from cooperative_clearing.modules.journal.application.service import (
    ActorClaim,
    SignedJournalService,
    signer_from_settings,
)
from cooperative_clearing.modules.journal.domain.crypto import (
    canonicalize,
    payload_hash,
    utc_timestamp,
    verify_signature,
)
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError

RESERVATION_OPERATIONS = frozenset(
    {
        PeerOperation.GOODS_RESERVE,
        PeerOperation.LOGISTICS_RESERVE,
        PeerOperation.GOODS_COMMIT,
        PeerOperation.LOGISTICS_COMMIT,
        PeerOperation.GOODS_RELEASE,
        PeerOperation.LOGISTICS_RELEASE,
    }
)


@dataclass(frozen=True, slots=True)
class RemoteEvidence:
    payload: dict[str, object]
    evidence_hash: str
    signature: bytes
    signer_fingerprint: str


async def handle_peer_reservation(
    session: AsyncSession,
    *,
    settings: Settings,
    request: PeerRequest,
    peer: ExternalNode,
) -> dict[str, object]:
    if request.operation in {PeerOperation.GOODS_RESERVE, PeerOperation.LOGISTICS_RESERVE}:
        return await _reserve(session, settings=settings, request=request, peer=peer)
    if request.operation in {PeerOperation.GOODS_COMMIT, PeerOperation.LOGISTICS_COMMIT}:
        return await _commit(session, settings=settings, request=request, peer=peer)
    if request.operation in {PeerOperation.GOODS_RELEASE, PeerOperation.LOGISTICS_RELEASE}:
        return await _release(session, settings=settings, request=request, peer=peer)
    raise federation_error("PEER_OPERATION_UNSUPPORTED", 422)


async def _reserve(
    session: AsyncSession,
    *,
    settings: Settings,
    request: PeerRequest,
    peer: ExternalNode,
) -> dict[str, object]:
    payload = request.payload
    kind = _operation_kind(request.operation)
    _require_kind(payload, kind)
    receipt_id = _uuid(payload, "receipt_id")
    intent_id = _uuid(payload, "purchase_intent_id")
    amount = _positive_decimal(payload, "amount")
    unit_code = _text(payload, "unit_code", 32)
    summary_hash = _sha256(payload, "summary_hash")
    requested_expiry = _datetime(payload, "requested_expires_at")
    now = datetime.now(UTC)

    existing = await session.get(PeerResourceReservation, receipt_id, with_for_update=True)
    if existing is not None:
        _ensure_same_reservation(
            existing,
            peer=peer,
            intent_id=intent_id,
            kind=kind,
            amount=amount,
            unit_code=unit_code,
            summary_hash=summary_hash,
        )
        if existing.status == "ACTIVE" and existing.expires_at <= now:
            existing.status = "EXPIRED"
            raise federation_error("PEER_RESERVATION_EXPIRED", 409)
        if existing.status not in {"ACTIVE", "COMMITTED"}:
            raise federation_error("PEER_RESERVATION_NOT_ACTIVE", 409)
        return {
            "reservation": _artifact(
                existing.receipt_payload, existing.receipt_hash, existing.receipt_signature
            ),
            "status": existing.status,
        }

    duplicate = (
        await session.execute(
            select(PeerResourceReservation).where(
                PeerResourceReservation.buyer_node_id == peer.id,
                PeerResourceReservation.buyer_intent_id == intent_id,
                PeerResourceReservation.kind == kind,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise federation_error("PEER_RESERVATION_INTENT_KIND_CONFLICT", 409)

    if kind == "GOODS":
        offer_id = _uuid(payload, "offer_id")
        offer_version = _positive_int(payload, "offer_version")
        offer = (
            await session.execute(
                select(FederatedOffer)
                .where(
                    FederatedOffer.offer_id == offer_id,
                    FederatedOffer.offer_version == offer_version,
                    FederatedOffer.external_node_id.is_(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if offer is None:
            raise federation_error("PEER_RESOURCE_NOT_FOUND", 404)
        resource_ref = f"offer:{offer.offer_id}:{offer.offer_version}"
        available = offer.quantity_available
        valid_until = min(offer.availability_until, offer.valid_until)
        if (
            offer.status != "ACTIVE"
            or offer.availability_from > now
            or valid_until <= now
            or offer.unit_code != unit_code
            or amount < offer.minimum_batch
            or (not offer.divisible and amount != offer.quantity_available)
        ):
            raise federation_error("PEER_RESOURCE_NOT_RESERVABLE", 409)
        offer_record_id: UUID | None = offer.id
        quote_record_id: UUID | None = None
        capability = "CATALOG"
        exposure_amount = amount * (offer.unit_price + offer.mandatory_fee_per_unit)
        exposure_unit = offer.valuation_unit
        source_event_id = offer.published_event_id
    else:
        quote_id = _uuid(payload, "quote_id")
        quote_version = _positive_int(payload, "quote_version")
        quote = (
            await session.execute(
                select(LogisticsQuote)
                .where(
                    LogisticsQuote.quote_id == quote_id,
                    LogisticsQuote.quote_version == quote_version,
                    LogisticsQuote.external_node_id.is_(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if quote is None:
            raise federation_error("PEER_RESOURCE_NOT_FOUND", 404)
        resource_ref = f"quote:{quote.quote_id}:{quote.quote_version}"
        available = quote.capacity
        valid_until = quote.valid_until
        if quote.status != "ACTIVE" or valid_until <= now or quote.unit_code != unit_code:
            raise federation_error("PEER_RESOURCE_NOT_RESERVABLE", 409)
        offer_record_id = None
        quote_record_id = quote.id
        capability = "LOGISTICS"
        try:
            exposure_amount = sum(
                (Decimal(str(value)) for value in quote.cost_components.values()),
                Decimal(0),
            )
        except InvalidOperation as exc:
            raise federation_error("PEER_RESOURCE_EXPOSURE_INVALID", 409) from exc
        exposure_unit = quote.valuation_unit
        source_event_id = quote.issued_event_id

    local_held = (
        await session.execute(
            select(func.coalesce(func.sum(ReservationReceipt.amount), 0)).where(
                ReservationReceipt.resource_ref == resource_ref,
                or_(
                    ReservationReceipt.status == "COMMITTED",
                    and_(
                        ReservationReceipt.status == "ACTIVE",
                        ReservationReceipt.expires_at > now,
                    ),
                ),
            )
        )
    ).scalar_one()
    peer_held = (
        await session.execute(
            select(func.coalesce(func.sum(PeerResourceReservation.amount), 0)).where(
                PeerResourceReservation.resource_ref == resource_ref,
                or_(
                    PeerResourceReservation.status == "COMMITTED",
                    and_(
                        PeerResourceReservation.status == "ACTIVE",
                        PeerResourceReservation.expires_at > now,
                    ),
                ),
            )
        )
    ).scalar_one()
    if Decimal(local_held) + Decimal(peer_held) + amount > available:
        raise federation_error("PEER_RESOURCE_CAPACITY_INSUFFICIENT", 409)

    expiry = min(requested_expiry, valid_until, now + timedelta(hours=24))
    if expiry <= now:
        raise federation_error("PEER_RESERVATION_EXPIRY_INVALID", 422)
    signer = signer_from_settings(settings)
    receipt_payload: dict[str, object] = {
        "receipt_id": str(receipt_id),
        "purchase_intent_id": str(intent_id),
        "buyer_node_code": peer.node_code,
        "kind": kind,
        "resource_ref": resource_ref,
        "home_node_code": settings.node_code,
        "amount": _decimal(amount),
        "unit_code": unit_code,
        "expires_at": utc_timestamp(expiry),
        "summary_hash": summary_hash,
    }
    receipt_hash = payload_hash(receipt_payload)
    receipt_signature = signer.sign(canonicalize(receipt_payload))
    exposure, current_exposure, reserved_exposure, exposure_limit = await _locked_exposure(
        session,
        peer=peer,
        capability=capability,
        unit=exposure_unit,
        delta=exposure_amount,
    )
    actor = await _actor_from_event(session, source_event_id)
    created_event = await SignedJournalService(settings).append(
        session,
        event_type="federation.peer_resource_reserved",
        aggregate_type="peer_resource_reservation",
        aggregate_id=receipt_id,
        aggregate_version=1,
        actor=actor,
        payload={
            **receipt_payload,
            "receipt_hash": receipt_hash,
            "peer_node_id": str(peer.id),
            "capability": capability,
            "exposure_amount": _decimal(exposure_amount),
            "exposure_unit": exposure_unit,
            "current_exposure_before": _decimal(current_exposure),
            "reserved_exposure_before": _decimal(reserved_exposure),
            "exposure_limit": _decimal(exposure_limit),
        },
    )
    if exposure is None:
        session.add(
            NodeExposure(
                id=uuid4(),
                node_id=peer.id,
                capability=capability,
                unit=exposure_unit,
                current_amount=Decimal(0),
                reserved_amount=exposure_amount,
                updated_event_id=created_event.event_id,
            )
        )
    else:
        exposure.reserved_amount += exposure_amount
        exposure.updated_event_id = created_event.event_id
        exposure.updated_at = now
        exposure.version += 1
    session.add(
        PeerResourceReservation(
            id=receipt_id,
            buyer_node_id=peer.id,
            buyer_intent_id=intent_id,
            kind=kind,
            resource_ref=resource_ref,
            offer_record_id=offer_record_id,
            quote_record_id=quote_record_id,
            amount=amount,
            unit_code=unit_code,
            capability=capability,
            exposure_amount=exposure_amount,
            exposure_unit=exposure_unit,
            summary_hash=summary_hash,
            status="ACTIVE",
            receipt_payload=receipt_payload,
            receipt_hash=receipt_hash,
            receipt_signature=receipt_signature,
            signer_fingerprint=signer.fingerprint,
            created_event_id=created_event.event_id,
            commit_event_id=None,
            release_event_id=None,
            expiry_event_id=None,
            commit_payload=None,
            commit_hash=None,
            commit_signature=None,
            release_payload=None,
            release_hash=None,
            release_signature=None,
            expires_at=expiry,
        )
    )
    return {
        "reservation": _artifact(receipt_payload, receipt_hash, receipt_signature),
        "status": "ACTIVE",
    }


async def _commit(
    session: AsyncSession,
    *,
    settings: Settings,
    request: PeerRequest,
    peer: ExternalNode,
) -> dict[str, object]:
    row = await _locked_reservation(session, request=request, peer=peer)
    if (
        row.commit_payload is not None
        and row.commit_hash is not None
        and row.commit_signature is not None
    ):
        return {
            "commit": _artifact(row.commit_payload, row.commit_hash, row.commit_signature),
            "status": row.status,
        }
    now = datetime.now(UTC)
    if row.status != "ACTIVE":
        raise federation_error("PEER_RESERVATION_NOT_ACTIVE", 409)
    if row.expires_at <= now:
        row.status = "EXPIRED"
        raise federation_error("PEER_RESERVATION_EXPIRED", 409)
    commit_request_hash = _sha256(request.payload, "commit_request_hash")
    signer = signer_from_settings(settings)
    commit_payload: dict[str, object] = {
        "receipt_id": str(row.id),
        "purchase_intent_id": str(row.buyer_intent_id),
        "buyer_node_code": peer.node_code,
        "kind": row.kind,
        "resource_ref": row.resource_ref,
        "receipt_hash": row.receipt_hash,
        "summary_hash": row.summary_hash,
        "commit_request_hash": commit_request_hash,
        "status": "COMMITTED",
        "committed_at": utc_timestamp(now),
    }
    digest = payload_hash(commit_payload)
    signature = signer.sign(canonicalize(commit_payload))
    exposure = await _required_exposure(session, row)
    if exposure.reserved_amount < row.exposure_amount:
        raise federation_error("PEER_EXPOSURE_STATE_INVALID", 409)
    actor = await _actor_from_event(session, row.created_event_id)
    commit_event = await SignedJournalService(settings).append(
        session,
        event_type="federation.peer_resource_committed",
        aggregate_type="peer_resource_reservation",
        aggregate_id=row.id,
        aggregate_version=2,
        actor=actor,
        payload={
            **commit_payload,
            "commit_hash": digest,
            "current_exposure_before": _decimal(exposure.current_amount),
            "reserved_exposure_before": _decimal(exposure.reserved_amount),
            "exposure_amount": _decimal(row.exposure_amount),
            "exposure_unit": row.exposure_unit,
        },
    )
    exposure.reserved_amount -= row.exposure_amount
    exposure.current_amount += row.exposure_amount
    exposure.updated_event_id = commit_event.event_id
    exposure.updated_at = now
    exposure.version += 1
    row.status = "COMMITTED"
    row.commit_event_id = commit_event.event_id
    row.commit_payload = commit_payload
    row.commit_hash = digest
    row.commit_signature = signature
    row.committed_at = now
    return {"commit": _artifact(commit_payload, digest, signature), "status": "COMMITTED"}


async def _release(
    session: AsyncSession,
    *,
    settings: Settings,
    request: PeerRequest,
    peer: ExternalNode,
) -> dict[str, object]:
    row = await _locked_reservation(session, request=request, peer=peer)
    if (
        row.release_payload is not None
        and row.release_hash is not None
        and row.release_signature is not None
    ):
        return {
            "release": _artifact(row.release_payload, row.release_hash, row.release_signature),
            "status": row.status,
        }
    if row.status == "COMMITTED":
        raise federation_error("PEER_COMMITTED_RESERVATION_CANNOT_RELEASE", 409)
    reason = _text(request.payload, "reason", 1000)
    now = datetime.now(UTC)
    signer = signer_from_settings(settings)
    release_payload: dict[str, object] = {
        "receipt_id": str(row.id),
        "purchase_intent_id": str(row.buyer_intent_id),
        "buyer_node_code": peer.node_code,
        "kind": row.kind,
        "resource_ref": row.resource_ref,
        "receipt_hash": row.receipt_hash,
        "summary_hash": row.summary_hash,
        "reason": reason,
        "status": "RELEASED",
        "released_at": utc_timestamp(now),
    }
    digest = payload_hash(release_payload)
    signature = signer.sign(canonicalize(release_payload))
    exposure = await _required_exposure(session, row)
    if exposure.reserved_amount < row.exposure_amount:
        raise federation_error("PEER_EXPOSURE_STATE_INVALID", 409)
    actor = await _actor_from_event(session, row.created_event_id)
    release_event = await SignedJournalService(settings).append(
        session,
        event_type="federation.peer_resource_released",
        aggregate_type="peer_resource_reservation",
        aggregate_id=row.id,
        aggregate_version=2,
        actor=actor,
        payload={
            **release_payload,
            "release_hash": digest,
            "current_exposure_before": _decimal(exposure.current_amount),
            "reserved_exposure_before": _decimal(exposure.reserved_amount),
            "exposure_amount": _decimal(row.exposure_amount),
            "exposure_unit": row.exposure_unit,
        },
    )
    exposure.reserved_amount -= row.exposure_amount
    exposure.updated_event_id = release_event.event_id
    exposure.updated_at = now
    exposure.version += 1
    row.status = "RELEASED"
    row.release_event_id = release_event.event_id
    row.release_payload = release_payload
    row.release_hash = digest
    row.release_signature = signature
    row.released_at = now
    return {"release": _artifact(release_payload, digest, signature), "status": "RELEASED"}


async def _locked_reservation(
    session: AsyncSession, *, request: PeerRequest, peer: ExternalNode
) -> PeerResourceReservation:
    kind = _operation_kind(request.operation)
    _require_kind(request.payload, kind)
    receipt_id = _uuid(request.payload, "receipt_id")
    intent_id = _uuid(request.payload, "purchase_intent_id")
    receipt_hash = _sha256(request.payload, "receipt_hash")
    summary_hash = _sha256(request.payload, "summary_hash")
    row = await session.get(PeerResourceReservation, receipt_id, with_for_update=True)
    if row is None:
        raise federation_error("PEER_RESERVATION_NOT_FOUND", 404)
    if (
        row.buyer_node_id != peer.id
        or row.buyer_intent_id != intent_id
        or row.kind != kind
        or row.receipt_hash != receipt_hash
        or row.summary_hash != summary_hash
    ):
        raise federation_error("PEER_RESERVATION_BINDING_INVALID", 409)
    return row


@dataclass(frozen=True, slots=True)
class ReservationExpirationResult:
    peer_reservations: int
    purchase_intents: int


async def expire_stale_reservations(
    session: AsyncSession,
    *,
    settings: Settings,
    batch_size: int = 100,
) -> ReservationExpirationResult:
    now = datetime.now(UTC)
    peer_rows = list(
        (
            await session.execute(
                select(PeerResourceReservation)
                .where(
                    PeerResourceReservation.status == "ACTIVE",
                    PeerResourceReservation.expires_at <= now,
                )
                .order_by(PeerResourceReservation.expires_at, PeerResourceReservation.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    journal = SignedJournalService(settings)
    for row in peer_rows:
        exposure = await _required_exposure(session, row)
        if exposure.reserved_amount < row.exposure_amount:
            raise federation_error("PEER_EXPOSURE_STATE_INVALID", 409)
        actor = await _actor_from_event(session, row.created_event_id)
        event = await journal.append(
            session,
            event_type="federation.peer_resource_expired",
            aggregate_type="peer_resource_reservation",
            aggregate_id=row.id,
            aggregate_version=2,
            actor=actor,
            payload={
                "receipt_id": str(row.id),
                "buyer_intent_id": str(row.buyer_intent_id),
                "buyer_node_id": str(row.buyer_node_id),
                "kind": row.kind,
                "resource_ref": row.resource_ref,
                "receipt_hash": row.receipt_hash,
                "expired_at": utc_timestamp(now),
                "exposure_amount": _decimal(row.exposure_amount),
                "exposure_unit": row.exposure_unit,
            },
        )
        exposure.reserved_amount -= row.exposure_amount
        exposure.updated_event_id = event.event_id
        exposure.updated_at = now
        exposure.version += 1
        row.status = "EXPIRED"
        row.expiry_event_id = event.event_id

    remaining = max(0, batch_size - len(peer_rows))
    purchase_rows = (
        list(
            (
                await session.execute(
                    select(PurchaseIntent)
                    .where(
                        PurchaseIntent.status.in_(
                            (
                                "PREPARING",
                                "GOODS_RESERVED",
                                "PREPARED",
                            )
                        ),
                        PurchaseIntent.expires_at <= now,
                    )
                    .order_by(PurchaseIntent.expires_at, PurchaseIntent.id)
                    .limit(remaining)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        if remaining
        else []
    )
    for intent in purchase_rows:
        actor = await _actor_from_event(session, intent.created_event_id)
        receipts = list(
            (
                await session.execute(
                    select(ReservationReceipt)
                    .where(
                        ReservationReceipt.intent_id == intent.id,
                        ReservationReceipt.status == "ACTIVE",
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        event = await journal.append(
            session,
            event_type="federation.purchase_intent_expired",
            aggregate_type="purchase_intent",
            aggregate_id=intent.id,
            aggregate_version=intent.version + 1,
            actor=actor,
            payload={
                "purchase_intent_id": str(intent.id),
                "summary_hash": intent.summary_hash,
                "expired_at": utc_timestamp(now),
                "expired_receipt_ids": sorted(str(receipt.id) for receipt in receipts),
            },
        )
        for receipt in receipts:
            receipt.status = "EXPIRED"
            receipt.expiry_event_id = event.event_id
            receipt.closed_at = now
            receipt.version += 1
        intent.status = "EXPIRED"
        intent.closed_at = now
        intent.version += 1
    return ReservationExpirationResult(len(peer_rows), len(purchase_rows))


async def _locked_exposure(
    session: AsyncSession,
    *,
    peer: ExternalNode,
    capability: str,
    unit: str,
    delta: Decimal,
) -> tuple[NodeExposure | None, Decimal, Decimal, Decimal]:
    limit = (
        await session.execute(
            select(NodeBilateralLimit)
            .where(
                NodeBilateralLimit.node_id == peer.id,
                NodeBilateralLimit.capability == capability,
                NodeBilateralLimit.unit == unit,
                NodeBilateralLimit.status == "ACTIVE",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if limit is None:
        raise federation_error("PEER_ACTIVE_BILATERAL_LIMIT_REQUIRED", 409)
    exposure = (
        await session.execute(
            select(NodeExposure)
            .where(
                NodeExposure.node_id == peer.id,
                NodeExposure.capability == capability,
                NodeExposure.unit == unit,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    current = exposure.current_amount if exposure is not None else Decimal(0)
    reserved = exposure.reserved_amount if exposure is not None else Decimal(0)
    if delta > limit.max_package_value:
        raise federation_error("PEER_PACKAGE_VALUE_LIMIT_EXCEEDED", 409)
    if current + reserved + delta > limit.max_unsettled_obligations:
        raise federation_error("PEER_UNSETTLED_EXPOSURE_LIMIT_EXCEEDED", 409)
    return exposure, current, reserved, limit.max_unsettled_obligations


async def _required_exposure(session: AsyncSession, row: PeerResourceReservation) -> NodeExposure:
    exposure = (
        await session.execute(
            select(NodeExposure)
            .where(
                NodeExposure.node_id == row.buyer_node_id,
                NodeExposure.capability == row.capability,
                NodeExposure.unit == row.exposure_unit,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if exposure is None:
        raise federation_error("PEER_EXPOSURE_STATE_INVALID", 409)
    return exposure


async def _actor_from_event(session: AsyncSession, event_id: UUID) -> ActorClaim:
    event = await session.get(SignedEvent, event_id)
    if event is None:
        raise federation_error("PEER_RESOURCE_ACTOR_EVIDENCE_MISSING", 500)
    return ActorClaim(
        person_id=event.actor_person_id,
        organization_id=event.actor_organization_id,
        role_assignment_id=event.actor_role_assignment_id,
    )


class PeerReservationClient:
    def __init__(self, settings: Settings, transport: PeerTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport or UrllibPeerTransport(settings)

    async def reserve(
        self, session: AsyncSession, *, node_id: UUID, kind: str, payload: dict[str, object]
    ) -> RemoteEvidence:
        operation = (
            PeerOperation.GOODS_RESERVE if kind == "GOODS" else PeerOperation.LOGISTICS_RESERVE
        )
        response, certificate = await self.exchange(
            session, node_id=node_id, operation=operation, payload=payload
        )
        evidence = self._evidence(response, "reservation", certificate)
        self._validate_binding(evidence.payload, payload=payload, kind=kind)
        return evidence

    async def commit(
        self, session: AsyncSession, *, node_id: UUID, kind: str, payload: dict[str, object]
    ) -> RemoteEvidence:
        operation = (
            PeerOperation.GOODS_COMMIT if kind == "GOODS" else PeerOperation.LOGISTICS_COMMIT
        )
        response, certificate = await self.exchange(
            session, node_id=node_id, operation=operation, payload=payload
        )
        evidence = self._evidence(response, "commit", certificate)
        self._validate_binding(evidence.payload, payload=payload, kind=kind)
        if evidence.payload.get("commit_request_hash") != payload.get("commit_request_hash"):
            raise federation_error("PEER_COMMIT_BINDING_INVALID", 502)
        return evidence

    async def release(
        self, session: AsyncSession, *, node_id: UUID, kind: str, payload: dict[str, object]
    ) -> RemoteEvidence:
        operation = (
            PeerOperation.GOODS_RELEASE if kind == "GOODS" else PeerOperation.LOGISTICS_RELEASE
        )
        response, certificate = await self.exchange(
            session, node_id=node_id, operation=operation, payload=payload
        )
        evidence = self._evidence(response, "release", certificate)
        self._validate_binding(evidence.payload, payload=payload, kind=kind)
        return evidence

    async def exchange(
        self,
        session: AsyncSession,
        *,
        node_id: UUID,
        operation: PeerOperation,
        payload: dict[str, object],
    ) -> tuple[dict[str, object], NodeCertificate]:
        capability = OPERATION_CAPABILITY[operation]
        node, certificate = await _trusted_material(session, node_id=node_id, capability=capability)
        endpoint = _peer_endpoint(node)
        signer = signer_from_settings(self.settings)
        issued_at = datetime.now(UTC).replace(microsecond=0)
        request = PeerRequest(
            message_id=uuid4(),
            source_node_code=self.settings.node_code,
            target_node_code=node.node_code,
            operation=operation,
            signer_fingerprint=signer.fingerprint,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=60),
            payload=payload,
        )
        request_signature = signer.sign(canonicalize(request.document()))
        try:
            wire = await self.transport.post(
                endpoint,
                {
                    **request.document(),
                    "signature_base64": base64.b64encode(request_signature).decode("ascii"),
                },
            )
            response_document, response_signature = self._verify_response(
                node=node, certificate=certificate, request=request, wire=wire
            )
        except BaseException as exc:
            code = exc.code if isinstance(exc, DomainError) else "PEER_UNAVAILABLE"
            session.add(_failed_exchange(node, request, request_signature, code))
            if isinstance(exc, DomainError):
                raise
            raise federation_error("PEER_UNAVAILABLE", 503) from exc
        session.add(
            _successful_exchange(
                node, request, request_signature, response_document, response_signature
            )
        )
        return cast(dict[str, object], response_document["payload"]), certificate

    def _verify_response(
        self,
        *,
        node: ExternalNode,
        certificate: NodeCertificate,
        request: PeerRequest,
        wire: dict[str, object],
    ) -> tuple[dict[str, object], bytes]:
        try:
            signature_value = wire["signature_base64"]
            response_payload = wire["payload"]
            if not isinstance(signature_value, str) or not isinstance(response_payload, dict):
                raise ValueError
            signature = base64.b64decode(signature_value, validate=True)
            response = PeerResponse(
                message_id=UUID(_wire_text(wire, "message_id")),
                request_hash=_wire_text(wire, "request_hash"),
                source_node_code=_wire_text(wire, "source_node_code"),
                target_node_code=_wire_text(wire, "target_node_code"),
                operation=PeerOperation(_wire_text(wire, "operation")),
                signer_fingerprint=_wire_text(wire, "signer_fingerprint"),
                signed_at=_datetime(wire, "signed_at"),
                expires_at=_datetime(wire, "expires_at"),
                payload=cast(dict[str, object], response_payload),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise federation_error("PEER_RESPONSE_INVALID", 502) from exc
        document = response.document()
        if {key: value for key, value in wire.items() if key != "signature_base64"} != document:
            raise federation_error("PEER_RESPONSE_CANONICAL_MISMATCH", 502)
        validate_response_window(
            now=datetime.now(UTC), signed_at=response.signed_at, expires_at=response.expires_at
        )
        if (
            response.message_id != request.message_id
            or response.request_hash != payload_hash(request.document())
            or response.source_node_code != node.node_code
            or response.target_node_code != self.settings.node_code
            or response.operation is not request.operation
            or response.signer_fingerprint != certificate.fingerprint
        ):
            raise federation_error("PEER_RESPONSE_BINDING_INVALID", 502)
        if not verify_signature(certificate.public_key, signature, canonicalize(document)):
            raise federation_error("PEER_RESPONSE_SIGNATURE_INVALID", 502)
        return document, signature

    @staticmethod
    def _evidence(
        response: dict[str, object], key: str, certificate: NodeCertificate
    ) -> RemoteEvidence:
        artifact = response.get(key)
        if not isinstance(artifact, dict):
            raise federation_error("PEER_RESERVATION_RESPONSE_INVALID", 502)
        body = artifact.get("payload")
        digest = artifact.get("payload_hash")
        encoded = artifact.get("signature_base64")
        if (
            not isinstance(body, dict)
            or not isinstance(digest, str)
            or not isinstance(encoded, str)
        ):
            raise federation_error("PEER_RESERVATION_RESPONSE_INVALID", 502)
        try:
            signature = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise federation_error("PEER_RESERVATION_RESPONSE_INVALID", 502) from exc
        body = cast(dict[str, object], body)
        if payload_hash(body) != digest or not verify_signature(
            certificate.public_key, signature, canonicalize(body)
        ):
            raise federation_error("PEER_RESERVATION_EVIDENCE_INVALID", 502)
        return RemoteEvidence(body, digest, signature, certificate.fingerprint)

    def _validate_binding(
        self, evidence: dict[str, object], *, payload: dict[str, object], kind: str
    ) -> None:
        if (
            evidence.get("receipt_id") != payload.get("receipt_id")
            or evidence.get("purchase_intent_id") != payload.get("purchase_intent_id")
            or evidence.get("buyer_node_code") != self.settings.node_code
            or evidence.get("kind") != kind
            or evidence.get("summary_hash") != payload.get("summary_hash")
        ):
            raise federation_error("PEER_RESERVATION_BINDING_INVALID", 502)


async def _trusted_material(
    session: AsyncSession, *, node_id: UUID, capability: str
) -> tuple[ExternalNode, NodeCertificate]:
    now = datetime.now(UTC)
    node = await session.get(ExternalNode, node_id)
    if node is None or node.status != "ACTIVE" or capability not in node.capabilities:
        raise federation_error("PEER_NOT_TRUSTED", 403)
    if "CC-PEER-1" not in node.supported_protocols:
        raise federation_error("PEER_PROTOCOL_UNSUPPORTED", 422)
    contract = (
        await session.execute(
            select(NodeTrustContract).where(
                NodeTrustContract.node_id == node.id,
                NodeTrustContract.status == "ACTIVE",
                NodeTrustContract.valid_from <= now,
                NodeTrustContract.valid_until > now,
            )
        )
    ).scalar_one_or_none()
    if contract is None or capability not in contract.capabilities:
        raise federation_error("PEER_CAPABILITY_NOT_TRUSTED", 403)
    certificate = (
        await session.execute(
            select(NodeCertificate).where(
                NodeCertificate.node_id == node.id,
                NodeCertificate.status == "ACTIVE",
                NodeCertificate.valid_from <= now,
                NodeCertificate.valid_until > now,
            )
        )
    ).scalar_one_or_none()
    if certificate is None:
        raise federation_error("PEER_CERTIFICATE_NOT_ACTIVE", 403)
    return node, certificate


def _peer_endpoint(node: ExternalNode) -> str:
    for value in node.network_endpoints:
        if isinstance(value, dict) and value.get("transport") in {"HTTPS", "HTTP"}:
            uri = value.get("uri")
            if isinstance(uri, str) and uri.strip():
                return uri
    raise federation_error("PEER_ENDPOINT_MISSING", 422)


def _successful_exchange(
    node: ExternalNode,
    request: PeerRequest,
    request_signature: bytes,
    response_document: dict[str, object],
    response_signature: bytes,
) -> PeerProtocolExchange:
    return PeerProtocolExchange(
        id=uuid4(),
        direction="OUTBOUND",
        peer_node_id=node.id,
        message_id=request.message_id,
        operation=request.operation.value,
        status="SUCCEEDED",
        request_document=request.document(),
        request_hash=payload_hash(request.document()),
        request_signature=request_signature,
        request_signer_fingerprint=request.signer_fingerprint,
        response_document=response_document,
        response_hash=payload_hash(response_document),
        response_signature=response_signature,
        response_signer_fingerprint=str(response_document["signer_fingerprint"]),
        error_code=None,
        error_detail=None,
        completed_at=datetime.now(UTC),
        expires_at=request.expires_at,
    )


def _failed_exchange(
    node: ExternalNode, request: PeerRequest, request_signature: bytes, code: str
) -> PeerProtocolExchange:
    return PeerProtocolExchange(
        id=uuid4(),
        direction="OUTBOUND",
        peer_node_id=node.id,
        message_id=request.message_id,
        operation=request.operation.value,
        status="FAILED",
        request_document=request.document(),
        request_hash=payload_hash(request.document()),
        request_signature=request_signature,
        request_signer_fingerprint=request.signer_fingerprint,
        response_document=None,
        response_hash=None,
        response_signature=None,
        response_signer_fingerprint=None,
        error_code=code,
        error_detail=None,
        completed_at=datetime.now(UTC),
        expires_at=request.expires_at,
    )


def _artifact(body: dict[str, object], digest: str, signature: bytes) -> dict[str, object]:
    return {
        "payload": body,
        "payload_hash": digest,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def _ensure_same_reservation(
    row: PeerResourceReservation,
    *,
    peer: ExternalNode,
    intent_id: UUID,
    kind: str,
    amount: Decimal,
    unit_code: str,
    summary_hash: str,
) -> None:
    if (
        row.buyer_node_id != peer.id
        or row.buyer_intent_id != intent_id
        or row.kind != kind
        or row.amount != amount
        or row.unit_code != unit_code
        or row.summary_hash != summary_hash
    ):
        raise federation_error("PEER_RESERVATION_TAMPERED_REPLAY", 409)


def _operation_kind(operation: PeerOperation) -> str:
    return "GOODS" if operation.value.startswith("GOODS_") else "LOGISTICS"


def _require_kind(payload: dict[str, object], expected: str) -> None:
    if payload.get("kind") != expected:
        raise federation_error("PEER_RESERVATION_KIND_INVALID", 422)


def _uuid(payload: dict[str, object], name: str) -> UUID:
    try:
        return UUID(_wire_text(payload, name))
    except ValueError as exc:
        raise federation_error("PEER_RESERVATION_PAYLOAD_INVALID", 422) from exc


def _text(payload: dict[str, object], name: str, maximum: int) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise federation_error("PEER_RESERVATION_PAYLOAD_INVALID", 422)
    result = value.strip()
    if not result or len(result) > maximum or any(ord(char) < 32 for char in result):
        raise federation_error("PEER_RESERVATION_PAYLOAD_INVALID", 422)
    return result


def _sha256(payload: dict[str, object], name: str) -> str:
    value = _text(payload, name, 71)
    if len(value) != 71 or not value.startswith("sha256:"):
        raise federation_error("PEER_RESERVATION_PAYLOAD_INVALID", 422)
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise federation_error("PEER_RESERVATION_PAYLOAD_INVALID", 422) from exc
    return value


def _positive_decimal(payload: dict[str, object], name: str) -> Decimal:
    try:
        value = Decimal(_wire_text(payload, name))
    except InvalidOperation as exc:
        raise federation_error("PEER_RESERVATION_PAYLOAD_INVALID", 422) from exc
    if not value.is_finite() or value <= 0:
        raise federation_error("PEER_RESERVATION_PAYLOAD_INVALID", 422)
    return value


def _positive_int(payload: dict[str, object], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise federation_error("PEER_RESERVATION_PAYLOAD_INVALID", 422)
    return value


def _datetime(payload: dict[str, object], name: str) -> datetime:
    try:
        value = datetime.fromisoformat(_wire_text(payload, name).replace("Z", "+00:00"))
    except ValueError as exc:
        raise federation_error("PEER_RESERVATION_PAYLOAD_INVALID", 422) from exc
    if value.tzinfo is None:
        raise federation_error("PEER_RESERVATION_PAYLOAD_INVALID", 422)
    return value.astimezone(UTC)


def _wire_text(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise ValueError(name)
    return value


def _decimal(value: Decimal) -> str:
    return format(value, "f")
