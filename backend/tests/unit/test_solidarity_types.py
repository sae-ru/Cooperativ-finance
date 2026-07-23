from decimal import Decimal

import pytest

from cooperative_clearing.modules.solidarity.domain.types import (
    AidBucket,
    BucketEntry,
    ContributionForm,
    build_bucket_balances,
    exact_quantity,
    normalize_unit,
)
from cooperative_clearing.shared.domain.errors import DomainError


def test_balances_only_count_verified_facts_in_exact_form_and_unit_buckets() -> None:
    kilograms = AidBucket(ContributionForm.GOODS, "KG")
    hours = AidBucket(ContributionForm.LABOR, "HOUR")

    balances = build_bucket_balances(
        [BucketEntry(kilograms, Decimal("10")), BucketEntry(hours, Decimal("4"))],
        [BucketEntry(kilograms, Decimal("7.5"))],
    )

    assert [(item.bucket, item.available) for item in balances] == [
        (kilograms, Decimal("2.5")),
        (hours, Decimal("4")),
    ]


def test_allocation_cannot_cross_units_or_exceed_verified_balance() -> None:
    kilograms = AidBucket(ContributionForm.GOODS, "KG")
    pieces = AidBucket(ContributionForm.GOODS, "PIECE")

    with pytest.raises(DomainError) as raised:
        build_bucket_balances(
            [BucketEntry(kilograms, Decimal("10"))],
            [BucketEntry(pieces, Decimal("1"))],
        )

    assert raised.value.code == "ALLOCATION_EXCEEDS_VERIFIED_BALANCE"


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "1.0000000000001"])
def test_quantity_rejects_nonpositive_nonfinite_and_excess_precision(value: str) -> None:
    with pytest.raises(DomainError) as raised:
        exact_quantity(value)
    assert raised.value.code == "QUANTITY_INVALID"


def test_unit_normalization_is_stable_and_ascii_only() -> None:
    assert normalize_unit(" kg ") == "KG"
    with pytest.raises(DomainError):
        normalize_unit("килограмм")
