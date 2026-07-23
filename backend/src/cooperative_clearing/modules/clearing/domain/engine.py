"""Versioned deterministic clearing engine without I/O or clock access."""

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from enum import StrEnum

from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.shared.domain.errors import DomainError


class ClearingCycleStatus(StrEnum):
    DRAFT = "DRAFT"
    COLLECTING = "COLLECTING"
    INPUT_FROZEN = "INPUT_FROZEN"
    PREVIEWED = "PREVIEWED"
    DISPUTE_WINDOW = "DISPUTE_WINDOW"
    DISPUTED = "DISPUTED"
    READY_TO_FINALIZE = "READY_TO_FINALIZE"
    FINALIZED = "FINALIZED"
    RECONCILED = "RECONCILED"
    CANCELLED = "CANCELLED"
    FAILED_FINALIZATION = "FAILED_FINALIZATION"
    SUPERSEDED = "SUPERSEDED"


class ClearingPolicyStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class ClearingDisputeStatus(StrEnum):
    OPEN = "OPEN"
    UPHELD = "UPHELD"
    REJECTED = "REJECTED"


class RoundingMode(StrEnum):
    DOWN = "DOWN"
    HALF_EVEN = "HALF_EVEN"


@dataclass(frozen=True, slots=True)
class ClearingPolicyParameters:
    policy_version: int
    algorithm_id: str
    algorithm_version: str
    decimal_scale: int
    rounding_mode: RoundingMode
    minimum_operation: Decimal
    max_iterations: int
    max_cycle_length: int
    liquidity_order: tuple[str, ...] = ("A", "B", "C", "D", "E", "UNASSESSED")

    def validate(self) -> "ClearingPolicyParameters":
        if self.policy_version < 1:
            raise clearing_error("POLICY_VERSION_INVALID")
        if (
            self.algorithm_id not in {"LOCAL_NETTING", "FEDERATED_NETTING"}
            or self.algorithm_version != "1.0.0"
        ):
            raise clearing_error("ALGORITHM_VERSION_UNSUPPORTED", 409)
        if not 0 <= self.decimal_scale <= 12:
            raise clearing_error("DECIMAL_SCALE_INVALID")
        if self.minimum_operation < 0:
            raise clearing_error("MINIMUM_OPERATION_INVALID")
        if not 1 <= self.max_iterations <= 100_000:
            raise clearing_error("MAX_ITERATIONS_INVALID")
        if not 3 <= self.max_cycle_length <= 12:
            raise clearing_error("MAX_CYCLE_LENGTH_INVALID")
        if not self.liquidity_order or len(set(self.liquidity_order)) != len(self.liquidity_order):
            raise clearing_error("LIQUIDITY_ORDER_INVALID")
        return self


@dataclass(frozen=True, slots=True)
class ClearingInputEntry:
    obligation_id: str
    debtor_member_id: str
    creditor_member_id: str
    unit_id: str
    amount: Decimal
    obligation_version: int
    liquidity_class: str
    eligible: bool = True
    exclusion_reason: str | None = None
    disputed: bool = False
    frozen: bool = False
    risk_limit: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ClearingInput:
    cycle_id: str
    entries: tuple[ClearingInputEntry, ...]


@dataclass(frozen=True, slots=True)
class ClearingAllocation:
    path_kind: str
    path_index: int
    amount: Decimal
    member_path: tuple[str, ...]
    obligation_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClearingEntryResult:
    obligation_id: str
    debtor_member_id: str
    creditor_member_id: str
    unit_id: str
    obligation_version: int
    amount_before: Decimal
    cleared_amount: Decimal
    amount_after: Decimal
    inclusion_status: str
    exclusion_reason: str | None
    allocations: tuple[ClearingAllocation, ...]


@dataclass(frozen=True, slots=True)
class ClearingResult:
    algorithm_id: str
    algorithm_version: str
    input_hash: str
    parameters_hash: str
    result_hash: str
    entries: tuple[ClearingEntryResult, ...]
    allocations: tuple[ClearingAllocation, ...]
    total_before: Decimal
    total_cleared: Decimal
    total_after: Decimal
    iteration_count: int
    warnings: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "algorithm_id": self.algorithm_id,
            "algorithm_version": self.algorithm_version,
            "input_hash": self.input_hash,
            "parameters_hash": self.parameters_hash,
            "entries": [_result_entry_payload(item) for item in self.entries],
            "allocations": [_allocation_payload(item) for item in self.allocations],
            "totals": {
                "before": decimal_string(self.total_before),
                "cleared": decimal_string(self.total_cleared),
                "after": decimal_string(self.total_after),
            },
            "iteration_count": self.iteration_count,
            "warnings": list(self.warnings),
        }


