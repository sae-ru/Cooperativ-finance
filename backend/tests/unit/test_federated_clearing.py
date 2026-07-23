from datetime import UTC, datetime, timedelta
from decimal import Decimal
from random import Random

import pytest

from cooperative_clearing.modules.clearing.domain.engine import RoundingMode
from cooperative_clearing.modules.federation.domain.federated_clearing import (
    FederatedClearingPolicy,
    FederatedObligationInput,
    apply_receipt_payload,
    calculate_federated_clearing,
    commit_certificate_payload,
    reconciliation_proof_payload,
)
from cooperative_clearing.shared.domain.errors import DomainError


def _policy() -> FederatedClearingPolicy:
    return FederatedClearingPolicy(
        policy_version=1,
        decimal_scale=2,
        rounding_mode=RoundingMode.DOWN,
        minimum_operation=Decimal("0.01"),
        max_iterations=100,
        max_cycle_length=6,
        prepare_ttl_seconds=900,
    )


def _obligations() -> tuple[FederatedObligationInput, ...]:
    return (
        FederatedObligationInput(
            obligation_id="obl-a-b",
            home_node_code="node-a",
            debtor_node_code="node-a",
            creditor_node_code="node-b",
            unit_code="COOP",
            amount=Decimal("10.00"),
            version=1,
        ),
        FederatedObligationInput(
            obligation_id="obl-b-c",
            home_node_code="node-b",
            debtor_node_code="node-b",
            creditor_node_code="node-c",
            unit_code="COOP",
            amount=Decimal("7.00"),
            version=2,
        ),
        FederatedObligationInput(
            obligation_id="obl-c-a",
            home_node_code="node-c",
            debtor_node_code="node-c",
            creditor_node_code="node-a",
            unit_code="COOP",
            amount=Decimal("5.00"),
            version=3,
        ),
    )


def test_federated_result_is_permutation_stable_and_bounds_every_obligation() -> None:
    first = calculate_federated_clearing(
        cycle_id="cycle-1", obligations=_obligations(), policy=_policy()
    )
    second = calculate_federated_clearing(
        cycle_id="cycle-1", obligations=tuple(reversed(_obligations())), policy=_policy()
    )

    assert first.result_hash == second.result_hash
    assert first.affected_node_codes == ("node-a", "node-b", "node-c")
    assert [item.cleared_amount for item in first.clearing.entries] == [
        Decimal("5.00"),
        Decimal("5.00"),
        Decimal("5.00"),
    ]
    assert all(item.amount_after >= 0 for item in first.clearing.entries)
    assert all(
        position.receivable_before - position.payable_before
        == position.receivable_after - position.payable_after
        for position in first.positions
    )


def test_commit_certificate_requires_every_affected_node_exactly_once() -> None:
    now = datetime.now(UTC)
    artifact_hash = "sha256:" + "1" * 64
    approvals = {code: artifact_hash for code in ("node-a", "node-b", "node-c")}
    certificate = commit_certificate_payload(
        cycle_id="cycle-1",
        coordinator_node_code="node-a",
        input_hash=artifact_hash,
        result_hash="sha256:" + "2" * 64,
        required_node_codes=("node-c", "node-a", "node-b"),
        prepare_receipt_hashes=approvals,
        approval_hashes=approvals,
        policy_hash="sha256:" + "3" * 64,
        certified_at=now,
    )

    assert certificate["required_node_codes"] == ["node-a", "node-b", "node-c"]
    assert str(certificate["certificate_hash"]).startswith("sha256:")

    with pytest.raises(DomainError) as missing:
        commit_certificate_payload(
            cycle_id="cycle-1",
            coordinator_node_code="node-a",
            input_hash=artifact_hash,
            result_hash="sha256:" + "2" * 64,
            required_node_codes=("node-a", "node-b", "node-c"),
            prepare_receipt_hashes=approvals,
            approval_hashes={"node-a": artifact_hash, "node-b": artifact_hash},
            policy_hash="sha256:" + "3" * 64,
            certified_at=now + timedelta(seconds=1),
        )
    assert missing.value.code == "FEDERATED_CERTIFICATE_APPROVALS_INCOMPLETE"


def test_obligation_home_node_must_be_an_actual_party() -> None:
    with pytest.raises(DomainError) as invalid:
        FederatedObligationInput(
            obligation_id="obl-1",
            home_node_code="node-c",
            debtor_node_code="node-a",
            creditor_node_code="node-b",
            unit_code="COOP",
            amount=Decimal("1"),
            version=1,
        ).validate()
    assert invalid.value.code == "FEDERATED_HOME_NODE_INVALID"


