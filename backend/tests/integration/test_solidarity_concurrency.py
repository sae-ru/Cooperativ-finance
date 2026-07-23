import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.inventory.infrastructure.models import EvidenceBlob
from cooperative_clearing.modules.solidarity.application.service import SolidarityService
from cooperative_clearing.modules.solidarity.domain.types import (
    ContributionForm,
    NeedCategory,
    PrivacyScope,
)
from cooperative_clearing.modules.solidarity.infrastructure.models import (
    AidAllocation,
    AidApplication,
    AidCampaign,
    Contribution,
    SolidarityFund,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database


def _principal(
    login: str,
    member_key: str,
    role: RoleCode,
    cooperative_id: UUID | None,
    *,
    demo_user: bool = False,
) -> Principal:
    assignment_kind = "bootstrap-role" if role in {RoleCode.COOPERATIVE_ADMIN} else "demo-role"
    if role in {RoleCode.AUDITOR}:
        assignment_kind = "bootstrap-role"
    return Principal(
        user_id=(
            stable_id("demo-user", "nina-arbitrator")
            if demo_user
            else stable_id("bootstrap-user", login)
        ),
        session_id=uuid4(),
        login=login,
        member_id=stable_id("member", member_key),
        must_change_password=False,
        roles=(
            RoleGrant(
                stable_id(assignment_kind, f"{login}:{role.value}"),
                role,
                cooperative_id,
            ),
        ),
    )


@pytest.mark.integration
async def test_parallel_approvals_cannot_spend_the_same_verified_balance_twice() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"solidarity-concurrency-{suffix}",
        blob_root=Path(f"/tmp/solidarity-concurrency-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    cooperative_id = stable_id("cooperative", settings.node_code)
    operator = _principal(
        "security", "demo-member-elena", RoleCode.SOLIDARITY_OPERATOR, cooperative_id
    )
    controller = _principal(
        "auditor", "demo-member-pavel", RoleCode.SOLIDARITY_CONTROLLER, cooperative_id
    )
    anna = _principal("registrar", "demo-member-anna", RoleCode.COOPERATIVE_ADMIN, cooperative_id)
    nina = _principal(
        "demo-arbitrator",
        "demo-member-nina",
        RoleCode.ARBITRATOR,
        None,
        demo_user=True,
    )
    service = SolidarityService(settings)
    try:
        async with database.session() as session:
            fund = (
                await session.execute(
                    select(SolidarityFund).where(SolidarityFund.fund_code == "DEMO_SOLIDARITY")
                )
            ).scalar_one()
            evidence_id = (
                await session.execute(
                    select(EvidenceBlob.id).where(
                        EvidenceBlob.original_name == "demo-solidarity-contribution-v1.txt"
                    )
                )
            ).scalar_one()
            now = datetime.now(UTC)
            campaign_result = await service.create_campaign(
                session,
                principal=operator,
                fund_id=fund.id,
                campaign_code=f"CONC-{suffix}",
                title="Concurrent allocation invariant",
                public_purpose="Verify that one aid bucket cannot be allocated twice.",
                eligibility_policy={"test": "concurrency"},
                accepted_forms=(ContributionForm.GOODS,),
                starts_at=now - timedelta(hours=1),
                ends_at=now + timedelta(days=1),
                idempotency_key=f"{suffix}-campaign-create",
                request_id=None,
            )
            campaign = await session.get(AidCampaign, campaign_result.object_id)
            assert campaign is not None
            await service.open_campaign(
                session,
                principal=controller,
                campaign_id=campaign.id,
                expected_version=1,
                idempotency_key=f"{suffix}-campaign-open",
                request_id=None,
            )
            contribution_result = await service.receive_contribution(
                session,
                principal=anna,
                campaign_id=campaign.id,
                pledge_id=None,
                donor_member_id=anna.member_id,
                contribution_form=ContributionForm.GOODS,
                unit_code="KG",
                quantity=Decimal("10"),
                description="Single verified bucket for concurrent allocation test.",
                evidence_ids=(evidence_id,),
                idempotency_key=f"{suffix}-contribution",
                request_id=None,
            )
            contribution = await session.get(Contribution, contribution_result.object_id)
            assert contribution is not None
            await service.verify_contribution(
                session,
                principal=controller,
                contribution_id=contribution.id,
                expected_version=1,
                accepted=True,
                verification_note="Verified test quantity.",
                idempotency_key=f"{suffix}-contribution-verify",
                request_id=None,
            )

            application_ids = []
            for index, recipient in enumerate((anna, nina), start=1):
                assert recipient.member_id is not None
                result = await service.submit_application(
                    session,
                    principal=recipient,
                    campaign_id=campaign.id,
                    recipient_member_id=recipient.member_id,
                    need_category=NeedCategory.BASIC_FOOD,
                    requested_form=ContributionForm.GOODS,
                    requested_unit_code="KG",
                    requested_quantity=Decimal("6"),
                    privacy_scope=PrivacyScope.RESTRICTED,
                    evidence_ids=(),
                    idempotency_key=f"{suffix}-application-{index}",
                    request_id=None,
                )
                application_ids.append(result.object_id)
                await service.review_application(
                    session,
                    principal=controller,
                    application_id=result.object_id,
                    expected_version=1,
                    eligible=True,
                    eligibility_note="Eligible for concurrency invariant test.",
                    idempotency_key=f"{suffix}-application-review-{index}",
                    request_id=None,
                )

            allocations: list[AidAllocation] = []
            for index, application_id in enumerate(application_ids, start=1):
                result = await service.propose_allocation(
                    session,
                    principal=operator,
                    application_id=application_id,
                    quantity=Decimal("6"),
                    public_summary=f"Concurrent test allocation {index}",
                    rationale="Competing for the same ten-unit verified bucket.",
                    idempotency_key=f"{suffix}-allocation-{index}",
                    request_id=None,
                )
                allocation = await session.get(AidAllocation, result.object_id)
                assert allocation is not None
                allocations.append(allocation)
            approval_inputs = [(item.id, item.allocation_hash) for item in allocations]
            await session.commit()

        async def approve(index: int, allocation_id: UUID, allocation_hash: str) -> str:
            async with database.session() as session:
                try:
                    await service.approve_allocation(
                        session,
                        principal=controller,
                        allocation_id=allocation_id,
                        expected_version=1,
                        allocation_hash=allocation_hash,
                        approved=True,
                        conflict_statement="No conflict for concurrent test.",
                        idempotency_key=f"{suffix}-approve-{index}",
                        request_id=None,
                    )
                    await session.commit()
                    return "APPROVED"
                except DomainError as exc:
                    await session.rollback()
                    return exc.code

        results = await asyncio.gather(
            *(
                approve(index, item_id, digest)
                for index, (item_id, digest) in enumerate(approval_inputs)
            )
        )
        assert sorted(results) == ["ALLOCATION_EXCEEDS_VERIFIED_BALANCE", "APPROVED"]

        async with database.session() as session:
            statuses = list(
                (
                    await session.execute(
                        select(AidAllocation.status).where(
                            AidAllocation.id.in_([item_id for item_id, _ in approval_inputs])
                        )
                    )
                ).scalars()
            )
            applications = list(
                (
                    await session.execute(
                        select(AidApplication.status).where(AidApplication.id.in_(application_ids))
                    )
                ).scalars()
            )
        assert sorted(statuses) == ["APPROVED", "PROPOSED"]
        assert sorted(applications) == ["ALLOCATED", "ELIGIBLE"]
    finally:
        await database.dispose()
