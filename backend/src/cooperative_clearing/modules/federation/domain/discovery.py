"""Pure freshness, landed-cost, ranking, and saga rules for federated discovery."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from cooperative_clearing.modules.federation.domain.types import bounded_amount, federation_error


class SearchMode(StrEnum):
    DIRECT = "DIRECT"
    INDEXED = "INDEXED"
    CACHED_OFFLINE = "CACHED_OFFLINE"


class FreshnessStatus(StrEnum):
    LIVE_VERIFIED = "LIVE_VERIFIED"
    SIGNED_CACHED = "SIGNED_CACHED"
    STALE = "STALE"
    REVOKED_OR_UNTRUSTED = "REVOKED_OR_UNTRUSTED"


class CostStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    ESTIMATED = "ESTIMATED"


class PurchaseIntentStatus(StrEnum):
    PREPARING = "PREPARING"
    GOODS_RESERVED = "GOODS_RESERVED"
    PREPARED = "PREPARED"
    COMMITTING = "COMMITTING"
    CANCELLING = "CANCELLING"
    COMMITTED = "COMMITTED"
    COMPENSATED = "COMPENSATED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


ALLOWED_COST_COMPONENTS = frozenset(
    {"transport", "handling", "transfer", "storage", "taxes", "insurance", "risk_reserve"}
)


@dataclass(frozen=True, slots=True)
class LandedCost:
    goods_cost: Decimal
    logistics_cost: Decimal
    mandatory_cost: Decimal
    landed_cost: Decimal
    status: CostStatus
    components: dict[str, Decimal]


def exact_discovery_amount(value: Decimal | str, *, allow_zero: bool = False) -> Decimal:
    return bounded_amount(value, allow_zero=allow_zero)


def calculate_landed_cost(
    *,
    quantity: Decimal,
    unit_price: Decimal,
    mandatory_fee_per_unit: Decimal,
    quote_components: dict[str, Decimal],
    quote_status: CostStatus,
) -> LandedCost:
    requested = exact_discovery_amount(quantity)
    price = exact_discovery_amount(unit_price, allow_zero=True)
    fee = exact_discovery_amount(mandatory_fee_per_unit, allow_zero=True)
    if set(quote_components) - ALLOWED_COST_COMPONENTS:
        raise federation_error("COST_COMPONENT_INVALID", 422)
    components = {
        name: exact_discovery_amount(value, allow_zero=True)
        for name, value in quote_components.items()
    }
    goods_cost = requested * price
    logistics_cost = sum(
        (
            components.get(name, Decimal(0))
            for name in ("transport", "handling", "transfer", "storage")
        ),
        Decimal(0),
    )
    mandatory_cost = requested * fee + sum(
        (components.get(name, Decimal(0)) for name in ("taxes", "insurance", "risk_reserve")),
        Decimal(0),
    )
    return LandedCost(
        goods_cost=goods_cost,
        logistics_cost=logistics_cost,
        mandatory_cost=mandatory_cost,
        landed_cost=goods_cost + logistics_cost + mandatory_cost,
        status=quote_status,
        components=components,
    )


def freshness_status(
    *,
    now: datetime,
    valid_until: datetime,
    signed_at: datetime,
    maximum_age_seconds: int,
    trusted: bool,
    revoked: bool,
    live_verified_at: datetime | None,
) -> FreshnessStatus:
    current = now.astimezone(UTC)
    if not trusted or revoked:
        return FreshnessStatus.REVOKED_OR_UNTRUSTED
    if valid_until.astimezone(UTC) <= current:
        return FreshnessStatus.STALE
    if live_verified_at is not None and current - timedelta(
        seconds=30
    ) <= live_verified_at.astimezone(UTC) <= current + timedelta(seconds=1):
        return FreshnessStatus.LIVE_VERIFIED
    age = (current - signed_at.astimezone(UTC)).total_seconds()
    if age > maximum_age_seconds:
        return FreshnessStatus.STALE
    return FreshnessStatus.SIGNED_CACHED


def ensure_reservable(status: FreshnessStatus, *, signature_verified: bool) -> None:
    if not signature_verified:
        raise federation_error("OFFER_SIGNATURE_INVALID", 422)
    if status not in {FreshnessStatus.LIVE_VERIFIED, FreshnessStatus.SIGNED_CACHED}:
        raise federation_error("OFFER_NOT_RESERVABLE")


def ranking_key(
    *,
    cost_status: CostStatus,
    landed_cost: Decimal,
    delivery_at: datetime,
    signed_at: datetime,
    offer_id: UUID,
) -> tuple[int, Decimal, datetime, float, str]:
    return (
        0 if cost_status is CostStatus.CONFIRMED else 1,
        landed_cost,
        delivery_at.astimezone(UTC),
        -signed_at.astimezone(UTC).timestamp(),
        str(offer_id),
    )


def bounded_reservation_expiry(
    *, now: datetime, requested: datetime, bounds: tuple[datetime, ...]
) -> datetime:
    expiry = min((requested, *bounds)).astimezone(UTC)
    if expiry <= now.astimezone(UTC):
        raise federation_error("RESERVATION_EXPIRY_INVALID", 422)
    return expiry
