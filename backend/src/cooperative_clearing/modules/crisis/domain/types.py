"""Pure calculations for verified reserves and deterministic rationing."""

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from enum import StrEnum

from cooperative_clearing.shared.domain.errors import DomainError


class CrisisType(StrEnum):
    PAYMENT_FAILURE = "PAYMENT_FAILURE"
    CONNECTIVITY_LOSS = "CONNECTIVITY_LOSS"
    CRITICAL_SHORTAGE = "CRITICAL_SHORTAGE"
    ENERGY_FAILURE = "ENERGY_FAILURE"
    LOGISTICS_FAILURE = "LOGISTICS_FAILURE"
    KEY_COMPROMISE = "KEY_COMPROMISE"
    MASS_DEFAULT = "MASS_DEFAULT"
    WAREHOUSE_INCIDENT = "WAREHOUSE_INCIDENT"
    PROTOCOL_INCOMPATIBILITY = "PROTOCOL_INCOMPATIBILITY"


class CrisisCapability(StrEnum):
    RESTRICT_NEW_RIGHTS = "RESTRICT_NEW_RIGHTS"
    REQUIRE_STRONGER_EVIDENCE = "REQUIRE_STRONGER_EVIDENCE"
    OPEN_OFFLINE_EPOCH = "OPEN_OFFLINE_EPOCH"
    PRIORITIZE_CRITICAL_RESOURCES = "PRIORITIZE_CRITICAL_RESOURCES"
    ENABLE_RATIONING = "ENABLE_RATIONING"
    FREEZE_DISPUTED_OBJECTS = "FREEZE_DISPUTED_OBJECTS"
    ENABLE_PAPER_FORMS = "ENABLE_PAPER_FORMS"
    RESTRICT_FEDERATION = "RESTRICT_FEDERATION"
    ENHANCED_AUDIT = "ENHANCED_AUDIT"


class ReserveLevel(StrEnum):
    UNKNOWN = "UNKNOWN"
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class QualityStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"


class RationFormula(StrEnum):
    EQUAL_PER_MEMBER = "EQUAL_PER_MEMBER"
    WEIGHTED_PRIORITY = "WEIGHTED_PRIORITY"


@dataclass(frozen=True, slots=True)
class ReserveAssessment:
    verified: Decimal
    committed: Decimal
    available: Decimal
    consumption_per_day: Decimal
    coverage_days: Decimal | None
    level: ReserveLevel


@dataclass(frozen=True, slots=True)
class EligibleMember:
    member_id: str
    weight: int = 1


@dataclass(frozen=True, slots=True)
class RationShare:
    member_id: str
    weight: int
    quantity: Decimal


def crisis_error(code: str, status_code: int = 409) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.crisis.{code.lower()}",
        status_code=status_code,
    )


