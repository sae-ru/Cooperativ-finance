"""Exact amounts and bounded exposure invariants."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

from cooperative_clearing.shared.domain.errors import DomainError

AMOUNT_QUANTUM = Decimal("0.000000000001")
RATIO_QUANTUM = Decimal("0.000001")
MAX_AMOUNT = Decimal("99999999999999999999999999.999999999999")


class ShareContour(StrEnum):
    PRIMARY = "PRIMARY"
    GUARANTEE = "GUARANTEE"
    ROLE = "ROLE"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    SOLIDARITY = "SOLIDARITY"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class PolicyStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class CommitmentType(StrEnum):
    DIRECT_OBLIGATION = "DIRECT_OBLIGATION"
    GUARANTEE = "GUARANTEE"
    CREDIT_LIMIT = "CREDIT_LIMIT"
    ROLE_BOND = "ROLE_BOND"


class CommitmentStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"


class RelatedLinkStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    ENDED = "ENDED"


class LiabilityStatus(StrEnum):
    OPEN = "OPEN"
    ASSESSED = "ASSESSED"
    CLOSED = "CLOSED"


class FaultClass(StrEnum):
    FORCE_MAJEURE = "FORCE_MAJEURE"
    GOOD_FAITH_ERROR = "GOOD_FAITH_ERROR"
    NEGLIGENCE = "NEGLIGENCE"
    GROSS_NEGLIGENCE = "GROSS_NEGLIGENCE"
    INTENT = "INTENT"
    COLLUSION = "COLLUSION"


class AntifraudSubjectType(StrEnum):
    MEMBER = "MEMBER"
    OFFER = "OFFER"
    LOGISTICS_QUOTE = "LOGISTICS_QUOTE"
    PURCHASE_INTENT = "PURCHASE_INTENT"
    SHARE_ACCOUNT = "SHARE_ACCOUNT"
    EXPOSURE_COMMITMENT = "EXPOSURE_COMMITMENT"


class AntifraudSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AntifraudAction(StrEnum):
    WARN = "WARN"
    HOLD = "HOLD"


class AntifraudSignalStatus(StrEnum):
    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    CLEARED = "CLEARED"
    CONFIRMED = "CONFIRMED"


class AntifraudRuleCode(StrEnum):
    OFFER_PRICE_OUTLIER = "OFFER_PRICE_OUTLIER"
    OFFER_REPUBLICATION_BURST = "OFFER_REPUBLICATION_BURST"
    LOGISTICS_PRICE_OUTLIER = "LOGISTICS_PRICE_OUTLIER"
    PURCHASE_CANCELLATION_BURST = "PURCHASE_CANCELLATION_BURST"
    CIRCULAR_GUARANTEE = "CIRCULAR_GUARANTEE"
    COLLATERAL_CONCENTRATION = "COLLATERAL_CONCENTRATION"


@dataclass(frozen=True, slots=True)
class AntifraudFinding:
    rule_code: AntifraudRuleCode
    subject_type: AntifraudSubjectType
    subject_id: UUID
    severity: AntifraudSeverity
    automation_action: AntifraudAction
    reason_key: str
    observed_data: dict[str, object]
    threshold_data: dict[str, object]

EXPOSED_CONTOURS: dict[CommitmentType, frozenset[ShareContour]] = {
    CommitmentType.DIRECT_OBLIGATION: frozenset({ShareContour.GUARANTEE}),
    CommitmentType.GUARANTEE: frozenset({ShareContour.GUARANTEE}),
    CommitmentType.CREDIT_LIMIT: frozenset({ShareContour.GUARANTEE}),
    CommitmentType.ROLE_BOND: frozenset({ShareContour.ROLE}),
}


def exact_amount(value: Decimal, *, allow_zero: bool = False) -> Decimal:
    try:
        normalized = value.quantize(AMOUNT_QUANTUM)
    except InvalidOperation as exc:
        raise risk_error("AMOUNT_INVALID") from exc
    minimum = Decimal(0) if allow_zero else AMOUNT_QUANTUM
    if value != normalized:
        raise risk_error("AMOUNT_PRECISION_INVALID")
    if not value.is_finite() or normalized < minimum or normalized > MAX_AMOUNT:
        raise risk_error("AMOUNT_INVALID")
    return normalized


def exact_ratio(value: Decimal) -> Decimal:
    try:
        normalized = value.quantize(RATIO_QUANTUM)
    except InvalidOperation as exc:
        raise risk_error("COVERAGE_RATIO_INVALID") from exc
    if value != normalized:
        raise risk_error("COVERAGE_RATIO_PRECISION_INVALID")
    if not value.is_finite() or normalized <= 0 or normalized > 1:
        raise risk_error("COVERAGE_RATIO_INVALID")
    return normalized


@dataclass(frozen=True, slots=True)
class AccountAmounts:
    balance: Decimal
    protected: Decimal
    reserved: Decimal
    executed_not_settled: Decimal

    def validate(self) -> "AccountAmounts":
        values = (
            exact_amount(self.balance, allow_zero=True),
            exact_amount(self.protected, allow_zero=True),
            exact_amount(self.reserved, allow_zero=True),
            exact_amount(self.executed_not_settled, allow_zero=True),
        )
        if values != (
            self.balance,
            self.protected,
            self.reserved,
            self.executed_not_settled,
        ):
            raise risk_error("AMOUNT_INVALID")
        if self.protected + self.reserved + self.executed_not_settled > self.balance:
            raise risk_error("ACCOUNT_EXPOSURE_EXCEEDS_BALANCE", 409)
        return self

    @property
    def available(self) -> Decimal:
        self.validate()
        return self.balance - self.protected - self.reserved - self.executed_not_settled


@dataclass(frozen=True, slots=True)
class ExposurePreview:
    account_available_before: Decimal
    account_available_after: Decimal
    member_exposure_before: Decimal
    member_exposure_after: Decimal
    related_exposure_before: Decimal
    related_exposure_after: Decimal
    max_member_exposure: Decimal
    max_related_exposure: Decimal
    allowed: bool
    reason_code: str | None


def preview_exposure(
    *,
    account: AccountAmounts,
    proposed_reservation: Decimal,
    proposed_max_loss: Decimal,
    member_exposure: Decimal,
    related_exposure: Decimal,
    max_member_exposure: Decimal,
    max_related_exposure: Decimal,
) -> ExposurePreview:
    account.validate()
    amount = exact_amount(proposed_reservation)
    max_loss = exact_amount(proposed_max_loss)
    direct = exact_amount(member_exposure, allow_zero=True)
    related = exact_amount(related_exposure, allow_zero=True)
    direct_limit = exact_amount(max_member_exposure)
    related_limit = exact_amount(max_related_exposure)
    if max_loss > amount:
        raise risk_error("MAX_LOSS_EXCEEDS_RESERVATION")
    after_available = account.available - amount
    after_direct = direct + max_loss
    after_related = related + max_loss
    reason: str | None = None
    if after_available < 0:
        reason = "ACCOUNT_AVAILABLE_EXCEEDED"
    elif after_direct > direct_limit:
        reason = "MEMBER_EXPOSURE_LIMIT_EXCEEDED"
    elif after_related > related_limit:
        reason = "RELATED_EXPOSURE_LIMIT_EXCEEDED"
    return ExposurePreview(
        account_available_before=account.available,
        account_available_after=after_available,
        member_exposure_before=direct,
        member_exposure_after=after_direct,
        related_exposure_before=related,
        related_exposure_after=after_related,
        max_member_exposure=direct_limit,
        max_related_exposure=related_limit,
        allowed=reason is None,
        reason_code=reason,
    )


def ensure_contour_supports(contour: ShareContour, commitment: CommitmentType) -> None:
    if contour not in EXPOSED_CONTOURS[commitment]:
        raise risk_error("SHARE_CONTOUR_NOT_EXPOSABLE", 409)


def risk_error(code: str, status_code: int = 422) -> DomainError:
    return DomainError(
        code=f"RISK_{code}",
        message_key=f"errors.risk.{code.lower()}",
        status_code=status_code,
    )
