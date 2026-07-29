"""Atomic reservation, commodity-right, transfer, freeze, and redemption commands."""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import Member, Membership
from cooperative_clearing.modules.inventory.application.common import (
    InventoryCommandResult,
    actor_claim,
    begin_command,
    bounded_text,
    complete_command,
)
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.application.service import InventoryService
from cooperative_clearing.modules.inventory.domain.types import (
    LotStatus,
    decimal_text,
    ensure_unit_scale,
    exact_quantity,
)
from cooperative_clearing.modules.inventory.infrastructure.models import (
    EvidenceBlob,
    EvidenceLink,
    InventoryLot,
    InventoryMovement,
    UnitOfMeasure,
)
from cooperative_clearing.modules.journal.application.service import (
    ActorClaim,
    SignedJournalService,
)
from cooperative_clearing.modules.journal.domain.assurance import (
    CommandAssurance,
    ExposureCategory,
    ExposureClaim,
    ExposureEffect,
    actor_party,
    member_party,
)
from cooperative_clearing.modules.rights.domain.types import (
    FREEZABLE_RIGHT_STATUSES,
    BalanceState,
    RedemptionStatus,
    ReservationStatus,
    RightStatus,
    ensure_right_operable,
    ensure_right_owner,
    rights_error,
)
from cooperative_clearing.modules.rights.infrastructure.models import (
    CommodityRight,
    InventoryReservation,
    LotBalance,
    RightRedemption,
    RightTransfer,
)
from cooperative_clearing.shared.core.config import Settings

ISSUE_ROLES = {RoleCode.RIGHTS_OPERATOR, RoleCode.COOPERATIVE_ADMIN}
FREEZE_ROLES = {RoleCode.RISK_ADMIN, RoleCode.AUDITOR}
REDEMPTION_ROLES = {RoleCode.WAREHOUSE_CUSTODIAN}


