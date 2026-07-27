from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from cooperative_clearing.modules.identity.infrastructure.models import Member
from cooperative_clearing.modules.inventory.infrastructure.models import InventoryLot, Product
from cooperative_clearing.modules.risk.application.antifraud_rules_v2 import (
    _campaign_splitting_findings,
    _coefficient_change_findings,
    _contribution_influence_findings,
    _critical_resource_findings,
    _limit_splitting_findings,
    _ordered_pair,
    _privileged_conflict_findings,
    _related_rating_findings,
    _reputation_synchronization_findings,
    _sanction_continuity_findings,
)
from cooperative_clearing.modules.risk.domain.types import AntifraudRuleCode
from cooperative_clearing.modules.risk.infrastructure.models import (
    ExposureCommitment,
    RelatedPartyLink,
    RiskPolicy,
)
from cooperative_clearing.modules.solidarity.infrastructure.models import (
    AidAllocation,
    AidCampaign,
    AllocationApproval,
    Contribution,
)
from cooperative_clearing.modules.trust.infrastructure.models import (
    ReputationEvent,
    Sanction,
)


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def scalars(self) -> list[Any]:
        return self.rows

    def all(self) -> list[Any]:
        return self.rows


class FakeSession:
    def __init__(self, *responses: list[Any]) -> None:
        self.responses = list(responses)

    async def execute(self, _statement: object) -> FakeResult:
        assert self.responses, "unexpected query"
        return FakeResult(self.responses.pop(0))


