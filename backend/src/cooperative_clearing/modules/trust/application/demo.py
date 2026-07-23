"""Deterministic appeal correction demo through production trust commands."""

import hashlib
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.infrastructure.models import EvidenceBlob
from cooperative_clearing.modules.trust.application.service import (
    RehabilitationStepDraft,
    TrustService,
)
from cooperative_clearing.modules.trust.domain.types import (
    AppealOutcome,
    ConflictAssessment,
    DecisionOutcome,
    DecisionStage,
    FaultClass,
    ReputationClassification,
    ReputationContext,
)
from cooperative_clearing.modules.trust.infrastructure.models import (
    Appeal,
    ProtectiveMeasure,
    RehabilitationPlan,
    ReputationEvent,
    Sanction,
    TrustCase,
    TrustPolicy,
)
from cooperative_clearing.shared.core.config import Settings


async def seed_demo_trust(session: AsyncSession, settings: Settings) -> None:
    cooperative_id = stable_id("cooperative", settings.node_code)
    anna_id = stable_id("member", "demo-member-anna")
    registrar = _bootstrap_principal(
        "registrar",
        anna_id,
        cooperative_id,
        (("bootstrap-role", "registrar:COOPERATIVE_ADMIN", RoleCode.COOPERATIVE_ADMIN),),
    )
    auditor = _bootstrap_principal(
        "auditor",
        stable_id("member", "demo-member-pavel"),
        cooperative_id,
        (("bootstrap-role", "auditor:AUDITOR", RoleCode.AUDITOR),),
    )
    original_arbitrator = _bootstrap_principal(
        "security",
        stable_id("member", "demo-member-elena"),
        cooperative_id,
        (
            ("demo-role", "security:RISK_ADMIN", RoleCode.RISK_ADMIN),
            ("demo-role", "security:ARBITRATOR", RoleCode.ARBITRATOR),
        ),
    )
    appeal_arbitrator = Principal(
        user_id=stable_id("demo-user", "nina-arbitrator"),
        session_id=stable_id("demo-session", "nina-arbitrator"),
        login="demo-arbitrator",
        member_id=stable_id("member", "demo-member-nina"),
        must_change_password=False,
        roles=(
            RoleGrant(
                stable_id("demo-role", "demo-arbitrator:ARBITRATOR"),
                RoleCode.ARBITRATOR,
                None,
            ),
        ),
    )

    reporter_evidence = await _evidence(
        session,
        settings,
        registrar,
        cooperative_id,
        "demo-trust-self-report-v1",
        "Self-report and original operational record for a suspected late obligation.",
    )
    response_evidence = await _evidence(
        session,
        settings,
        registrar,
        cooperative_id,
        "demo-trust-response-v1",
        "Participant response showing the source timestamp was interpreted incorrectly.",
    )
    decision_evidence = await _evidence(
        session,
        settings,
        original_arbitrator,
        cooperative_id,
        "demo-trust-original-decision-v1",
        "Original review worksheet later found to use the wrong time zone.",
    )
    reputation_evidence = await _evidence(
        session,
        settings,
        auditor,
        cooperative_id,
        "demo-trust-reputation-v1",
        "Independent recording note for the disputed contextual event.",
    )
    appeal_evidence = await _evidence(
        session,
        settings,
        registrar,
        cooperative_id,
        "demo-trust-appeal-v1",
        "Authoritative local timestamp and custody receipt submitted on appeal.",
    )
    appeal_decision_evidence = await _evidence(
        session,
        settings,
        auditor,
        cooperative_id,
        "demo-trust-appeal-decision-v1",
        "Independent appeal packet verification confirming the timestamp error.",
    )
    source_blob = await session.get(EvidenceBlob, reporter_evidence)
    if source_blob is None or source_blob.completed_event_id is None:
        raise RuntimeError("demo trust source event was not created")

    service = TrustService(settings)
    policy_result = await service.propose_policy(
        session,
        principal=registrar,
        cooperative_id=cooperative_id,
        semantic_version="1.0.0-DEMO",
        appeal_window_seconds=1_209_600,
        max_protective_seconds=2_592_000,
        panel_quorum=1,
        terms={
            "scope": "DEMO_ONLY",
            "open_decisions": ["OD-025", "OD-029", "OD-030", "OD-031", "OD-032"],
            "no_automatic_liability_execution": True,
            "no_universal_reputation_score": True,
        },
        idempotency_key="demo-trust-policy-propose-v1",
        request_id=None,
    )
    policy = await session.get(TrustPolicy, policy_result.object_id)
    if policy is None:
        raise RuntimeError("demo trust policy was not created")
    await service.approve_policy(
        session,
        principal=auditor,
        policy_id=policy.id,
        expected_version=1,
        idempotency_key="demo-trust-policy-approve-v1",
        request_id=None,
    )

    case_result = await service.open_case(
        session,
        principal=registrar,
        cooperative_id=cooperative_id,
        case_reference="DEMO-TRUST-APPEAL-001",
        subject_member_id=anna_id,
        claimant_member_id=anna_id,
        source_type="OTHER",
        source_reference="DEMO-OBLIGATION-TIMESTAMP-001",
        source_event_ids=(source_blob.completed_event_id,),
        evidence_ids=(reporter_evidence,),
        summary="Self-reported possible delay with disputed timestamp",
        facts="The first review treated a local timestamp as UTC and classified the action late.",
        requested_outcome=(
            "Verify the timestamp, correct any measure, and preserve the audit trail."
        ),
        confidentiality="NORMAL",
        idempotency_key="demo-trust-case-open-v1",
        request_id=None,
    )
    case = await session.get(TrustCase, case_result.object_id)
    if case is None:
        raise RuntimeError("demo trust case was not created")
    await service.record_response(
        session,
        principal=registrar,
        case_id=case.id,
        expected_version=1,
        response_text=(
            "The receipt used local civil time; the obligation was completed before its deadline."
        ),
        evidence_ids=(response_evidence,),
        idempotency_key="demo-trust-case-response-v1",
        request_id=None,
    )
    now = case.opened_at
    measure_result = await service.impose_protective_measure(
        session,
        principal=original_arbitrator,
        case_id=case.id,
        expected_case_version=2,
        measure_type="ADDITIONAL_REVIEW",
        scope={"blocked_actions": ["AUTO_LIMIT_INCREASE"], "case_only": True},
        rationale="Pause automatic limit increases while the timestamp is reviewed.",
        expires_at=now + timedelta(days=7),
        review_at=now + timedelta(days=1),
        idempotency_key="demo-trust-protective-measure-v1",
        request_id=None,
    )
    await service.mark_case_ready(
        session,
        principal=auditor,
        case_id=case.id,
        expected_version=3,
        review_note="Both evidence packets are readable and the response has been disclosed.",
        idempotency_key="demo-trust-case-ready-v1",
        request_id=None,
    )
    await service.declare_conflict(
        session,
        principal=original_arbitrator,
        case_id=case.id,
        stage=DecisionStage.ORIGINAL,
        assessment=ConflictAssessment.CLEAR,
        relationship="No personal, economic, or operational relationship with the subject.",
        rationale="Registry and responsibility assignments were checked before review.",
        idempotency_key="demo-trust-original-conflict-v1",
        request_id=None,
    )
    original_result = await service.issue_original_decision(
        session,
        principal=original_arbitrator,
        case_id=case.id,
        expected_case_version=4,
        outcome=DecisionOutcome.SUBSTANTIATED,
        standard_of_proof="Preponderance of verified operational evidence",
        fault_class=FaultClass.GOOD_FAITH_ERROR,
        causal_findings={
            "source_timestamp": "interpreted_as_utc",
            "causal_link": "apparent_delay",
            "known_uncertainty": "local_timezone_not_verified",
        },
        established_loss=Decimal("0"),
        reasoning="The first panel classified the timestamp as UTC and found a non-damaging delay.",
        consequence_spec={"warning": True, "automatic_liability_execution": False},
        evidence_ids=(decision_evidence,),
        idempotency_key="demo-trust-original-decision-v1",
        request_id=None,
    )
    sanction_result = await service.propose_sanction(
        session,
        principal=original_arbitrator,
        decision_id=original_result.object_id,
        measure_type="WARNING",
        severity="LOW",
        scope={"context": "OBLIGATION", "blocked_actions": []},
        rationale="Document the apparent delay without moving shares or restricting basic access.",
        starts_at=now,
        expires_at=now + timedelta(days=30),
        review_at=now + timedelta(days=14),
        idempotency_key="demo-trust-sanction-propose-v1",
        request_id=None,
    )
    await service.record_reputation_event(
        session,
        principal=auditor,
        decision_id=original_result.object_id,
        context=ReputationContext.OBLIGATION,
        classification=ReputationClassification.BREACH,
        severity=1,
        confidence=Decimal("0.65"),
        observation_start=now - timedelta(days=1),
        observation_end=now,
        source_event_ids=(),
        evidence_ids=(reputation_evidence,),
        visibility="COOPERATIVE",
        idempotency_key="demo-trust-reputation-disputed-v1",
        request_id=None,
    )
    plan_result = await service.create_rehabilitation_plan(
        session,
        principal=original_arbitrator,
        decision_id=original_result.object_id,
        title="Timestamp handling review",
        completion_criteria={"requires": "verified local-time handling refresher"},
        starts_at=now,
        due_at=now + timedelta(days=30),
        steps=(
            RehabilitationStepDraft(
                "Review timestamp notation with an auditor.",
                "Signed training record references the approved notation rule.",
            ),
        ),
        idempotency_key="demo-trust-rehabilitation-plan-v1",
        request_id=None,
    )
    appeal_result = await service.submit_appeal(
        session,
        principal=registrar,
        case_id=case.id,
        original_decision_id=original_result.object_id,
        sanction_id=sanction_result.object_id,
        expected_case_version=5,
        grounds=(
            "The original decision used the wrong time zone despite the custody receipt metadata."
        ),
        evidence_ids=(appeal_evidence,),
        idempotency_key="demo-trust-appeal-submit-v1",
        request_id=None,
    )
    await service.declare_conflict(
        session,
        principal=appeal_arbitrator,
        case_id=case.id,
        stage=DecisionStage.APPEAL,
        assessment=ConflictAssessment.CLEAR,
        relationship=(
            "No participation in intake, review, protective measure, or original decision."
        ),
        rationale=(
            "Separate account, member, and role assignment verified against the case history."
        ),
        idempotency_key="demo-trust-appeal-conflict-v1",
        request_id=None,
    )
    await service.decide_appeal(
        session,
        principal=appeal_arbitrator,
        appeal_id=appeal_result.object_id,
        expected_case_version=6,
        outcome=AppealOutcome.OVERTURNED,
        standard_of_proof="Clear verified contradiction in the original timestamp interpretation",
        causal_findings={
            "source_timestamp": "verified_local_time",
            "actual_delay": False,
            "original_error": "timezone_conversion",
        },
        reasoning=(
            "The authoritative receipt proves timely performance; "
            "the original finding is cancelled."
        ),
        consequence_spec={
            "revoke_sanction": True,
            "revoke_protective_measure": True,
            "cancel_rehabilitation": True,
            "append_reputation_correction": True,
        },
        evidence_ids=(appeal_decision_evidence,),
        idempotency_key="demo-trust-appeal-decision-v1",
        request_id=None,
    )
    await session.flush()

    appeal = await session.get(Appeal, appeal_result.object_id)
    measure = await session.get(ProtectiveMeasure, measure_result.object_id)
    sanction = await session.get(Sanction, sanction_result.object_id)
    plan = await session.get(RehabilitationPlan, plan_result.object_id)
    reputation = list(
        (
            await session.execute(
                select(ReputationEvent)
                .where(ReputationEvent.case_id == case.id)
                .order_by(ReputationEvent.created_at, ReputationEvent.id)
            )
        ).scalars()
    )
    reputation_by_class = {item.classification: item for item in reputation}
    breach = reputation_by_class.get("BREACH")
    correction = reputation_by_class.get("CORRECTION")
    if (
        case.status != "CLOSED"
        or appeal is None
        or appeal.outcome != "OVERTURNED"
        or measure is None
        or measure.status != "REVOKED"
        or sanction is None
        or sanction.status != "REVOKED"
        or plan is None
        or plan.status != "CANCELLED"
        or len(reputation) != 2
        or breach is None
        or breach.status != "DISPUTED"
        or correction is None
        or correction.status != "ACTIVE"
        or correction.corrects_event_id != breach.id
    ):
        raise RuntimeError(
            "demo trust appeal correction flow was not completed: "
            f"case={case.status}, appeal={appeal.outcome if appeal else None}, "
            f"measure={measure.status if measure else None}, "
            f"sanction={sanction.status if sanction else None}, "
            f"plan={plan.status if plan else None}, "
            f"reputation={[(item.classification, item.status) for item in reputation]}"
        )


