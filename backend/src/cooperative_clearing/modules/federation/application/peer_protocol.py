"""Authenticated peer search handling, fan-out, and signed artifact import."""

import asyncio
import base64
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.federation.application.discovery import DiscoveryService
from cooperative_clearing.modules.federation.application.peer_clearing import (
    CLEARING_OPERATIONS,
    handle_peer_clearing,
)
from cooperative_clearing.modules.federation.application.peer_reservations import (
    RESERVATION_OPERATIONS,
    handle_peer_reservation,
)
from cooperative_clearing.modules.federation.domain.discovery import CostStatus, SearchMode
from cooperative_clearing.modules.federation.domain.peer_protocol import (
    OPERATION_CAPABILITY,
    PeerOperation,
    PeerRequest,
    PeerResponse,
    validate_request_window,
    validate_response_window,
)
from cooperative_clearing.modules.federation.domain.types import (
    federation_error,
    normalize_code,
)
from cooperative_clearing.modules.federation.infrastructure.discovery_models import (
    FederatedOffer,
    LogisticsQuote,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    NodeCertificate,
    NodeTrustContract,
)
from cooperative_clearing.modules.federation.infrastructure.peer_models import (
    PeerProtocolExchange,
)
from cooperative_clearing.modules.federation.infrastructure.peer_transport import (
    PeerTransport,
    UrllibPeerTransport,
)
from cooperative_clearing.modules.identity.domain.types import Principal
from cooperative_clearing.modules.journal.application.service import signer_from_settings
from cooperative_clearing.modules.journal.domain.crypto import (
    canonicalize,
    payload_hash,
    verify_signature,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SignedPeerResponse:
    document: dict[str, object]
    signature: bytes


@dataclass(frozen=True, slots=True)
class PeerFanoutStatus:
    node_code: str
    status: str
    result_code: str
    imported_offers: int = 0
    imported_quotes: int = 0


@dataclass(frozen=True, slots=True)
class PeerFanoutResult:
    statuses: tuple[PeerFanoutStatus, ...]


class PeerProtocolService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def handle(
        self,
        session: AsyncSession,
        *,
        request: PeerRequest,
        signature: bytes,
    ) -> SignedPeerResponse:
        document = request.document()
        validate_request_window(
            now=datetime.now(UTC), issued_at=request.issued_at, expires_at=request.expires_at
        )
        if request.target_node_code != self.settings.node_code:
            raise federation_error("PEER_REQUEST_WRONG_TARGET", 422)
        capability = OPERATION_CAPABILITY[request.operation]
        peer, certificate = await trusted_peer_material(
            session,
            node_code=request.source_node_code,
            capability=capability,
            fingerprint=request.signer_fingerprint,
        )
        if not verify_signature(certificate.public_key, signature, canonicalize(document)):
            raise federation_error("PEER_REQUEST_SIGNATURE_INVALID", 401)
        request_hash = payload_hash(document)
        previous = (
            await session.execute(
                select(PeerProtocolExchange).where(
                    PeerProtocolExchange.direction == "INBOUND",
                    PeerProtocolExchange.peer_node_id == peer.id,
                    PeerProtocolExchange.message_id == request.message_id,
                )
            )
        ).scalar_one_or_none()
        if previous is not None:
            if previous.request_hash != request_hash:
                raise federation_error("PEER_REQUEST_TAMPERED_REPLAY", 409)
            if previous.response_document is None or previous.response_signature is None:
                raise federation_error("PEER_REPLAY_EVIDENCE_INCOMPLETE", 500)
            return SignedPeerResponse(previous.response_document, previous.response_signature)

        if request.operation is PeerOperation.CATALOG_SEARCH:
            response_payload = await self._catalog_search(session, request.payload)
        elif request.operation in RESERVATION_OPERATIONS:
            response_payload = await handle_peer_reservation(
                session,
                settings=self.settings,
                request=request,
                peer=peer,
            )
        elif request.operation in CLEARING_OPERATIONS:
            response_payload = await handle_peer_clearing(
                session,
                settings=self.settings,
                request=request,
                peer=peer,
            )
        else:
            raise federation_error("PEER_OPERATION_UNSUPPORTED", 422)

        signer = signer_from_settings(self.settings)
        signed_at = datetime.now(UTC).replace(microsecond=0)
        response = PeerResponse(
            message_id=request.message_id,
            request_hash=request_hash,
            source_node_code=self.settings.node_code,
            target_node_code=peer.node_code,
            operation=request.operation,
            signer_fingerprint=signer.fingerprint,
            signed_at=signed_at,
            expires_at=min(request.expires_at, signed_at + timedelta(seconds=60)),
            payload=response_payload,
        )
        response_document = response.document()
        response_signature = signer.sign(canonicalize(response_document))
        session.add(
            PeerProtocolExchange(
                id=uuid4(),
                direction="INBOUND",
                peer_node_id=peer.id,
                message_id=request.message_id,
                operation=request.operation.value,
                status="SUCCEEDED",
                request_document=document,
                request_hash=request_hash,
                request_signature=signature,
                request_signer_fingerprint=request.signer_fingerprint,
                response_document=response_document,
                response_hash=payload_hash(response_document),
                response_signature=response_signature,
                response_signer_fingerprint=signer.fingerprint,
                error_code=None,
                error_detail=None,
                completed_at=datetime.now(UTC),
                expires_at=request.expires_at,
            )
        )
        return SignedPeerResponse(response_document, response_signature)

    async def _catalog_search(
        self, session: AsyncSession, payload: dict[str, object]
    ) -> dict[str, object]:
        product = normalize_code(_text(payload, "product_code", 80), 80)
        unit = normalize_code(_text(payload, "unit_code", 32), 32)
        valuation = normalize_code(_text(payload, "valuation_unit", 32), 32)
        destination = _text(payload, "destination_region", 200)
        quantity = _positive_decimal(payload, "quantity")
        quality = _optional_text(payload, "quality_grade", 80)
        certificates = _text_list(payload, "required_certificates", 50, 200)
        maximum_goods_cost = _optional_nonnegative_decimal(payload, "maximum_goods_cost")
        latest_delivery = _optional_datetime(payload, "latest_delivery")
        top_k = _bounded_int(payload, "top_k", minimum=1, maximum=100)
        now = datetime.now(UTC)
        rows = list(
            (
                await session.execute(
                    select(FederatedOffer).where(
                        FederatedOffer.external_node_id.is_(None),
                        FederatedOffer.product_code == product,
                        FederatedOffer.unit_code == unit,
                        FederatedOffer.valuation_unit == valuation,
                        FederatedOffer.status == "ACTIVE",
                        FederatedOffer.quantity_available >= quantity,
                        FederatedOffer.minimum_batch <= quantity,
                        FederatedOffer.availability_from <= now,
                        FederatedOffer.availability_until > now,
                        FederatedOffer.valid_until > now,
                    )
                )
            ).scalars()
        )
        latest: dict[UUID, FederatedOffer] = {}
        for row in rows:
            current = latest.get(row.offer_id)
            if current is None or row.offer_version > current.offer_version:
                latest[row.offer_id] = row
        selected: list[FederatedOffer] = []
        for offer in latest.values():
            if quality is not None and offer.quality_grade != quality:
                continue
            if certificates and not set(certificates).issubset(offer.certificate_refs):
                continue
            if not offer.divisible and quantity != offer.quantity_available:
                continue
            if maximum_goods_cost is not None and quantity * offer.unit_price > maximum_goods_cost:
                continue
            if latest_delivery is not None and offer.fulfillment_deadline > latest_delivery:
                continue
            selected.append(offer)
        selected.sort(
            key=lambda offer: (offer.unit_price, offer.fulfillment_deadline, str(offer.id))
        )
        selected = selected[:top_k]
        offer_ids = [offer.id for offer in selected]
        quotes = (
            list(
                (
                    await session.execute(
                        select(LogisticsQuote).where(
                            LogisticsQuote.external_node_id.is_(None),
                            LogisticsQuote.offer_record_id.in_(offer_ids),
                            LogisticsQuote.destination_region == destination,
                            LogisticsQuote.capacity >= quantity,
                            LogisticsQuote.status == "ACTIVE",
                            LogisticsQuote.valid_until > now,
                        )
                    )
                ).scalars()
            )
            if offer_ids
            else []
        )
        return {
            "offers": [
                _artifact(offer.payload, offer.payload_hash, offer.node_signature)
                for offer in selected
            ],
            "quotes": [
                _artifact(quote.payload, quote.payload_hash, quote.node_signature)
                for quote in quotes
            ],
            "quality_mapping_version": "EXACT-V1",
            "unit_mapping_version": "EXACT-V1",
            "result_count": len(selected),
        }


class PeerDiscoveryClient:
    def __init__(self, settings: Settings, transport: PeerTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport or UrllibPeerTransport(settings)

    async def refresh_direct_catalog(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        payload: dict[str, object],
    ) -> PeerFanoutResult:
        nodes = list(
            (
                await session.execute(
                    select(ExternalNode)
                    .where(ExternalNode.status == "ACTIVE")
                    .order_by(ExternalNode.node_code)
                    .limit(self.settings.peer_max_fanout)
                )
            ).scalars()
        )
        eligible: list[tuple[ExternalNode, str, NodeCertificate]] = []
        statuses: list[PeerFanoutStatus] = []
        for node in nodes:
            if "CC-PEER-1" not in node.supported_protocols:
                continue
            try:
                endpoint = _peer_endpoint(node)
                _peer, certificate = await trusted_peer_material(
                    session,
                    node_code=node.node_code,
                    capability="CATALOG",
                    fingerprint=None,
                )
            except DomainError as exc:
                statuses.append(PeerFanoutStatus(node.node_code, "SKIPPED", exc.code))
                continue
            eligible.append((node, endpoint, certificate))
        signer = signer_from_settings(self.settings)
        requests: list[tuple[ExternalNode, NodeCertificate, PeerRequest, bytes, str]] = []
        for node, endpoint, certificate in eligible:
            issued_at = datetime.now(UTC).replace(microsecond=0)
            request = PeerRequest(
                message_id=uuid4(),
                source_node_code=self.settings.node_code,
                target_node_code=node.node_code,
                operation=PeerOperation.CATALOG_SEARCH,
                signer_fingerprint=signer.fingerprint,
                issued_at=issued_at,
                expires_at=issued_at + timedelta(seconds=60),
                payload=payload,
            )
            signature = signer.sign(canonicalize(request.document()))
            requests.append((node, certificate, request, signature, endpoint))
        results = await asyncio.gather(
            *(
                self._send(endpoint, request, signature)
                for _node, _certificate, request, signature, endpoint in requests
            ),
            return_exceptions=True,
        )
        for (node, certificate, request, signature, _endpoint), result in zip(
            requests, results, strict=True
        ):
            if isinstance(result, BaseException):
                code = result.code if isinstance(result, DomainError) else "PEER_UNAVAILABLE"
                self._record_outbound_failure(session, node, request, signature, code)
                statuses.append(PeerFanoutStatus(node.node_code, "FAILED", code))
                continue
            try:
                response_document, response_signature = self._verify_response(
                    node=node,
                    certificate=certificate,
                    request=request,
                    request_signature=signature,
                    wire=result,
                )
                imported_offers, imported_quotes = await self._import_artifacts(
                    session,
                    principal=principal,
                    node=node,
                    payload=cast(dict[str, object], response_document["payload"]),
                )
                session.add(
                    _successful_outbound_exchange(
                        node=node,
                        request=request,
                        request_signature=signature,
                        response_document=response_document,
                        response_signature=response_signature,
                    )
                )
                statuses.append(
                    PeerFanoutStatus(
                        node.node_code,
                        "SUCCEEDED",
                        "OK",
                        imported_offers,
                        imported_quotes,
                    )
                )
            except DomainError as exc:
                self._record_outbound_failure(session, node, request, signature, exc.code)
                statuses.append(PeerFanoutStatus(node.node_code, "FAILED", exc.code))
        return PeerFanoutResult(tuple(statuses))

    async def _send(
        self, endpoint: str, request: PeerRequest, signature: bytes
    ) -> dict[str, object]:
        return await self.transport.post(
            endpoint,
            {
                **request.document(),
                "signature_base64": base64.b64encode(signature).decode("ascii"),
            },
        )

    def _verify_response(
        self,
        *,
        node: ExternalNode,
        certificate: NodeCertificate,
        request: PeerRequest,
        request_signature: bytes,
        wire: dict[str, object],
    ) -> tuple[dict[str, object], bytes]:
        del request_signature
        try:
            signature_value = wire["signature_base64"]
            if not isinstance(signature_value, str):
                raise ValueError
            signature = base64.b64decode(signature_value, validate=True)
            payload = wire["payload"]
            if not isinstance(payload, dict):
                raise ValueError
            response = PeerResponse(
                message_id=UUID(_wire_text(wire, "message_id")),
                request_hash=_wire_text(wire, "request_hash"),
                source_node_code=_wire_text(wire, "source_node_code"),
                target_node_code=_wire_text(wire, "target_node_code"),
                operation=PeerOperation(_wire_text(wire, "operation")),
                signer_fingerprint=_wire_text(wire, "signer_fingerprint"),
                signed_at=_wire_datetime(wire, "signed_at"),
                expires_at=_wire_datetime(wire, "expires_at"),
                payload=cast(dict[str, object], payload),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise federation_error("PEER_RESPONSE_INVALID", 502) from exc
        document = response.document()
        wire_document = {key: value for key, value in wire.items() if key != "signature_base64"}
        if wire_document != document:
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

    async def _import_artifacts(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        node: ExternalNode,
        payload: dict[str, object],
    ) -> tuple[int, int]:
        offers = _artifact_list(payload, "offers")
        quotes = _artifact_list(payload, "quotes")
        service = DiscoveryService(self.settings)
        imported_offers = 0
        imported_quotes = 0
        now = datetime.now(UTC)
        offer_records: dict[tuple[UUID, int], FederatedOffer] = {}
        for artifact in offers:
            body, signature, claimed_hash = _artifact_parts(artifact)
            offer_id = UUID(_wire_text(body, "offer_id"))
            offer_version = _wire_int(body, "offer_version")
            existing = (
                await session.execute(
                    select(FederatedOffer).where(
                        FederatedOffer.offer_id == offer_id,
                        FederatedOffer.offer_version == offer_version,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                if (
                    existing.payload_hash != claimed_hash
                    or existing.home_node_code != node.node_code
                ):
                    raise federation_error("PEER_OFFER_TAMPERED_DUPLICATE")
                existing.last_verified_at = now
                offer_records[(offer_id, offer_version)] = existing
                continue
            result = await service.publish_offer(
                session,
                principal=principal,
                offer_id=offer_id,
                offer_version=offer_version,
                external_node_id=node.id,
                seller_ref=_wire_text(body, "seller_ref"),
                product_code=_wire_text(body, "product_code"),
                description=_wire_text(body, "description"),
                quality_grade=_wire_text(body, "quality_grade"),
                certificate_refs=_wire_text_list(body, "certificate_refs"),
                quantity_available=_wire_decimal(body, "quantity_available"),
                quantity_is_band=_wire_bool(body, "quantity_is_band"),
                unit_code=_wire_text(body, "unit_code"),
                unit_scale=_wire_int(body, "unit_scale"),
                minimum_batch=_wire_decimal(body, "minimum_batch"),
                divisible=_wire_bool(body, "divisible"),
                origin_region=_wire_text(body, "origin_region"),
                origin_precision=_wire_text(body, "origin_precision"),
                availability_from=_wire_datetime(body, "availability_from"),
                availability_until=_wire_datetime(body, "availability_until"),
                fulfillment_deadline=_wire_datetime(body, "fulfillment_deadline"),
                unit_price=_wire_decimal(body, "unit_price"),
                mandatory_fee_per_unit=_wire_decimal(body, "mandatory_fee_per_unit"),
                valuation_unit=_wire_text(body, "valuation_unit"),
                price_policy_version=_wire_text(body, "price_policy_version"),
                handling_requirements=_wire_dict(body, "handling_requirements"),
                counterparty_policy=_wire_dict(body, "counterparty_policy"),
                geography_policy=_wire_dict(body, "geography_policy"),
                guarantee_terms=_wire_dict(body, "guarantee_terms"),
                source_mode=SearchMode(_wire_text(body, "source_mode")),
                node_sequence=_wire_int(body, "node_sequence"),
                signed_at=_wire_datetime(body, "signed_at"),
                valid_until=_wire_datetime(body, "valid_until"),
                external_signature=signature,
                idempotency_key=f"peer-offer:{node.node_code}:{claimed_hash}",
                request_id=None,
                trusted_import=True,
            )
            await session.flush()
            row = await session.get(FederatedOffer, result.object_id)
            if row is None or row.payload_hash != claimed_hash:
                raise federation_error("PEER_OFFER_IMPORT_INVALID", 502)
            row.last_verified_at = now
            offer_records[(offer_id, offer_version)] = row
            imported_offers += 1
        for artifact in quotes:
            body, signature, claimed_hash = _artifact_parts(artifact)
            quote_id = UUID(_wire_text(body, "quote_id"))
            quote_version = _wire_int(body, "quote_version")
            existing_quote = (
                await session.execute(
                    select(LogisticsQuote).where(
                        LogisticsQuote.quote_id == quote_id,
                        LogisticsQuote.quote_version == quote_version,
                    )
                )
            ).scalar_one_or_none()
            if existing_quote is not None:
                if (
                    existing_quote.payload_hash != claimed_hash
                    or existing_quote.home_node_code != node.node_code
                ):
                    raise federation_error("PEER_QUOTE_TAMPERED_DUPLICATE")
                existing_quote.last_verified_at = now
                continue
            offer_key = (
                UUID(_wire_text(body, "offer_id")),
                _wire_int(body, "offer_version"),
            )
            offer = offer_records.get(offer_key)
            if offer is None:
                offer = (
                    await session.execute(
                        select(FederatedOffer).where(
                            FederatedOffer.offer_id == offer_key[0],
                            FederatedOffer.offer_version == offer_key[1],
                            FederatedOffer.home_node_code == node.node_code,
                        )
                    )
                ).scalar_one_or_none()
            if offer is None:
                raise federation_error("PEER_QUOTE_OFFER_MISSING", 502)
            result = await service.issue_logistics_quote(
                session,
                principal=principal,
                quote_id=quote_id,
                quote_version=quote_version,
                offer_record_id=offer.id,
                external_node_id=node.id,
                carrier_ref=_wire_text(body, "carrier_ref"),
                destination_region=_wire_text(body, "destination_region"),
                route_legs=_wire_list(body, "route_legs"),
                custody_transfers=_wire_int(body, "custody_transfers"),
                capacity=_wire_decimal(body, "capacity"),
                cost_components={
                    key: Decimal(str(value))
                    for key, value in _wire_dict(body, "cost_components").items()
                },
                cost_status=CostStatus(_wire_text(body, "cost_status")),
                delivery_from=_wire_datetime(body, "delivery_from"),
                delivery_until=_wire_datetime(body, "delivery_until"),
                liability_limit=_wire_decimal(body, "liability_limit"),
                bond_ref=_wire_optional_text(body, "bond_ref"),
                assumptions=_wire_text_list(body, "assumptions"),
                signed_at=_wire_datetime(body, "signed_at"),
                valid_until=_wire_datetime(body, "valid_until"),
                external_signature=signature,
                idempotency_key=f"peer-quote:{node.node_code}:{claimed_hash}",
                request_id=None,
                trusted_import=True,
            )
            await session.flush()
            quote_row = await session.get(LogisticsQuote, result.object_id)
            if quote_row is None or quote_row.payload_hash != claimed_hash:
                raise federation_error("PEER_QUOTE_IMPORT_INVALID", 502)
            quote_row.last_verified_at = now
            imported_quotes += 1
        return imported_offers, imported_quotes

    @staticmethod
    def _record_outbound_failure(
        session: AsyncSession,
        node: ExternalNode,
        request: PeerRequest,
        signature: bytes,
        code: str,
    ) -> None:
        session.add(
            PeerProtocolExchange(
                id=uuid4(),
                direction="OUTBOUND",
                peer_node_id=node.id,
                message_id=request.message_id,
                operation=request.operation.value,
                status="FAILED",
                request_document=request.document(),
                request_hash=payload_hash(request.document()),
                request_signature=signature,
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
        )


async def trusted_peer_material(
    session: AsyncSession,
    *,
    node_code: str,
    capability: str,
    fingerprint: str | None,
) -> tuple[ExternalNode, NodeCertificate]:
    now = datetime.now(UTC)
    normalized = str(normalize_code(node_code, 63)).lower()
    node = (
        await session.execute(
            select(ExternalNode).where(
                ExternalNode.node_code == normalized,
                ExternalNode.status == "ACTIVE",
            )
        )
    ).scalar_one_or_none()
    if node is None or capability not in node.capabilities:
        raise federation_error("PEER_NOT_TRUSTED", 403)
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
    certificate_query = select(NodeCertificate).where(
        NodeCertificate.node_id == node.id,
        NodeCertificate.status == "ACTIVE",
        NodeCertificate.valid_from <= now,
        NodeCertificate.valid_until > now,
    )
    if fingerprint is not None:
        certificate_query = certificate_query.where(NodeCertificate.fingerprint == fingerprint)
    certificate = (await session.execute(certificate_query)).scalar_one_or_none()
    if certificate is None:
        raise federation_error("PEER_CERTIFICATE_NOT_ACTIVE", 403)
    return node, certificate


def _artifact(payload: dict[str, object], digest: str, signature: bytes) -> dict[str, object]:
    return {
        "payload": payload,
        "payload_hash": digest,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def _successful_outbound_exchange(
    *,
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


def _peer_endpoint(node: ExternalNode) -> str:
    for value in node.network_endpoints:
        if not isinstance(value, dict):
            continue
        transport = value.get("transport")
        uri = value.get("uri")
        if transport in {"HTTPS", "HTTP"} and isinstance(uri, str) and uri.strip():
            return uri
    raise federation_error("PEER_ENDPOINT_MISSING", 422)


def _artifact_list(payload: dict[str, object], name: str) -> list[dict[str, object]]:
    value = payload.get(name)
    if not isinstance(value, list) or len(value) > 200:
        raise federation_error("PEER_ARTIFACT_LIST_INVALID", 502)
    if not all(isinstance(item, dict) for item in value):
        raise federation_error("PEER_ARTIFACT_LIST_INVALID", 502)
    return cast(list[dict[str, object]], value)


def _artifact_parts(
    artifact: dict[str, object],
) -> tuple[dict[str, object], bytes, str]:
    payload = artifact.get("payload")
    digest = artifact.get("payload_hash")
    signature_value = artifact.get("signature_base64")
    if (
        not isinstance(payload, dict)
        or not isinstance(digest, str)
        or not isinstance(signature_value, str)
    ):
        raise federation_error("PEER_ARTIFACT_INVALID", 502)
    if payload_hash(payload) != digest:
        raise federation_error("PEER_ARTIFACT_HASH_INVALID", 502)
    try:
        signature = base64.b64decode(signature_value, validate=True)
    except ValueError as exc:
        raise federation_error("PEER_ARTIFACT_SIGNATURE_INVALID", 502) from exc
    return cast(dict[str, object], payload), signature, digest


def _text(payload: dict[str, object], name: str, maximum: int) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise federation_error("PEER_SEARCH_PAYLOAD_INVALID", 422)
    result = value.strip()
    if not result or len(result) > maximum or any(ord(character) < 32 for character in result):
        raise federation_error("PEER_SEARCH_PAYLOAD_INVALID", 422)
    return result


def _optional_text(payload: dict[str, object], name: str, maximum: int) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise federation_error("PEER_SEARCH_PAYLOAD_INVALID", 422)
    return value.strip()


def _text_list(
    payload: dict[str, object], name: str, maximum_items: int, maximum_length: int
) -> list[str]:
    value = payload.get(name, [])
    if not isinstance(value, list) or len(value) > maximum_items:
        raise federation_error("PEER_SEARCH_PAYLOAD_INVALID", 422)
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > maximum_length:
            raise federation_error("PEER_SEARCH_PAYLOAD_INVALID", 422)
        result.append(item.strip())
    return result


def _positive_decimal(payload: dict[str, object], name: str) -> Decimal:
    try:
        value = Decimal(str(payload[name]))
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise federation_error("PEER_SEARCH_PAYLOAD_INVALID", 422) from exc
    if not value.is_finite() or value <= 0:
        raise federation_error("PEER_SEARCH_PAYLOAD_INVALID", 422)
    return value


def _optional_nonnegative_decimal(payload: dict[str, object], name: str) -> Decimal | None:
    if payload.get(name) is None:
        return None
    try:
        value = Decimal(str(payload[name]))
    except (InvalidOperation, ValueError) as exc:
        raise federation_error("PEER_SEARCH_PAYLOAD_INVALID", 422) from exc
    if not value.is_finite() or value < 0:
        raise federation_error("PEER_SEARCH_PAYLOAD_INVALID", 422)
    return value


def _bounded_int(payload: dict[str, object], name: str, *, minimum: int, maximum: int) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise federation_error("PEER_SEARCH_PAYLOAD_INVALID", 422)
    return value


def _optional_datetime(payload: dict[str, object], name: str) -> datetime | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise federation_error("PEER_SEARCH_PAYLOAD_INVALID", 422)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise federation_error("PEER_SEARCH_PAYLOAD_INVALID", 422) from exc
    if parsed.tzinfo is None:
        raise federation_error("PEER_SEARCH_PAYLOAD_INVALID", 422)
    return parsed


def _wire_text(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise ValueError(name)
    return value


def _wire_optional_text(payload: dict[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise federation_error("PEER_ARTIFACT_INVALID", 502)
    return value


def _wire_int(payload: dict[str, object], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise federation_error("PEER_ARTIFACT_INVALID", 502)
    return value


def _wire_bool(payload: dict[str, object], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise federation_error("PEER_ARTIFACT_INVALID", 502)
    return value


def _wire_decimal(payload: dict[str, object], name: str) -> Decimal:
    try:
        return Decimal(_wire_text(payload, name))
    except InvalidOperation as exc:
        raise federation_error("PEER_ARTIFACT_INVALID", 502) from exc


def _wire_datetime(payload: dict[str, object], name: str) -> datetime:
    try:
        value = datetime.fromisoformat(_wire_text(payload, name).replace("Z", "+00:00"))
    except ValueError as exc:
        raise federation_error("PEER_ARTIFACT_INVALID", 502) from exc
    if value.tzinfo is None:
        raise federation_error("PEER_ARTIFACT_INVALID", 502)
    return value


def _wire_dict(payload: dict[str, object], name: str) -> dict[str, object]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise federation_error("PEER_ARTIFACT_INVALID", 502)
    return cast(dict[str, object], value)


def _wire_list(payload: dict[str, object], name: str) -> list[object]:
    value = payload.get(name)
    if not isinstance(value, list):
        raise federation_error("PEER_ARTIFACT_INVALID", 502)
    return cast(list[object], value)


def _wire_text_list(payload: dict[str, object], name: str) -> list[str]:
    value = _wire_list(payload, name)
    if not all(isinstance(item, str) for item in value):
        raise federation_error("PEER_ARTIFACT_INVALID", 502)
    return cast(list[str], value)
