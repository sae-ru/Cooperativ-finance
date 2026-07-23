"""Exact-balance rules and state machines for commodity rights."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from cooperative_clearing.modules.inventory.domain.types import exact_quantity
from cooperative_clearing.shared.domain.errors import DomainError


class ReservationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class RightStatus(StrEnum):
    ISSUED = "ISSUED"
    TRANSFERRED = "TRANSFERRED"
    REDEMPTION_PENDING = "REDEMPTION_PENDING"
    FROZEN = "FROZEN"
    REDEEMED = "REDEEMED"
    EXPIRED = "EXPIRED"
    CANCELLED_BY_COMPENSATION = "CANCELLED_BY_COMPENSATION"


class RedemptionStatus(StrEnum):
    REQUESTED = "REQUESTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


ACTIVE_RIGHT_STATUSES = frozenset({RightStatus.ISSUED, RightStatus.TRANSFERRED})
FREEZABLE_RIGHT_STATUSES = frozenset(
    {RightStatus.ISSUED, RightStatus.TRANSFERRED, RightStatus.REDEMPTION_PENDING}
)


@dataclass(frozen=True, slots=True)
class BalanceState:
    verified: Decimal
    available: Decimal
    reserved: Decimal
    issued: Decimal
    redeemed: Decimal
    quarantined: Decimal
    shortfall: Decimal

    def validate(self) -> "BalanceState":
        values = (
            self.verified,
            self.available,
            self.reserved,
            self.issued,
            self.redeemed,
            self.quarantined,
            self.shortfall,
        )
        if any(exact_quantity(value, allow_zero=True) != value for value in values):
            raise rights_error("BALANCE_QUANTITY_INVALID")
        allocated = self.available + self.reserved + self.issued + self.quarantined
        if allocated != self.verified + self.shortfall:
            raise rights_error("BALANCE_INVARIANT_BROKEN", 409)
        return self

    def reserve_and_issue(self, quantity: Decimal) -> "BalanceState":
        amount = exact_quantity(quantity)
        if amount > self.available:
            raise rights_error("INSUFFICIENT_AVAILABLE_QUANTITY", 409)
        return BalanceState(
            verified=self.verified,
            available=self.available - amount,
            reserved=self.reserved,
            issued=self.issued + amount,
            redeemed=self.redeemed,
            quarantined=self.quarantined,
            shortfall=self.shortfall,
        ).validate()

    def redeem(self, quantity: Decimal) -> "BalanceState":
        amount = exact_quantity(quantity)
        if amount > self.issued or amount > self.verified:
            raise rights_error("RIGHT_BACKING_UNAVAILABLE", 409)
        return BalanceState(
            verified=self.verified - amount,
            available=self.available,
            reserved=self.reserved,
            issued=self.issued - amount,
            redeemed=self.redeemed + amount,
            quarantined=self.quarantined,
            shortfall=self.shortfall,
        ).validate()

    def quarantine_physical_count(self, actual: Decimal) -> "BalanceState":
        measured = exact_quantity(actual, allow_zero=True)
        committed = self.reserved + self.issued
        shortfall = max(committed - measured, Decimal(0))
        quarantined = max(measured - committed, Decimal(0))
        return BalanceState(
            verified=measured,
            available=Decimal(0),
            reserved=self.reserved,
            issued=self.issued,
            redeemed=self.redeemed,
            quarantined=quarantined,
            shortfall=shortfall,
        ).validate()


def ensure_right_operable(status: RightStatus, valid_until: datetime | None) -> None:
    if status is RightStatus.FROZEN:
        raise rights_error("RIGHT_FROZEN", 409)
    if status not in ACTIVE_RIGHT_STATUSES:
        raise rights_error("RIGHT_NOT_OPERABLE", 409)
    if valid_until is not None and valid_until.astimezone(UTC) <= datetime.now(UTC):
        raise rights_error("RIGHT_EXPIRED", 409)


def ensure_right_owner(owner_member_id: UUID, expected_owner_member_id: UUID) -> None:
    if owner_member_id != expected_owner_member_id:
        raise rights_error("RIGHT_OWNER_CHANGED", 409)


def rights_error(code: str, status_code: int = 422) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.rights.{code.lower()}",
        status_code=status_code,
    )
