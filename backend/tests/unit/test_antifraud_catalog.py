from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cooperative_clearing.modules.risk.application.antifraud_rules_v2 import (
    _largest_overlapping_campaign_group,
    _qualified_reputation_window,
    _related_components,
)
from cooperative_clearing.modules.risk.domain.antifraud_catalog import (
    ALGORITHM_VERSION,
    CALIBRATION_CASES,
    CALIBRATION_DATASET_VERSION,
    RULE_DESCRIPTORS,
    rule_manifest_hash,
    rule_manifest_payload,
)
from cooperative_clearing.modules.risk.domain.types import AntifraudRuleCode
from cooperative_clearing.modules.solidarity.infrastructure.models import AidCampaign
from cooperative_clearing.modules.trust.infrastructure.models import ReputationEvent


def test_rule_manifest_covers_every_rule_with_positive_and_negative_cases() -> None:
    descriptor_codes = {descriptor.code for descriptor in RULE_DESCRIPTORS}
    assert descriptor_codes == set(AntifraudRuleCode)
    assert len(RULE_DESCRIPTORS) == 15
    assert len(CALIBRATION_CASES) == 30

    for rule_code in AntifraudRuleCode:
        cases = [case for case in CALIBRATION_CASES if case.rule_code is rule_code]
        assert {case.expected_signal for case in cases} == {False, True}
        assert len({case.case_id for case in cases}) == 2


def test_rule_manifest_is_versioned_stable_and_explicitly_not_pilot_approved() -> None:
    payload = rule_manifest_payload()

    assert ALGORITHM_VERSION == "2.0.0"
    assert CALIBRATION_DATASET_VERSION == "synthetic-v2.0.0"
    assert rule_manifest_hash().startswith("sha256:")
    assert len(rule_manifest_hash()) == 71
    assert rule_manifest_hash() == rule_manifest_hash()
    assert [item["code"] for item in payload] == sorted(item["code"] for item in payload)
    assert all(item["engineering_case_count"] == 2 for item in payload)
    assert all(item["pilot_false_positive_rate"] is None for item in payload)
    assert all(item["production_approved"] is False for item in payload)


def test_related_components_include_transitive_relations() -> None:
    first, second, third, fourth = (uuid4() for _ in range(4))

    components = _related_components({(first, second), (second, third)})

    expected = frozenset({first, second, third})
    assert components[first] == expected
    assert components[second] == expected
    assert components[third] == expected
    assert fourth not in components


def reputation_event(
    *,
    created_at: datetime,
    recorded_by_member_id: UUID,
) -> ReputationEvent:
    return ReputationEvent(
        id=uuid4(),
        created_at=created_at,
        recorded_by_member_id=recorded_by_member_id,
    )


def campaign(*, starts_at: datetime, ends_at: datetime) -> AidCampaign:
    return AidCampaign(id=uuid4(), starts_at=starts_at, ends_at=ends_at)


def test_reputation_window_selects_a_qualified_burst_not_the_largest_clean_burst() -> None:
    now = datetime.now(UTC)
    one_recorder = uuid4()
    second_recorder = uuid4()
    clean_larger = [
        reputation_event(
            created_at=now + timedelta(minutes=index),
            recorded_by_member_id=one_recorder,
        )
        for index in range(4)
    ]
    suspicious_smaller = [
        reputation_event(
            created_at=now + timedelta(hours=1, minutes=index),
            recorded_by_member_id=(one_recorder if index < 2 else second_recorder),
        )
        for index in range(3)
    ]

    result = _qualified_reputation_window(
        clean_larger + suspicious_smaller,
        maximum_window=timedelta(minutes=10),
    )

    assert len(result) == 3
    assert {item.recorded_by_member_id for item in result} == {
        one_recorder,
        second_recorder,
    }


def test_campaign_window_finds_three_overlaps_despite_unrelated_campaign() -> None:
    now = datetime.now(UTC)
    overlapping = [
        campaign(
            starts_at=now + timedelta(days=index),
            ends_at=now + timedelta(days=10 + index),
        )
        for index in range(3)
    ]
    unrelated = campaign(
        starts_at=now + timedelta(days=30),
        ends_at=now + timedelta(days=40),
    )

    result = _largest_overlapping_campaign_group([*overlapping, unrelated])

    assert {item.id for item in result} == {item.id for item in overlapping}