def calculate_clearing(
    clearing_input: ClearingInput, policy: ClearingPolicyParameters
) -> ClearingResult:
    """Calculate bilateral offsets and stable simple cycles."""
    policy.validate()
    entries = order_clearing_entries(clearing_input.entries, policy)
    _validate_entries(entries)
    input_payload = clearing_input_payload(clearing_input.cycle_id, entries)
    parameters_payload = policy_parameters_payload(policy)
    input_hash = payload_hash(input_payload)
    parameters_hash = payload_hash(parameters_payload)
    quantum = Decimal(1).scaleb(-policy.decimal_scale)

    original = {item.obligation_id: item.amount for item in entries}
    available: dict[str, Decimal] = {}
    reasons: dict[str, str | None] = {}
    warnings: set[str] = set()
    by_id = {item.obligation_id: item for item in entries}
    entry_allocations: dict[str, list[ClearingAllocation]] = {
        item.obligation_id: [] for item in entries
    }

    for item in entries:
        reason = _exclusion_reason(item)
        reasons[item.obligation_id] = reason
        if reason is not None:
            available[item.obligation_id] = Decimal(0)
            continue
        limit = item.amount if item.risk_limit is None else min(item.amount, item.risk_limit)
        if limit <= 0:
            available[item.obligation_id] = Decimal(0)
            reasons[item.obligation_id] = "RISK_LIMIT_ZERO"
            continue
        if limit < item.amount:
            warnings.add("RISK_LIMIT_APPLIED")
        available[item.obligation_id] = limit

    allocations: list[ClearingAllocation] = []
    iteration_count = 0
    grouped: dict[str, list[ClearingInputEntry]] = {}
    for item in entries:
        grouped.setdefault(item.unit_id, []).append(item)

    for unit_id in sorted(grouped):
        unit_entries = grouped[unit_id]
        iteration_count = _apply_bilateral(
            unit_entries,
            available,
            allocations,
            entry_allocations,
            policy,
            quantum,
            iteration_count,
        )
        iteration_count = _apply_cycles(
            unit_entries,
            by_id,
            available,
            allocations,
            entry_allocations,
            policy,
            quantum,
            iteration_count,
        )
        if iteration_count >= policy.max_iterations:
            warnings.add("ITERATION_LIMIT_REACHED")

    result_entries: list[ClearingEntryResult] = []
    for item in entries:
        cleared = original[item.obligation_id] - (
            available[item.obligation_id]
            + max(Decimal(0), original[item.obligation_id] - _entry_limit(item))
        )
        cleared = max(Decimal(0), cleared)
        reason = reasons[item.obligation_id]
        if reason is None and cleared == 0:
            reason = "NO_OFFSETTING_POSITION"
        result_entries.append(
            ClearingEntryResult(
                obligation_id=item.obligation_id,
                debtor_member_id=item.debtor_member_id,
                creditor_member_id=item.creditor_member_id,
                unit_id=item.unit_id,
                obligation_version=item.obligation_version,
                amount_before=item.amount,
                cleared_amount=cleared,
                amount_after=item.amount - cleared,
                inclusion_status="EXCLUDED" if reasons[item.obligation_id] else "INCLUDED",
                exclusion_reason=reason,
                allocations=tuple(entry_allocations[item.obligation_id]),
            )
        )

    total_before = sum((item.amount_before for item in result_entries), Decimal(0))
    total_cleared = sum((item.cleared_amount for item in result_entries), Decimal(0))
    total_after = total_before - total_cleared
    provisional = ClearingResult(
        algorithm_id=policy.algorithm_id,
        algorithm_version=policy.algorithm_version,
        input_hash=input_hash,
        parameters_hash=parameters_hash,
        result_hash="",
        entries=tuple(result_entries),
        allocations=tuple(allocations),
        total_before=total_before,
        total_cleared=total_cleared,
        total_after=total_after,
        iteration_count=iteration_count,
        warnings=tuple(sorted(warnings)),
    )
    return ClearingResult(
        **{
            **{field: getattr(provisional, field) for field in provisional.__slots__},
            "result_hash": payload_hash(provisional.payload()),
        }
    )


