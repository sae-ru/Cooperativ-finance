"""Emergency physical custody continuity after confirmed member incapacity."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.audit.infrastructure.repository import (
    AuditRepository,
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.identity.domain.types import (
    MemberContinuityCaseStatus,
    MemberContinuityCaseType,
    MemberStatus,
    Principal,
    RoleCode,
    RoleGrantSource,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    MemberContinuityCase,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.domain.types import (
    CustodyContinuityItemStatus,
    CustodyContinuityStatus,
    decimal_text,
    exact_quantity,
)
from cooperative_clearing.modules.inventory.infrastructure.models import (
    CustodyContinuityCase,
    CustodyContinuityItem,
    CustodyTransfer,
    EvidenceBlob,
    EvidenceLink,
    InventoryDiscrepancy,
    InventoryLot,
    Warehouse,
)
from cooperative_clearing.modules.journal.application.service import (
    ActorClaim,
    SignedJournalService,
)
from cooperative_clearing.modules.responsibility.infrastructure.models import (
    ResponsibilityAssignment,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError

REQUEST_ROLES = frozenset({RoleCode.COOPERATIVE_ADMIN, RoleCode.SECURITY_ADMIN})
INVENTORY_ROLES = frozenset({RoleCode.INVENTORY_CONTROLLER, RoleCode.AUDITOR})
REVIEW_ROLES = frozenset({RoleCode.SECURITY_ADMIN})
READ_ROLES = frozenset(
    {
        RoleCode.COOPERATIVE_ADMIN,
        RoleCode.SECURITY_ADMIN,
        RoleCode.INVENTORY_CONTROLLER,
        RoleCode.AUDITOR,
        RoleCode.WAREHOUSE_CUSTODIAN,
    }
)
REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,199}")
REASON_PATTERN = re.compile(r"[A-Z0-9_.-]{2,100}")
MIN_TEMPORARY_DURATION = timedelta(hours=1)
MAX_TEMPORARY_DURATION = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class CustodyContinuityCommandResult:
    event_id: UUID
    object_id: UUID
    status: str
    replayed: bool = False


class CustodyContinuityService:
    def __init__(self, settings: Settings) -> None:
        self.journal = SignedJournalService(settings)

    async def request_case(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        member_continuity_case_id: UUID,
        source_assignment_id: UUID,
        expected_source_assignment_version: int,
        target_role_assignment_id: UUID,
        handover_place: str,
        temporary_valid_until: datetime,
        evidence_refs: Sequence[str],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CustodyContinuityCommandResult:
        place = _bounded_text(handover_place, "CUSTODY_CONTINUITY_PLACE_INVALID", 500)
        evidence = _evidence_refs(evidence_refs)
        valid_until = _temporary_expiry(temporary_valid_until)
        payload = {
            "member_continuity_case_id": str(member_continuity_case_id),
            "source_assignment_id": str(source_assignment_id),
            "expected_source_assignment_version": expected_source_assignment_version,
            "target_role_assignment_id": str(target_role_assignment_id),
            "handover_place": place,
            "temporary_valid_until": valid_until.isoformat(),
            "evidence_refs": list(evidence),
        }
        record, replay = await self._begin(
            session, principal, "CUSTODY_CONTINUITY_REQUEST", idempotency_key, payload
        )
        if replay is not None:
            return replay

        continuity = await session.get(
            MemberContinuityCase, member_continuity_case_id, with_for_update=True
        )
        if continuity is None:
            raise _error("MEMBER_CONTINUITY_CASE_NOT_FOUND", 404)
        if (
            continuity.case_type != MemberContinuityCaseType.DEATH_OR_INCAPACITY.value
            or continuity.status != MemberContinuityCaseStatus.CONFIRMED.value
        ):
            raise _error("MEMBER_CONTINUITY_NOT_CONFIRMED", 409)
        self._require_role(principal, REQUEST_ROLES, continuity.cooperative_id)

        source = await session.get(
            ResponsibilityAssignment, source_assignment_id, with_for_update=True
        )
        if source is None:
            raise _error("CUSTODY_SOURCE_ASSIGNMENT_NOT_FOUND", 404)
        if source.version != expected_source_assignment_version:
            raise _version_conflict(source.version)
        if (
            source.cooperative_id != continuity.cooperative_id
            or source.member_id != continuity.member_id
            or source.status != "ACTIVE"
            or source.accepted_at is None
            or source.subject_type != "warehouse"
        ):
            raise _error("CUSTODY_SOURCE_ASSIGNMENT_INELIGIBLE", 409)
        source_role = await session.get(RoleAssignment, source.role_assignment_id)
        if (
            source_role is None
            or source_role.role_code != RoleCode.WAREHOUSE_CUSTODIAN.value
            or source_role.source != RoleGrantSource.ASSIGNMENT.value
        ):
            raise _error("CUSTODY_SOURCE_ROLE_INELIGIBLE", 409)
        source_member = await session.get(Member, source.member_id)
        if (
            source_member is None
            or source_member.status != MemberStatus.SUCCESSION_REVIEW.value
        ):
            raise _error("CUSTODY_SOURCE_MEMBER_NOT_CONTAINED", 409)
        warehouse = await session.get(Warehouse, source.subject_id)
        if (
            warehouse is None
            or warehouse.cooperative_id != continuity.cooperative_id
            or warehouse.status not in {"ACTIVE", "SUSPENDED"}
        ):
            raise _error("CUSTODY_WAREHOUSE_NOT_AVAILABLE", 409)

        target_role, target_user, target_member = await self._eligible_target(
            session,
            cooperative_id=continuity.cooperative_id,
            role_assignment_id=target_role_assignment_id,
            temporary_valid_until=valid_until,
        )
        if target_member.id == source.member_id:
            raise _error("CUSTODY_CONTINUITY_TARGET_SAME_AS_SOURCE", 422)
        if target_user.id == principal.user_id:
            raise _error("CUSTODY_CONTINUITY_SELF_APPOINTMENT", 403)
        existing_target = await session.scalar(
            select(ResponsibilityAssignment.id).where(
                ResponsibilityAssignment.cooperative_id == continuity.cooperative_id,
                ResponsibilityAssignment.member_id == target_member.id,
                ResponsibilityAssignment.subject_type == "warehouse",
                ResponsibilityAssignment.subject_id == warehouse.id,
                ResponsibilityAssignment.status.in_(
                    ["PENDING_APPROVAL", "PENDING_ACCEPTANCE", "ACTIVE"]
                ),
            )
        )
        if existing_target is not None:
            raise _error("CUSTODY_CONTINUITY_TARGET_ALREADY_RESPONSIBLE", 409)

        lots = list(
            (
                await session.execute(
                    select(InventoryLot)
                    .where(InventoryLot.custodian_assignment_id == source.id)
                    .order_by(InventoryLot.id)
                    .with_for_update()
                )
            ).scalars()
        )
        if not lots:
            raise _error("CUSTODY_CONTINUITY_NO_LOTS", 409)
        if any(item.continuity_hold_case_id is not None for item in lots):
            raise _error("CUSTODY_CONTINUITY_LOT_ALREADY_HELD", 409)
        open_transfer = await session.scalar(
            select(CustodyTransfer.id).where(
                CustodyTransfer.lot_id.in_([item.id for item in lots]),
                CustodyTransfer.status == "OFFERED",
            )
        )
        if open_transfer is not None:
            raise _error("CUSTODY_CONTINUITY_OPEN_TRANSFER", 409)

        now = datetime.now(UTC)
        case_id = uuid4()
        actor = self._actor(principal, continuity.cooperative_id, REQUEST_ROLES)
        request_event = await self.journal.append(
            session,
            event_type="responsibility.custody_continuity_started",
            aggregate_type="custody_continuity_case",
            aggregate_id=case_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "cooperative_id": str(continuity.cooperative_id),
                "source_member_id": str(source.member_id),
                "target_member_id": str(target_member.id),
                "warehouse_id": str(warehouse.id),
                "lot_count": len(lots),
            },
        )
        continuity_case = CustodyContinuityCase(
            id=case_id,
            cooperative_id=continuity.cooperative_id,
            member_continuity_case_id=continuity.id,
            source_member_id=source.member_id,
            warehouse_id=warehouse.id,
            source_assignment_id=source.id,
            source_assignment_version=source.version,
            target_member_id=target_member.id,
            target_role_assignment_id=target_role.id,
            target_assignment_id=None,
            handover_place=place,
            temporary_valid_until=valid_until,
            evidence_refs=list(evidence),
            blocked_reasons=[],
            status=CustodyContinuityStatus.INVENTORY_PENDING.value,
            requested_by_user_id=principal.user_id,
            decided_by_user_id=None,
            accepted_by_user_id=None,
            decision_reason_code=None,
            requested_event_id=request_event.event_id,
            decided_event_id=None,
            accepted_event_id=None,
            created_at=now,
            inventory_completed_at=None,
            decided_at=None,
            accepted_at=None,
            updated_at=now,
            version=1,
        )
        session.add(continuity_case)
        for lot in lots:
            await self.journal.append(
                session,
                event_type="responsibility.custody_hold_applied",
                aggregate_type="inventory_lot",
                aggregate_id=lot.id,
                aggregate_version=lot.version + 1,
                actor=actor,
                payload={
                    "custody_continuity_case_id": str(case_id),
                    "source_assignment_id": str(source.id),
                    "warehouse_id": str(warehouse.id),
                    "previous_lot_version": lot.version,
                },
            )
            lot.continuity_hold_case_id = case_id
            lot.updated_at = now
            lot.version += 1
            session.add(
                CustodyContinuityItem(
                    id=uuid4(),
                    case_id=case_id,
                    lot_id=lot.id,
                    lot_version=lot.version,
                    expected_quantity=_lot_quantity(lot),
                    actual_quantity=None,
                    status=CustodyContinuityItemStatus.PENDING.value,
                    condition_notes=None,
                    evidence_ids=[],
                    attested_by_user_id=None,
                    event_id=None,
                    attested_at=None,
                    version=1,
                )
            )
        await self._audit(
            session,
            principal,
            continuity.cooperative_id,
            "CUSTODY_CONTINUITY_STARTED",
            case_id,
            request_event.event_id,
            request_id,
            {"source_assignment_id": str(source.id), "lot_count": len(lots)},
        )
        return self._complete(
            record,
            request_event.event_id,
            case_id,
            CustodyContinuityStatus.INVENTORY_PENDING.value,
        )

    async def attest_item(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        continuity_case_id: UUID,
        item_id: UUID,
        actual_quantity: Decimal,
        condition_notes: str,
        evidence_ids: Sequence[UUID],
        expected_case_version: int,
        expected_item_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CustodyContinuityCommandResult:
        actual = exact_quantity(actual_quantity, allow_zero=True)
        notes = _bounded_text(
            condition_notes, "CUSTODY_CONTINUITY_CONDITION_INVALID", 1000
        )
        payload = {
            "continuity_case_id": str(continuity_case_id),
            "item_id": str(item_id),
            "actual_quantity": decimal_text(actual),
            "condition_notes": notes,
            "evidence_ids": [str(item) for item in evidence_ids],
            "expected_case_version": expected_case_version,
            "expected_item_version": expected_item_version,
        }
        record, replay = await self._begin(
            session, principal, "CUSTODY_CONTINUITY_ATTEST", idempotency_key, payload
        )
        if replay is not None:
            return replay
        continuity_case = await session.get(
            CustodyContinuityCase, continuity_case_id, with_for_update=True
        )
        if continuity_case is None:
            raise _error("CUSTODY_CONTINUITY_CASE_NOT_FOUND", 404)
        self._require_role(principal, INVENTORY_ROLES, continuity_case.cooperative_id)
        if continuity_case.version != expected_case_version:
            raise _version_conflict(continuity_case.version)
        if continuity_case.status != CustodyContinuityStatus.INVENTORY_PENDING.value:
            raise _error("CUSTODY_CONTINUITY_NOT_INVENTORY_PENDING", 409)

        items = await self._locked_items(session, continuity_case.id)
        item = next((value for value in items if value.id == item_id), None)
        if item is None:
            raise _error("CUSTODY_CONTINUITY_ITEM_NOT_FOUND", 404)
        if item.version != expected_item_version:
            raise _version_conflict(item.version)
        if item.status != CustodyContinuityItemStatus.PENDING.value:
            raise _error("CUSTODY_CONTINUITY_ITEM_ALREADY_ATTESTED", 409)
        target_role = await session.get(
            RoleAssignment, continuity_case.target_role_assignment_id
        )
        if (
            principal.user_id == continuity_case.requested_by_user_id
            or target_role is None
            or principal.user_id == target_role.user_id
        ):
            raise _error("INDEPENDENT_CUSTODY_INVENTORY_REQUIRED", 403)
        lot = await session.get(InventoryLot, item.lot_id, with_for_update=True)
        blockers = self._lot_blockers(continuity_case, item, lot)
        if blockers:
            return await self._block(
                session,
                record,
                continuity_case,
                principal,
                sorted(blockers),
                request_id,
            )
        evidence = await EvidenceService.require_ready(
            session,
            continuity_case.cooperative_id,
            evidence_ids,
            required=True,
        )
        actor = self._actor(principal, continuity_case.cooperative_id, INVENTORY_ROLES)
        matched = actual == item.expected_quantity
        status = (
            CustodyContinuityItemStatus.MATCH
            if matched
            else CustodyContinuityItemStatus.DISCREPANCY
        )
        event = await self.journal.append(
            session,
            event_type=(
                "inventory.emergency_count_attested"
                if matched
                else "inventory.emergency_count_discrepancy"
            ),
            aggregate_type="custody_continuity_case",
            aggregate_id=continuity_case.id,
            aggregate_version=continuity_case.version + 1,
            actor=actor,
            payload={
                **payload,
                "lot_id": str(item.lot_id),
                "expected_quantity": decimal_text(item.expected_quantity),
                "result": status.value,
                "evidence": self._evidence_payload(evidence),
            },
        )
        now = datetime.now(UTC)
        item.actual_quantity = actual
        item.status = status.value
        item.condition_notes = notes
        item.evidence_ids = [str(value.id) for value in evidence]
        item.attested_by_user_id = principal.user_id
        item.event_id = event.event_id
        item.attested_at = now
        item.version += 1
        continuity_case.version += 1
        continuity_case.updated_at = now
        if not matched:
            continuity_case.status = CustodyContinuityStatus.BLOCKED.value
            continuity_case.blocked_reasons = ["QUANTITY_DISCREPANCY"]
            session.add(
                InventoryDiscrepancy(
                    id=uuid4(),
                    lot_id=item.lot_id,
                    expected_quantity=item.expected_quantity,
                    actual_quantity=actual,
                    variance=actual - item.expected_quantity,
                    reason_code="CUSTODY_CONTINUITY_COUNT",
                    notes=notes,
                    status="OPEN",
                    recorded_by_user_id=principal.user_id,
                    event_id=event.event_id,
                    created_at=now,
                )
            )
        elif all(
            value.id == item.id
            or value.status == CustodyContinuityItemStatus.MATCH.value
            for value in items
        ):
            continuity_case.status = CustodyContinuityStatus.PENDING_APPROVAL.value
            continuity_case.inventory_completed_at = now
        self._link_evidence(
            session,
            evidence,
            event.event_id,
            "custody_continuity_item",
            item.id,
        )
        await self._audit(
            session,
            principal,
            continuity_case.cooperative_id,
            "CUSTODY_CONTINUITY_ITEM_ATTESTED",
            continuity_case.id,
            event.event_id,
            request_id,
            {"item_id": str(item.id), "status": status.value},
        )
        return self._complete(
            record,
            event.event_id,
            continuity_case.id,
            continuity_case.status,
        )

    async def decide_case(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        continuity_case_id: UUID,
        approve: bool,
        expected_version: int,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CustodyContinuityCommandResult:
        reason = _reason(reason_code)
        payload = {
            "continuity_case_id": str(continuity_case_id),
            "approve": approve,
            "expected_version": expected_version,
            "reason_code": reason,
        }
        record, replay = await self._begin(
            session, principal, "CUSTODY_CONTINUITY_DECIDE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        continuity_case = await session.get(
            CustodyContinuityCase, continuity_case_id, with_for_update=True
        )
        if continuity_case is None:
            raise _error("CUSTODY_CONTINUITY_CASE_NOT_FOUND", 404)
        self._require_role(principal, REVIEW_ROLES, continuity_case.cooperative_id)
        if continuity_case.version != expected_version:
            raise _version_conflict(continuity_case.version)
        allowed_reject = {
            CustodyContinuityStatus.INVENTORY_PENDING.value,
            CustodyContinuityStatus.PENDING_APPROVAL.value,
            CustodyContinuityStatus.BLOCKED.value,
        }
        if approve and (
            continuity_case.status
            != CustodyContinuityStatus.PENDING_APPROVAL.value
        ):
            raise _error("CUSTODY_CONTINUITY_NOT_READY_FOR_APPROVAL", 409)
        if not approve and continuity_case.status not in allowed_reject:
            raise _error("CUSTODY_CONTINUITY_DECISION_NOT_ALLOWED", 409)
        items = await self._locked_items(session, continuity_case.id)
        target_role = await session.get(
            RoleAssignment, continuity_case.target_role_assignment_id
        )
        attesters = {
            item.attested_by_user_id
            for item in items
            if item.attested_by_user_id is not None
        }
        if (
            principal.user_id == continuity_case.requested_by_user_id
            or target_role is None
            or principal.user_id == target_role.user_id
            or principal.user_id in attesters
        ):
            raise _error("INDEPENDENT_CUSTODY_APPROVER_REQUIRED", 403)

        actor = self._actor(principal, continuity_case.cooperative_id, REVIEW_ROLES)
        now = datetime.now(UTC)
        if approve:
            blockers = await self._state_blockers(
                session, continuity_case, items, require_target_assignment=False
            )
            if blockers:
                return await self._block(
                    session,
                    record,
                    continuity_case,
                    principal,
                    blockers,
                    request_id,
                )
            source = await session.get(
                ResponsibilityAssignment,
                continuity_case.source_assignment_id,
                with_for_update=True,
            )
            if source is None:
                raise _error("CUSTODY_SOURCE_ASSIGNMENT_NOT_FOUND", 409)
            target_assignment_id = uuid4()
            event = await self.journal.append(
                session,
                event_type="responsibility.temporary_custodian_approved",
                aggregate_type="custody_continuity_case",
                aggregate_id=continuity_case.id,
                aggregate_version=continuity_case.version + 1,
                actor=actor,
                payload={
                    **payload,
                    "target_assignment_id": str(target_assignment_id),
                    "target_member_id": str(continuity_case.target_member_id),
                    "target_role_assignment_id": str(
                        continuity_case.target_role_assignment_id
                    ),
                    "warehouse_id": str(continuity_case.warehouse_id),
                    "temporary_valid_until": (
                        continuity_case.temporary_valid_until.isoformat()
                    ),
                },
            )
            session.add(
                ResponsibilityAssignment(
                    id=target_assignment_id,
                    cooperative_id=continuity_case.cooperative_id,
                    member_id=continuity_case.target_member_id,
                    role_assignment_id=continuity_case.target_role_assignment_id,
                    subject_type="warehouse",
                    subject_id=continuity_case.warehouse_id,
                    scope=f"Temporary custody continuity {continuity_case.id}",
                    max_exposure=source.max_exposure,
                    exposure_unit=source.exposure_unit,
                    valid_from=now,
                    valid_until=continuity_case.temporary_valid_until,
                    status="PENDING_ACCEPTANCE",
                    created_by_user_id=continuity_case.requested_by_user_id,
                    approved_by_user_id=principal.user_id,
                    accepted_by_user_id=None,
                    created_event_id=continuity_case.requested_event_id,
                    approved_event_id=event.event_id,
                    accepted_event_id=None,
                    created_at=continuity_case.created_at,
                    approved_at=now,
                    accepted_at=None,
                    released_at=None,
                    version=2,
                )
            )
            await session.flush()
            continuity_case.target_assignment_id = target_assignment_id
            continuity_case.status = CustodyContinuityStatus.PENDING_ACCEPTANCE.value
        else:
            event = await self.journal.append(
                session,
                event_type="responsibility.custody_continuity_rejected",
                aggregate_type="custody_continuity_case",
                aggregate_id=continuity_case.id,
                aggregate_version=continuity_case.version + 1,
                actor=actor,
                payload=payload,
            )
            await self._release_holds(session, continuity_case, items, actor, reason)
            continuity_case.status = CustodyContinuityStatus.REJECTED.value
            if continuity_case.target_assignment_id is not None:
                target_assignment = await session.get(
                    ResponsibilityAssignment,
                    continuity_case.target_assignment_id,
                    with_for_update=True,
                )
                if (
                    target_assignment is not None
                    and target_assignment.status == "PENDING_ACCEPTANCE"
                ):
                    target_assignment.status = "REJECTED"
                    target_assignment.version += 1
        continuity_case.decided_by_user_id = principal.user_id
        continuity_case.decision_reason_code = reason
        continuity_case.decided_event_id = event.event_id
        continuity_case.decided_at = now
        continuity_case.updated_at = now
        continuity_case.version += 1
        await self._audit(
            session,
            principal,
            continuity_case.cooperative_id,
            "CUSTODY_CONTINUITY_APPROVED"
            if approve
            else "CUSTODY_CONTINUITY_REJECTED",
            continuity_case.id,
            event.event_id,
            request_id,
            {"status": continuity_case.status},
        )
        return self._complete(
            record,
            event.event_id,
            continuity_case.id,
            continuity_case.status,
        )

    async def candidate_decision(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        continuity_case_id: UUID,
        accept: bool,
        expected_version: int,
        evidence_ids: Sequence[UUID],
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CustodyContinuityCommandResult:
        reason = _reason(reason_code)
        payload = {
            "continuity_case_id": str(continuity_case_id),
            "accept": accept,
            "expected_version": expected_version,
            "evidence_ids": [str(item) for item in evidence_ids],
            "reason_code": reason,
        }
        record, replay = await self._begin(
            session,
            principal,
            "CUSTODY_CONTINUITY_CANDIDATE_DECISION",
            idempotency_key,
            payload,
        )
        if replay is not None:
            return replay
        continuity_case = await session.get(
            CustodyContinuityCase, continuity_case_id, with_for_update=True
        )
        if continuity_case is None:
            raise _error("CUSTODY_CONTINUITY_CASE_NOT_FOUND", 404)
        if continuity_case.version != expected_version:
            raise _version_conflict(continuity_case.version)
        if (
            continuity_case.status
            != CustodyContinuityStatus.PENDING_ACCEPTANCE.value
            or continuity_case.target_assignment_id is None
        ):
            raise _error("CUSTODY_CONTINUITY_NOT_PENDING_ACCEPTANCE", 409)
        target_role, target_user, target_member = await self._eligible_target(
            session,
            cooperative_id=continuity_case.cooperative_id,
            role_assignment_id=continuity_case.target_role_assignment_id,
            temporary_valid_until=continuity_case.temporary_valid_until,
        )
        if (
            target_user.id != principal.user_id
            or target_member.id != principal.member_id
            or not self._has_exact_permanent_role(principal, target_role.id)
        ):
            raise _error("CUSTODY_CONTINUITY_TARGET_REQUIRED", 403)
        target_assignment = await session.get(
            ResponsibilityAssignment,
            continuity_case.target_assignment_id,
            with_for_update=True,
        )
        if target_assignment is None or target_assignment.status != "PENDING_ACCEPTANCE":
            raise _error("CUSTODY_CONTINUITY_TARGET_ASSIGNMENT_CHANGED", 409)
        items = await self._locked_items(session, continuity_case.id)
        actor = self._actor(
            principal,
            continuity_case.cooperative_id,
            frozenset({RoleCode.WAREHOUSE_CUSTODIAN}),
            exact_assignment_id=target_role.id,
        )
        now = datetime.now(UTC)
        evidence: list[EvidenceBlob] = []
        if accept:
            evidence = list(
                await EvidenceService.require_ready(
                    session,
                    continuity_case.cooperative_id,
                    evidence_ids,
                    required=True,
                )
            )
            blockers = await self._state_blockers(
                session, continuity_case, items, require_target_assignment=True
            )
            if blockers:
                target_assignment.status = "REJECTED"
                target_assignment.version += 1
                return await self._block(
                    session,
                    record,
                    continuity_case,
                    principal,
                    blockers,
                    request_id,
                )
            event = await self.journal.append(
                session,
                event_type="responsibility.emergency_custody_accepted",
                aggregate_type="custody_continuity_case",
                aggregate_id=continuity_case.id,
                aggregate_version=continuity_case.version + 1,
                actor=actor,
                payload={
                    **payload,
                    "source_assignment_id": str(
                        continuity_case.source_assignment_id
                    ),
                    "target_assignment_id": str(target_assignment.id),
                    "warehouse_id": str(continuity_case.warehouse_id),
                    "lot_count": len(items),
                    "evidence": self._evidence_payload(evidence),
                },
            )
            source = await session.get(
                ResponsibilityAssignment,
                continuity_case.source_assignment_id,
                with_for_update=True,
            )
            if source is None:
                raise _error("CUSTODY_SOURCE_ASSIGNMENT_NOT_FOUND", 409)
            lots = await self._locked_lots(session, items)
            for item in items:
                lot = lots[item.lot_id]
                lot_event = await self.journal.append(
                    session,
                    event_type="responsibility.emergency_custody_transferred",
                    aggregate_type="inventory_lot",
                    aggregate_id=lot.id,
                    aggregate_version=lot.version + 1,
                    actor=actor,
                    payload={
                        "custody_continuity_case_id": str(continuity_case.id),
                        "from_assignment_id": str(source.id),
                        "to_assignment_id": str(target_assignment.id),
                        "warehouse_id": str(continuity_case.warehouse_id),
                        "quantity": decimal_text(_lot_quantity(lot)),
                    },
                )
                session.add(
                    CustodyTransfer(
                        id=uuid4(),
                        lot_id=lot.id,
                        from_warehouse_id=continuity_case.warehouse_id,
                        to_warehouse_id=continuity_case.warehouse_id,
                        from_assignment_id=source.id,
                        to_assignment_id=target_assignment.id,
                        place=continuity_case.handover_place,
                        notes=f"Emergency continuity case {continuity_case.id}",
                        status="ACCEPTED",
                        offered_by_user_id=continuity_case.requested_by_user_id,
                        accepted_by_user_id=principal.user_id,
                        offered_event_id=continuity_case.requested_event_id,
                        accepted_event_id=lot_event.event_id,
                        offered_at=continuity_case.created_at,
                        accepted_at=now,
                    )
                )
                lot.custodian_assignment_id = target_assignment.id
                lot.continuity_hold_case_id = None
                lot.updated_at = now
                lot.version += 1
            source.status = "RELEASED"
            source.released_at = now
            source.version += 1
            target_assignment.status = "ACTIVE"
            target_assignment.accepted_by_user_id = principal.user_id
            target_assignment.accepted_event_id = event.event_id
            target_assignment.accepted_at = now
            target_assignment.version += 1
            continuity_case.status = CustodyContinuityStatus.ACCEPTED.value
            continuity_case.accepted_by_user_id = principal.user_id
            continuity_case.accepted_event_id = event.event_id
            continuity_case.accepted_at = now
            self._link_evidence(
                session,
                evidence,
                event.event_id,
                "custody_continuity_case",
                continuity_case.id,
            )
        else:
            event = await self.journal.append(
                session,
                event_type="responsibility.temporary_custodian_declined",
                aggregate_type="custody_continuity_case",
                aggregate_id=continuity_case.id,
                aggregate_version=continuity_case.version + 1,
                actor=actor,
                payload=payload,
            )
            target_assignment.status = "REJECTED"
            target_assignment.version += 1
            await self._release_holds(session, continuity_case, items, actor, reason)
            continuity_case.status = CustodyContinuityStatus.REJECTED.value
            continuity_case.decision_reason_code = reason
        continuity_case.updated_at = now
        continuity_case.version += 1
        await self._audit(
            session,
            principal,
            continuity_case.cooperative_id,
            "CUSTODY_CONTINUITY_ACCEPTED"
            if accept
            else "CUSTODY_CONTINUITY_DECLINED",
            continuity_case.id,
            event.event_id,
            request_id,
            {"status": continuity_case.status},
        )
        return self._complete(
            record,
            event.event_id,
            continuity_case.id,
            continuity_case.status,
        )

    async def _state_blockers(
        self,
        session: AsyncSession,
        continuity_case: CustodyContinuityCase,
        items: list[CustodyContinuityItem],
        *,
        require_target_assignment: bool,
    ) -> list[str]:
        blockers: set[str] = set()
        if continuity_case.temporary_valid_until <= datetime.now(UTC):
            blockers.add("TEMPORARY_PERIOD_EXPIRED")
        source = await session.get(
            ResponsibilityAssignment,
            continuity_case.source_assignment_id,
            with_for_update=True,
        )
        if source is None:
            blockers.add("SOURCE_ASSIGNMENT_MISSING")
        else:
            if source.version != continuity_case.source_assignment_version:
                blockers.add("SOURCE_ASSIGNMENT_VERSION_CHANGED")
            if source.status != "ACTIVE":
                blockers.add("SOURCE_ASSIGNMENT_STATUS_CHANGED")
            if (
                source.member_id != continuity_case.source_member_id
                or source.subject_type != "warehouse"
                or source.subject_id != continuity_case.warehouse_id
            ):
                blockers.add("SOURCE_ASSIGNMENT_SCOPE_CHANGED")
        member_case = await session.get(
            MemberContinuityCase, continuity_case.member_continuity_case_id
        )
        source_member = await session.get(Member, continuity_case.source_member_id)
        if (
            member_case is None
            or member_case.status != MemberContinuityCaseStatus.CONFIRMED.value
            or member_case.case_type
            != MemberContinuityCaseType.DEATH_OR_INCAPACITY.value
        ):
            blockers.add("MEMBER_CONTINUITY_CHANGED")
        if (
            source_member is None
            or source_member.status != MemberStatus.SUCCESSION_REVIEW.value
        ):
            blockers.add("SOURCE_MEMBER_STATUS_CHANGED")
        try:
            await self._eligible_target(
                session,
                cooperative_id=continuity_case.cooperative_id,
                role_assignment_id=continuity_case.target_role_assignment_id,
                temporary_valid_until=continuity_case.temporary_valid_until,
            )
        except DomainError:
            blockers.add("TARGET_CUSTODIAN_INELIGIBLE")
        if require_target_assignment:
            target_assignment = (
                await session.get(
                    ResponsibilityAssignment,
                    continuity_case.target_assignment_id,
                    with_for_update=True,
                )
                if continuity_case.target_assignment_id is not None
                else None
            )
            if target_assignment is None:
                blockers.add("TARGET_ASSIGNMENT_MISSING")
            elif (
                target_assignment.status != "PENDING_ACCEPTANCE"
                or target_assignment.member_id != continuity_case.target_member_id
                or target_assignment.subject_id != continuity_case.warehouse_id
                or target_assignment.role_assignment_id
                != continuity_case.target_role_assignment_id
            ):
                blockers.add("TARGET_ASSIGNMENT_CHANGED")
        if any(
            item.status != CustodyContinuityItemStatus.MATCH.value for item in items
        ):
            blockers.add("INVENTORY_INCOMPLETE")
        lots = await self._locked_lots(session, items)
        if len(lots) != len(items):
            blockers.add("LOT_MISSING")
        for item in items:
            blockers.update(
                self._lot_blockers(continuity_case, item, lots.get(item.lot_id))
            )
        return sorted(blockers)

    @staticmethod
    def _lot_blockers(
        continuity_case: CustodyContinuityCase,
        item: CustodyContinuityItem,
        lot: InventoryLot | None,
    ) -> set[str]:
        blockers: set[str] = set()
        if lot is None:
            return {"LOT_MISSING"}
        if lot.continuity_hold_case_id != continuity_case.id:
            blockers.add("LOT_HOLD_CHANGED")
        if lot.version != item.lot_version:
            blockers.add("LOT_VERSION_CHANGED")
        if lot.custodian_assignment_id != continuity_case.source_assignment_id:
            blockers.add("LOT_CUSTODIAN_CHANGED")
        if lot.warehouse_id != continuity_case.warehouse_id:
            blockers.add("LOT_WAREHOUSE_CHANGED")
        if _lot_quantity(lot) != item.expected_quantity:
            blockers.add("LOT_QUANTITY_CHANGED")
        return blockers

    async def _block(
        self,
        session: AsyncSession,
        record: IdempotencyRecord,
        continuity_case: CustodyContinuityCase,
        principal: Principal,
        blockers: Sequence[str],
        request_id: UUID | None,
    ) -> CustodyContinuityCommandResult:
        actor = self._actor_for_any_permanent_role(
            principal, continuity_case.cooperative_id
        )
        event = await self.journal.append(
            session,
            event_type="responsibility.custody_continuity_blocked",
            aggregate_type="custody_continuity_case",
            aggregate_id=continuity_case.id,
            aggregate_version=continuity_case.version + 1,
            actor=actor,
            payload={
                "custody_continuity_case_id": str(continuity_case.id),
                "blocked_reasons": sorted(set(blockers)),
            },
        )
        continuity_case.status = CustodyContinuityStatus.BLOCKED.value
        continuity_case.blocked_reasons = sorted(set(blockers))
        continuity_case.updated_at = datetime.now(UTC)
        continuity_case.version += 1
        await self._audit(
            session,
            principal,
            continuity_case.cooperative_id,
            "CUSTODY_CONTINUITY_BLOCKED",
            continuity_case.id,
            event.event_id,
            request_id,
            {"blocked_reasons": continuity_case.blocked_reasons},
        )
        return self._complete(
            record,
            event.event_id,
            continuity_case.id,
            CustodyContinuityStatus.BLOCKED.value,
        )

    async def _release_holds(
        self,
        session: AsyncSession,
        continuity_case: CustodyContinuityCase,
        items: list[CustodyContinuityItem],
        actor: ActorClaim,
        reason: str,
    ) -> None:
        lots = await self._locked_lots(session, items)
        now = datetime.now(UTC)
        for lot in lots.values():
            if lot.continuity_hold_case_id != continuity_case.id:
                continue
            await self.journal.append(
                session,
                event_type="responsibility.custody_hold_released",
                aggregate_type="inventory_lot",
                aggregate_id=lot.id,
                aggregate_version=lot.version + 1,
                actor=actor,
                payload={
                    "custody_continuity_case_id": str(continuity_case.id),
                    "reason_code": reason,
                },
            )
            lot.continuity_hold_case_id = None
            lot.updated_at = now
            lot.version += 1

    @staticmethod
    async def _eligible_target(
        session: AsyncSession,
        *,
        cooperative_id: UUID,
        role_assignment_id: UUID,
        temporary_valid_until: datetime,
    ) -> tuple[RoleAssignment, UserAccount, Member]:
        role = await session.get(RoleAssignment, role_assignment_id)
        if (
            role is None
            or role.status != "ACTIVE"
            or role.source != RoleGrantSource.ASSIGNMENT.value
            or role.role_code != RoleCode.WAREHOUSE_CUSTODIAN.value
            or role.cooperative_id != cooperative_id
            or (
                role.expires_at is not None
                and role.expires_at < temporary_valid_until
            )
        ):
            raise _error("CUSTODY_CONTINUITY_TARGET_ROLE_INELIGIBLE", 409)
        user = await session.get(UserAccount, role.user_id)
        member = await session.get(Member, user.member_id) if user is not None else None
        if (
            user is None
            or user.status != "ACTIVE"
            or member is None
            or member.status not in {MemberStatus.ACTIVE.value, MemberStatus.LIMITED.value}
        ):
            raise _error("CUSTODY_CONTINUITY_TARGET_INELIGIBLE", 409)
        membership = await session.scalar(
            select(Membership.id).where(
                Membership.cooperative_id == cooperative_id,
                Membership.member_id == member.id,
                Membership.status == "ACTIVE",
            )
        )
        if membership is None:
            raise _error("CUSTODY_CONTINUITY_TARGET_MEMBERSHIP_INACTIVE", 409)
        return role, user, member

    @staticmethod
    async def _locked_items(
        session: AsyncSession, continuity_case_id: UUID
    ) -> list[CustodyContinuityItem]:
        return list(
            (
                await session.execute(
                    select(CustodyContinuityItem)
                    .where(CustodyContinuityItem.case_id == continuity_case_id)
                    .order_by(CustodyContinuityItem.lot_id)
                    .with_for_update()
                )
            ).scalars()
        )

    @staticmethod
    async def _locked_lots(
        session: AsyncSession, items: Sequence[CustodyContinuityItem]
    ) -> dict[UUID, InventoryLot]:
        if not items:
            return {}
        rows = list(
            (
                await session.execute(
                    select(InventoryLot)
                    .where(InventoryLot.id.in_([item.lot_id for item in items]))
                    .order_by(InventoryLot.id)
                    .with_for_update()
                )
            ).scalars()
        )
        return {item.id: item for item in rows}

    @staticmethod
    def _require_role(
        principal: Principal, roles: frozenset[RoleCode], cooperative_id: UUID
    ) -> None:
        if principal.must_change_password:
            raise _error("PASSWORD_CHANGE_REQUIRED", 403)
        if principal.member_id is None:
            raise _error("PERSONAL_ACTOR_REQUIRED", 403)
        if not principal.has_permanent_role(set(roles), cooperative_id):
            raise _error("PERMANENT_CUSTODY_CONTINUITY_ROLE_REQUIRED", 403)

    @staticmethod
    def _actor(
        principal: Principal,
        cooperative_id: UUID,
        roles: frozenset[RoleCode],
        *,
        exact_assignment_id: UUID | None = None,
    ) -> ActorClaim:
        if principal.member_id is None:
            raise _error("PERSONAL_ACTOR_REQUIRED", 403)
        for grant in principal.roles:
            if (
                grant.source is RoleGrantSource.ASSIGNMENT
                and grant.role in roles
                and grant.cooperative_id in {None, cooperative_id}
                and (
                    exact_assignment_id is None
                    or grant.assignment_id == exact_assignment_id
                )
            ):
                return ActorClaim(
                    person_id=principal.member_id,
                    organization_id=cooperative_id,
                    role_assignment_id=grant.assignment_id,
                )
        raise _error("PERMANENT_CUSTODY_CONTINUITY_ROLE_REQUIRED", 403)

    @staticmethod
    def _actor_for_any_permanent_role(
        principal: Principal, cooperative_id: UUID
    ) -> ActorClaim:
        if principal.member_id is None:
            raise _error("PERSONAL_ACTOR_REQUIRED", 403)
        for grant in principal.roles:
            if (
                grant.source is RoleGrantSource.ASSIGNMENT
                and grant.cooperative_id in {None, cooperative_id}
            ):
                return ActorClaim(
                    person_id=principal.member_id,
                    organization_id=cooperative_id,
                    role_assignment_id=grant.assignment_id,
                )
        raise _error("PERMANENT_CUSTODY_CONTINUITY_ROLE_REQUIRED", 403)

    @staticmethod
    def _has_exact_permanent_role(
        principal: Principal, assignment_id: UUID
    ) -> bool:
        return any(
            grant.source is RoleGrantSource.ASSIGNMENT
            and grant.role is RoleCode.WAREHOUSE_CUSTODIAN
            and grant.assignment_id == assignment_id
            for grant in principal.roles
        )

    @staticmethod
    async def _begin(
        session: AsyncSession,
        principal: Principal,
        operation: str,
        idempotency_key: str,
        payload: object,
    ) -> tuple[IdempotencyRecord, CustodyContinuityCommandResult | None]:
        record = await IdempotencyRepository(session).begin(
            actor_user_id=principal.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_payload_hash(payload),
        )
        if record.status == "COMPLETED":
            stored = record.response_payload or {}
            return record, CustodyContinuityCommandResult(
                event_id=UUID(str(stored["event_id"])),
                object_id=UUID(str(stored["object_id"])),
                status=str(stored["status"]),
                replayed=True,
            )
        return record, None

    @staticmethod
    def _complete(
        record: IdempotencyRecord,
        event_id: UUID,
        object_id: UUID,
        status: str,
    ) -> CustodyContinuityCommandResult:
        IdempotencyRepository.complete(
            record,
            response_status=201,
            response_payload={
                "event_id": str(event_id),
                "object_id": str(object_id),
                "status": status,
            },
        )
        return CustodyContinuityCommandResult(event_id, object_id, status)

    @staticmethod
    async def _audit(
        session: AsyncSession,
        principal: Principal,
        cooperative_id: UUID,
        action: str,
        object_id: UUID,
        event_id: UUID,
        request_id: UUID | None,
        payload: dict[str, object],
    ) -> None:
        await AuditRepository(session).record(
            action=action,
            object_type="CustodyContinuityCase",
            object_id=object_id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={**payload, "signed_event_id": str(event_id)},
        )

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


def _lot_quantity(lot: InventoryLot) -> Decimal:
    return exact_quantity(
        lot.current_quantity
        if lot.current_quantity is not None
        else lot.declared_quantity,
        allow_zero=True,
    )


def _temporary_expiry(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise _error("CUSTODY_CONTINUITY_EXPIRY_INVALID", 422)
    normalized = value.astimezone(UTC)
    duration = normalized - datetime.now(UTC)
    if duration < MIN_TEMPORARY_DURATION or duration > MAX_TEMPORARY_DURATION:
        raise _error("CUSTODY_CONTINUITY_EXPIRY_INVALID", 422)
    return normalized


def _evidence_refs(values: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(item.strip() for item in values if item.strip()))
    if not 1 <= len(normalized) <= 10 or any(
        REFERENCE_PATTERN.fullmatch(item) is None for item in normalized
    ):
        raise _error("CUSTODY_CONTINUITY_EVIDENCE_INVALID", 422)
    return normalized


def _reason(value: str) -> str:
    normalized = value.strip().upper()
    if REASON_PATTERN.fullmatch(normalized) is None:
        raise _error("CUSTODY_CONTINUITY_REASON_INVALID", 422)
    return normalized


def _bounded_text(value: str, code: str, limit: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise _error(code, 422)
    return normalized


def _version_conflict(current_version: int) -> DomainError:
    return DomainError(
        code="VERSION_CONFLICT",
        message_key="errors.request.version_conflict",
        parameters={"current_version": current_version},
        status_code=409,
    )


def _error(code: str, status_code: int) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.inventory.{code.lower()}",
        status_code=status_code,
    )
