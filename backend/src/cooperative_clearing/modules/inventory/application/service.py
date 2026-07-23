"""Transactional inventory receipt, attestation, discrepancy, and custody commands."""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.inventory.application.common import (
    InventoryCommandResult,
    actor_claim,
    begin_command,
    bounded_text,
    complete_command,
    inventory_error,
)
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.domain.types import (
    QUANTITY_QUANTUM,
    CustodyStatus,
    LotStatus,
    QualityDecision,
    decimal_text,
    ensure_can_attest,
    ensure_can_offer_custody,
    ensure_can_record_discrepancy,
    ensure_unit_scale,
    evaluate_attestation,
    exact_quantity,
)
from cooperative_clearing.modules.inventory.infrastructure.models import (
    CustodyTransfer,
    EvidenceBlob,
    EvidenceLink,
    InventoryDiscrepancy,
    InventoryLot,
    InventoryMovement,
    Product,
    QualityInspection,
    StockAttestation,
    UnitOfMeasure,
    Warehouse,
)
from cooperative_clearing.modules.journal.application.service import SignedJournalService
from cooperative_clearing.modules.responsibility.infrastructure.models import (
    ResponsibilityAssignment,
)
from cooperative_clearing.modules.rights.infrastructure.models import LotBalance
from cooperative_clearing.shared.core.config import Settings

RECEIPT_ROLES = {RoleCode.WAREHOUSE_CUSTODIAN}
ATTESTATION_ROLES = {RoleCode.INVENTORY_CONTROLLER, RoleCode.AUDITOR}
DISCREPANCY_ROLES = RECEIPT_ROLES | ATTESTATION_ROLES
CUSTODY_ROLES = {RoleCode.WAREHOUSE_CUSTODIAN, RoleCode.LOGISTICS_OPERATOR}