def clearing_input_payload(
    cycle_id: str, entries: tuple[ClearingInputEntry, ...] | list[ClearingInputEntry]
) -> dict[str, object]:
    return {
        "cycle_id": cycle_id,
        "entries": [
            {
                "obligation_id": item.obligation_id,
                "debtor_member_id": item.debtor_member_id,
                "creditor_member_id": item.creditor_member_id,
                "unit_id": item.unit_id,
                "amount": decimal_string(item.amount),
                "obligation_version": item.obligation_version,
                "liquidity_class": item.liquidity_class,
                "eligible": item.eligible,
                "exclusion_reason": item.exclusion_reason,
                "disputed": item.disputed,
                "frozen": item.frozen,
                "risk_limit": decimal_string(item.risk_limit)
                if item.risk_limit is not None
                else None,
            }
            for item in entries
        ],
    }


def policy_parameters_payload(policy: ClearingPolicyParameters) -> dict[str, object]:
    return {
        "policy_version": policy.policy_version,
        "algorithm_id": policy.algorithm_id,
        "algorithm_version": policy.algorithm_version,
        "decimal_scale": policy.decimal_scale,
        "rounding_mode": policy.rounding_mode.value,
        "minimum_operation": decimal_string(policy.minimum_operation),
        "max_iterations": policy.max_iterations,
        "max_cycle_length": policy.max_cycle_length,
        "liquidity_order": list(policy.liquidity_order),
    }


def decimal_string(value: Decimal) -> str:
    fixed = format(value, "f")
    if "." in fixed:
        fixed = fixed.rstrip("0").rstrip(".")
    return fixed or "0"


def clearing_error(code: str, status_code: int = 422) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.clearing.{code.lower()}",
        status_code=status_code,
    )


def order_clearing_entries(
    entries: tuple[ClearingInputEntry, ...], policy: ClearingPolicyParameters
) -> tuple[ClearingInputEntry, ...]:
    priority = {value: index for index, value in enumerate(policy.liquidity_order)}
    return tuple(
        sorted(
            entries,
            key=lambda item: (
                item.unit_id,
                priority.get(item.liquidity_class, len(priority)),
                item.debtor_member_id,
                item.creditor_member_id,
                item.obligation_id,
            ),
        )
    )


def _validate_entries(entries: tuple[ClearingInputEntry, ...]) -> None:
    identifiers: set[str] = set()
    for item in entries:
        if item.obligation_id in identifiers:
            raise clearing_error("DUPLICATE_OBLIGATION")
        identifiers.add(item.obligation_id)
        if item.amount <= 0 or item.obligation_version < 1:
            raise clearing_error("INPUT_ENTRY_INVALID")
        if item.risk_limit is not None and item.risk_limit < 0:
            raise clearing_error("RISK_LIMIT_INVALID")


def _exclusion_reason(item: ClearingInputEntry) -> str | None:
    if item.exclusion_reason:
        return item.exclusion_reason
    if not item.eligible:
        return "NOT_ELIGIBLE"
    if item.debtor_member_id == item.creditor_member_id:
        return "SELF_OBLIGATION"
    if item.disputed:
        return "DISPUTED"
    if item.frozen:
        return "FROZEN"
    return None


def _entry_limit(item: ClearingInputEntry) -> Decimal:
    if item.risk_limit is None:
        return item.amount
    return min(item.amount, max(Decimal(0), item.risk_limit))


def _rounded_operation(
    value: Decimal, policy: ClearingPolicyParameters, quantum: Decimal
) -> Decimal:
    rounding = ROUND_DOWN if policy.rounding_mode is RoundingMode.DOWN else ROUND_HALF_EVEN
    rounded = value.quantize(quantum, rounding=rounding)
    if rounded > value:
        rounded = value.quantize(quantum, rounding=ROUND_DOWN)
    if rounded < policy.minimum_operation:
        return Decimal(0)
    return rounded


