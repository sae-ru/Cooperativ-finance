from dataclasses import replace
from decimal import Decimal
from itertools import permutations
from random import Random
from typing import Any

import pytest

from cooperative_clearing.modules.clearing.domain.engine import (
    ClearingInput,
    ClearingInputEntry,
    ClearingPolicyParameters,
    RoundingMode,
    calculate_clearing,
    clearing_input_payload,
    policy_parameters_payload,
)
from cooperative_clearing.modules.clearing.domain.verifier import verify_proof_payload
from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.shared.domain.errors import DomainError


def policy() -> ClearingPolicyParameters:
    return ClearingPolicyParameters(
        policy_version=1,
        algorithm_id="LOCAL_NETTING",
        algorithm_version="1.0.0",
        decimal_scale=2,
        rounding_mode=RoundingMode.DOWN,
        minimum_operation=Decimal("0.01"),
        max_iterations=100,
        max_cycle_length=6,
    )


def entry(
    obligation_id: str,
    debtor: str,
    creditor: str,
    amount: str,
    **changes: Any,
) -> ClearingInputEntry:
    value = ClearingInputEntry(
        obligation_id=obligation_id,
        debtor_member_id=debtor,
        creditor_member_id=creditor,
        unit_id="unit-1",
        amount=Decimal(amount),
        obligation_version=1,
        liquidity_class="B",
    )
    return replace(value, **changes)


def test_bilateral_golden_vector_and_bounds() -> None:
    result = calculate_clearing(
        ClearingInput(
            "cycle-1",
            (
                entry("o-1", "anna", "boris", "70"),
                entry("o-2", "boris", "anna", "50"),
            ),
        ),
        policy(),
    )

    assert [item.cleared_amount for item in result.entries] == [Decimal("50"), Decimal("50")]
    assert [item.amount_after for item in result.entries] == [Decimal("20"), Decimal("0")]
    assert result.total_before == Decimal("120")
    assert result.total_cleared == Decimal("100")
    assert result.total_after == Decimal("20")
    assert result.allocations[0].path_kind == "BILATERAL"
    assert result.result_hash.startswith("sha256:")


def test_three_party_cycle_clears_only_common_amount() -> None:
    result = calculate_clearing(
        ClearingInput(
            "cycle-cycle",
            (
                entry("a-b", "a", "b", "12"),
                entry("b-c", "b", "c", "8"),
                entry("c-a", "c", "a", "10"),
            ),
        ),
        policy(),
    )

    assert {item.obligation_id: item.cleared_amount for item in result.entries} == {
        "a-b": Decimal("8"),
        "b-c": Decimal("8"),
        "c-a": Decimal("8"),
    }
    assert result.allocations[0].path_kind == "CYCLE"
    assert result.allocations[0].member_path == ("a", "b", "c", "a")


def test_exclusions_risk_limit_rounding_and_self_loop() -> None:
    result = calculate_clearing(
        ClearingInput(
            "cycle-exclusions",
            (
                entry("limited", "a", "b", "9.999", risk_limit=Decimal("4.567")),
                entry("reverse", "b", "a", "8"),
                entry("disputed", "c", "d", "3", disputed=True),
                entry("self", "e", "e", "2"),
            ),
        ),
        policy(),
    )

    by_id = {item.obligation_id: item for item in result.entries}
    assert by_id["limited"].cleared_amount == Decimal("4.56")
    assert by_id["reverse"].cleared_amount == Decimal("4.56")
    assert by_id["disputed"].exclusion_reason == "DISPUTED"
    assert by_id["self"].exclusion_reason == "SELF_OBLIGATION"
    assert result.warnings == ("RISK_LIMIT_APPLIED",)


def test_result_is_permutation_invariant() -> None:
    source = (
        entry("a-b", "a", "b", "7"),
        entry("b-a", "b", "a", "2"),
        entry("b-c", "b", "c", "5"),
        entry("c-a", "c", "a", "5"),
    )
    hashes = {
        calculate_clearing(ClearingInput("cycle-order", tuple(items)), policy()).result_hash
        for items in permutations(source)
    }
    assert len(hashes) == 1


