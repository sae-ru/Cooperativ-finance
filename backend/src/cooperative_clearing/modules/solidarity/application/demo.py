"""Deterministic end-to-end demo for voluntary solidarity aid."""

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.solidarity.application.service import SolidarityService
from cooperative_clearing.modules.solidarity.domain.types import (
    ContributionForm,
    DeliveryAttestorKind,
    NeedCategory,
    PrivacyScope,
    ResidueRule,
)
from cooperative_clearing.modules.solidarity.infrastructure.models import (
    AidAllocation,
    AidApplication,
    AidCampaign,
    CampaignReport,
    Contribution,
    Pledge,
    SolidarityComplaint,
    SolidarityFund,
)
from cooperative_clearing.shared.core.config import Settings


async def seed_demo_solidarity(session: AsyncSession, settings: Settings) -> None:
    cooperative_id = stable_id("cooperative", settings.node_code)
    existing_report = await session.scalar(
        select(CampaignReport.id)
        .join(AidCampaign, AidCampaign.id == CampaignReport.campaign_id)
        .where(AidCampaign.campaign_code == "DEMO-AID-001")
    )
    if existing_report is not None:
        return

    anna_id = stable_id("member", "demo-member-anna")
    elena_id = stable_id("member", "demo-member-elena")
    pavel_id = stable_id("member", "demo-member-pavel")
    nina_id = stable_id("member", "demo-member-nina")
    operator = _bootstrap_principal(
        "security",
        elena_id,
        cooperative_id,
        (
            ("demo-role", "security:SOLIDARITY_OPERATOR", RoleCode.SOLIDARITY_OPERATOR),
            ("demo-role", "security:DATA_STEWARD", RoleCode.DATA_STEWARD),
        ),
    )
    controller = _bootstrap_principal(
        "auditor",
        pavel_id,
        cooperative_id,
        (
            ("demo-role", "auditor:SOLIDARITY_CONTROLLER", RoleCode.SOLIDARITY_CONTROLLER),
            ("bootstrap-role", "auditor:AUDITOR", RoleCode.AUDITOR),
        ),
    )
    donor_and_reviewer = _bootstrap_principal(
        "registrar",
        anna_id,
        cooperative_id,
        (
            ("demo-role", "registrar:SOLIDARITY_CONTROLLER", RoleCode.SOLIDARITY_CONTROLLER),
            ("bootstrap-role", "registrar:COOPERATIVE_ADMIN", RoleCode.COOPERATIVE_ADMIN),
        ),
    )
    recipient = Principal(
        user_id=stable_id("demo-user", "nina-arbitrator"),
        session_id=stable_id("demo-session", "nina-solidarity-recipient"),
        login="demo-arbitrator",
        member_id=nina_id,
        must_change_password=False,
        roles=(
            RoleGrant(
                stable_id("demo-role", "demo-arbitrator:ARBITRATOR"),
                RoleCode.ARBITRATOR,
                None,
            ),
        ),
    )

    contribution_evidence = await _evidence(
        session,
        settings,
        donor_and_reviewer,
        cooperative_id,
        "demo-solidarity-contribution-v1",
        "Receipt for ten kilograms of cabbage accepted into the solidarity campaign.",
    )
    delivery_evidence = await _evidence(
        session,
        settings,
        operator,
        cooperative_id,
        "demo-solidarity-delivery-v1",
        "Delivery handover record for the full approved allocation.",
    )

    service = SolidarityService(settings)
    fund_result = await service.propose_fund(
        session,
        principal=operator,
        cooperative_id=cooperative_id,
        fund_code="DEMO_SOLIDARITY",
        name="Demo solidarity fund",
        purpose="Voluntary material aid with independent verification and no reciprocal debt.",
        residue_rule=ResidueRule.RETAIN_IN_FUND,
        admin_expense_limit=Decimal("0"),
        terms={
            "demo_only": True,
            "no_debt": True,
            "no_reputation_benefit": True,
            "no_voting_or_priority_benefit": True,
            "open_decisions": ["OD-026", "OD-027", "OD-028", "OD-037"],
        },
        idempotency_key="demo-solidarity-fund-propose-v1",
        request_id=None,
    )
    fund = await session.get(SolidarityFund, fund_result.object_id)
    if fund is None:
        raise RuntimeError("demo solidarity fund was not created")
    await service.approve_fund(
        session,
        principal=controller,
        fund_id=fund.id,
        expected_version=1,
        idempotency_key="demo-solidarity-fund-approve-v1",
        request_id=None,
    )

    now = datetime.now(UTC)
    campaign_result = await service.create_campaign(
        session,
        principal=operator,
        fund_id=fund.id,
        campaign_code="DEMO-AID-001",
        title="Food support demo",
        public_purpose="Verified food support without publishing recipient identity.",
        eligibility_policy={
            "need_categories": [NeedCategory.BASIC_FOOD.value],
            "review": "independent controller",
            "demo_only": True,
        },
        accepted_forms=(ContributionForm.GOODS,),
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=30),
        idempotency_key="demo-solidarity-campaign-create-v1",
        request_id=None,
    )
    campaign = await session.get(AidCampaign, campaign_result.object_id)
    if campaign is None:
        raise RuntimeError("demo solidarity campaign was not created")
    await service.open_campaign(
        session,
        principal=controller,
        campaign_id=campaign.id,
        expected_version=1,
        idempotency_key="demo-solidarity-campaign-open-v1",
        request_id=None,
    )

    pledge_result = await service.create_pledge(
        session,
        principal=donor_and_reviewer,
        campaign_id=campaign.id,
        donor_member_id=anna_id,
        contribution_form=ContributionForm.GOODS,
        unit_code="KG",
        quantity=Decimal("10"),
        description="Voluntary pledge of cabbage; not available until physically verified.",
        expires_at=now + timedelta(days=7),
        idempotency_key="demo-solidarity-pledge-v1",
        request_id=None,
    )
    pledge = await session.get(Pledge, pledge_result.object_id)
    if pledge is None:
        raise RuntimeError("demo solidarity pledge was not created")
    contribution_result = await service.receive_contribution(
        session,
        principal=donor_and_reviewer,
        campaign_id=campaign.id,
        pledge_id=pledge.id,
        donor_member_id=anna_id,
        contribution_form=ContributionForm.GOODS,
        unit_code="KG",
        quantity=Decimal("10"),
        description="Ten kilograms of cabbage physically received for the campaign.",
        evidence_ids=(contribution_evidence,),
        idempotency_key="demo-solidarity-contribution-receive-v1",
        request_id=None,
    )
    contribution = await session.get(Contribution, contribution_result.object_id)
    if contribution is None:
        raise RuntimeError("demo solidarity contribution was not created")
    await service.verify_contribution(
        session,
        principal=controller,
        contribution_id=contribution.id,
        expected_version=1,
        accepted=True,
        verification_note="Quantity and evidence independently verified.",
        idempotency_key="demo-solidarity-contribution-verify-v1",
        request_id=None,
    )

    application_result = await service.submit_application(
        session,
        principal=recipient,
        campaign_id=campaign.id,
        recipient_member_id=nina_id,
        need_category=NeedCategory.BASIC_FOOD,
        requested_form=ContributionForm.GOODS,
        requested_unit_code="KG",
        requested_quantity=Decimal("10"),
        privacy_scope=PrivacyScope.RESTRICTED,
        evidence_ids=(),
        idempotency_key="demo-solidarity-application-submit-v1",
        request_id=None,
    )
    application = await session.get(AidApplication, application_result.object_id)
    if application is None:
        raise RuntimeError("demo solidarity application was not created")
    await service.review_application(
        session,
        principal=controller,
        application_id=application.id,
        expected_version=1,
        eligible=True,
        eligibility_note="Demo eligibility criteria confirmed without public identity disclosure.",
        idempotency_key="demo-solidarity-application-review-v1",
        request_id=None,
    )
    allocation_result = await service.propose_allocation(
        session,
        principal=operator,
        application_id=application.id,
        quantity=Decimal("10"),
        public_summary="One verified basic-food allocation",
        rationale="The reviewed request exactly matches the verified goods bucket.",
        idempotency_key="demo-solidarity-allocation-propose-v1",
        request_id=None,
    )
    allocation = await session.get(AidAllocation, allocation_result.object_id)
    if allocation is None:
        raise RuntimeError("demo solidarity allocation was not created")
    await service.approve_allocation(
        session,
        principal=controller,
        allocation_id=allocation.id,
        expected_version=1,
        allocation_hash=allocation.allocation_hash,
        approved=True,
        conflict_statement="No relationship with proposer, donor, or recipient.",
        idempotency_key="demo-solidarity-allocation-approve-v1",
        request_id=None,
    )

    complaint_result = await service.open_complaint(
        session,
        principal=recipient,
        campaign_id=campaign.id,
        allocation_id=allocation.id,
        contribution_id=None,
        category="ALLOCATION",
        summary="Confirm the approved quantity before handover.",
        privacy_scope=PrivacyScope.RESTRICTED,
        evidence_ids=(),
        idempotency_key="demo-solidarity-complaint-open-v1",
        request_id=None,
    )
    complaint = await session.get(SolidarityComplaint, complaint_result.object_id)
    if complaint is None:
        raise RuntimeError("demo solidarity complaint was not created")
    await service.resolve_complaint(
        session,
        principal=donor_and_reviewer,
        complaint_id=complaint.id,
        expected_version=1,
        accepted=True,
        resolution_action="RESTORE_ALLOCATION",
        resolution_note="The recorded quantity matches the approved and verified bucket.",
        idempotency_key="demo-solidarity-complaint-resolve-v1",
        request_id=None,
    )
    await service.record_delivery(
        session,
        principal=recipient,
        allocation_id=allocation.id,
        expected_version=4,
        attestor_kind=DeliveryAttestorKind.RECIPIENT,
        acknowledgement="The full approved allocation was received without reciprocal obligation.",
        evidence_ids=(delivery_evidence,),
        idempotency_key="demo-solidarity-delivery-v1",
        request_id=None,
    )
    await service.close_campaign(
        session,
        principal=controller,
        campaign_id=campaign.id,
        expected_version=2,
        reconciliation_note="Verified and delivered quantities match; no unresolved work remains.",
        idempotency_key="demo-solidarity-campaign-close-v1",
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
        session_id=stable_id("demo-session", f"{login}-solidarity"),
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
