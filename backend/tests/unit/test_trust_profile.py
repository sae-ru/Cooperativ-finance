from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from cooperative_clearing.modules.trust.domain.types import (
    ReliabilityEventFact,
    ReputationClassification,
    ReputationContext,
    ReputationStatus,
    build_reliability_profile,
)


def fact(
    *,
    context: ReputationContext,
    classification: ReputationClassification,
    status: ReputationStatus,
    confidence: str = "0.75",
    offset_days: int = 0,
) -> ReliabilityEventFact:
    return ReliabilityEventFact(
        event_id=uuid4(),
        context=context,
        classification=classification,
        severity=1,
        confidence=Decimal(confidence),
        status=status,
        appeal_state="OVERTURNED" if status == ReputationStatus.DISPUTED else "NONE",
        observation_end=datetime(2035, 1, 1, tzinfo=UTC) + timedelta(days=offset_days),
    )


def test_overturned_breach_is_not_counted_and_correction_preserves_history() -> None:
    profile = build_reliability_profile(
        [
            fact(
                context=ReputationContext.OBLIGATION,
                classification=ReputationClassification.BREACH,
                status=ReputationStatus.DISPUTED,
            ),
            fact(
                context=ReputationContext.OBLIGATION,
                classification=ReputationClassification.CORRECTION,
                status=ReputationStatus.ACTIVE,
                confidence="1",
                offset_days=1,
            ),
        ]
    )

    assert len(profile) == 1
    obligation = profile[0]
    assert obligation.confirmed_breaches == 0
    assert obligation.disputed_events == 1
    assert obligation.corrections == 1
    assert obligation.sample_count == 0
    assert obligation.confidence_min is None
    assert len(obligation.source_event_ids) == 2


def test_profile_is_contextual_deterministic_and_has_no_universal_score() -> None:
    events = [
        fact(
            context=ReputationContext.SUPPLY,
            classification=ReputationClassification.FULFILLED,
            status=ReputationStatus.ACTIVE,
            confidence="0.9",
        ),
        fact(
            context=ReputationContext.QUALITY,
            classification=ReputationClassification.BREACH,
            status=ReputationStatus.ACTIVE,
            confidence="0.6",
        ),
    ]

    profile = build_reliability_profile(list(reversed(events)))

    assert [item.context for item in profile] == [
        ReputationContext.QUALITY,
        ReputationContext.SUPPLY,
    ]
    assert profile[0].confirmed_breaches == 1
    assert profile[1].confirmed_fulfillments == 1
    assert not hasattr(profile[0], "score")
