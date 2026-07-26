from decimal import Decimal

from cooperative_clearing.modules.risk.domain.antifraud import (
    decimal_median,
    outside_ratio_band,
)


def test_ratio_rules_are_exact_and_deterministic() -> None:
    assert decimal_median([Decimal("11"), Decimal("9"), Decimal("10")]) == Decimal("10")
    assert decimal_median(
        [Decimal("12"), Decimal("9"), Decimal("10"), Decimal("11")]
    ) == Decimal("10.5")
    assert outside_ratio_band(
        Decimal("21"),
        Decimal("10"),
        lower_ratio=Decimal("0.5"),
        upper_ratio=Decimal("2"),
    )
    assert not outside_ratio_band(
        Decimal("20"),
        Decimal("10"),
        lower_ratio=Decimal("0.5"),
        upper_ratio=Decimal("2"),
    )
    assert not outside_ratio_band(
        Decimal("100"),
        Decimal("0"),
        lower_ratio=None,
        upper_ratio=Decimal("2"),
    )
