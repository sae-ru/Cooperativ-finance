"""Signed offer discovery, deterministic landed cost, and compensating reservation saga."""

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.federation.application.common import (
    FederationCommandResult,
    audit_federation_action,
    begin_federation_command,
    complete_federation_command,
    federation_actor,
)
from cooperative_clearing.modules.federation.domain.discovery import (
    CostStatus,
    FreshnessStatus,
    PurchaseIntentStatus,
    SearchMode,
    bounded_reservation_expiry,
    calculate_landed_cost,
    ensure_reservable,
    exact_discovery_amount,
    freshness_status,
    ranking_key,
)
from cooperative_clearing.modules.federation.domain.types import federation_error, normalize_code
from cooperative_clearing.modules.federation.infrastructure.discovery_models import (
    FederatedOffer,
    LogisticsQuote,
    OfferIndexSnapshot,
    PurchaseIntent,
    ReservationReceipt,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    NodeCertificate,
    NodeTrustContract,
)
from cooperative_clearing.modules.federation.infrastructure.reservation_models import (
    PeerResourceReservation,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    Membership,
    RoleAssignment,
    UserAccount,
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
from cooperative_clearing.modules.risk.application.antifraud_enforcement import (
    require_antifraud_action_allowed,
)
from cooperative_clearing.modules.risk.domain.types import AntifraudSubjectType
from cooperative_clearing.shared.core.config import Environment, Settings
from cooperative_clearing.shared.domain.errors import DomainError

OFFER_PUBLISH_ROLES = {RoleCode.EXCHANGE_PARTICIPANT, RoleCode.NODE_BUSINESS_OPERATOR}
QUOTE_PUBLISH_ROLES = {RoleCode.LOGISTICS_OPERATOR, RoleCode.NODE_BUSINESS_OPERATOR}
PURCHASE_CREATE_ROLES = {RoleCode.EXCHANGE_PARTICIPANT, RoleCode.NODE_BUSINESS_OPERATOR}


@dataclass(frozen=True, slots=True)
class ArtifactVerification:
    valid: bool
    freshness: FreshnessStatus
    home_node_code: str
    signer_fingerprint: str
    valid_until: datetime


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    offer: FederatedOffer
    quote: LogisticsQuote | None
    freshness: FreshnessStatus
    signature_verified: bool
    goods_cost: Decimal
    logistics_cost: Decimal | None
    mandatory_cost: Decimal | None
    landed_cost: Decimal | None
    cost_status: CostStatus | None


@dataclass(frozen=True, slots=True)
class RemoteReservationPlan:
    external_node_id: UUID
    kind: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class RemoteReceiptActionPlan:
    receipt_id: UUID
    external_node_id: UUID
    kind: str
    payload: dict[str, object]


class DiscoveryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.journal = SignedJournalService(settings)

    async def publish_offer(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        offer_id: UUID,
        offer_version: int,
        external_node_id: UUID | None,
        seller_ref: str,
        product_code: str,
        description: str,
        quality_grade: str,
        certificate_refs: list[str],
        quantity_available: Decimal,
        quantity_is_band: bool,
        unit_code: str,
        unit_scale: int,
        minimum_batch: Decimal,
        divisible: bool,
        origin_region: str,
        origin_precision: str,
        pickup_address_text: str | None = None,
        pickup_contact_name: str | None = None,
        pickup_contact_phone: str | None = None,
        pickup_instructions: str | None = None,
        availability_from: datetime,
        availability_until: datetime,
        fulfillment_deadline: datetime,
        unit_price: Decimal,
        mandatory_fee_per_unit: Decimal,
        valuation_unit: str,
        price_policy_version: str,
        handling_requirements: dict[str, object],
        counterparty_policy: dict[str, object],
        geography_policy: dict[str, object],
        guarantee_terms: dict[str, object],
        source_mode: SearchMode,
        node_sequence: int,
        signed_at: datetime,
        valid_until: datetime,
        external_signature: bytes | None,
        idempotency_key: str,
        request_id: UUID | None,
        trusted_import: bool = False,
    ) -> FederationCommandResult:
        if trusted_import and external_node_id is None:
            raise federation_error("TRUSTED_IMPORT_REQUIRES_EXTERNAL_NODE", 422)
        actor = (
            await self._member_actor(session, principal)
            if trusted_import
            else await federation_actor(session, principal, OFFER_PUBLISH_ROLES)
        )
        cooperative_id = await self._cooperative_id_for_actor(session, actor)
        quantity = exact_discovery_amount(quantity_available)
        minimum = exact_discovery_amount(minimum_batch)
        price = exact_discovery_amount(unit_price, allow_zero=True)
        fee = exact_discovery_amount(mandatory_fee_per_unit, allow_zero=True)
        normalized_unit = normalize_code(unit_code, 32)
        normalized_product = normalize_code(product_code, 80)
        normalized_valuation = normalize_code(valuation_unit, 32)
        if offer_version < 1 or node_sequence < 1 or unit_scale not in range(13):
            raise federation_error("OFFER_VERSION_INVALID", 422)
        self._ensure_scale(quantity, unit_scale)
        self._ensure_scale(minimum, unit_scale)
        if minimum > quantity or (not divisible and minimum != quantity):
            raise federation_error("OFFER_BATCH_INVALID", 422)
        if availability_until <= availability_from or valid_until <= signed_at:
            raise federation_error("OFFER_PERIOD_INVALID", 422)
        if fulfillment_deadline < availability_from:
            raise federation_error("OFFER_FULFILLMENT_INVALID", 422)
        if source_mode is SearchMode.CACHED_OFFLINE and external_node_id is None:
            raise federation_error("OFFER_SOURCE_INVALID", 422)
        home_node_code, signature, fingerprint = await self._artifact_signature(
            session,
            external_node_id=external_node_id,
            capability="CATALOG",
            payload_builder=lambda node_code: self._offer_payload(
                offer_id=offer_id,
                offer_version=offer_version,
                home_node_code=node_code,
                seller_ref=self._bounded_text(seller_ref, 160),
                product_code=normalized_product,
                description=self._bounded_text(description, 2000),
                quality_grade=self._bounded_text(quality_grade, 80),
                certificate_refs=self._bounded_refs(certificate_refs),
                quantity_available=quantity,
                quantity_is_band=quantity_is_band,
                unit_code=normalized_unit,
                unit_scale=unit_scale,
                minimum_batch=minimum,
                divisible=divisible,
                origin_region=self._bounded_text(origin_region, 200),
                origin_precision=origin_precision,
                availability_from=availability_from,
                availability_until=availability_until,
                fulfillment_deadline=fulfillment_deadline,
                unit_price=price,
                mandatory_fee_per_unit=fee,
                valuation_unit=normalized_valuation,
                price_policy_version=self._bounded_text(price_policy_version, 80),
                handling_requirements=handling_requirements,
                counterparty_policy=counterparty_policy,
                geography_policy=geography_policy,
                guarantee_terms=guarantee_terms,
                source_mode=source_mode,
                node_sequence=node_sequence,
                signed_at=signed_at,
                valid_until=valid_until,
            ),
            external_signature=external_signature,
        )
        payload = self._offer_payload(
            offer_id=offer_id,
            offer_version=offer_version,
            home_node_code=home_node_code,
            seller_ref=self._bounded_text(seller_ref, 160),
            product_code=normalized_product,
            description=self._bounded_text(description, 2000),
            quality_grade=self._bounded_text(quality_grade, 80),
            certificate_refs=self._bounded_refs(certificate_refs),
            quantity_available=quantity,
            quantity_is_band=quantity_is_band,
            unit_code=normalized_unit,
            unit_scale=unit_scale,
            minimum_batch=minimum,
            divisible=divisible,
            origin_region=self._bounded_text(origin_region, 200),
            origin_precision=origin_precision,
            availability_from=availability_from,
            availability_until=availability_until,
            fulfillment_deadline=fulfillment_deadline,
            unit_price=price,
            mandatory_fee_per_unit=fee,
            valuation_unit=normalized_valuation,
            price_policy_version=self._bounded_text(price_policy_version, 80),
            handling_requirements=handling_requirements,
            counterparty_policy=counterparty_policy,
            geography_policy=geography_policy,
            guarantee_terms=guarantee_terms,
            source_mode=source_mode,
            node_sequence=node_sequence,
            signed_at=signed_at,
            valid_until=valid_until,
        )
        command_payload = {
            **payload,
            "cooperative_id": str(cooperative_id),
            "payload_hash": payload_hash(payload),
        }
        record, replay = await begin_federation_command(
            session, principal, "FEDERATION_PUBLISH_OFFER", idempotency_key, command_payload
        )
        if replay is not None:
            return replay
        if not trusted_import:
            await require_antifraud_action_allowed(
                session,
                cooperative_id=cooperative_id,
                subjects=(
                    (AntifraudSubjectType.MEMBER, actor.person_id),
                    (AntifraudSubjectType.OFFER, offer_id),
                ),
            )
        previous = (
            await session.execute(
                select(FederatedOffer)
                .where(FederatedOffer.offer_id == offer_id)
                .order_by(FederatedOffer.offer_version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if previous is not None and (
            previous.cooperative_id != cooperative_id
            or previous.home_node_code != home_node_code
            or offer_version != previous.offer_version + 1
            or node_sequence <= previous.node_sequence
        ):
            raise federation_error("OFFER_VERSION_CONFLICT")
        if previous is None and offer_version != 1:
            raise federation_error("OFFER_VERSION_CONFLICT")
        row_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="federation.offer_published",
            aggregate_type="federated_offer",
            aggregate_id=offer_id,
            aggregate_version=offer_version,
            actor=actor,
            payload={**command_payload, "offer_record_id": str(row_id)},
        )
        offer_row = FederatedOffer(
            id=row_id,
            cooperative_id=cooperative_id,
            offer_id=offer_id,
            offer_version=offer_version,
            external_node_id=external_node_id,
            home_node_code=home_node_code,
            seller_ref=str(payload["seller_ref"]),
            product_code=normalized_product,
            description=str(payload["description"]),
            quality_grade=str(payload["quality_grade"]),
            certificate_refs=certificate_refs,
            quantity_available=quantity,
            quantity_is_band=quantity_is_band,
            unit_code=normalized_unit,
            unit_scale=unit_scale,
            minimum_batch=minimum,
            divisible=divisible,
            origin_region=str(payload["origin_region"]),
            origin_precision=origin_precision,
            pickup_address_text=(
                self._bounded_text(pickup_address_text, 500)
                if external_node_id is None and pickup_address_text
                else None
            ),
            pickup_contact_name=(
                self._bounded_text(pickup_contact_name, 200)
                if external_node_id is None and pickup_contact_name
                else None
            ),
            pickup_contact_phone=(
                self._bounded_text(pickup_contact_phone, 80)
                if external_node_id is None and pickup_contact_phone
                else None
            ),
            pickup_instructions=(
                self._bounded_text(pickup_instructions, 2000)
                if external_node_id is None and pickup_instructions
                else None
            ),
            availability_from=availability_from,
            availability_until=availability_until,
            fulfillment_deadline=fulfillment_deadline,
            unit_price=price,
            mandatory_fee_per_unit=fee,
            valuation_unit=normalized_valuation,
            price_policy_version=str(payload["price_policy_version"]),
            handling_requirements=handling_requirements,
            counterparty_policy=counterparty_policy,
            geography_policy=geography_policy,
            guarantee_terms=guarantee_terms,
            source_mode="LOCAL" if external_node_id is None else source_mode.value,
            status="ACTIVE",
            node_sequence=node_sequence,
            signed_at=signed_at,
            valid_until=valid_until,
            payload=payload,
            payload_hash=str(command_payload["payload_hash"]),
            node_signature=signature,
            signer_fingerprint=fingerprint,
            publisher_member_id=actor.person_id,
            publisher_role_assignment_id=actor.role_assignment_id,
            published_event_id=event.event_id,
        )
        session.add(offer_row)
        if (
            external_node_id is None
            and str(handling_requirements.get("offer_kind", "")).upper() == "SERVICE"
        ):
            await self._add_service_fulfillment_quote(
                session,
                principal=principal,
                actor=actor,
                offer=offer_row,
                request_id=request_id,
            )
        await audit_federation_action(
            session,
            principal,
            "FEDERATED_OFFER_PUBLISHED",
            "FederatedOffer",
            row_id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, row_id)

    async def _add_service_fulfillment_quote(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        actor: ActorClaim,
        offer: FederatedOffer,
        request_id: UUID | None,
    ) -> None:
        quote_id = uuid4()
        row_id = uuid4()
        route_legs: list[object] = [
            {
                "kind": "SERVICE_FULFILLMENT",
                "region": offer.origin_region,
            }
        ]
        route_payload = {
            "origin_region": offer.origin_region,
            "destination_region": offer.origin_region,
            "capacity": self._decimal(offer.quantity_available),
            "unit_code": offer.unit_code,
            "route_legs": route_legs,
        }
        route_hash = payload_hash(route_payload)
        assumptions = ["NO_PHYSICAL_LOGISTICS_REQUIRED"]

        def build(node_code: str) -> dict[str, object]:
            return {
                "quote_id": str(quote_id),
                "quote_version": 1,
                "offer_id": str(offer.offer_id),
                "offer_version": offer.offer_version,
                "home_node_code": node_code,
                "carrier_ref": self._bounded_text(offer.seller_ref, 160),
                "route_request_hash": route_hash,
                **route_payload,
                "custody_transfers": 0,
                "cost_components": {},
                "valuation_unit": offer.valuation_unit,
                "cost_status": CostStatus.CONFIRMED.value,
                "delivery_from": utc_timestamp(offer.availability_from),
                "delivery_until": utc_timestamp(offer.fulfillment_deadline),
                "liability_limit": "0",
                "bond_ref": None,
                "assumptions": assumptions,
                "signed_at": utc_timestamp(offer.signed_at),
                "valid_until": utc_timestamp(offer.valid_until),
            }

        home_node_code, signature, fingerprint = await self._artifact_signature(
            session,
            external_node_id=None,
            capability="LOGISTICS",
            payload_builder=build,
            external_signature=None,
        )
        payload = build(home_node_code)
        event = await self.journal.append(
            session,
            event_type="federation.service_fulfillment_quote_issued",
            aggregate_type="logistics_quote",
            aggregate_id=quote_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "quote_record_id": str(row_id)},
        )
        session.add(
            LogisticsQuote(
                id=row_id,
                cooperative_id=offer.cooperative_id,
                quote_id=quote_id,
                quote_version=1,
                offer_record_id=offer.id,
                external_node_id=None,
                home_node_code=home_node_code,
                carrier_ref=str(payload["carrier_ref"]),
                route_request_hash=route_hash,
                origin_region=offer.origin_region,
                destination_region=offer.origin_region,
                route_legs=route_legs,
                custody_transfers=0,
                capacity=offer.quantity_available,
                unit_code=offer.unit_code,
                cost_components={},
                valuation_unit=offer.valuation_unit,
                cost_status=CostStatus.CONFIRMED.value,
                delivery_from=offer.availability_from,
                delivery_until=offer.fulfillment_deadline,
                liability_limit=Decimal("0"),
                bond_ref=None,
                assumptions=assumptions,
                status="ACTIVE",
                signed_at=offer.signed_at,
                valid_until=offer.valid_until,
                payload=payload,
                payload_hash=payload_hash(payload),
                node_signature=signature,
                signer_fingerprint=fingerprint,
                issued_event_id=event.event_id,
            )
        )
        await audit_federation_action(
            session,
            principal,
            "SERVICE_FULFILLMENT_QUOTE_ISSUED",
            "LogisticsQuote",
            row_id,
            event.event_id,
            request_id,
        )

    async def revoke_offer(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        offer_id: UUID,
        expected_version: int,
        reason: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, OFFER_PUBLISH_ROLES)
        offer = (
            await session.execute(
                select(FederatedOffer)
                .where(FederatedOffer.offer_id == offer_id)
                .order_by(FederatedOffer.offer_version.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if offer is None:
            raise federation_error("OFFER_NOT_FOUND", 404)
        may_manage_node_catalog = any(
            grant.role is RoleCode.NODE_BUSINESS_OPERATOR for grant in principal.roles
        )
        if offer.publisher_member_id != principal.member_id and not may_manage_node_catalog:
            raise federation_error("OFFER_PUBLISHER_MISMATCH", 403)
        payload = {
            "offer_id": str(offer_id),
            "offer_version": offer.offer_version,
            "expected_version": expected_version,
            "reason": self._bounded_text(reason, 1000),
        }
        record, replay = await begin_federation_command(
            session, principal, "FEDERATION_REVOKE_OFFER", idempotency_key, payload
        )
        if replay is not None:
            return replay
        if offer.offer_version != expected_version or offer.status != "ACTIVE":
            raise federation_error("OFFER_VERSION_CONFLICT")
        event = await self.journal.append(
            session,
            event_type="federation.offer_revoked",
            aggregate_type="federated_offer",
            aggregate_id=offer.offer_id,
            aggregate_version=offer.offer_version + 1,
            actor=actor,
            payload=payload,
        )
        offer.status = "REVOKED"
        offer.revoked_event_id = event.event_id
        offer.updated_at = datetime.now(UTC)
        await audit_federation_action(
            session,
            principal,
            "FEDERATED_OFFER_REVOKED",
            "FederatedOffer",
            offer.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, offer.id)

    async def publish_offer_index(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        external_node_id: UUID | None,
        source_mode: SearchMode,
        node_sequence: int,
        ordered_offer_hashes: list[str],
        signed_at: datetime,
        valid_until: datetime,
        external_signature: bytes | None,
        idempotency_key: str,
        request_id: UUID | None,
        trusted_import: bool = False,
    ) -> FederationCommandResult:
        if trusted_import and external_node_id is None:
            raise federation_error("TRUSTED_IMPORT_REQUIRES_EXTERNAL_NODE", 422)
        actor = (
            await self._member_actor(session, principal)
            if trusted_import
            else await federation_actor(session, principal, OFFER_PUBLISH_ROLES)
        )
        if source_mode not in {SearchMode.INDEXED, SearchMode.CACHED_OFFLINE}:
            raise federation_error("OFFER_INDEX_MODE_INVALID", 422)
        if node_sequence < 1 or valid_until <= signed_at:
            raise federation_error("OFFER_INDEX_PERIOD_INVALID", 422)
        hashes = self._ordered_hashes(ordered_offer_hashes)

        def build(node_code: str) -> dict[str, object]:
            base: dict[str, object] = {
                "home_node_code": node_code,
                "source_mode": source_mode.value,
                "node_sequence": node_sequence,
                "ordered_offer_hashes": hashes,
                "signed_at": utc_timestamp(signed_at),
                "valid_until": utc_timestamp(valid_until),
            }
            return {**base, "checkpoint_hash": payload_hash(base)}

        home_node_code, signature, fingerprint = await self._artifact_signature(
            session,
            external_node_id=external_node_id,
            capability="CATALOG",
            payload_builder=build,
            external_signature=external_signature,
        )
        payload = build(home_node_code)
        record, replay = await begin_federation_command(
            session,
            principal,
            "FEDERATION_PUBLISH_OFFER_INDEX",
            idempotency_key,
            payload,
        )
        if replay is not None:
            return replay
        previous = (
            await session.execute(
                select(OfferIndexSnapshot)
                .where(OfferIndexSnapshot.home_node_code == home_node_code)
                .order_by(OfferIndexSnapshot.node_sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        expected_sequence = 1 if previous is None else previous.node_sequence + 1
        if node_sequence != expected_sequence:
            raise federation_error("OFFER_INDEX_SEQUENCE_CONFLICT")
        snapshot_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="federation.offer_index_published",
            aggregate_type="offer_index_snapshot",
            aggregate_id=snapshot_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "snapshot_id": str(snapshot_id)},
        )
        session.add(
            OfferIndexSnapshot(
                id=snapshot_id,
                external_node_id=external_node_id,
                home_node_code=home_node_code,
                source_mode=source_mode.value,
                node_sequence=node_sequence,
                ordered_offer_hashes=hashes,
                checkpoint_hash=str(payload["checkpoint_hash"]),
                signed_at=signed_at,
                valid_until=valid_until,
                node_signature=signature,
                signer_fingerprint=fingerprint,
                recorded_event_id=event.event_id,
            )
        )
        await audit_federation_action(
            session,
            principal,
            "OFFER_INDEX_PUBLISHED",
            "OfferIndexSnapshot",
            snapshot_id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, snapshot_id)

    async def verify_offer_index(
        self,
        session: AsyncSession,
        snapshot: OfferIndexSnapshot,
        *,
        maximum_age_seconds: int,
    ) -> ArtifactVerification:
        trusted, public_key = await self._verification_material(
            session,
            snapshot.external_node_id,
            snapshot.signer_fingerprint,
            "CATALOG",
        )
        payload: dict[str, object] = {
            "home_node_code": snapshot.home_node_code,
            "source_mode": snapshot.source_mode,
            "node_sequence": snapshot.node_sequence,
            "ordered_offer_hashes": snapshot.ordered_offer_hashes,
            "signed_at": utc_timestamp(snapshot.signed_at),
            "valid_until": utc_timestamp(snapshot.valid_until),
            "checkpoint_hash": snapshot.checkpoint_hash,
        }
        checkpoint_base = dict(payload)
        del checkpoint_base["checkpoint_hash"]
        checkpoint_valid = payload_hash(checkpoint_base) == snapshot.checkpoint_hash
        valid = (
            checkpoint_valid
            and public_key is not None
            and verify_signature(public_key, snapshot.node_signature, canonicalize(payload))
        )
        status = freshness_status(
            now=datetime.now(UTC),
            valid_until=snapshot.valid_until,
            signed_at=snapshot.signed_at,
            maximum_age_seconds=maximum_age_seconds,
            trusted=trusted and valid,
            revoked=False,
            live_verified_at=(
                datetime.now(UTC)
                if valid and trusted and snapshot.external_node_id is None
                else None
            ),
        )
        return ArtifactVerification(
            valid=valid,
            freshness=status,
            home_node_code=snapshot.home_node_code,
            signer_fingerprint=snapshot.signer_fingerprint,
            valid_until=snapshot.valid_until,
        )

    async def issue_logistics_quote(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        quote_id: UUID,
        quote_version: int,
        offer_record_id: UUID,
        external_node_id: UUID | None,
        carrier_ref: str,
        destination_region: str,
        route_legs: list[object],
        custody_transfers: int,
        capacity: Decimal,
        cost_components: dict[str, Decimal],
        cost_status: CostStatus,
        delivery_from: datetime,
        delivery_until: datetime,
        liability_limit: Decimal,
        bond_ref: str | None,
        assumptions: list[str],
        signed_at: datetime,
        valid_until: datetime,
        external_signature: bytes | None,
        idempotency_key: str,
        request_id: UUID | None,
        trusted_import: bool = False,
    ) -> FederationCommandResult:
        if trusted_import and external_node_id is None:
            raise federation_error("TRUSTED_IMPORT_REQUIRES_EXTERNAL_NODE", 422)
        actor = (
            await self._member_actor(session, principal)
            if trusted_import
            else await federation_actor(session, principal, QUOTE_PUBLISH_ROLES)
        )
        cooperative_id = await self._cooperative_id_for_actor(session, actor)
        offer = await session.get(FederatedOffer, offer_record_id)
        if offer is None:
            raise federation_error("OFFER_NOT_FOUND", 404)
        quantity = exact_discovery_amount(capacity)
        liability = exact_discovery_amount(liability_limit, allow_zero=True)
        normalized_components = {
            key: exact_discovery_amount(value, allow_zero=True)
            for key, value in cost_components.items()
        }
        calculate_landed_cost(
            quantity=Decimal(1),
            unit_price=Decimal(0),
            mandatory_fee_per_unit=Decimal(0),
            quote_components=normalized_components,
            quote_status=cost_status,
        )
        if quote_version < 1 or custody_transfers < 0 or delivery_until < delivery_from:
            raise federation_error("LOGISTICS_QUOTE_INVALID", 422)
        if valid_until <= signed_at:
            raise federation_error("LOGISTICS_QUOTE_PERIOD_INVALID", 422)
        destination = self._bounded_text(destination_region, 200)
        route_payload = {
            "origin_region": offer.origin_region,
            "destination_region": destination,
            "capacity": self._decimal(quantity),
            "unit_code": offer.unit_code,
            "route_legs": route_legs,
        }
        route_hash = payload_hash(route_payload)

        def build(node_code: str) -> dict[str, object]:
            return {
                "quote_id": str(quote_id),
                "quote_version": quote_version,
                "offer_id": str(offer.offer_id),
                "offer_version": offer.offer_version,
                "home_node_code": node_code,
                "carrier_ref": self._bounded_text(carrier_ref, 160),
                "route_request_hash": route_hash,
                **route_payload,
                "custody_transfers": custody_transfers,
                "cost_components": {
                    key: self._decimal(value)
                    for key, value in sorted(normalized_components.items())
                },
                "valuation_unit": offer.valuation_unit,
                "cost_status": cost_status.value,
                "delivery_from": utc_timestamp(delivery_from),
                "delivery_until": utc_timestamp(delivery_until),
                "liability_limit": self._decimal(liability),
                "bond_ref": self._bounded_text(bond_ref, 160) if bond_ref else None,
                "assumptions": self._bounded_refs(assumptions),
                "signed_at": utc_timestamp(signed_at),
                "valid_until": utc_timestamp(valid_until),
            }

        home_node_code, signature, fingerprint = await self._artifact_signature(
            session,
            external_node_id=external_node_id,
            capability="LOGISTICS",
            payload_builder=build,
            external_signature=external_signature,
        )
        payload = build(home_node_code)
        command_payload = {
            **payload,
            "cooperative_id": str(cooperative_id),
            "payload_hash": payload_hash(payload),
        }
        record, replay = await begin_federation_command(
            session,
            principal,
            "FEDERATION_ISSUE_LOGISTICS_QUOTE",
            idempotency_key,
            command_payload,
        )
        if replay is not None:
            return replay
        if not trusted_import:
            await require_antifraud_action_allowed(
                session,
                cooperative_id=cooperative_id,
                subjects=(
                    (AntifraudSubjectType.MEMBER, actor.person_id),
                    (AntifraudSubjectType.LOGISTICS_QUOTE, quote_id),
                ),
            )
            await require_antifraud_action_allowed(
                session,
                cooperative_id=offer.cooperative_id,
                subjects=((AntifraudSubjectType.OFFER, offer.offer_id),),
            )
        previous = (
            await session.execute(
                select(LogisticsQuote)
                .where(LogisticsQuote.quote_id == quote_id)
                .order_by(LogisticsQuote.quote_version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if (previous is None and quote_version != 1) or (
            previous is not None
            and (
                previous.cooperative_id != cooperative_id
                or quote_version != previous.quote_version + 1
            )
        ):
            raise federation_error("LOGISTICS_QUOTE_VERSION_CONFLICT")
        row_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="federation.logistics_quote_issued",
            aggregate_type="logistics_quote",
            aggregate_id=quote_id,
            aggregate_version=quote_version,
            actor=actor,
            payload={**command_payload, "quote_record_id": str(row_id)},
        )
        session.add(
            LogisticsQuote(
                id=row_id,
                cooperative_id=cooperative_id,
                quote_id=quote_id,
                quote_version=quote_version,
                offer_record_id=offer.id,
                external_node_id=external_node_id,
                home_node_code=home_node_code,
                carrier_ref=str(payload["carrier_ref"]),
                route_request_hash=route_hash,
                origin_region=offer.origin_region,
                destination_region=destination,
                route_legs=route_legs,
                custody_transfers=custody_transfers,
                capacity=quantity,
                unit_code=offer.unit_code,
                cost_components=payload["cost_components"],
                valuation_unit=offer.valuation_unit,
                cost_status=cost_status.value,
                delivery_from=delivery_from,
                delivery_until=delivery_until,
                liability_limit=liability,
                bond_ref=bond_ref,
                assumptions=assumptions,
                status="ACTIVE",
                signed_at=signed_at,
                valid_until=valid_until,
                payload=payload,
                payload_hash=str(command_payload["payload_hash"]),
                node_signature=signature,
                signer_fingerprint=fingerprint,
                issued_event_id=event.event_id,
            )
        )
        await audit_federation_action(
            session,
            principal,
            "LOGISTICS_QUOTE_ISSUED",
            "LogisticsQuote",
            row_id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, row_id)

    async def verify_offer(
        self, session: AsyncSession, offer: FederatedOffer, *, live: bool, maximum_age_seconds: int
    ) -> ArtifactVerification:
        trusted, public_key = await self._verification_material(
            session, offer.external_node_id, offer.signer_fingerprint, "CATALOG"
        )
        valid = public_key is not None and verify_signature(
            public_key, offer.node_signature, canonicalize(offer.payload)
        )
        status = freshness_status(
            now=datetime.now(UTC),
            valid_until=offer.valid_until,
            signed_at=offer.signed_at,
            maximum_age_seconds=maximum_age_seconds,
            trusted=trusted and valid,
            revoked=offer.status != "ACTIVE",
            live_verified_at=(
                offer.last_verified_at
                if live and valid and trusted and offer.external_node_id is not None
                else (datetime.now(UTC) if live and valid and trusted else None)
            ),
        )
        return ArtifactVerification(
            valid=valid,
            freshness=status,
            home_node_code=offer.home_node_code,
            signer_fingerprint=offer.signer_fingerprint,
            valid_until=offer.valid_until,
        )

    async def verify_quote(
        self, session: AsyncSession, quote: LogisticsQuote
    ) -> ArtifactVerification:
        trusted, public_key = await self._verification_material(
            session, quote.external_node_id, quote.signer_fingerprint, "LOGISTICS"
        )
        valid = public_key is not None and verify_signature(
            public_key, quote.node_signature, canonicalize(quote.payload)
        )
        status = freshness_status(
            now=datetime.now(UTC),
            valid_until=quote.valid_until,
            signed_at=quote.signed_at,
            maximum_age_seconds=max(1, int((quote.valid_until - quote.signed_at).total_seconds())),
            trusted=trusted and valid,
            revoked=quote.status != "ACTIVE",
            live_verified_at=(
                quote.last_verified_at
                if valid and trusted and quote.external_node_id is not None
                else (datetime.now(UTC) if valid and trusted else None)
            ),
        )
        return ArtifactVerification(
            valid=valid,
            freshness=status,
            home_node_code=quote.home_node_code,
            signer_fingerprint=quote.signer_fingerprint,
            valid_until=quote.valid_until,
        )

    async def search(
        self,
        session: AsyncSession,
        *,
        mode: SearchMode,
        product_code: str,
        quantity: Decimal,
        unit_code: str,
        valuation_unit: str,
        destination_region: str,
        maximum_age_seconds: int,
        trusted_node_codes: list[str],
        required_certificates: list[str],
        quality_minimum: str | None,
        maximum_goods_cost: Decimal | None,
        maximum_landed_cost: Decimal | None,
        latest_delivery: datetime | None,
        top_k: int,
    ) -> list[SearchCandidate]:
        requested = exact_discovery_amount(quantity)
        product = normalize_code(product_code, 80)
        unit = normalize_code(unit_code, 32)
        valuation = normalize_code(valuation_unit, 32)
        maximum_goods = (
            exact_discovery_amount(maximum_goods_cost, allow_zero=True)
            if maximum_goods_cost is not None
            else None
        )
        now = datetime.now(UTC)
        offers = list(
            (
                await session.execute(
                    select(FederatedOffer).where(
                        FederatedOffer.product_code == product,
                        FederatedOffer.unit_code == unit,
                        FederatedOffer.valuation_unit == valuation,
                        FederatedOffer.status == "ACTIVE",
                        FederatedOffer.availability_from <= now,
                        FederatedOffer.availability_until > now,
                        FederatedOffer.valid_until > now,
                        FederatedOffer.quantity_available >= requested,
                        FederatedOffer.minimum_batch <= requested,
                    )
                )
            ).scalars()
        )
        latest: dict[UUID, FederatedOffer] = {}
        for offer in offers:
            current = latest.get(offer.offer_id)
            if current is None or offer.offer_version > current.offer_version:
                latest[offer.offer_id] = offer
        candidates: list[SearchCandidate] = []
        for offer in latest.values():
            if trusted_node_codes and offer.home_node_code not in trusted_node_codes:
                continue
            if required_certificates and not set(required_certificates).issubset(
                offer.certificate_refs
            ):
                continue
            # Quality grades have no universal ordering. EXACT-V1 only accepts
            # an identical grade until a versioned federation mapping is approved.
            if quality_minimum and offer.quality_grade != quality_minimum.strip():
                continue
            if not offer.divisible and requested != offer.quantity_available:
                continue
            if maximum_goods is not None and requested * offer.unit_price > maximum_goods:
                continue
            if latest_delivery is not None and offer.fulfillment_deadline > latest_delivery:
                continue
            verification = await self.verify_offer(
                session,
                offer,
                live=mode is SearchMode.DIRECT,
                maximum_age_seconds=maximum_age_seconds,
            )
            effective_freshness = verification.freshness
            if verification.freshness is FreshnessStatus.REVOKED_OR_UNTRUSTED:
                continue
            if mode is not SearchMode.DIRECT and offer.external_node_id is not None:
                snapshot = (
                    await session.execute(
                        select(OfferIndexSnapshot)
                        .where(
                            OfferIndexSnapshot.external_node_id == offer.external_node_id,
                            OfferIndexSnapshot.source_mode == mode.value,
                        )
                        .order_by(OfferIndexSnapshot.node_sequence.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if snapshot is None or offer.payload_hash not in snapshot.ordered_offer_hashes:
                    continue
                index_verification = await self.verify_offer_index(
                    session,
                    snapshot,
                    maximum_age_seconds=maximum_age_seconds,
                )
                if (
                    not index_verification.valid
                    or index_verification.freshness is FreshnessStatus.REVOKED_OR_UNTRUSTED
                ):
                    continue
                if index_verification.freshness is FreshnessStatus.STALE:
                    effective_freshness = FreshnessStatus.STALE
            quotes = list(
                (
                    await session.execute(
                        select(LogisticsQuote).where(
                            LogisticsQuote.offer_record_id == offer.id,
                            LogisticsQuote.destination_region == destination_region,
                            LogisticsQuote.unit_code == unit,
                            LogisticsQuote.capacity >= requested,
                            LogisticsQuote.status == "ACTIVE",
                        )
                    )
                ).scalars()
            )
            priced: list[tuple[LogisticsQuote, Decimal, Decimal, Decimal, CostStatus]] = []
            for quote in quotes:
                if latest_delivery is not None and quote.delivery_until > latest_delivery:
                    continue
                quote_verification = await self.verify_quote(session, quote)
                if (
                    not quote_verification.valid
                    or quote_verification.freshness is FreshnessStatus.STALE
                ):
                    continue
                components = {
                    key: Decimal(str(value)) for key, value in quote.cost_components.items()
                }
                cost = calculate_landed_cost(
                    quantity=requested,
                    unit_price=offer.unit_price,
                    mandatory_fee_per_unit=offer.mandatory_fee_per_unit,
                    quote_components=components,
                    quote_status=CostStatus(quote.cost_status),
                )
                priced.append(
                    (quote, cost.logistics_cost, cost.mandatory_cost, cost.landed_cost, cost.status)
                )
            if priced:
                priced.sort(
                    key=lambda item: ranking_key(
                        cost_status=item[4],
                        landed_cost=item[3],
                        delivery_at=item[0].delivery_until,
                        signed_at=offer.signed_at,
                        offer_id=offer.offer_id,
                    )
                )
                quote, logistics, mandatory, landed, status = priced[0]
                if maximum_landed_cost is not None and landed > maximum_landed_cost:
                    continue
                candidates.append(
                    SearchCandidate(
                        offer=offer,
                        quote=quote,
                        freshness=effective_freshness,
                        signature_verified=verification.valid,
                        goods_cost=requested * offer.unit_price,
                        logistics_cost=logistics,
                        mandatory_cost=mandatory,
                        landed_cost=landed,
                        cost_status=status,
                    )
                )
            elif maximum_landed_cost is None:
                candidates.append(
                    SearchCandidate(
                        offer=offer,
                        quote=None,
                        freshness=effective_freshness,
                        signature_verified=verification.valid,
                        goods_cost=requested * offer.unit_price,
                        logistics_cost=None,
                        mandatory_cost=None,
                        landed_cost=None,
                        cost_status=None,
                    )
                )
        candidates.sort(
            key=lambda item: (
                2
                if item.cost_status is None
                else (0 if item.cost_status is CostStatus.CONFIRMED else 1),
                item.landed_cost if item.landed_cost is not None else Decimal("Infinity"),
                item.quote.delivery_until if item.quote else item.offer.fulfillment_deadline,
                str(item.offer.offer_id),
            )
        )
        return candidates[:top_k]

    async def create_purchase_intent(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        offer_record_id: UUID,
        quote_record_id: UUID,
        quantity: Decimal,
        destination_region: str,
        delivery_address_text: str | None = None,
        delivery_contact_name: str | None = None,
        delivery_contact_phone: str | None = None,
        delivery_instructions: str | None = None,
        max_landed_cost: Decimal,
        expires_at: datetime,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, PURCHASE_CREATE_ROLES)
        cooperative_id = await self._cooperative_id_for_actor(session, actor)
        offer = await session.get(FederatedOffer, offer_record_id)
        quote = await session.get(LogisticsQuote, quote_record_id)
        if offer is None or quote is None or quote.offer_record_id != offer.id:
            raise federation_error("PURCHASE_SELECTION_INVALID", 404)

        offer_maximum_age_seconds = min(
            604_800,
            max(1, int((offer.valid_until - offer.signed_at).total_seconds())),
        )
        offer_verification = await self.verify_offer(
            session,
            offer,
            live=True,
            maximum_age_seconds=offer_maximum_age_seconds,
        )
        quote_verification = await self.verify_quote(session, quote)
        ensure_reservable(offer_verification.freshness, signature_verified=offer_verification.valid)
        ensure_reservable(quote_verification.freshness, signature_verified=quote_verification.valid)
        requested = exact_discovery_amount(quantity)
        maximum = exact_discovery_amount(max_landed_cost, allow_zero=True)
        if requested < offer.minimum_batch or requested > offer.quantity_available:
            raise federation_error("PURCHASE_QUANTITY_INVALID", 422)
        if requested > quote.capacity:
            raise federation_error("LOGISTICS_CAPACITY_INSUFFICIENT")
        components = {key: Decimal(str(value)) for key, value in quote.cost_components.items()}
        cost = calculate_landed_cost(
            quantity=requested,
            unit_price=offer.unit_price,
            mandatory_fee_per_unit=offer.mandatory_fee_per_unit,
            quote_components=components,
            quote_status=CostStatus(quote.cost_status),
        )
        if cost.landed_cost > maximum:
            raise federation_error("MAXIMUM_LANDED_COST_EXCEEDED")
        now = datetime.now(UTC)
        expiry = bounded_reservation_expiry(
            now=now,
            requested=expires_at,
            bounds=(offer.valid_until, quote.valid_until),
        )
        breakdown = {
            "goods_cost": self._decimal(cost.goods_cost),
            "logistics_cost": self._decimal(cost.logistics_cost),
            "mandatory_cost": self._decimal(cost.mandatory_cost),
            "landed_cost": self._decimal(cost.landed_cost),
            "components": {key: self._decimal(value) for key, value in cost.components.items()},
        }
        summary = {
            "cooperative_id": str(cooperative_id),
            "offer_id": str(offer.offer_id),
            "offer_version": offer.offer_version,
            "quote_id": str(quote.quote_id),
            "quote_version": quote.quote_version,
            "quantity": self._decimal(requested),
            "unit_code": offer.unit_code,
            "destination_region": self._bounded_text(destination_region, 200),
            "delivery_address_text": (
                self._bounded_text(delivery_address_text, 500)
                if delivery_address_text
                else None
            ),
            "delivery_contact_name": (
                self._bounded_text(delivery_contact_name, 200)
                if delivery_contact_name
                else None
            ),
            "delivery_contact_phone": (
                self._bounded_text(delivery_contact_phone, 80)
                if delivery_contact_phone
                else None
            ),
            "delivery_instructions": (
                self._bounded_text(delivery_instructions, 2000)
                if delivery_instructions
                else None
            ),
            "landed_cost_breakdown": breakdown,
            "cost_status": cost.status.value,
            "expires_at": utc_timestamp(expiry),
        }
        summary_hash = payload_hash(summary)
        command_payload = {**summary, "summary_hash": summary_hash}
        record, replay = await begin_federation_command(
            session,
            principal,
            "FEDERATION_CREATE_PURCHASE_INTENT",
            idempotency_key,
            command_payload,
        )
        if replay is not None:
            return replay
        await require_antifraud_action_allowed(
            session,
            cooperative_id=cooperative_id,
            subjects=((AntifraudSubjectType.MEMBER, actor.person_id),),
        )
        await require_antifraud_action_allowed(
            session,
            cooperative_id=offer.cooperative_id,
            subjects=((AntifraudSubjectType.OFFER, offer.offer_id),),
        )
        await require_antifraud_action_allowed(
            session,
            cooperative_id=quote.cooperative_id,
            subjects=((AntifraudSubjectType.LOGISTICS_QUOTE, quote.quote_id),),
        )
        intent_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="federation.purchase_intent_created",
            aggregate_type="purchase_intent",
            aggregate_id=intent_id,
            aggregate_version=1,
            actor=actor,
            payload={**command_payload, "purchase_intent_id": str(intent_id)},
        )
        session.add(
            PurchaseIntent(
                id=intent_id,
                cooperative_id=cooperative_id,
                buyer_node_code=self.settings.node_code,
                buyer_user_id=principal.user_id,
                buyer_member_id=actor.person_id,
                buyer_role_assignment_id=actor.role_assignment_id,
                offer_record_id=offer.id,
                quote_record_id=quote.id,
                quantity=requested,
                unit_code=offer.unit_code,
                destination_region=str(summary["destination_region"]),
                delivery_address_text=summary["delivery_address_text"],
                delivery_contact_name=summary["delivery_contact_name"],
                delivery_contact_phone=summary["delivery_contact_phone"],
                delivery_instructions=summary["delivery_instructions"],
                max_landed_cost=maximum,
                landed_cost_breakdown=breakdown,
                cost_status=cost.status.value,
                summary_hash=summary_hash,
                status=PurchaseIntentStatus.PREPARING.value,
                created_event_id=event.event_id,
                created_at=now,
                expires_at=expiry,
            )
        )
        await audit_federation_action(
            session,
            principal,
            "PURCHASE_INTENT_CREATED",
            "PurchaseIntent",
            intent_id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, intent_id)

    async def remote_reservation_plan(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        intent_id: UUID,
        kind: str,
        receipt_id: UUID,
        expires_at: datetime,
    ) -> RemoteReservationPlan | None:
        intent = await session.get(PurchaseIntent, intent_id)
        if intent is None:
            raise federation_error("PURCHASE_INTENT_NOT_FOUND", 404)
        self._ensure_buyer(intent, principal)
        now = datetime.now(UTC)
        if intent.expires_at <= now:
            raise federation_error("PURCHASE_INTENT_EXPIRED")
        if kind == "GOODS":
            if intent.status != PurchaseIntentStatus.PREPARING.value:
                raise federation_error("PURCHASE_INTENT_STATE_INVALID")
            offer = await session.get(FederatedOffer, intent.offer_record_id)
            if offer is None:
                raise federation_error("OFFER_NOT_FOUND", 404)
            if offer.external_node_id is None:
                return None
            external_node_id = offer.external_node_id
            resource_valid_until = min(offer.availability_until, offer.valid_until)
            resource_fields: dict[str, object] = {
                "offer_id": str(offer.offer_id),
                "offer_version": offer.offer_version,
            }
        elif kind == "LOGISTICS":
            if intent.status != PurchaseIntentStatus.GOODS_RESERVED.value:
                raise federation_error("PURCHASE_INTENT_STATE_INVALID")
            quote = await session.get(LogisticsQuote, intent.quote_record_id)
            if quote is None:
                raise federation_error("LOGISTICS_QUOTE_NOT_FOUND", 404)
            if quote.external_node_id is None:
                return None
            external_node_id = quote.external_node_id
            resource_valid_until = quote.valid_until
            resource_fields = {
                "quote_id": str(quote.quote_id),
                "quote_version": quote.quote_version,
            }
        else:
            raise federation_error("RESERVATION_KIND_INVALID", 422)
        expiry = bounded_reservation_expiry(
            now=now,
            requested=expires_at,
            bounds=(intent.expires_at, resource_valid_until),
        )
        return RemoteReservationPlan(
            external_node_id=external_node_id,
            kind=kind,
            payload={
                "receipt_id": str(receipt_id),
                "purchase_intent_id": str(intent.id),
                "kind": kind,
                **resource_fields,
                "amount": self._decimal(intent.quantity),
                "unit_code": intent.unit_code,
                "requested_expires_at": utc_timestamp(expiry),
                "summary_hash": intent.summary_hash,
            },
        )

    async def reserve(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        intent_id: UUID,
        kind: str,
        receipt_id: UUID,
        expires_at: datetime,
        external_signature: bytes | None,
        external_receipt_payload: dict[str, object] | None,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await self._member_actor(session, principal)
        intent = await session.get(PurchaseIntent, intent_id, with_for_update=True)
        if intent is None:
            raise federation_error("PURCHASE_INTENT_NOT_FOUND", 404)
        self._ensure_buyer(intent, principal)
        now = datetime.now(UTC)
        if intent.expires_at <= now:
            raise federation_error("PURCHASE_INTENT_EXPIRED")
        if kind == "GOODS":
            if intent.status != PurchaseIntentStatus.PREPARING.value:
                raise federation_error("PURCHASE_INTENT_STATE_INVALID")
            offer = await session.get(FederatedOffer, intent.offer_record_id, with_for_update=True)
            if offer is None:
                raise federation_error("OFFER_NOT_FOUND", 404)
            home_node_code = offer.home_node_code
            external_node_id = offer.external_node_id
            resource_ref = f"offer:{offer.offer_id}:{offer.offer_version}"
            available = offer.quantity_available
            resource_valid_until = offer.valid_until
            event_type = "federation.goods_reserved"
            target_status = PurchaseIntentStatus.GOODS_RESERVED
        elif kind == "LOGISTICS":
            if intent.status != PurchaseIntentStatus.GOODS_RESERVED.value:
                raise federation_error("PURCHASE_INTENT_STATE_INVALID")
            quote = await session.get(LogisticsQuote, intent.quote_record_id, with_for_update=True)
            if quote is None:
                raise federation_error("LOGISTICS_QUOTE_NOT_FOUND", 404)
            home_node_code = quote.home_node_code
            external_node_id = quote.external_node_id
            resource_ref = f"quote:{quote.quote_id}:{quote.quote_version}"
            available = quote.capacity
            resource_valid_until = quote.valid_until
            event_type = "federation.logistics_reserved"
            target_status = PurchaseIntentStatus.PREPARED
        else:
            raise federation_error("RESERVATION_KIND_INVALID", 422)
        active_amount = (
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
        peer_active_amount = (
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
        if Decimal(active_amount) + Decimal(peer_active_amount) + intent.quantity > available:
            if kind == "LOGISTICS":
                raise federation_error("LOGISTICS_CAPACITY_INSUFFICIENT")
            raise federation_error("GOODS_QUANTITY_INSUFFICIENT")
        expiry = bounded_reservation_expiry(
            now=now, requested=expires_at, bounds=(intent.expires_at, resource_valid_until)
        )
        expected_receipt: dict[str, object] = {
            "receipt_id": str(receipt_id),
            "purchase_intent_id": str(intent.id),
            "buyer_node_code": self.settings.node_code,
            "kind": kind,
            "resource_ref": resource_ref,
            "home_node_code": home_node_code,
            "amount": self._decimal(intent.quantity),
            "unit_code": intent.unit_code,
            "expires_at": utc_timestamp(expiry),
            "summary_hash": intent.summary_hash,
        }
        receipt_payload = expected_receipt
        if external_node_id is not None:
            if external_receipt_payload is None:
                raise federation_error("EXTERNAL_RESERVATION_RECEIPT_REQUIRED", 422)
            try:
                remote_expiry_value = external_receipt_payload["expires_at"]
                if not isinstance(remote_expiry_value, str):
                    raise ValueError
                remote_expiry = datetime.fromisoformat(remote_expiry_value.replace("Z", "+00:00"))
            except (KeyError, ValueError) as exc:
                raise federation_error("EXTERNAL_RESERVATION_RECEIPT_INVALID", 422) from exc
            if remote_expiry.tzinfo is None or remote_expiry > expiry or remote_expiry <= now:
                raise federation_error("EXTERNAL_RESERVATION_RECEIPT_INVALID", 422)
            expected_receipt["expires_at"] = utc_timestamp(remote_expiry)
            if external_receipt_payload != expected_receipt:
                raise federation_error("EXTERNAL_RESERVATION_RECEIPT_BINDING_INVALID", 422)
            receipt_payload = external_receipt_payload
            expiry = remote_expiry
        elif external_receipt_payload is not None:
            raise federation_error("LOCAL_RESERVATION_RECEIPT_FORBIDDEN", 422)
        command_payload = {
            **receipt_payload,
            "signature": base64.b64encode(external_signature).decode()
            if external_signature
            else None,
        }
        record, replay = await begin_federation_command(
            session, principal, f"FEDERATION_RESERVE_{kind}", idempotency_key, command_payload
        )
        if replay is not None:
            return replay
        signature, fingerprint = await self._receipt_signature(
            session,
            external_node_id=external_node_id,
            capability="CATALOG" if kind == "GOODS" else "LOGISTICS",
            payload=receipt_payload,
            external_signature=external_signature,
        )
        event = await self.journal.append(
            session,
            event_type=event_type,
            aggregate_type="purchase_intent",
            aggregate_id=intent.id,
            aggregate_version=intent.version + 1,
            actor=actor,
            payload={**receipt_payload, "receipt_hash": payload_hash(receipt_payload)},
        )
        session.add(
            ReservationReceipt(
                id=receipt_id,
                intent_id=intent.id,
                kind=kind,
                resource_ref=resource_ref,
                home_node_code=home_node_code,
                amount=intent.quantity,
                unit_code=intent.unit_code,
                status="ACTIVE",
                receipt_payload=receipt_payload,
                receipt_hash=payload_hash(receipt_payload),
                node_signature=signature,
                signer_fingerprint=fingerprint,
                created_event_id=event.event_id,
                created_at=now,
                expires_at=expiry,
            )
        )
        intent.status = target_status.value
        intent.version += 1
        await audit_federation_action(
            session,
            principal,
            f"{kind}_RESERVED",
            "PurchaseIntent",
            intent.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, receipt_id)

    async def begin_commit(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        intent_id: UUID,
        summary_hash: str,
        expected_version: int,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await self._member_actor(session, principal)
        intent = await session.get(PurchaseIntent, intent_id, with_for_update=True)
        if intent is None:
            raise federation_error("PURCHASE_INTENT_NOT_FOUND", 404)
        self._ensure_buyer(intent, principal)
        payload = {
            "purchase_intent_id": str(intent.id),
            "summary_hash": summary_hash,
            "expected_version": expected_version,
        }
        begin_key = f"commit-begin:{payload_hash(payload)[7:39]}"
        record, replay = await begin_federation_command(
            session,
            principal,
            "FEDERATION_BEGIN_COMMIT_PURCHASE",
            begin_key,
            payload,
        )
        if replay is not None:
            return replay
        now = datetime.now(UTC)
        if (
            intent.status != PurchaseIntentStatus.PREPARED.value
            or intent.version != expected_version
            or intent.summary_hash != summary_hash
            or intent.expires_at <= now
        ):
            raise federation_error("PURCHASE_COMMIT_PRECONDITION_FAILED")
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
        if {receipt.kind for receipt in receipts} != {"GOODS", "LOGISTICS"} or any(
            receipt.expires_at <= now for receipt in receipts
        ):
            raise federation_error("PURCHASE_RECEIPTS_INCOMPLETE")
        signer = signer_from_settings(self.settings)
        commit_request: dict[str, object] = {
            **payload,
            "buyer_node_code": self.settings.node_code,
            "receipt_hashes": sorted(receipt.receipt_hash for receipt in receipts),
            "requested_at": utc_timestamp(now),
        }
        commit_request_hash = payload_hash(commit_request)
        commit_request_signature = signer.sign(canonicalize(commit_request))
        event = await self.journal.append(
            session,
            event_type="federation.purchase_commit_requested",
            aggregate_type="purchase_intent",
            aggregate_id=intent.id,
            aggregate_version=intent.version + 1,
            actor=actor,
            payload={
                **commit_request,
                "commit_request_hash": commit_request_hash,
                "commit_request_signer_fingerprint": signer.fingerprint,
            },
        )
        intent.status = PurchaseIntentStatus.COMMITTING.value
        intent.commit_requested_event_id = event.event_id
        intent.commit_request_payload = commit_request
        intent.commit_request_hash = commit_request_hash
        intent.commit_request_signature = commit_request_signature
        intent.commit_request_signer_fingerprint = signer.fingerprint
        intent.commit_requested_at = now
        intent.version += 1
        await audit_federation_action(
            session,
            principal,
            "PURCHASE_COMMIT_REQUESTED",
            "PurchaseIntent",
            intent.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, intent.id)

    async def remote_commit_plans(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        intent_id: UUID,
    ) -> list[RemoteReceiptActionPlan]:
        intent = await session.get(PurchaseIntent, intent_id)
        if intent is None:
            raise federation_error("PURCHASE_INTENT_NOT_FOUND", 404)
        self._ensure_buyer(intent, principal)
        if intent.status == PurchaseIntentStatus.COMMITTED.value:
            return []
        if (
            intent.status != PurchaseIntentStatus.COMMITTING.value
            or intent.commit_request_hash is None
        ):
            raise federation_error("PURCHASE_INTENT_STATE_INVALID")
        receipts = list(
            (
                await session.execute(
                    select(ReservationReceipt).where(
                        ReservationReceipt.intent_id == intent.id,
                        ReservationReceipt.status == "ACTIVE",
                    )
                )
            ).scalars()
        )
        plans: list[RemoteReceiptActionPlan] = []
        for receipt in receipts:
            external_node_id = await self._external_node_for_receipt(
                session, intent=intent, receipt=receipt
            )
            if external_node_id is None or receipt.remote_commit_hash is not None:
                continue
            plans.append(
                RemoteReceiptActionPlan(
                    receipt_id=receipt.id,
                    external_node_id=external_node_id,
                    kind=receipt.kind,
                    payload={
                        "receipt_id": str(receipt.id),
                        "purchase_intent_id": str(intent.id),
                        "kind": receipt.kind,
                        "receipt_hash": receipt.receipt_hash,
                        "summary_hash": intent.summary_hash,
                        "commit_request_hash": intent.commit_request_hash,
                    },
                )
            )
        return plans

    async def record_remote_commit(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        intent_id: UUID,
        receipt_id: UUID,
        evidence_payload: dict[str, object],
        evidence_hash: str,
        evidence_signature: bytes,
        signer_fingerprint: str,
    ) -> None:
        intent = await session.get(PurchaseIntent, intent_id, with_for_update=True)
        receipt = await session.get(ReservationReceipt, receipt_id, with_for_update=True)
        if intent is None or receipt is None or receipt.intent_id != intent_id:
            raise federation_error("PURCHASE_RECEIPT_NOT_FOUND", 404)
        self._ensure_buyer(intent, principal)
        if intent.status != PurchaseIntentStatus.COMMITTING.value:
            raise federation_error("PURCHASE_INTENT_STATE_INVALID")
        external_node_id = await self._external_node_for_receipt(
            session, intent=intent, receipt=receipt
        )
        if external_node_id is None:
            raise federation_error("REMOTE_RECEIPT_EXPECTED", 422)
        if receipt.remote_commit_hash is not None:
            if (
                receipt.remote_commit_hash != evidence_hash
                or receipt.remote_commit_payload != evidence_payload
                or receipt.remote_commit_signature != evidence_signature
            ):
                raise federation_error("REMOTE_COMMIT_EVIDENCE_CONFLICT", 409)
            return
        node, certificate = await self._trusted_external(
            session,
            external_node_id,
            "CATALOG" if receipt.kind == "GOODS" else "LOGISTICS",
        )
        expected = {
            "receipt_id": str(receipt.id),
            "purchase_intent_id": str(intent.id),
            "buyer_node_code": self.settings.node_code,
            "kind": receipt.kind,
            "resource_ref": receipt.resource_ref,
            "receipt_hash": receipt.receipt_hash,
            "summary_hash": intent.summary_hash,
            "commit_request_hash": intent.commit_request_hash,
            "status": "COMMITTED",
        }
        if (
            node.node_code != receipt.home_node_code
            or signer_fingerprint != certificate.fingerprint
            or any(evidence_payload.get(key) != value for key, value in expected.items())
            or payload_hash(evidence_payload) != evidence_hash
            or not verify_signature(
                certificate.public_key,
                evidence_signature,
                canonicalize(evidence_payload),
            )
        ):
            raise federation_error("REMOTE_COMMIT_EVIDENCE_INVALID", 422)
        receipt.remote_commit_payload = evidence_payload
        receipt.remote_commit_hash = evidence_hash
        receipt.remote_commit_signature = evidence_signature
        receipt.remote_commit_signer_fingerprint = signer_fingerprint

    async def commit(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        intent_id: UUID,
        summary_hash: str,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await self._member_actor(session, principal)
        intent = await session.get(PurchaseIntent, intent_id, with_for_update=True)
        if intent is None:
            raise federation_error("PURCHASE_INTENT_NOT_FOUND", 404)
        self._ensure_buyer(intent, principal)
        payload = {
            "purchase_intent_id": str(intent.id),
            "summary_hash": summary_hash,
            "expected_version": expected_version,
        }
        record, replay = await begin_federation_command(
            session, principal, "FEDERATION_COMMIT_PURCHASE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        if (
            intent.status != PurchaseIntentStatus.COMMITTING.value
            or intent.summary_hash != summary_hash
            or intent.commit_request_payload is None
            or intent.commit_request_payload.get("expected_version") != expected_version
            or intent.commit_request_hash is None
        ):
            raise federation_error("PURCHASE_COMMIT_PRECONDITION_FAILED")
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
        if {receipt.kind for receipt in receipts} != {"GOODS", "LOGISTICS"}:
            raise federation_error("PURCHASE_RECEIPTS_INCOMPLETE")
        now = datetime.now(UTC)
        remote_hashes: list[str] = []
        for receipt in receipts:
            external_node_id = await self._external_node_for_receipt(
                session, intent=intent, receipt=receipt
            )
            if external_node_id is None:
                if receipt.expires_at <= now:
                    raise federation_error("PURCHASE_RECEIPTS_INCOMPLETE")
            elif receipt.remote_commit_hash is None:
                raise federation_error("REMOTE_COMMIT_ACKNOWLEDGEMENT_MISSING")
            else:
                remote_hashes.append(receipt.remote_commit_hash)
        event = await self.journal.append(
            session,
            event_type="federation.purchase_committed",
            aggregate_type="purchase_intent",
            aggregate_id=intent.id,
            aggregate_version=intent.version + 1,
            actor=actor,
            payload={
                **payload,
                "commit_request_hash": intent.commit_request_hash,
                "receipt_hashes": sorted(receipt.receipt_hash for receipt in receipts),
                "remote_commit_hashes": sorted(remote_hashes),
                "landed_cost_breakdown": intent.landed_cost_breakdown,
            },
        )
        for receipt in receipts:
            receipt.status = "COMMITTED"
            receipt.closed_at = now
            receipt.version += 1
        intent.status = PurchaseIntentStatus.COMMITTED.value
        intent.committed_event_id = event.event_id
        intent.committed_at = now
        intent.version += 1
        await audit_federation_action(
            session,
            principal,
            "PURCHASE_COMMITTED",
            "PurchaseIntent",
            intent.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, intent.id)

    async def begin_cancel(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        intent_id: UUID,
        reason: str,
        expected_version: int,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await self._member_actor(session, principal)
        intent = await session.get(PurchaseIntent, intent_id, with_for_update=True)
        if intent is None:
            raise federation_error("PURCHASE_INTENT_NOT_FOUND", 404)
        self._ensure_buyer(intent, principal)
        bounded_reason = self._bounded_text(reason, 1000)
        payload = {
            "purchase_intent_id": str(intent.id),
            "reason": bounded_reason,
            "expected_version": expected_version,
        }
        begin_key = f"cancel-begin:{payload_hash(payload)[7:39]}"
        record, replay = await begin_federation_command(
            session,
            principal,
            "FEDERATION_BEGIN_CANCEL_PURCHASE",
            begin_key,
            payload,
        )
        if replay is not None:
            return replay
        if intent.version != expected_version:
            raise federation_error("PURCHASE_INTENT_VERSION_CONFLICT")
        if intent.status in {
            PurchaseIntentStatus.COMMITTED.value,
            PurchaseIntentStatus.COMMITTING.value,
            PurchaseIntentStatus.CANCELLING.value,
        }:
            raise federation_error("PURCHASE_INTENT_CANNOT_CANCEL")
        event = await self.journal.append(
            session,
            event_type="federation.purchase_cancellation_requested",
            aggregate_type="purchase_intent",
            aggregate_id=intent.id,
            aggregate_version=intent.version + 1,
            actor=actor,
            payload=payload,
        )
        intent.status = PurchaseIntentStatus.CANCELLING.value
        intent.cancellation_requested_event_id = event.event_id
        intent.cancellation_reason = bounded_reason
        intent.cancellation_requested_at = datetime.now(UTC)
        intent.version += 1
        await audit_federation_action(
            session,
            principal,
            "PURCHASE_CANCELLATION_REQUESTED",
            "PurchaseIntent",
            intent.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, intent.id)

    async def remote_release_plans(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        intent_id: UUID,
    ) -> list[RemoteReceiptActionPlan]:
        intent = await session.get(PurchaseIntent, intent_id)
        if intent is None:
            raise federation_error("PURCHASE_INTENT_NOT_FOUND", 404)
        self._ensure_buyer(intent, principal)
        if intent.status in {
            PurchaseIntentStatus.CANCELLED.value,
            PurchaseIntentStatus.COMPENSATED.value,
        }:
            return []
        if intent.status != PurchaseIntentStatus.CANCELLING.value:
            raise federation_error("PURCHASE_INTENT_STATE_INVALID")
        receipts = list(
            (
                await session.execute(
                    select(ReservationReceipt).where(
                        ReservationReceipt.intent_id == intent.id,
                        ReservationReceipt.status == "ACTIVE",
                    )
                )
            ).scalars()
        )
        plans: list[RemoteReceiptActionPlan] = []
        for receipt in receipts:
            external_node_id = await self._external_node_for_receipt(
                session, intent=intent, receipt=receipt
            )
            if external_node_id is None or receipt.remote_release_hash is not None:
                continue
            plans.append(
                RemoteReceiptActionPlan(
                    receipt_id=receipt.id,
                    external_node_id=external_node_id,
                    kind=receipt.kind,
                    payload={
                        "receipt_id": str(receipt.id),
                        "purchase_intent_id": str(intent.id),
                        "kind": receipt.kind,
                        "receipt_hash": receipt.receipt_hash,
                        "summary_hash": intent.summary_hash,
                        "reason": intent.cancellation_reason,
                    },
                )
            )
        return plans

    async def record_remote_release(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        intent_id: UUID,
        receipt_id: UUID,
        evidence_payload: dict[str, object],
        evidence_hash: str,
        evidence_signature: bytes,
        signer_fingerprint: str,
    ) -> None:
        intent = await session.get(PurchaseIntent, intent_id, with_for_update=True)
        receipt = await session.get(ReservationReceipt, receipt_id, with_for_update=True)
        if intent is None or receipt is None or receipt.intent_id != intent_id:
            raise federation_error("PURCHASE_RECEIPT_NOT_FOUND", 404)
        self._ensure_buyer(intent, principal)
        if intent.status != PurchaseIntentStatus.CANCELLING.value:
            raise federation_error("PURCHASE_INTENT_STATE_INVALID")
        external_node_id = await self._external_node_for_receipt(
            session, intent=intent, receipt=receipt
        )
        if external_node_id is None:
            raise federation_error("REMOTE_RECEIPT_EXPECTED", 422)
        if receipt.remote_release_hash is not None:
            if (
                receipt.remote_release_hash != evidence_hash
                or receipt.remote_release_payload != evidence_payload
                or receipt.remote_release_signature != evidence_signature
            ):
                raise federation_error("REMOTE_RELEASE_EVIDENCE_CONFLICT", 409)
            return
        node, certificate = await self._trusted_external(
            session,
            external_node_id,
            "CATALOG" if receipt.kind == "GOODS" else "LOGISTICS",
        )
        expected = {
            "receipt_id": str(receipt.id),
            "purchase_intent_id": str(intent.id),
            "buyer_node_code": self.settings.node_code,
            "kind": receipt.kind,
            "resource_ref": receipt.resource_ref,
            "receipt_hash": receipt.receipt_hash,
            "summary_hash": intent.summary_hash,
            "reason": intent.cancellation_reason,
            "status": "RELEASED",
        }
        if (
            node.node_code != receipt.home_node_code
            or signer_fingerprint != certificate.fingerprint
            or any(evidence_payload.get(key) != value for key, value in expected.items())
            or payload_hash(evidence_payload) != evidence_hash
            or not verify_signature(
                certificate.public_key,
                evidence_signature,
                canonicalize(evidence_payload),
            )
        ):
            raise federation_error("REMOTE_RELEASE_EVIDENCE_INVALID", 422)
        receipt.remote_release_payload = evidence_payload
        receipt.remote_release_hash = evidence_hash
        receipt.remote_release_signature = evidence_signature
        receipt.remote_release_signer_fingerprint = signer_fingerprint

    async def cancel(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        intent_id: UUID,
        reason: str,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await self._member_actor(session, principal)
        intent = await session.get(PurchaseIntent, intent_id, with_for_update=True)
        if intent is None:
            raise federation_error("PURCHASE_INTENT_NOT_FOUND", 404)
        self._ensure_buyer(intent, principal)
        bounded_reason = self._bounded_text(reason, 1000)
        payload = {
            "purchase_intent_id": str(intent.id),
            "reason": bounded_reason,
            "expected_version": expected_version,
        }
        record, replay = await begin_federation_command(
            session, principal, "FEDERATION_COMPENSATE_PURCHASE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        if (
            intent.status != PurchaseIntentStatus.CANCELLING.value
            or intent.version != expected_version + 1
            or intent.cancellation_reason != bounded_reason
        ):
            raise federation_error("PURCHASE_CANCEL_PRECONDITION_FAILED")
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
        for receipt in receipts:
            external_node_id = await self._external_node_for_receipt(
                session, intent=intent, receipt=receipt
            )
            if external_node_id is not None and receipt.remote_release_hash is None:
                raise federation_error("REMOTE_RELEASE_ACKNOWLEDGEMENT_MISSING")
        event_payload: dict[str, object] = {
            **payload,
            "released_receipt_ids": sorted(str(receipt.id) for receipt in receipts),
            "remote_release_hashes": sorted(
                receipt.remote_release_hash
                for receipt in receipts
                if receipt.remote_release_hash is not None
            ),
        }
        event = await self.journal.append(
            session,
            event_type="federation.purchase_compensated",
            aggregate_type="purchase_intent",
            aggregate_id=intent.id,
            aggregate_version=intent.version + 1,
            actor=actor,
            payload=event_payload,
        )
        now = datetime.now(UTC)
        for receipt in receipts:
            receipt.status = "RELEASED"
            receipt.released_event_id = event.event_id
            receipt.closed_at = now
            receipt.version += 1
        intent.status = (
            PurchaseIntentStatus.COMPENSATED.value
            if receipts
            else PurchaseIntentStatus.CANCELLED.value
        )
        if receipts:
            intent.compensated_event_id = event.event_id
        else:
            intent.cancelled_event_id = event.event_id
        intent.closed_at = now
        intent.version += 1
        await audit_federation_action(
            session,
            principal,
            "PURCHASE_COMPENSATED",
            "PurchaseIntent",
            intent.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, intent.id)

    async def _external_node_for_receipt(
        self,
        session: AsyncSession,
        *,
        intent: PurchaseIntent,
        receipt: ReservationReceipt,
    ) -> UUID | None:
        if receipt.kind == "GOODS":
            offer = await session.get(FederatedOffer, intent.offer_record_id)
            if offer is None:
                raise federation_error("PURCHASE_RESOURCE_NOT_FOUND", 404)
            external_node_id = offer.external_node_id
        else:
            quote = await session.get(LogisticsQuote, intent.quote_record_id)
            if quote is None:
                raise federation_error("PURCHASE_RESOURCE_NOT_FOUND", 404)
            external_node_id = quote.external_node_id
        if external_node_id is None:
            if receipt.home_node_code != self.settings.node_code:
                raise federation_error("PURCHASE_RECEIPT_HOME_NODE_INVALID", 409)
            return None
        node = await session.get(ExternalNode, external_node_id)
        if node is None or node.node_code != receipt.home_node_code:
            raise federation_error("PURCHASE_RECEIPT_HOME_NODE_INVALID", 409)
        return external_node_id

    async def _artifact_signature(
        self,
        session: AsyncSession,
        *,
        external_node_id: UUID | None,
        capability: str,
        payload_builder: Callable[[str], dict[str, object]],
        external_signature: bytes | None,
    ) -> tuple[str, bytes, str]:

        if external_node_id is None:
            signer = signer_from_settings(self.settings)
            payload = payload_builder(self.settings.node_code)
            return self.settings.node_code, signer.sign(canonicalize(payload)), signer.fingerprint
        node, certificate = await self._trusted_external(session, external_node_id, capability)
        if external_signature is None:
            raise federation_error("EXTERNAL_SIGNATURE_REQUIRED", 422)
        payload = payload_builder(node.node_code)
        if not verify_signature(certificate.public_key, external_signature, canonicalize(payload)):
            raise federation_error("EXTERNAL_SIGNATURE_INVALID", 422)
        return node.node_code, external_signature, certificate.fingerprint

    async def _receipt_signature(
        self,
        session: AsyncSession,
        *,
        external_node_id: UUID | None,
        capability: str,
        payload: dict[str, object],
        external_signature: bytes | None,
    ) -> tuple[bytes, str]:
        if external_node_id is None:
            signer = signer_from_settings(self.settings)
            return signer.sign(canonicalize(payload)), signer.fingerprint
        _node, certificate = await self._trusted_external(session, external_node_id, capability)
        if external_signature is None or not verify_signature(
            certificate.public_key, external_signature, canonicalize(payload)
        ):
            raise federation_error("EXTERNAL_RESERVATION_SIGNATURE_INVALID", 422)
        return external_signature, certificate.fingerprint

    async def _trusted_external(
        self, session: AsyncSession, node_id: UUID, capability: str
    ) -> tuple[ExternalNode, NodeCertificate]:
        now = datetime.now(UTC)
        node = await session.get(ExternalNode, node_id)
        if node is None or node.status != "ACTIVE":
            raise federation_error("NODE_NOT_TRUSTED")
        contract = (
            await session.execute(
                select(NodeTrustContract).where(
                    NodeTrustContract.node_id == node_id,
                    NodeTrustContract.status == "ACTIVE",
                    NodeTrustContract.valid_from <= now,
                    NodeTrustContract.valid_until > now,
                )
            )
        ).scalar_one_or_none()
        allowed = contract is not None and capability in contract.capabilities
        if (
            not allowed
            and self.settings.environment is Environment.DEV
            and contract is not None
            and "TEST_EXCHANGE" in contract.capabilities
        ):
            allowed = True
        if not allowed:
            raise federation_error("NODE_CAPABILITY_NOT_TRUSTED")
        certificate = (
            await session.execute(
                select(NodeCertificate).where(
                    NodeCertificate.node_id == node_id,
                    NodeCertificate.status == "ACTIVE",
                    NodeCertificate.valid_from <= now,
                    NodeCertificate.valid_until > now,
                )
            )
        ).scalar_one_or_none()
        if certificate is None:
            raise federation_error("NODE_CERTIFICATE_NOT_ACTIVE")
        return node, certificate

    async def _verification_material(
        self,
        session: AsyncSession,
        external_node_id: UUID | None,
        fingerprint: str,
        capability: str,
    ) -> tuple[bool, bytes | None]:
        if external_node_id is None:
            signer = signer_from_settings(self.settings)
            return signer.fingerprint == fingerprint, signer.public_key_bytes
        try:
            _node, certificate = await self._trusted_external(session, external_node_id, capability)
        except DomainError:
            return False, None
        return certificate.fingerprint == fingerprint, certificate.public_key

    async def _cooperative_id_for_actor(
        self,
        session: AsyncSession,
        actor: ActorClaim,
    ) -> UUID:
        if actor.organization_id is not None:
            cooperative_id = await session.scalar(
                select(Cooperative.id).where(
                    Cooperative.id == actor.organization_id,
                    Cooperative.status == "ACTIVE",
                )
            )
            if cooperative_id is None:
                raise federation_error("COOPERATIVE_CONTEXT_REQUIRED", 403)
            return cooperative_id

        memberships: list[tuple[UUID, str]] = list(
            (
                await session.execute(
                    select(Cooperative.id, Cooperative.code)
                    .join(Membership, Membership.cooperative_id == Cooperative.id)
                    .where(
                        Membership.member_id == actor.person_id,
                        Membership.status == "ACTIVE",
                        Cooperative.status == "ACTIVE",
                    )
                    .order_by(Cooperative.id)
                )
            ).tuples()
        )
        if len(memberships) == 1:
            return memberships[0][0]
        local_matches = [
            cooperative_id
            for cooperative_id, code in memberships
            if code == self.settings.node_code
        ]
        if len(local_matches) == 1:
            return local_matches[0]
        raise federation_error("COOPERATIVE_CONTEXT_REQUIRED", 403)

    async def _member_actor(self, session: AsyncSession, principal: Principal) -> ActorClaim:
        if principal.member_id is None:
            raise federation_error("PERSONAL_ACTOR_REQUIRED", 403)
        user = await session.get(UserAccount, principal.user_id)
        member = await session.get(Member, principal.member_id)
        if user is None or user.status != "ACTIVE" or member is None or member.status != "ACTIVE":
            raise federation_error("ACTOR_NOT_ACTIVE", 403)
        for grant in principal.roles:
            assignment = await session.get(RoleAssignment, grant.assignment_id)
            if assignment is not None and assignment.status == "ACTIVE":
                return ActorClaim(
                    person_id=principal.member_id,
                    organization_id=assignment.cooperative_id,
                    role_assignment_id=assignment.id,
                )
        raise federation_error("ACTIVE_ROLE_REQUIRED", 403)

    @staticmethod
    def _ensure_buyer(intent: PurchaseIntent, principal: Principal) -> None:
        if intent.buyer_member_id != principal.member_id:
            raise federation_error("PURCHASE_BUYER_MISMATCH", 403)

    @staticmethod
    def _ensure_scale(value: Decimal, scale: int) -> None:
        exponent = value.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -scale:
            raise federation_error("UNIT_SCALE_MISMATCH", 422)

    @staticmethod
    def _decimal(value: Decimal) -> str:
        return format(value, "f")

    @staticmethod
    def _bounded_text(value: str | None, maximum: int) -> str:
        result = (value or "").strip()
        if not result or len(result) > maximum or any(ord(character) < 32 for character in result):
            raise federation_error("TEXT_INVALID", 422)
        return result

    @classmethod
    def _bounded_refs(cls, values: list[str]) -> list[str]:
        if len(values) > 50:
            raise federation_error("REFERENCE_LIST_INVALID", 422)
        return [cls._bounded_text(value, 200) for value in values]

    @staticmethod
    def _ordered_hashes(values: list[str]) -> list[str]:
        if len(values) > 10000 or values != sorted(set(values)):
            raise federation_error("OFFER_INDEX_HASHES_INVALID", 422)
        hexadecimal = frozenset("0123456789abcdef")
        if any(
            len(value) != 71
            or not value.startswith("sha256:")
            or any(character not in hexadecimal for character in value[7:])
            for value in values
        ):
            raise federation_error("OFFER_INDEX_HASHES_INVALID", 422)
        return list(values)

    @classmethod
    def _offer_payload(cls, **values: object) -> dict[str, object]:
        payload = dict(values)
        for name in (
            "quantity_available",
            "minimum_batch",
            "unit_price",
            "mandatory_fee_per_unit",
        ):
            amount = payload[name]
            if not isinstance(amount, Decimal):
                raise TypeError(f"{name} must be Decimal")
            payload[name] = cls._decimal(amount)
        for name in (
            "availability_from",
            "availability_until",
            "fulfillment_deadline",
            "signed_at",
            "valid_until",
        ):
            timestamp = payload[name]
            if not isinstance(timestamp, datetime):
                raise TypeError(f"{name} must be datetime")
            payload[name] = utc_timestamp(timestamp)
        if isinstance(payload.get("offer_id"), UUID):
            payload["offer_id"] = str(payload["offer_id"])
        source_mode = payload.get("source_mode")
        if isinstance(source_mode, SearchMode):
            payload["source_mode"] = source_mode.value
        return payload
