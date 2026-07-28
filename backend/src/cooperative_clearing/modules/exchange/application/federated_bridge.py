"""Materialize committed local marketplace purchases as exchange contracts."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.exchange.application.service import (
    ExchangeService,
    ObligationDraft,
)
from cooperative_clearing.modules.exchange.domain.types import DealStatus, exchange_error
from cooperative_clearing.modules.exchange.infrastructure.models import (
    Deal,
    DealConfirmation,
    DealTermsVersion,
    LogisticsOrder,
    Obligation,
)
from cooperative_clearing.modules.federation.infrastructure.discovery_models import (
    FederatedOffer,
    LogisticsQuote,
    PurchaseIntent,
)
from cooperative_clearing.modules.identity.domain.types import Principal
from cooperative_clearing.modules.identity.infrastructure.models import (
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.inventory.infrastructure.models import Product, UnitOfMeasure
from cooperative_clearing.modules.journal.application.service import (
    ActorClaim,
    SignedJournalService,
)
from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.shared.core.config import Settings


@dataclass(frozen=True, slots=True)
class PurchaseDealResult:
    event_id: UUID
    deal_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class _Party:
    member_id: UUID
    user_id: UUID
    role_assignment_id: UUID
    role_cooperative_id: UUID | None = None


class FederatedPurchaseBridge:
    """Translate signed reservation consent into a local active deal exactly once."""

    def __init__(self, settings: Settings) -> None:
        self.exchange = ExchangeService(settings)
        self.journal = SignedJournalService(settings)

    async def materialize(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        intent_id: UUID,
        request_id: UUID | None,
    ) -> PurchaseDealResult | None:
        intent = await session.get(PurchaseIntent, intent_id, with_for_update=True)
        if intent is None:
            raise exchange_error("PURCHASE_INTENT_NOT_FOUND", 404)
        if intent.buyer_member_id != principal.member_id:
            raise exchange_error("PURCHASE_BUYER_MISMATCH", 403)
        if intent.status != "COMMITTED" or intent.committed_event_id is None:
            raise exchange_error("PURCHASE_NOT_COMMITTED", 409)

        existing = (
            await session.execute(select(Deal).where(Deal.source_purchase_intent_id == intent.id))
        ).scalar_one_or_none()
        if existing is not None:
            if existing.confirmed_event_id is None:
                raise exchange_error("PURCHASE_DEAL_INCOMPLETE", 500)
            return PurchaseDealResult(existing.confirmed_event_id, existing.id, True)

        offer = await session.get(FederatedOffer, intent.offer_record_id)
        quote = await session.get(LogisticsQuote, intent.quote_record_id)
        if offer is None or quote is None:
            raise exchange_error("PURCHASE_ARTIFACT_NOT_FOUND", 404)
        if offer.external_node_id is not None:
            return None
        if offer.publisher_member_id is None or offer.publisher_role_assignment_id is None:
            raise exchange_error("PURCHASE_SELLER_NOT_IDENTIFIED", 409)
        if offer.publisher_member_id == intent.buyer_member_id:
            return None

        buyer = await self._party(
            session,
            member_id=intent.buyer_member_id,
            user_id=intent.buyer_user_id,
            role_assignment_id=intent.buyer_role_assignment_id,
        )
        seller = await self._party(
            session,
            member_id=offer.publisher_member_id,
            role_assignment_id=offer.publisher_role_assignment_id,
        )
        quote_event = await session.get(SignedEvent, quote.issued_event_id)
        if quote_event is None:
            raise exchange_error("PURCHASE_LOGISTICS_ACTOR_MISSING", 409)
        carrier = await self._party(
            session,
            member_id=quote_event.actor_person_id,
            role_assignment_id=quote_event.actor_role_assignment_id,
        )
        parties = self._unique_parties((buyer, seller, carrier))
        cooperative_id = await self._common_cooperative(
            session, {party.member_id for party in parties}
        )
        if any(
            party.role_cooperative_id not in {None, cooperative_id} for party in parties
        ):
            raise exchange_error("PURCHASE_PARTY_ROLE_SCOPE_MISMATCH", 409)
        goods_unit = await self._unit(session, cooperative_id, intent.unit_code)
        valuation_unit = await self._valuation_unit(session, cooperative_id, offer.valuation_unit)

        breakdown = intent.landed_cost_breakdown
        goods_cost = self._amount(breakdown, "goods_cost")
        mandatory_cost = self._amount(breakdown, "mandatory_cost")
        logistics_cost = self._amount(breakdown, "logistics_cost")
        landed_cost = self._amount(breakdown, "landed_cost")
        if goods_cost + mandatory_cost + logistics_cost != landed_cost:
            raise exchange_error("PURCHASE_VALUATION_INCONSISTENT", 409)

        offer_kind = str(offer.handling_requirements.get("offer_kind", "PRODUCT")).upper()
        if offer_kind not in {"PRODUCT", "SERVICE"}:
            offer_kind = "PRODUCT"
        product = (
            await self._product(session, cooperative_id, offer, goods_unit, seller)
            if offer_kind == "PRODUCT"
            else None
        )
        valuation_source = (
            f"{offer.price_policy_version}; signed offer {offer.payload_hash}; "
            f"purchase summary {intent.summary_hash}"
        )
        obligations = [
            ObligationDraft(
                debtor_member_id=seller.member_id,
                creditor_member_id=buyer.member_id,
                subject_type=offer_kind,
                subject_id=product.id if product is not None else None,
                description=offer.description,
                quality_criteria=offer.quality_grade,
                fulfillment_place=intent.delivery_address_text or intent.destination_region,
                due_at=offer.fulfillment_deadline,
                unit_id=goods_unit.id,
                quantity=intent.quantity,
                partial_allowed=offer.divisible,
                evidence_required=True,
                confirmation_method="Signed delivery and recipient acceptance",
                substitute_policy="Only with a new signed consent of both parties",
                valuation_source=valuation_source,
                liquidity_class="UNASSESSED",
                clearing_allowed=False,
            ),
            ObligationDraft(
                debtor_member_id=buyer.member_id,
                creditor_member_id=seller.member_id,
                subject_type="OTHER",
                subject_id=None,
                description=f"Exchange value for {offer.description}, including mandatory fees",
                quality_criteria="Confirmed goods fulfillment or accepted clearing entry",
                fulfillment_place=intent.delivery_address_text or intent.destination_region,
                due_at=offer.fulfillment_deadline,
                unit_id=valuation_unit.id,
                quantity=goods_cost + mandatory_cost,
                partial_allowed=True,
                evidence_required=False,
                confirmation_method="Clearing certificate or settlement record",
                substitute_policy="According to the cooperative settlement policy",
                valuation_source=valuation_source,
                liquidity_class="B",
                clearing_allowed=True,
            ),
        ]
        if logistics_cost > 0:
            obligations.append(
                ObligationDraft(
                    debtor_member_id=buyer.member_id,
                    creditor_member_id=carrier.member_id,
                    subject_type="LOGISTICS",
                    subject_id=None,
                    description=f"Delivery for {offer.description}: {quote.carrier_ref}",
                    quality_criteria="Delivery within the signed logistics quote",
                    fulfillment_place=intent.delivery_address_text or intent.destination_region,
                    due_at=quote.delivery_until,
                    unit_id=valuation_unit.id,
                    quantity=logistics_cost,
                    partial_allowed=True,
                    evidence_required=False,
                    confirmation_method="Clearing certificate or settlement record",
                    substitute_policy="According to the signed logistics quote",
                    valuation_source=(
                        f"signed quote {quote.payload_hash}; cost status {quote.cost_status}"
                    ),
                    liquidity_class="B",
                    clearing_allowed=True,
                )
            )

        terms = await self.exchange._terms_payload(session, cooperative_id, obligations)
        terms.update(
            {
                "source_purchase_intent_id": str(intent.id),
                "source_purchase_event_id": str(intent.committed_event_id),
                "valuation": {
                    "display_unit": offer.valuation_unit,
                    "ledger_unit": valuation_unit.code,
                    "goods_cost": str(goods_cost),
                    "mandatory_cost": str(mandatory_cost),
                    "logistics_cost": str(logistics_cost),
                    "landed_cost": str(landed_cost),
                    "price_policy_version": offer.price_policy_version,
                    "offer_payload_hash": offer.payload_hash,
                    "summary_hash": intent.summary_hash,
                },
            }
        )
        terms_hash = payload_hash(terms)
        deal_id = uuid4()
        now = datetime.now(UTC)
        buyer_actor = self._actor(buyer, cooperative_id)
        proposed_event = await self.journal.append(
            session,
            event_type="deals.marketplace_deal_proposed",
            aggregate_type="deal",
            aggregate_id=deal_id,
            aggregate_version=1,
            actor=buyer_actor,
            payload={
                "deal_id": str(deal_id),
                "source_purchase_intent_id": str(intent.id),
                "terms_version": 1,
                "terms_hash": terms_hash,
                "terms": terms,
            },
        )
        deal = Deal(
            id=deal_id,
            source_purchase_intent_id=intent.id,
            cooperative_id=cooperative_id,
            title=offer.description,
            status=DealStatus.ACTIVE.value,
            terms_version=1,
            terms_hash=terms_hash,
            proposed_by_user_id=buyer.user_id,
            proposed_by_member_id=buyer.member_id,
            proposed_role_assignment_id=buyer.role_assignment_id,
            proposed_event_id=proposed_event.event_id,
            confirmed_event_id=None,
            confirmed_at=None,
            version=1,
        )
        session.add(deal)
        session.add(
            DealTermsVersion(
                id=uuid4(),
                deal_id=deal_id,
                terms_version=1,
                terms_hash=terms_hash,
                terms_payload=terms,
                created_by_user_id=buyer.user_id,
                event_id=proposed_event.event_id,
            )
        )
        await session.flush()
        self.exchange._add_deal_parties(
            session, deal_id, 1, terms_hash, terms, proposed_event.event_id
        )

        version = 1
        confirmed_ids: list[str] = []
        for party in parties:
            version += 1
            event = await self.journal.append(
                session,
                event_type="deals.marketplace_party_confirmed",
                aggregate_type="deal",
                aggregate_id=deal_id,
                aggregate_version=version,
                actor=self._actor(party, cooperative_id),
                payload={
                    "deal_id": str(deal_id),
                    "member_id": str(party.member_id),
                    "terms_version": 1,
                    "terms_hash": terms_hash,
                    "consent_source": (
                        "buyer_purchase_commit"
                        if party.member_id == buyer.member_id
                        else "signed_marketplace_reservation"
                    ),
                },
            )
            session.add(
                DealConfirmation(
                    id=uuid4(),
                    deal_id=deal_id,
                    terms_version=1,
                    terms_hash=terms_hash,
                    member_id=party.member_id,
                    confirmed_by_user_id=party.user_id,
                    role_assignment_id=party.role_assignment_id,
                    event_id=event.event_id,
                )
            )
            confirmed_ids.append(str(party.member_id))

        version += 1
        confirmed_event = await self.journal.append(
            session,
            event_type="deals.marketplace_deal_confirmed",
            aggregate_type="deal",
            aggregate_id=deal_id,
            aggregate_version=version,
            actor=buyer_actor,
            payload={
                "deal_id": str(deal_id),
                "source_purchase_intent_id": str(intent.id),
                "terms_hash": terms_hash,
                "confirmed_party_ids": sorted(confirmed_ids),
                "purchase_event_id": str(intent.committed_event_id),
            },
        )
        deal.confirmed_event_id = confirmed_event.event_id
        deal.confirmed_at = now
        deal.updated_at = now
        deal.version = version
        created_obligations = await self.exchange._create_obligations(
            session, deal, terms, buyer_actor, confirmed_event.event_id
        )
        if logistics_cost > 0:
            await self._create_logistics_order(
                session,
                deal=deal,
                goods_obligation=created_obligations[0],
                intent=intent,
                buyer=buyer,
                buyer_actor=buyer_actor,
                carrier=carrier,
                quote=quote,
                offer=offer,
                request_id=request_id,
            )
        await AuditRepository(session).record(
            action="MARKETPLACE_DEAL_MATERIALIZED",
            object_type="Deal",
            object_id=deal.id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={
                "signed_event_id": str(confirmed_event.event_id),
                "purchase_intent_id": str(intent.id),
            },
        )
        return PurchaseDealResult(confirmed_event.event_id, deal.id, False)

    async def _create_logistics_order(
        self,
        session: AsyncSession,
        *,
        deal: Deal,
        goods_obligation: Obligation,
        intent: PurchaseIntent,
        buyer: _Party,
        buyer_actor: ActorClaim,
        carrier: _Party,
        quote: LogisticsQuote,
        offer: FederatedOffer,
        request_id: UUID | None,
    ) -> None:
        order_id = uuid4()
        payload: dict[str, object] = {
            "logistics_order_id": str(order_id),
            "deal_id": str(deal.id),
            "obligation_id": str(goods_obligation.id),
            "quote_record_id": str(quote.id),
            "carrier_member_id": str(carrier.member_id),
            "quantity": str(goods_obligation.quantity_total),
            "unit_id": str(goods_obligation.unit_id),
            "origin_text": offer.pickup_address_text or offer.origin_region,
            "destination_text": intent.delivery_address_text or quote.destination_region,
            "origin_contact_name": offer.pickup_contact_name,
            "origin_contact_phone": offer.pickup_contact_phone,
            "origin_instructions": offer.pickup_instructions,
            "destination_contact_name": intent.delivery_contact_name,
            "destination_contact_phone": intent.delivery_contact_phone,
            "destination_instructions": intent.delivery_instructions,
            "pickup_due_at": quote.delivery_from.isoformat(),
            "delivery_due_at": quote.delivery_until.isoformat(),
        }
        event = await self.journal.append(
            session,
            event_type="logistics.marketplace_order_offered",
            aggregate_type="logistics_order",
            aggregate_id=order_id,
            aggregate_version=1,
            actor=buyer_actor,
            payload=payload,
        )
        session.add(
            LogisticsOrder(
                id=order_id,
                obligation_id=goods_obligation.id,
                cooperative_id=deal.cooperative_id,
                carrier_member_id=carrier.member_id,
                quantity=goods_obligation.quantity_total,
                unit_id=goods_obligation.unit_id,
                origin_text=offer.pickup_address_text or offer.origin_region,
                destination_text=intent.delivery_address_text or quote.destination_region,
                origin_contact_name=offer.pickup_contact_name,
                origin_contact_phone=offer.pickup_contact_phone,
                origin_instructions=offer.pickup_instructions,
                destination_contact_name=intent.delivery_contact_name,
                destination_contact_phone=intent.delivery_contact_phone,
                destination_instructions=intent.delivery_instructions,
                pickup_due_at=quote.delivery_from,
                delivery_due_at=quote.delivery_until,
                status="OFFERED",
                offered_by_user_id=buyer.user_id,
                offered_event_id=event.event_id,
                version=1,
            )
        )
        await AuditRepository(session).record(
            action="MARKETPLACE_LOGISTICS_ORDER_OFFERED",
            object_type="LogisticsOrder",
            object_id=order_id,
            cooperative_id=deal.cooperative_id,
            actor_user_id=buyer.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={
                "signed_event_id": str(event.event_id),
                "quote_record_id": str(quote.id),
            },
        )

    @staticmethod
    async def _party(
        session: AsyncSession,
        *,
        member_id: UUID,
        role_assignment_id: UUID,
        user_id: UUID | None = None,
    ) -> _Party:
        assignment = await session.get(RoleAssignment, role_assignment_id)
        if (
            assignment is None
            or assignment.status != "ACTIVE"
            or (user_id is not None and assignment.user_id != user_id)
        ):
            raise exchange_error("PURCHASE_PARTY_ROLE_INACTIVE", 409)
        user = await session.get(UserAccount, assignment.user_id)
        if user is None or user.status != "ACTIVE" or user.member_id != member_id:
            raise exchange_error("PURCHASE_PARTY_INACTIVE", 409)
        return _Party(member_id, user.id, assignment.id, assignment.cooperative_id)

    @staticmethod
    def _unique_parties(parties: tuple[_Party, ...]) -> tuple[_Party, ...]:
        result: dict[UUID, _Party] = {}
        for party in parties:
            result.setdefault(party.member_id, party)
        return tuple(result.values())

    @staticmethod
    async def _common_cooperative(session: AsyncSession, member_ids: set[UUID]) -> UUID:
        rows = (
            await session.execute(
                select(Membership.cooperative_id, Membership.member_id).where(
                    Membership.member_id.in_(member_ids),
                    Membership.status == "ACTIVE",
                )
            )
        ).all()
        by_cooperative: dict[UUID, set[UUID]] = {}
        for cooperative_id, member_id in rows:
            by_cooperative.setdefault(cooperative_id, set()).add(member_id)
        choices = sorted(
            (
                cooperative_id
                for cooperative_id, members in by_cooperative.items()
                if members == member_ids
            ),
            key=lambda value: value.int,
        )
        if not choices:
            raise exchange_error("PURCHASE_PARTIES_HAVE_NO_COMMON_COOPERATIVE", 409)
        return choices[0]

    @staticmethod
    async def _unit(session: AsyncSession, cooperative_id: UUID, code: str) -> UnitOfMeasure:
        unit = (
            await session.execute(
                select(UnitOfMeasure).where(
                    UnitOfMeasure.cooperative_id == cooperative_id,
                    UnitOfMeasure.code == code.upper(),
                    UnitOfMeasure.status == "ACTIVE",
                )
            )
        ).scalar_one_or_none()
        if unit is None:
            raise exchange_error("PURCHASE_UNIT_NOT_REGISTERED", 409)
        return unit

    @staticmethod
    async def _product(
        session: AsyncSession,
        cooperative_id: UUID,
        offer: FederatedOffer,
        unit: UnitOfMeasure,
        seller: _Party,
    ) -> Product:
        sku = offer.product_code.strip().upper()
        if not sku or len(sku) > 63:
            raise exchange_error("PURCHASE_PRODUCT_CODE_INVALID", 409)
        product = (
            await session.execute(
                select(Product).where(
                    Product.cooperative_id == cooperative_id,
                    Product.sku == sku,
                )
            )
        ).scalar_one_or_none()
        if product is None:
            product = Product(
                id=uuid4(),
                cooperative_id=cooperative_id,
                sku=sku,
                name=offer.description[:200],
                description=offer.description,
                default_unit_id=unit.id,
                quantity_tolerance=Decimal(0),
                requires_evidence=True,
                shelf_life_required=False,
                status="ACTIVE",
                created_by_user_id=seller.user_id,
                created_event_id=offer.published_event_id,
            )
            session.add(product)
            await session.flush()
        elif product.status != "ACTIVE" or product.default_unit_id != unit.id:
            raise exchange_error("PURCHASE_PRODUCT_NOT_AVAILABLE", 409)
        return product

    @staticmethod
    async def _valuation_unit(
        session: AsyncSession, cooperative_id: UUID, display_code: str
    ) -> UnitOfMeasure:
        unit = (
            await session.execute(
                select(UnitOfMeasure)
                .where(
                    UnitOfMeasure.cooperative_id == cooperative_id,
                    UnitOfMeasure.status == "ACTIVE",
                    (
                        (UnitOfMeasure.code == display_code.upper())
                        | (UnitOfMeasure.dimension == "VALUATION")
                    ),
                )
                .order_by((UnitOfMeasure.code == display_code.upper()).desc(), UnitOfMeasure.code)
                .limit(1)
            )
        ).scalar_one_or_none()
        if unit is None:
            raise exchange_error("PURCHASE_VALUATION_UNIT_NOT_REGISTERED", 409)
        return unit

    @staticmethod
    def _amount(breakdown: dict[str, object], key: str) -> Decimal:
        value = breakdown.get(key)
        if value is None and key == "logistics_cost":
            return Decimal(0)
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise exchange_error("PURCHASE_VALUATION_INVALID", 409) from exc
        if amount < 0:
            raise exchange_error("PURCHASE_VALUATION_INVALID", 409)
        return amount

    @staticmethod
    def _actor(party: _Party, cooperative_id: UUID) -> ActorClaim:
        return ActorClaim(
            person_id=party.member_id,
            organization_id=cooperative_id,
            role_assignment_id=party.role_assignment_id,
        )
