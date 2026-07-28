"""Atomic local deal, obligation, fulfillment, dispute, and logistics commands."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.exchange.application.common import (
    ExchangeCommandResult,
    begin_exchange_command,
    complete_exchange_command,
    party_actor,
    role_actor,
)
from cooperative_clearing.modules.exchange.domain.types import (
    AcceptanceDecision,
    DealStatus,
    FulfillmentStatus,
    LogisticsStatus,
    ObligationAmounts,
    ObligationStatus,
    acceptance_decision,
    ensure_obligation_operable,
    exchange_error,
    next_logistics_status,
    obligation_status_for,
)
from cooperative_clearing.modules.exchange.infrastructure.models import (
    AcceptanceRecord,
    Deal,
    DealConfirmation,
    DealParty,
    DealTermsVersion,
    Fulfillment,
    FulfillmentProvenance,
    LogisticsOrder,
    Obligation,
    ObligationDispute,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.domain.types import (
    decimal_text,
    ensure_unit_scale,
    exact_quantity,
)
from cooperative_clearing.modules.inventory.infrastructure.models import (
    EvidenceBlob,
    EvidenceLink,
    InventoryLot,
    Product,
    UnitOfMeasure,
)
from cooperative_clearing.modules.journal.application.service import SignedJournalService
from cooperative_clearing.modules.journal.domain.assurance import (
    CommandAssurance,
    ExposureCategory,
    ExposureClaim,
    ExposureEffect,
)
from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.modules.rights.infrastructure.models import (
    CommodityRight,
    RightRedemption,
)
from cooperative_clearing.shared.core.config import Settings

DEAL_OPERATOR_ROLES = {RoleCode.COOPERATIVE_ADMIN}
LOGISTICS_ROLES = {RoleCode.LOGISTICS_OPERATOR}
OVERDUE_ROLES = {RoleCode.COOPERATIVE_ADMIN, RoleCode.RISK_ADMIN, RoleCode.AUDITOR}
DISPUTE_RESOLVER_ROLES = {RoleCode.COOPERATIVE_ADMIN, RoleCode.RISK_ADMIN, RoleCode.AUDITOR}
PROVENANCE_RECONCILE_ROLES = {RoleCode.COOPERATIVE_ADMIN, RoleCode.RIGHTS_OPERATOR}


@dataclass(frozen=True, slots=True)
class ObligationDraft:
    debtor_member_id: UUID
    creditor_member_id: UUID
    subject_type: str
    subject_id: UUID | None
    description: str
    quality_criteria: str
    fulfillment_place: str
    due_at: datetime
    unit_id: UUID
    quantity: Decimal
    partial_allowed: bool
    evidence_required: bool
    confirmation_method: str
    substitute_policy: str
    valuation_source: str
    liquidity_class: str = "UNASSESSED"
    clearing_allowed: bool = False


class ExchangeService:
    def __init__(self, settings: Settings) -> None:
        self.journal = SignedJournalService(settings)

    async def propose_deal(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        title: str,
        obligations: Sequence[ObligationDraft],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ExchangeCommandResult:
        actor = role_actor(principal, cooperative_id, DEAL_OPERATOR_ROLES)
        terms = await self._terms_payload(session, cooperative_id, obligations)
        normalized_title = self._text(title, "DEAL_TITLE_INVALID", 200)
        terms_hash = payload_hash(terms)
        payload = {
            "cooperative_id": str(cooperative_id),
            "title": normalized_title,
            "terms_version": 1,
            "terms_hash": terms_hash,
            "terms": terms,
        }
        record, replay = await begin_exchange_command(
            session, principal, "EXCHANGE_PROPOSE_DEAL", idempotency_key, payload
        )
        if replay is not None:
            return replay
        deal_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="deals.deal_proposed",
            aggregate_type="deal",
            aggregate_id=deal_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "deal_id": str(deal_id)},
        )
        session.add(
            Deal(
                id=deal_id,
                cooperative_id=cooperative_id,
                title=normalized_title,
                status=DealStatus.PROPOSED.value,
                terms_version=1,
                terms_hash=terms_hash,
                proposed_by_user_id=principal.user_id,
                proposed_by_member_id=actor.person_id,
                proposed_role_assignment_id=actor.role_assignment_id,
                proposed_event_id=event.event_id,
                version=1,
            )
        )
        session.add(
            DealTermsVersion(
                id=uuid4(),
                deal_id=deal_id,
                terms_version=1,
                terms_hash=terms_hash,
                terms_payload=terms,
                created_by_user_id=principal.user_id,
                event_id=event.event_id,
            )
        )
        await session.flush()
        self._add_deal_parties(session, deal_id, 1, terms_hash, terms, event.event_id)
        await self._audit(
            session,
            principal,
            cooperative_id,
            "DEAL_PROPOSED",
            "Deal",
            deal_id,
            event.event_id,
            request_id,
        )
        return complete_exchange_command(record, event.event_id, deal_id)

    async def revise_deal(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        deal_id: UUID,
        title: str,
        obligations: Sequence[ObligationDraft],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ExchangeCommandResult:
        deal = await session.get(Deal, deal_id, with_for_update=True)
        if deal is None:
            raise exchange_error("DEAL_NOT_FOUND", 404)
        actor = role_actor(principal, deal.cooperative_id, DEAL_OPERATOR_ROLES)
        terms = await self._terms_payload(session, deal.cooperative_id, obligations)
        normalized_title = self._text(title, "DEAL_TITLE_INVALID", 200)
        new_terms_hash = payload_hash(terms)
        payload = {
            "deal_id": str(deal_id),
            "title": normalized_title,
            "terms_version": deal.terms_version + 1,
            "terms_hash": new_terms_hash,
            "terms": terms,
            "expected_version": expected_version,
        }
        record, replay = await begin_exchange_command(
            session, principal, "EXCHANGE_REVISE_DEAL", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(deal.version, expected_version)
        if deal.status != DealStatus.PROPOSED.value:
            raise exchange_error("DEAL_TERMS_LOCKED", 409)
        event = await self.journal.append(
            session,
            event_type="deals.deal_terms_revised",
            aggregate_type="deal",
            aggregate_id=deal.id,
            aggregate_version=deal.version + 1,
            actor=actor,
            payload={**payload, "previous_terms_hash": deal.terms_hash},
        )
        deal.title = normalized_title
        deal.terms_version += 1
        deal.terms_hash = new_terms_hash
        deal.version += 1
        deal.updated_at = datetime.now(UTC)
        session.add(
            DealTermsVersion(
                id=uuid4(),
                deal_id=deal.id,
                terms_version=deal.terms_version,
                terms_hash=new_terms_hash,
                terms_payload=terms,
                created_by_user_id=principal.user_id,
                event_id=event.event_id,
            )
        )
        await session.flush()
        self._add_deal_parties(
            session,
            deal.id,
            deal.terms_version,
            new_terms_hash,
            terms,
            event.event_id,
        )
        await self._audit(
            session,
            principal,
            deal.cooperative_id,
            "DEAL_TERMS_REVISED",
            "Deal",
            deal.id,
            event.event_id,
            request_id,
        )
        return complete_exchange_command(record, event.event_id, deal.id)

    async def confirm_deal(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        deal_id: UUID,
        terms_version: int,
        terms_hash: str,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ExchangeCommandResult:
        payload = {
            "deal_id": str(deal_id),
            "terms_version": terms_version,
            "terms_hash": terms_hash,
            "expected_version": expected_version,
        }
        record, replay = await begin_exchange_command(
            session, principal, "EXCHANGE_CONFIRM_DEAL", idempotency_key, payload
        )
        if replay is not None:
            return replay
        deal = await session.get(Deal, deal_id, with_for_update=True)
        if deal is None:
            raise exchange_error("DEAL_NOT_FOUND", 404)
        self._version(deal.version, expected_version)
        if deal.status != DealStatus.PROPOSED.value:
            raise exchange_error("DEAL_NOT_PROPOSED", 409)
        if deal.terms_version != terms_version or deal.terms_hash != terms_hash:
            raise exchange_error("DEAL_TERMS_CHANGED", 409)
        terms = (
            await session.execute(
                select(DealTermsVersion).where(
                    DealTermsVersion.deal_id == deal.id,
                    DealTermsVersion.terms_version == terms_version,
                )
            )
        ).scalar_one()
        raw_required_parties = terms.terms_payload.get("required_party_ids")
        if not isinstance(raw_required_parties, list) or not all(
            isinstance(value, str) for value in raw_required_parties
        ):
            raise exchange_error("DEAL_TERMS_CORRUPTED", 500)
        required_parties = {UUID(value) for value in raw_required_parties}
        if principal.member_id not in required_parties:
            raise exchange_error("DEAL_CONFIRMATION_NOT_A_PARTY", 403)
        actor = party_actor(principal, deal.cooperative_id, principal.member_id)
        existing = (
            await session.execute(
                select(DealConfirmation.id).where(
                    DealConfirmation.deal_id == deal.id,
                    DealConfirmation.terms_version == terms_version,
                    DealConfirmation.member_id == principal.member_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise exchange_error("DEAL_PARTY_ALREADY_CONFIRMED", 409)
        confirmation_id = uuid4()
        confirmation_event = await self.journal.append(
            session,
            event_type="deals.party_confirmed",
            aggregate_type="deal",
            aggregate_id=deal.id,
            aggregate_version=deal.version + 1,
            actor=actor,
            payload={
                **payload,
                "confirmation_id": str(confirmation_id),
                "member_id": str(principal.member_id),
            },
        )
        session.add(
            DealConfirmation(
                id=confirmation_id,
                deal_id=deal.id,
                terms_version=terms_version,
                terms_hash=terms_hash,
                member_id=principal.member_id,
                confirmed_by_user_id=principal.user_id,
                role_assignment_id=actor.role_assignment_id,
                event_id=confirmation_event.event_id,
            )
        )
        confirmed = set(
            (
                await session.execute(
                    select(DealConfirmation.member_id).where(
                        DealConfirmation.deal_id == deal.id,
                        DealConfirmation.terms_version == terms_version,
                    )
                )
            ).scalars()
        )
        confirmed.add(principal.member_id)
        deal.version += 1
        result_event = confirmation_event
        if confirmed == required_parties:
            result_event = await self.journal.append(
                session,
                event_type="deals.deal_confirmed",
                aggregate_type="deal",
                aggregate_id=deal.id,
                aggregate_version=deal.version + 1,
                actor=actor,
                payload={
                    "deal_id": str(deal.id),
                    "terms_version": terms_version,
                    "terms_hash": terms_hash,
                    "confirmed_party_ids": sorted(str(value) for value in confirmed),
                },
            )
            deal.status = DealStatus.ACTIVE.value
            deal.confirmed_event_id = result_event.event_id
            deal.confirmed_at = datetime.now(UTC)
            deal.version += 1
            await self._create_obligations(
                session, deal, terms.terms_payload, actor, result_event.event_id
            )
        deal.updated_at = datetime.now(UTC)
        await self._audit(
            session,
            principal,
            deal.cooperative_id,
            "DEAL_PARTY_CONFIRMED",
            "Deal",
            deal.id,
            result_event.event_id,
            request_id,
        )
        return complete_exchange_command(record, result_event.event_id, deal.id)

    async def submit_fulfillment(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        obligation_id: UUID,
        quantity: Decimal,
        quality_claim: str,
        location_text: str,
        performed_at: datetime,
        logistics_order_id: UUID | None,
        source_redemption_id: UUID | None,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ExchangeCommandResult:
        amount = exact_quantity(quantity)
        payload = {
            "obligation_id": str(obligation_id),
            "quantity": decimal_text(amount),
            "quality_claim": self._text(quality_claim, "QUALITY_CLAIM_INVALID", 2000),
            "location_text": self._text(location_text, "FULFILLMENT_LOCATION_INVALID", 500),
            "performed_at": performed_at.astimezone(UTC).isoformat(),
            "logistics_order_id": str(logistics_order_id) if logistics_order_id else None,
            "source_redemption_id": (
                str(source_redemption_id) if source_redemption_id else None
            ),
            "evidence_ids": [str(value) for value in evidence_ids],
            "expected_version": expected_version,
        }
        record, replay = await begin_exchange_command(
            session, principal, "EXCHANGE_SUBMIT_FULFILLMENT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        obligation = await session.get(Obligation, obligation_id, with_for_update=True)
        if obligation is None:
            raise exchange_error("OBLIGATION_NOT_FOUND", 404)
        self._version(obligation.version, expected_version)
        ensure_obligation_operable(ObligationStatus(obligation.status))
        actor = party_actor(principal, obligation.cooperative_id, obligation.debtor_member_id)
        unit = await self._unit(session, obligation.unit_id)
        ensure_unit_scale(amount, unit.decimal_scale)
        amounts = self._amounts(obligation).submit(
            amount, partial_allowed=obligation.partial_allowed
        )
        source = await self._product_fulfillment_source(
            session,
            obligation=obligation,
            amount=amount,
            performed_at=performed_at,
            source_redemption_id=source_redemption_id,
        )
        if logistics_order_id is not None:
            order = await session.get(LogisticsOrder, logistics_order_id)
            if (
                order is None
                or order.obligation_id != obligation.id
                or order.status != LogisticsStatus.DELIVERED.value
                or amount > order.quantity
            ):
                raise exchange_error("LOGISTICS_DELIVERY_NOT_AVAILABLE", 409)
        evidence = await EvidenceService.require_ready(
            session,
            obligation.cooperative_id,
            evidence_ids,
            required=obligation.evidence_required,
        )
        fulfillment_id = uuid4()
        evidence_refs = (
            *self._evidence_payload(evidence),
            {
                "event_id": str(obligation.last_event_id),
                "kind": "OBLIGATION_STATE",
            },
        )
        event = await self.journal.append(
            session,
            event_type="obligations.fulfillment_recorded",
            aggregate_type="obligation",
            aggregate_id=obligation.id,
            aggregate_version=obligation.version + 1,
            actor=actor,
            payload={
                **payload,
                "fulfillment_id": str(fulfillment_id),
                "remaining_after_submission": decimal_text(amounts.remaining),
                "evidence": list(evidence_refs),
            },
            assurance=CommandAssurance(
                exposure=ExposureClaim(
                    category=ExposureCategory.OBLIGATION,
                    effect=ExposureEffect.EXECUTE,
                    subject_type="obligation",
                    subject_id=obligation.id,
                    amount=amount,
                    unit=str(obligation.unit_id),
                ),
                evidence_refs=evidence_refs,
            ),
        )
        session.add(
            Fulfillment(
                id=fulfillment_id,
                obligation_id=obligation.id,
                logistics_order_id=logistics_order_id,
                quantity=amount,
                accepted_quantity=Decimal(0),
                quality_claim=payload["quality_claim"],
                location_text=payload["location_text"],
                performed_at=performed_at.astimezone(UTC),
                status=FulfillmentStatus.SUBMITTED.value,
                performed_by_user_id=principal.user_id,
                performed_by_member_id=actor.person_id,
                submitted_event_id=event.event_id,
                version=1,
            )
        )
        if source is not None:
            redemption, right, lot = source
            session.add(
                FulfillmentProvenance(
                    fulfillment_id=fulfillment_id,
                    cooperative_id=obligation.cooperative_id,
                    redemption_id=redemption.id,
                    right_id=right.id,
                    lot_id=lot.id,
                    product_id=lot.product_id,
                    source_owner_member_id=right.owner_member_id,
                    intended_recipient_member_id=obligation.creditor_member_id,
                    quantity=amount,
                    linked_event_id=event.event_id,
                )
            )
        obligation.quantity_submitted = amounts.submitted
        obligation.version += 1
        obligation.last_event_id = event.event_id
        obligation.updated_at = datetime.now(UTC)
        self._link_evidence(session, evidence, event.event_id, "fulfillment", fulfillment_id)
        await self._audit(
            session,
            principal,
            obligation.cooperative_id,
            "FULFILLMENT_RECORDED",
            "Fulfillment",
            fulfillment_id,
            event.event_id,
            request_id,
        )
        return complete_exchange_command(record, event.event_id, fulfillment_id)

    async def reconcile_fulfillment_provenance(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        fulfillment_id: UUID,
        source_redemption_id: UUID,
        rationale: str,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ExchangeCommandResult:
        payload = {
            "fulfillment_id": str(fulfillment_id),
            "source_redemption_id": str(source_redemption_id),
            "rationale": self._text(rationale, "PROVENANCE_RATIONALE_INVALID", 2000),
            "evidence_ids": [str(value) for value in evidence_ids],
        }
        record, replay = await begin_exchange_command(
            session,
            principal,
            "EXCHANGE_RECONCILE_FULFILLMENT_PROVENANCE",
            idempotency_key,
            payload,
        )
        if replay is not None:
            return replay
        fulfillment = await session.get(Fulfillment, fulfillment_id, with_for_update=True)
        if fulfillment is None:
            raise exchange_error("FULFILLMENT_NOT_FOUND", 404)
        obligation = await session.get(Obligation, fulfillment.obligation_id)
        if obligation is None:
            raise exchange_error("OBLIGATION_NOT_FOUND", 404)
        actor = role_actor(principal, obligation.cooperative_id, PROVENANCE_RECONCILE_ROLES)
        if await session.get(FulfillmentProvenance, fulfillment.id) is not None:
            raise exchange_error("FULFILLMENT_PROVENANCE_EXISTS", 409)
        source = await self._product_fulfillment_source(
            session,
            obligation=obligation,
            amount=fulfillment.quantity,
            performed_at=fulfillment.performed_at,
            source_redemption_id=source_redemption_id,
        )
        if source is None:
            raise exchange_error("FULFILLMENT_SOURCE_NOT_ALLOWED", 409)
        evidence = await EvidenceService.require_ready(
            session,
            obligation.cooperative_id,
            evidence_ids,
            required=True,
        )
        redemption, right, lot = source
        event = await self.journal.append(
            session,
            event_type="obligations.fulfillment_provenance_reconciled",
            aggregate_type="fulfillment_provenance",
            aggregate_id=fulfillment.id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "obligation_id": str(obligation.id),
                "deal_id": str(obligation.deal_id),
                "right_id": str(right.id),
                "lot_id": str(lot.id),
                "product_id": str(lot.product_id),
                "source_owner_member_id": str(right.owner_member_id),
                "intended_recipient_member_id": str(obligation.creditor_member_id),
                "quantity": decimal_text(fulfillment.quantity),
                "evidence": self._evidence_payload(evidence),
            },
        )
        session.add(
            FulfillmentProvenance(
                fulfillment_id=fulfillment.id,
                cooperative_id=obligation.cooperative_id,
                redemption_id=redemption.id,
                right_id=right.id,
                lot_id=lot.id,
                product_id=lot.product_id,
                source_owner_member_id=right.owner_member_id,
                intended_recipient_member_id=obligation.creditor_member_id,
                quantity=fulfillment.quantity,
                linked_event_id=event.event_id,
            )
        )
        self._link_evidence(
            session,
            evidence,
            event.event_id,
            "fulfillment_provenance",
            fulfillment.id,
        )
        await self._audit(
            session,
            principal,
            obligation.cooperative_id,
            "FULFILLMENT_PROVENANCE_RECONCILED",
            "FulfillmentProvenance",
            fulfillment.id,
            event.event_id,
            request_id,
        )
        return complete_exchange_command(record, event.event_id, fulfillment.id)
    async def accept_fulfillment(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        fulfillment_id: UUID,
        accepted_quantity: Decimal,
        quality_status: str,
        notes: str,
        evidence_ids: Sequence[UUID],
        expected_fulfillment_version: int,
        expected_obligation_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ExchangeCommandResult:
        accepted = exact_quantity(accepted_quantity, allow_zero=True)
        payload = {
            "fulfillment_id": str(fulfillment_id),
            "accepted_quantity": decimal_text(accepted),
            "quality_status": self._text(quality_status, "QUALITY_STATUS_INVALID", 200),
            "notes": self._text(notes, "ACCEPTANCE_NOTES_INVALID", 2000),
            "evidence_ids": [str(value) for value in evidence_ids],
            "expected_fulfillment_version": expected_fulfillment_version,
            "expected_obligation_version": expected_obligation_version,
        }
        record, replay = await begin_exchange_command(
            session, principal, "EXCHANGE_ACCEPT_FULFILLMENT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        fulfillment = await session.get(Fulfillment, fulfillment_id, with_for_update=True)
        if fulfillment is None:
            raise exchange_error("FULFILLMENT_NOT_FOUND", 404)
        self._version(fulfillment.version, expected_fulfillment_version)
        if fulfillment.status != FulfillmentStatus.SUBMITTED.value:
            raise exchange_error("FULFILLMENT_NOT_PENDING", 409)
        obligation = await session.get(Obligation, fulfillment.obligation_id, with_for_update=True)
        if obligation is None:
            raise exchange_error("OBLIGATION_NOT_FOUND", 404)
        self._version(obligation.version, expected_obligation_version)
        ensure_obligation_operable(ObligationStatus(obligation.status))
        actor = party_actor(principal, obligation.cooperative_id, obligation.creditor_member_id)
        unit = await self._unit(session, obligation.unit_id)
        ensure_unit_scale(accepted, unit.decimal_scale)
        decision = acceptance_decision(fulfillment.quantity, accepted)
        evidence = await EvidenceService.require_ready(
            session, obligation.cooperative_id, evidence_ids, required=True
        )
        amounts = self._amounts(obligation).accept(fulfillment.quantity, accepted)
        overdue = obligation.due_at < datetime.now(UTC)
        next_status = obligation_status_for(amounts, overdue=overdue)
        acceptance_id = uuid4()
        evidence_refs = (
            *self._evidence_payload(evidence),
            {
                "event_id": str(fulfillment.submitted_event_id),
                "kind": "FULFILLMENT_SUBMISSION",
            },
        )
        event = await self.journal.append(
            session,
            event_type="obligations.fulfillment_accepted",
            aggregate_type="obligation",
            aggregate_id=obligation.id,
            aggregate_version=obligation.version + 1,
            actor=actor,
            payload={
                **payload,
                "acceptance_id": str(acceptance_id),
                "decision": decision.value,
                "quantity_fulfilled_after": decimal_text(amounts.fulfilled),
                "quantity_remaining_after": decimal_text(amounts.remaining),
                "evidence": list(evidence_refs),
            },
            assurance=CommandAssurance(
                exposure=ExposureClaim(
                    category=ExposureCategory.OBLIGATION,
                    effect=ExposureEffect.REDUCE,
                    subject_type="obligation",
                    subject_id=obligation.id,
                    amount=accepted if accepted > 0 else None,
                    unit=str(obligation.unit_id) if accepted > 0 else None,
                    basis_refs=(str(fulfillment.submitted_event_id),),
                ),
                evidence_refs=evidence_refs,
            ),
        )
        session.add(
            AcceptanceRecord(
                id=acceptance_id,
                fulfillment_id=fulfillment.id,
                accepted_quantity=accepted,
                decision=decision.value,
                quality_status=payload["quality_status"],
                notes=payload["notes"],
                accepted_by_user_id=principal.user_id,
                accepted_by_member_id=actor.person_id,
                event_id=event.event_id,
            )
        )
        fulfillment.accepted_quantity = accepted
        fulfillment.status = {
            AcceptanceDecision.ACCEPTED: FulfillmentStatus.ACCEPTED,
            AcceptanceDecision.PARTIALLY_ACCEPTED: FulfillmentStatus.PARTIALLY_ACCEPTED,
            AcceptanceDecision.REJECTED: FulfillmentStatus.REJECTED,
        }[decision].value
        fulfillment.accepted_event_id = event.event_id
        fulfillment.version += 1
        fulfillment.updated_at = datetime.now(UTC)
        obligation.quantity_submitted = amounts.submitted
        obligation.quantity_fulfilled = amounts.fulfilled
        obligation.status = next_status.value
        obligation.last_event_id = event.event_id
        obligation.version += 1
        obligation.updated_at = datetime.now(UTC)
        await self._refresh_deal_status(session, obligation.deal_id)
        self._link_evidence(session, evidence, event.event_id, "acceptance", acceptance_id)
        await self._audit(
            session,
            principal,
            obligation.cooperative_id,
            "FULFILLMENT_ACCEPTED",
            "Fulfillment",
            fulfillment.id,
            event.event_id,
            request_id,
        )
        return complete_exchange_command(record, event.event_id, fulfillment.id)

    async def open_dispute(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        obligation_id: UUID,
        fulfillment_id: UUID | None,
        reason_code: str,
        statement: str,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ExchangeCommandResult:
        payload = {
            "obligation_id": str(obligation_id),
            "fulfillment_id": str(fulfillment_id) if fulfillment_id else None,
            "reason_code": self._text(reason_code, "DISPUTE_REASON_INVALID", 80),
            "statement": self._text(statement, "DISPUTE_STATEMENT_INVALID", 4000),
            "evidence_ids": [str(value) for value in evidence_ids],
            "expected_version": expected_version,
        }
        record, replay = await begin_exchange_command(
            session, principal, "EXCHANGE_OPEN_OBLIGATION_DISPUTE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        obligation = await session.get(Obligation, obligation_id, with_for_update=True)
        if obligation is None:
            raise exchange_error("OBLIGATION_NOT_FOUND", 404)
        self._version(obligation.version, expected_version)
        if principal.member_id not in {
            obligation.debtor_member_id,
            obligation.creditor_member_id,
        }:
            raise exchange_error("DISPUTE_NOT_A_PARTY", 403)
        actor = party_actor(principal, obligation.cooperative_id, principal.member_id)
        open_case = (
            await session.execute(
                select(ObligationDispute.id).where(
                    ObligationDispute.obligation_id == obligation.id,
                    ObligationDispute.status == "OPEN",
                )
            )
        ).scalar_one_or_none()
        if open_case is not None:
            raise exchange_error("OBLIGATION_DISPUTE_ALREADY_OPEN", 409)
        fulfillment = None
        if fulfillment_id is not None:
            fulfillment = await session.get(Fulfillment, fulfillment_id, with_for_update=True)
            if fulfillment is None or fulfillment.obligation_id != obligation.id:
                raise exchange_error("FULFILLMENT_NOT_FOUND", 404)
        evidence = await EvidenceService.require_ready(
            session, obligation.cooperative_id, evidence_ids, required=True
        )
        dispute_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="obligations.obligation_disputed",
            aggregate_type="obligation",
            aggregate_id=obligation.id,
            aggregate_version=obligation.version + 1,
            actor=actor,
            payload={
                **payload,
                "dispute_id": str(dispute_id),
                "opened_by_member_id": str(actor.person_id),
                "evidence": self._evidence_payload(evidence),
            },
        )
        session.add(
            ObligationDispute(
                id=dispute_id,
                obligation_id=obligation.id,
                fulfillment_id=fulfillment_id,
                reason_code=payload["reason_code"],
                statement=payload["statement"],
                status="OPEN",
                previous_obligation_status=obligation.status,
                previous_fulfillment_status=fulfillment.status if fulfillment is not None else None,
                opened_by_user_id=principal.user_id,
                opened_by_member_id=actor.person_id,
                event_id=event.event_id,
            )
        )
        obligation.status = ObligationStatus.DISPUTED.value
        obligation.last_event_id = event.event_id
        obligation.version += 1
        obligation.updated_at = datetime.now(UTC)
        if fulfillment is not None:
            fulfillment.status = FulfillmentStatus.DISPUTED.value
            fulfillment.version += 1
            fulfillment.updated_at = datetime.now(UTC)
        deal = await session.get(Deal, obligation.deal_id, with_for_update=True)
        if deal is not None:
            deal.status = DealStatus.DISPUTED.value
            deal.version += 1
            deal.updated_at = datetime.now(UTC)
        self._link_evidence(session, evidence, event.event_id, "obligation_dispute", dispute_id)
        await self._audit(
            session,
            principal,
            obligation.cooperative_id,
            "OBLIGATION_DISPUTED",
            "ObligationDispute",
            dispute_id,
            event.event_id,
            request_id,
        )
        return complete_exchange_command(record, event.event_id, dispute_id)

    async def resolve_dispute(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        dispute_id: UUID,
        resolution_action: str,
        resolution_notes: str,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ExchangeCommandResult:
        action = self._text(resolution_action, "DISPUTE_RESOLUTION_ACTION_INVALID", 32).upper()
        if action not in {
            "REJECT_CLAIM",
            "CONTINUE_PERFORMANCE",
            "DEFAULT_OBLIGATION",
            "CLOSE_OBLIGATION",
        }:
            raise exchange_error("DISPUTE_RESOLUTION_ACTION_INVALID")
        notes = self._text(resolution_notes, "DISPUTE_RESOLUTION_NOTES_INVALID", 4000)
        payload = {
            "dispute_id": str(dispute_id),
            "resolution_action": action,
            "resolution_notes": notes,
            "evidence_ids": [str(value) for value in evidence_ids],
            "expected_version": expected_version,
        }
        record, replay = await begin_exchange_command(
            session, principal, "EXCHANGE_RESOLVE_OBLIGATION_DISPUTE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        dispute = await session.get(ObligationDispute, dispute_id, with_for_update=True)
        if dispute is None:
            raise exchange_error("OBLIGATION_DISPUTE_NOT_FOUND", 404)
        self._version(dispute.version, expected_version)
        if dispute.status != "OPEN":
            raise exchange_error("OBLIGATION_DISPUTE_NOT_OPEN", 409)
        obligation = await session.get(Obligation, dispute.obligation_id, with_for_update=True)
        if obligation is None:
            raise exchange_error("OBLIGATION_NOT_FOUND", 404)
        actor = role_actor(principal, obligation.cooperative_id, DISPUTE_RESOLVER_ROLES)
        if actor.person_id in {
            obligation.debtor_member_id,
            obligation.creditor_member_id,
            dispute.opened_by_member_id,
        }:
            raise exchange_error("DISPUTE_RESOLVER_CONFLICT", 403)
        fulfillment = None
        if dispute.fulfillment_id is not None:
            fulfillment = await session.get(
                Fulfillment, dispute.fulfillment_id, with_for_update=True
            )
            if fulfillment is None or fulfillment.obligation_id != obligation.id:
                raise exchange_error("FULFILLMENT_NOT_FOUND", 404)
        evidence = await EvidenceService.require_ready(
            session, obligation.cooperative_id, evidence_ids, required=True
        )
        event = await self.journal.append(
            session,
            event_type="disputes.obligation_dispute_resolved",
            aggregate_type="obligation_dispute",
            aggregate_id=dispute.id,
            aggregate_version=dispute.version + 1,
            actor=actor,
            payload={
                **payload,
                "obligation_id": str(obligation.id),
                "opened_by_member_id": str(dispute.opened_by_member_id),
                "resolved_by_member_id": str(actor.person_id),
                "previous_obligation_status": dispute.previous_obligation_status,
                "previous_fulfillment_status": dispute.previous_fulfillment_status,
                "evidence": self._evidence_payload(evidence),
            },
        )
        now = datetime.now(UTC)
        dispute.status = "REJECTED" if action == "REJECT_CLAIM" else "RESOLVED"
        dispute.resolution_action = action
        dispute.resolution_notes = notes
        dispute.resolved_by_user_id = principal.user_id
        dispute.resolved_by_member_id = actor.person_id
        dispute.resolution_event_id = event.event_id
        dispute.resolved_at = now
        dispute.version += 1
        if action in {"REJECT_CLAIM", "CONTINUE_PERFORMANCE"}:
            obligation.status = dispute.previous_obligation_status
        elif action == "DEFAULT_OBLIGATION":
            obligation.status = ObligationStatus.DEFAULTED.value
        else:
            obligation.status = ObligationStatus.CLOSED.value
        obligation.last_event_id = event.event_id
        obligation.version += 1
        obligation.updated_at = now
        if fulfillment is not None and dispute.previous_fulfillment_status is not None:
            fulfillment.status = dispute.previous_fulfillment_status
            fulfillment.version += 1
            fulfillment.updated_at = now
        await self._refresh_deal_status(session, obligation.deal_id)
        self._link_evidence(
            session, evidence, event.event_id, "obligation_dispute_resolution", dispute.id
        )
        await self._audit(
            session,
            principal,
            obligation.cooperative_id,
            "OBLIGATION_DISPUTE_RESOLVED",
            "ObligationDispute",
            dispute.id,
            event.event_id,
            request_id,
        )
        return complete_exchange_command(record, event.event_id, dispute.id)

    async def mark_overdue(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        as_of: datetime,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ExchangeCommandResult:
        actor = role_actor(principal, cooperative_id, OVERDUE_ROLES)
        cutoff = as_of.astimezone(UTC)
        payload = {"cooperative_id": str(cooperative_id), "as_of": cutoff.isoformat()}
        record, replay = await begin_exchange_command(
            session, principal, "EXCHANGE_MARK_OVERDUE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        obligations = list(
            (
                await session.execute(
                    select(Obligation)
                    .where(
                        Obligation.cooperative_id == cooperative_id,
                        Obligation.due_at < cutoff,
                        Obligation.status.in_(
                            [
                                ObligationStatus.ACTIVE.value,
                                ObligationStatus.PARTIALLY_FULFILLED.value,
                            ]
                        ),
                    )
                    .order_by(Obligation.due_at, Obligation.id)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        changed_ids: list[str] = []
        for obligation in obligations:
            event = await self.journal.append(
                session,
                event_type="obligations.obligation_overdue",
                aggregate_type="obligation",
                aggregate_id=obligation.id,
                aggregate_version=obligation.version + 1,
                actor=actor,
                payload={
                    "obligation_id": str(obligation.id),
                    "due_at": obligation.due_at.isoformat(),
                    "as_of": cutoff.isoformat(),
                    "quantity_remaining": decimal_text(
                        obligation.quantity_total
                        - obligation.quantity_fulfilled
                        - obligation.quantity_cleared
                    ),
                },
            )
            obligation.status = ObligationStatus.OVERDUE.value
            obligation.last_event_id = event.event_id
            obligation.version += 1
            obligation.updated_at = datetime.now(UTC)
            changed_ids.append(str(obligation.id))
        scan_id = uuid4()
        final_event = await self.journal.append(
            session,
            event_type="obligations.overdue_scan_completed",
            aggregate_type="overdue_scan",
            aggregate_id=scan_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "obligation_ids": changed_ids, "changed_count": len(changed_ids)},
        )
        await self._audit(
            session,
            principal,
            cooperative_id,
            "OBLIGATIONS_MARKED_OVERDUE",
            "OverdueScan",
            scan_id,
            final_event.event_id,
            request_id,
        )
        return complete_exchange_command(record, final_event.event_id, scan_id)

    async def create_logistics_order(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        obligation_id: UUID,
        carrier_member_id: UUID,
        quantity: Decimal,
        origin_text: str,
        destination_text: str,
        pickup_due_at: datetime,
        delivery_due_at: datetime,
        expected_obligation_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ExchangeCommandResult:
        obligation = await session.get(Obligation, obligation_id, with_for_update=True)
        if obligation is None:
            raise exchange_error("OBLIGATION_NOT_FOUND", 404)
        actor = role_actor(principal, obligation.cooperative_id, DEAL_OPERATOR_ROLES)
        amount = exact_quantity(quantity)
        payload = {
            "obligation_id": str(obligation_id),
            "carrier_member_id": str(carrier_member_id),
            "quantity": decimal_text(amount),
            "origin_text": self._text(origin_text, "LOGISTICS_ORIGIN_INVALID", 500),
            "destination_text": self._text(destination_text, "LOGISTICS_DESTINATION_INVALID", 500),
            "pickup_due_at": pickup_due_at.astimezone(UTC).isoformat(),
            "delivery_due_at": delivery_due_at.astimezone(UTC).isoformat(),
            "expected_obligation_version": expected_obligation_version,
        }
        record, replay = await begin_exchange_command(
            session, principal, "EXCHANGE_CREATE_LOGISTICS_ORDER", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(obligation.version, expected_obligation_version)
        ensure_obligation_operable(ObligationStatus(obligation.status))
        if pickup_due_at.astimezone(UTC) >= delivery_due_at.astimezone(UTC):
            raise exchange_error("LOGISTICS_WINDOW_INVALID")
        unit = await self._unit(session, obligation.unit_id)
        ensure_unit_scale(amount, unit.decimal_scale)
        await self._eligible_logistics_member(session, obligation.cooperative_id, carrier_member_id)
        committed = await session.scalar(
            select(func.coalesce(func.sum(LogisticsOrder.quantity), 0)).where(
                LogisticsOrder.obligation_id == obligation.id,
                LogisticsOrder.status.in_(
                    [
                        LogisticsStatus.OFFERED.value,
                        LogisticsStatus.ACCEPTED.value,
                        LogisticsStatus.IN_TRANSIT.value,
                    ]
                ),
            )
        )
        available = (
            obligation.quantity_total - obligation.quantity_fulfilled - obligation.quantity_cleared
        )
        if Decimal(committed or 0) + amount > available:
            raise exchange_error("LOGISTICS_QUANTITY_EXCEEDED", 409)
        order_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="logistics.order_offered",
            aggregate_type="logistics_order",
            aggregate_id=order_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "logistics_order_id": str(order_id)},
        )
        session.add(
            LogisticsOrder(
                id=order_id,
                obligation_id=obligation.id,
                cooperative_id=obligation.cooperative_id,
                carrier_member_id=carrier_member_id,
                quantity=amount,
                unit_id=obligation.unit_id,
                origin_text=payload["origin_text"],
                destination_text=payload["destination_text"],
                pickup_due_at=pickup_due_at.astimezone(UTC),
                delivery_due_at=delivery_due_at.astimezone(UTC),
                status=LogisticsStatus.OFFERED.value,
                offered_by_user_id=principal.user_id,
                offered_event_id=event.event_id,
                version=1,
            )
        )
        await self._audit(
            session,
            principal,
            obligation.cooperative_id,
            "LOGISTICS_ORDER_OFFERED",
            "LogisticsOrder",
            order_id,
            event.event_id,
            request_id,
        )
        return complete_exchange_command(record, event.event_id, order_id)

    async def transition_logistics_order(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        order_id: UUID,
        action: str,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ExchangeCommandResult:
        payload = {
            "order_id": str(order_id),
            "action": action,
            "evidence_ids": [str(value) for value in evidence_ids],
            "expected_version": expected_version,
        }
        record, replay = await begin_exchange_command(
            session,
            principal,
            f"EXCHANGE_LOGISTICS_{action.upper()}",
            idempotency_key,
            payload,
        )
        if replay is not None:
            return replay
        order = await session.get(LogisticsOrder, order_id, with_for_update=True)
        if order is None:
            raise exchange_error("LOGISTICS_ORDER_NOT_FOUND", 404)
        self._version(order.version, expected_version)
        actor = role_actor(principal, order.cooperative_id, LOGISTICS_ROLES)
        if actor.person_id != order.carrier_member_id:
            raise exchange_error("LOGISTICS_CARRIER_MISMATCH", 403)
        if action != "accept" and order.carrier_user_id != principal.user_id:
            raise exchange_error("LOGISTICS_RESPONSIBLE_USER_CHANGED", 409)
        current = LogisticsStatus(order.status)
        target = next_logistics_status(current, action)
        evidence = await EvidenceService.require_ready(
            session,
            order.cooperative_id,
            evidence_ids,
            required=action in {"pickup", "deliver"},
        )
        event = await self.journal.append(
            session,
            event_type=(
                f"logistics.order_{action}ed" if action != "pickup" else "logistics.order_picked_up"
            ),
            aggregate_type="logistics_order",
            aggregate_id=order.id,
            aggregate_version=order.version + 1,
            actor=actor,
            payload={
                **payload,
                "from_status": current.value,
                "to_status": target.value,
                "carrier_member_id": str(actor.person_id),
                "evidence": self._evidence_payload(evidence),
            },
        )
        now = datetime.now(UTC)
        order.status = target.value
        order.version += 1
        order.updated_at = now
        if action == "accept":
            order.carrier_user_id = principal.user_id
            order.carrier_role_assignment_id = actor.role_assignment_id
            order.accepted_event_id = event.event_id
            order.accepted_at = now
        elif action == "pickup":
            order.pickup_event_id = event.event_id
            order.picked_up_at = now
        else:
            order.delivered_event_id = event.event_id
            order.delivered_at = now
        self._link_evidence(session, evidence, event.event_id, "logistics_order", order.id)
        audit_action = {
            "accept": "LOGISTICS_ORDER_ACCEPTED",
            "pickup": "LOGISTICS_ORDER_PICKED_UP",
            "deliver": "LOGISTICS_ORDER_DELIVERED",
        }[action]
        await self._audit(
            session,
            principal,
            order.cooperative_id,
            audit_action,
            "LogisticsOrder",
            order.id,
            event.event_id,
            request_id,
        )
        return complete_exchange_command(record, event.event_id, order.id)

    async def _product_fulfillment_source(
        self,
        session: AsyncSession,
        *,
        obligation: Obligation,
        amount: Decimal,
        performed_at: datetime,
        source_redemption_id: UUID | None,
    ) -> tuple[RightRedemption, CommodityRight, InventoryLot] | None:
        if obligation.subject_type != "PRODUCT":
            if source_redemption_id is not None:
                raise exchange_error("FULFILLMENT_SOURCE_NOT_ALLOWED", 409)
            return None
        if obligation.subject_id is None:
            raise exchange_error("PRODUCT_SUBJECT_REQUIRED", 409)
        if source_redemption_id is None:
            raise exchange_error("FULFILLMENT_SOURCE_REQUIRED", 409)
        redemption = await session.get(
            RightRedemption,
            source_redemption_id,
            with_for_update=True,
        )
        if (
            redemption is None
            or redemption.status != "COMPLETED"
            or redemption.completed_event_id is None
            or redemption.completed_at is None
        ):
            raise exchange_error("FULFILLMENT_SOURCE_NOT_COMPLETED", 409)
        used_by = (
            await session.execute(
                select(FulfillmentProvenance.fulfillment_id).where(
                    FulfillmentProvenance.redemption_id == redemption.id
                )
            )
        ).scalar_one_or_none()
        if used_by is not None:
            raise exchange_error("FULFILLMENT_SOURCE_ALREADY_USED", 409)
        right = await session.get(CommodityRight, redemption.right_id)
        lot = await session.get(InventoryLot, redemption.lot_id)
        if right is None or lot is None:
            raise exchange_error("FULFILLMENT_SOURCE_BROKEN", 409)
        if (
            right.status != "REDEEMED"
            or right.redeemed_event_id != redemption.completed_event_id
            or redemption.right_id != right.id
            or redemption.lot_id != right.lot_id
            or right.lot_id != lot.id
            or redemption.owner_member_id != right.owner_member_id
            or right.owner_member_id != obligation.debtor_member_id
            or right.cooperative_id != obligation.cooperative_id
            or lot.cooperative_id != obligation.cooperative_id
            or lot.product_id != obligation.subject_id
            or right.unit_id != obligation.unit_id
            or lot.unit_id != obligation.unit_id
            or redemption.quantity != right.quantity
            or redemption.quantity != amount
        ):
            raise exchange_error("FULFILLMENT_SOURCE_MISMATCH", 409)
        if redemption.completed_at > performed_at.astimezone(UTC):
            raise exchange_error("FULFILLMENT_SOURCE_AFTER_PERFORMANCE", 409)
        return redemption, right, lot
    async def _terms_payload(
        self,
        session: AsyncSession,
        cooperative_id: UUID,
        obligations: Sequence[ObligationDraft],
    ) -> dict[str, object]:
        if not 1 <= len(obligations) <= 20:
            raise exchange_error("DEAL_OBLIGATIONS_INVALID")
        rows: list[dict[str, object]] = []
        parties: set[UUID] = set()
        for sequence_no, draft in enumerate(obligations, start=1):
            if draft.debtor_member_id == draft.creditor_member_id:
                raise exchange_error("OBLIGATION_PARTIES_IDENTICAL")
            await self._eligible_member(session, cooperative_id, draft.debtor_member_id)
            await self._eligible_member(session, cooperative_id, draft.creditor_member_id)
            unit = await self._unit(session, draft.unit_id)
            amount = exact_quantity(draft.quantity)
            ensure_unit_scale(amount, unit.decimal_scale)
            due_at = draft.due_at.astimezone(UTC)
            subject_type = self._text(draft.subject_type, "SUBJECT_TYPE_INVALID", 32).upper()
            if subject_type not in {"PRODUCT", "SERVICE", "LOGISTICS", "OTHER"}:
                raise exchange_error("SUBJECT_TYPE_INVALID")
            if subject_type == "PRODUCT":
                if draft.subject_id is None:
                    raise exchange_error("PRODUCT_SUBJECT_REQUIRED")
                product = await session.get(Product, draft.subject_id)
                if (
                    product is None
                    or product.cooperative_id != cooperative_id
                    or product.status != "ACTIVE"
                ):
                    raise exchange_error("PRODUCT_NOT_AVAILABLE", 409)
                if product.default_unit_id != draft.unit_id:
                    raise exchange_error("PRODUCT_UNIT_MISMATCH", 409)
            liquidity_class = draft.liquidity_class.upper()
            if liquidity_class not in {"UNASSESSED", "A", "B", "C", "D", "E"}:
                raise exchange_error("LIQUIDITY_CLASS_INVALID")
            rows.append(
                {
                    "sequence_no": sequence_no,
                    "debtor_member_id": str(draft.debtor_member_id),
                    "creditor_member_id": str(draft.creditor_member_id),
                    "subject_type": subject_type,
                    "subject_id": str(draft.subject_id) if draft.subject_id else None,
                    "description": self._text(draft.description, "DESCRIPTION_INVALID", 4000),
                    "quality_criteria": self._text(
                        draft.quality_criteria, "QUALITY_CRITERIA_INVALID", 4000
                    ),
                    "fulfillment_place": self._text(
                        draft.fulfillment_place, "FULFILLMENT_PLACE_INVALID", 500
                    ),
                    "due_at": due_at.isoformat(),
                    "unit_id": str(draft.unit_id),
                    "quantity": decimal_text(amount),
                    "partial_allowed": draft.partial_allowed,
                    "evidence_required": draft.evidence_required,
                    "confirmation_method": self._text(
                        draft.confirmation_method, "CONFIRMATION_METHOD_INVALID", 200
                    ),
                    "substitute_policy": self._text(
                        draft.substitute_policy, "SUBSTITUTE_POLICY_INVALID", 4000
                    ),
                    "valuation_source": self._text(
                        draft.valuation_source, "VALUATION_SOURCE_INVALID", 300
                    ),
                    "liquidity_class": liquidity_class,
                    "clearing_allowed": draft.clearing_allowed,
                }
            )
            parties.update({draft.debtor_member_id, draft.creditor_member_id})
        return {
            "required_party_ids": sorted(str(value) for value in parties),
            "obligations": rows,
        }

    @staticmethod
    def _add_deal_parties(
        session: AsyncSession,
        deal_id: UUID,
        terms_version: int,
        terms_hash: str,
        terms_payload: dict[str, object],
        event_id: UUID,
    ) -> None:
        raw_parties = terms_payload.get("required_party_ids")
        if not isinstance(raw_parties, list) or not all(
            isinstance(value, str) for value in raw_parties
        ):
            raise exchange_error("DEAL_TERMS_INVALID", 500)
        session.add_all(
            [
                DealParty(
                    id=uuid4(),
                    deal_id=deal_id,
                    terms_version=terms_version,
                    terms_hash=terms_hash,
                    member_id=UUID(value),
                    created_event_id=event_id,
                )
                for value in raw_parties
            ]
        )

    async def _create_obligations(
        self,
        session: AsyncSession,
        deal: Deal,
        terms_payload: dict[str, object],
        actor: object,
        confirmed_event_id: UUID,
    ) -> tuple[Obligation, ...]:
        from cooperative_clearing.modules.journal.application.service import ActorClaim

        if not isinstance(actor, ActorClaim):
            raise exchange_error("ACTOR_CLAIM_INVALID", 500)
        rows = terms_payload["obligations"]
        if not isinstance(rows, list):
            raise exchange_error("DEAL_TERMS_INVALID", 500)
        created: list[Obligation] = []
        for raw in rows:
            if not isinstance(raw, dict):
                raise exchange_error("DEAL_TERMS_INVALID", 500)
            obligation_id = uuid4()
            event = await self.journal.append(
                session,
                event_type="obligations.obligation_created",
                aggregate_type="obligation",
                aggregate_id=obligation_id,
                aggregate_version=1,
                actor=actor,
                payload={
                    "obligation_id": str(obligation_id),
                    "deal_id": str(deal.id),
                    "terms_version": deal.terms_version,
                    "terms_hash": deal.terms_hash,
                    "deal_confirmed_event_id": str(confirmed_event_id),
                    **raw,
                },
            )
            obligation = Obligation(
                id=obligation_id,
                deal_id=deal.id,
                cooperative_id=deal.cooperative_id,
                sequence_no=int(raw["sequence_no"]),
                terms_version=deal.terms_version,
                debtor_member_id=UUID(str(raw["debtor_member_id"])),
                creditor_member_id=UUID(str(raw["creditor_member_id"])),
                subject_type=str(raw["subject_type"]),
                subject_id=UUID(str(raw["subject_id"])) if raw["subject_id"] else None,
                description=str(raw["description"]),
                quality_criteria=str(raw["quality_criteria"]),
                fulfillment_place=str(raw["fulfillment_place"]),
                due_at=datetime.fromisoformat(str(raw["due_at"])),
                unit_id=UUID(str(raw["unit_id"])),
                quantity_total=Decimal(str(raw["quantity"])),
                quantity_submitted=Decimal(0),
                quantity_fulfilled=Decimal(0),
                quantity_cleared=Decimal(0),
                clearing_allowed=bool(raw.get("clearing_allowed", False)),
                partial_allowed=bool(raw["partial_allowed"]),
                evidence_required=bool(raw["evidence_required"]),
                confirmation_method=str(raw["confirmation_method"]),
                substitute_policy=str(raw["substitute_policy"]),
                valuation_source=str(raw["valuation_source"]),
                liquidity_class=str(raw["liquidity_class"]),
                status=ObligationStatus.ACTIVE.value,
                created_event_id=event.event_id,
                last_event_id=event.event_id,
                version=1,
            )
            session.add(obligation)
            created.append(obligation)
        return tuple(created)

    async def _refresh_deal_status(self, session: AsyncSession, deal_id: UUID) -> None:
        deal = await session.get(Deal, deal_id, with_for_update=True)
        if deal is None:
            return
        open_dispute = (
            await session.execute(
                select(ObligationDispute.id)
                .join(Obligation, Obligation.id == ObligationDispute.obligation_id)
                .where(
                    Obligation.deal_id == deal_id,
                    ObligationDispute.status == "OPEN",
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if open_dispute is not None:
            target = DealStatus.DISPUTED
        else:
            obligations = list(
                (
                    await session.execute(select(Obligation).where(Obligation.deal_id == deal_id))
                ).scalars()
            )
            statuses = {item.status for item in obligations}
            if ObligationStatus.DEFAULTED.value in statuses:
                target = DealStatus.DEFAULTED
            elif statuses and statuses.issubset(
                {ObligationStatus.FULFILLED.value, ObligationStatus.CLOSED.value}
            ):
                target = DealStatus.FULFILLED
            elif any(
                item.quantity_fulfilled > 0 or item.quantity_cleared > 0 for item in obligations
            ):
                target = DealStatus.PARTIALLY_FULFILLED
            else:
                target = DealStatus.ACTIVE
        if deal.status != target.value:
            deal.status = target.value
            deal.version += 1
            deal.updated_at = datetime.now(UTC)

    @staticmethod
    async def _eligible_member(
        session: AsyncSession, cooperative_id: UUID, member_id: UUID
    ) -> None:
        member = await session.get(Member, member_id)
        membership = (
            await session.execute(
                select(Membership.id).where(
                    Membership.cooperative_id == cooperative_id,
                    Membership.member_id == member_id,
                    Membership.status == "ACTIVE",
                )
            )
        ).scalar_one_or_none()
        if member is None or member.status != "ACTIVE" or membership is None:
            raise exchange_error("PARTY_NOT_ELIGIBLE", 409)

    @staticmethod
    async def _eligible_logistics_member(
        session: AsyncSession, cooperative_id: UUID, member_id: UUID
    ) -> None:
        await ExchangeService._eligible_member(session, cooperative_id, member_id)
        role = (
            await session.execute(
                select(RoleAssignment.id)
                .join(UserAccount, UserAccount.id == RoleAssignment.user_id)
                .where(
                    UserAccount.member_id == member_id,
                    UserAccount.status == "ACTIVE",
                    RoleAssignment.role_code == RoleCode.LOGISTICS_OPERATOR.value,
                    RoleAssignment.status == "ACTIVE",
                    or_(
                        RoleAssignment.cooperative_id == cooperative_id,
                        RoleAssignment.cooperative_id.is_(None),
                    ),
                )
            )
        ).scalar_one_or_none()
        if role is None:
            raise exchange_error("LOGISTICS_OPERATOR_NOT_ELIGIBLE", 409)

    @staticmethod
    async def _unit(session: AsyncSession, unit_id: UUID) -> UnitOfMeasure:
        unit = await session.get(UnitOfMeasure, unit_id)
        if unit is None or unit.status != "ACTIVE":
            raise exchange_error("UNIT_NOT_AVAILABLE", 409)
        return unit

    @staticmethod
    def _amounts(obligation: Obligation) -> ObligationAmounts:
        return ObligationAmounts(
            total=obligation.quantity_total,
            submitted=obligation.quantity_submitted,
            fulfilled=obligation.quantity_fulfilled,
            cleared=obligation.quantity_cleared,
        ).validate()

    @staticmethod
    def _version(current: int, expected: int) -> None:
        if current != expected:
            raise exchange_error("VERSION_CONFLICT", 409)

    @staticmethod
    def _text(value: str, code: str, maximum: int) -> str:
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > maximum:
            raise exchange_error(code)
        return normalized

    @staticmethod
    def _evidence_payload(items: Sequence[EvidenceBlob]) -> list[dict[str, object]]:
        return [
            {
                "evidence_id": str(item.id),
                "sha256": item.expected_sha256,
                "size": item.expected_size,
                "kind": item.kind,
            }
            for item in items
        ]

    @staticmethod
    def _link_evidence(
        session: AsyncSession,
        evidence: Sequence[EvidenceBlob],
        event_id: UUID,
        subject_type: str,
        subject_id: UUID,
    ) -> None:
        session.add_all(
            [
                EvidenceLink(
                    id=uuid4(),
                    evidence_id=item.id,
                    event_id=event_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                )
                for item in evidence
            ]
        )

    @staticmethod
    async def _audit(
        session: AsyncSession,
        principal: Principal,
        cooperative_id: UUID,
        action: str,
        object_type: str,
        object_id: UUID,
        event_id: UUID,
        request_id: UUID | None,
    ) -> None:
        await AuditRepository(session).record(
            action=action,
            object_type=object_type,
            object_id=object_id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"signed_event_id": str(event_id)},
        )
