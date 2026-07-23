from decimal import Decimal

import pytest

from cooperative_clearing.modules.risk.domain.types import (
    AccountAmounts,
    CommitmentType,
    ShareContour,
    ensure_contour_supports,
    exact_amount,
    exact_ratio,
    preview_exposure,
)
from cooperative_clearing.shared.domain.errors import DomainError


def account(
    *,
    balance: str = "100",
    protected: str = "20",
    reserved: str = "10",
    executed: str = "5",
) -> AccountAmounts:
    return AccountAmounts(
        balance=Decimal(balance),
        protected=Decimal(protected),
        reserved=Decimal(reserved),
        executed_not_settled=Decimal(executed),
    ).validate()


def test_available_amount_excludes_protected_reserved_and_unsettled_shares() -> None:
    assert account().available == Decimal("65")


def test_account_cannot_expose_more_than_its_balance() -> None:
    with pytest.raises(DomainError, match="RISK_ACCOUNT_EXPOSURE_EXCEEDS_BALANCE"):
        account(protected="60", reserved="30", executed="11")


def test_preview_reports_each_bounded_failure_without_mutating_account() -> None:
    current = account()
    available_failure = preview_exposure(
        account=current,
        proposed_reservation=Decimal("66"),
        proposed_max_loss=Decimal("10"),
        member_exposure=Decimal("0"),
        related_exposure=Decimal("0"),
        max_member_exposure=Decimal("100"),
        max_related_exposure=Decimal("150"),
    )
    assert available_failure.reason_code == "ACCOUNT_AVAILABLE_EXCEEDED"

    member_failure = preview_exposure(
        account=current,
        proposed_reservation=Decimal("20"),
        proposed_max_loss=Decimal("20"),
        member_exposure=Decimal("85"),
        related_exposure=Decimal("85"),
        max_member_exposure=Decimal("100"),
        max_related_exposure=Decimal("150"),
    )
    assert member_failure.reason_code == "MEMBER_EXPOSURE_LIMIT_EXCEEDED"

    related_failure = preview_exposure(
        account=current,
        proposed_reservation=Decimal("20"),
        proposed_max_loss=Decimal("20"),
        member_exposure=Decimal("50"),
        related_exposure=Decimal("135"),
        max_member_exposure=Decimal("100"),
        max_related_exposure=Decimal("150"),
    )
    assert related_failure.reason_code == "RELATED_EXPOSURE_LIMIT_EXCEEDED"
    assert current.available == Decimal("65")


def test_preview_rejects_loss_above_reserved_amount() -> None:
    with pytest.raises(DomainError, match="RISK_MAX_LOSS_EXCEEDS_RESERVATION"):
        preview_exposure(
            account=account(),
            proposed_reservation=Decimal("10"),
            proposed_max_loss=Decimal("11"),
            member_exposure=Decimal("0"),
            related_exposure=Decimal("0"),
            max_member_exposure=Decimal("100"),
            max_related_exposure=Decimal("150"),
        )


def test_amount_and_ratio_precision_are_exact_and_never_rounded() -> None:
    assert exact_amount(Decimal("1.000000000001")) == Decimal("1.000000000001")
    assert exact_ratio(Decimal("0.750000")) == Decimal("0.750000")
    with pytest.raises(DomainError, match="RISK_AMOUNT_PRECISION_INVALID"):
        exact_amount(Decimal("1.0000000000001"))
    with pytest.raises(DomainError, match="RISK_COVERAGE_RATIO_PRECISION_INVALID"):
        exact_ratio(Decimal("0.1234567"))
    with pytest.raises(DomainError, match="RISK_COVERAGE_RATIO_INVALID"):
        exact_ratio(Decimal("1.000001"))


def test_only_explicit_guarantee_and_role_contours_can_be_reserved() -> None:
    ensure_contour_supports(ShareContour.GUARANTEE, CommitmentType.GUARANTEE)
    ensure_contour_supports(ShareContour.ROLE, CommitmentType.ROLE_BOND)
    for contour in (
        ShareContour.PRIMARY,
        ShareContour.INFRASTRUCTURE,
        ShareContour.SOLIDARITY,
    ):
        with pytest.raises(DomainError, match="RISK_SHARE_CONTOUR_NOT_EXPOSABLE"):
            ensure_contour_supports(contour, CommitmentType.DIRECT_OBLIGATION)
