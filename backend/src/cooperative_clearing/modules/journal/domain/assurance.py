"""Typed assurance contract for economically critical signed events."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class ExposureCategory(StrEnum):
    COMMODITY = "COMMODITY"
    OBLIGATION = "OBLIGATION"
    SHARE = "SHARE"
    SOLIDARITY = "SOLIDARITY"
    NODE = "NODE"


class ExposureEffect(StrEnum):
    CREATE = "CREATE"
    RESERVE = "RESERVE"
    TRANSFER = "TRANSFER"
    REDUCE = "REDUCE"
    RELEASE = "RELEASE"
    EXECUTE = "EXECUTE"
    FINALIZE = "FINALIZE"


@dataclass(frozen=True, slots=True)
class ExposureClaim:
    category: ExposureCategory
    effect: ExposureEffect
    subject_type: str
    subject_id: UUID
    amount: Decimal | None = None
    unit: str | None = None
    maximum_loss: Decimal | None = None
    basis_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandAssurance:
    exposure: ExposureClaim
    evidence_refs: tuple[object, ...]


CRITICAL_EVENT_TYPES = frozenset(
    {
        "inventory.quantity_reserved",
        "rights.commodity_right_issued",
        "rights.commodity_right_transferred",
        "rights.commodity_right_redeemed",
        "obligations.fulfillment_recorded",
        "obligations.fulfillment_accepted",
        "obligations.obligation_cleared",
        "shares.contribution_recorded",
        "shares.exposure_reserved",
        "shares.exposure_cancelled",
        "shares.exposure_released",
        "liability.compensation_authorized",
        "liability.compensation_settled",
        "liability.compensation_voided",
        "solidarity.allocation_approved",
        "solidarity.aid_delivered",
        "federation.node_exposure_reserved",
        "federation.clearing_node_prepared",
        "federation.clearing_commit_certified",
        "federation.clearing_certificate_applied",
        "federation.clearing_reconciled",
    }
)