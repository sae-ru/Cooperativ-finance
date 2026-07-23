"""Transactional procedural-fairness lifecycle for local trust cases."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import Member, Membership
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.journal.application.service import SignedJournalService
from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.modules.trust.application.common import (
    TrustCommandResult,
    audit_trust_action,
    begin_trust_command,
    complete_trust_command,
    evidence_payload,
    link_evidence,
    trust_participant_actor,
    trust_role_actor,
)
from cooperative_clearing.modules.trust.domain.types import (
    AppealOutcome,
    ConflictAssessment,
    DecisionOutcome,
    DecisionStage,
    FaultClass,
    ReputationClassification,
    ReputationContext,
    trust_error,
)
from cooperative_clearing.modules.trust.infrastructure.models import (
    Appeal,
    ArbitrationDecision,
    ConflictDeclaration,
    ProtectiveMeasure,
    RehabilitationPlan,
    RehabilitationStep,
    ReputationEvent,
    Sanction,
    TrustCase,
    TrustPolicy,
)
from cooperative_clearing.shared.core.config import Settings

POLICY_PROPOSER_ROLES = {RoleCode.COOPERATIVE_ADMIN}
POLICY_APPROVER_ROLES = {RoleCode.AUDITOR}
CASE_REVIEW_ROLES = {RoleCode.AUDITOR}
PROTECTIVE_ROLES = {RoleCode.RISK_ADMIN, RoleCode.AUDITOR}
ARBITRATION_ROLES = {RoleCode.ARBITRATOR}
REPUTATION_ROLES = {RoleCode.AUDITOR}


@dataclass(frozen=True, slots=True)
class RehabilitationStepDraft:
    description: str
    completion_criterion: str


class TrustService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.journal = SignedJournalService(settings)

    async def propose_policy(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        semantic_version: str,
        appeal_window_seconds: int,
        max_protective_seconds: int,
        panel_quorum: int,
        terms: dict[str, object],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        semantic = self._code(semantic_version, "SEMANTIC_VERSION_INVALID", 24)
        if not 0 <= appeal_window_seconds <= 2_592_000:
            raise trust_error("APPEAL_WINDOW_INVALID", 422)
        if not 1 <= max_protective_seconds <= 2_592_000:
            raise trust_error("PROTECTIVE_WINDOW_INVALID", 422)
        if not 1 <= panel_quorum <= 9:
            raise trust_error("PANEL_QUORUM_INVALID", 422)
        command = {
            "cooperative_id": str(cooperative_id),
            "semantic_version": semantic,
            "appeal_window_seconds": appeal_window_seconds,
            "max_protective_seconds": max_protective_seconds,
            "panel_quorum": panel_quorum,
            "terms": terms,
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_PROPOSE_POLICY", idempotency_key, command
        )
        if replay is not None:
            return replay
        actor = await trust_role_actor(session, principal, cooperative_id, POLICY_PROPOSER_ROLES)
        await self._lock_cooperative(session, cooperative_id)
        current_version = await session.scalar(
            select(func.coalesce(func.max(TrustPolicy.policy_version), 0)).where(
                TrustPolicy.cooperative_id == cooperative_id
            )
        )
        policy_version = int(current_version or 0) + 1
        terms_payload = {
            **command,
            "policy_code": "TRUST_PROCEDURE",
            "policy_version": policy_version,
        }
        terms_hash = payload_hash(terms_payload)
        policy_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="trust.policy_proposed",
            aggregate_type="trust_policy",
            aggregate_id=policy_id,
            aggregate_version=1,
            actor=actor,
            payload={**terms_payload, "policy_id": str(policy_id), "terms_hash": terms_hash},
        )
        session.add(
            TrustPolicy(
                id=policy_id,
                cooperative_id=cooperative_id,
                policy_version=policy_version,
                policy_code="TRUST_PROCEDURE",
                semantic_version=semantic,
                appeal_window_seconds=appeal_window_seconds,
                max_protective_seconds=max_protective_seconds,
                panel_quorum=panel_quorum,
                terms_payload=terms_payload,
                terms_hash=terms_hash,
                status="PROPOSED",
                proposed_by_user_id=principal.user_id,
                proposed_by_member_id=actor.person_id,
                proposed_role_assignment_id=actor.role_assignment_id,
                proposed_event_id=event.event_id,
                version=1,
            )
        )
        await audit_trust_action(
            session,
            principal,
            cooperative_id,
            "TRUST_POLICY_PROPOSED",
            "TrustPolicy",
            policy_id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, policy_id)

    async def approve_policy(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        policy_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        policy = await session.get(TrustPolicy, policy_id, with_for_update=True)
        if policy is None:
            raise trust_error("POLICY_NOT_FOUND", 404)
        payload = {"policy_id": str(policy_id), "expected_version": expected_version}
        record, replay = await begin_trust_command(
            session, principal, "TRUST_APPROVE_POLICY", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(policy.version, expected_version)
        if policy.status != "PROPOSED":
            raise trust_error("POLICY_NOT_PROPOSED")
        actor = await trust_role_actor(
            session, principal, policy.cooperative_id, POLICY_APPROVER_ROLES
        )
        if (
            principal.user_id == policy.proposed_by_user_id
            or actor.person_id == policy.proposed_by_member_id
        ):
            raise trust_error("INDEPENDENT_APPROVER_REQUIRED")
        await self._lock_cooperative(session, policy.cooperative_id)
        current = await session.scalar(
            select(TrustPolicy)
            .where(
                TrustPolicy.cooperative_id == policy.cooperative_id,
                TrustPolicy.status == "ACTIVE",
            )
            .with_for_update()
        )
        if current is not None:
            superseded = await self.journal.append(
                session,
                event_type="trust.policy_superseded",
                aggregate_type="trust_policy",
                aggregate_id=current.id,
                aggregate_version=current.version + 1,
                actor=actor,
                payload={
                    "policy_id": str(current.id),
                    "replacement_policy_id": str(policy.id),
                    "replacement_terms_hash": policy.terms_hash,
                },
            )
            current.status = "SUPERSEDED"
            current.version += 1
            await audit_trust_action(
                session,
                principal,
                policy.cooperative_id,
                "TRUST_POLICY_SUPERSEDED",
                "TrustPolicy",
                current.id,
                superseded.event_id,
                request_id,
            )
        now = datetime.now(UTC)
        event = await self.journal.append(
            session,
            event_type="trust.policy_approved",
            aggregate_type="trust_policy",
            aggregate_id=policy.id,
            aggregate_version=policy.version + 1,
            actor=actor,
            payload={**payload, "terms_hash": policy.terms_hash},
        )
        policy.status = "ACTIVE"
        policy.approved_by_user_id = principal.user_id
        policy.approved_by_member_id = actor.person_id
        policy.approved_role_assignment_id = actor.role_assignment_id
        policy.approved_event_id = event.event_id
        policy.approved_at = now
        policy.version += 1
        await audit_trust_action(
            session,
            principal,
            policy.cooperative_id,
            "TRUST_POLICY_APPROVED",
            "TrustPolicy",
            policy.id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, policy.id)

    async def open_case(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        case_reference: str,
        subject_member_id: UUID,
        claimant_member_id: UUID,
        source_type: str,
        source_reference: str,
        source_event_ids: Sequence[UUID],
        evidence_ids: Sequence[UUID],
        summary: str,
        facts: str,
        requested_outcome: str,
        confidentiality: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        reference = self._code(case_reference, "CASE_REFERENCE_INVALID", 80)
        source_code = self._choice(
            source_type,
            {"LIABILITY", "EXCHANGE", "CLEARING", "INVENTORY", "RIGHTS", "NODE", "OTHER"},
            "SOURCE_TYPE_INVALID",
        )
        confidentiality_code = self._choice(
            confidentiality, {"NORMAL", "RESTRICTED"}, "CONFIDENTIALITY_INVALID"
        )
        payload = {
            "cooperative_id": str(cooperative_id),
            "case_reference": reference,
            "subject_member_id": str(subject_member_id),
            "claimant_member_id": str(claimant_member_id),
            "source_type": source_code,
            "source_reference": self._text(source_reference, "SOURCE_REFERENCE_INVALID", 120),
            "source_event_ids": [str(value) for value in source_event_ids],
            "evidence_ids": [str(value) for value in evidence_ids],
            "summary": self._text(summary, "SUMMARY_INVALID", 240),
            "facts": self._text(facts, "FACTS_INVALID", 20_000),
            "requested_outcome": self._text(requested_outcome, "REQUESTED_OUTCOME_INVALID", 5_000),
            "confidentiality": confidentiality_code,
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_OPEN_CASE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        actor = await trust_participant_actor(session, principal, cooperative_id)
        if actor.person_id != claimant_member_id and not principal.has_role(
            {RoleCode.AUDITOR, RoleCode.RISK_ADMIN}, cooperative_id
        ):
            raise trust_error("CLAIMANT_OR_REVIEWER_REQUIRED", 403)
        await self._eligible_member(session, cooperative_id, subject_member_id)
        await self._eligible_member(session, cooperative_id, claimant_member_id)
        policy = await self._active_policy(session, cooperative_id)
        await self._validate_source_events(session, cooperative_id, source_event_ids)
        evidence = await EvidenceService.require_ready(
            session, cooperative_id, evidence_ids, required=True
        )
        await self._lock_cooperative(session, cooperative_id)
        duplicate = await session.scalar(
            select(TrustCase.id).where(
                TrustCase.cooperative_id == cooperative_id,
                TrustCase.case_reference == reference,
            )
        )
        if duplicate is not None:
            raise trust_error("CASE_REFERENCE_EXISTS")
        evidence_refs = evidence_payload(evidence)
        case_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="disputes.dispute_opened",
            aggregate_type="trust_case",
            aggregate_id=case_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "case_id": str(case_id), "evidence": evidence_refs},
        )
        session.add(
            TrustCase(
                id=case_id,
                cooperative_id=cooperative_id,
                policy_id=policy.id,
                case_reference=reference,
                subject_member_id=subject_member_id,
                claimant_member_id=claimant_member_id,
                source_type=source_code,
                source_reference=str(payload["source_reference"]),
                source_event_ids=list(payload["source_event_ids"]),
                evidence_refs=evidence_refs,
                summary=str(payload["summary"]),
                facts=str(payload["facts"]),
                requested_outcome=str(payload["requested_outcome"]),
                confidentiality=confidentiality_code,
                status="OPEN",
                opened_by_user_id=principal.user_id,
                opened_by_member_id=actor.person_id,
                opened_role_assignment_id=actor.role_assignment_id,
                opened_event_id=event.event_id,
                version=1,
            )
        )
        link_evidence(session, evidence, event.event_id, "trust_case", case_id)
        await audit_trust_action(
            session,
            principal,
            cooperative_id,
            "TRUST_CASE_OPENED",
            "TrustCase",
            case_id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, case_id)

    async def record_response(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        case_id: UUID,
        expected_version: int,
        response_text: str,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        case = await self._case(session, case_id, lock=True)
        payload = {
            "case_id": str(case_id),
            "expected_version": expected_version,
            "response_text": self._text(response_text, "RESPONSE_INVALID", 20_000),
            "evidence_ids": [str(value) for value in evidence_ids],
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_RECORD_RESPONSE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(case.version, expected_version)
        if case.status not in {"OPEN", "REMANDED"} or case.response_event_id is not None:
            raise trust_error("CASE_RESPONSE_NOT_ALLOWED")
        actor = await trust_participant_actor(session, principal, case.cooperative_id)
        if actor.person_id != case.subject_member_id:
            raise trust_error("CASE_SUBJECT_REQUIRED", 403)
        evidence = await EvidenceService.require_ready(
            session, case.cooperative_id, evidence_ids, required=False
        )
        refs = evidence_payload(evidence)
        now = datetime.now(UTC)
        event = await self.journal.append(
            session,
            event_type="disputes.response_recorded",
            aggregate_type="trust_case",
            aggregate_id=case.id,
            aggregate_version=case.version + 1,
            actor=actor,
            payload={**payload, "evidence": refs},
        )
        case.response_text = str(payload["response_text"])
        case.response_evidence_refs = refs
        case.responded_by_user_id = principal.user_id
        case.response_event_id = event.event_id
        case.responded_at = now
        case.status = "RESPONSE_RECEIVED"
        case.version += 1
        link_evidence(session, evidence, event.event_id, "trust_case_response", case.id)
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_CASE_RESPONSE_RECORDED",
            "TrustCase",
            case.id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, case.id)

    async def mark_case_ready(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        case_id: UUID,
        expected_version: int,
        review_note: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        case = await self._case(session, case_id, lock=True)
        payload = {
            "case_id": str(case_id),
            "expected_version": expected_version,
            "review_note": self._text(review_note, "REVIEW_NOTE_INVALID", 5_000),
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_MARK_CASE_READY", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(case.version, expected_version)
        if case.status not in {"RESPONSE_RECEIVED", "REMANDED"}:
            raise trust_error("CASE_NOT_RESPONSE_RECEIVED")
        actor = await trust_role_actor(session, principal, case.cooperative_id, CASE_REVIEW_ROLES)
        if actor.person_id in {case.subject_member_id, case.claimant_member_id}:
            raise trust_error("INDEPENDENT_REVIEWER_REQUIRED")
        event = await self.journal.append(
            session,
            event_type="disputes.case_ready_for_decision",
            aggregate_type="trust_case",
            aggregate_id=case.id,
            aggregate_version=case.version + 1,
            actor=actor,
            payload=payload,
        )
        case.status = "READY_FOR_DECISION"
        case.version += 1
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_CASE_READY",
            "TrustCase",
            case.id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, case.id)

    async def declare_conflict(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        case_id: UUID,
        stage: DecisionStage | str,
        assessment: ConflictAssessment,
        relationship: str,
        rationale: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        case = await self._case(session, case_id, lock=False)
        stage_value = (
            stage.value
            if isinstance(stage, DecisionStage)
            else self._choice(
                stage, {"ORIGINAL", "APPEAL", "REHABILITATION"}, "CONFLICT_STAGE_INVALID"
            )
        )
        payload = {
            "case_id": str(case_id),
            "stage": stage_value,
            "assessment": assessment.value,
            "relationship": self._text(relationship, "RELATIONSHIP_INVALID", 120),
            "rationale": self._text(rationale, "CONFLICT_RATIONALE_INVALID", 5_000),
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_DECLARE_CONFLICT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        actor = await trust_role_actor(session, principal, case.cooperative_id, ARBITRATION_ROLES)
        forced_conflict = actor.person_id in {
            case.subject_member_id,
            case.claimant_member_id,
            case.opened_by_member_id,
        }
        if forced_conflict and assessment != ConflictAssessment.CONFLICT:
            raise trust_error("CONFLICT_MUST_BE_DECLARED")
        exists = await session.scalar(
            select(ConflictDeclaration.id).where(
                ConflictDeclaration.case_id == case.id,
                ConflictDeclaration.stage == stage_value,
                ConflictDeclaration.member_id == actor.person_id,
            )
        )
        if exists is not None:
            raise trust_error("CONFLICT_ALREADY_DECLARED")
        declaration_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="disputes.conflict_declared",
            aggregate_type="conflict_declaration",
            aggregate_id=declaration_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "declaration_id": str(declaration_id)},
        )
        session.add(
            ConflictDeclaration(
                id=declaration_id,
                case_id=case.id,
                stage=stage_value,
                member_id=actor.person_id,
                user_id=principal.user_id,
                role_assignment_id=actor.role_assignment_id,
                assessment=assessment.value,
                relationship=str(payload["relationship"]),
                rationale=str(payload["rationale"]),
                event_id=event.event_id,
            )
        )
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_CONFLICT_DECLARED",
            "ConflictDeclaration",
            declaration_id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, declaration_id)

    async def impose_protective_measure(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        case_id: UUID,
        expected_case_version: int,
        measure_type: str,
        scope: dict[str, object],
        rationale: str,
        expires_at: datetime,
        review_at: datetime,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        case = await self._case(session, case_id, lock=True)
        measure_code = self._choice(
            measure_type,
            {
                "ADDITIONAL_REVIEW",
                "LIMIT_SCOPE",
                "SUSPEND_ROLE",
                "SUSPEND_KEY",
                "BLOCK_NEW_GUARANTEES",
            },
            "PROTECTIVE_MEASURE_INVALID",
        )
        expiry = self._utc(expires_at, "PROTECTIVE_EXPIRY_INVALID")
        review = self._utc(review_at, "PROTECTIVE_REVIEW_INVALID")
        payload = {
            "case_id": str(case_id),
            "expected_case_version": expected_case_version,
            "measure_type": measure_code,
            "scope": scope,
            "rationale": self._text(rationale, "PROTECTIVE_RATIONALE_INVALID", 5_000),
            "expires_at": expiry.isoformat(),
            "review_at": review.isoformat(),
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_IMPOSE_PROTECTIVE_MEASURE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(case.version, expected_case_version)
        if case.status == "CLOSED":
            raise trust_error("CASE_CLOSED")
        actor = await trust_role_actor(session, principal, case.cooperative_id, PROTECTIVE_ROLES)
        if actor.person_id == case.subject_member_id:
            raise trust_error("INDEPENDENT_MEASURE_ACTOR_REQUIRED")
        policy = await session.get(TrustPolicy, case.policy_id)
        if policy is None:
            raise trust_error("POLICY_NOT_FOUND", 404)
        now = datetime.now(UTC)
        if expiry <= now or expiry > now + timedelta(seconds=policy.max_protective_seconds):
            raise trust_error("PROTECTIVE_EXPIRY_INVALID", 422)
        if review > expiry:
            raise trust_error("PROTECTIVE_REVIEW_INVALID", 422)
        if not scope:
            raise trust_error("PROTECTIVE_SCOPE_REQUIRED", 422)
        measure_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="sanctions.protective_measure_imposed",
            aggregate_type="protective_measure",
            aggregate_id=measure_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "measure_id": str(measure_id),
                "subject_member_id": str(case.subject_member_id),
            },
        )
        session.add(
            ProtectiveMeasure(
                id=measure_id,
                case_id=case.id,
                subject_member_id=case.subject_member_id,
                measure_type=measure_code,
                scope=scope,
                rationale=str(payload["rationale"]),
                status="ACTIVE",
                starts_at=now,
                expires_at=expiry,
                review_at=review,
                imposed_by_user_id=principal.user_id,
                imposed_by_member_id=actor.person_id,
                imposed_role_assignment_id=actor.role_assignment_id,
                imposed_event_id=event.event_id,
                version=1,
            )
        )
        case.version += 1
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_PROTECTIVE_MEASURE_IMPOSED",
            "ProtectiveMeasure",
            measure_id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, measure_id)

    async def lift_protective_measure(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        measure_id: UUID,
        expected_version: int,
        reason: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        measure = await session.get(ProtectiveMeasure, measure_id, with_for_update=True)
        if measure is None:
            raise trust_error("PROTECTIVE_MEASURE_NOT_FOUND", 404)
        case = await self._case(session, measure.case_id, lock=False)
        payload = {
            "measure_id": str(measure_id),
            "expected_version": expected_version,
            "reason": self._text(reason, "LIFT_REASON_INVALID", 5_000),
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_LIFT_PROTECTIVE_MEASURE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(measure.version, expected_version)
        if measure.status != "ACTIVE":
            raise trust_error("PROTECTIVE_MEASURE_NOT_ACTIVE")
        actor = await trust_role_actor(session, principal, case.cooperative_id, PROTECTIVE_ROLES)
        event = await self.journal.append(
            session,
            event_type="sanctions.protective_measure_lifted",
            aggregate_type="protective_measure",
            aggregate_id=measure.id,
            aggregate_version=measure.version + 1,
            actor=actor,
            payload=payload,
        )
        now = datetime.now(UTC)
        measure.status = "LIFTED"
        measure.lifted_by_user_id = principal.user_id
        measure.lifted_by_member_id = actor.person_id
        measure.lifted_event_id = event.event_id
        measure.lift_reason = str(payload["reason"])
        measure.lifted_at = now
        measure.version += 1
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_PROTECTIVE_MEASURE_LIFTED",
            "ProtectiveMeasure",
            measure.id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, measure.id)

    async def issue_original_decision(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        case_id: UUID,
        expected_case_version: int,
        outcome: DecisionOutcome,
        standard_of_proof: str,
        fault_class: FaultClass | None,
        causal_findings: dict[str, object],
        established_loss: Decimal | None,
        reasoning: str,
        consequence_spec: dict[str, object],
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        if outcome not in {
            DecisionOutcome.SUBSTANTIATED,
            DecisionOutcome.PARTLY_SUBSTANTIATED,
            DecisionOutcome.UNSUBSTANTIATED,
        }:
            raise trust_error("ORIGINAL_DECISION_OUTCOME_INVALID", 422)
        case = await self._case(session, case_id, lock=True)
        loss = established_loss
        if loss is not None and loss < 0:
            raise trust_error("ESTABLISHED_LOSS_INVALID", 422)
        if outcome != DecisionOutcome.UNSUBSTANTIATED and fault_class is None:
            raise trust_error("FAULT_CLASS_REQUIRED", 422)
        if outcome == DecisionOutcome.UNSUBSTANTIATED and (
            fault_class is not None or loss not in {None, Decimal("0")}
        ):
            raise trust_error("UNSUBSTANTIATED_EFFECT_INVALID", 422)
        payload = {
            "case_id": str(case_id),
            "expected_case_version": expected_case_version,
            "outcome": outcome.value,
            "standard_of_proof": self._text(standard_of_proof, "PROOF_STANDARD_INVALID", 120),
            "fault_class": fault_class.value if fault_class else None,
            "causal_findings": causal_findings,
            "established_loss": str(loss) if loss is not None else None,
            "reasoning": self._text(reasoning, "DECISION_REASONING_INVALID", 20_000),
            "consequence_spec": consequence_spec,
            "evidence_ids": [str(value) for value in evidence_ids],
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_ISSUE_ORIGINAL_DECISION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(case.version, expected_case_version)
        if case.status != "READY_FOR_DECISION":
            raise trust_error("CASE_NOT_READY_FOR_DECISION")
        actor = await trust_role_actor(session, principal, case.cooperative_id, ARBITRATION_ROLES)
        await self._require_clear_conflict(session, case, "ORIGINAL", actor.person_id)
        if actor.person_id in {
            case.subject_member_id,
            case.claimant_member_id,
            case.opened_by_member_id,
        }:
            raise trust_error("INDEPENDENT_ARBITRATOR_REQUIRED")
        policy = await session.get(TrustPolicy, case.policy_id)
        if policy is None:
            raise trust_error("POLICY_NOT_FOUND", 404)
        if policy.panel_quorum != 1:
            raise trust_error("PANEL_QUORUM_NOT_MET")
        evidence = await EvidenceService.require_ready(
            session, case.cooperative_id, evidence_ids, required=True
        )
        refs = evidence_payload(evidence)
        now = datetime.now(UTC)
        current_round = await session.scalar(
            select(func.coalesce(func.max(ArbitrationDecision.decision_round), 0)).where(
                ArbitrationDecision.case_id == case.id,
                ArbitrationDecision.stage == "ORIGINAL",
            )
        )
        decision_round = int(current_round or 0) + 1
        decision_id = uuid4()
        panel = [
            {
                "member_id": str(actor.person_id),
                "user_id": str(principal.user_id),
                "role_assignment_id": str(actor.role_assignment_id),
            }
        ]
        event = await self.journal.append(
            session,
            event_type="disputes.decision_issued",
            aggregate_type="arbitration_decision",
            aggregate_id=decision_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "decision_id": str(decision_id),
                "panel": panel,
                "evidence": refs,
                "policy_version": self._policy_label(policy),
            },
        )
        session.add(
            ArbitrationDecision(
                id=decision_id,
                case_id=case.id,
                stage="ORIGINAL",
                decision_round=decision_round,
                related_object_id=None,
                outcome=outcome.value,
                standard_of_proof=str(payload["standard_of_proof"]),
                fault_class=fault_class.value if fault_class else None,
                causal_findings=causal_findings,
                established_loss=loss,
                reasoning=str(payload["reasoning"]),
                consequence_spec=consequence_spec,
                evidence_refs=refs,
                panel_snapshot=panel,
                policy_version=self._policy_label(policy),
                issued_by_user_id=principal.user_id,
                issued_by_member_id=actor.person_id,
                issued_role_assignment_id=actor.role_assignment_id,
                issued_event_id=event.event_id,
                issued_at=now,
            )
        )
        case.status = "DECIDED"
        case.original_decision_at = now
        case.appeal_until = now + timedelta(seconds=policy.appeal_window_seconds)
        case.version += 1
        link_evidence(session, evidence, event.event_id, "arbitration_decision", decision_id)
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_DECISION_ISSUED",
            "ArbitrationDecision",
            decision_id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, decision_id)

    async def propose_sanction(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        decision_id: UUID,
        measure_type: str,
        severity: str,
        scope: dict[str, object],
        rationale: str,
        starts_at: datetime,
        expires_at: datetime | None,
        review_at: datetime | None,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        decision = await session.get(ArbitrationDecision, decision_id)
        if decision is None:
            raise trust_error("DECISION_NOT_FOUND", 404)
        case = await self._case(session, decision.case_id, lock=True)
        measure_code = self._choice(
            measure_type,
            {
                "WARNING",
                "TRAINING",
                "ADDITIONAL_REVIEW",
                "LIMIT_SCOPE",
                "SUSPEND_ROLE",
                "BLOCK_NEW_GUARANTEES",
                "TERMINATE_ROLE",
            },
            "SANCTION_TYPE_INVALID",
        )
        severity_code = self._choice(
            severity, {"LOW", "MEDIUM", "HIGH", "CRITICAL"}, "SANCTION_SEVERITY_INVALID"
        )
        starts = self._utc(starts_at, "SANCTION_START_INVALID")
        expiry = self._utc(expires_at, "SANCTION_EXPIRY_INVALID") if expires_at else None
        review = self._utc(review_at, "SANCTION_REVIEW_INVALID") if review_at else None
        if expiry is not None and expiry <= starts:
            raise trust_error("SANCTION_EXPIRY_INVALID", 422)
        if review is not None and expiry is not None and review > expiry:
            raise trust_error("SANCTION_REVIEW_INVALID", 422)
        payload = {
            "decision_id": str(decision_id),
            "case_id": str(case.id),
            "measure_type": measure_code,
            "severity": severity_code,
            "scope": scope,
            "rationale": self._text(rationale, "SANCTION_RATIONALE_INVALID", 5_000),
            "starts_at": starts.isoformat(),
            "expires_at": expiry.isoformat() if expiry else None,
            "review_at": review.isoformat() if review else None,
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_PROPOSE_SANCTION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        if decision.stage != "ORIGINAL" or decision.outcome == "UNSUBSTANTIATED":
            raise trust_error("SANCTION_DECISION_NOT_ELIGIBLE")
        if case.status != "DECIDED" or case.appeal_until is None:
            raise trust_error("CASE_NOT_DECIDED")
        actor = await trust_role_actor(session, principal, case.cooperative_id, ARBITRATION_ROLES)
        if actor.person_id != decision.issued_by_member_id:
            raise trust_error("DECISION_PANEL_REQUIRED", 403)
        if not scope:
            raise trust_error("SANCTION_SCOPE_REQUIRED", 422)
        sanction_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="sanctions.sanction_proposed",
            aggregate_type="sanction",
            aggregate_id=sanction_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "sanction_id": str(sanction_id),
                "subject_member_id": str(case.subject_member_id),
            },
        )
        session.add(
            Sanction(
                id=sanction_id,
                case_id=case.id,
                decision_id=decision.id,
                subject_member_id=case.subject_member_id,
                measure_type=measure_code,
                severity=severity_code,
                scope=scope,
                rationale=str(payload["rationale"]),
                status="PENDING_APPEAL",
                starts_at=starts,
                expires_at=expiry,
                review_at=review,
                appeal_until=case.appeal_until,
                proposed_by_user_id=principal.user_id,
                proposed_by_member_id=actor.person_id,
                proposed_event_id=event.event_id,
                version=1,
            )
        )
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_SANCTION_PROPOSED",
            "Sanction",
            sanction_id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, sanction_id)

    async def record_reputation_event(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        decision_id: UUID,
        context: ReputationContext,
        classification: ReputationClassification,
        severity: int,
        confidence: Decimal,
        observation_start: datetime,
        observation_end: datetime,
        source_event_ids: Sequence[UUID],
        evidence_ids: Sequence[UUID],
        visibility: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        decision = await session.get(ArbitrationDecision, decision_id)
        if decision is None:
            raise trust_error("DECISION_NOT_FOUND", 404)
        case = await self._case(session, decision.case_id, lock=False)
        if not 0 <= severity <= 5 or not Decimal("0") <= confidence <= Decimal("1"):
            raise trust_error("REPUTATION_MEASUREMENT_INVALID", 422)
        if classification == ReputationClassification.CORRECTION:
            raise trust_error("CORRECTION_IS_SYSTEM_GENERATED", 422)
        start = self._utc(observation_start, "OBSERVATION_PERIOD_INVALID")
        end = self._utc(observation_end, "OBSERVATION_PERIOD_INVALID")
        if end < start:
            raise trust_error("OBSERVATION_PERIOD_INVALID", 422)
        visibility_code = self._choice(
            visibility, {"PARTICIPANT", "COOPERATIVE", "RESTRICTED"}, "VISIBILITY_INVALID"
        )
        payload = {
            "decision_id": str(decision_id),
            "case_id": str(case.id),
            "subject_member_id": str(case.subject_member_id),
            "context": context.value,
            "classification": classification.value,
            "severity": severity,
            "confidence": str(confidence),
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
            "source_event_ids": [str(value) for value in source_event_ids],
            "evidence_ids": [str(value) for value in evidence_ids],
            "visibility": visibility_code,
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_RECORD_REPUTATION_EVENT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        if decision.stage != "ORIGINAL":
            raise trust_error("ORIGINAL_DECISION_REQUIRED")
        if (
            decision.outcome == "UNSUBSTANTIATED"
            and classification == ReputationClassification.BREACH
        ):
            raise trust_error("BREACH_NOT_ESTABLISHED")
        actor = await trust_role_actor(session, principal, case.cooperative_id, REPUTATION_ROLES)
        if actor.person_id == decision.issued_by_member_id:
            raise trust_error("INDEPENDENT_REPUTATION_REVIEWER_REQUIRED")
        source_ids = tuple(dict.fromkeys((*source_event_ids, decision.issued_event_id)))
        await self._validate_source_events(session, case.cooperative_id, source_ids)
        evidence = await EvidenceService.require_ready(
            session, case.cooperative_id, evidence_ids, required=True
        )
        refs = evidence_payload(evidence)
        event_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="reputation.event_recorded",
            aggregate_type="reputation_event",
            aggregate_id=event_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "reputation_event_id": str(event_id),
                "source_event_ids": [str(value) for value in source_ids],
                "evidence": refs,
                "appeal_state": "PENDING",
                "status": "DISPUTED",
                "policy_version": decision.policy_version,
            },
        )
        session.add(
            ReputationEvent(
                id=event_id,
                cooperative_id=case.cooperative_id,
                case_id=case.id,
                decision_id=decision.id,
                subject_member_id=case.subject_member_id,
                context=context.value,
                classification=classification.value,
                severity=severity,
                confidence=confidence,
                observation_start=start,
                observation_end=end,
                source_event_ids=[str(value) for value in source_ids],
                evidence_refs=refs,
                appeal_state="PENDING",
                status="DISPUTED",
                visibility=visibility_code,
                policy_version=decision.policy_version,
                corrects_event_id=None,
                recorded_by_user_id=principal.user_id,
                recorded_by_member_id=actor.person_id,
                recorded_event_id=event.event_id,
            )
        )
        link_evidence(session, evidence, event.event_id, "reputation_event", event_id)
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_REPUTATION_EVENT_RECORDED",
            "ReputationEvent",
            event_id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, event_id)

    async def submit_appeal(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        case_id: UUID,
        original_decision_id: UUID,
        sanction_id: UUID | None,
        expected_case_version: int,
        grounds: str,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        case = await self._case(session, case_id, lock=True)
        payload = {
            "case_id": str(case_id),
            "original_decision_id": str(original_decision_id),
            "sanction_id": str(sanction_id) if sanction_id else None,
            "expected_case_version": expected_case_version,
            "grounds": self._text(grounds, "APPEAL_GROUNDS_INVALID", 20_000),
            "evidence_ids": [str(value) for value in evidence_ids],
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_SUBMIT_APPEAL", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(case.version, expected_case_version)
        if case.status != "DECIDED" or case.appeal_until is None:
            raise trust_error("APPEAL_NOT_ALLOWED")
        if datetime.now(UTC) > case.appeal_until:
            raise trust_error("APPEAL_WINDOW_EXPIRED")
        decision = await session.get(ArbitrationDecision, original_decision_id)
        if decision is None or decision.case_id != case.id or decision.stage != "ORIGINAL":
            raise trust_error("ORIGINAL_DECISION_NOT_FOUND", 404)
        if sanction_id is not None:
            sanction = await session.get(Sanction, sanction_id)
            if sanction is None or sanction.case_id != case.id:
                raise trust_error("SANCTION_NOT_FOUND", 404)
        actor = await trust_participant_actor(session, principal, case.cooperative_id)
        if actor.person_id not in {case.subject_member_id, case.claimant_member_id}:
            raise trust_error("CASE_PARTY_REQUIRED", 403)
        evidence = await EvidenceService.require_ready(
            session, case.cooperative_id, evidence_ids, required=True
        )
        refs = evidence_payload(evidence)
        appeal_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="appeals.appeal_submitted",
            aggregate_type="appeal",
            aggregate_id=appeal_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "appeal_id": str(appeal_id), "evidence": refs},
        )
        session.add(
            Appeal(
                id=appeal_id,
                case_id=case.id,
                original_decision_id=decision.id,
                sanction_id=sanction_id,
                appellant_member_id=actor.person_id,
                grounds=str(payload["grounds"]),
                evidence_refs=refs,
                status="SUBMITTED",
                submitted_by_user_id=principal.user_id,
                submitted_event_id=event.event_id,
            )
        )
        case.status = "UNDER_APPEAL"
        case.version += 1
        link_evidence(session, evidence, event.event_id, "appeal", appeal_id)
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_APPEAL_SUBMITTED",
            "Appeal",
            appeal_id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, appeal_id)

    async def decide_appeal(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        appeal_id: UUID,
        expected_case_version: int,
        outcome: AppealOutcome,
        standard_of_proof: str,
        causal_findings: dict[str, object],
        reasoning: str,
        consequence_spec: dict[str, object],
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        appeal = await session.get(Appeal, appeal_id, with_for_update=True)
        if appeal is None:
            raise trust_error("APPEAL_NOT_FOUND", 404)
        case = await self._case(session, appeal.case_id, lock=True)
        payload = {
            "appeal_id": str(appeal_id),
            "case_id": str(case.id),
            "expected_case_version": expected_case_version,
            "outcome": outcome.value,
            "standard_of_proof": self._text(standard_of_proof, "PROOF_STANDARD_INVALID", 120),
            "causal_findings": causal_findings,
            "reasoning": self._text(reasoning, "DECISION_REASONING_INVALID", 20_000),
            "consequence_spec": consequence_spec,
            "evidence_ids": [str(value) for value in evidence_ids],
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_DECIDE_APPEAL", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(case.version, expected_case_version)
        if appeal.status != "SUBMITTED" or case.status != "UNDER_APPEAL":
            raise trust_error("APPEAL_NOT_PENDING")
        original = await session.get(ArbitrationDecision, appeal.original_decision_id)
        if original is None:
            raise trust_error("ORIGINAL_DECISION_NOT_FOUND", 404)
        actor = await trust_role_actor(session, principal, case.cooperative_id, ARBITRATION_ROLES)
        await self._require_clear_conflict(session, case, "APPEAL", actor.person_id)
        if actor.person_id in {
            original.issued_by_member_id,
            case.subject_member_id,
            case.claimant_member_id,
            case.opened_by_member_id,
        }:
            raise trust_error("INDEPENDENT_APPEAL_PANEL_REQUIRED")
        evidence = await EvidenceService.require_ready(
            session, case.cooperative_id, evidence_ids, required=True
        )
        refs = evidence_payload(evidence)
        current_round = await session.scalar(
            select(func.coalesce(func.max(ArbitrationDecision.decision_round), 0)).where(
                ArbitrationDecision.case_id == case.id,
                ArbitrationDecision.stage == "APPEAL",
            )
        )
        decision_round = int(current_round or 0) + 1
        decision_id = uuid4()
        now = datetime.now(UTC)
        panel = [
            {
                "member_id": str(actor.person_id),
                "user_id": str(principal.user_id),
                "role_assignment_id": str(actor.role_assignment_id),
            }
        ]
        event = await self.journal.append(
            session,
            event_type="appeals.appeal_decided",
            aggregate_type="arbitration_decision",
            aggregate_id=decision_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "decision_id": str(decision_id),
                "original_decision_id": str(original.id),
                "panel": panel,
                "evidence": refs,
                "policy_version": original.policy_version,
            },
        )
        decision = ArbitrationDecision(
            id=decision_id,
            case_id=case.id,
            stage="APPEAL",
            decision_round=decision_round,
            related_object_id=appeal.id,
            outcome=outcome.value,
            standard_of_proof=str(payload["standard_of_proof"]),
            fault_class=original.fault_class
            if outcome in {AppealOutcome.AFFIRMED, AppealOutcome.MODIFIED}
            else None,
            causal_findings=causal_findings,
            established_loss=original.established_loss
            if outcome == AppealOutcome.AFFIRMED
            else None,
            reasoning=str(payload["reasoning"]),
            consequence_spec=consequence_spec,
            evidence_refs=refs,
            panel_snapshot=panel,
            policy_version=original.policy_version,
            issued_by_user_id=principal.user_id,
            issued_by_member_id=actor.person_id,
            issued_role_assignment_id=actor.role_assignment_id,
            issued_event_id=event.event_id,
            issued_at=now,
        )
        session.add(decision)
        await session.flush([decision])
        appeal.status = "DECIDED"
        appeal.appeal_decision_id = decision_id
        appeal.outcome = outcome.value
        appeal.decided_event_id = event.event_id
        appeal.decided_at = now
        if outcome == AppealOutcome.REMANDED:
            case.status = "REMANDED"
        else:
            case.status = "CLOSED"
            case.closed_at = now
        case.version += 1
        await session.flush()
        if outcome == AppealOutcome.AFFIRMED:
            await self._activate_pending_consequences(
                session, principal, actor, case, original, decision, request_id
            )
        elif outcome in {AppealOutcome.MODIFIED, AppealOutcome.OVERTURNED}:
            await self._correct_consequences(
                session,
                principal,
                actor,
                case,
                original,
                decision,
                outcome,
                request_id,
            )
        link_evidence(session, evidence, event.event_id, "appeal_decision", decision_id)
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_APPEAL_DECIDED",
            "ArbitrationDecision",
            decision_id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, decision_id)

    async def finalize_unappealed_sanction(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        sanction_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        sanction = await session.get(Sanction, sanction_id, with_for_update=True)
        if sanction is None:
            raise trust_error("SANCTION_NOT_FOUND", 404)
        case = await self._case(session, sanction.case_id, lock=True)
        decision = await session.get(ArbitrationDecision, sanction.decision_id)
        if decision is None:
            raise trust_error("DECISION_NOT_FOUND", 404)
        payload = {"sanction_id": str(sanction_id), "expected_version": expected_version}
        record, replay = await begin_trust_command(
            session, principal, "TRUST_FINALIZE_SANCTION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(sanction.version, expected_version)
        if sanction.status != "PENDING_APPEAL":
            raise trust_error("SANCTION_NOT_PENDING")
        if datetime.now(UTC) <= sanction.appeal_until:
            raise trust_error("APPEAL_WINDOW_OPEN")
        open_appeal = await session.scalar(
            select(Appeal.id).where(
                Appeal.original_decision_id == decision.id,
                Appeal.status == "SUBMITTED",
            )
        )
        if open_appeal is not None:
            raise trust_error("APPEAL_PENDING")
        actor = await trust_role_actor(session, principal, case.cooperative_id, ARBITRATION_ROLES)
        if actor.person_id == decision.issued_by_member_id:
            raise trust_error("INDEPENDENT_FINALIZER_REQUIRED")
        await self._require_clear_conflict(session, case, "APPEAL", actor.person_id)
        event = await self.journal.append(
            session,
            event_type="sanctions.sanction_finalized",
            aggregate_type="sanction",
            aggregate_id=sanction.id,
            aggregate_version=sanction.version + 1,
            actor=actor,
            payload=payload,
        )
        sanction.status = "ACTIVE"
        sanction.finalized_by_user_id = principal.user_id
        sanction.finalized_by_member_id = actor.person_id
        sanction.finalized_event_id = event.event_id
        sanction.finalized_at = datetime.now(UTC)
        sanction.version += 1
        case.status = "CLOSED"
        case.closed_at = datetime.now(UTC)
        case.version += 1
        await self._append_affirmed_reputation(
            session, principal, actor, case, decision, event.event_id, request_id
        )
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_SANCTION_FINALIZED",
            "Sanction",
            sanction.id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, sanction.id)

    async def create_rehabilitation_plan(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        decision_id: UUID,
        title: str,
        completion_criteria: dict[str, object],
        starts_at: datetime,
        due_at: datetime,
        steps: Sequence[RehabilitationStepDraft],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        decision = await session.get(ArbitrationDecision, decision_id)
        if decision is None:
            raise trust_error("DECISION_NOT_FOUND", 404)
        case = await self._case(session, decision.case_id, lock=False)
        starts = self._utc(starts_at, "REHABILITATION_PERIOD_INVALID")
        due = self._utc(due_at, "REHABILITATION_PERIOD_INVALID")
        if due <= starts or not steps:
            raise trust_error("REHABILITATION_PLAN_INVALID", 422)
        normalized_steps = [
            {
                "sequence": index,
                "description": self._text(item.description, "REHABILITATION_STEP_INVALID", 5_000),
                "completion_criterion": self._text(
                    item.completion_criterion, "REHABILITATION_CRITERION_INVALID", 5_000
                ),
            }
            for index, item in enumerate(steps, start=1)
        ]
        payload = {
            "decision_id": str(decision_id),
            "case_id": str(case.id),
            "title": self._text(title, "REHABILITATION_TITLE_INVALID", 200),
            "completion_criteria": completion_criteria,
            "starts_at": starts.isoformat(),
            "due_at": due.isoformat(),
            "steps": normalized_steps,
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_CREATE_REHABILITATION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        if decision.outcome not in {
            "SUBSTANTIATED",
            "PARTLY_SUBSTANTIATED",
            "AFFIRMED",
            "MODIFIED",
        }:
            raise trust_error("REHABILITATION_DECISION_NOT_ELIGIBLE")
        actor = await trust_role_actor(session, principal, case.cooperative_id, ARBITRATION_ROLES)
        plan_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="rehabilitation.plan_created",
            aggregate_type="rehabilitation_plan",
            aggregate_id=plan_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "plan_id": str(plan_id),
                "subject_member_id": str(case.subject_member_id),
            },
        )
        session.add(
            RehabilitationPlan(
                id=plan_id,
                case_id=case.id,
                decision_id=decision.id,
                subject_member_id=case.subject_member_id,
                title=str(payload["title"]),
                completion_criteria=completion_criteria,
                status="ACTIVE",
                starts_at=starts,
                due_at=due,
                created_by_user_id=principal.user_id,
                created_by_member_id=actor.person_id,
                created_event_id=event.event_id,
                version=1,
            )
        )
        session.add_all(
            [
                RehabilitationStep(
                    id=uuid4(),
                    plan_id=plan_id,
                    sequence=int(str(item["sequence"])),
                    description=str(item["description"]),
                    completion_criterion=str(item["completion_criterion"]),
                    status="PENDING",
                    evidence_refs=[],
                )
                for item in normalized_steps
            ]
        )
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_REHABILITATION_CREATED",
            "RehabilitationPlan",
            plan_id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, plan_id)

    async def complete_rehabilitation_step(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        plan_id: UUID,
        step_id: UUID,
        expected_plan_version: int,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        plan = await session.get(RehabilitationPlan, plan_id, with_for_update=True)
        if plan is None:
            raise trust_error("REHABILITATION_PLAN_NOT_FOUND", 404)
        step = await session.get(RehabilitationStep, step_id, with_for_update=True)
        if step is None or step.plan_id != plan.id:
            raise trust_error("REHABILITATION_STEP_NOT_FOUND", 404)
        case = await self._case(session, plan.case_id, lock=False)
        payload = {
            "plan_id": str(plan_id),
            "step_id": str(step_id),
            "expected_plan_version": expected_plan_version,
            "evidence_ids": [str(value) for value in evidence_ids],
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_COMPLETE_REHABILITATION_STEP", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(plan.version, expected_plan_version)
        if plan.status != "ACTIVE" or step.status != "PENDING":
            raise trust_error("REHABILITATION_STEP_NOT_PENDING")
        actor = await trust_role_actor(session, principal, case.cooperative_id, REPUTATION_ROLES)
        if actor.person_id == plan.subject_member_id:
            raise trust_error("INDEPENDENT_STEP_VERIFIER_REQUIRED")
        evidence = await EvidenceService.require_ready(
            session, case.cooperative_id, evidence_ids, required=True
        )
        refs = evidence_payload(evidence)
        event = await self.journal.append(
            session,
            event_type="rehabilitation.step_completed",
            aggregate_type="rehabilitation_plan",
            aggregate_id=plan.id,
            aggregate_version=plan.version + 1,
            actor=actor,
            payload={**payload, "sequence": step.sequence, "evidence": refs},
        )
        step.status = "COMPLETED"
        step.evidence_refs = refs
        step.completed_by_user_id = principal.user_id
        step.completed_by_member_id = actor.person_id
        step.completed_event_id = event.event_id
        step.completed_at = datetime.now(UTC)
        plan.version += 1
        link_evidence(session, evidence, event.event_id, "rehabilitation_step", step.id)
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_REHABILITATION_STEP_COMPLETED",
            "RehabilitationStep",
            step.id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, step.id)

    async def close_rehabilitation_plan(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        plan_id: UUID,
        expected_version: int,
        context: ReputationContext,
        closure_reason: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> TrustCommandResult:
        plan = await session.get(RehabilitationPlan, plan_id, with_for_update=True)
        if plan is None:
            raise trust_error("REHABILITATION_PLAN_NOT_FOUND", 404)
        case = await self._case(session, plan.case_id, lock=False)
        payload = {
            "plan_id": str(plan_id),
            "expected_version": expected_version,
            "context": context.value,
            "closure_reason": self._text(closure_reason, "CLOSURE_REASON_INVALID", 5_000),
        }
        record, replay = await begin_trust_command(
            session, principal, "TRUST_CLOSE_REHABILITATION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(plan.version, expected_version)
        if plan.status != "ACTIVE":
            raise trust_error("REHABILITATION_PLAN_NOT_ACTIVE")
        pending = await session.scalar(
            select(func.count())
            .select_from(RehabilitationStep)
            .where(
                RehabilitationStep.plan_id == plan.id,
                RehabilitationStep.status == "PENDING",
            )
        )
        if int(pending or 0) != 0:
            raise trust_error("REHABILITATION_STEPS_INCOMPLETE")
        actor = await trust_role_actor(session, principal, case.cooperative_id, ARBITRATION_ROLES)
        if actor.person_id == plan.created_by_member_id:
            raise trust_error("INDEPENDENT_REHABILITATION_CLOSER_REQUIRED")
        await self._require_clear_conflict(session, case, "REHABILITATION", actor.person_id)
        now = datetime.now(UTC)
        event = await self.journal.append(
            session,
            event_type="rehabilitation.plan_completed",
            aggregate_type="rehabilitation_plan",
            aggregate_id=plan.id,
            aggregate_version=plan.version + 1,
            actor=actor,
            payload=payload,
        )
        plan.status = "COMPLETED"
        plan.closed_by_user_id = principal.user_id
        plan.closed_by_member_id = actor.person_id
        plan.closed_event_id = event.event_id
        plan.closure_reason = str(payload["closure_reason"])
        plan.closed_at = now
        plan.version += 1
        await self._append_reputation_event(
            session,
            principal,
            actor,
            case,
            decision_id=plan.decision_id,
            context=context.value,
            classification="REHABILITATION",
            severity=0,
            confidence=Decimal("1"),
            observation_start=plan.starts_at,
            observation_end=now,
            source_event_ids=(plan.created_event_id, event.event_id),
            appeal_state="AFFIRMED",
            status="ACTIVE",
            visibility="COOPERATIVE",
            corrects_event_id=None,
            event_type="reputation.rehabilitation_recorded",
            request_id=request_id,
        )
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_REHABILITATION_COMPLETED",
            "RehabilitationPlan",
            plan.id,
            event.event_id,
            request_id,
        )
        return complete_trust_command(record, event.event_id, plan.id)

    async def _activate_pending_consequences(
        self,
        session: AsyncSession,
        principal: Principal,
        actor: object,
        case: TrustCase,
        original: ArbitrationDecision,
        appeal_decision: ArbitrationDecision,
        request_id: UUID | None,
    ) -> None:
        from cooperative_clearing.modules.journal.application.service import ActorClaim

        if not isinstance(actor, ActorClaim):
            raise TypeError("actor must be ActorClaim")
        sanctions = list(
            (
                await session.execute(
                    select(Sanction)
                    .where(Sanction.decision_id == original.id, Sanction.status == "PENDING_APPEAL")
                    .with_for_update()
                )
            ).scalars()
        )
        for sanction in sanctions:
            event = await self.journal.append(
                session,
                event_type="sanctions.sanction_finalized",
                aggregate_type="sanction",
                aggregate_id=sanction.id,
                aggregate_version=sanction.version + 1,
                actor=actor,
                payload={
                    "sanction_id": str(sanction.id),
                    "appeal_decision_id": str(appeal_decision.id),
                    "appeal_outcome": "AFFIRMED",
                },
            )
            sanction.status = "ACTIVE"
            sanction.finalized_by_user_id = principal.user_id
            sanction.finalized_by_member_id = actor.person_id
            sanction.finalized_event_id = event.event_id
            sanction.finalized_at = datetime.now(UTC)
            sanction.version += 1
            await audit_trust_action(
                session,
                principal,
                case.cooperative_id,
                "TRUST_SANCTION_FINALIZED",
                "Sanction",
                sanction.id,
                event.event_id,
                request_id,
            )
        await self._append_affirmed_reputation(
            session,
            principal,
            actor,
            case,
            original,
            appeal_decision.issued_event_id,
            request_id,
        )

    async def _correct_consequences(
        self,
        session: AsyncSession,
        principal: Principal,
        actor: object,
        case: TrustCase,
        original: ArbitrationDecision,
        appeal_decision: ArbitrationDecision,
        outcome: AppealOutcome,
        request_id: UUID | None,
    ) -> None:
        from cooperative_clearing.modules.journal.application.service import ActorClaim

        if not isinstance(actor, ActorClaim):
            raise TypeError("actor must be ActorClaim")
        sanctions = list(
            (
                await session.execute(
                    select(Sanction)
                    .where(
                        Sanction.decision_id == original.id,
                        Sanction.status.in_(("PROPOSED", "PENDING_APPEAL", "ACTIVE")),
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        for sanction in sanctions:
            event = await self.journal.append(
                session,
                event_type="sanctions.sanction_revoked",
                aggregate_type="sanction",
                aggregate_id=sanction.id,
                aggregate_version=sanction.version + 1,
                actor=actor,
                payload={
                    "sanction_id": str(sanction.id),
                    "appeal_decision_id": str(appeal_decision.id),
                    "appeal_outcome": outcome.value,
                },
            )
            sanction.status = "REVOKED"
            sanction.revoked_event_id = event.event_id
            sanction.revocation_reason = f"Appeal outcome {outcome.value}"
            sanction.version += 1
            await audit_trust_action(
                session,
                principal,
                case.cooperative_id,
                "TRUST_SANCTION_REVOKED",
                "Sanction",
                sanction.id,
                event.event_id,
                request_id,
            )
        measures = list(
            (
                await session.execute(
                    select(ProtectiveMeasure)
                    .where(
                        ProtectiveMeasure.case_id == case.id, ProtectiveMeasure.status == "ACTIVE"
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        for measure in measures:
            event = await self.journal.append(
                session,
                event_type="sanctions.protective_measure_revoked",
                aggregate_type="protective_measure",
                aggregate_id=measure.id,
                aggregate_version=measure.version + 1,
                actor=actor,
                payload={
                    "measure_id": str(measure.id),
                    "appeal_decision_id": str(appeal_decision.id),
                    "appeal_outcome": outcome.value,
                },
            )
            measure.status = "REVOKED"
            measure.lifted_by_user_id = principal.user_id
            measure.lifted_by_member_id = actor.person_id
            measure.lifted_event_id = event.event_id
            measure.lift_reason = f"Appeal outcome {outcome.value}"
            measure.lifted_at = datetime.now(UTC)
            measure.version += 1
        plans = list(
            (
                await session.execute(
                    select(RehabilitationPlan)
                    .where(
                        RehabilitationPlan.case_id == case.id, RehabilitationPlan.status == "ACTIVE"
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        for plan in plans:
            event = await self.journal.append(
                session,
                event_type="rehabilitation.plan_cancelled",
                aggregate_type="rehabilitation_plan",
                aggregate_id=plan.id,
                aggregate_version=plan.version + 1,
                actor=actor,
                payload={
                    "plan_id": str(plan.id),
                    "appeal_decision_id": str(appeal_decision.id),
                    "appeal_outcome": outcome.value,
                },
            )
            plan.status = "CANCELLED"
            plan.closed_by_user_id = principal.user_id
            plan.closed_by_member_id = actor.person_id
            plan.closed_event_id = event.event_id
            plan.closure_reason = f"Appeal outcome {outcome.value}"
            plan.closed_at = datetime.now(UTC)
            plan.version += 1
        originals = list(
            (
                await session.execute(
                    select(ReputationEvent).where(
                        ReputationEvent.decision_id == original.id,
                        ReputationEvent.classification != "CORRECTION",
                    )
                )
            ).scalars()
        )
        for item in originals:
            await self._append_reputation_event(
                session,
                principal,
                actor,
                case,
                decision_id=appeal_decision.id,
                context=item.context,
                classification="CORRECTION",
                severity=0,
                confidence=Decimal("1"),
                observation_start=appeal_decision.issued_at,
                observation_end=appeal_decision.issued_at,
                source_event_ids=(item.recorded_event_id, appeal_decision.issued_event_id),
                appeal_state="OVERTURNED",
                status="ACTIVE",
                visibility=item.visibility,
                corrects_event_id=item.id,
                event_type="reputation.event_corrected",
                request_id=request_id,
            )

    async def _append_affirmed_reputation(
        self,
        session: AsyncSession,
        principal: Principal,
        actor: object,
        case: TrustCase,
        original: ArbitrationDecision,
        source_event_id: UUID,
        request_id: UUID | None,
    ) -> None:
        from cooperative_clearing.modules.journal.application.service import ActorClaim

        if not isinstance(actor, ActorClaim):
            raise TypeError("actor must be ActorClaim")
        originals = list(
            (
                await session.execute(
                    select(ReputationEvent).where(
                        ReputationEvent.decision_id == original.id,
                        ReputationEvent.status == "DISPUTED",
                        ReputationEvent.classification != "CORRECTION",
                    )
                )
            ).scalars()
        )
        for item in originals:
            await self._append_reputation_event(
                session,
                principal,
                actor,
                case,
                decision_id=original.id,
                context=item.context,
                classification=item.classification,
                severity=item.severity,
                confidence=item.confidence,
                observation_start=item.observation_start,
                observation_end=item.observation_end,
                source_event_ids=(item.recorded_event_id, source_event_id),
                appeal_state="AFFIRMED",
                status="ACTIVE",
                visibility=item.visibility,
                corrects_event_id=None,
                event_type="reputation.event_activated",
                request_id=request_id,
            )

    async def _append_reputation_event(
        self,
        session: AsyncSession,
        principal: Principal,
        actor: object,
        case: TrustCase,
        *,
        decision_id: UUID,
        context: str,
        classification: str,
        severity: int,
        confidence: Decimal,
        observation_start: datetime,
        observation_end: datetime,
        source_event_ids: Sequence[UUID],
        appeal_state: str,
        status: str,
        visibility: str,
        corrects_event_id: UUID | None,
        event_type: str,
        request_id: UUID | None,
    ) -> ReputationEvent:
        from cooperative_clearing.modules.journal.application.service import ActorClaim

        if not isinstance(actor, ActorClaim):
            raise TypeError("actor must be ActorClaim")
        item_id = uuid4()
        decision = await session.get(ArbitrationDecision, decision_id)
        policy_version = decision.policy_version if decision else "UNKNOWN"
        event = await self.journal.append(
            session,
            event_type=event_type,
            aggregate_type="reputation_event",
            aggregate_id=item_id,
            aggregate_version=1,
            actor=actor,
            payload={
                "reputation_event_id": str(item_id),
                "case_id": str(case.id),
                "decision_id": str(decision_id),
                "subject_member_id": str(case.subject_member_id),
                "context": context,
                "classification": classification,
                "severity": severity,
                "confidence": str(confidence),
                "source_event_ids": [str(value) for value in source_event_ids],
                "appeal_state": appeal_state,
                "status": status,
                "corrects_event_id": str(corrects_event_id) if corrects_event_id else None,
                "policy_version": policy_version,
            },
        )
        item = ReputationEvent(
            id=item_id,
            cooperative_id=case.cooperative_id,
            case_id=case.id,
            decision_id=decision_id,
            subject_member_id=case.subject_member_id,
            context=context,
            classification=classification,
            severity=severity,
            confidence=confidence,
            observation_start=observation_start,
            observation_end=observation_end,
            source_event_ids=[str(value) for value in source_event_ids],
            evidence_refs=[],
            appeal_state=appeal_state,
            status=status,
            visibility=visibility,
            policy_version=policy_version,
            corrects_event_id=corrects_event_id,
            recorded_by_user_id=principal.user_id,
            recorded_by_member_id=actor.person_id,
            recorded_event_id=event.event_id,
        )
        session.add(item)
        await audit_trust_action(
            session,
            principal,
            case.cooperative_id,
            "TRUST_REPUTATION_PROJECTION_EVENT_RECORDED",
            "ReputationEvent",
            item_id,
            event.event_id,
            request_id,
        )
        return item

    @staticmethod
    async def _require_clear_conflict(
        session: AsyncSession, case: TrustCase, stage: str, member_id: UUID
    ) -> None:
        declaration = await session.scalar(
            select(ConflictDeclaration).where(
                ConflictDeclaration.case_id == case.id,
                ConflictDeclaration.stage == stage,
                ConflictDeclaration.member_id == member_id,
            )
        )
        if declaration is None or declaration.assessment != "CLEAR":
            raise trust_error("CLEAR_CONFLICT_DECLARATION_REQUIRED")

    @staticmethod
    async def _active_policy(session: AsyncSession, cooperative_id: UUID) -> TrustPolicy:
        policy = await session.scalar(
            select(TrustPolicy).where(
                TrustPolicy.cooperative_id == cooperative_id,
                TrustPolicy.status == "ACTIVE",
            )
        )
        if policy is None:
            raise trust_error("ACTIVE_POLICY_REQUIRED")
        return policy

    @staticmethod
    async def _case(session: AsyncSession, case_id: UUID, *, lock: bool) -> TrustCase:
        case = await session.get(TrustCase, case_id, with_for_update=lock)
        if case is None:
            raise trust_error("CASE_NOT_FOUND", 404)
        return case

    @staticmethod
    async def _eligible_member(
        session: AsyncSession, cooperative_id: UUID, member_id: UUID
    ) -> None:
        member = await session.get(Member, member_id)
        membership = await session.scalar(
            select(Membership.id).where(
                Membership.cooperative_id == cooperative_id,
                Membership.member_id == member_id,
                Membership.status == "ACTIVE",
            )
        )
        if member is None or member.status != "ACTIVE" or membership is None:
            raise trust_error("MEMBER_NOT_ELIGIBLE", 422)

    @staticmethod
    async def _validate_source_events(
        session: AsyncSession, cooperative_id: UUID, event_ids: Sequence[UUID]
    ) -> None:
        unique_ids = tuple(dict.fromkeys(event_ids))
        if not unique_ids:
            raise trust_error("SOURCE_EVENT_REQUIRED", 422)
        count = await session.scalar(
            select(func.count())
            .select_from(SignedEvent)
            .where(
                SignedEvent.event_id.in_(unique_ids),
                SignedEvent.actor_organization_id == cooperative_id,
            )
        )
        if int(count or 0) != len(unique_ids):
            raise trust_error("SOURCE_EVENT_INVALID", 422)

    @staticmethod
    async def _lock_cooperative(session: AsyncSession, cooperative_id: UUID) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"cooperative-clearing:trust:{cooperative_id}"},
        )

    @staticmethod
    def _policy_label(policy: TrustPolicy) -> str:
        return f"{policy.policy_code}/{policy.semantic_version}/v{policy.policy_version}"

    @staticmethod
    def _version(current: int, expected: int) -> None:
        if current != expected:
            raise trust_error("VERSION_CONFLICT")

    @staticmethod
    def _text(value: str, code: str, maximum: int) -> str:
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > maximum:
            raise trust_error(code, 422)
        return normalized

    @classmethod
    def _code(cls, value: str, code: str, maximum: int) -> str:
        normalized = cls._text(value, code, maximum).upper()
        if not normalized.isascii() or not all(
            character.isalnum() or character in {"_", "-", "."} for character in normalized
        ):
            raise trust_error(code, 422)
        return normalized

    @staticmethod
    def _choice(value: str, allowed: set[str], code: str) -> str:
        normalized = value.strip().upper()
        if normalized not in allowed:
            raise trust_error(code, 422)
        return normalized

    @staticmethod
    def _utc(value: datetime, code: str) -> datetime:
        if value.utcoffset() is None:
            raise trust_error(code, 422)
        return value.astimezone(UTC)
