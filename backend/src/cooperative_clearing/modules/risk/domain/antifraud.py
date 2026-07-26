"""Deterministic helpers used by explainable anti-fraud rules."""

from collections.abc import Sequence
from decimal import Decimal

from cooperative_clearing.modules.risk.domain.types import risk_error


def decimal_median(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise risk_error("ANTIFRAUD_SAMPLE_EMPTY", 500)
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def outside_ratio_band(
    value: Decimal,
    median: Decimal,
    *,
    lower_ratio: Decimal | None,
    upper_ratio: Decimal | None,
) -> bool:
    if median <= 0 or value < 0:
        return False
    if lower_ratio is not None and value < median * lower_ratio:
        return True
    return upper_ratio is not None and value > median * upper_ratio