def test_conservation_for_each_member_and_unit() -> None:
    source = (
        entry("a-b", "a", "b", "20"),
        entry("b-a", "b", "a", "5"),
        entry("b-c", "b", "c", "9"),
        entry("c-a", "c", "a", "7"),
    )
    result = calculate_clearing(ClearingInput("cycle-conservation", source), policy())
    changes: dict[str, Decimal] = {}
    for item in result.entries:
        changes[item.debtor_member_id] = (
            changes.get(item.debtor_member_id, Decimal(0)) - item.cleared_amount
        )
        changes[item.creditor_member_id] = (
            changes.get(item.creditor_member_id, Decimal(0)) + item.cleared_amount
        )
    assert sum(changes.values(), Decimal(0)) == 0
    assert all(Decimal(0) <= item.cleared_amount <= item.amount_before for item in result.entries)


def test_proof_verifier_recalculates_and_rejects_tampering() -> None:
    source = (
        entry("o-1", "a", "b", "11"),
        entry("o-2", "b", "a", "6"),
    )
    clearing_input = ClearingInput("cycle-proof", source)
    result = calculate_clearing(clearing_input, policy())
    proof: dict[str, object] = {
        "cycle_id": "cycle-proof",
        "input_hash": result.input_hash,
        "parameters_hash": result.parameters_hash,
        "result_hash": result.result_hash,
        "input": clearing_input_payload(
            "cycle-proof",
            tuple(
                sorted(
                    source,
                    key=lambda item: (
                        item.unit_id,
                        item.liquidity_class,
                        item.debtor_member_id,
                        item.creditor_member_id,
                        item.obligation_id,
                    ),
                )
            ),
        ),
        "parameters": policy_parameters_payload(policy()),
        "result": result.payload(),
    }
    proof["proof_hash"] = payload_hash(proof)
    assert verify_proof_payload(proof)["valid"] is True

    tampered = {**proof, "result_hash": "sha256:" + "0" * 64}
    with pytest.raises(DomainError) as raised:
        verify_proof_payload(tampered)
    assert raised.value.code == "PROOF_HASH_MISMATCH"


def test_duplicate_obligation_and_unsupported_algorithm_are_rejected() -> None:
    duplicate = entry("same", "a", "b", "1")
    with pytest.raises(DomainError, match="DUPLICATE_OBLIGATION"):
        calculate_clearing(ClearingInput("cycle", (duplicate, duplicate)), policy())
    with pytest.raises(DomainError, match="ALGORITHM_VERSION_UNSUPPORTED"):
        calculate_clearing(
            ClearingInput("cycle", (duplicate,)),
            replace(policy(), algorithm_version="2.0.0"),
        )


def test_seeded_property_matrix_is_bounded_conservative_and_stable() -> None:
    for seed in range(200):
        random = Random(seed)
        members = tuple(f"member-{index}" for index in range(random.randint(2, 8)))
        source = tuple(
            entry(
                f"seed-{seed}-obligation-{index}",
                random.choice(members),
                random.choice(members),
                str(Decimal(random.randint(1, 100_000)) / Decimal(100)),
                risk_limit=(
                    Decimal(random.randint(0, 100_000)) / Decimal(100)
                    if random.randrange(4) == 0
                    else None
                ),
                disputed=random.randrange(17) == 0,
                eligible=random.randrange(19) != 0,
                liquidity_class=random.choice(("A", "B", "C", "D")),
            )
            for index in range(random.randint(1, 24))
        )
        shuffled = list(source)
        random.shuffle(shuffled)

        first = calculate_clearing(
            ClearingInput(f"property-{seed}", source),
            replace(policy(), max_iterations=10_000),
        )
        reverse = calculate_clearing(
            ClearingInput(f"property-{seed}", tuple(reversed(source))),
            replace(policy(), max_iterations=10_000),
        )
        randomized = calculate_clearing(
            ClearingInput(f"property-{seed}", tuple(shuffled)),
            replace(policy(), max_iterations=10_000),
        )

        assert first.result_hash == reverse.result_hash == randomized.result_hash
        assert first.total_before == sum((item.amount_before for item in first.entries), Decimal(0))
        assert first.total_cleared == sum(
            (item.cleared_amount for item in first.entries), Decimal(0)
        )
        assert first.total_after == sum((item.amount_after for item in first.entries), Decimal(0))
        assert all(
            item.amount_before == item.cleared_amount + item.amount_after
            and Decimal(0) <= item.cleared_amount <= item.amount_before
            for item in first.entries
        )

        member_changes: dict[str, Decimal] = {}
        for item in first.entries:
            member_changes[item.debtor_member_id] = (
                member_changes.get(item.debtor_member_id, Decimal(0)) - item.cleared_amount
            )
            member_changes[item.creditor_member_id] = (
                member_changes.get(item.creditor_member_id, Decimal(0)) + item.cleared_amount
            )
        assert sum(member_changes.values(), Decimal(0)) == 0
