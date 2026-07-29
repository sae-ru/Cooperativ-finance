"""Transactional crisis lifecycle with bounded authority and explicit responsibility."""

import zlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.crisis.application.common import (
    CrisisCommandResult,
    audit_crisis_action,
    begin_crisis_command,
    complete_crisis_command,
    crisis_command_assurance,
    crisis_role_actor,
    evidence_payload,
    link_evidence,
)
from cooperative_clearing.modules.crisis.domain.types import (
    CrisisCapability,
    CrisisType,
    EligibleMember,
    QualityStatus,
    RationFormula,
    allocate_rations,
    assess_reserve,
    crisis_error,
    normalize_code,
    quantity,
    ratio,
)
from cooperative_clearing.modules.crisis.infrastructure.models import (
    CrisisMandate,
    CrisisPaperForm,
    CrisisReport,
    CrisisReview,
    RationingAllocation,
    RationingPlan,
    RationingRule,
    RationIssuance,
    ReserveSnapshot,
    ReserveTarget,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import Membership
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.journal.application.service import SignedJournalService
from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.shared.core.config import Settings

OPERATOR_ROLES = {RoleCode.CRISIS_OPERATOR}
CONTROLLER_ROLES = {RoleCode.CRISIS_CONTROLLER}
REVIEW_ROLES = {RoleCode.CRISIS_CONTROLLER, RoleCode.AUDITOR}
SNAPSHOT_ROLES = {RoleCode.INVENTORY_CONTROLLER, RoleCode.CRISIS_CONTROLLER}


class CrisisService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.journal = SignedJournalService(settings)

    async def propose_reserve_target(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        resource_code: str,
        resource_name: str,
        unit_code: str,
        target_quantity: Decimal,
        critical_minimum: Decimal,
        warning_coverage_days: Decimal,
        critical_coverage_days: Decimal,
        max_snapshot_age_hours: int,
        terms: dict[str, object],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        code = normalize_code(resource_code)
        unit = normalize_code(unit_code, maximum=24)
        target = quantity(target_quantity)
        critical = quantity(critical_minimum, allow_zero=True)
        warning_days = quantity(warning_coverage_days, allow_zero=True)
        critical_days = quantity(critical_coverage_days, allow_zero=True)
        if (
            critical > target
            or critical_days > warning_days
            or not 1 <= max_snapshot_age_hours <= 720
        ):
            raise crisis_error("RESERVE_POLICY_INVALID", 422)
        command = {
            "cooperative_id": str(cooperative_id),
            "resource_code": code,
            "resource_name": self._text(resource_name, "RESOURCE_NAME_INVALID", 200),
            "unit_code": unit,
            "target_quantity": str(target),
            "critical_minimum": str(critical),
            "warning_coverage_days": str(warning_days),
            "critical_coverage_days": str(critical_days),
            "max_snapshot_age_hours": max_snapshot_age_hours,
            "terms": terms,
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_PROPOSE_RESERVE_TARGET", idempotency_key, command
        )
        if replay is not None:
            return replay
        actor = await crisis_role_actor(session, principal, cooperative_id, OPERATOR_ROLES)
        await self._lock_cooperative(session, cooperative_id)
        policy_version = (
            int(
                await session.scalar(
                    select(func.coalesce(func.max(ReserveTarget.policy_version), 0)).where(
                        ReserveTarget.cooperative_id == cooperative_id,
                        ReserveTarget.resource_code == code,
                    )
                )
                or 0
            )
            + 1
        )
        terms_payload = {**command, "policy_version": policy_version}
        terms_hash = payload_hash(terms_payload)
        target_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="crisis.reserve_target_proposed",
            aggregate_type="reserve_target",
            aggregate_id=target_id,
            aggregate_version=1,
            actor=actor,
            payload={**terms_payload, "target_id": str(target_id), "terms_hash": terms_hash},
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.reserve_target_proposed",
                subject_type="reserve_target",
                subject_id=target_id,
                command_record=record,
                amount=target,
                unit=unit,
            ),
        )
        session.add(
            ReserveTarget(
                id=target_id,
                cooperative_id=cooperative_id,
                resource_code=code,
                resource_name=str(command["resource_name"]),
                unit_code=unit,
                target_quantity=target,
                critical_minimum=critical,
                warning_coverage_days=warning_days,
                critical_coverage_days=critical_days,
                max_snapshot_age_hours=max_snapshot_age_hours,
                policy_version=policy_version,
                terms_payload=terms_payload,
                terms_hash=terms_hash,
                status="DRAFT",
                proposed_by_user_id=principal.user_id,
                proposed_by_member_id=actor.person_id,
                proposed_role_assignment_id=actor.role_assignment_id,
                proposed_event_id=event.event_id,
                version=1,
            )
        )
        await audit_crisis_action(
            session,
            principal,
            cooperative_id,
            "CRISIS_RESERVE_TARGET_PROPOSED",
            "ReserveTarget",
            target_id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, target_id)

    async def approve_reserve_target(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        target_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        target = await self._target(session, target_id, lock=True)
        payload = {
            "target_id": str(target_id),
            "expected_version": expected_version,
            "terms_hash": target.terms_hash,
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_APPROVE_RESERVE_TARGET", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(target.version, expected_version)
        if target.status != "DRAFT":
            raise crisis_error("RESERVE_TARGET_NOT_DRAFT")
        actor = await crisis_role_actor(session, principal, target.cooperative_id, CONTROLLER_ROLES)
        if actor.person_id == target.proposed_by_member_id:
            raise crisis_error("INDEPENDENT_APPROVER_REQUIRED")
        active_target = await session.scalar(
            select(ReserveTarget)
            .where(
                ReserveTarget.cooperative_id == target.cooperative_id,
                ReserveTarget.resource_code == target.resource_code,
                ReserveTarget.status == "ACTIVE",
                ReserveTarget.id != target.id,
            )
            .with_for_update()
        )
        if active_target is not None:
            active_rule = await session.scalar(
                select(RationingRule.id)
                .join(CrisisMandate, CrisisMandate.id == RationingRule.mandate_id)
                .where(
                    RationingRule.target_id == active_target.id,
                    RationingRule.status == "ACTIVE",
                    CrisisMandate.status == "ACTIVE",
                )
            )
            if active_rule is not None:
                raise crisis_error("RESERVE_POLICY_IN_USE")
            retirement_event = await self.journal.append(
                session,
                event_type="crisis.reserve_target_retired",
                aggregate_type="reserve_target",
                aggregate_id=active_target.id,
                aggregate_version=active_target.version + 1,
                actor=actor,
                payload={
                    "target_id": str(active_target.id),
                    "replaced_by_target_id": str(target.id),
                    "previous_terms_hash": active_target.terms_hash,
                    "replacement_terms_hash": target.terms_hash,
                },
                assurance=crisis_command_assurance(
                    principal=principal,
                    actor=actor,
                    event_type="crisis.reserve_target_retired",
                    subject_type="reserve_target",
                    subject_id=active_target.id,
                    command_record=record,
                    attester_member_ids=(active_target.proposed_by_member_id,),
                    amount=active_target.target_quantity,
                    unit=active_target.unit_code,
                ),
            )
            active_target.status = "RETIRED"
            active_target.version += 1
            await audit_crisis_action(
                session,
                principal,
                target.cooperative_id,
                "CRISIS_RESERVE_TARGET_RETIRED",
                "ReserveTarget",
                active_target.id,
                retirement_event.event_id,
                request_id,
            )
        event = await self.journal.append(
            session,
            event_type="crisis.reserve_target_approved",
            aggregate_type="reserve_target",
            aggregate_id=target.id,
            aggregate_version=target.version + 1,
            actor=actor,
            payload=payload,
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.reserve_target_approved",
                subject_type="reserve_target",
                subject_id=target.id,
                command_record=record,
                next_member_ids=(target.proposed_by_member_id,),
                attester_member_ids=(target.proposed_by_member_id,),
                amount=target.target_quantity,
                unit=target.unit_code,
            ),
        )
        target.status = "ACTIVE"
        target.approved_by_user_id = principal.user_id
        target.approved_by_member_id = actor.person_id
        target.approved_role_assignment_id = actor.role_assignment_id
        target.approved_event_id = event.event_id
        target.approved_at = datetime.now(UTC)
        target.version += 1
        await audit_crisis_action(
            session,
            principal,
            target.cooperative_id,
            "CRISIS_RESERVE_TARGET_APPROVED",
            "ReserveTarget",
            target.id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, target.id)

    async def record_reserve_snapshot(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        target_id: UUID,
        physical_verified_quantity: Decimal,
        committed_quantity: Decimal,
        consumption_rate_per_day: Decimal,
        expiring_quantity: Decimal,
        quality_status: QualityStatus,
        confidence: Decimal,
        observed_at: datetime,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        target = await self._target(session, target_id, lock=False)
        if target.status != "ACTIVE":
            raise crisis_error("ACTIVE_RESERVE_TARGET_REQUIRED")
        observed = self._utc(observed_at, "SNAPSHOT_TIME_INVALID")
        now = datetime.now(UTC)
        if observed > now + timedelta(minutes=5) or observed < now - timedelta(
            hours=target.max_snapshot_age_hours
        ):
            raise crisis_error("SNAPSHOT_TIME_INVALID", 422)
        assessment = assess_reserve(
            verified=physical_verified_quantity,
            committed=committed_quantity,
            consumption_per_day=consumption_rate_per_day,
            target=target.target_quantity,
            critical_minimum=target.critical_minimum,
            warning_coverage_days=target.warning_coverage_days,
            critical_coverage_days=target.critical_coverage_days,
            confidence=confidence,
            quality_status=quality_status,
        )
        expiring = quantity(expiring_quantity, allow_zero=True)
        if expiring > assessment.verified:
            raise crisis_error("EXPIRING_QUANTITY_INVALID", 422)
        command = {
            "target_id": str(target.id),
            "physical_verified_quantity": str(assessment.verified),
            "committed_quantity": str(assessment.committed),
            "available_quantity": str(assessment.available),
            "consumption_rate_per_day": str(assessment.consumption_per_day),
            "coverage_days": str(assessment.coverage_days)
            if assessment.coverage_days is not None
            else None,
            "expiring_quantity": str(expiring),
            "quality_status": quality_status.value,
            "confidence": str(ratio(confidence)),
            "reserve_level": assessment.level.value,
            "observed_at": observed.isoformat(),
            "evidence_ids": [str(item) for item in evidence_ids],
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_RECORD_RESERVE_SNAPSHOT", idempotency_key, command
        )
        if replay is not None:
            return replay
        actor = await crisis_role_actor(session, principal, target.cooperative_id, SNAPSHOT_ROLES)
        evidence = await EvidenceService.require_ready(
            session, target.cooperative_id, evidence_ids, required=True
        )
        snapshot_id = uuid4()
        signed_payload = {
            **command,
            "snapshot_id": str(snapshot_id),
            "evidence": evidence_payload(evidence),
            "target_terms_hash": target.terms_hash,
        }
        snapshot_hash = payload_hash(signed_payload)
        event = await self.journal.append(
            session,
            event_type="crisis.reserve_snapshot_recorded",
            aggregate_type="reserve_snapshot",
            aggregate_id=snapshot_id,
            aggregate_version=1,
            actor=actor,
            payload={**signed_payload, "snapshot_hash": snapshot_hash},
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.reserve_snapshot_recorded",
                subject_type="reserve_snapshot",
                subject_id=snapshot_id,
                command_record=record,
                evidence_refs=evidence_payload(evidence),
                amount=assessment.verified if assessment.verified > 0 else None,
                unit=target.unit_code if assessment.verified > 0 else None,
            ),
        )
        session.add(
            ReserveSnapshot(
                id=snapshot_id,
                target_id=target.id,
                physical_verified_quantity=assessment.verified,
                committed_quantity=assessment.committed,
                available_quantity=assessment.available,
                consumption_rate_per_day=assessment.consumption_per_day,
                coverage_days=assessment.coverage_days,
                expiring_quantity=expiring,
                quality_status=quality_status.value,
                confidence=ratio(confidence),
                reserve_level=assessment.level.value,
                observed_at=observed,
                evidence_ids=[str(item.id) for item in evidence],
                snapshot_hash=snapshot_hash,
                recorded_by_user_id=principal.user_id,
                recorded_by_member_id=actor.person_id,
                recorded_role_assignment_id=actor.role_assignment_id,
                recorded_event_id=event.event_id,
            )
        )
        link_evidence(session, evidence, event.event_id, "ReserveSnapshot", snapshot_id)
        await audit_crisis_action(
            session,
            principal,
            target.cooperative_id,
            "CRISIS_RESERVE_SNAPSHOT_RECORDED",
            "ReserveSnapshot",
            snapshot_id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, snapshot_id)

    async def propose_mandate(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        mandate_code: str,
        crisis_type: CrisisType,
        scope_payload: dict[str, object],
        capabilities: Sequence[CrisisCapability],
        evidence_ids: Sequence[UUID],
        rationale: str,
        exit_criteria: str,
        safe_state: str,
        starts_at: datetime,
        review_at: datetime,
        expires_at: datetime,
        maximum_end_at: datetime,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        code = normalize_code(mandate_code)
        start = self._utc(starts_at, "MANDATE_PERIOD_INVALID")
        review = self._utc(review_at, "MANDATE_PERIOD_INVALID")
        expiry = self._utc(expires_at, "MANDATE_PERIOD_INVALID")
        maximum_end = self._utc(maximum_end_at, "MANDATE_PERIOD_INVALID")
        now = datetime.now(UTC)
        if (
            start < now - timedelta(minutes=5)
            or not start < review <= expiry <= maximum_end
            or maximum_end - start > timedelta(days=90)
        ):
            raise crisis_error("MANDATE_PERIOD_INVALID", 422)
        capability_values = sorted({item.value for item in capabilities})
        if not capability_values:
            raise crisis_error("MANDATE_CAPABILITY_REQUIRED", 422)
        command = {
            "cooperative_id": str(cooperative_id),
            "mandate_code": code,
            "crisis_type": crisis_type.value,
            "scope_payload": scope_payload,
            "capabilities": capability_values,
            "evidence_ids": [str(item) for item in evidence_ids],
            "rationale": self._text(rationale, "MANDATE_RATIONALE_INVALID", 5_000),
            "exit_criteria": self._text(exit_criteria, "MANDATE_EXIT_CRITERIA_INVALID", 5_000),
            "safe_state": self._text(safe_state, "MANDATE_SAFE_STATE_INVALID", 5_000),
            "starts_at": start.isoformat(),
            "review_at": review.isoformat(),
            "expires_at": expiry.isoformat(),
            "maximum_end_at": maximum_end.isoformat(),
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_PROPOSE_MANDATE", idempotency_key, command
        )
        if replay is not None:
            return replay
        actor = await crisis_role_actor(session, principal, cooperative_id, OPERATOR_ROLES)
        evidence = await EvidenceService.require_ready(
            session, cooperative_id, evidence_ids, required=True
        )
        await self._lock_cooperative(session, cooperative_id)
        if await session.scalar(
            select(CrisisMandate.id).where(
                CrisisMandate.cooperative_id == cooperative_id, CrisisMandate.mandate_code == code
            )
        ):
            raise crisis_error("MANDATE_CODE_EXISTS")
        policy_version = (
            int(
                await session.scalar(
                    select(func.coalesce(func.max(CrisisMandate.policy_version), 0)).where(
                        CrisisMandate.cooperative_id == cooperative_id
                    )
                )
                or 0
            )
            + 1
        )
        terms_payload = {
            **command,
            "policy_version": policy_version,
            "evidence": evidence_payload(evidence),
            "bounded_authority": True,
        }
        terms_hash = payload_hash(terms_payload)
        mandate_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="crisis.mandate_proposed",
            aggregate_type="crisis_mandate",
            aggregate_id=mandate_id,
            aggregate_version=1,
            actor=actor,
            payload={**terms_payload, "mandate_id": str(mandate_id), "terms_hash": terms_hash},
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.mandate_proposed",
                subject_type="crisis_mandate",
                subject_id=mandate_id,
                command_record=record,
                evidence_refs=evidence_payload(evidence),
            ),
        )
        session.add(
            CrisisMandate(
                id=mandate_id,
                cooperative_id=cooperative_id,
                mandate_code=code,
                crisis_type=crisis_type.value,
                scope_payload=scope_payload,
                capabilities=capability_values,
                evidence_ids=[str(item.id) for item in evidence],
                rationale=str(command["rationale"]),
                exit_criteria=str(command["exit_criteria"]),
                safe_state=str(command["safe_state"]),
                policy_version=policy_version,
                starts_at=start,
                review_at=review,
                expires_at=expiry,
                maximum_end_at=maximum_end,
                terms_payload=terms_payload,
                terms_hash=terms_hash,
                status="DRAFT",
                proposed_by_user_id=principal.user_id,
                proposed_by_member_id=actor.person_id,
                proposed_role_assignment_id=actor.role_assignment_id,
                proposed_event_id=event.event_id,
                version=1,
            )
        )
        link_evidence(session, evidence, event.event_id, "CrisisMandate", mandate_id)
        await audit_crisis_action(
            session,
            principal,
            cooperative_id,
            "CRISIS_MANDATE_PROPOSED",
            "CrisisMandate",
            mandate_id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, mandate_id)

    async def activate_mandate(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        mandate_id: UUID,
        expected_version: int,
        terms_hash: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        mandate = await self._mandate(session, mandate_id, lock=True)
        payload = {
            "mandate_id": str(mandate.id),
            "expected_version": expected_version,
            "terms_hash": terms_hash,
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_ACTIVATE_MANDATE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(mandate.version, expected_version)
        if mandate.status != "DRAFT" or mandate.terms_hash != terms_hash:
            raise crisis_error("MANDATE_ACTIVATION_INVALID")
        now = datetime.now(UTC)
        if now >= mandate.expires_at:
            raise crisis_error("MANDATE_EXPIRED")
        actor = await crisis_role_actor(
            session, principal, mandate.cooperative_id, CONTROLLER_ROLES
        )
        if actor.person_id == mandate.proposed_by_member_id:
            raise crisis_error("INDEPENDENT_APPROVER_REQUIRED")
        await self._lock_cooperative(session, mandate.cooperative_id)
        if await session.scalar(
            select(CrisisMandate.id).where(
                CrisisMandate.cooperative_id == mandate.cooperative_id,
                CrisisMandate.status == "ACTIVE",
                CrisisMandate.id != mandate.id,
            )
        ):
            raise crisis_error("ACTIVE_MANDATE_EXISTS")
        event = await self.journal.append(
            session,
            event_type="crisis.mandate_activated",
            aggregate_type="crisis_mandate",
            aggregate_id=mandate.id,
            aggregate_version=mandate.version + 1,
            actor=actor,
            payload={
                **payload,
                "capabilities": mandate.capabilities,
                "expires_at": mandate.expires_at.isoformat(),
                "safe_state": mandate.safe_state,
            },
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.mandate_activated",
                subject_type="crisis_mandate",
                subject_id=mandate.id,
                command_record=record,
                next_member_ids=(mandate.proposed_by_member_id,),
                attester_member_ids=(mandate.proposed_by_member_id,),
            ),
        )
        mandate.status = "ACTIVE"
        mandate.activated_by_user_id = principal.user_id
        mandate.activated_by_member_id = actor.person_id
        mandate.activated_role_assignment_id = actor.role_assignment_id
        mandate.activated_event_id = event.event_id
        mandate.activated_at = now
        mandate.version += 1
        await audit_crisis_action(
            session,
            principal,
            mandate.cooperative_id,
            "CRISIS_MANDATE_ACTIVATED",
            "CrisisMandate",
            mandate.id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, mandate.id)

    async def review_mandate(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        mandate_id: UUID,
        expected_version: int,
        decision: str,
        facts_payload: dict[str, object],
        rationale: str,
        new_review_at: datetime | None,
        new_expires_at: datetime | None,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        mandate = await self._mandate(session, mandate_id, lock=True)
        normalized_decision = decision.strip().upper()
        if normalized_decision not in {"CONTINUE", "EXTEND"}:
            raise crisis_error("REVIEW_DECISION_INVALID", 422)
        review = (
            self._utc(new_review_at, "REVIEW_PERIOD_INVALID")
            if new_review_at
            else mandate.review_at
        )
        expiry = (
            self._utc(new_expires_at, "REVIEW_PERIOD_INVALID")
            if new_expires_at
            else mandate.expires_at
        )
        payload = {
            "mandate_id": str(mandate.id),
            "expected_version": expected_version,
            "decision": normalized_decision,
            "facts_payload": facts_payload,
            "rationale": self._text(rationale, "REVIEW_RATIONALE_INVALID", 5_000),
            "new_review_at": review.isoformat(),
            "new_expires_at": expiry.isoformat(),
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_REVIEW_MANDATE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(mandate.version, expected_version)
        self._require_effective_mandate(mandate)
        actor = await crisis_role_actor(session, principal, mandate.cooperative_id, REVIEW_ROLES)
        if actor.person_id in {mandate.proposed_by_member_id, mandate.activated_by_member_id}:
            raise crisis_error("INDEPENDENT_REVIEWER_REQUIRED")
        now = datetime.now(UTC)
        if normalized_decision == "CONTINUE":
            if expiry != mandate.expires_at or review <= now or review >= expiry:
                raise crisis_error("REVIEW_PERIOD_INVALID", 422)
        elif (
            review <= now
            or review >= expiry
            or expiry <= mandate.expires_at
            or expiry > mandate.maximum_end_at
        ):
            raise crisis_error("REVIEW_PERIOD_INVALID", 422)
        round_number = (
            int(
                await session.scalar(
                    select(func.coalesce(func.max(CrisisReview.decision_round), 0)).where(
                        CrisisReview.mandate_id == mandate.id
                    )
                )
                or 0
            )
            + 1
        )
        review_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="crisis.mandate_reviewed",
            aggregate_type="crisis_mandate",
            aggregate_id=mandate.id,
            aggregate_version=mandate.version + 1,
            actor=actor,
            payload={
                **payload,
                "review_id": str(review_id),
                "decision_round": round_number,
                "previous_review_at": mandate.review_at.isoformat(),
                "previous_expires_at": mandate.expires_at.isoformat(),
            },
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.mandate_reviewed",
                subject_type="crisis_mandate",
                subject_id=mandate.id,
                command_record=record,
                next_member_ids=(mandate.proposed_by_member_id, mandate.activated_by_member_id),
                attester_member_ids=(mandate.proposed_by_member_id, mandate.activated_by_member_id),
            ),
        )
        session.add(
            CrisisReview(
                id=review_id,
                mandate_id=mandate.id,
                decision_round=round_number,
                decision=normalized_decision,
                facts_payload=facts_payload,
                rationale=str(payload["rationale"]),
                previous_review_at=mandate.review_at,
                previous_expires_at=mandate.expires_at,
                new_review_at=review,
                new_expires_at=expiry,
                reviewer_user_id=principal.user_id,
                reviewer_member_id=actor.person_id,
                reviewer_role_assignment_id=actor.role_assignment_id,
                event_id=event.event_id,
            )
        )
        mandate.review_at = review
        mandate.expires_at = expiry
        mandate.version += 1
        await audit_crisis_action(
            session,
            principal,
            mandate.cooperative_id,
            "CRISIS_MANDATE_REVIEWED",
            "CrisisMandate",
            mandate.id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, review_id)

    async def propose_rationing_rule(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        mandate_id: UUID,
        target_id: UUID,
        formula: RationFormula,
        eligibility_policy: dict[str, object],
        protected_minimum: Decimal,
        maximum_per_member: Decimal,
        period_hours: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        mandate = await self._mandate(session, mandate_id, lock=False)
        self._require_effective_mandate(mandate, CrisisCapability.ENABLE_RATIONING)
        target = await self._target(session, target_id, lock=False)
        if target.cooperative_id != mandate.cooperative_id or target.status != "ACTIVE":
            raise crisis_error("ACTIVE_RESERVE_TARGET_REQUIRED")
        protected = quantity(protected_minimum, allow_zero=True)
        maximum = quantity(maximum_per_member)
        if protected > maximum or not 1 <= period_hours <= 720:
            raise crisis_error("RATION_POLICY_INVALID", 422)
        command = {
            "mandate_id": str(mandate.id),
            "target_id": str(target.id),
            "formula": formula.value,
            "eligibility_policy": eligibility_policy,
            "protected_minimum": str(protected),
            "maximum_per_member": str(maximum),
            "period_hours": period_hours,
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_PROPOSE_RATIONING_RULE", idempotency_key, command
        )
        if replay is not None:
            return replay
        actor = await crisis_role_actor(session, principal, mandate.cooperative_id, OPERATOR_ROLES)
        policy_version = (
            int(
                await session.scalar(
                    select(func.coalesce(func.max(RationingRule.policy_version), 0)).where(
                        RationingRule.mandate_id == mandate.id, RationingRule.target_id == target.id
                    )
                )
                or 0
            )
            + 1
        )
        terms_payload = {**command, "policy_version": policy_version, "creates_debt": False}
        terms_hash = payload_hash(terms_payload)
        rule_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="crisis.rationing_rule_proposed",
            aggregate_type="rationing_rule",
            aggregate_id=rule_id,
            aggregate_version=1,
            actor=actor,
            payload={**terms_payload, "rule_id": str(rule_id), "terms_hash": terms_hash},
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.rationing_rule_proposed",
                subject_type="rationing_rule",
                subject_id=rule_id,
                command_record=record,
                amount=maximum,
                unit=target.unit_code,
            ),
        )
        session.add(
            RationingRule(
                id=rule_id,
                mandate_id=mandate.id,
                target_id=target.id,
                policy_version=policy_version,
                formula=formula.value,
                eligibility_policy=eligibility_policy,
                protected_minimum=protected,
                maximum_per_member=maximum,
                period_hours=period_hours,
                terms_payload=terms_payload,
                terms_hash=terms_hash,
                status="DRAFT",
                proposed_by_user_id=principal.user_id,
                proposed_by_member_id=actor.person_id,
                proposed_role_assignment_id=actor.role_assignment_id,
                proposed_event_id=event.event_id,
                version=1,
            )
        )
        await audit_crisis_action(
            session,
            principal,
            mandate.cooperative_id,
            "CRISIS_RATIONING_RULE_PROPOSED",
            "RationingRule",
            rule_id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, rule_id)

    async def approve_rationing_rule(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        rule_id: UUID,
        expected_version: int,
        terms_hash: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        rule = await self._rule(session, rule_id, lock=True)
        mandate = await self._mandate(session, rule.mandate_id, lock=False)
        self._require_effective_mandate(mandate, CrisisCapability.ENABLE_RATIONING)
        payload = {
            "rule_id": str(rule.id),
            "expected_version": expected_version,
            "terms_hash": terms_hash,
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_APPROVE_RATIONING_RULE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(rule.version, expected_version)
        if rule.status != "DRAFT" or rule.terms_hash != terms_hash:
            raise crisis_error("RATIONING_RULE_APPROVAL_INVALID")
        actor = await crisis_role_actor(
            session, principal, mandate.cooperative_id, CONTROLLER_ROLES
        )
        if actor.person_id == rule.proposed_by_member_id:
            raise crisis_error("INDEPENDENT_APPROVER_REQUIRED")
        active_rule = await session.scalar(
            select(RationingRule)
            .where(
                RationingRule.mandate_id == rule.mandate_id,
                RationingRule.target_id == rule.target_id,
                RationingRule.status == "ACTIVE",
                RationingRule.id != rule.id,
            )
            .with_for_update()
        )
        if active_rule is not None:
            open_allocations = int(
                await session.scalar(
                    select(func.count())
                    .select_from(RationingAllocation)
                    .join(RationingPlan, RationingPlan.id == RationingAllocation.plan_id)
                    .where(
                        RationingPlan.rule_id == active_rule.id,
                        RationingAllocation.status.in_({"PROPOSED", "RESERVED"}),
                    )
                )
                or 0
            )
            if open_allocations:
                raise crisis_error("RATIONING_RULE_IN_USE")
            retirement_event = await self.journal.append(
                session,
                event_type="crisis.rationing_rule_retired",
                aggregate_type="rationing_rule",
                aggregate_id=active_rule.id,
                aggregate_version=active_rule.version + 1,
                actor=actor,
                payload={
                    "rule_id": str(active_rule.id),
                    "replaced_by_rule_id": str(rule.id),
                    "previous_terms_hash": active_rule.terms_hash,
                    "replacement_terms_hash": rule.terms_hash,
                },
                assurance=crisis_command_assurance(
                    principal=principal,
                    actor=actor,
                    event_type="crisis.rationing_rule_retired",
                    subject_type="rationing_rule",
                    subject_id=active_rule.id,
                    command_record=record,
                    attester_member_ids=(active_rule.proposed_by_member_id,),
                ),
            )
            active_rule.status = "RETIRED"
            active_rule.version += 1
            await audit_crisis_action(
                session,
                principal,
                mandate.cooperative_id,
                "CRISIS_RATIONING_RULE_RETIRED",
                "RationingRule",
                active_rule.id,
                retirement_event.event_id,
                request_id,
            )
        event = await self.journal.append(
            session,
            event_type="crisis.rationing_rule_approved",
            aggregate_type="rationing_rule",
            aggregate_id=rule.id,
            aggregate_version=rule.version + 1,
            actor=actor,
            payload=payload,
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.rationing_rule_approved",
                subject_type="rationing_rule",
                subject_id=rule.id,
                command_record=record,
                next_member_ids=(rule.proposed_by_member_id,),
                attester_member_ids=(rule.proposed_by_member_id,),
            ),
        )
        rule.status = "ACTIVE"
        rule.approved_by_user_id = principal.user_id
        rule.approved_by_member_id = actor.person_id
        rule.approved_role_assignment_id = actor.role_assignment_id
        rule.approved_event_id = event.event_id
        rule.approved_at = datetime.now(UTC)
        rule.version += 1
        await audit_crisis_action(
            session,
            principal,
            mandate.cooperative_id,
            "CRISIS_RATIONING_RULE_APPROVED",
            "RationingRule",
            rule.id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, rule.id)

    async def preview_rationing_plan(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        rule_id: UUID,
        eligible_members: Sequence[tuple[UUID, int]],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        rule = await self._rule(session, rule_id, lock=False)
        mandate = await self._mandate(session, rule.mandate_id, lock=False)
        self._require_effective_mandate(mandate, CrisisCapability.ENABLE_RATIONING)
        if rule.status != "ACTIVE":
            raise crisis_error("ACTIVE_RATIONING_RULE_REQUIRED")
        command = {
            "rule_id": str(rule.id),
            "eligible_members": [
                {"member_id": str(member_id), "weight": weight}
                for member_id, weight in eligible_members
            ],
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_PREVIEW_RATIONING_PLAN", idempotency_key, command
        )
        if replay is not None:
            return replay
        actor = await crisis_role_actor(session, principal, mandate.cooperative_id, OPERATOR_ROLES)
        await self._lock_cooperative(session, mandate.cooperative_id)
        if not eligible_members or len(eligible_members) > 10_000:
            raise crisis_error("ELIGIBLE_MEMBERS_INVALID", 422)
        member_ids = [item[0] for item in eligible_members]
        active_count = int(
            await session.scalar(
                select(func.count())
                .select_from(Membership)
                .where(
                    Membership.cooperative_id == mandate.cooperative_id,
                    Membership.member_id.in_(member_ids),
                    Membership.status == "ACTIVE",
                )
            )
            or 0
        )
        if active_count != len(set(member_ids)):
            raise crisis_error("ACTIVE_ELIGIBLE_MEMBERS_REQUIRED", 422)
        snapshot = await self._latest_snapshot(session, rule.target_id)
        target = await self._target(session, rule.target_id, lock=False)
        self._require_fresh_snapshot(snapshot, target)
        reserved = await self._reserved_total(session, rule.target_id)
        available = snapshot.available_quantity - reserved
        if available < 0:
            raise crisis_error("RESERVE_OVERCOMMITTED")
        shares = allocate_rations(
            eligible=[
                EligibleMember(str(member_id), weight) for member_id, weight in eligible_members
            ],
            available=available,
            protected_minimum=rule.protected_minimum,
            maximum_per_member=rule.maximum_per_member,
            formula=RationFormula(rule.formula),
        )
        allocations_payload = [
            {"member_id": item.member_id, "weight": item.weight, "quantity": str(item.quantity)}
            for item in shares
        ]
        input_payload = {
            **command,
            "snapshot_id": str(snapshot.id),
            "snapshot_hash": snapshot.snapshot_hash,
            "available_input": str(available),
            "rule_terms_hash": rule.terms_hash,
        }
        input_hash = payload_hash(input_payload)
        allocations_hash = payload_hash(allocations_payload)
        plan_id = uuid4()
        expires_at = min(datetime.now(UTC) + timedelta(hours=rule.period_hours), mandate.expires_at)
        event = await self.journal.append(
            session,
            event_type="crisis.rationing_previewed",
            aggregate_type="rationing_plan",
            aggregate_id=plan_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **input_payload,
                "plan_id": str(plan_id),
                "input_hash": input_hash,
                "allocations_hash": allocations_hash,
                "eligible_count": len(shares),
                "total_allocated": str(sum((item.quantity for item in shares), Decimal(0))),
                "expires_at": expires_at.isoformat(),
            },
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.rationing_previewed",
                subject_type="rationing_plan",
                subject_id=plan_id,
                command_record=record,
                amount=sum((item.quantity for item in shares), Decimal(0)),
                unit=target.unit_code,
            ),
        )
        session.add(
            RationingPlan(
                id=plan_id,
                rule_id=rule.id,
                snapshot_id=snapshot.id,
                available_input=available,
                eligible_count=len(shares),
                total_allocated=sum((item.quantity for item in shares), Decimal(0)),
                eligibility_snapshot=command["eligible_members"],
                input_hash=input_hash,
                allocations_hash=allocations_hash,
                status="PREVIEWED",
                expires_at=expires_at,
                proposed_by_user_id=principal.user_id,
                proposed_by_member_id=actor.person_id,
                proposed_role_assignment_id=actor.role_assignment_id,
                preview_event_id=event.event_id,
                version=1,
            )
        )
        await session.flush()
        session.add_all(
            [
                RationingAllocation(
                    id=uuid4(),
                    plan_id=plan_id,
                    member_id=UUID(item.member_id),
                    weight=item.weight,
                    quantity=item.quantity,
                    status="PROPOSED",
                )
                for item in shares
            ]
        )
        await audit_crisis_action(
            session,
            principal,
            mandate.cooperative_id,
            "CRISIS_RATIONING_PREVIEWED",
            "RationingPlan",
            plan_id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, plan_id)

    async def confirm_rationing_plan(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        plan_id: UUID,
        expected_version: int,
        allocations_hash: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        plan = await self._plan(session, plan_id, lock=True)
        rule = await self._rule(session, plan.rule_id, lock=False)
        mandate = await self._mandate(session, rule.mandate_id, lock=False)
        self._require_effective_mandate(mandate, CrisisCapability.ENABLE_RATIONING)
        payload = {
            "plan_id": str(plan.id),
            "expected_version": expected_version,
            "allocations_hash": allocations_hash,
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_CONFIRM_RATIONING_PLAN", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(plan.version, expected_version)
        if (
            plan.status != "PREVIEWED"
            or plan.allocations_hash != allocations_hash
            or datetime.now(UTC) >= plan.expires_at
        ):
            raise crisis_error("RATIONING_CONFIRMATION_INVALID")
        actor = await crisis_role_actor(
            session, principal, mandate.cooperative_id, CONTROLLER_ROLES
        )
        if actor.person_id == plan.proposed_by_member_id:
            raise crisis_error("INDEPENDENT_APPROVER_REQUIRED")
        await self._lock_cooperative(session, mandate.cooperative_id)
        snapshot = await self._latest_snapshot(session, rule.target_id)
        target = await self._target(session, rule.target_id, lock=False)
        self._require_fresh_snapshot(snapshot, target)
        available = snapshot.available_quantity - await self._reserved_total(
            session, rule.target_id
        )
        if snapshot.id != plan.snapshot_id or plan.total_allocated > available:
            raise crisis_error("RATIONING_INPUT_STALE")
        event = await self.journal.append(
            session,
            event_type="crisis.rationing_confirmed",
            aggregate_type="rationing_plan",
            aggregate_id=plan.id,
            aggregate_version=plan.version + 1,
            actor=actor,
            payload={
                **payload,
                "input_hash": plan.input_hash,
                "total_allocated": str(plan.total_allocated),
                "creates_debt": False,
            },
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.rationing_confirmed",
                subject_type="rationing_plan",
                subject_id=plan.id,
                command_record=record,
                next_member_ids=(plan.proposed_by_member_id,),
                attester_member_ids=(plan.proposed_by_member_id,),
                amount=plan.total_allocated,
                unit=target.unit_code,
            ),
        )
        plan.status = "CONFIRMED"
        plan.confirmed_by_user_id = principal.user_id
        plan.confirmed_by_member_id = actor.person_id
        plan.confirmed_role_assignment_id = actor.role_assignment_id
        plan.confirmed_event_id = event.event_id
        plan.confirmed_at = datetime.now(UTC)
        plan.version += 1
        await session.execute(
            update(RationingAllocation)
            .where(RationingAllocation.plan_id == plan.id, RationingAllocation.status == "PROPOSED")
            .values(status="RESERVED")
        )
        await audit_crisis_action(
            session,
            principal,
            mandate.cooperative_id,
            "CRISIS_RATIONING_CONFIRMED",
            "RationingPlan",
            plan.id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, plan.id)

    async def cancel_rationing_plan(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        plan_id: UUID,
        expected_version: int,
        rationale: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        plan = await self._plan(session, plan_id, lock=True)
        rule = await self._rule(session, plan.rule_id, lock=False)
        mandate = await self._mandate(session, rule.mandate_id, lock=False)
        target = await self._target(session, rule.target_id, lock=False)
        payload = {
            "plan_id": str(plan.id),
            "expected_version": expected_version,
            "rationale": self._text(rationale, "RATIONING_CANCEL_RATIONALE_INVALID", 5_000),
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_CANCEL_RATIONING_PLAN", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(plan.version, expected_version)
        if plan.status not in {"PREVIEWED", "CONFIRMED"}:
            raise crisis_error("RATIONING_PLAN_NOT_CANCELLABLE")
        actor = await crisis_role_actor(
            session, principal, mandate.cooperative_id, CONTROLLER_ROLES
        )
        if actor.person_id == plan.proposed_by_member_id:
            raise crisis_error("INDEPENDENT_APPROVER_REQUIRED")
        issued = int(
            await session.scalar(
                select(func.count())
                .select_from(RationingAllocation)
                .where(
                    RationingAllocation.plan_id == plan.id,
                    RationingAllocation.status == "ISSUED",
                )
            )
            or 0
        )
        if issued:
            raise crisis_error("ISSUED_RATION_CANNOT_BE_CANCELLED")
        event = await self.journal.append(
            session,
            event_type="crisis.rationing_cancelled",
            aggregate_type="rationing_plan",
            aggregate_id=plan.id,
            aggregate_version=plan.version + 1,
            actor=actor,
            payload={**payload, "allocations_hash": plan.allocations_hash},
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.rationing_cancelled",
                subject_type="rationing_plan",
                subject_id=plan.id,
                command_record=record,
                attester_member_ids=(plan.proposed_by_member_id,),
                amount=plan.total_allocated,
                unit=target.unit_code,
            ),
        )
        await session.execute(
            update(RationingAllocation)
            .where(
                RationingAllocation.plan_id == plan.id,
                RationingAllocation.status.in_({"PROPOSED", "RESERVED"}),
            )
            .values(status="CANCELLED")
        )
        plan.status = "CANCELLED"
        plan.version += 1
        await audit_crisis_action(
            session,
            principal,
            mandate.cooperative_id,
            "CRISIS_RATIONING_CANCELLED",
            "RationingPlan",
            plan.id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, plan.id)

    async def issue_ration(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        allocation_id: UUID,
        acknowledgement: str,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        allocation = await self._allocation(session, allocation_id, lock=True)
        plan = await self._plan(session, allocation.plan_id, lock=False)
        rule = await self._rule(session, plan.rule_id, lock=False)
        mandate = await self._mandate(session, rule.mandate_id, lock=False)
        target = await self._target(session, rule.target_id, lock=False)
        self._require_effective_mandate(mandate, CrisisCapability.ENABLE_RATIONING)
        command = {
            "allocation_id": str(allocation.id),
            "quantity": str(allocation.quantity),
            "acknowledgement": self._text(acknowledgement, "RATION_ACKNOWLEDGEMENT_INVALID", 5_000),
            "evidence_ids": [str(item) for item in evidence_ids],
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_ISSUE_RATION", idempotency_key, command
        )
        if replay is not None:
            return replay
        if allocation.status != "RESERVED" or allocation.quantity <= 0:
            raise crisis_error("RESERVED_ALLOCATION_REQUIRED")
        actor = await crisis_role_actor(session, principal, mandate.cooperative_id, OPERATOR_ROLES)
        if actor.person_id in {allocation.member_id, plan.confirmed_by_member_id}:
            raise crisis_error("INDEPENDENT_ISSUER_REQUIRED")
        evidence = await EvidenceService.require_ready(
            session, mandate.cooperative_id, evidence_ids, required=True
        )
        issuance_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="crisis.ration_issued",
            aggregate_type="rationing_allocation",
            aggregate_id=allocation.id,
            aggregate_version=2,
            actor=actor,
            payload={
                **command,
                "issuance_id": str(issuance_id),
                "recipient_member_id": str(allocation.member_id),
                "evidence": evidence_payload(evidence),
                "creates_debt": False,
            },
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.ration_issued",
                subject_type="rationing_allocation",
                subject_id=allocation.id,
                command_record=record,
                evidence_refs=evidence_payload(evidence),
                next_member_ids=(allocation.member_id,),
                attester_member_ids=(plan.confirmed_by_member_id,),
                amount=allocation.quantity,
                unit=target.unit_code,
            ),
        )
        session.add(
            RationIssuance(
                id=issuance_id,
                allocation_id=allocation.id,
                quantity=allocation.quantity,
                acknowledgement=str(command["acknowledgement"]),
                evidence_ids=[str(item.id) for item in evidence],
                issued_by_user_id=principal.user_id,
                issued_by_member_id=actor.person_id,
                issued_role_assignment_id=actor.role_assignment_id,
                event_id=event.event_id,
            )
        )
        allocation.status = "ISSUED"
        allocation.issued_event_id = event.event_id
        allocation.issued_at = datetime.now(UTC)
        link_evidence(session, evidence, event.event_id, "RationIssuance", issuance_id)
        await audit_crisis_action(
            session,
            principal,
            mandate.cooperative_id,
            "CRISIS_RATION_ISSUED",
            "RationingAllocation",
            allocation.id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, issuance_id)

    async def issue_paper_form(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        mandate_id: UUID,
        serial_number: str,
        form_type: str,
        assigned_to_member_id: UUID,
        expires_at: datetime,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        mandate = await self._mandate(session, mandate_id, lock=False)
        self._require_effective_mandate(mandate, CrisisCapability.ENABLE_PAPER_FORMS)
        serial = normalize_code(serial_number)
        normalized_type = form_type.strip().upper()
        if normalized_type not in {"RESERVE_SNAPSHOT", "RATION_ISSUANCE", "INCIDENT", "EXCEPTION"}:
            raise crisis_error("PAPER_FORM_TYPE_INVALID", 422)
        expiry = self._utc(expires_at, "PAPER_FORM_EXPIRY_INVALID")
        now = datetime.now(UTC)
        if expiry <= now or expiry > mandate.expires_at:
            raise crisis_error("PAPER_FORM_EXPIRY_INVALID", 422)
        command = {
            "mandate_id": str(mandate.id),
            "serial_number": serial,
            "form_type": normalized_type,
            "assigned_to_member_id": str(assigned_to_member_id),
            "expires_at": expiry.isoformat(),
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_ISSUE_PAPER_FORM", idempotency_key, command
        )
        if replay is not None:
            return replay
        actor = await crisis_role_actor(session, principal, mandate.cooperative_id, OPERATOR_ROLES)
        if not await session.scalar(
            select(Membership.id).where(
                Membership.cooperative_id == mandate.cooperative_id,
                Membership.member_id == assigned_to_member_id,
                Membership.status == "ACTIVE",
            )
        ):
            raise crisis_error("ACTIVE_ASSIGNEE_REQUIRED", 422)
        checksum = f"{zlib.crc32(f'{mandate.cooperative_id}:{serial}'.encode()):08X}"
        form_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="crisis.paper_form_issued",
            aggregate_type="crisis_paper_form",
            aggregate_id=form_id,
            aggregate_version=1,
            actor=actor,
            payload={**command, "form_id": str(form_id), "checksum": checksum},
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.paper_form_issued",
                subject_type="crisis_paper_form",
                subject_id=form_id,
                command_record=record,
                next_member_ids=(assigned_to_member_id,),
            ),
        )
        session.add(
            CrisisPaperForm(
                id=form_id,
                cooperative_id=mandate.cooperative_id,
                mandate_id=mandate.id,
                serial_number=serial,
                checksum=checksum,
                form_type=normalized_type,
                assigned_to_member_id=assigned_to_member_id,
                status="ISSUED",
                issued_at=now,
                expires_at=expiry,
                issued_by_user_id=principal.user_id,
                issued_by_member_id=actor.person_id,
                issued_role_assignment_id=actor.role_assignment_id,
                issued_event_id=event.event_id,
            )
        )
        await audit_crisis_action(
            session,
            principal,
            mandate.cooperative_id,
            "CRISIS_PAPER_FORM_ISSUED",
            "CrisisPaperForm",
            form_id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, form_id)

    async def record_paper_form(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        form_id: UUID,
        checksum: str,
        payload: dict[str, object],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> CrisisCommandResult:
        form = await self._paper_form(session, form_id, lock=True)
        mandate = await self._mandate(session, form.mandate_id, lock=False)
        self._require_effective_mandate(mandate, CrisisCapability.ENABLE_PAPER_FORMS)
        command = {
            "form_id": str(form.id),
            "checksum": checksum.strip().upper(),
            "payload": payload,
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_RECORD_PAPER_FORM", idempotency_key, command
        )
        if replay is not None:
            return replay
        if (
            form.status != "ISSUED"
            or datetime.now(UTC) >= form.expires_at
            or form.checksum != command["checksum"]
        ):
            raise crisis_error("PAPER_FORM_RECORDING_INVALID")
        actor = await crisis_role_actor(session, principal, form.cooperative_id, CONTROLLER_ROLES)
        if actor.person_id == form.issued_by_member_id:
            raise crisis_error("INDEPENDENT_RECORDER_REQUIRED")
        form_payload_hash = payload_hash(payload)
        event = await self.journal.append(
            session,
            event_type="crisis.paper_form_recorded",
            aggregate_type="crisis_paper_form",
            aggregate_id=form.id,
            aggregate_version=2,
            actor=actor,
            payload={
                **command,
                "payload_hash": form_payload_hash,
                "issued_event_id": str(form.issued_event_id),
            },
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.paper_form_recorded",
                subject_type="crisis_paper_form",
                subject_id=form.id,
                command_record=record,
                next_member_ids=(form.assigned_to_member_id,),
                attester_member_ids=(form.issued_by_member_id,),
            ),
        )
        form.status = "RECORDED"
        form.payload = payload
        form.payload_hash = form_payload_hash
        form.recorded_by_user_id = principal.user_id
        form.recorded_by_member_id = actor.person_id
        form.recorded_event_id = event.event_id
        form.recorded_at = datetime.now(UTC)
        await audit_crisis_action(
            session,
            principal,
            form.cooperative_id,
            "CRISIS_PAPER_FORM_RECORDED",
            "CrisisPaperForm",
            form.id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, form.id)

    async def close_mandate(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        mandate_id: UUID,
        expected_version: int,
        reconciliation_note: str,
        corrective_actions: Sequence[str],
        idempotency_key: str,
        request_id: UUID | None,
        expired: bool = False,
    ) -> CrisisCommandResult:
        mandate = await self._mandate(session, mandate_id, lock=True)
        decision = "EXPIRE" if expired else "CLOSE"
        command = {
            "mandate_id": str(mandate.id),
            "expected_version": expected_version,
            "decision": decision,
            "reconciliation_note": self._text(
                reconciliation_note, "RECONCILIATION_NOTE_INVALID", 5_000
            ),
            "corrective_actions": [
                self._text(item, "CORRECTIVE_ACTION_INVALID", 1_000) for item in corrective_actions
            ],
        }
        record, replay = await begin_crisis_command(
            session, principal, "CRISIS_CLOSE_MANDATE", idempotency_key, command
        )
        if replay is not None:
            return replay
        self._version(mandate.version, expected_version)
        if mandate.status != "ACTIVE":
            raise crisis_error("ACTIVE_MANDATE_REQUIRED")
        now = datetime.now(UTC)
        if expired and now < mandate.expires_at:
            raise crisis_error("MANDATE_NOT_EXPIRED")
        if not expired and now >= mandate.expires_at:
            raise crisis_error("EXPIRE_DECISION_REQUIRED")
        actor = await crisis_role_actor(session, principal, mandate.cooperative_id, REVIEW_ROLES)
        if actor.person_id == mandate.proposed_by_member_id:
            raise crisis_error("INDEPENDENT_REVIEWER_REQUIRED")
        open_allocations = int(
            await session.scalar(
                select(func.count())
                .select_from(RationingAllocation)
                .join(RationingPlan, RationingPlan.id == RationingAllocation.plan_id)
                .join(RationingRule, RationingRule.id == RationingPlan.rule_id)
                .where(
                    RationingRule.mandate_id == mandate.id, RationingAllocation.status == "RESERVED"
                )
            )
            or 0
        )
        open_forms = int(
            await session.scalar(
                select(func.count())
                .select_from(CrisisPaperForm)
                .where(
                    CrisisPaperForm.mandate_id == mandate.id,
                    CrisisPaperForm.status == "ISSUED",
                    CrisisPaperForm.expires_at > now,
                )
            )
            or 0
        )
        if open_allocations or open_forms:
            raise crisis_error("CRISIS_RECONCILIATION_INCOMPLETE")
        review_round = (
            int(
                await session.scalar(
                    select(func.coalesce(func.max(CrisisReview.decision_round), 0)).where(
                        CrisisReview.mandate_id == mandate.id
                    )
                )
                or 0
            )
            + 1
        )
        counts = await self._mandate_counts(session, mandate.id)
        report_id = uuid4()
        report_payload = {
            **command,
            **counts,
            "report_id": str(report_id),
            "mandate_code": mandate.mandate_code,
            "terms_hash": mandate.terms_hash,
            "safe_state": mandate.safe_state,
            "responsibility": [
                {"role": "CRISIS_OPERATOR", "member_id": str(mandate.proposed_by_member_id)},
                {"role": "CRISIS_CONTROLLER", "member_id": str(mandate.activated_by_member_id)},
                {"role": "CRISIS_REVIEWER", "member_id": str(actor.person_id)},
            ],
        }
        report_hash = payload_hash(report_payload)
        event = await self.journal.append(
            session,
            event_type="crisis.mandate_expired" if expired else "crisis.mandate_closed",
            aggregate_type="crisis_mandate",
            aggregate_id=mandate.id,
            aggregate_version=mandate.version + 1,
            actor=actor,
            payload={**report_payload, "report_hash": report_hash},
            assurance=crisis_command_assurance(
                principal=principal,
                actor=actor,
                event_type="crisis.mandate_expired" if expired else "crisis.mandate_closed",
                subject_type="crisis_mandate",
                subject_id=mandate.id,
                command_record=record,
                next_member_ids=(mandate.proposed_by_member_id, mandate.activated_by_member_id),
                attester_member_ids=(mandate.proposed_by_member_id, mandate.activated_by_member_id),
            ),
        )
        review_id = uuid4()
        session.add(
            CrisisReview(
                id=review_id,
                mandate_id=mandate.id,
                decision_round=review_round,
                decision=decision,
                facts_payload=counts,
                rationale=str(command["reconciliation_note"]),
                previous_review_at=mandate.review_at,
                previous_expires_at=mandate.expires_at,
                new_review_at=None,
                new_expires_at=None,
                reviewer_user_id=principal.user_id,
                reviewer_member_id=actor.person_id,
                reviewer_role_assignment_id=actor.role_assignment_id,
                event_id=event.event_id,
            )
        )
        session.add(
            CrisisReport(
                id=report_id,
                mandate_id=mandate.id,
                report_payload=report_payload,
                report_hash=report_hash,
                generated_event_id=event.event_id,
            )
        )
        mandate.status = "EXPIRED" if expired else "CLOSED"
        mandate.closed_by_user_id = principal.user_id
        mandate.closed_by_member_id = actor.person_id
        mandate.closed_event_id = event.event_id
        mandate.closed_at = now
        mandate.version += 1
        await audit_crisis_action(
            session,
            principal,
            mandate.cooperative_id,
            "CRISIS_MANDATE_EXPIRED" if expired else "CRISIS_MANDATE_CLOSED",
            "CrisisMandate",
            mandate.id,
            event.event_id,
            request_id,
        )
        return complete_crisis_command(record, event.event_id, report_id)

    @staticmethod
    async def _target(session: AsyncSession, target_id: UUID, *, lock: bool) -> ReserveTarget:
        item = await session.get(ReserveTarget, target_id, with_for_update=lock)
        if item is None:
            raise crisis_error("RESERVE_TARGET_NOT_FOUND", 404)
        return item

    @staticmethod
    async def _mandate(session: AsyncSession, mandate_id: UUID, *, lock: bool) -> CrisisMandate:
        item = await session.get(CrisisMandate, mandate_id, with_for_update=lock)
        if item is None:
            raise crisis_error("MANDATE_NOT_FOUND", 404)
        return item

    @staticmethod
    async def _rule(session: AsyncSession, rule_id: UUID, *, lock: bool) -> RationingRule:
        item = await session.get(RationingRule, rule_id, with_for_update=lock)
        if item is None:
            raise crisis_error("RATIONING_RULE_NOT_FOUND", 404)
        return item

    @staticmethod
    async def _plan(session: AsyncSession, plan_id: UUID, *, lock: bool) -> RationingPlan:
        item = await session.get(RationingPlan, plan_id, with_for_update=lock)
        if item is None:
            raise crisis_error("RATIONING_PLAN_NOT_FOUND", 404)
        return item

    @staticmethod
    async def _allocation(
        session: AsyncSession, allocation_id: UUID, *, lock: bool
    ) -> RationingAllocation:
        item = await session.get(RationingAllocation, allocation_id, with_for_update=lock)
        if item is None:
            raise crisis_error("RATIONING_ALLOCATION_NOT_FOUND", 404)
        return item

    @staticmethod
    async def _paper_form(session: AsyncSession, form_id: UUID, *, lock: bool) -> CrisisPaperForm:
        item = await session.get(CrisisPaperForm, form_id, with_for_update=lock)
        if item is None:
            raise crisis_error("PAPER_FORM_NOT_FOUND", 404)
        return item

    @staticmethod
    async def _latest_snapshot(session: AsyncSession, target_id: UUID) -> ReserveSnapshot:
        item = await session.scalar(
            select(ReserveSnapshot)
            .where(ReserveSnapshot.target_id == target_id)
            .order_by(ReserveSnapshot.observed_at.desc(), ReserveSnapshot.created_at.desc())
            .limit(1)
        )
        if item is None:
            raise crisis_error("RESERVE_SNAPSHOT_REQUIRED")
        return item

    @staticmethod
    def _require_fresh_snapshot(snapshot: ReserveSnapshot, target: ReserveTarget) -> None:
        if snapshot.observed_at < datetime.now(UTC) - timedelta(
            hours=target.max_snapshot_age_hours
        ):
            raise crisis_error("RESERVE_SNAPSHOT_STALE")

    @staticmethod
    def _require_effective_mandate(
        mandate: CrisisMandate, capability: CrisisCapability | None = None
    ) -> None:
        if (
            mandate.status != "ACTIVE"
            or datetime.now(UTC) < mandate.starts_at
            or datetime.now(UTC) >= mandate.expires_at
        ):
            raise crisis_error("ACTIVE_MANDATE_REQUIRED")
        if capability is not None and capability.value not in mandate.capabilities:
            raise crisis_error("MANDATE_CAPABILITY_REQUIRED")

    @staticmethod
    async def _reserved_total(session: AsyncSession, target_id: UUID) -> Decimal:
        value = await session.scalar(
            select(func.coalesce(func.sum(RationingAllocation.quantity), 0))
            .select_from(RationingAllocation)
            .join(RationingPlan, RationingPlan.id == RationingAllocation.plan_id)
            .join(RationingRule, RationingRule.id == RationingPlan.rule_id)
            .where(
                RationingRule.target_id == target_id,
                RationingPlan.status == "CONFIRMED",
                RationingAllocation.status.in_({"RESERVED", "ISSUED"}),
            )
        )
        return Decimal(value or 0)

    @staticmethod
    async def _mandate_counts(session: AsyncSession, mandate_id: UUID) -> dict[str, int]:
        rules = int(
            await session.scalar(
                select(func.count())
                .select_from(RationingRule)
                .where(RationingRule.mandate_id == mandate_id)
            )
            or 0
        )
        plans = int(
            await session.scalar(
                select(func.count())
                .select_from(RationingPlan)
                .join(RationingRule, RationingRule.id == RationingPlan.rule_id)
                .where(RationingRule.mandate_id == mandate_id)
            )
            or 0
        )
        issuances = int(
            await session.scalar(
                select(func.count())
                .select_from(RationIssuance)
                .join(RationingAllocation, RationingAllocation.id == RationIssuance.allocation_id)
                .join(RationingPlan, RationingPlan.id == RationingAllocation.plan_id)
                .join(RationingRule, RationingRule.id == RationingPlan.rule_id)
                .where(RationingRule.mandate_id == mandate_id)
            )
            or 0
        )
        forms = int(
            await session.scalar(
                select(func.count())
                .select_from(CrisisPaperForm)
                .where(CrisisPaperForm.mandate_id == mandate_id)
            )
            or 0
        )
        return {
            "rationing_rule_count": rules,
            "rationing_plan_count": plans,
            "ration_issuance_count": issuances,
            "paper_form_count": forms,
        }

    @staticmethod
    async def _lock_cooperative(session: AsyncSession, cooperative_id: UUID) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"crisis:{cooperative_id}"},
        )

    @staticmethod
    def _version(current: int, expected: int) -> None:
        if current != expected:
            raise crisis_error("VERSION_CONFLICT")

    @staticmethod
    def _text(value: str, code: str, maximum: int) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > maximum:
            raise crisis_error(code, 422)
        return normalized

    @staticmethod
    def _utc(value: datetime | None, code: str) -> datetime:
        if value is None or value.tzinfo is None:
            raise crisis_error(code, 422)
        return value.astimezone(UTC)
