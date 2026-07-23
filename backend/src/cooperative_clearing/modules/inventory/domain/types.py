"""State machines and exact-quantity rules for physical inventory."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from cooperative_clearing.shared.domain.errors import DomainError

QUANTITY_QUANTUM = Decimal("0.000000000001")
MAX_QUANTITY = Decimal("99999999999999999999999999.999999999999")


class LotStatus(StrEnum):
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    DISPUTED = "DISPUTED"
    FROZEN = "FROZEN"
    LOST = "LOST"
    DEPLETED = "DEPLETED"


class QualityDecision(StrEnum):
    ACCEPTED = "ACCEPTED"
    CONDITIONAL = "CONDITIONAL"
    REJECTED = "REJECTED"


class QuantityDecision(StrEnum):
    MATCH = "MATCH"
    WITHIN_TOLERANCE = "WITHIN_TOLERANCE"
    DISCREPANCY = "DISCREPANCY"


class CustodyStatus(StrEnum):
    OFFERED = "OFFERED"
    ACCEPTED = "ACCEPTED"
    CANCELLED = "CANCELLED"


class EvidenceStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class AttestationOutcome:
    measured_quantity: Decimal
    variance: Decimal
    quantity_decision: QuantityDecision
    lot_status: LotStatus


def exact_quantity(value: Decimal, *, allow_zero: bool = False) -> Decimal:
    try:
        normalized = value.quantize(QUANTITY_QUANTUM)
    except InvalidOperation as exc:
        raise _error("INVENTORY_QUANTITY_INVALID") from exc
    minimum = Decimal("0") if allow_zero else QUANTITY_QUANTUM
    if value != normalized:
        raise _error("INVENTORY_QUANTITY_PRECISION_INVALID")
    if not value.is_finite() or normalized < minimum or normalized > MAX_QUANTITY:
        raise _error("INVENTORY_QUANTITY_INVALID")
    return normalized


def evaluate_attestation(
    declared_quantity: Decimal,
    measured_quantity: Decimal,
    tolerance: Decimal,
    quality: QualityDecision,
) -> AttestationOutcome:
    declared = exact_quantity(declared_quantity)
    measured = exact_quantity(measured_quantity, allow_zero=True)
    allowed = exact_quantity(tolerance, allow_zero=True)
    variance = (measured - declared).quantize(QUANTITY_QUANTUM)
    if variance == 0:
        quantity_decision = QuantityDecision.MATCH
    elif abs(variance) <= allowed:
        quantity_decision = QuantityDecision.WITHIN_TOLERANCE
    else:
        quantity_decision = QuantityDecision.DISCREPANCY

    if measured == 0:
        status = LotStatus.LOST
    elif quality is QualityDecision.REJECTED:
        status = LotStatus.FROZEN
    elif quantity_decision is QuantityDecision.DISCREPANCY:
        status = LotStatus.DISPUTED
    else:
        status = LotStatus.VERIFIED
    return AttestationOutcome(measured, variance, quantity_decision, status)


def ensure_can_attest(status: LotStatus) -> None:
    if status is not LotStatus.PENDING_VERIFICATION:
        raise _error("LOT_NOT_PENDING_VERIFICATION", 409)


def ensure_can_record_discrepancy(status: LotStatus) -> None:
    if status not in {LotStatus.VERIFIED, LotStatus.DISPUTED, LotStatus.FROZEN}:
        raise _error("LOT_NOT_COUNTABLE", 409)


def ensure_can_offer_custody(status: LotStatus) -> None:
    if status in {LotStatus.LOST, LotStatus.DEPLETED}:
        raise _error("LOT_CUSTODY_TRANSFER_FORBIDDEN", 409)


def ensure_unit_scale(value: Decimal, scale: int) -> None:
    quantum = Decimal(1).scaleb(-scale)
    if value != value.quantize(quantum):
        raise _error("INVENTORY_UNIT_SCALE_EXCEEDED")


def decimal_text(value: Decimal) -> str:
    return format(value.quantize(QUANTITY_QUANTUM), "f")


def _error(code: str, status_code: int = 422) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.inventory.{code.lower()}",
        status_code=status_code,
    )
