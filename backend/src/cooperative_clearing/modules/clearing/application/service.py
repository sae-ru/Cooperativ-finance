"""Transactional lifecycle for deterministic local clearing."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.clearing.application.common import (
    ClearingCommandResult,
    begin_clearing_command,
    clearing_participant_actor,
    clearing_role_actor,
    complete_clearing_command,
)
from cooperative_clearing.modules.clearing.domain.engine import (
    ClearingCycleStatus,
    ClearingInput,
    ClearingInputEntry,
    ClearingPolicyParameters,
    ClearingPolicyStatus,
    RoundingMode,
    calculate_clearing,
    clearing_error,
    clearing_input_payload,
    decimal_string,
    order_clearing_entries,
    policy_parameters_payload,
)
from cooperative_clearing.modules.clearing.domain.verifier import verify_proof_payload
from cooperative_clearing.modules.clearing.infrastructure.models import (
    ClearingAccountingExport,
    ClearingApproval,
    ClearingCycle,
    ClearingDispute,
    ClearingEntry,
    ClearingInputSnapshot,
    ClearingPolicy,
    ClearingPosition,
    ClearingProof,
    ClearingStatement,
)
from cooperative_clearing.modules.exchange.application.service import ExchangeService
from cooperative_clearing.modules.exchange.domain.types import (
    OPERABLE_OBLIGATION_STATUSES,
    ObligationStatus,
    obligation_status_for,
)
from cooperative_clearing.modules.exchange.infrastructure.models import (
    Obligation,
    ObligationDispute,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.infrastructure.models import (
    EvidenceBlob,
    EvidenceLink,
    UnitOfMeasure,
)
from cooperative_clearing.modules.journal.application.service import SignedJournalService
from cooperative_clearing.modules.journal.domain.assurance import (
    CommandAssurance,
    ExposureCategory,
    ExposureClaim,
    ExposureEffect,
    actor_party,
    member_party,
)
from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.shared.core.config import Settings

POLICY_PROPOSE_ROLES = {RoleCode.CLEARING_OPERATOR}
POLICY_APPROVE_ROLES = {RoleCode.CLEARING_CONTROLLER}
CYCLE_OPERATOR_ROLES = {RoleCode.CLEARING_OPERATOR}
CYCLE_CONTROLLER_ROLES = {RoleCode.CLEARING_CONTROLLER}
CYCLE_FINALIZER_ROLES = {RoleCode.CLEARING_FINALIZER}
RECONCILE_ROLES = {RoleCode.CLEARING_FINALIZER, RoleCode.AUDITOR}


class ClearingService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.journal = SignedJournalService(settings)

    async def propose_policy(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        valuation_unit_id: UUID,
        decimal_scale: int,
        rounding_mode: RoundingMode,
        minimum_operation: Decimal,
        max_iterations: int,
        max_cycle_length: int,
        dispute_window_seconds: int,
        required_approvals: int,
        liquidity_order: Sequence[str],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ClearingCommandResult:
        normalized_liquidity = tuple(
            self._code(item, "LIQUIDITY_CLASS_INVALID", 16) for item in liquidity_order
        )
        provisional = ClearingPolicyParameters(
            policy_version=1,
            algorithm_id="LOCAL_NETTING",
            algorithm_version="1.0.0",
            decimal_scale=decimal_scale,
            rounding_mode=rounding_mode,
            minimum_operation=minimum_operation,
            max_iterations=max_iterations,
            max_cycle_length=max_cycle_length,
            liquidity_order=normalized_liquidity,
        ).validate()
        if not 0 <= dispute_window_seconds <= 2_592_000:
            raise clearing_error("DISPUTE_WINDOW_INVALID")
        if not 1 <= required_approvals <= 3:
            raise clearing_error("REQUIRED_APPROVALS_INVALID")
        command_payload = {
            "cooperative_id": str(cooperative_id),
            "valuation_unit_id": str(valuation_unit_id),
            **policy_parameters_payload(provisional),
            "dispute_window_seconds": dispute_window_seconds,
            "required_approvals": required_approvals,
        }
        record, replay = await begin_clearing_command(
            session, principal, "CLEARING_PROPOSE_POLICY", idempotency_key, command_payload
        )
        if replay is not None:
            return replay
        actor = await clearing_role_actor(session, principal, cooperative_id, POLICY_PROPOSE_ROLES)
        await self._lock_cooperative(session, cooperative_id)
        unit = await session.get(UnitOfMeasure, valuation_unit_id)
        if (
            unit is None
            or unit.cooperative_id != cooperative_id
            or unit.status != "ACTIVE"
            or unit.dimension != "VALUATION"
            or unit.decimal_scale != decimal_scale
        ):
            raise clearing_error("VALUATION_UNIT_NOT_ELIGIBLE", 409)
        current_version = await session.scalar(
            select(func.coalesce(func.max(ClearingPolicy.policy_version), 0)).where(
                ClearingPolicy.cooperative_id == cooperative_id
            )
        )
        policy_version = int(current_version or 0) + 1
        terms_payload = {
            **command_payload,
            "policy_version": policy_version,
            "valuation_unit_code": unit.code,
        }
        terms_hash = payload_hash(terms_payload)
        policy_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="clearing.policy_proposed",
            aggregate_type="clearing_policy",
            aggregate_id=policy_id,
            aggregate_version=1,
            actor=actor,
            payload={**terms_payload, "policy_id": str(policy_id), "terms_hash": terms_hash},
        )
        session.add(
            ClearingPolicy(
                id=policy_id,
                cooperative_id=cooperative_id,
                policy_version=policy_version,
                valuation_unit_id=valuation_unit_id,
                algorithm_id="LOCAL_NETTING",
                algorithm_version="1.0.0",
                decimal_scale=decimal_scale,
                rounding_mode=rounding_mode.value,
                minimum_operation=minimum_operation,
                max_iterations=max_iterations,
                max_cycle_length=max_cycle_length,
                dispute_window_seconds=dispute_window_seconds,
                required_approvals=required_approvals,
                liquidity_order=list(normalized_liquidity),
                terms_payload=terms_payload,
                terms_hash=terms_hash,
                status=ClearingPolicyStatus.PROPOSED.value,
                proposed_by_user_id=principal.user_id,
                proposed_by_member_id=actor.person_id,
                proposed_role_assignment_id=actor.role_assignment_id,
                proposed_event_id=event.event_id,
                version=1,
            )
        )
        await self._audit(
            session,
            principal,
            cooperative_id,
            "CLEARING_POLICY_PROPOSED",
            "ClearingPolicy",
            policy_id,
            event.event_id,
            request_id,
        )
        return complete_clearing_command(record, event.event_id, policy_id)

    async def approve_policy(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        policy_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ClearingCommandResult:
        policy = await session.get(ClearingPolicy, policy_id, with_for_update=True)
        if policy is None:
            raise clearing_error("POLICY_NOT_FOUND", 404)
        payload = {"policy_id": str(policy_id), "expected_version": expected_version}
        record, replay = await begin_clearing_command(
            session, principal, "CLEARING_APPROVE_POLICY", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(policy.version, expected_version)
        if policy.status != ClearingPolicyStatus.PROPOSED.value:
            raise clearing_error("POLICY_NOT_PROPOSED", 409)
        actor = await clearing_role_actor(
            session, principal, policy.cooperative_id, POLICY_APPROVE_ROLES
        )
        if (
            principal.user_id == policy.proposed_by_user_id
            or actor.person_id == policy.proposed_by_member_id
        ):
            raise clearing_error("INDEPENDENT_APPROVER_REQUIRED", 409)
        await self._lock_cooperative(session, policy.cooperative_id)
        current = (
            await session.execute(
                select(ClearingPolicy)
                .where(
                    ClearingPolicy.cooperative_id == policy.cooperative_id,
                    ClearingPolicy.status == ClearingPolicyStatus.ACTIVE.value,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if current is not None:
            superseded = await self.journal.append(
                session,
                event_type="clearing.policy_superseded",
                aggregate_type="clearing_policy",
                aggregate_id=current.id,
                aggregate_version=current.version + 1,
                actor=actor,
                payload={
                    "policy_id": str(current.id),
                    "replacement_policy_id": str(policy.id),
                    "replacement_terms_hash": policy.terms_hash,
                },
            )
            current.status = ClearingPolicyStatus.SUPERSEDED.value
            current.version += 1
            await self._audit(
                session,
                principal,
                policy.cooperative_id,
                "CLEARING_POLICY_SUPERSEDED",
                "ClearingPolicy",
                current.id,
                superseded.event_id,
                request_id,
            )
        now = datetime.now(UTC)
        event = await self.journal.append(
            session,
            event_type="clearing.policy_approved",
            aggregate_type="clearing_policy",
            aggregate_id=policy.id,
            aggregate_version=policy.version + 1,
            actor=actor,
            payload={**payload, "terms_hash": policy.terms_hash},
        )
        policy.status = ClearingPolicyStatus.ACTIVE.value
        policy.approved_by_user_id = principal.user_id
        policy.approved_by_member_id = actor.person_id
        policy.approved_role_assignment_id = actor.role_assignment_id
        policy.approved_event_id = event.event_id
        policy.approved_at = now
        policy.version += 1
        await self._audit(
            session,
            principal,
            policy.cooperative_id,
            "CLEARING_POLICY_APPROVED",
            "ClearingPolicy",
            policy.id,
            event.event_id,
            request_id,
        )
        return complete_clearing_command(record, event.event_id, policy.id)

    async def create_cycle(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        policy_id: UUID,
        cycle_code: str,
        period_start: datetime,
        period_end: datetime,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ClearingCommandResult:
        code = self._code(cycle_code, "CYCLE_CODE_INVALID", 80)
        start = self._utc(period_start, "PERIOD_START_INVALID")
        end = self._utc(period_end, "PERIOD_END_INVALID")
        if end <= start:
            raise clearing_error("PERIOD_INVALID")
        payload = {
            "cooperative_id": str(cooperative_id),
            "policy_id": str(policy_id),
            "cycle_code": code,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
        }
        record, replay = await begin_clearing_command(
            session, principal, "CLEARING_CREATE_CYCLE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        actor = await clearing_role_actor(session, principal, cooperative_id, CYCLE_OPERATOR_ROLES)
        policy = await self._active_policy(session, policy_id)
        if policy.cooperative_id != cooperative_id:
            raise clearing_error("POLICY_SCOPE_MISMATCH", 409)
        cycle_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="clearing.cycle_created",
            aggregate_type="clearing_cycle",
            aggregate_id=cycle_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "cycle_id": str(cycle_id), "policy_terms_hash": policy.terms_hash},
        )
        session.add(
            ClearingCycle(
                id=cycle_id,
                cooperative_id=cooperative_id,
                policy_id=policy_id,
                cycle_code=code,
                period_start=start,
                period_end=end,
                status=ClearingCycleStatus.DRAFT.value,
                collected_count=0,
                created_by_user_id=principal.user_id,
                created_by_member_id=actor.person_id,
                created_role_assignment_id=actor.role_assignment_id,
                created_event_id=event.event_id,
                version=1,
            )
        )
        await self._audit(
            session,
            principal,
            cooperative_id,
            "CLEARING_CYCLE_CREATED",
            "ClearingCycle",
            cycle_id,
            event.event_id,
            request_id,
        )
        return complete_clearing_command(record, event.event_id, cycle_id)

    async def collect(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cycle_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ClearingCommandResult:
        cycle = await self._cycle(session, cycle_id, lock=True)
        payload = {"cycle_id": str(cycle_id), "expected_version": expected_version}
        record, replay = await begin_clearing_command(
            session, principal, "CLEARING_COLLECT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(cycle.version, expected_version)
        if cycle.status not in {
            ClearingCycleStatus.DRAFT.value,
            ClearingCycleStatus.COLLECTING.value,
        }:
            raise clearing_error("CYCLE_NOT_COLLECTABLE", 409)
        actor = await clearing_role_actor(
            session, principal, cycle.cooperative_id, CYCLE_OPERATOR_ROLES
        )
        policy = await self._active_policy(session, cycle.policy_id)
        count = await session.scalar(
            select(func.count())
            .select_from(Obligation)
            .where(
                Obligation.cooperative_id == cycle.cooperative_id,
                Obligation.unit_id == policy.valuation_unit_id,
                Obligation.clearing_allowed.is_(True),
                Obligation.quantity_total
                - Obligation.quantity_fulfilled
                - Obligation.quantity_cleared
                > 0,
            )
        )
        event = await self.journal.append(
            session,
            event_type="clearing.input_collected",
            aggregate_type="clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload={**payload, "candidate_count": int(count or 0)},
        )
        cycle.status = ClearingCycleStatus.COLLECTING.value
        cycle.collected_count = int(count or 0)
        cycle.collection_event_id = event.event_id
        cycle.version += 1
        cycle.updated_at = datetime.now(UTC)
        await self._audit(
            session,
            principal,
            cycle.cooperative_id,
            "CLEARING_INPUT_COLLECTED",
            "ClearingCycle",
            cycle.id,
            event.event_id,
            request_id,
        )
        return complete_clearing_command(record, event.event_id, cycle.id)

    async def freeze_input(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cycle_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ClearingCommandResult:
        cycle = await self._cycle(session, cycle_id, lock=True)
        payload = {"cycle_id": str(cycle_id), "expected_version": expected_version}
        record, replay = await begin_clearing_command(
            session, principal, "CLEARING_FREEZE_INPUT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(cycle.version, expected_version)
        if cycle.status != ClearingCycleStatus.COLLECTING.value:
            raise clearing_error("CYCLE_NOT_COLLECTING", 409)
        actor = await clearing_role_actor(
            session, principal, cycle.cooperative_id, CYCLE_OPERATOR_ROLES
        )
        policy = await self._active_policy(session, cycle.policy_id)
        entries = await self._collect_input_entries(session, cycle, policy)
        if not entries:
            raise clearing_error("CLEARING_INPUT_EMPTY", 409)
        parameters = self._policy_parameters(policy)
        ordered = order_clearing_entries(tuple(entries), parameters)
        input_payload = clearing_input_payload(str(cycle.id), ordered)
        input_hash = payload_hash(input_payload)
        now = datetime.now(UTC)
        snapshot_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="clearing.input_frozen",
            aggregate_type="clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload={
                **payload,
                "snapshot_id": str(snapshot_id),
                "input_hash": input_hash,
                "entry_count": len(ordered),
                "policy_version": policy.policy_version,
                "policy_terms_hash": policy.terms_hash,
            },
        )
        session.add(
            ClearingInputSnapshot(
                id=snapshot_id,
                cycle_id=cycle.id,
                input_version=1,
                policy_version=policy.policy_version,
                ordered_payload=input_payload,
                input_hash=input_hash,
                frozen_by_user_id=principal.user_id,
                frozen_by_member_id=actor.person_id,
                frozen_event_id=event.event_id,
                frozen_at=now,
            )
        )
        cycle.status = ClearingCycleStatus.INPUT_FROZEN.value
        cycle.input_hash = input_hash
        cycle.freeze_event_id = event.event_id
        cycle.version += 1
        cycle.updated_at = now
        await self._audit(
            session,
            principal,
            cycle.cooperative_id,
            "CLEARING_INPUT_FROZEN",
            "ClearingInputSnapshot",
            snapshot_id,
            event.event_id,
            request_id,
        )
        return complete_clearing_command(record, event.event_id, snapshot_id)

    async def preview(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cycle_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ClearingCommandResult:
        cycle = await self._cycle(session, cycle_id, lock=True)
        payload = {"cycle_id": str(cycle_id), "expected_version": expected_version}
        record, replay = await begin_clearing_command(
            session, principal, "CLEARING_PREVIEW", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(cycle.version, expected_version)
        if cycle.status != ClearingCycleStatus.INPUT_FROZEN.value:
            raise clearing_error("CYCLE_INPUT_NOT_FROZEN", 409)
        actor = await clearing_role_actor(
            session, principal, cycle.cooperative_id, CYCLE_OPERATOR_ROLES
        )
        policy = await self._active_policy(session, cycle.policy_id)
        snapshot = await self._snapshot(session, cycle.id)
        clearing_input = self._snapshot_input(snapshot)
        result = calculate_clearing(clearing_input, self._policy_parameters(policy))
        if result.input_hash != snapshot.input_hash or result.input_hash != cycle.input_hash:
            raise clearing_error("INPUT_HASH_MISMATCH", 409)
        event = await self.journal.append(
            session,
            event_type="clearing.preview_created",
            aggregate_type="clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload={
                **payload,
                "input_hash": result.input_hash,
                "parameters_hash": result.parameters_hash,
                "result_hash": result.result_hash,
                "total_before": decimal_string(result.total_before),
                "total_cleared": decimal_string(result.total_cleared),
                "total_after": decimal_string(result.total_after),
                "warnings": list(result.warnings),
            },
        )
        self._persist_preview(session, cycle.id, result)
        now = datetime.now(UTC)
        cycle.status = ClearingCycleStatus.PREVIEWED.value
        cycle.parameters_hash = result.parameters_hash
        cycle.result_hash = result.result_hash
        cycle.preview_event_id = event.event_id
        cycle.previewed_at = now
        cycle.version += 1
        cycle.updated_at = now
        await self._audit(
            session,
            principal,
            cycle.cooperative_id,
            "CLEARING_PREVIEW_CREATED",
            "ClearingCycle",
            cycle.id,
            event.event_id,
            request_id,
        )
        return complete_clearing_command(record, event.event_id, cycle.id)

    async def approve_preview(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cycle_id: UUID,
        expected_version: int,
        input_hash: str,
        result_hash: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ClearingCommandResult:
        cycle = await self._cycle(session, cycle_id, lock=True)
        payload = {
            "cycle_id": str(cycle_id),
            "expected_version": expected_version,
            "input_hash": input_hash,
            "result_hash": result_hash,
        }
        record, replay = await begin_clearing_command(
            session, principal, "CLEARING_APPROVE_PREVIEW", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(cycle.version, expected_version)
        if cycle.status != ClearingCycleStatus.PREVIEWED.value:
            raise clearing_error("CYCLE_NOT_PREVIEWED", 409)
        if cycle.input_hash != input_hash or cycle.result_hash != result_hash:
            raise clearing_error("APPROVAL_HASH_MISMATCH", 409)
        actor = await clearing_role_actor(
            session, principal, cycle.cooperative_id, CYCLE_CONTROLLER_ROLES
        )
        if (
            principal.user_id == cycle.created_by_user_id
            or actor.person_id == cycle.created_by_member_id
        ):
            raise clearing_error("INDEPENDENT_CONTROLLER_REQUIRED", 409)
        participant = await session.scalar(
            select(ClearingEntry.id).where(
                ClearingEntry.cycle_id == cycle.id,
                ClearingEntry.cleared_amount > 0,
                or_(
                    ClearingEntry.debtor_member_id == actor.person_id,
                    ClearingEntry.creditor_member_id == actor.person_id,
                ),
            )
        )
        if participant is not None:
            raise clearing_error("CONTROLLER_CONFLICT_OF_INTEREST", 409)
        approval_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="clearing.preview_approved",
            aggregate_type="clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload={**payload, "approval_id": str(approval_id)},
        )
        session.add(
            ClearingApproval(
                id=approval_id,
                cycle_id=cycle.id,
                approval_type="CONTROLLER",
                input_hash=input_hash,
                result_hash=result_hash,
                user_id=principal.user_id,
                member_id=actor.person_id,
                role_assignment_id=actor.role_assignment_id,
                event_id=event.event_id,
            )
        )
        await session.flush()
        policy = await self._active_policy(session, cycle.policy_id)
        approval_count = await session.scalar(
            select(func.count())
            .select_from(ClearingApproval)
            .where(ClearingApproval.cycle_id == cycle.id)
        )
        now = datetime.now(UTC)
        if int(approval_count or 0) >= policy.required_approvals:
            cycle.status = ClearingCycleStatus.DISPUTE_WINDOW.value
            cycle.dispute_until = now + timedelta(seconds=policy.dispute_window_seconds)
        cycle.version += 1
        cycle.updated_at = now
        await self._audit(
            session,
            principal,
            cycle.cooperative_id,
            "CLEARING_PREVIEW_APPROVED",
            "ClearingApproval",
            approval_id,
            event.event_id,
            request_id,
        )
        return complete_clearing_command(record, event.event_id, approval_id)

    async def open_dispute(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cycle_id: UUID,
        entry_id: UUID,
        reason_code: str,
        statement: str,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ClearingCommandResult:
        cycle = await self._cycle(session, cycle_id, lock=True)
        payload = {
            "cycle_id": str(cycle_id),
            "entry_id": str(entry_id),
            "reason_code": self._code(reason_code, "DISPUTE_REASON_INVALID", 80),
            "statement": self._text(statement, "DISPUTE_STATEMENT_INVALID", 4000),
            "evidence_ids": [str(value) for value in evidence_ids],
            "expected_version": expected_version,
        }
        record, replay = await begin_clearing_command(
            session, principal, "CLEARING_OPEN_DISPUTE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(cycle.version, expected_version)
        if cycle.status != ClearingCycleStatus.DISPUTE_WINDOW.value:
            raise clearing_error("DISPUTE_WINDOW_NOT_OPEN", 409)
        if cycle.dispute_until is None or datetime.now(UTC) > cycle.dispute_until:
            raise clearing_error("DISPUTE_WINDOW_EXPIRED", 409)
        entry = await session.get(ClearingEntry, entry_id)
        if entry is None or entry.cycle_id != cycle.id:
            raise clearing_error("CLEARING_ENTRY_NOT_FOUND", 404)
        actor = await clearing_participant_actor(session, principal, cycle.cooperative_id)
        if actor.person_id not in {entry.debtor_member_id, entry.creditor_member_id}:
            raise clearing_error("DISPUTE_PARTICIPANT_REQUIRED", 403)
        evidence = await EvidenceService.require_ready(
            session, cycle.cooperative_id, evidence_ids, required=True
        )
        evidence_payload = self._evidence_payload(evidence)
        dispute_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="clearing.dispute_opened",
            aggregate_type="clearing_dispute",
            aggregate_id=dispute_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "dispute_id": str(dispute_id), "evidence": evidence_payload},
        )
        session.add(
            ClearingDispute(
                id=dispute_id,
                cycle_id=cycle.id,
                entry_id=entry.id,
                reason_code=str(payload["reason_code"]),
                statement=str(payload["statement"]),
                evidence_refs=evidence_payload,
                status="OPEN",
                opened_by_user_id=principal.user_id,
                opened_by_member_id=actor.person_id,
                opened_event_id=event.event_id,
                version=1,
            )
        )
        self._link_evidence(session, evidence, event.event_id, "CLEARING_DISPUTE", dispute_id)
        cycle.status = ClearingCycleStatus.DISPUTED.value
        cycle.version += 1
        cycle.updated_at = datetime.now(UTC)
        await self._audit(
            session,
            principal,
            cycle.cooperative_id,
            "CLEARING_DISPUTE_OPENED",
            "ClearingDispute",
            dispute_id,
            event.event_id,
            request_id,
        )
        return complete_clearing_command(record, event.event_id, dispute_id)

    async def decide_dispute(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        dispute_id: UUID,
        decision: str,
        resolution_notes: str,
        expected_version: int,
        expected_cycle_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ClearingCommandResult:
        dispute = await session.get(ClearingDispute, dispute_id, with_for_update=True)
        if dispute is None:
            raise clearing_error("CLEARING_DISPUTE_NOT_FOUND", 404)
        cycle = await self._cycle(session, dispute.cycle_id, lock=True)
        normalized_decision = self._code(decision, "DISPUTE_DECISION_INVALID", 16)
        if normalized_decision not in {"UPHOLD", "REJECT"}:
            raise clearing_error("DISPUTE_DECISION_INVALID")
        payload = {
            "dispute_id": str(dispute_id),
            "decision": normalized_decision,
            "resolution_notes": self._text(
                resolution_notes, "DISPUTE_RESOLUTION_NOTES_INVALID", 4000
            ),
            "expected_version": expected_version,
            "expected_cycle_version": expected_cycle_version,
        }
        record, replay = await begin_clearing_command(
            session, principal, "CLEARING_DECIDE_DISPUTE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(dispute.version, expected_version)
        self._version(cycle.version, expected_cycle_version)
        if dispute.status != "OPEN" or cycle.status != ClearingCycleStatus.DISPUTED.value:
            raise clearing_error("DISPUTE_NOT_OPEN", 409)
        actor = await clearing_role_actor(
            session, principal, cycle.cooperative_id, CYCLE_CONTROLLER_ROLES
        )
        entry = await session.get(ClearingEntry, dispute.entry_id)
        if entry is None:
            raise clearing_error("CLEARING_ENTRY_NOT_FOUND", 404)
        if actor.person_id in {
            dispute.opened_by_member_id,
            entry.debtor_member_id,
            entry.creditor_member_id,
        }:
            raise clearing_error("DISPUTE_RESOLVER_CONFLICT", 409)
        now = datetime.now(UTC)
        event = await self.journal.append(
            session,
            event_type="clearing.dispute_decided",
            aggregate_type="clearing_dispute",
            aggregate_id=dispute.id,
            aggregate_version=dispute.version + 1,
            actor=actor,
            payload=payload,
        )
        dispute.status = "UPHELD" if normalized_decision == "UPHOLD" else "REJECTED"
        dispute.resolution_notes = str(payload["resolution_notes"])
        dispute.resolved_by_user_id = principal.user_id
        dispute.resolved_by_member_id = actor.person_id
        dispute.resolution_event_id = event.event_id
        dispute.resolved_at = now
        dispute.version += 1
        if normalized_decision == "REJECT":
            remaining_open = await session.scalar(
                select(ClearingDispute.id).where(
                    ClearingDispute.cycle_id == cycle.id,
                    ClearingDispute.status == "OPEN",
                    ClearingDispute.id != dispute.id,
                )
            )
            if remaining_open is None:
                cycle.status = ClearingCycleStatus.DISPUTE_WINDOW.value
        cycle.version += 1
        cycle.updated_at = now
        await self._audit(
            session,
            principal,
            cycle.cooperative_id,
            "CLEARING_DISPUTE_DECIDED",
            "ClearingDispute",
            dispute.id,
            event.event_id,
            request_id,
        )
        return complete_clearing_command(record, event.event_id, dispute.id)

    async def mark_ready(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cycle_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ClearingCommandResult:
        cycle = await self._cycle(session, cycle_id, lock=True)
        payload = {"cycle_id": str(cycle_id), "expected_version": expected_version}
        record, replay = await begin_clearing_command(
            session, principal, "CLEARING_MARK_READY", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(cycle.version, expected_version)
        if cycle.status != ClearingCycleStatus.DISPUTE_WINDOW.value:
            raise clearing_error("CYCLE_NOT_IN_DISPUTE_WINDOW", 409)
        now = datetime.now(UTC)
        if cycle.dispute_until is None or now < cycle.dispute_until:
            raise clearing_error("DISPUTE_WINDOW_ACTIVE", 409)
        actor = await clearing_role_actor(
            session, principal, cycle.cooperative_id, CYCLE_FINALIZER_ROLES
        )
        unresolved = await session.scalar(
            select(ClearingDispute.id).where(
                ClearingDispute.cycle_id == cycle.id,
                ClearingDispute.status.in_(["OPEN", "UPHELD"]),
            )
        )
        if unresolved is not None:
            raise clearing_error("UNRESOLVED_CLEARING_DISPUTE", 409)
        policy = await self._active_policy(session, cycle.policy_id)
        approvals = await session.scalar(
            select(func.count())
            .select_from(ClearingApproval)
            .where(
                ClearingApproval.cycle_id == cycle.id,
                ClearingApproval.input_hash == cycle.input_hash,
                ClearingApproval.result_hash == cycle.result_hash,
            )
        )
        if int(approvals or 0) < policy.required_approvals:
            raise clearing_error("CLEARING_APPROVALS_MISSING", 409)
        event = await self.journal.append(
            session,
            event_type="clearing.cycle_ready",
            aggregate_type="clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload={
                **payload,
                "input_hash": cycle.input_hash,
                "result_hash": cycle.result_hash,
                "approval_count": int(approvals or 0),
            },
        )
        cycle.status = ClearingCycleStatus.READY_TO_FINALIZE.value
        cycle.ready_event_id = event.event_id
        cycle.version += 1
        cycle.updated_at = now
        await self._audit(
            session,
            principal,
            cycle.cooperative_id,
            "CLEARING_CYCLE_READY",
            "ClearingCycle",
            cycle.id,
            event.event_id,
            request_id,
        )
        return complete_clearing_command(record, event.event_id, cycle.id)

    async def finalize(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cycle_id: UUID,
        expected_version: int,
        result_hash: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ClearingCommandResult:
        cycle = await self._cycle(session, cycle_id, lock=True)
        payload = {
            "cycle_id": str(cycle_id),
            "expected_version": expected_version,
            "result_hash": result_hash,
        }
        record, replay = await begin_clearing_command(
            session, principal, "CLEARING_FINALIZE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(cycle.version, expected_version)
        if cycle.status != ClearingCycleStatus.READY_TO_FINALIZE.value:
            raise clearing_error("CYCLE_NOT_READY", 409)
        if cycle.result_hash != result_hash:
            raise clearing_error("FINALIZE_RESULT_HASH_MISMATCH", 409)
        actor = await clearing_role_actor(
            session, principal, cycle.cooperative_id, CYCLE_FINALIZER_ROLES
        )
        if principal.user_id == cycle.created_by_user_id:
            raise clearing_error("INDEPENDENT_FINALIZER_REQUIRED", 409)
        approval_by_same_user = await session.scalar(
            select(ClearingApproval.id).where(
                ClearingApproval.cycle_id == cycle.id,
                ClearingApproval.user_id == principal.user_id,
            )
        )
        if approval_by_same_user is not None:
            raise clearing_error("INDEPENDENT_FINALIZER_REQUIRED", 409)
        await self._lock_cooperative(session, cycle.cooperative_id)
        policy = await self._active_policy(session, cycle.policy_id)
        snapshot = await self._snapshot(session, cycle.id)
        result = calculate_clearing(self._snapshot_input(snapshot), self._policy_parameters(policy))
        if (
            result.input_hash != cycle.input_hash
            or result.parameters_hash != cycle.parameters_hash
            or result.result_hash != cycle.result_hash
        ):
            raise clearing_error("FINALIZE_RECALCULATION_MISMATCH", 409)
        result_by_id = {UUID(item.obligation_id): item for item in result.entries}
        approvals = list(
            (
                await session.execute(
                    select(ClearingApproval)
                    .where(ClearingApproval.cycle_id == cycle.id)
                    .order_by(ClearingApproval.id)
                )
            ).scalars()
        )
        approval_parties = tuple(
            member_party(item.member_id, item.role_assignment_id) for item in approvals
        )
        obligations = list(
            (
                await session.execute(
                    select(Obligation)
                    .where(Obligation.id.in_(sorted(result_by_id, key=str)))
                    .order_by(Obligation.id)
                    .with_for_update()
                )
            ).scalars()
        )
        if len(obligations) != len(result_by_id):
            raise clearing_error("FINALIZE_OBLIGATION_MISSING", 409)
        obligation_events: list[dict[str, object]] = []
        affected_deals: set[UUID] = set()
        for obligation in obligations:
            entry = result_by_id[obligation.id]
            available = (
                obligation.quantity_total
                - obligation.quantity_submitted
                - obligation.quantity_fulfilled
                - obligation.quantity_cleared
            )
            if (
                obligation.version != entry.obligation_version
                or available != entry.amount_before
                or obligation.unit_id != UUID(entry.unit_id)
                or not obligation.clearing_allowed
                or ObligationStatus(obligation.status) not in OPERABLE_OBLIGATION_STATUSES
            ):
                raise clearing_error("FINALIZE_INPUT_VERSION_CONFLICT", 409)
            if entry.cleared_amount <= 0:
                continue
            evidence_refs = (
                {
                    "cycle_id": str(cycle.id),
                    "input_hash": cycle.input_hash,
                    "result_hash": result.result_hash,
                    "kind": "CLEARING_RESULT",
                },
            )
            event = await self.journal.append(
                session,
                event_type="obligations.obligation_cleared",
                aggregate_type="obligation",
                aggregate_id=obligation.id,
                aggregate_version=obligation.version + 1,
                actor=actor,
                payload={
                    "cycle_id": str(cycle.id),
                    "obligation_id": str(obligation.id),
                    "amount_before": decimal_string(entry.amount_before),
                    "cleared_amount": decimal_string(entry.cleared_amount),
                    "amount_after": decimal_string(entry.amount_after),
                    "result_hash": result.result_hash,
                },
                assurance=CommandAssurance(
                    on_behalf_of=actor_party(actor),
                    next_responsible=(
                        (member_party(obligation.debtor_member_id),)
                        if entry.amount_after > 0
                        else ()
                    ),
                    attesters=(member_party(actor.person_id, actor.role_assignment_id),),
                    approvers=approval_parties,
                    exposure=ExposureClaim(
                        category=ExposureCategory.OBLIGATION,
                        effect=ExposureEffect.REDUCE,
                        subject_type="obligation",
                        subject_id=obligation.id,
                        amount=entry.cleared_amount,
                        unit=str(obligation.unit_id),
                    ),
                    evidence_refs=evidence_refs,
                ),
            )
            obligation.quantity_cleared += entry.cleared_amount
            amounts = ExchangeService._amounts(obligation)
            obligation.status = obligation_status_for(
                amounts, overdue=obligation.due_at < datetime.now(UTC)
            ).value
            obligation.last_event_id = event.event_id
            obligation.version += 1
            obligation.updated_at = datetime.now(UTC)
            affected_deals.add(obligation.deal_id)
            obligation_events.append(
                {"obligation_id": str(obligation.id), "event_id": str(event.event_id)}
            )
        statement_specs = self._statement_specs(cycle, result)
        statement_hashes = [payload_hash(value) for value in statement_specs]
        unsigned_proof: dict[str, object] = {
            "cycle_id": str(cycle.id),
            "input_hash": result.input_hash,
            "parameters_hash": result.parameters_hash,
            "result_hash": result.result_hash,
            "input": snapshot.ordered_payload,
            "parameters": policy_parameters_payload(self._policy_parameters(policy)),
            "result": result.payload(),
            "policy_terms_hash": policy.terms_hash,
            "approval_event_ids": [str(item.event_id) for item in approvals],
            "statement_hashes": statement_hashes,
        }
        proof_hash = payload_hash(unsigned_proof)
        proof_payload = {**unsigned_proof, "proof_hash": proof_hash}
        finalization_evidence = (
            {
                "input_hash": result.input_hash,
                "parameters_hash": result.parameters_hash,
                "result_hash": result.result_hash,
                "proof_hash": proof_hash,
                "approval_event_ids": [str(item.event_id) for item in approvals],
                "kind": "CLEARING_FINALIZATION_PROOF",
            },
        )
        final_event = await self.journal.append(
            session,
            event_type="clearing.cycle_finalized",
            aggregate_type="clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload={
                **payload,
                "input_hash": result.input_hash,
                "parameters_hash": result.parameters_hash,
                "proof_hash": proof_hash,
                "obligation_events": obligation_events,
                "statement_hashes": statement_hashes,
                "total_cleared": decimal_string(result.total_cleared),
            },
            assurance=CommandAssurance(
                on_behalf_of=actor_party(actor),
                next_responsible=(actor_party(actor),),
                attesters=(member_party(actor.person_id, actor.role_assignment_id),),
                approvers=approval_parties,
                exposure=ExposureClaim(
                    category=ExposureCategory.OBLIGATION,
                    effect=ExposureEffect.FINALIZE,
                    subject_type="clearing_cycle",
                    subject_id=cycle.id,
                    basis_refs=(
                        result.input_hash,
                        result.parameters_hash,
                        result.result_hash,
                        proof_hash,
                    ),
                ),
                evidence_refs=finalization_evidence,
            ),
        )
        proof_id = uuid4()
        session.add(
            ClearingProof(
                id=proof_id,
                cycle_id=cycle.id,
                proof_payload=proof_payload,
                proof_hash=proof_hash,
                finalized_event_id=final_event.event_id,
                node_event_hash=final_event.event_hash,
            )
        )
        for spec, statement_hash in zip(statement_specs, statement_hashes, strict=True):
            statement_id = uuid4()
            statement_event = await self.journal.append(
                session,
                event_type="clearing.statement_created",
                aggregate_type="clearing_statement",
                aggregate_id=statement_id,
                aggregate_version=1,
                actor=actor,
                payload={
                    "cycle_id": str(cycle.id),
                    "member_id": spec["member_id"],
                    "unit_id": spec["unit_id"],
                    "statement_hash": statement_hash,
                    "finalized_event_id": str(final_event.event_id),
                },
            )
            session.add(
                ClearingStatement(
                    id=statement_id,
                    cycle_id=cycle.id,
                    member_id=UUID(str(spec["member_id"])),
                    unit_id=UUID(str(spec["unit_id"])),
                    statement_payload=spec,
                    statement_hash=statement_hash,
                    created_event_id=statement_event.event_id,
                )
            )
        for deal_id in affected_deals:
            await ExchangeService(self.settings)._refresh_deal_status(session, deal_id)
        now = datetime.now(UTC)
        cycle.status = ClearingCycleStatus.FINALIZED.value
        cycle.finalized_event_id = final_event.event_id
        cycle.finalized_at = now
        cycle.version += 1
        cycle.updated_at = now
        await self._audit(
            session,
            principal,
            cycle.cooperative_id,
            "CLEARING_CYCLE_FINALIZED",
            "ClearingProof",
            proof_id,
            final_event.event_id,
            request_id,
        )
        return complete_clearing_command(record, final_event.event_id, proof_id)

    async def reconcile(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cycle_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ClearingCommandResult:
        cycle = await self._cycle(session, cycle_id, lock=True)
        payload = {"cycle_id": str(cycle_id), "expected_version": expected_version}
        record, replay = await begin_clearing_command(
            session, principal, "CLEARING_RECONCILE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(cycle.version, expected_version)
        if cycle.status != ClearingCycleStatus.FINALIZED.value:
            raise clearing_error("CYCLE_NOT_FINALIZED", 409)
        actor = await clearing_role_actor(session, principal, cycle.cooperative_id, RECONCILE_ROLES)
        proof = (
            await session.execute(select(ClearingProof).where(ClearingProof.cycle_id == cycle.id))
        ).scalar_one_or_none()
        if proof is None:
            raise clearing_error("CLEARING_PROOF_NOT_FOUND", 409)
        verification = verify_proof_payload(proof.proof_payload)
        statements = list(
            (
                await session.execute(
                    select(ClearingStatement)
                    .where(ClearingStatement.cycle_id == cycle.id)
                    .order_by(ClearingStatement.member_id, ClearingStatement.unit_id)
                )
            ).scalars()
        )
        entries = list(
            (
                await session.execute(
                    select(ClearingEntry)
                    .where(ClearingEntry.cycle_id == cycle.id)
                    .order_by(ClearingEntry.obligation_id)
                )
            ).scalars()
        )
        raw_statement_hashes = proof.proof_payload.get("statement_hashes")
        if not isinstance(raw_statement_hashes, list):
            raise clearing_error("PROOF_STATEMENTS_INVALID", 500)
        expected_statement_hashes = sorted(str(value) for value in raw_statement_hashes)
        if sorted(item.statement_hash for item in statements) != expected_statement_hashes:
            raise clearing_error("STATEMENT_RECONCILIATION_MISMATCH", 409)
        export_payload: dict[str, object] = {
            "schema_version": 1,
            "status": "DRAFT",
            "cooperative_id": str(cycle.cooperative_id),
            "cycle_id": str(cycle.id),
            "cycle_code": cycle.cycle_code,
            "period_start": cycle.period_start.isoformat(),
            "period_end": cycle.period_end.isoformat(),
            "proof_hash": proof.proof_hash,
            "source_event_id": str(proof.finalized_event_id),
            "documents": [
                {
                    "obligation_id": str(item.obligation_id),
                    "unit_id": str(item.unit_id),
                    "amount_before": decimal_string(item.amount_before),
                    "cleared_amount": decimal_string(item.cleared_amount),
                    "amount_after": decimal_string(item.amount_after),
                }
                for item in entries
                if item.cleared_amount > 0
            ],
            "statement_hashes": [item.statement_hash for item in statements],
            "verification": verification,
        }
        package_hash = payload_hash(export_payload)
        export_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="clearing.cycle_reconciled",
            aggregate_type="clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload={**payload, "export_id": str(export_id), "package_hash": package_hash},
            assurance=CommandAssurance(
                on_behalf_of=actor_party(actor),
                next_responsible=(),
                attesters=(member_party(actor.person_id, actor.role_assignment_id),),
                exposure=ExposureClaim(
                    category=ExposureCategory.OBLIGATION,
                    effect=ExposureEffect.FINALIZE,
                    subject_type="clearing_cycle",
                    subject_id=cycle.id,
                    basis_refs=(proof.proof_hash, package_hash),
                ),
                evidence_refs=(
                    {
                        "event_id": str(proof.finalized_event_id),
                        "proof_hash": proof.proof_hash,
                        "package_hash": package_hash,
                        "kind": "CLEARING_RECONCILIATION_PROOF",
                    },
                ),
            ),
        )
        session.add(
            ClearingAccountingExport(
                id=export_id,
                cycle_id=cycle.id,
                export_payload=export_payload,
                package_hash=package_hash,
                created_by_user_id=principal.user_id,
                created_event_id=event.event_id,
            )
        )
        now = datetime.now(UTC)
        cycle.status = ClearingCycleStatus.RECONCILED.value
        cycle.reconciled_event_id = event.event_id
        cycle.reconciled_at = now
        cycle.version += 1
        cycle.updated_at = now
        await self._audit(
            session,
            principal,
            cycle.cooperative_id,
            "CLEARING_CYCLE_RECONCILED",
            "ClearingAccountingExport",
            export_id,
            event.event_id,
            request_id,
        )
        return complete_clearing_command(record, event.event_id, export_id)

    async def _collect_input_entries(
        self, session: AsyncSession, cycle: ClearingCycle, policy: ClearingPolicy
    ) -> list[ClearingInputEntry]:
        obligations = list(
            (
                await session.execute(
                    select(Obligation)
                    .where(
                        Obligation.cooperative_id == cycle.cooperative_id,
                        Obligation.unit_id == policy.valuation_unit_id,
                        Obligation.clearing_allowed.is_(True),
                        Obligation.quantity_total
                        - Obligation.quantity_fulfilled
                        - Obligation.quantity_cleared
                        > 0,
                    )
                    .order_by(Obligation.id)
                )
            ).scalars()
        )
        disputed_ids = set(
            (
                await session.execute(
                    select(ObligationDispute.obligation_id).where(
                        ObligationDispute.obligation_id.in_([item.id for item in obligations]),
                        ObligationDispute.status == "OPEN",
                    )
                )
            ).scalars()
        )
        entries: list[ClearingInputEntry] = []
        for item in obligations:
            amount = item.quantity_total - item.quantity_fulfilled - item.quantity_cleared
            status = ObligationStatus(item.status)
            reason: str | None = None
            if item.quantity_submitted > 0:
                reason = "PENDING_FULFILLMENT"
            elif item.id in disputed_ids or status is ObligationStatus.DISPUTED:
                reason = "DISPUTED"
            elif status not in OPERABLE_OBLIGATION_STATUSES:
                reason = f"STATUS_{status.value}"
            entries.append(
                ClearingInputEntry(
                    obligation_id=str(item.id),
                    debtor_member_id=str(item.debtor_member_id),
                    creditor_member_id=str(item.creditor_member_id),
                    unit_id=str(item.unit_id),
                    amount=amount,
                    obligation_version=item.version,
                    liquidity_class=item.liquidity_class,
                    eligible=reason is None,
                    exclusion_reason=reason,
                    disputed=reason == "DISPUTED",
                    frozen=status in {ObligationStatus.DEFAULTED, ObligationStatus.CLOSED},
                    risk_limit=amount,
                )
            )
        return entries

    def _persist_preview(self, session: AsyncSession, cycle_id: UUID, result: object) -> None:
        from cooperative_clearing.modules.clearing.domain.engine import ClearingResult

        if not isinstance(result, ClearingResult):
            raise clearing_error("CLEARING_RESULT_INVALID", 500)
        positions: dict[tuple[UUID, UUID], dict[str, Decimal]] = {}
        for item in result.entries:
            debtor_id = UUID(item.debtor_member_id)
            creditor_id = UUID(item.creditor_member_id)
            unit_id = UUID(item.unit_id)
            session.add(
                ClearingEntry(
                    id=uuid4(),
                    cycle_id=cycle_id,
                    obligation_id=UUID(item.obligation_id),
                    debtor_member_id=debtor_id,
                    creditor_member_id=creditor_id,
                    unit_id=unit_id,
                    obligation_version=item.obligation_version,
                    amount_before=item.amount_before,
                    cleared_amount=item.cleared_amount,
                    amount_after=item.amount_after,
                    inclusion_status=item.inclusion_status,
                    exclusion_reason=item.exclusion_reason,
                    allocations=[
                        {
                            "path_kind": allocation.path_kind,
                            "path_index": allocation.path_index,
                            "amount": decimal_string(allocation.amount),
                            "member_path": list(allocation.member_path),
                            "obligation_path": list(allocation.obligation_path),
                        }
                        for allocation in item.allocations
                    ],
                )
            )
            debtor = positions.setdefault((debtor_id, unit_id), self._empty_position())
            creditor = positions.setdefault((creditor_id, unit_id), self._empty_position())
            debtor["outgoing_before"] += item.amount_before
            debtor["outgoing_cleared"] += item.cleared_amount
            creditor["incoming_before"] += item.amount_before
            creditor["incoming_cleared"] += item.cleared_amount
        for (member_id, unit_id), value in positions.items():
            incoming_after = value["incoming_before"] - value["incoming_cleared"]
            outgoing_after = value["outgoing_before"] - value["outgoing_cleared"]
            session.add(
                ClearingPosition(
                    id=uuid4(),
                    cycle_id=cycle_id,
                    member_id=member_id,
                    unit_id=unit_id,
                    incoming_before=value["incoming_before"],
                    outgoing_before=value["outgoing_before"],
                    incoming_cleared=value["incoming_cleared"],
                    outgoing_cleared=value["outgoing_cleared"],
                    incoming_after=incoming_after,
                    outgoing_after=outgoing_after,
                    net_before=value["incoming_before"] - value["outgoing_before"],
                    net_after=incoming_after - outgoing_after,
                )
            )

    @staticmethod
    def _empty_position() -> dict[str, Decimal]:
        return {
            "incoming_before": Decimal(0),
            "outgoing_before": Decimal(0),
            "incoming_cleared": Decimal(0),
            "outgoing_cleared": Decimal(0),
        }

    @staticmethod
    def _statement_specs(cycle: ClearingCycle, result: object) -> list[dict[str, object]]:
        from cooperative_clearing.modules.clearing.domain.engine import ClearingResult

        if not isinstance(result, ClearingResult):
            raise clearing_error("CLEARING_RESULT_INVALID", 500)
        members = sorted(
            {item.debtor_member_id for item in result.entries}
            | {item.creditor_member_id for item in result.entries}
        )
        units = sorted({item.unit_id for item in result.entries})
        statements: list[dict[str, object]] = []
        for member_id in members:
            for unit_id in units:
                details = [
                    {
                        "obligation_id": item.obligation_id,
                        "direction": "OUTGOING"
                        if item.debtor_member_id == member_id
                        else "INCOMING",
                        "counterparty_member_id": item.creditor_member_id
                        if item.debtor_member_id == member_id
                        else item.debtor_member_id,
                        "amount_before": decimal_string(item.amount_before),
                        "cleared_amount": decimal_string(item.cleared_amount),
                        "amount_after": decimal_string(item.amount_after),
                        "exclusion_reason": item.exclusion_reason,
                    }
                    for item in result.entries
                    if item.unit_id == unit_id
                    and member_id in {item.debtor_member_id, item.creditor_member_id}
                ]
                if not details:
                    continue
                statements.append(
                    {
                        "schema_version": 1,
                        "cycle_id": str(cycle.id),
                        "cycle_code": cycle.cycle_code,
                        "member_id": member_id,
                        "unit_id": unit_id,
                        "input_hash": result.input_hash,
                        "result_hash": result.result_hash,
                        "entries": details,
                    }
                )
        return statements

    @staticmethod
    def _snapshot_input(snapshot: ClearingInputSnapshot) -> ClearingInput:
        raw_entries = snapshot.ordered_payload.get("entries")
        if not isinstance(raw_entries, list):
            raise clearing_error("SNAPSHOT_PAYLOAD_INVALID", 500)
        entries: list[ClearingInputEntry] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise clearing_error("SNAPSHOT_PAYLOAD_INVALID", 500)
            risk_limit = raw.get("risk_limit")
            entries.append(
                ClearingInputEntry(
                    obligation_id=str(raw["obligation_id"]),
                    debtor_member_id=str(raw["debtor_member_id"]),
                    creditor_member_id=str(raw["creditor_member_id"]),
                    unit_id=str(raw["unit_id"]),
                    amount=Decimal(str(raw["amount"])),
                    obligation_version=int(str(raw["obligation_version"])),
                    liquidity_class=str(raw["liquidity_class"]),
                    eligible=bool(raw["eligible"]),
                    exclusion_reason=str(raw["exclusion_reason"])
                    if raw.get("exclusion_reason") is not None
                    else None,
                    disputed=bool(raw["disputed"]),
                    frozen=bool(raw["frozen"]),
                    risk_limit=Decimal(str(risk_limit)) if risk_limit is not None else None,
                )
            )
        return ClearingInput(cycle_id=str(snapshot.cycle_id), entries=tuple(entries))

    @staticmethod
    def _policy_parameters(policy: ClearingPolicy) -> ClearingPolicyParameters:
        return ClearingPolicyParameters(
            policy_version=policy.policy_version,
            algorithm_id=policy.algorithm_id,
            algorithm_version=policy.algorithm_version,
            decimal_scale=policy.decimal_scale,
            rounding_mode=RoundingMode(policy.rounding_mode),
            minimum_operation=policy.minimum_operation,
            max_iterations=policy.max_iterations,
            max_cycle_length=policy.max_cycle_length,
            liquidity_order=tuple(policy.liquidity_order),
        ).validate()

    @staticmethod
    async def _active_policy(session: AsyncSession, policy_id: UUID) -> ClearingPolicy:
        policy = await session.get(ClearingPolicy, policy_id)
        if policy is None:
            raise clearing_error("POLICY_NOT_FOUND", 404)
        if policy.status != ClearingPolicyStatus.ACTIVE.value:
            raise clearing_error("POLICY_NOT_ACTIVE", 409)
        return policy

    @staticmethod
    async def _cycle(session: AsyncSession, cycle_id: UUID, *, lock: bool) -> ClearingCycle:
        cycle = await session.get(ClearingCycle, cycle_id, with_for_update=lock)
        if cycle is None:
            raise clearing_error("CLEARING_CYCLE_NOT_FOUND", 404)
        return cycle

    @staticmethod
    async def _snapshot(session: AsyncSession, cycle_id: UUID) -> ClearingInputSnapshot:
        snapshot = (
            await session.execute(
                select(ClearingInputSnapshot).where(ClearingInputSnapshot.cycle_id == cycle_id)
            )
        ).scalar_one_or_none()
        if snapshot is None:
            raise clearing_error("CLEARING_SNAPSHOT_NOT_FOUND", 409)
        return snapshot

    @staticmethod
    async def _lock_cooperative(session: AsyncSession, cooperative_id: UUID) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"cooperative-clearing:clearing:{cooperative_id}"},
        )

    @staticmethod
    def _version(current: int, expected: int) -> None:
        if current != expected:
            raise clearing_error("VERSION_CONFLICT", 409)

    @staticmethod
    def _text(value: str, code: str, maximum: int) -> str:
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > maximum:
            raise clearing_error(code)
        return normalized

    @classmethod
    def _code(cls, value: str, code: str, maximum: int) -> str:
        normalized = cls._text(value, code, maximum).upper()
        if not normalized.isascii() or not all(
            character.isalnum() or character in {"_", "-", "."} for character in normalized
        ):
            raise clearing_error(code)
        return normalized

    @staticmethod
    def _utc(value: datetime, code: str) -> datetime:
        if value.utcoffset() is None:
            raise clearing_error(code)
        return value.astimezone(UTC)

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
