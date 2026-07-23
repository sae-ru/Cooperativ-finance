"""Pure inter-node clearing calculation and commit-certificate rules."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from cooperative_clearing.modules.clearing.domain.engine import (
    ClearingInput,
    ClearingInputEntry,
    ClearingPolicyParameters,
    ClearingResult,
    RoundingMode,
    calculate_clearing,
    decimal_string,
)
from cooperative_clearing.modules.federation.domain.types import federation_error
from cooperative_clearing.modules.journal.domain.crypto import payload_hash, utc_timestamp
from cooperative_clearing.modules.node.domain.node_code import NodeCode

FEDERATED_ALGORITHM_ID = "FEDERATED_NETTING"
FEDERATED_ALGORITHM_VERSION = "1.0.0"


class FederatedClearingCycleStatus(StrEnum):
    DRAFT = "DRAFT"
    COLLECTING_SNAPSHOTS = "COLLECTING_SNAPSHOTS"
    PREPARING_NODES = "PREPARING_NODES"
    PREPARED = "PREPARED"
    PROPOSED = "PROPOSED"
    VERIFYING = "VERIFYING"
    COMMIT_CERTIFIED = "COMMIT_CERTIFIED"
    APPLYING = "APPLYING"
    COMMITTED_PENDING_APPLY = "COMMITTED_PENDING_APPLY"
    RECONCILED = "RECONCILED"
    PREPARE_EXPIRED = "PREPARE_EXPIRED"
    REJECTED = "REJECTED"
    CONFLICT = "CONFLICT"
    CANCELLED = "CANCELLED"


class FederatedObligationStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    PREPARED = "PREPARED"
    PARTIALLY_CLEARED = "PARTIALLY_CLEARED"
    CLEARED = "CLEARED"
    DISPUTED = "DISPUTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class FederatedClearingPolicy:
    policy_version: int
    decimal_scale: int
    rounding_mode: RoundingMode
    minimum_operation: Decimal
    max_iterations: int
    max_cycle_length: int
    prepare_ttl_seconds: int

    def engine_policy(self) -> ClearingPolicyParameters:
        if not 30 <= self.prepare_ttl_seconds <= 86_400:
            raise federation_error("FEDERATED_PREPARE_TTL_INVALID", 422)
        return ClearingPolicyParameters(
            policy_version=self.policy_version,
            algorithm_id=FEDERATED_ALGORITHM_ID,
            algorithm_version=FEDERATED_ALGORITHM_VERSION,
            decimal_scale=self.decimal_scale,
            rounding_mode=self.rounding_mode,
            minimum_operation=self.minimum_operation,
            max_iterations=self.max_iterations,
            max_cycle_length=self.max_cycle_length,
        ).validate()

    def payload(self) -> dict[str, object]:
        engine = self.engine_policy()
        return {
            "policy_version": self.policy_version,
            "algorithm_id": engine.algorithm_id,
            "algorithm_version": engine.algorithm_version,
            "decimal_scale": self.decimal_scale,
            "rounding_mode": self.rounding_mode.value,
            "minimum_operation": decimal_string(self.minimum_operation),
            "max_iterations": self.max_iterations,
            "max_cycle_length": self.max_cycle_length,
            "prepare_ttl_seconds": self.prepare_ttl_seconds,
        }


@dataclass(frozen=True, slots=True)
class FederatedObligationInput:
    obligation_id: str
    home_node_code: str
    debtor_node_code: str
    creditor_node_code: str
    unit_code: str
    amount: Decimal
    version: int
    liquidity_class: str = "UNASSESSED"
    eligible: bool = True
    exclusion_reason: str | None = None
    disputed: bool = False
    frozen: bool = False
    risk_limit: Decimal | None = None
    source_event_hash: str | None = None

    def validate(self) -> "FederatedObligationInput":
        home = str(NodeCode(self.home_node_code))
        debtor = str(NodeCode(self.debtor_node_code))
        creditor = str(NodeCode(self.creditor_node_code))
        if debtor == creditor:
            raise federation_error("FEDERATED_SELF_OBLIGATION", 422)
        if home not in {debtor, creditor}:
            raise federation_error("FEDERATED_HOME_NODE_INVALID", 422)
        if not self.obligation_id.strip() or self.amount <= 0 or self.version < 1:
            raise federation_error("FEDERATED_OBLIGATION_INVALID", 422)
        if self.risk_limit is not None and self.risk_limit < 0:
            raise federation_error("FEDERATED_RISK_LIMIT_INVALID", 422)
        if self.source_event_hash is not None and not _is_sha256(self.source_event_hash):
            raise federation_error("FEDERATED_SOURCE_HASH_INVALID", 422)
        return self

    def payload(self) -> dict[str, object]:
        self.validate()
        return {
            "obligation_id": self.obligation_id,
            "home_node_code": str(NodeCode(self.home_node_code)),
            "debtor_node_code": str(NodeCode(self.debtor_node_code)),
            "creditor_node_code": str(NodeCode(self.creditor_node_code)),
            "unit_code": self.unit_code,
            "amount": decimal_string(self.amount),
            "version": self.version,
            "liquidity_class": self.liquidity_class,
            "eligible": self.eligible,
            "exclusion_reason": self.exclusion_reason,
            "disputed": self.disputed,
            "frozen": self.frozen,
            "risk_limit": (
                decimal_string(self.risk_limit) if self.risk_limit is not None else None
            ),
            "source_event_hash": self.source_event_hash,
        }


@dataclass(frozen=True, slots=True)
class NodePosition:
    node_code: str
    unit_code: str
    payable_before: Decimal
    receivable_before: Decimal
    payable_after: Decimal
    receivable_after: Decimal

    def payload(self) -> dict[str, object]:
        return {
            "node_code": self.node_code,
            "unit_code": self.unit_code,
            "payable_before": decimal_string(self.payable_before),
            "receivable_before": decimal_string(self.receivable_before),
            "net_before": decimal_string(self.receivable_before - self.payable_before),
            "payable_after": decimal_string(self.payable_after),
            "receivable_after": decimal_string(self.receivable_after),
            "net_after": decimal_string(self.receivable_after - self.payable_after),
        }


@dataclass(frozen=True, slots=True)
class FederatedClearingResult:
    clearing: ClearingResult
    obligations: tuple[FederatedObligationInput, ...]
    positions: tuple[NodePosition, ...]
    affected_node_codes: tuple[str, ...]
    result_hash: str

    def payload(self) -> dict[str, object]:
        return {
            "algorithm_id": FEDERATED_ALGORITHM_ID,
            "algorithm_version": FEDERATED_ALGORITHM_VERSION,
            "input_hash": self.clearing.input_hash,
            "parameters_hash": self.clearing.parameters_hash,
            "clearing_result_hash": self.clearing.result_hash,
            "entries": self.clearing.payload()["entries"],
            "allocations": self.clearing.payload()["allocations"],
            "totals": self.clearing.payload()["totals"],
            "iteration_count": self.clearing.iteration_count,
            "warnings": list(self.clearing.warnings),
            "positions": [item.payload() for item in self.positions],
            "affected_node_codes": list(self.affected_node_codes),
        }


def calculate_federated_clearing(
    *,
    cycle_id: str,
    obligations: tuple[FederatedObligationInput, ...],
    policy: FederatedClearingPolicy,
) -> FederatedClearingResult:
    if not obligations:
        raise federation_error("FEDERATED_INPUT_EMPTY", 422)
    ordered = tuple(sorted((item.validate() for item in obligations), key=_obligation_key))
    engine_input = ClearingInput(
        cycle_id=cycle_id,
        entries=tuple(
            ClearingInputEntry(
                obligation_id=item.obligation_id,
                debtor_member_id=str(NodeCode(item.debtor_node_code)),
                creditor_member_id=str(NodeCode(item.creditor_node_code)),
                unit_id=item.unit_code,
                amount=item.amount,
                obligation_version=item.version,
                liquidity_class=item.liquidity_class,
                eligible=item.eligible,
                exclusion_reason=item.exclusion_reason,
                disputed=item.disputed,
                frozen=item.frozen,
                risk_limit=item.risk_limit,
            )
            for item in ordered
        ),
    )
    clearing = calculate_clearing(engine_input, policy.engine_policy())
    positions = _positions(ordered, clearing)
    cleared_ids = {
        item.obligation_id for item in clearing.entries if item.cleared_amount > Decimal(0)
    }
    affected = tuple(
        sorted(
            {
                node
                for item in ordered
                if item.obligation_id in cleared_ids
                for node in (item.home_node_code, item.debtor_node_code, item.creditor_node_code)
            }
        )
    )
    provisional = FederatedClearingResult(
        clearing=clearing,
        obligations=ordered,
        positions=positions,
        affected_node_codes=affected,
        result_hash="",
    )
    return FederatedClearingResult(
        clearing=clearing,
        obligations=ordered,
        positions=positions,
        affected_node_codes=affected,
        result_hash=payload_hash(provisional.payload()),
    )


def snapshot_payload(
    *,
    cycle_id: str,
    node_code: str,
    obligations: tuple[FederatedObligationInput, ...],
    checkpoint_hash: str,
    policy_hash: str,
    signed_at: datetime,
    expires_at: datetime,
) -> dict[str, object]:
    if not _is_sha256(checkpoint_hash) or not _is_sha256(policy_hash):
        raise federation_error("FEDERATED_SNAPSHOT_HASH_INVALID", 422)
    ordered = tuple(sorted((item.validate() for item in obligations), key=_obligation_key))
    home = str(NodeCode(node_code))
    if any(str(NodeCode(item.home_node_code)) != home for item in ordered):
        raise federation_error("FEDERATED_SNAPSHOT_HOME_NODE_INVALID", 422)
    body: dict[str, object] = {
        "cycle_id": cycle_id,
        "node_code": home,
        "obligations": [item.payload() for item in ordered],
        "checkpoint_hash": checkpoint_hash,
        "policy_hash": policy_hash,
        "signed_at": utc_timestamp(signed_at),
        "expires_at": utc_timestamp(expires_at),
    }
    return {**body, "snapshot_hash": payload_hash(body)}


def prepare_receipt_payload(
    *,
    cycle_id: str,
    node_code: str,
    input_hash: str,
    snapshot_hash: str,
    obligation_versions: dict[str, int],
    reserved_by_unit: dict[str, Decimal],
    expires_at: datetime,
) -> dict[str, object]:
    _require_hashes(input_hash, snapshot_hash)
    body: dict[str, object] = {
        "cycle_id": cycle_id,
        "node_code": str(NodeCode(node_code)),
        "input_hash": input_hash,
        "snapshot_hash": snapshot_hash,
        "obligation_versions": {
            key: obligation_versions[key] for key in sorted(obligation_versions)
        },
        "reserved_by_unit": {
            key: decimal_string(reserved_by_unit[key]) for key in sorted(reserved_by_unit)
        },
        "expires_at": utc_timestamp(expires_at),
    }
    return {**body, "receipt_hash": payload_hash(body)}


def approval_payload(
    *,
    cycle_id: str,
    node_code: str,
    input_hash: str,
    result_hash: str,
    prepare_receipt_hash: str,
    approved_at: datetime,
) -> dict[str, object]:
    _require_hashes(input_hash, result_hash, prepare_receipt_hash)
    body: dict[str, object] = {
        "cycle_id": cycle_id,
        "node_code": str(NodeCode(node_code)),
        "input_hash": input_hash,
        "result_hash": result_hash,
        "prepare_receipt_hash": prepare_receipt_hash,
        "approved_at": utc_timestamp(approved_at),
    }
    return {**body, "approval_hash": payload_hash(body)}


def commit_certificate_payload(
    *,
    cycle_id: str,
    coordinator_node_code: str,
    input_hash: str,
    result_hash: str,
    required_node_codes: tuple[str, ...],
    prepare_receipt_hashes: dict[str, str],
    approval_hashes: dict[str, str],
    policy_hash: str,
    certified_at: datetime,
) -> dict[str, object]:
    _require_hashes(input_hash, result_hash, policy_hash)
    required = tuple(sorted(str(NodeCode(code)) for code in required_node_codes))
    if not required or len(set(required)) != len(required):
        raise federation_error("FEDERATED_REQUIRED_NODES_INVALID", 422)
    if set(prepare_receipt_hashes) != set(required) or set(approval_hashes) != set(required):
        raise federation_error("FEDERATED_CERTIFICATE_APPROVALS_INCOMPLETE", 409)
    for code in required:
        _require_hashes(prepare_receipt_hashes[code], approval_hashes[code])
    body: dict[str, object] = {
        "cycle_id": cycle_id,
        "coordinator_node_code": str(NodeCode(coordinator_node_code)),
        "algorithm_id": FEDERATED_ALGORITHM_ID,
        "algorithm_version": FEDERATED_ALGORITHM_VERSION,
        "input_hash": input_hash,
        "result_hash": result_hash,
        "required_node_codes": list(required),
        "prepare_receipt_hashes": {code: prepare_receipt_hashes[code] for code in required},
        "approval_hashes": {code: approval_hashes[code] for code in required},
        "policy_hash": policy_hash,
        "certified_at": utc_timestamp(certified_at),
    }
    return {**body, "certificate_hash": payload_hash(body)}


def apply_receipt_payload(
    *,
    cycle_id: str,
    node_code: str,
    certificate_hash: str,
    applications: tuple[dict[str, object], ...],
    applied_at: datetime,
) -> dict[str, object]:
    _require_hashes(certificate_hash)
    ordered = tuple(sorted(applications, key=lambda item: str(item.get("obligation_id", ""))))
    total = Decimal(0)
    for item in ordered:
        amount = item.get("cleared_amount")
        if not isinstance(amount, str):
            raise federation_error("FEDERATED_APPLICATION_INVALID", 422)
        try:
            parsed = Decimal(amount)
        except (InvalidOperation, ValueError) as exc:
            raise federation_error("FEDERATED_APPLICATION_INVALID", 422) from exc
        if parsed <= 0:
            raise federation_error("FEDERATED_APPLICATION_INVALID", 422)
        total += parsed
    body: dict[str, object] = {
        "cycle_id": cycle_id,
        "node_code": str(NodeCode(node_code)),
        "certificate_hash": certificate_hash,
        "applications": list(ordered),
        "applied_count": len(ordered),
        "applied_amount": decimal_string(total),
        "applied_at": utc_timestamp(applied_at),
    }
    return {**body, "receipt_hash": payload_hash(body)}


def reconciliation_proof_payload(
    *,
    cycle_id: str,
    input_hash: str,
    result_hash: str,
    certificate_hash: str,
    required_node_codes: tuple[str, ...],
    snapshot_hashes: dict[str, str],
    prepare_receipt_hashes: dict[str, str],
    approval_hashes: dict[str, str],
    apply_receipt_hashes: dict[str, str],
    reconciled_at: datetime,
) -> dict[str, object]:
    _require_hashes(input_hash, result_hash, certificate_hash)
    required = tuple(sorted(str(NodeCode(code)) for code in required_node_codes))
    if not required or any(
        set(mapping) != set(required)
        for mapping in (
            snapshot_hashes,
            prepare_receipt_hashes,
            approval_hashes,
            apply_receipt_hashes,
        )
    ):
        raise federation_error("FEDERATED_RECONCILIATION_INCOMPLETE", 409)
    for code in required:
        _require_hashes(
            snapshot_hashes[code],
            prepare_receipt_hashes[code],
            approval_hashes[code],
            apply_receipt_hashes[code],
        )
    body: dict[str, object] = {
        "cycle_id": cycle_id,
        "input_hash": input_hash,
        "result_hash": result_hash,
        "certificate_hash": certificate_hash,
        "required_node_codes": list(required),
        "snapshot_hashes": {code: snapshot_hashes[code] for code in required},
        "prepare_receipt_hashes": {code: prepare_receipt_hashes[code] for code in required},
        "approval_hashes": {code: approval_hashes[code] for code in required},
        "apply_receipt_hashes": {code: apply_receipt_hashes[code] for code in required},
        "reconciled_at": utc_timestamp(reconciled_at),
    }
    return {**body, "proof_hash": payload_hash(body)}


def _positions(
    obligations: tuple[FederatedObligationInput, ...], clearing: ClearingResult
) -> tuple[NodePosition, ...]:
    by_id = {item.obligation_id: item for item in clearing.entries}
    amounts: dict[tuple[str, str], list[Decimal]] = {}
    for obligation in obligations:
        result = by_id[obligation.obligation_id]
        debtor_key = (str(NodeCode(obligation.debtor_node_code)), obligation.unit_code)
        creditor_key = (str(NodeCode(obligation.creditor_node_code)), obligation.unit_code)
        amounts.setdefault(debtor_key, [Decimal(0)] * 4)
        amounts.setdefault(creditor_key, [Decimal(0)] * 4)
        amounts[debtor_key][0] += result.amount_before
        amounts[debtor_key][2] += result.amount_after
        amounts[creditor_key][1] += result.amount_before
        amounts[creditor_key][3] += result.amount_after
    return tuple(
        NodePosition(code, unit, values[0], values[1], values[2], values[3])
        for (code, unit), values in sorted(amounts.items())
    )


def _obligation_key(item: FederatedObligationInput) -> tuple[str, str, str, str]:
    return (
        item.unit_code,
        str(NodeCode(item.debtor_node_code)),
        str(NodeCode(item.creditor_node_code)),
        item.obligation_id,
    )


def _require_hashes(*values: str) -> None:
    if any(not _is_sha256(value) for value in values):
        raise federation_error("FEDERATED_ARTIFACT_HASH_INVALID", 422)


def _is_sha256(value: str) -> bool:
    return (
        len(value) == 71
        and value.startswith("sha256:")
        and all(char in "0123456789abcdef" for char in value[7:])
    )