def _apply_bilateral(
    entries: list[ClearingInputEntry],
    available: dict[str, Decimal],
    allocations: list[ClearingAllocation],
    entry_allocations: dict[str, list[ClearingAllocation]],
    policy: ClearingPolicyParameters,
    quantum: Decimal,
    iteration_count: int,
) -> int:
    directions: dict[tuple[str, str], list[str]] = {}
    for item in entries:
        if available[item.obligation_id] > 0:
            directions.setdefault((item.debtor_member_id, item.creditor_member_id), []).append(
                item.obligation_id
            )
    for ids in directions.values():
        ids.sort()
    pairs = sorted({tuple(sorted(pair)) for pair in directions if pair[0] != pair[1]})
    for left, right in pairs:
        forward = directions.get((left, right), [])
        reverse = directions.get((right, left), [])
        fi = ri = 0
        while fi < len(forward) and ri < len(reverse):
            if iteration_count >= policy.max_iterations:
                return iteration_count
            first, second = forward[fi], reverse[ri]
            amount = _rounded_operation(min(available[first], available[second]), policy, quantum)
            if amount <= 0:
                break
            allocation = ClearingAllocation(
                path_kind="BILATERAL",
                path_index=len(allocations) + 1,
                amount=amount,
                member_path=(left, right, left),
                obligation_path=(first, second),
            )
            allocations.append(allocation)
            entry_allocations[first].append(allocation)
            entry_allocations[second].append(allocation)
            available[first] -= amount
            available[second] -= amount
            iteration_count += 1
            if available[first] < policy.minimum_operation:
                fi += 1
            if available[second] < policy.minimum_operation:
                ri += 1
    return iteration_count


def _apply_cycles(
    entries: list[ClearingInputEntry],
    by_id: dict[str, ClearingInputEntry],
    available: dict[str, Decimal],
    allocations: list[ClearingAllocation],
    entry_allocations: dict[str, list[ClearingAllocation]],
    policy: ClearingPolicyParameters,
    quantum: Decimal,
    iteration_count: int,
) -> int:
    unit_ids = {item.obligation_id for item in entries}
    while iteration_count < policy.max_iterations:
        cycle = _first_cycle(unit_ids, by_id, available, policy)
        if cycle is None:
            break
        obligation_path, member_path = cycle
        amount = _rounded_operation(
            min(available[obligation_id] for obligation_id in obligation_path),
            policy,
            quantum,
        )
        if amount <= 0:
            break
        allocation = ClearingAllocation(
            path_kind="CYCLE",
            path_index=len(allocations) + 1,
            amount=amount,
            member_path=member_path,
            obligation_path=obligation_path,
        )
        allocations.append(allocation)
        for obligation_id in obligation_path:
            available[obligation_id] -= amount
            entry_allocations[obligation_id].append(allocation)
        iteration_count += 1
    return iteration_count


def _first_cycle(
    unit_ids: set[str],
    by_id: dict[str, ClearingInputEntry],
    available: dict[str, Decimal],
    policy: ClearingPolicyParameters,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for obligation_id in sorted(unit_ids):
        item = by_id[obligation_id]
        if available[obligation_id] < policy.minimum_operation or available[obligation_id] <= 0:
            continue
        adjacency.setdefault(item.debtor_member_id, []).append(
            (item.creditor_member_id, obligation_id)
        )
    for edges in adjacency.values():
        edges.sort()

    def search(
        start: str,
        current: str,
        members: tuple[str, ...],
        obligations: tuple[str, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        for target, obligation_id in adjacency.get(current, []):
            if target == start and len(members) >= 3:
                return (*obligations, obligation_id), (*members, start)
            if target in members or len(members) >= policy.max_cycle_length:
                continue
            found = search(
                start,
                target,
                (*members, target),
                (*obligations, obligation_id),
            )
            if found is not None:
                return found
        return None

    for start in sorted(adjacency):
        found = search(start, start, (start,), ())
        if found is not None:
            return found
    return None


def _allocation_payload(item: ClearingAllocation) -> dict[str, object]:
    return {
        "path_kind": item.path_kind,
        "path_index": item.path_index,
        "amount": decimal_string(item.amount),
        "member_path": list(item.member_path),
        "obligation_path": list(item.obligation_path),
    }


def _result_entry_payload(item: ClearingEntryResult) -> dict[str, object]:
    return {
        "obligation_id": item.obligation_id,
        "debtor_member_id": item.debtor_member_id,
        "creditor_member_id": item.creditor_member_id,
        "unit_id": item.unit_id,
        "obligation_version": item.obligation_version,
        "amount_before": decimal_string(item.amount_before),
        "cleared_amount": decimal_string(item.cleared_amount),
        "amount_after": decimal_string(item.amount_after),
        "inclusion_status": item.inclusion_status,
        "exclusion_reason": item.exclusion_reason,
        "allocations": [_allocation_payload(value) for value in item.allocations],
    }
