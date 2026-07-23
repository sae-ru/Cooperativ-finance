"""Stable trust lifecycle vocabulary and deterministic profile projection."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from cooperative_clearing.shared.domain.errors import DomainError


class TrustCaseStatus(StrEnum):
    OPEN = "OPEN"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    READY_FOR_DECISION = "READY_FOR_DECISION"
    DECIDED = "DECIDED"
    UNDER_APPEAL = "UNDER_APPEAL"
    REMANDED = "REMANDED"
    CLOSED = "CLOSED"


class DecisionStage(StrEnum):
    ORIGINAL = "ORIGINAL"
    APPEAL = "APPEAL"


class DecisionOutcome(StrEnum):
    SUBSTANTIATED = "SUBSTANTIATED"
    PARTLY_SUBSTANTIATED = "PARTLY_SUBSTANTIATED"
    UNSUBSTANTIATED = "UNSUBSTANTIATED"
    AFFIRMED = "AFFIRMED"
    MODIFIED = "MODIFIED"
    OVERTURNED = "OVERTURNED"
    REMANDED = "REMANDED"


class ConflictAssessment(StrEnum):
    CLEAR = "CLEAR"
    CONFLICT = "CONFLICT"


class ReputationContext(StrEnum):
    SUPPLY = "SUPPLY"
    QUALITY = "QUALITY"
    STORAGE = "STORAGE"
    LOGISTICS = "LOGISTICS"
    SERVICE = "SERVICE"
    OBLIGATION = "OBLIGATION"
    GUARANTEE = "GUARANTEE"
    WAREHOUSE_CONTROL = "WAREHOUSE_CONTROL"
    AUDIT = "AUDIT"
    ARBITRATION = "ARBITRATION"
    FUND_GOVERNANCE = "FUND_GOVERNANCE"
    NODE_SECURITY = "NODE_SECURITY"


class ReputationClassification(StrEnum):
    FULFILLED = "FULFILLED"
    BREACH = "BREACH"
    SELF_REPORTED_ERROR = "SELF_REPORTED_ERROR"
    CORRECTION = "CORRECTION"
    REHABILITATION = "REHABILITATION"


class ReputationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISPUTED = "DISPUTED"
    VOIDED = "VOIDED"


class AppealOutcome(StrEnum):
    AFFIRMED = "AFFIRMED"
    MODIFIED = "MODIFIED"
    OVERTURNED = "OVERTURNED"
    REMANDED = "REMANDED"


class FaultClass(StrEnum):
    FORCE_MAJEURE = "FORCE_MAJEURE"
    GOOD_FAITH_ERROR = "GOOD_FAITH_ERROR"
    NEGLIGENCE = "NEGLIGENCE"
    GROSS_NEGLIGENCE = "GROSS_NEGLIGENCE"
    INTENT = "INTENT"
    COLLUSION = "COLLUSION"


@dataclass(frozen=True, slots=True)
class ReliabilityEventFact:
    event_id: UUID
    context: ReputationContext
    classification: ReputationClassification
    severity: int
    confidence: Decimal
    status: ReputationStatus
    appeal_state: str
    observation_end: datetime


@dataclass(frozen=True, slots=True)
class ContextReliabilityProfile:
    context: ReputationContext
    confirmed_fulfillments: int
    confirmed_breaches: int
    self_reported_errors: int
    rehabilitation_events: int
    disputed_events: int
    voided_events: int
    corrections: int
    sample_count: int
    confidence_min: Decimal | None
    confidence_max: Decimal | None
    last_observation: datetime | None
    source_event_ids: tuple[UUID, ...]


def build_reliability_profile(
    events: list[ReliabilityEventFact],
) -> tuple[ContextReliabilityProfile, ...]:
    """Build a context matrix without producing or implying a universal score."""

    result: list[ContextReliabilityProfile] = []
    for context in sorted({event.context for event in events}, key=lambda item: item.value):
        context_events = sorted(
            (event for event in events if event.context == context),
            key=lambda item: (item.observation_end, str(item.event_id)),
        )
        active = [event for event in context_events if event.status == ReputationStatus.ACTIVE]
        sampled = [
            event for event in active if event.classification != ReputationClassification.CORRECTION
        ]
        confidences = [event.confidence for event in sampled]
        result.append(
            ContextReliabilityProfile(
                context=context,
                confirmed_fulfillments=sum(
                    event.classification == ReputationClassification.FULFILLED for event in sampled
                ),
                confirmed_breaches=sum(
                    event.classification == ReputationClassification.BREACH for event in sampled
                ),
                self_reported_errors=sum(
                    event.classification == ReputationClassification.SELF_REPORTED_ERROR
                    for event in sampled
                ),
                rehabilitation_events=sum(
                    event.classification == ReputationClassification.REHABILITATION
                    for event in sampled
                ),
                disputed_events=sum(
                    event.status == ReputationStatus.DISPUTED for event in context_events
                ),
                voided_events=sum(
                    event.status == ReputationStatus.VOIDED for event in context_events
                ),
                corrections=sum(
                    event.classification == ReputationClassification.CORRECTION for event in active
                ),
                sample_count=len(sampled),
                confidence_min=min(confidences) if confidences else None,
                confidence_max=max(confidences) if confidences else None,
                last_observation=max((event.observation_end for event in active), default=None),
                source_event_ids=tuple(event.event_id for event in context_events),
            )
        )
    return tuple(result)


def trust_error(code: str, status_code: int = 409) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.trust.{code.lower()}",
        status_code=status_code,
    )