async def _evidence(
    session: AsyncSession,
    settings: Settings,
    principal: Principal,
    cooperative_id: UUID,
    key: str,
    content_text: str,
) -> UUID:
    content = content_text.encode("utf-8")
    service = EvidenceService(settings)
    intent = await service.create_intent(
        session,
        principal=principal,
        cooperative_id=cooperative_id,
        expected_sha256=hashlib.sha256(content).hexdigest(),
        expected_size=len(content),
        mime_type="text/plain",
        kind="TRUST_CASE",
        original_name=f"{key}.txt",
        access_scope="COOPERATIVE",
        retention_until=None,
        idempotency_key=f"{key}-intent",
        request_id=None,
    )

    async def chunks() -> AsyncIterator[bytes]:
        yield content

    await service.store_content(
        session,
        principal=principal,
        evidence_id=intent.object_id,
        chunks=chunks(),
        request_id=None,
    )
    return intent.object_id


def _bootstrap_principal(
    login: str,
    member_id: UUID,
    cooperative_id: UUID,
    roles: tuple[tuple[str, str, RoleCode], ...],
) -> Principal:
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=stable_id("demo-session", login),
        login=login,
        member_id=member_id,
        must_change_password=False,
        roles=tuple(
            RoleGrant(
                stable_id(kind, value),
                role,
                None if role in {RoleCode.AUDITOR, RoleCode.ARBITRATOR} else cooperative_id,
            )
            for kind, value, role in roles
        ),
    )
