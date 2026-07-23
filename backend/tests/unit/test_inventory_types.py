from decimal import Decimal

import pytest

from cooperative_clearing.modules.inventory.domain.types import (
    LotStatus,
    QualityDecision,
    QuantityDecision,
    ensure_unit_scale,
    evaluate_attestation,
    exact_quantity,
)
from cooperative_clearing.shared.domain.errors import DomainError


def test_attestation_outcomes_are_deterministic() -> None:
    accepted = evaluate_attestation(
        Decimal("100.000"),
        Decimal("99.500"),
        Decimal("0.500"),
        QualityDecision.ACCEPTED,
    )
    assert accepted.quantity_decision is QuantityDecision.WITHIN_TOLERANCE
    assert accepted.lot_status is LotStatus.VERIFIED

    disputed = evaluate_attestation(
        Decimal("100.000"),
        Decimal("98.000"),
        Decimal("0.500"),
        QualityDecision.ACCEPTED,
    )
    assert disputed.quantity_decision is QuantityDecision.DISCREPANCY
    assert disputed.lot_status is LotStatus.DISPUTED

    rejected = evaluate_attestation(
        Decimal("100.000"),
        Decimal("100.000"),
        Decimal("0.500"),
        QualityDecision.REJECTED,
    )
    assert rejected.lot_status is LotStatus.FROZEN


def test_quantities_reject_rounding_and_unit_scale_overflow() -> None:
    with pytest.raises(DomainError, match="INVENTORY_QUANTITY_PRECISION_INVALID"):
        exact_quantity(Decimal("1.0000000000001"))
    with pytest.raises(DomainError, match="INVENTORY_UNIT_SCALE_EXCEEDED"):
        ensure_unit_scale(Decimal("1.001"), 2)
