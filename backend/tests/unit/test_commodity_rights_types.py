from decimal import Decimal
from uuid import uuid4

import pytest

from cooperative_clearing.modules.rights.domain.types import (
    BalanceState,
    RightStatus,
    ensure_right_operable,
    ensure_right_owner,
)
from cooperative_clearing.shared.domain.errors import DomainError


def balance(available: str = "100", issued: str = "0") -> BalanceState:
    return BalanceState(
        verified=Decimal("100"),
        available=Decimal(available),
        reserved=Decimal(0),
        issued=Decimal(issued),
        redeemed=Decimal(0),
        quarantined=Decimal(0),
        shortfall=Decimal(0),
    ).validate()


def test_issue_and_redeem_preserve_exact_backing() -> None:
    issued = balance().reserve_and_issue(Decimal("37.25"))
    assert issued.available == Decimal("62.75")
    assert issued.issued == Decimal("37.25")

    redeemed = issued.redeem(Decimal("12.25"))
    assert redeemed.verified == Decimal("87.75")
    assert redeemed.issued == Decimal("25.00")
    assert redeemed.redeemed == Decimal("12.25")
    assert redeemed.available + redeemed.issued == redeemed.verified


def test_over_issue_is_rejected() -> None:
    with pytest.raises(DomainError, match="INSUFFICIENT_AVAILABLE_QUANTITY"):
        balance().reserve_and_issue(Decimal("100.01"))


def test_physical_shortfall_is_explicit_and_quarantined() -> None:
    state = balance(available="60", issued="40").quarantine_physical_count(Decimal("30"))
    assert state.available == 0
    assert state.quarantined == 0
    assert state.shortfall == Decimal("10")
    assert state.issued == Decimal("40")


def test_uncommitted_remainder_is_quarantined_after_discrepancy() -> None:
    state = balance(available="60", issued="40").quarantine_physical_count(Decimal("90"))
    assert state.available == 0
    assert state.quarantined == Decimal("50")
    assert state.shortfall == 0


def test_frozen_and_wrong_owner_rights_are_not_operable() -> None:
    with pytest.raises(DomainError, match="RIGHT_FROZEN"):
        ensure_right_operable(RightStatus.FROZEN, None)
    with pytest.raises(DomainError, match="RIGHT_OWNER_CHANGED"):
        ensure_right_owner(uuid4(), uuid4())
