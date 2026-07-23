from decimal import Decimal

import pytest

from cooperative_clearing.modules.exchange.domain.types import (
    AcceptanceDecision,
    LogisticsStatus,
    ObligationAmounts,
    ObligationStatus,
    acceptance_decision,
    next_logistics_status,
    obligation_status_for,
)
from cooperative_clearing.shared.domain.errors import DomainError


def amounts(total: str = "10", submitted: str = "0", fulfilled: str = "0") -> ObligationAmounts:
    return ObligationAmounts(
        total=Decimal(total),
        submitted=Decimal(submitted),
        fulfilled=Decimal(fulfilled),
    ).validate()


def test_submission_reserves_quantity_until_creditor_accepts_it() -> None:
    submitted = amounts().submit(Decimal("6"), partial_allowed=True)
    assert submitted.remaining == Decimal("4")
    assert submitted.submitted == Decimal("6")

    accepted = submitted.accept(Decimal("6"), Decimal("4"))
    assert accepted.submitted == 0
    assert accepted.fulfilled == Decimal("4")
    assert accepted.remaining == Decimal("6")
    assert obligation_status_for(accepted, overdue=False) is ObligationStatus.PARTIALLY_FULFILLED


def test_non_partial_obligation_requires_full_remaining_quantity() -> None:
    with pytest.raises(DomainError, match="PARTIAL_FULFILLMENT_FORBIDDEN"):
        amounts().submit(Decimal("9"), partial_allowed=False)


def test_rejection_releases_the_entire_submitted_quantity() -> None:
    submitted = amounts().submit(Decimal("10"), partial_allowed=False)
    released = submitted.accept(Decimal("10"), Decimal("0"))
    assert released == amounts()
    assert acceptance_decision(Decimal("10"), Decimal("0")) is AcceptanceDecision.REJECTED


def test_quantities_cannot_exceed_the_obligation() -> None:
    with pytest.raises(DomainError, match="OBLIGATION_QUANTITY_EXCEEDED"):
        amounts(total="10", submitted="6", fulfilled="5")
    with pytest.raises(DomainError, match="FULFILLMENT_EXCEEDS_REMAINING"):
        amounts(total="10", fulfilled="8").submit(Decimal("3"), partial_allowed=True)


def test_logistics_transitions_are_strict_and_ordered() -> None:
    accepted = next_logistics_status(LogisticsStatus.OFFERED, "accept")
    in_transit = next_logistics_status(accepted, "pickup")
    delivered = next_logistics_status(in_transit, "deliver")
    assert delivered is LogisticsStatus.DELIVERED

    with pytest.raises(DomainError, match="LOGISTICS_TRANSITION_FORBIDDEN"):
        next_logistics_status(LogisticsStatus.OFFERED, "deliver")
