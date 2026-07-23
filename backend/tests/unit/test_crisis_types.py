"""Pure reserve and rationing invariants."""

from decimal import Decimal

import pytest

from cooperative_clearing.modules.crisis.domain.types import (
    EligibleMember,
    QualityStatus,
    RationFormula,
    ReserveLevel,
    allocate_rations,
    assess_reserve,
)
from cooperative_clearing.shared.domain.errors import DomainError


def test_reserve_assessment_uses_only_verified_available_stock() -> None:
    result = assess_reserve(
        verified=Decimal("100"),
        committed=Decimal("20"),
        consumption_per_day=Decimal("10"),
        target=Decimal("90"),
        critical_minimum=Decimal("20"),
        warning_coverage_days=Decimal("10"),
        critical_coverage_days=Decimal("3"),
        confidence=Decimal("0.9"),
        quality_status=QualityStatus.ACCEPTED,
    )
    assert result.available == Decimal("80")
    assert result.coverage_days == Decimal("8.000000000000")
    assert result.level is ReserveLevel.WARNING


def test_rejected_stock_cannot_increase_reserve() -> None:
    with pytest.raises(DomainError, match="REJECTED_STOCK_CANNOT_COUNT"):
        assess_reserve(
            verified=Decimal("1"),
            committed=Decimal("0"),
            consumption_per_day=Decimal("0"),
            target=Decimal("1"),
            critical_minimum=Decimal("0"),
            warning_coverage_days=Decimal("3"),
            critical_coverage_days=Decimal("1"),
            confidence=Decimal("1"),
            quality_status=QualityStatus.REJECTED,
        )


def test_equal_rationing_is_deterministic_and_never_overallocates() -> None:
    members = [EligibleMember("b"), EligibleMember("a"), EligibleMember("c")]
    result = allocate_rations(
        eligible=members,
        available=Decimal("10"),
        protected_minimum=Decimal("2"),
        maximum_per_member=Decimal("4"),
        formula=RationFormula.EQUAL_PER_MEMBER,
    )
    assert [item.member_id for item in result] == ["a", "b", "c"]
    assert sum((item.quantity for item in result), Decimal(0)) <= Decimal("10")
    assert {item.quantity for item in result} == {Decimal("3.333333333333")}


def test_weighted_rationing_rejects_duplicate_member() -> None:
    with pytest.raises(DomainError, match="ELIGIBLE_MEMBER_DUPLICATE"):
        allocate_rations(
            eligible=[EligibleMember("a", 1), EligibleMember("a", 2)],
            available=Decimal("10"),
            protected_minimum=Decimal("0"),
            maximum_per_member=Decimal("10"),
            formula=RationFormula.WEIGHTED_PRIORITY,
        )


def test_weighted_rationing_protects_the_minimum_before_priority_weight() -> None:
    result = allocate_rations(
        eligible=[EligibleMember("a", 1), EligibleMember("b", 9)],
        available=Decimal("20"),
        protected_minimum=Decimal("5"),
        maximum_per_member=Decimal("20"),
        formula=RationFormula.WEIGHTED_PRIORITY,
    )
    assert [item.quantity for item in result] == [Decimal("6"), Decimal("14")]


def test_shortage_below_protected_total_is_shared_equally() -> None:
    result = allocate_rations(
        eligible=[EligibleMember("a", 1), EligibleMember("b", 99)],
        available=Decimal("6"),
        protected_minimum=Decimal("5"),
        maximum_per_member=Decimal("20"),
        formula=RationFormula.WEIGHTED_PRIORITY,
    )
    assert [item.quantity for item in result] == [Decimal("3"), Decimal("3")]