def test_apply_receipt_is_stable_and_sums_only_positive_applications() -> None:
    now = datetime.now(UTC)
    certificate_hash = "sha256:" + "4" * 64
    applications: tuple[dict[str, object], ...] = (
        {
            "obligation_id": "obl-b",
            "amount_before": "7.00",
            "cleared_amount": "2.00",
            "amount_after": "5.00",
        },
        {
            "obligation_id": "obl-a",
            "amount_before": "3.00",
            "cleared_amount": "3.00",
            "amount_after": "0.00",
        },
    )

    first = apply_receipt_payload(
        cycle_id="cycle-1",
        node_code="node-a",
        certificate_hash=certificate_hash,
        applications=applications,
        applied_at=now,
    )
    second = apply_receipt_payload(
        cycle_id="cycle-1",
        node_code="node-a",
        certificate_hash=certificate_hash,
        applications=tuple(reversed(applications)),
        applied_at=now,
    )

    assert first == second
    assert first["applied_count"] == 2
    assert first["applied_amount"] == "5"


def test_reconciliation_requires_apply_receipt_from_every_affected_node() -> None:
    hashes = {code: "sha256:" + char * 64 for code, char in (("node-a", "a"), ("node-b", "b"))}
    proof = reconciliation_proof_payload(
        cycle_id="cycle-1",
        input_hash="sha256:" + "1" * 64,
        result_hash="sha256:" + "2" * 64,
        certificate_hash="sha256:" + "3" * 64,
        required_node_codes=("node-b", "node-a"),
        snapshot_hashes=hashes,
        prepare_receipt_hashes=hashes,
        approval_hashes=hashes,
        apply_receipt_hashes=hashes,
        reconciled_at=datetime.now(UTC),
    )
    assert proof["required_node_codes"] == ["node-a", "node-b"]

    with pytest.raises(DomainError) as missing:
        reconciliation_proof_payload(
            cycle_id="cycle-1",
            input_hash="sha256:" + "1" * 64,
            result_hash="sha256:" + "2" * 64,
            certificate_hash="sha256:" + "3" * 64,
            required_node_codes=("node-a", "node-b"),
            snapshot_hashes=hashes,
            prepare_receipt_hashes=hashes,
            approval_hashes=hashes,
            apply_receipt_hashes={"node-a": hashes["node-a"]},
            reconciled_at=datetime.now(UTC),
        )
    assert missing.value.code == "FEDERATED_RECONCILIATION_INCOMPLETE"


def test_seeded_federated_property_matrix_is_stable_and_conservative() -> None:
    for seed in range(100):
        random = Random(seed)
        nodes = tuple(f"node-{index}" for index in range(random.randint(2, 7)))
        obligations = []
        for index in range(random.randint(1, 24)):
            debtor = random.choice(nodes)
            creditor = random.choice(tuple(node for node in nodes if node != debtor))
            obligations.append(
                FederatedObligationInput(
                    obligation_id=f"seed-{seed}-obligation-{index}",
                    home_node_code=random.choice((debtor, creditor)),
                    debtor_node_code=debtor,
                    creditor_node_code=creditor,
                    unit_code="COOP",
                    amount=Decimal(random.randint(1, 100_000)) / Decimal(100),
                    version=random.randint(1, 20),
                )
            )
        shuffled = list(obligations)
        random.shuffle(shuffled)

        first = calculate_federated_clearing(
            cycle_id=f"property-{seed}",
            obligations=tuple(obligations),
            policy=_policy(),
        )
        reverse = calculate_federated_clearing(
            cycle_id=f"property-{seed}",
            obligations=tuple(reversed(obligations)),
            policy=_policy(),
        )
        randomized = calculate_federated_clearing(
            cycle_id=f"property-{seed}",
            obligations=tuple(shuffled),
            policy=_policy(),
        )

        assert first.result_hash == reverse.result_hash == randomized.result_hash
        assert all(
            item.amount_before == item.cleared_amount + item.amount_after
            and Decimal(0) <= item.cleared_amount <= item.amount_before
            for item in first.clearing.entries
        )
        assert all(
            position.receivable_before - position.payable_before
            == position.receivable_after - position.payable_after
            for position in first.positions
        )
