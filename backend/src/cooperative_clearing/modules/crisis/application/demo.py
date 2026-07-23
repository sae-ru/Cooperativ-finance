"""Deterministic field drill for reserves, crisis authority, and rationing."""

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.crisis.application.service import CrisisService
from cooperative_clearing.modules.crisis.domain.types import (
    CrisisCapability,
    CrisisType,
    QualityStatus,
    RationFormula,
)
from cooperative_clearing.modules.crisis.infrastructure.models import (
    CrisisMandate,
    CrisisPaperForm,
    CrisisReport,
    RationingAllocation,
    RationingPlan,
    RationingRule,
    ReserveTarget,
)
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.shared.core.config import Settings


async def seed_demo_crisis(session: AsyncSession, settings: Settings) -> None:
    cooperative_id = stable_id("cooperative", settings.node_code)
    existing = await session.scalar(
        select(CrisisReport.id)
        .join(CrisisMandate, CrisisMandate.id == CrisisReport.mandate_id)
        .where(CrisisMandate.mandate_code == "DEMO-CRISIS-001")
    )
    if existing is not None:
        return

    elena_id = stable_id("member", "demo-member-elena")
    pavel_id = stable_id("member", "demo-member-pavel")
    anna_id = stable_id("member", "demo-member-anna")
    nina_id = stable_id("member", "demo-member-nina")
    operator = _principal(
        "security",
        elena_id,
        cooperative_id,
        (("demo-role", "security:CRISIS_OPERATOR", RoleCode.CRISIS_OPERATOR),),
    )
    controller = _principal(
        "auditor",
        pavel_id,
        cooperative_id,
        (
            ("demo-role", "auditor:CRISIS_CONTROLLER", RoleCode.CRISIS_CONTROLLER),
            ("demo-role", "auditor:INVENTORY_CONTROLLER", RoleCode.INVENTORY_CONTROLLER),
            ("bootstrap-role", "auditor:AUDITOR", RoleCode.AUDITOR),
        ),
    )
    reviewer = _principal(
        "registrar",
        anna_id,
        cooperative_id,
        (
            ("demo-role", "registrar:CRISIS_CONTROLLER", RoleCode.CRISIS_CONTROLLER),
            ("bootstrap-role", "registrar:COOPERATIVE_ADMIN", RoleCode.COOPERATIVE_ADMIN),
        ),
    )
    service = CrisisService(settings)

    snapshot_evidence = await _evidence(
        session,
        settings,
        controller,
        cooperative_id,
        "demo-reserve-count-v1",
        "Signed physical count: fifty kilograms of verified cabbage, no committed quantity.",
    )
    mandate_evidence = await _evidence(
        session,
        settings,
        operator,
        cooperative_id,
        "demo-crisis-evidence-v1",
        "Field drill evidence: simulated payment failure and critical food distribution window.",
    )
    issuance_evidence = await _evidence(
        session,
        settings,
        operator,
        cooperative_id,
        "demo-ration-issuance-v1",
        "Recipient witness confirms issue of the reserved five kilogram ration.",
    )

    target_result = await service.propose_reserve_target(
        session,
        principal=operator,
        cooperative_id=cooperative_id,
        resource_code="CABBAGE",
        resource_name="Cabbage reserve",
        unit_code="KG",
        target_quantity=Decimal("100"),
        critical_minimum=Decimal("20"),
        warning_coverage_days=Decimal("10"),
        critical_coverage_days=Decimal("3"),
        max_snapshot_age_hours=24,
        terms={"demo_only": True, "verified_stock_only": True},
        idempotency_key="demo-crisis-reserve-target-propose-v1",
        request_id=None,
    )
    target = await session.get(ReserveTarget, target_result.object_id)
    if target is None:
        raise RuntimeError("demo reserve target was not created")
    await service.approve_reserve_target(
        session,
        principal=controller,
        target_id=target.id,
        expected_version=1,
        idempotency_key="demo-crisis-reserve-target-approve-v1",
        request_id=None,
    )
    now = datetime.now(UTC)
    await service.record_reserve_snapshot(
        session,
        principal=controller,
        target_id=target.id,
        physical_verified_quantity=Decimal("50"),
        committed_quantity=Decimal("0"),
        consumption_rate_per_day=Decimal("10"),
        expiring_quantity=Decimal("5"),
        quality_status=QualityStatus.ACCEPTED,
        confidence=Decimal("0.95"),
        observed_at=now,
        evidence_ids=(snapshot_evidence,),
        idempotency_key="demo-crisis-reserve-snapshot-v1",
        request_id=None,
    )
    mandate_result = await service.propose_mandate(
        session,
        principal=operator,
        cooperative_id=cooperative_id,
        mandate_code="DEMO-CRISIS-001",
        crisis_type=CrisisType.PAYMENT_FAILURE,
        scope_payload={"territory": "demo-node", "resources": ["CABBAGE"], "demo_only": True},
        capabilities=(
            CrisisCapability.ENABLE_RATIONING,
            CrisisCapability.ENABLE_PAPER_FORMS,
            CrisisCapability.ENHANCED_AUDIT,
        ),
        evidence_ids=(mandate_evidence,),
        rationale="A bounded drill verifies that essential distribution survives loss of payments.",
        exit_criteria="All drill allocations and paper forms are reconciled.",
        safe_state=(
            "Rationing and paper-form authority stop automatically; "
            "ordinary reserve monitoring remains."
        ),
        starts_at=now - timedelta(minutes=1),
        review_at=now + timedelta(hours=6),
        expires_at=now + timedelta(hours=24),
        maximum_end_at=now + timedelta(hours=48),
        idempotency_key="demo-crisis-mandate-propose-v1",
        request_id=None,
    )
    mandate = await session.get(CrisisMandate, mandate_result.object_id)
    if mandate is None:
        raise RuntimeError("demo crisis mandate was not created")
    await service.activate_mandate(
        session,
        principal=controller,
        mandate_id=mandate.id,
        expected_version=1,
        terms_hash=mandate.terms_hash,
        idempotency_key="demo-crisis-mandate-activate-v1",
        request_id=None,
    )
    rule_result = await service.propose_rationing_rule(
        session,
        principal=operator,
        mandate_id=mandate.id,
        target_id=target.id,
        formula=RationFormula.EQUAL_PER_MEMBER,
        eligibility_policy={"active_membership": True, "demo_selected_population": True},
        protected_minimum=Decimal("2"),
        maximum_per_member=Decimal("5"),
        period_hours=12,
        idempotency_key="demo-crisis-ration-rule-propose-v1",
        request_id=None,
    )
    rule = await session.get(RationingRule, rule_result.object_id)
    if rule is None:
        raise RuntimeError("demo rationing rule was not created")
    await service.approve_rationing_rule(
        session,
        principal=controller,
        rule_id=rule.id,
        expected_version=1,
        terms_hash=rule.terms_hash,
        idempotency_key="demo-crisis-ration-rule-approve-v1",
        request_id=None,
    )
    plan_result = await service.preview_rationing_plan(
        session,
        principal=operator,
        rule_id=rule.id,
        eligible_members=((nina_id, 1),),
        idempotency_key="demo-crisis-ration-preview-v1",
        request_id=None,
    )
    plan = await session.get(RationingPlan, plan_result.object_id)
    if plan is None:
        raise RuntimeError("demo rationing plan was not created")
    await service.confirm_rationing_plan(
        session,
        principal=controller,
        plan_id=plan.id,
        expected_version=1,
        allocations_hash=plan.allocations_hash,
        idempotency_key="demo-crisis-ration-confirm-v1",
        request_id=None,
    )
    allocation = await session.scalar(
        select(RationingAllocation).where(RationingAllocation.plan_id == plan.id)
    )
    if allocation is None:
        raise RuntimeError("demo rationing allocation was not created")
    await service.issue_ration(
        session,
        principal=operator,
        allocation_id=allocation.id,
        acknowledgement="Witnessed issue; no debt, vote, score, or reciprocal duty is created.",
        evidence_ids=(issuance_evidence,),
        idempotency_key="demo-crisis-ration-issue-v1",
        request_id=None,
    )
    form_result = await service.issue_paper_form(
        session,
        principal=operator,
        mandate_id=mandate.id,
        serial_number="DEMO-PAPER-001",
        form_type="INCIDENT",
        assigned_to_member_id=anna_id,
        expires_at=now + timedelta(hours=12),
        idempotency_key="demo-crisis-paper-issue-v1",
        request_id=None,
    )
    paper_form = await session.get(CrisisPaperForm, form_result.object_id)
    if paper_form is None:
        raise RuntimeError("demo paper form was not created")
    await service.record_paper_form(
        session,
        principal=controller,
        form_id=paper_form.id,
        checksum=paper_form.checksum,
        payload={
            "incident": "field drill",
            "outcome": "reconciled",
            "paper_original_retained": True,
        },
        idempotency_key="demo-crisis-paper-record-v1",
        request_id=None,
    )
    await service.review_mandate(
        session,
        principal=reviewer,
        mandate_id=mandate.id,
        expected_version=2,
        decision="CONTINUE",
        facts_payload={"issued_allocations": 1, "open_incidents": 0},
        rationale=(
            "Independent drill review confirms controls and permits completion "
            "within the original expiry."
        ),
        new_review_at=now + timedelta(hours=12),
        new_expires_at=None,
        idempotency_key="demo-crisis-review-v1",
        request_id=None,
    )
    await service.close_mandate(
        session,
        principal=reviewer,
        mandate_id=mandate.id,
        expected_version=3,
        reconciliation_note=(
            "Inventory input, ration issue, paper form, actors, and signed events are reconciled."
        ),
        corrective_actions=("Repeat the field drill after the next release.",),
        idempotency_key="demo-crisis-close-v1",
        request_id=None,
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
        kind="SOLIDARITY_AID",
        original_name=f"{key}.txt",
        access_scope="RESTRICTED",
        retention_until=None,
        idempotency_key=f"{key}-intent",
        request_id=None,
    )

    async def chunks() -> AsyncIterator[bytes]:
        yield content

    await service.store_content(
        session, principal=principal, evidence_id=intent.object_id, chunks=chunks(), request_id=None
    )
    return intent.object_id


def _principal(
    login: str, member_id: UUID, cooperative_id: UUID, roles: tuple[tuple[str, str, RoleCode], ...]
) -> Principal:
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=stable_id("demo-session", f"{login}-crisis"),
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