def reputation(
    subject: UUID,
    recorder: UUID,
    created_at: datetime,
    *,
    context: str = "SUPPLY",
) -> ReputationEvent:
    return ReputationEvent(
        id=uuid4(),
        cooperative_id=uuid4(),
        subject_member_id=subject,
        recorded_by_member_id=recorder,
        classification="FULFILLED",
        status="ACTIVE",
        context=context,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_related_rating_rule_requires_reciprocal_positive_events() -> None:
    cooperative_id = uuid4()
    first, second, third = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    link = RelatedPartyLink(id=uuid4(), member_a_id=first, member_b_id=second)
    pair = _ordered_pair(first, second)
    rows = [
        reputation(second, first, now),
        reputation(first, second, now + timedelta(minutes=1)),
        reputation(third, first, now + timedelta(minutes=2)),
    ]

    findings = await _related_rating_findings(
        FakeSession(rows),  # type: ignore[arg-type]
        cooperative_id,
        now - timedelta(hours=1),
        now + timedelta(hours=1),
        {pair: link},
    )

    assert {item.subject_id for item in findings} == {first, second}
    assert {item.rule_code for item in findings} == {AntifraudRuleCode.RELATED_ACCOUNT_RATING_RING}


@pytest.mark.asyncio
async def test_limit_splitting_rule_detects_only_small_commitments_near_limit() -> None:
    cooperative_id = uuid4()
    suspicious_member, clean_member, policy_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    policy = RiskPolicy(
        id=policy_id,
        cooperative_id=cooperative_id,
        max_member_exposure=Decimal("100"),
    )
    rows = [
        *[
            ExposureCommitment(
                id=uuid4(),
                owner_member_id=suspicious_member,
                policy_id=policy_id,
                risk_type="DELIVERY",
                max_loss=Decimal("20"),
            )
            for _ in range(4)
        ],
        *[
            ExposureCommitment(
                id=uuid4(),
                owner_member_id=clean_member,
                policy_id=policy_id,
                risk_type="DELIVERY",
                max_loss=Decimal("25"),
            )
            for _ in range(3)
        ],
    ]

    findings = await _limit_splitting_findings(
        FakeSession([policy], rows),  # type: ignore[arg-type]
        cooperative_id,
        now - timedelta(hours=1),
        now + timedelta(hours=1),
    )

    assert [item.subject_id for item in findings] == [suspicious_member]
    assert findings[0].automation_action.value == "WARN"


@pytest.mark.asyncio
async def test_coefficient_rule_ignores_gradual_change_and_warns_on_spike() -> None:
    cooperative_id, actor = uuid4(), uuid4()
    now = datetime.now(UTC)
    policies = [
        RiskPolicy(
            id=uuid4(),
            denomination="SPIKE",
            policy_version=1,
            created_at=now - timedelta(days=2),
            max_member_exposure=Decimal("100"),
            max_related_exposure=Decimal("150"),
            max_guarantee_chain_depth=3,
            proposed_by_member_id=actor,
        ),
        RiskPolicy(
            id=uuid4(),
            denomination="SPIKE",
            policy_version=2,
            created_at=now,
            max_member_exposure=Decimal("160"),
            max_related_exposure=Decimal("150"),
            max_guarantee_chain_depth=3,
            proposed_by_member_id=actor,
        ),
        RiskPolicy(
            id=uuid4(),
            denomination="CLEAN",
            policy_version=1,
            created_at=now - timedelta(days=2),
            max_member_exposure=Decimal("100"),
            max_related_exposure=Decimal("150"),
            max_guarantee_chain_depth=3,
            proposed_by_member_id=uuid4(),
        ),
        RiskPolicy(
            id=uuid4(),
            denomination="CLEAN",
            policy_version=2,
            created_at=now,
            max_member_exposure=Decimal("130"),
            max_related_exposure=Decimal("150"),
            max_guarantee_chain_depth=4,
            proposed_by_member_id=uuid4(),
        ),
    ]

    findings = await _coefficient_change_findings(
        FakeSession(policies),  # type: ignore[arg-type]
        cooperative_id,
        now - timedelta(days=1),
        now + timedelta(days=1),
    )

    assert [item.subject_id for item in findings] == [actor]


@pytest.mark.asyncio
async def test_critical_resource_rule_requires_two_related_owners_above_eighty_percent() -> None:
    cooperative_id = uuid4()
    first, second, unrelated = uuid4(), uuid4(), uuid4()
    component = frozenset({first, second})
    product = Product(id=uuid4(), sku="WATER")
    lots = [
        (InventoryLot(id=uuid4(), owner_member_id=first, current_quantity=Decimal("45")), product),
        (InventoryLot(id=uuid4(), owner_member_id=second, current_quantity=Decimal("45")), product),
        (
            InventoryLot(id=uuid4(), owner_member_id=unrelated, current_quantity=Decimal("10")),
            product,
        ),
    ]

    findings = await _critical_resource_findings(
        FakeSession(["WATER"], lots),  # type: ignore[arg-type]
        cooperative_id,
        datetime.now(UTC),
        {first: component, second: component},
    )

    assert {item.subject_id for item in findings} == {first, second}
    assert all(
        Decimal(str(item.observed_data["concentration_ratio"])) == Decimal("0.9")
        for item in findings
    )


@pytest.mark.asyncio
async def test_reputation_rule_requires_multiple_recorders_in_ten_minutes() -> None:
    cooperative_id = uuid4()
    suspicious, clean, first_recorder, second_recorder = (uuid4() for _ in range(4))
    now = datetime.now(UTC)
    rows = [
        reputation(suspicious, first_recorder, now),
        reputation(suspicious, first_recorder, now + timedelta(minutes=1)),
        reputation(suspicious, second_recorder, now + timedelta(minutes=2)),
        reputation(clean, first_recorder, now),
        reputation(clean, first_recorder, now + timedelta(minutes=1)),
        reputation(clean, first_recorder, now + timedelta(minutes=2)),
    ]

    findings = await _reputation_synchronization_findings(
        FakeSession(rows),  # type: ignore[arg-type]
        cooperative_id,
        now - timedelta(hours=1),
        now + timedelta(hours=1),
    )

    assert [item.subject_id for item in findings] == [suspicious]


@pytest.mark.asyncio
async def test_contribution_influence_requires_the_verifier_to_issue_the_rating() -> None:
    cooperative_id, campaign_id = uuid4(), uuid4()
    suspicious, clean, verifier, outsider = (uuid4() for _ in range(4))
    now = datetime.now(UTC)
    contributions = [
        Contribution(
            id=uuid4(),
            campaign_id=campaign_id,
            donor_member_id=suspicious,
            verified_by_member_id=verifier,
            verified_at=now,
        ),
        Contribution(
            id=uuid4(),
            campaign_id=campaign_id,
            donor_member_id=clean,
            verified_by_member_id=verifier,
            verified_at=now,
        ),
    ]
    reputations = [
        reputation(suspicious, verifier, now + timedelta(hours=2)),
        reputation(clean, outsider, now + timedelta(hours=2)),
    ]

    findings = await _contribution_influence_findings(
        FakeSession(contributions, [campaign_id], reputations),  # type: ignore[arg-type]
        cooperative_id,
        now - timedelta(hours=1),
        now + timedelta(days=1),
    )

    assert [item.subject_id for item in findings] == [suspicious]


@pytest.mark.asyncio
async def test_privileged_conflict_requires_an_active_related_pair() -> None:
    cooperative_id, actor, recipient, unrelated = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    allocation = AidAllocation(
        id=uuid4(),
        recipient_member_id=recipient,
        proposed_by_member_id=actor,
        proposed_event_id=uuid4(),
    )
    approval = AllocationApproval(
        id=uuid4(),
        decided_by_member_id=unrelated,
        decided_event_id=uuid4(),
        decided_at=now,
    )

    findings = await _privileged_conflict_findings(
        FakeSession([(allocation, approval)], []),  # type: ignore[arg-type]
        cooperative_id,
        now - timedelta(hours=1),
        now + timedelta(hours=1),
        {_ordered_pair(actor, recipient)},
    )

    assert [item.subject_id for item in findings] == [actor]
    assert findings[0].automation_action.value == "HOLD"


@pytest.mark.asyncio
async def test_campaign_splitting_requires_three_overlapping_campaigns() -> None:
    cooperative_id, actor, clean_actor, fund_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    rows = [
        *[
            AidCampaign(
                id=uuid4(),
                created_by_member_id=actor,
                fund_id=fund_id,
                starts_at=now + timedelta(days=index),
                ends_at=now + timedelta(days=10 + index),
            )
            for index in range(3)
        ],
        *[
            AidCampaign(
                id=uuid4(),
                created_by_member_id=clean_actor,
                fund_id=fund_id,
                starts_at=now + timedelta(days=index * 10),
                ends_at=now + timedelta(days=index * 10 + 2),
            )
            for index in range(3)
        ],
    ]

    findings = await _campaign_splitting_findings(
        FakeSession(rows),  # type: ignore[arg-type]
        cooperative_id,
        now - timedelta(hours=1),
        now + timedelta(days=1),
    )

    assert [item.subject_id for item in findings] == [actor]


@pytest.mark.asyncio
async def test_sanction_continuity_requires_related_account_created_after_sanction() -> None:
    cooperative_id, sanctioned, new_account, old_account = (uuid4() for _ in range(4))
    now = datetime.now(UTC)
    sanction = Sanction(
        id=uuid4(),
        subject_member_id=sanctioned,
        starts_at=now,
        expires_at=None,
        status="ACTIVE",
    )
    members = [
        Member(id=new_account, status="ACTIVE", created_at=now + timedelta(minutes=1)),
        Member(id=old_account, status="ACTIVE", created_at=now - timedelta(days=1)),
    ]

    findings = await _sanction_continuity_findings(
        FakeSession([sanction], members),  # type: ignore[arg-type]
        cooperative_id,
        now + timedelta(hours=1),
        {
            _ordered_pair(sanctioned, new_account),
            _ordered_pair(sanctioned, old_account),
        },
    )

    assert [item.subject_id for item in findings] == [new_account]
