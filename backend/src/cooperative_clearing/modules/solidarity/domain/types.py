"""Types and pure balance rules for voluntary solidarity aid."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from cooperative_clearing.shared.domain.errors import DomainError


class ContributionForm(StrEnum):
    MONEY = "MONEY"
    GOODS = "GOODS"
    LABOR = "LABOR"
    SERVICE = "SERVICE"
    LOGISTICS = "LOGISTICS"
    INFRASTRUCTURE = "INFRASTRUCTURE"


class ResidueRule(StrEnum):
    RETAIN_IN_FUND = "RETAIN_IN_FUND"
    RETURN_TO_DONORS = "RETURN_TO_DONORS"
    TRANSFER_APPROVED_CAMPAIGN = "TRANSFER_APPROVED_CAMPAIGN"


class PrivacyScope(StrEnum):
    PARTICIPANT_STAFF = "PARTICIPANT_STAFF"
    RESTRICTED = "RESTRICTED"


class NeedCategory(StrEnum):
    BASIC_FOOD = "BASIC_FOOD"
    MEDICAL = "MEDICAL"
    SHELTER = "SHELTER"
    TRANSPORT = "TRANSPORT"
    CARE = "CARE"
    OTHER = "OTHER"


class DeliveryAttestorKind(StrEnum):
    RECIPIENT = "RECIPIENT"
    REPRESENTATIVE = "REPRESENTATIVE"
    WITNESS = "WITNESS"


@dataclass(frozen=True, slots=True, order=True)
class AidBucket:
    contribution_form: ContributionForm
    unit_code: str


@dataclass(frozen=True, slots=True)
class BucketEntry:
    bucket: AidBucket
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class BucketBalance:
    bucket: AidBucket
    verified: Decimal
    reserved_or_delivered: Decimal
    available: Decimal


def solidarity_error(code: str, status_code: int = 409) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.solidarity.{code.lower()}",
        status_code=status_code,
    )


def exact_quantity(value: Decimal | str) -> Decimal:
    try:
        quantity = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise solidarity_error("QUANTITY_INVALID", 422) from exc
    exponent = quantity.as_tuple().exponent
    if not quantity.is_finite() or quantity <= 0 or not isinstance(exponent, int) or exponent < -12:
        raise solidarity_error("QUANTITY_INVALID", 422)
    if quantity.adjusted() > 25:
        raise solidarity_error("QUANTITY_INVALID", 422)
    return quantity


def normalize_unit(value: str) -> str:
    normalized = value.strip().upper()
    if (
        not normalized
        or len(normalized) > 24
        or not normalized.isascii()
        or not all(character.isalnum() or character in {"_", "-", "."} for character in normalized)
    ):
        raise solidarity_error("UNIT_CODE_INVALID", 422)
    return normalized


def build_bucket_balances(
    verified_contributions: Iterable[BucketEntry],
    reserved_or_delivered_allocations: Iterable[BucketEntry],
) -> tuple[BucketBalance, ...]:
    verified: dict[AidBucket, Decimal] = {}
    allocated: dict[AidBucket, Decimal] = {}
    for entry in verified_contributions:
        verified[entry.bucket] = verified.get(entry.bucket, Decimal(0)) + exact_quantity(
            entry.quantity
        )
    for entry in reserved_or_delivered_allocations:
        allocated[entry.bucket] = allocated.get(entry.bucket, Decimal(0)) + exact_quantity(
            entry.quantity
        )
    balances: list[BucketBalance] = []
    for bucket in sorted(set(verified) | set(allocated)):
        total = verified.get(bucket, Decimal(0))
        used = allocated.get(bucket, Decimal(0))
        if used > total:
            raise solidarity_error("ALLOCATION_EXCEEDS_VERIFIED_BALANCE")
        balances.append(BucketBalance(bucket, total, used, total - used))
    return tuple(balances)
