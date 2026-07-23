import pytest

from cooperative_clearing.modules.responsibility.domain.types import (
    ResponsibilityStatus,
    ensure_can_accept,
    ensure_can_decide,
)
from cooperative_clearing.shared.domain.errors import DomainError


def test_responsibility_lifecycle_guards() -> None:
    ensure_can_decide(ResponsibilityStatus.PENDING_APPROVAL)
    ensure_can_accept(ResponsibilityStatus.PENDING_ACCEPTANCE)
    with pytest.raises(DomainError):
        ensure_can_decide(ResponsibilityStatus.ACTIVE)
    with pytest.raises(DomainError):
        ensure_can_accept(ResponsibilityStatus.PENDING_APPROVAL)
