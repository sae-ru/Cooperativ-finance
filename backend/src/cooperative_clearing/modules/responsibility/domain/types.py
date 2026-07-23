"""Lifecycle rules for explicit personal responsibility."""

from enum import StrEnum

from cooperative_clearing.shared.domain.errors import DomainError


class ResponsibilityStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    PENDING_ACCEPTANCE = "PENDING_ACCEPTANCE"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    RELEASED = "RELEASED"


class ApprovalDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


def ensure_can_decide(status: ResponsibilityStatus) -> None:
    if status is not ResponsibilityStatus.PENDING_APPROVAL:
        raise _invalid_transition(status, "DECIDE")


def ensure_can_accept(status: ResponsibilityStatus) -> None:
    if status is not ResponsibilityStatus.PENDING_ACCEPTANCE:
        raise _invalid_transition(status, "ACCEPT")


def _invalid_transition(current: ResponsibilityStatus, action: str) -> DomainError:
    return DomainError(
        code="RESPONSIBILITY_TRANSITION_INVALID",
        message_key="errors.responsibility.transition_invalid",
        parameters={"current": current.value, "action": action},
        status_code=409,
    )