def quantity(value: Decimal | str, *, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise crisis_error("QUANTITY_INVALID", 422) from exc
    exponent = result.as_tuple().exponent
    invalid_sign = result < 0 if allow_zero else result <= 0
    if (
        not result.is_finite()
        or invalid_sign
        or not isinstance(exponent, int)
        or exponent < -12
        or (result != 0 and result.adjusted() > 25)
    ):
        raise crisis_error("QUANTITY_INVALID", 422)
    return result


def ratio(value: Decimal | str) -> Decimal:
    result = quantity(value, allow_zero=True)
    if result > 1:
        raise crisis_error("RATIO_INVALID", 422)
    return result


def normalize_code(value: str, *, maximum: int = 64) -> str:
    normalized = value.strip().upper()
    if (
        not normalized
        or len(normalized) > maximum
        or not normalized.isascii()
        or not all(character.isalnum() or character in {"_", "-", "."} for character in normalized)
    ):
        raise crisis_error("CODE_INVALID", 422)
    return normalized


def assess_reserve(
    *,
    verified: Decimal,
    committed: Decimal,
    consumption_per_day: Decimal,
    target: Decimal,
    critical_minimum: Decimal,
    warning_coverage_days: Decimal,
    critical_coverage_days: Decimal,
    confidence: Decimal,
    quality_status: QualityStatus,
) -> ReserveAssessment:
    verified_value = quantity(verified, allow_zero=True)
    committed_value = quantity(committed, allow_zero=True)
    rate = quantity(consumption_per_day, allow_zero=True)
    target_value = quantity(target)
    critical_value = quantity(critical_minimum, allow_zero=True)
    warning_days = quantity(warning_coverage_days, allow_zero=True)
    critical_days = quantity(critical_coverage_days, allow_zero=True)
    confidence_value = ratio(confidence)
    if critical_value > target_value or critical_days > warning_days:
        raise crisis_error("RESERVE_POLICY_INVALID", 422)
    if committed_value > verified_value:
        raise crisis_error("COMMITTED_EXCEEDS_VERIFIED", 422)
    if quality_status is QualityStatus.REJECTED and verified_value != 0:
        raise crisis_error("REJECTED_STOCK_CANNOT_COUNT", 422)
    available = verified_value - committed_value
    coverage = None if rate == 0 else (available / rate).quantize(Decimal("0.000000000001"))
    if confidence_value < Decimal("0.5") or quality_status is QualityStatus.REJECTED:
        level = ReserveLevel.UNKNOWN
    elif available <= critical_value or (coverage is not None and coverage <= critical_days):
        level = ReserveLevel.CRITICAL
    elif (
        available < target_value
        or (coverage is not None and coverage <= warning_days)
        or quality_status is QualityStatus.DEGRADED
    ):
        level = ReserveLevel.WARNING
    else:
        level = ReserveLevel.NORMAL
    return ReserveAssessment(
        verified=verified_value,
        committed=committed_value,
        available=available,
        consumption_per_day=rate,
        coverage_days=coverage,
        level=level,
    )


def allocate_rations(
    *,
    eligible: list[EligibleMember],
    available: Decimal,
    protected_minimum: Decimal,
    maximum_per_member: Decimal,
    formula: RationFormula,
) -> tuple[RationShare, ...]:
    if not eligible:
        raise crisis_error("ELIGIBLE_MEMBERS_REQUIRED", 422)
    if len({item.member_id for item in eligible}) != len(eligible):
        raise crisis_error("ELIGIBLE_MEMBER_DUPLICATE", 422)
    if any(item.weight < 1 or item.weight > 100 for item in eligible):
        raise crisis_error("ELIGIBLE_WEIGHT_INVALID", 422)
    available_value = quantity(available, allow_zero=True)
    protected = quantity(protected_minimum, allow_zero=True)
    maximum = quantity(maximum_per_member)
    if protected > maximum:
        raise crisis_error("RATION_LIMIT_INVALID", 422)
    ordered = sorted(eligible, key=lambda item: item.member_id)
    quantum = Decimal("0.000000000001")
    protected_total = protected * len(ordered)
    base = (
        protected
        if available_value >= protected_total
        else (available_value / len(ordered)).quantize(quantum, rounding=ROUND_DOWN)
    )
    amounts = [min(base, maximum) for _ in ordered]
    remaining = available_value - sum(amounts, Decimal(0))
    weights = [1 if formula is RationFormula.EQUAL_PER_MEMBER else item.weight for item in ordered]
    active = {index for index, amount in enumerate(amounts) if amount < maximum}
    while remaining >= quantum and active:
        total_weight = sum(weights[index] for index in active)
        additions = {
            index: min(
                maximum - amounts[index],
                (remaining * Decimal(weights[index]) / Decimal(total_weight)).quantize(
                    quantum, rounding=ROUND_DOWN
                ),
            )
            for index in active
        }
        distributed = sum(additions.values(), Decimal(0))
        if distributed == 0:
            break
        for index, addition in additions.items():
            amounts[index] += addition
        remaining -= distributed
        active = {index for index in active if amounts[index] < maximum}
    shares = [
        RationShare(item.member_id, item.weight, amounts[index])
        for index, item in enumerate(ordered)
    ]
    if sum((item.quantity for item in shares), Decimal(0)) > available_value:
        raise crisis_error("RATION_EXCEEDS_AVAILABLE")
    return tuple(shares)
