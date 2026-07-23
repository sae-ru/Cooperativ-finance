"""State machines and exact-quantity invariants for local exchange."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from cooperative_clearing.modules.inventory.domain.types import exact_quantity
from cooperative_clearing.shared.domain.errors import DomainError


class DealStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED"
    FULFILLED = "FULFILLED"
    DISPUTED = "DISPUTED"
    CANCELLED = "CANCELLED"
    DEFAULTED = "DEFAULTED"


class ObligationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED"
    FULFILLED = "FULFILLED"
    OVERDUE = "OVERDUE"
    DISPUTED = "DISPUTED"
    DEFAULTED = "DEFAULTED"
    CLOSED = "CLOSED"


class FulfillmentStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_ACCEPTED = "PARTIALLY_ACCEPTED"
    REJECTED = "REJECTED"
    DISPUTED = "DISPUTED"


class AcceptanceDecision(StrEnum):
    ACCEPTED = "ACCEPTED"
    PARTIALLY_ACCEPTED = "PARTIALLY_ACCEPTED"
    REJECTED = "REJECTED"


class LogisticsStatus(StrEnum):
    OFFERED = "OFFERED"
    ACCEPTED = "ACCEPTED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    DISPUTED = "DISPUTED"


OPERABLE_OBLIGATION_STATUSES = frozenset(
    {
        ObligationStatus.ACTIVE,
        ObligationStatus.PARTIALLY_FULFILLED,
        ObligationStatus.OVERDUE,
    }
)


@dataclass(frozen=True, slots=True)
class ObligationAmounts:
    total: Decimal
    submitted: Decimal
    fulfilled: Decimal
    cleared: Decimal = Decimal(0)

    def validate(self) -> "ObligationAmounts":
        total = exact_quantity(self.total)
        submitted = exact_quantity(self.submitted, allow_zero=True)
        fulfilled = exact_quantity(self.fulfilled, allow_zero=True)
        cleared = exact_quantity(self.cleared, allow_zero=True)
        if (
            total != self.total
            or submitted != self.submitted
            or fulfilled != self.fulfilled
            or cleared != self.cleared
        ):
            raise exchange_error("OBLIGATION_QUANTITY_INVALID")
        if submitted + fulfilled + cleared > total:
            raise exchange_error("OBLIGATION_QUANTITY_EXCEEDED", 409)
        return self

    @property
    def remaining(self) -> Decimal:
        return self.total - self.submitted - self.fulfilled - self.cleared

    def submit(self, quantity: Decimal, *, partial_allowed: bool) -> "ObligationAmounts":
        amount = exact_quantity(quantity)
        if amount > self.remaining:
            raise exchange_error("FULFILLMENT_EXCEEDS_REMAINING", 409)
        if not partial_allowed and amount != self.remaining:
            raise exchange_error("PARTIAL_FULFILLMENT_FORBIDDEN", 409)
        return ObligationAmounts(
            total=self.total,
            submitted=self.submitted + amount,
            fulfilled=self.fulfilled,
            cleared=self.cleared,
        ).validate()

    def accept(
        self,
        submitted_quantity: Decimal,
        accepted_quantity: Decimal,
    ) -> "ObligationAmounts":
        submitted = exact_quantity(submitted_quantity)
        accepted = exact_quantity(accepted_quantity, allow_zero=True)
        if submitted > self.submitted:
            raise exchange_error("SUBMITTED_QUANTITY_INCONSISTENT", 409)
        if accepted > submitted:
            raise exchange_error("ACCEPTED_QUANTITY_EXCEEDED", 409)
        return ObligationAmounts(
            total=self.total,
            submitted=self.submitted - submitted,
            fulfilled=self.fulfilled + accepted,
            cleared=self.cleared,
        ).validate()


def obligation_status_for(amounts: ObligationAmounts, *, overdue: bool) -> ObligationStatus:
    amounts.validate()
    if amounts.cleared > 0 and amounts.remaining == 0:
        return ObligationStatus.CLOSED
    if amounts.fulfilled == amounts.total:
        return ObligationStatus.FULFILLED
    if overdue:
        return ObligationStatus.OVERDUE
    if amounts.fulfilled > 0 or amounts.cleared > 0:
        return ObligationStatus.PARTIALLY_FULFILLED
    return ObligationStatus.ACTIVE


def acceptance_decision(
    submitted_quantity: Decimal, accepted_quantity: Decimal
) -> AcceptanceDecision:
    submitted = exact_quantity(submitted_quantity)
    accepted = exact_quantity(accepted_quantity, allow_zero=True)
    if accepted > submitted:
        raise exchange_error("ACCEPTED_QUANTITY_EXCEEDED", 409)
    if accepted == submitted:
        return AcceptanceDecision.ACCEPTED
    if accepted == 0:
        return AcceptanceDecision.REJECTED
    return AcceptanceDecision.PARTIALLY_ACCEPTED


def ensure_obligation_operable(status: ObligationStatus) -> None:
    if status not in OPERABLE_OBLIGATION_STATUSES:
        raise exchange_error("OBLIGATION_NOT_OPERABLE", 409)


def next_logistics_status(current: LogisticsStatus, action: str) -> LogisticsStatus:
    transitions = {
        (LogisticsStatus.OFFERED, "accept"): LogisticsStatus.ACCEPTED,
        (LogisticsStatus.ACCEPTED, "pickup"): LogisticsStatus.IN_TRANSIT,
        (LogisticsStatus.IN_TRANSIT, "deliver"): LogisticsStatus.DELIVERED,
    }
    target = transitions.get((current, action))
    if target is None:
        raise exchange_error("LOGISTICS_TRANSITION_FORBIDDEN", 409)
    return target


def exchange_error(code: str, status_code: int = 422) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.exchange.{code.lower()}",
        status_code=status_code,
    )
