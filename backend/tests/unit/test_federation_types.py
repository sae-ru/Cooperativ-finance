from decimal import Decimal

import pytest

from cooperative_clearing.modules.federation.domain.types import (
    bounded_amount,
    ensure_compatible,
    normalize_code,
    preview_exposure,
)
from cooperative_clearing.shared.domain.errors import DomainError


def test_exposure_preview_is_exact_and_bounded() -> None:
    preview = preview_exposure(
        current=Decimal("10.100000000001"),
        reserved=Decimal("2.25"),
        delta=Decimal("7.65"),
        limit=Decimal("20.000000000001"),
    )
    assert preview.after == Decimal("20.000000000001")

    with pytest.raises(DomainError) as error:
        preview_exposure(
            current=Decimal("10.1"),
            reserved=Decimal("2.25"),
            delta=Decimal("7.650000000002"),
            limit=Decimal("20"),
        )
    assert error.value.code == "NODE_EXPOSURE_LIMIT_EXCEEDED"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-0.01", "0.0000000000001", "1e26"])
def test_bounded_amount_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(DomainError) as error:
        bounded_amount(value)
    assert error.value.code == "AMOUNT_INVALID"


@pytest.mark.parametrize("value", ["10.000000000000000", "0E-15", "1.2300000000000"])
def test_bounded_amount_accepts_safe_trailing_zeroes(value: str) -> None:
    assert bounded_amount(value) == Decimal(value)


def test_node_codes_are_ascii_canonical() -> None:
    assert normalize_code("  west-01.demo ") == "WEST-01.DEMO"
    with pytest.raises(DomainError):
        normalize_code("узел-01")


def test_protocol_and_policy_compatibility_is_exact() -> None:
    ensure_compatible(
        protocol_version="1.0",
        supported_protocols=["1.0", "1.1"],
        required_policies={"clearing": 4, "identity": 2},
        supported_policies={"clearing": 4, "identity": 2},
    )
    with pytest.raises(DomainError) as error:
        ensure_compatible(
            protocol_version="1.0",
            supported_protocols=["1.0"],
            required_policies={"clearing": 4},
            supported_policies={"clearing": 3},
        )
    assert error.value.code == "POLICY_VERSION_MISMATCH"
