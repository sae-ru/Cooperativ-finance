from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from cooperative_clearing.modules.federation.domain.discovery import (
    CostStatus,
    FreshnessStatus,
    bounded_reservation_expiry,
    calculate_landed_cost,
    ensure_reservable,
    freshness_status,
    ranking_key,
)
from cooperative_clearing.shared.domain.errors import DomainError


def test_landed_cost_keeps_confirmed_components_reproducible() -> None:
    cost = calculate_landed_cost(
        quantity=Decimal("10"),
        unit_price=Decimal("3.50"),
        mandatory_fee_per_unit=Decimal("0.10"),
        quote_components={
            "transport": Decimal("8"),
            "handling": Decimal("2"),
            "transfer": Decimal("1"),
            "storage": Decimal("0"),
            "taxes": Decimal("4"),
            "insurance": Decimal("0.50"),
        },
        quote_status=CostStatus.CONFIRMED,
    )

    assert cost.goods_cost == Decimal("35.00")
    assert cost.logistics_cost == Decimal("11")
    assert cost.mandatory_cost == Decimal("5.50")
    assert cost.landed_cost == Decimal("51.50")
    assert cost.status is CostStatus.CONFIRMED


def test_stale_or_untrusted_offer_cannot_be_reserved() -> None:
    now = datetime(2026, 7, 21, 12, tzinfo=UTC)
    assert (
        freshness_status(
            now=now,
            valid_until=now + timedelta(hours=2),
            signed_at=now - timedelta(hours=3),
            maximum_age_seconds=3600,
            trusted=True,
            revoked=False,
            live_verified_at=None,
        )
        is FreshnessStatus.STALE
    )

    with pytest.raises(DomainError) as error:
        ensure_reservable(FreshnessStatus.STALE, signature_verified=True)
    assert error.value.code == "OFFER_NOT_RESERVABLE"


def test_ranking_is_confirmed_then_cost_delivery_freshness_and_id() -> None:
    now = datetime(2026, 7, 21, 12, tzinfo=UTC)
    confirmed = ranking_key(
        cost_status=CostStatus.CONFIRMED,
        landed_cost=Decimal("20"),
        delivery_at=now + timedelta(days=2),
        signed_at=now,
        offer_id=UUID(int=2),
    )
    estimated = ranking_key(
        cost_status=CostStatus.ESTIMATED,
        landed_cost=Decimal("10"),
        delivery_at=now + timedelta(days=1),
        signed_at=now,
        offer_id=UUID(int=1),
    )
    assert confirmed < estimated


def test_reservation_expiry_uses_the_earliest_signed_bound() -> None:
    now = datetime(2026, 7, 21, 12, tzinfo=UTC)
    expiry = bounded_reservation_expiry(
        now=now,
        requested=now + timedelta(hours=4),
        bounds=(now + timedelta(hours=2), now + timedelta(hours=3)),
    )
    assert expiry == now + timedelta(hours=2)


def test_live_verification_supersedes_signed_snapshot_age() -> None:
    now = datetime(2026, 7, 21, 12, tzinfo=UTC)

    result = freshness_status(
        now=now,
        valid_until=now + timedelta(hours=2),
        signed_at=now - timedelta(hours=3),
        maximum_age_seconds=1,
        trusted=True,
        revoked=False,
        live_verified_at=now,
    )

    assert result is FreshnessStatus.LIVE_VERIFIED