class CommodityRightsService:
    def __init__(self, settings: Settings) -> None:
        self.journal = SignedJournalService(settings)

    async def issue(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        lot_id: UUID,
        owner_member_id: UUID,
        quantity: Decimal,
        redeem_warehouse_id: UUID,
        valid_until: datetime | None,
        expected_balance_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        amount = exact_quantity(quantity)
        payload = {
            "lot_id": str(lot_id),
            "owner_member_id": str(owner_member_id),
            "quantity": decimal_text(amount),
            "redeem_warehouse_id": str(redeem_warehouse_id),
            "valid_until": valid_until.isoformat() if valid_until else None,
            "expected_balance_version": expected_balance_version,
        }
        record, replay = await begin_command(
            session, principal, "RIGHTS_ISSUE_COMMODITY_RIGHT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        lot = await session.get(InventoryLot, lot_id, with_for_update=True)
        if lot is None:
            raise rights_error("LOT_NOT_FOUND", 404)
        if lot.status != LotStatus.VERIFIED.value or lot.current_quantity is None:
            raise rights_error("LOT_NOT_AVAILABLE", 409)
        if lot.expires_at is not None and lot.expires_at <= datetime.now(UTC):
            raise rights_error("LOT_EXPIRED", 409)
        if redeem_warehouse_id != lot.warehouse_id:
            raise rights_error("REDEMPTION_WAREHOUSE_MISMATCH", 409)
        if valid_until is not None:
            normalized_valid_until = valid_until.astimezone(UTC)
            if normalized_valid_until <= datetime.now(UTC):
                raise rights_error("RIGHT_VALID_UNTIL_INVALID")
            if lot.expires_at is not None and normalized_valid_until > lot.expires_at:
                raise rights_error("RIGHT_OUTLIVES_LOT", 409)
        await self._eligible_member(session, lot.cooperative_id, owner_member_id)
        unit = await session.get(UnitOfMeasure, lot.unit_id)
        if unit is None:
            raise rights_error("LOT_UNIT_NOT_FOUND", 409)
        ensure_unit_scale(amount, unit.decimal_scale)
        balance = await self._locked_balance(session, lot)
        self._version(balance.version, expected_balance_version)
        state = self._state(balance).reserve_and_issue(amount)
        actor = actor_claim(principal, lot.cooperative_id, ISSUE_ROLES)
        right_id = uuid4()
        reservation_id = uuid4()
        backing_evidence = (
            {
                "event_id": str(lot.verified_event_id),
                "kind": "LOT_VERIFICATION",
            },
        )
        reservation_event = await self.journal.append(
            session,
            event_type="inventory.quantity_reserved",
            aggregate_type="lot_balance",
            aggregate_id=lot.id,
            aggregate_version=balance.version + 1,
            actor=actor,
            payload={
                "reservation_id": str(reservation_id),
                "right_id": str(right_id),
                "lot_id": str(lot.id),
                "purpose": "COMMODITY_RIGHT_ISSUANCE",
                "quantity": decimal_text(amount),
                "expires_at": valid_until.isoformat() if valid_until else None,
                "available_before": decimal_text(balance.available_quantity),
            },
            assurance=CommandAssurance(
                on_behalf_of=actor_party(actor),
                next_responsible=(actor_party(actor),),
                exposure=ExposureClaim(
                    category=ExposureCategory.COMMODITY,
                    effect=ExposureEffect.RESERVE,
                    subject_type="inventory_lot",
                    subject_id=lot.id,
                    amount=amount,
                    unit=str(lot.unit_id),
                ),
                evidence_refs=backing_evidence,
            ),
        )
        issued_event = await self.journal.append(
            session,
            event_type="rights.commodity_right_issued",
            aggregate_type="commodity_right",
            aggregate_id=right_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "right_id": str(right_id),
                "reservation_id": str(reservation_id),
                "unit_id": str(lot.unit_id),
                "lot_verified_event_id": str(lot.verified_event_id),
                "available_after": decimal_text(state.available),
                "rights_issued_after": decimal_text(state.issued),
            },
            assurance=CommandAssurance(
                on_behalf_of=actor_party(actor),
                next_responsible=(member_party(owner_member_id),),
                exposure=ExposureClaim(
                    category=ExposureCategory.COMMODITY,
                    effect=ExposureEffect.CREATE,
                    subject_type="commodity_right",
                    subject_id=right_id,
                    amount=amount,
                    unit=str(lot.unit_id),
                ),
                evidence_refs=backing_evidence,
            ),
        )
        session.add(
            InventoryReservation(
                id=reservation_id,
                lot_id=lot.id,
                purpose_type="COMMODITY_RIGHT",
                purpose_id=right_id,
                quantity=amount,
                status=ReservationStatus.CONSUMED.value,
                expires_at=valid_until,
                created_by_user_id=principal.user_id,
                created_event_id=reservation_event.event_id,
                completed_event_id=issued_event.event_id,
            )
        )
        await session.flush()
        session.add(
            CommodityRight(
                id=right_id,
                cooperative_id=lot.cooperative_id,
                lot_id=lot.id,
                owner_member_id=owner_member_id,
                original_owner_member_id=owner_member_id,
                quantity=amount,
                unit_id=lot.unit_id,
                status=RightStatus.ISSUED.value,
                redeem_warehouse_id=redeem_warehouse_id,
                valid_until=valid_until,
                reservation_id=reservation_id,
                issued_by_user_id=principal.user_id,
                issued_by_member_id=actor.person_id,
                issued_role_assignment_id=actor.role_assignment_id,
                issued_event_id=issued_event.event_id,
                version=1,
            )
        )
        self._apply_state(balance, state)
        balance.version += 1
        balance.updated_at = datetime.now(UTC)
        await self._audit(
            session,
            principal,
            lot.cooperative_id,
            "COMMODITY_RIGHT_ISSUED",
            "CommodityRight",
            right_id,
            issued_event.event_id,
            request_id,
        )
        return complete_command(record, issued_event.event_id, right_id)

    async def transfer(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        right_id: UUID,
        from_member_id: UUID,
        to_member_id: UUID,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        payload = {
            "right_id": str(right_id),
            "from_member_id": str(from_member_id),
            "to_member_id": str(to_member_id),
            "evidence_ids": [str(item) for item in evidence_ids],
            "expected_version": expected_version,
        }
        record, replay = await begin_command(
            session, principal, "RIGHTS_TRANSFER_COMMODITY_RIGHT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        right = await session.get(CommodityRight, right_id, with_for_update=True)
        if right is None:
            raise rights_error("RIGHT_NOT_FOUND", 404)
        self._version(right.version, expected_version)
        ensure_right_operable(RightStatus(right.status), right.valid_until)
        ensure_right_owner(right.owner_member_id, from_member_id)
        if from_member_id == to_member_id:
            raise rights_error("RIGHT_TRANSFER_SAME_OWNER")
        await self._eligible_member(session, right.cooperative_id, to_member_id)
        evidence = await EvidenceService.require_ready(
            session, right.cooperative_id, evidence_ids, required=True
        )
        actor = actor_claim(principal, right.cooperative_id, ISSUE_ROLES)
        transfer_id = uuid4()
        evidence_refs = tuple(self._evidence_payload(evidence))
        event = await self.journal.append(
            session,
            event_type="rights.commodity_right_transferred",
            aggregate_type="commodity_right",
            aggregate_id=right.id,
            aggregate_version=right.version + 1,
            actor=actor,
            payload={
                **payload,
                "transfer_id": str(transfer_id),
                "quantity": decimal_text(right.quantity),
                "evidence": list(evidence_refs),
            },
            assurance=CommandAssurance(
                on_behalf_of=actor_party(actor),
                next_responsible=(member_party(to_member_id),),
                exposure=ExposureClaim(
                    category=ExposureCategory.COMMODITY,
                    effect=ExposureEffect.TRANSFER,
                    subject_type="commodity_right",
                    subject_id=right.id,
                    amount=right.quantity,
                    unit=str(right.unit_id),
                ),
                evidence_refs=evidence_refs,
            ),
        )
        session.add(
            RightTransfer(
                id=transfer_id,
                right_id=right.id,
                from_member_id=from_member_id,
                to_member_id=to_member_id,
                quantity=right.quantity,
                performed_by_user_id=principal.user_id,
                event_id=event.event_id,
            )
        )
        right.owner_member_id = to_member_id
        right.status = RightStatus.TRANSFERRED.value
        right.updated_at = datetime.now(UTC)
        right.version += 1
        self._link_evidence(session, evidence, event.event_id, "commodity_right", right.id)
        await self._audit(
            session,
            principal,
            right.cooperative_id,
            "COMMODITY_RIGHT_TRANSFERRED",
            "CommodityRight",
            right.id,
            event.event_id,
            request_id,
        )
        return complete_command(record, event.event_id, right.id)

    async def freeze(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        right_id: UUID,
        reason_code: str,
        decision_reference: str,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        reason = bounded_text(reason_code, "RIGHT_FREEZE_REASON_INVALID", 100).upper()
        decision = bounded_text(decision_reference, "RIGHT_FREEZE_DECISION_INVALID", 500)
        payload = {
            "right_id": str(right_id),
            "reason_code": reason,
            "decision_reference": decision,
            "expected_version": expected_version,
        }
        record, replay = await begin_command(
            session, principal, "RIGHTS_FREEZE_COMMODITY_RIGHT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        right = await session.get(CommodityRight, right_id, with_for_update=True)
        if right is None:
            raise rights_error("RIGHT_NOT_FOUND", 404)
        self._version(right.version, expected_version)
        current = RightStatus(right.status)
        if current not in FREEZABLE_RIGHT_STATUSES:
            raise rights_error("RIGHT_NOT_FREEZABLE", 409)
        actor = actor_claim(principal, right.cooperative_id, FREEZE_ROLES)
        event = await self.journal.append(
            session,
            event_type="rights.commodity_right_frozen",
            aggregate_type="commodity_right",
            aggregate_id=right.id,
            aggregate_version=right.version + 1,
            actor=actor,
            payload={**payload, "previous_status": current.value},
        )
        right.frozen_previous_status = current.value
        right.status = RightStatus.FROZEN.value
        right.freeze_reason = f"{reason}: {decision}"
        right.frozen_by_user_id = principal.user_id
        right.frozen_event_id = event.event_id
        right.updated_at = datetime.now(UTC)
        right.version += 1
        await self._audit(
            session,
            principal,
            right.cooperative_id,
            "COMMODITY_RIGHT_FROZEN",
            "CommodityRight",
            right.id,
            event.event_id,
            request_id,
        )
        return complete_command(record, event.event_id, right.id)

    async def unfreeze(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        right_id: UUID,
        decision_reference: str,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        decision = bounded_text(decision_reference, "RIGHT_UNFREEZE_DECISION_INVALID", 500)
        payload = {
            "right_id": str(right_id),
            "decision_reference": decision,
            "expected_version": expected_version,
        }
        record, replay = await begin_command(
            session, principal, "RIGHTS_UNFREEZE_COMMODITY_RIGHT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        right = await session.get(CommodityRight, right_id, with_for_update=True)
        if right is None:
            raise rights_error("RIGHT_NOT_FOUND", 404)
        self._version(right.version, expected_version)
        if right.status != RightStatus.FROZEN.value or right.frozen_previous_status is None:
            raise rights_error("RIGHT_NOT_FROZEN", 409)
        restored = RightStatus(right.frozen_previous_status)
        if restored not in FREEZABLE_RIGHT_STATUSES:
            raise rights_error("RIGHT_RESTORE_STATUS_INVALID", 409)
        actor = actor_claim(principal, right.cooperative_id, FREEZE_ROLES)
        event = await self.journal.append(
            session,
            event_type="rights.commodity_right_unfrozen",
            aggregate_type="commodity_right",
            aggregate_id=right.id,
            aggregate_version=right.version + 1,
            actor=actor,
            payload={**payload, "restored_status": restored.value},
        )
        right.status = restored.value
        right.frozen_previous_status = None
        right.freeze_reason = None
        right.frozen_by_user_id = None
        right.frozen_event_id = None
        right.updated_at = datetime.now(UTC)
        right.version += 1
        await self._audit(
            session,
            principal,
            right.cooperative_id,
            "COMMODITY_RIGHT_UNFROZEN",
            "CommodityRight",
            right.id,
            event.event_id,
            request_id,
        )
        return complete_command(record, event.event_id, right.id)

    async def request_redemption(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        right_id: UUID,
        owner_member_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        payload = {
            "right_id": str(right_id),
            "owner_member_id": str(owner_member_id),
            "expected_version": expected_version,
        }
        record, replay = await begin_command(
            session, principal, "RIGHTS_REQUEST_REDEMPTION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        right = await session.get(CommodityRight, right_id, with_for_update=True)
        if right is None:
            raise rights_error("RIGHT_NOT_FOUND", 404)
        self._version(right.version, expected_version)
        ensure_right_operable(RightStatus(right.status), right.valid_until)
        ensure_right_owner(right.owner_member_id, owner_member_id)
        lot = await session.get(InventoryLot, right.lot_id, with_for_update=True)
        if lot is None or lot.status != LotStatus.VERIFIED.value:
            raise rights_error("LOT_NOT_AVAILABLE", 409)
        if lot.warehouse_id != right.redeem_warehouse_id:
            raise rights_error("REDEMPTION_WAREHOUSE_CHANGED", 409)
        actor = actor_claim(principal, right.cooperative_id, ISSUE_ROLES)
        redemption_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="rights.commodity_right_redemption_requested",
            aggregate_type="commodity_right",
            aggregate_id=right.id,
            aggregate_version=right.version + 1,
            actor=actor,
            payload={
                **payload,
                "redemption_id": str(redemption_id),
                "lot_id": str(lot.id),
                "warehouse_id": str(lot.warehouse_id),
                "custodian_assignment_id": str(lot.custodian_assignment_id),
                "quantity": decimal_text(right.quantity),
            },
        )
        session.add(
            RightRedemption(
                id=redemption_id,
                right_id=right.id,
                lot_id=lot.id,
                owner_member_id=right.owner_member_id,
                warehouse_id=lot.warehouse_id,
                custodian_assignment_id=lot.custodian_assignment_id,
                quantity=right.quantity,
                status=RedemptionStatus.REQUESTED.value,
                requested_by_user_id=principal.user_id,
                fulfilled_by_user_id=None,
                requested_event_id=event.event_id,
                completed_event_id=None,
            )
        )
        right.status = RightStatus.REDEMPTION_PENDING.value
        right.updated_at = datetime.now(UTC)
        right.version += 1
        await self._audit(
            session,
            principal,
            right.cooperative_id,
            "COMMODITY_RIGHT_REDEMPTION_REQUESTED",
            "RightRedemption",
            redemption_id,
            event.event_id,
            request_id,
        )
        return complete_command(record, event.event_id, redemption_id)

    async def complete_redemption(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        redemption_id: UUID,
        evidence_ids: Sequence[UUID],
        expected_right_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        payload = {
            "redemption_id": str(redemption_id),
            "evidence_ids": [str(item) for item in evidence_ids],
            "expected_right_version": expected_right_version,
        }
        record, replay = await begin_command(
            session, principal, "RIGHTS_COMPLETE_REDEMPTION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        redemption = await session.get(RightRedemption, redemption_id, with_for_update=True)
        if redemption is None:
            raise rights_error("REDEMPTION_NOT_FOUND", 404)
        if redemption.status != RedemptionStatus.REQUESTED.value:
            raise rights_error("REDEMPTION_NOT_PENDING", 409)
        right = await session.get(CommodityRight, redemption.right_id, with_for_update=True)
        if right is None:
            raise rights_error("RIGHT_NOT_FOUND", 404)
        self._version(right.version, expected_right_version)
        if right.status != RightStatus.REDEMPTION_PENDING.value:
            raise rights_error("RIGHT_NOT_PENDING_REDEMPTION", 409)
        lot = await session.get(InventoryLot, redemption.lot_id, with_for_update=True)
        if lot is None or lot.status != LotStatus.VERIFIED.value or lot.current_quantity is None:
            raise rights_error("LOT_NOT_AVAILABLE", 409)
        if lot.continuity_hold_case_id is not None:
            raise rights_error("LOT_CUSTODY_CONTINUITY_HELD", 409)
        if (
            lot.warehouse_id != redemption.warehouse_id
            or lot.custodian_assignment_id != redemption.custodian_assignment_id
        ):
            raise rights_error("REDEMPTION_CUSTODY_CHANGED", 409)
        _responsibility, role = await InventoryService._custody_assignment(
            session,
            redemption.custodian_assignment_id,
            right.cooperative_id,
            redemption.warehouse_id,
            principal=principal,
            allowed_roles=REDEMPTION_ROLES,
        )
        evidence = await EvidenceService.require_ready(
            session, right.cooperative_id, evidence_ids, required=True
        )
        balance = await self._locked_balance(session, lot)
        state = self._state(balance).redeem(redemption.quantity)
        actor = actor_claim(
            principal,
            right.cooperative_id,
            REDEMPTION_ROLES,
            exact_assignment_id=role.id,
        )
        evidence_refs = tuple(self._evidence_payload(evidence))
        event = await self.journal.append(
            session,
            event_type="rights.commodity_right_redeemed",
            aggregate_type="commodity_right",
            aggregate_id=right.id,
            aggregate_version=right.version + 1,
            actor=actor,
            payload={
                **payload,
                "right_id": str(right.id),
                "lot_id": str(lot.id),
                "owner_member_id": str(right.owner_member_id),
                "warehouse_id": str(lot.warehouse_id),
                "custodian_assignment_id": str(lot.custodian_assignment_id),
                "quantity": decimal_text(redemption.quantity),
                "lot_quantity_after": decimal_text(lot.current_quantity - redemption.quantity),
                "evidence": list(evidence_refs),
            },
            assurance=CommandAssurance(
                on_behalf_of=actor_party(actor),
                next_responsible=(member_party(right.owner_member_id),),
                exposure=ExposureClaim(
                    category=ExposureCategory.COMMODITY,
                    effect=ExposureEffect.EXECUTE,
                    subject_type="commodity_right",
                    subject_id=right.id,
                    amount=redemption.quantity,
                    unit=str(right.unit_id),
                ),
                evidence_refs=evidence_refs,
            ),
        )
        self._apply_state(balance, state)
        balance.version += 1
        balance.updated_at = datetime.now(UTC)
        lot.current_quantity -= redemption.quantity
        lot.status = (
            LotStatus.DEPLETED.value if lot.current_quantity == 0 else LotStatus.VERIFIED.value
        )
        lot.updated_at = datetime.now(UTC)
        lot.version += 1
        right.status = RightStatus.REDEEMED.value
        right.redeemed_event_id = event.event_id
        right.updated_at = datetime.now(UTC)
        right.version += 1
        redemption.status = RedemptionStatus.COMPLETED.value
        redemption.fulfilled_by_user_id = principal.user_id
        redemption.completed_event_id = event.event_id
        redemption.completed_at = datetime.now(UTC)
        session.add(
            InventoryMovement(
                id=uuid4(),
                lot_id=lot.id,
                movement_type="RIGHT_REDEMPTION",
                quantity_delta=-redemption.quantity,
                resulting_quantity=lot.current_quantity,
                reason_code="COMMODITY_RIGHT_REDEEMED",
                performed_by_user_id=principal.user_id,
                event_id=event.event_id,
            )
        )
        self._link_evidence(session, evidence, event.event_id, "commodity_right", right.id)
        await self._audit(
            session,
            principal,
            right.cooperative_id,
            "COMMODITY_RIGHT_REDEEMED",
            "RightRedemption",
            redemption.id,
            event.event_id,
            request_id,
        )
        return complete_command(record, event.event_id, redemption.id)

    async def quarantine_for_discrepancy(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        lot: InventoryLot,
        actual_quantity: Decimal,
        actor: ActorClaim,
        discrepancy_event_id: UUID,
        reason_code: str,
    ) -> None:
        balance = await session.get(LotBalance, lot.id, with_for_update=True)
        if balance is None:
            return
        state = self._state(balance).quarantine_physical_count(actual_quantity)
        self._apply_state(balance, state)
        balance.version += 1
        balance.updated_at = datetime.now(UTC)
        rights = list(
            (
                await session.execute(
                    select(CommodityRight)
                    .where(
                        CommodityRight.lot_id == lot.id,
                        CommodityRight.status.in_(
                            [item.value for item in FREEZABLE_RIGHT_STATUSES]
                        ),
                    )
                    .order_by(CommodityRight.id)
                    .with_for_update()
                )
            ).scalars()
        )
        for right in rights:
            previous = right.status
            event = await self.journal.append(
                session,
                event_type="rights.commodity_right_frozen",
                aggregate_type="commodity_right",
                aggregate_id=right.id,
                aggregate_version=right.version + 1,
                actor=actor,
                payload={
                    "right_id": str(right.id),
                    "lot_id": str(lot.id),
                    "reason_code": reason_code,
                    "decision_reference": str(discrepancy_event_id),
                    "previous_status": previous,
                    "automatic_backing_protection": True,
                    "backing_shortfall": decimal_text(state.shortfall),
                },
            )
            right.frozen_previous_status = previous
            right.status = RightStatus.FROZEN.value
            right.freeze_reason = f"{reason_code}: {discrepancy_event_id}"
            right.frozen_by_user_id = principal.user_id
            right.frozen_event_id = event.event_id
            right.updated_at = datetime.now(UTC)
            right.version += 1

    @staticmethod
    async def _eligible_member(
        session: AsyncSession, cooperative_id: UUID, member_id: UUID
    ) -> Member:
        member = await session.get(Member, member_id)
        membership_id = await session.scalar(
            select(Membership.id).where(
                Membership.cooperative_id == cooperative_id,
                Membership.member_id == member_id,
                Membership.status == "ACTIVE",
            )
        )
        if member is None or member.status not in {"ACTIVE", "LIMITED"} or membership_id is None:
            raise rights_error("RIGHT_OWNER_NOT_ELIGIBLE", 409)
        return member

    @staticmethod
    async def _locked_balance(session: AsyncSession, lot: InventoryLot) -> LotBalance:
        balance = await session.get(LotBalance, lot.id, with_for_update=True)
        if balance is None:
            if lot.status != LotStatus.VERIFIED.value or lot.current_quantity is None:
                raise rights_error("LOT_BALANCE_UNAVAILABLE", 409)
            balance = LotBalance(
                lot_id=lot.id,
                verified_quantity=lot.current_quantity,
                available_quantity=lot.current_quantity,
                reserved_quantity=Decimal(0),
                rights_issued_quantity=Decimal(0),
                redeemed_quantity=Decimal(0),
                quarantined_quantity=Decimal(0),
                backing_shortfall_quantity=Decimal(0),
                version=1,
            )
            session.add(balance)
            await session.flush()
        return balance

    @staticmethod
    def _state(balance: LotBalance) -> BalanceState:
        return BalanceState(
            verified=balance.verified_quantity,
            available=balance.available_quantity,
            reserved=balance.reserved_quantity,
            issued=balance.rights_issued_quantity,
            redeemed=balance.redeemed_quantity,
            quarantined=balance.quarantined_quantity,
            shortfall=balance.backing_shortfall_quantity,
        ).validate()

    @staticmethod
    def _apply_state(balance: LotBalance, state: BalanceState) -> None:
        balance.verified_quantity = state.verified
        balance.available_quantity = state.available
        balance.reserved_quantity = state.reserved
        balance.rights_issued_quantity = state.issued
        balance.redeemed_quantity = state.redeemed
        balance.quarantined_quantity = state.quarantined
        balance.backing_shortfall_quantity = state.shortfall

    @staticmethod
    def _version(current: int, expected: int) -> None:
        if current != expected:
            raise rights_error("VERSION_CONFLICT", 409)

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
