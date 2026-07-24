"""Focused invariants for the marketplace-to-deal bridge."""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from cooperative_clearing.api.participant import _decimal, _require_member
from cooperative_clearing.modules.exchange.application.federated_bridge import (
    FederatedPurchaseBridge,
    _Party,
)
from cooperative_clearing.modules.identity.domain.types import Principal
from cooperative_clearing.shared.domain.errors import DomainError


def test_unique_purchase_parties_preserve_one_named_actor_per_member() -> None:
    member = uuid4()
    first = _Party(member, uuid4(), uuid4())
    duplicate = _Party(member, uuid4(), uuid4())
    other = _Party(uuid4(), uuid4(), uuid4())

    assert FederatedPurchaseBridge._unique_parties((first, duplicate, other)) == (
        first,
        other,
    )


def test_purchase_valuation_components_are_exact_and_nonnegative() -> None:
    breakdown: dict[str, object] = {
        "goods_cost": "10.000000000000",
        "mandatory_cost": "6.000000000000",
        "logistics_cost": "15.00",
        "landed_cost": "31.000000000000",
    }

    assert FederatedPurchaseBridge._amount(breakdown, "goods_cost") == Decimal("10")
    assert FederatedPurchaseBridge._amount(breakdown, "logistics_cost") == Decimal("15")
    assert FederatedPurchaseBridge._amount({}, "logistics_cost") == Decimal("0")

    with pytest.raises(DomainError):
        FederatedPurchaseBridge._amount({"goods_cost": "-1"}, "goods_cost")


def test_purchase_helpers_reject_unknown_values_and_preserve_actor_identity() -> None:
    with pytest.raises(DomainError):
        FederatedPurchaseBridge._amount({"goods_cost": "not-a-number"}, "goods_cost")
    with pytest.raises(DomainError):
        FederatedPurchaseBridge._amount({}, "goods_cost")

    party = _Party(uuid4(), uuid4(), uuid4())
    cooperative_id = uuid4()
    actor = FederatedPurchaseBridge._actor(party, cooperative_id)
    assert actor.person_id == party.member_id
    assert actor.organization_id == cooperative_id
    assert actor.role_assignment_id == party.role_assignment_id


def test_participant_guard_requires_a_ready_personal_profile() -> None:
    member_id = uuid4()
    principal = Principal(
        user_id=uuid4(),
        session_id=uuid4(),
        login="member",
        member_id=member_id,
        must_change_password=False,
        roles=(),
    )
    assert _require_member(principal) == member_id
    assert _decimal(Decimal("10.500")) == "10.500"

    with pytest.raises(DomainError) as password_error:
        _require_member(replace(principal, must_change_password=True))
    assert password_error.value.code == "PASSWORD_CHANGE_REQUIRED"

    with pytest.raises(DomainError) as actor_error:
        _require_member(replace(principal, member_id=None))
    assert actor_error.value.code == "PERSONAL_ACTOR_REQUIRED"
