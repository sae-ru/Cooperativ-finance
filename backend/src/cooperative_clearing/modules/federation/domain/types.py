"""Pure validation, compatibility, and bounded-exposure rules for peer nodes."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from cooperative_clearing.shared.domain.errors import DomainError


class NodeStatus(StrEnum):
    DRAFT = "DRAFT"
    APPLICATION_SUBMITTED = "APPLICATION_SUBMITTED"
    IDENTITY_VERIFIED = "IDENTITY_VERIFIED"
    TECHNICAL_CHALLENGE = "TECHNICAL_CHALLENGE"
    AUDIT_PENDING = "AUDIT_PENDING"
    LIMITED = "LIMITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    QUARANTINED = "QUARANTINED"
    REVOKED = "REVOKED"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"


class TrustLevel(StrEnum):
    UNTRUSTED = "UNTRUSTED"
    LIMITED = "LIMITED"
    STANDARD = "STANDARD"
    HIGH = "HIGH"


class NodeCapability(StrEnum):
    TEST_EXCHANGE = "TEST_EXCHANGE"
    CATALOG = "CATALOG"
    STORAGE = "STORAGE"
    LOGISTICS = "LOGISTICS"
    RIGHTS = "RIGHTS"
    CLEARING = "CLEARING"
    AUDIT = "AUDIT"
    RELAY = "RELAY"


class ResponsibleRole(StrEnum):
    OWNER_SIGNATORY = "OWNER_SIGNATORY"
    TECHNICAL_CUSTODIAN = "TECHNICAL_CUSTODIAN"
    SECURITY_ADMINISTRATOR = "SECURITY_ADMINISTRATOR"
    BUSINESS_OPERATOR = "BUSINESS_OPERATOR"
    NODE_AUDITOR = "NODE_AUDITOR"
    SPONSOR_APPROVER = "SPONSOR_APPROVER"


class PackageStatus(StrEnum):
    EXPORTED = "EXPORTED"
    QUARANTINED = "QUARANTINED"
    VERIFIED = "VERIFIED"
    SIMULATED = "SIMULATED"
    CONFLICT = "CONFLICT"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"


class ConflictClass(StrEnum):
    DUPLICATE = "DUPLICATE"
    TAMPERED_DUPLICATE = "TAMPERED_DUPLICATE"
    REFERENTIAL_GAP = "REFERENTIAL_GAP"
    CONCURRENT_METADATA = "CONCURRENT_METADATA"
    COMPETING_RESERVATION = "COMPETING_RESERVATION"
    DOUBLE_REDEMPTION = "DOUBLE_REDEMPTION"
    ROLE_KEY_INVALID = "ROLE_KEY_INVALID"
    POLICY_MISMATCH = "POLICY_MISMATCH"
    CUSTODY_CONFLICT = "CUSTODY_CONFLICT"
    REPUTATION_DIVERGENCE = "REPUTATION_DIVERGENCE"


class ConflictDecision(StrEnum):
    ACCEPT_REMOTE = "ACCEPT_REMOTE"
    KEEP_LOCAL = "KEEP_LOCAL"
    COMPENSATE = "COMPENSATE"
    REJECT_PACKAGE = "REJECT_PACKAGE"


@dataclass(frozen=True, slots=True)
class ExposurePreview:
    current: Decimal
    reserved: Decimal
    delta: Decimal
    after: Decimal
    limit: Decimal


def federation_error(code: str, status_code: int = 409) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.federation.{code.lower()}",
        status_code=status_code,
    )


def normalize_code(value: str, maximum: int = 64) -> str:
    result = value.strip().upper()
    if (
        not result
        or len(result) > maximum
        or not result.isascii()
        or not all(character.isalnum() or character in {"_", "-", "."} for character in result)
    ):
        raise federation_error("CODE_INVALID", 422)
    return result


def bounded_amount(value: Decimal | str, *, allow_zero: bool = True) -> Decimal:
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise federation_error("AMOUNT_INVALID", 422) from exc
    significant_amount = amount.normalize() if amount != 0 else Decimal(0)
    exponent = significant_amount.as_tuple().exponent
    if (
        not amount.is_finite()
        or amount < 0
        or (not allow_zero and amount == 0)
        or not isinstance(exponent, int)
        or exponent < -12
        or (amount != 0 and amount.adjusted() > 25)
    ):
        raise federation_error("AMOUNT_INVALID", 422)
    return amount


def preview_exposure(
    *,
    current: Decimal,
    reserved: Decimal,
    delta: Decimal,
    limit: Decimal,
) -> ExposurePreview:
    current_value = bounded_amount(current)
    reserved_value = bounded_amount(reserved)
    delta_value = bounded_amount(delta, allow_zero=False)
    limit_value = bounded_amount(limit, allow_zero=False)
    after = current_value + reserved_value + delta_value
    if after > limit_value:
        raise federation_error("NODE_EXPOSURE_LIMIT_EXCEEDED")
    return ExposurePreview(current_value, reserved_value, delta_value, after, limit_value)


def ensure_compatible(
    *,
    protocol_version: str,
    supported_protocols: list[str],
    required_policies: dict[str, int],
    supported_policies: dict[str, int],
) -> None:
    if protocol_version not in supported_protocols:
        raise federation_error("PROTOCOL_VERSION_UNSUPPORTED", 422)
    if any(supported_policies.get(key) != version for key, version in required_policies.items()):
        raise federation_error("POLICY_VERSION_MISMATCH", 422)