class InventoryService:
    def __init__(self, settings: Settings) -> None:
        self.journal = SignedJournalService(settings)

    async def register_lot(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        lot_number: str,
        product_id: UUID,
        warehouse_id: UUID,
        owner_member_id: UUID,
        declared_quantity: Decimal,
        unit_id: UUID,
        declared_quality: str,
        expires_at: datetime | None,
        storage_conditions: str,
        custodian_assignment_id: UUID,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        number = bounded_text(lot_number, "LOT_NUMBER_INVALID", 100).upper()
        quality = bounded_text(declared_quality, "LOT_QUALITY_INVALID", 200)
        conditions = bounded_text(storage_conditions, "LOT_CONDITIONS_INVALID", 500)
        quantity = exact_quantity(declared_quantity)
        payload = {
            "cooperative_id": str(cooperative_id),
            "lot_number": number,
            "product_id": str(product_id),
            "warehouse_id": str(warehouse_id),
            "owner_member_id": str(owner_member_id),
            "declared_quantity": decimal_text(quantity),
            "unit_id": str(unit_id),
            "declared_quality": quality,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "storage_conditions": conditions,
            "custodian_assignment_id": str(custodian_assignment_id),
            "evidence_ids": [str(item) for item in evidence_ids],
        }
        record, replay = await begin_command(
            session, principal, "INVENTORY_REGISTER_LOT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        product = await session.get(Product, product_id)
        unit = await session.get(UnitOfMeasure, unit_id)
        warehouse = await session.get(Warehouse, warehouse_id)
        owner = await session.get(Member, owner_member_id)
        if (
            product is None
            or product.cooperative_id != cooperative_id
            or product.status != "ACTIVE"
        ):
            raise inventory_error("PRODUCT_NOT_ACTIVE", 409)
        if unit is None or unit.id != product.default_unit_id or unit.status != "ACTIVE":
            raise inventory_error("LOT_UNIT_MISMATCH", 409)
        ensure_unit_scale(quantity, unit.decimal_scale)
        if warehouse is None or warehouse.cooperative_id != cooperative_id:
            raise inventory_error("WAREHOUSE_NOT_FOUND", 404)
        if warehouse.status != "ACTIVE":
            raise inventory_error("WAREHOUSE_NOT_ACTIVE", 409)
        if owner is None or owner.status not in {"ACTIVE", "LIMITED"}:
            raise inventory_error("LOT_OWNER_NOT_ELIGIBLE", 409)
        owner_membership = await session.scalar(
            select(Membership.id).where(
                Membership.cooperative_id == cooperative_id,
                Membership.member_id == owner_member_id,
                Membership.status == "ACTIVE",
            )
        )
        if owner_membership is None:
            raise inventory_error("LOT_OWNER_MEMBERSHIP_NOT_ACTIVE", 409)
        if product.shelf_life_required and expires_at is None:
            raise inventory_error("LOT_EXPIRY_REQUIRED", 422)
        if expires_at is not None and expires_at.astimezone(UTC) <= datetime.now(UTC):
            raise inventory_error("LOT_EXPIRY_INVALID", 422)
        responsibility, role = await self._custody_assignment(
            session,
            custodian_assignment_id,
            cooperative_id,
            warehouse_id,
            principal=principal,
        )
        evidence = await EvidenceService.require_ready(
            session, cooperative_id, evidence_ids, required=False
        )
        actor = actor_claim(
            principal,
            cooperative_id,
            RECEIPT_ROLES,
            exact_assignment_id=role.id,
        )
        lot_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="inventory.lot_registered",
            aggregate_type="inventory_lot",
            aggregate_id=lot_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "lot_id": str(lot_id),
                "status": LotStatus.PENDING_VERIFICATION.value,
                "evidence": self._evidence_payload(evidence),
            },
        )
        session.add(
            InventoryLot(
                id=lot_id,
                cooperative_id=cooperative_id,
                lot_number=number,
                product_id=product_id,
                warehouse_id=warehouse_id,
                owner_member_id=owner_member_id,
                unit_id=unit_id,
                declared_quantity=quantity,
                current_quantity=None,
                declared_quality=quality,
                verified_quality=None,
                expires_at=expires_at,
                storage_conditions=conditions,
                status=LotStatus.PENDING_VERIFICATION.value,
                received_by_user_id=principal.user_id,
                received_by_member_id=principal.member_id,
                received_role_assignment_id=role.id,
                custodian_assignment_id=responsibility.id,
                registered_event_id=event.event_id,
                verified_event_id=None,
            )
        )
        self._link_evidence(session, evidence, event.event_id, "inventory_lot", lot_id)
        await self._audit(
            session,
            principal,
            cooperative_id,
            "INVENTORY_LOT_REGISTERED",
            "InventoryLot",
            lot_id,
            event.event_id,
            request_id,
        )
        return complete_command(record, event.event_id, lot_id)

    async def attest_lot(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        lot_id: UUID,
        measured_quantity: Decimal,
        quality_decision: QualityDecision,
        verified_quality: str,
        measurements: dict[str, str],
        notes: str,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        measured = exact_quantity(measured_quantity, allow_zero=True)
        grade = bounded_text(verified_quality, "VERIFIED_QUALITY_INVALID", 200)
        normalized_notes = bounded_text(notes, "ATTESTATION_NOTES_INVALID", 1000)
        normalized_measurements = self._measurements(measurements)
        payload = {
            "lot_id": str(lot_id),
            "measured_quantity": decimal_text(measured),
            "quality_decision": quality_decision.value,
            "verified_quality": grade,
            "measurements": normalized_measurements,
            "notes": normalized_notes,
            "evidence_ids": [str(item) for item in evidence_ids],
            "expected_version": expected_version,
        }
        record, replay = await begin_command(
            session, principal, "INVENTORY_ATTEST_LOT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        lot = await session.get(InventoryLot, lot_id, with_for_update=True)
        if lot is None:
            raise inventory_error("LOT_NOT_FOUND", 404)
        ensure_can_attest(LotStatus(lot.status))
        self._version(lot.version, expected_version)
        if lot.received_by_user_id == principal.user_id:
            raise inventory_error("INDEPENDENT_ATTESTER_REQUIRED", 403)
        product = await session.get(Product, lot.product_id)
        unit = await session.get(UnitOfMeasure, lot.unit_id)
        if unit is None:
            raise inventory_error("LOT_UNIT_NOT_FOUND", 409)
        ensure_unit_scale(measured, unit.decimal_scale)
        if product is None:
            raise inventory_error("PRODUCT_NOT_FOUND", 404)
        evidence = await EvidenceService.require_ready(
            session, lot.cooperative_id, evidence_ids, required=product.requires_evidence
        )
        actor = actor_claim(principal, lot.cooperative_id, ATTESTATION_ROLES)
        outcome = evaluate_attestation(
            lot.declared_quantity, measured, product.quantity_tolerance, quality_decision
        )
        attestation_id = uuid4()
        attested = await self.journal.append(
            session,
            event_type="inventory.lot_attested",
            aggregate_type="inventory_lot",
            aggregate_id=lot.id,
            aggregate_version=lot.version + 1,
            actor=actor,
            payload={
                **payload,
                "attestation_id": str(attestation_id),
                "variance": decimal_text(outcome.variance),
                "quantity_decision": outcome.quantity_decision.value,
                "evidence": self._evidence_payload(evidence),
            },
        )
        result_type = (
            "inventory.lot_verified"
            if outcome.lot_status is LotStatus.VERIFIED
            else "inventory.discrepancy_recorded"
        )
        result = await self.journal.append(
            session,
            event_type=result_type,
            aggregate_type="inventory_lot",
            aggregate_id=lot.id,
            aggregate_version=lot.version + 2,
            actor=actor,
            payload={
                "lot_id": str(lot.id),
                "attestation_id": str(attestation_id),
                "declared_quantity": decimal_text(lot.declared_quantity),
                "valid_quantity": decimal_text(outcome.measured_quantity),
                "variance": decimal_text(outcome.variance),
                "quality_decision": quality_decision.value,
                "verified_quality": grade,
                "lot_status": outcome.lot_status.value,
                "evidence": self._evidence_payload(evidence),
            },
        )
        lot.current_quantity = outcome.measured_quantity
        lot.verified_quality = grade
        lot.status = outcome.lot_status.value
        lot.verified_event_id = (
            result.event_id if outcome.lot_status is LotStatus.VERIFIED else None
        )
        lot.updated_at = datetime.now(UTC)
        lot.version += 2
        if outcome.lot_status is LotStatus.VERIFIED:
            session.add(
                LotBalance(
                    lot_id=lot.id,
                    verified_quantity=outcome.measured_quantity,
                    available_quantity=outcome.measured_quantity,
                    reserved_quantity=Decimal(0),
                    rights_issued_quantity=Decimal(0),
                    redeemed_quantity=Decimal(0),
                    quarantined_quantity=Decimal(0),
                    backing_shortfall_quantity=Decimal(0),
                    version=1,
                )
            )
        session.add(
            StockAttestation(
                id=attestation_id,
                lot_id=lot.id,
                measured_quantity=outcome.measured_quantity,
                variance=outcome.variance,
                quantity_decision=outcome.quantity_decision.value,
                quality_decision=quality_decision.value,
                verified_quality=grade,
                measurements=normalized_measurements,
                notes=normalized_notes,
                attested_by_user_id=principal.user_id,
                attested_by_member_id=principal.member_id,
                role_assignment_id=actor.role_assignment_id,
                event_id=attested.event_id,
            )
        )
        await session.flush()
        session.add(
            QualityInspection(
                id=uuid4(),
                lot_id=lot.id,
                attestation_id=attestation_id,
                decision=quality_decision.value,
                quality_grade=grade,
                measurements=normalized_measurements,
                event_id=result.event_id,
            )
        )
        session.add(
            InventoryMovement(
                id=uuid4(),
                lot_id=lot.id,
                movement_type="ATTESTED_RECEIPT",
                quantity_delta=outcome.measured_quantity,
                resulting_quantity=outcome.measured_quantity,
                reason_code="INDEPENDENT_ATTESTATION",
                performed_by_user_id=principal.user_id,
                event_id=result.event_id,
            )
        )
        if outcome.lot_status is not LotStatus.VERIFIED:
            session.add(
                InventoryDiscrepancy(
                    id=uuid4(),
                    lot_id=lot.id,
                    expected_quantity=lot.declared_quantity,
                    actual_quantity=outcome.measured_quantity,
                    variance=outcome.variance,
                    reason_code=(
                        "QUALITY_REJECTED"
                        if quality_decision is QualityDecision.REJECTED
                        else "RECEIPT_QUANTITY_VARIANCE"
                    ),
                    notes=normalized_notes,
                    status="OPEN",
                    recorded_by_user_id=principal.user_id,
                    event_id=result.event_id,
                )
            )
        self._link_evidence(session, evidence, attested.event_id, "inventory_lot", lot.id)
        self._link_evidence(session, evidence, result.event_id, "inventory_lot", lot.id)
        await self._audit(
            session,
            principal,
            lot.cooperative_id,
            "INVENTORY_LOT_ATTESTED",
            "InventoryLot",
            lot.id,
            result.event_id,
            request_id,
        )
        return complete_command(record, result.event_id, lot.id)

    async def record_discrepancy(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        lot_id: UUID,
        actual_quantity: Decimal,
        reason_code: str,
        notes: str,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        actual = exact_quantity(actual_quantity, allow_zero=True)
        reason = bounded_text(reason_code, "DISCREPANCY_REASON_INVALID", 100).upper()
        normalized_notes = bounded_text(notes, "DISCREPANCY_NOTES_INVALID", 1000)
        payload = {
            "lot_id": str(lot_id),
            "actual_quantity": decimal_text(actual),
            "reason_code": reason,
            "notes": normalized_notes,
            "evidence_ids": [str(item) for item in evidence_ids],
            "expected_version": expected_version,
        }
        record, replay = await begin_command(
            session, principal, "INVENTORY_RECORD_DISCREPANCY", idempotency_key, payload
        )
        if replay is not None:
            return replay
        lot = await session.get(InventoryLot, lot_id, with_for_update=True)
        if lot is None:
            raise inventory_error("LOT_NOT_FOUND", 404)
        ensure_can_record_discrepancy(LotStatus(lot.status))
        self._version(lot.version, expected_version)
        expected = lot.current_quantity
        if expected is None:
            raise inventory_error("LOT_QUANTITY_UNVERIFIED", 409)
        unit = await session.get(UnitOfMeasure, lot.unit_id)
        if unit is None:
            raise inventory_error("LOT_UNIT_NOT_FOUND", 409)
        ensure_unit_scale(actual, unit.decimal_scale)
        if actual == expected:
            raise inventory_error("NO_DISCREPANCY", 422)
        evidence = await EvidenceService.require_ready(
            session, lot.cooperative_id, evidence_ids, required=True
        )
        actor = actor_claim(principal, lot.cooperative_id, DISCREPANCY_ROLES)
        variance = (actual - expected).quantize(QUANTITY_QUANTUM)
        discrepancy_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="inventory.discrepancy_recorded",
            aggregate_type="inventory_lot",
            aggregate_id=lot.id,
            aggregate_version=lot.version + 1,
            actor=actor,
            payload={
                **payload,
                "discrepancy_id": str(discrepancy_id),
                "expected_quantity": decimal_text(expected),
                "variance": decimal_text(variance),
                "evidence": self._evidence_payload(evidence),
            },
        )
        lot.current_quantity = actual
        lot.status = LotStatus.LOST.value if actual == 0 else LotStatus.DISPUTED.value
        lot.updated_at = datetime.now(UTC)
        lot.version += 1
        from cooperative_clearing.modules.rights.application.service import (
            CommodityRightsService,
        )

        await CommodityRightsService(self.journal.settings).quarantine_for_discrepancy(
            session,
            principal=principal,
            lot=lot,
            actual_quantity=actual,
            actor=actor,
            discrepancy_event_id=event.event_id,
            reason_code=reason,
        )
        session.add(
            InventoryDiscrepancy(
                id=discrepancy_id,
                lot_id=lot.id,
                expected_quantity=expected,
                actual_quantity=actual,
                variance=variance,
                reason_code=reason,
                notes=normalized_notes,
                status="OPEN",
                recorded_by_user_id=principal.user_id,
                event_id=event.event_id,
            )
        )
        session.add(
            InventoryMovement(
                id=uuid4(),
                lot_id=lot.id,
                movement_type="DISCREPANCY_ADJUSTMENT",
                quantity_delta=variance,
                resulting_quantity=actual,
                reason_code=reason,
                performed_by_user_id=principal.user_id,
                event_id=event.event_id,
            )
        )
        self._link_evidence(session, evidence, event.event_id, "inventory_lot", lot.id)
        await self._audit(
            session,
            principal,
            lot.cooperative_id,
            "INVENTORY_DISCREPANCY_RECORDED",
            "InventoryDiscrepancy",
            discrepancy_id,
            event.event_id,
            request_id,
        )
        return complete_command(record, event.event_id, discrepancy_id)

    async def offer_custody(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        lot_id: UUID,
        to_warehouse_id: UUID,
        to_assignment_id: UUID,
        place: str,
        notes: str,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        normalized_place = bounded_text(place, "CUSTODY_PLACE_INVALID", 500)
        normalized_notes = bounded_text(notes, "CUSTODY_NOTES_INVALID", 1000)
        payload = {
            "lot_id": str(lot_id),
            "to_warehouse_id": str(to_warehouse_id),
            "to_assignment_id": str(to_assignment_id),
            "place": normalized_place,
            "notes": normalized_notes,
            "evidence_ids": [str(item) for item in evidence_ids],
            "expected_version": expected_version,
        }
        record, replay = await begin_command(
            session, principal, "INVENTORY_OFFER_CUSTODY", idempotency_key, payload
        )
        if replay is not None:
            return replay
        lot = await session.get(InventoryLot, lot_id, with_for_update=True)
        if lot is None:
            raise inventory_error("LOT_NOT_FOUND", 404)
        ensure_can_offer_custody(LotStatus(lot.status))
        self._version(lot.version, expected_version)
        from_responsibility, from_role = await self._custody_assignment(
            session,
            lot.custodian_assignment_id,
            lot.cooperative_id,
            lot.warehouse_id,
            principal=principal,
            allowed_roles=CUSTODY_ROLES,
        )
        if from_responsibility.id == to_assignment_id:
            raise inventory_error("CUSTODY_TARGET_SAME_AS_SOURCE", 422)
        target_warehouse = await session.get(Warehouse, to_warehouse_id)
        if (
            target_warehouse is None
            or target_warehouse.cooperative_id != lot.cooperative_id
            or target_warehouse.status != "ACTIVE"
        ):
            raise inventory_error("CUSTODY_TARGET_WAREHOUSE_NOT_ACTIVE", 409)
        target_responsibility, target_role = await self._custody_assignment(
            session,
            to_assignment_id,
            lot.cooperative_id,
            to_warehouse_id,
            allowed_roles=CUSTODY_ROLES,
        )
        if target_role.user_id == principal.user_id:
            raise inventory_error("INDEPENDENT_CUSTODY_RECIPIENT_REQUIRED", 422)
        evidence = await EvidenceService.require_ready(
            session, lot.cooperative_id, evidence_ids, required=False
        )
        actor = actor_claim(
            principal,
            lot.cooperative_id,
            CUSTODY_ROLES,
            exact_assignment_id=from_role.id,
        )
        transfer_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="responsibility.custody_offered",
            aggregate_type="inventory_lot",
            aggregate_id=lot.id,
            aggregate_version=lot.version + 1,
            actor=actor,
            payload={
                **payload,
                "transfer_id": str(transfer_id),
                "from_assignment_id": str(from_responsibility.id),
                "from_warehouse_id": str(lot.warehouse_id),
                "evidence": self._evidence_payload(evidence),
            },
        )
        session.add(
            CustodyTransfer(
                id=transfer_id,
                lot_id=lot.id,
                from_warehouse_id=lot.warehouse_id,
                to_warehouse_id=to_warehouse_id,
                from_assignment_id=from_responsibility.id,
                to_assignment_id=target_responsibility.id,
                place=normalized_place,
                notes=normalized_notes,
                status=CustodyStatus.OFFERED.value,
                offered_by_user_id=principal.user_id,
                accepted_by_user_id=None,
                offered_event_id=event.event_id,
                accepted_event_id=None,
            )
        )
        lot.updated_at = datetime.now(UTC)
        lot.version += 1
        self._link_evidence(session, evidence, event.event_id, "custody_transfer", transfer_id)
        await self._audit(
            session,
            principal,
            lot.cooperative_id,
            "CUSTODY_TRANSFER_OFFERED",
            "CustodyTransfer",
            transfer_id,
            event.event_id,
            request_id,
        )
        return complete_command(record, event.event_id, transfer_id)

    async def accept_custody(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        transfer_id: UUID,
        evidence_ids: Sequence[UUID],
        expected_lot_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        payload = {
            "transfer_id": str(transfer_id),
            "evidence_ids": [str(item) for item in evidence_ids],
            "expected_lot_version": expected_lot_version,
        }
        record, replay = await begin_command(
            session, principal, "INVENTORY_ACCEPT_CUSTODY", idempotency_key, payload
        )
        if replay is not None:
            return replay
        transfer = await session.get(CustodyTransfer, transfer_id, with_for_update=True)
        if transfer is None:
            raise inventory_error("CUSTODY_TRANSFER_NOT_FOUND", 404)
        if transfer.status != CustodyStatus.OFFERED.value:
            raise inventory_error("CUSTODY_TRANSFER_NOT_OFFERED", 409)
        lot = await session.get(InventoryLot, transfer.lot_id, with_for_update=True)
        if lot is None:
            raise inventory_error("LOT_NOT_FOUND", 404)
        self._version(lot.version, expected_lot_version)
        if lot.custodian_assignment_id != transfer.from_assignment_id:
            raise inventory_error("CUSTODY_SOURCE_CHANGED", 409)
        _target_responsibility, target_role = await self._custody_assignment(
            session,
            transfer.to_assignment_id,
            lot.cooperative_id,
            transfer.to_warehouse_id,
            principal=principal,
            allowed_roles=CUSTODY_ROLES,
        )
        if transfer.offered_by_user_id == principal.user_id:
            raise inventory_error("INDEPENDENT_CUSTODY_RECIPIENT_REQUIRED", 403)
        evidence = await EvidenceService.require_ready(
            session, lot.cooperative_id, evidence_ids, required=True
        )
        actor = actor_claim(
            principal,
            lot.cooperative_id,
            CUSTODY_ROLES,
            exact_assignment_id=target_role.id,
        )
        event = await self.journal.append(
            session,
            event_type="responsibility.custody_accepted",
            aggregate_type="inventory_lot",
            aggregate_id=lot.id,
            aggregate_version=lot.version + 1,
            actor=actor,
            payload={
                **payload,
                "lot_id": str(lot.id),
                "from_assignment_id": str(transfer.from_assignment_id),
                "to_assignment_id": str(transfer.to_assignment_id),
                "from_warehouse_id": str(transfer.from_warehouse_id),
                "to_warehouse_id": str(transfer.to_warehouse_id),
                "evidence": self._evidence_payload(evidence),
            },
        )
        transfer.status = CustodyStatus.ACCEPTED.value
        transfer.accepted_by_user_id = principal.user_id
        transfer.accepted_event_id = event.event_id
        transfer.accepted_at = datetime.now(UTC)
        lot.custodian_assignment_id = transfer.to_assignment_id
        lot.warehouse_id = transfer.to_warehouse_id
        lot.updated_at = datetime.now(UTC)
        lot.version += 1
        self._link_evidence(session, evidence, event.event_id, "custody_transfer", transfer.id)
        await self._audit(
            session,
            principal,
            lot.cooperative_id,
            "CUSTODY_TRANSFER_ACCEPTED",
            "CustodyTransfer",
            transfer.id,
            event.event_id,
            request_id,
        )
        return complete_command(record, event.event_id, transfer.id)

    @staticmethod
    async def _custody_assignment(
        session: AsyncSession,
        assignment_id: UUID,
        cooperative_id: UUID,
        warehouse_id: UUID,
        *,
        principal: Principal | None = None,
        allowed_roles: set[RoleCode] = RECEIPT_ROLES,
    ) -> tuple[ResponsibilityAssignment, RoleAssignment]:
        assignment = await session.get(ResponsibilityAssignment, assignment_id)
        now = datetime.now(UTC)
        if (
            assignment is None
            or assignment.cooperative_id != cooperative_id
            or assignment.status != "ACTIVE"
            or assignment.accepted_at is None
            or (assignment.valid_until is not None and assignment.valid_until <= now)
            or assignment.subject_type not in {"warehouse", "warehouse_zone"}
            or assignment.subject_id != warehouse_id
        ):
            raise inventory_error("CUSTODY_RESPONSIBILITY_NOT_ACTIVE", 409)
        role = await session.get(RoleAssignment, assignment.role_assignment_id)
        if role is None or role.status != "ACTIVE" or RoleCode(role.role_code) not in allowed_roles:
            raise inventory_error("CUSTODY_ROLE_NOT_ACTIVE", 409)
        user = await session.get(UserAccount, role.user_id)
        if user is None or user.status != "ACTIVE" or user.member_id != assignment.member_id:
            raise inventory_error("CUSTODY_ACTOR_NOT_ACTIVE", 409)
        active_membership = await session.scalar(
            select(Membership.id).where(
                Membership.cooperative_id == cooperative_id,
                Membership.member_id == assignment.member_id,
                Membership.status == "ACTIVE",
            )
        )
        if active_membership is None:
            raise inventory_error("CUSTODY_MEMBERSHIP_NOT_ACTIVE", 409)
        if principal is not None and (
            principal.user_id != user.id or principal.member_id != assignment.member_id
        ):
            raise inventory_error("CURRENT_CUSTODIAN_REQUIRED", 403)
        return assignment, role

    @staticmethod
    def _measurements(values: dict[str, str]) -> dict[str, str]:
        if len(values) > 30:
            raise inventory_error("ATTESTATION_MEASUREMENTS_INVALID")
        result: dict[str, str] = {}
        for key, value in values.items():
            normalized_key = bounded_text(key, "ATTESTATION_MEASUREMENTS_INVALID", 80)
            normalized_value = bounded_text(value, "ATTESTATION_MEASUREMENTS_INVALID", 200)
            result[normalized_key] = normalized_value
        if not result:
            raise inventory_error("ATTESTATION_MEASUREMENTS_REQUIRED")
        return result

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
    def _version(current: int, expected: int) -> None:
        if current != expected:
            raise inventory_error("VERSION_CONFLICT", 409)

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
