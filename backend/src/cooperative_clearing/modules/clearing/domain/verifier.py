"""Verifier for persisted clearing proofs."""

from decimal import Decimal
from typing import cast

from cooperative_clearing.modules.clearing.domain.engine import (
    ClearingInput,
    ClearingInputEntry,
    ClearingPolicyParameters,
    RoundingMode,
    calculate_clearing,
    clearing_error,
)
from cooperative_clearing.modules.journal.domain.crypto import payload_hash


def verify_proof_payload(proof: dict[str, object]) -> dict[str, object]:
    input_payload = _mapping(proof.get("input"), "PROOF_INPUT_INVALID")
    parameters = _mapping(proof.get("parameters"), "PROOF_PARAMETERS_INVALID")
    expected_result = _mapping(proof.get("result"), "PROOF_RESULT_INVALID")
    expected_hash = proof.get("proof_hash")
    if not isinstance(expected_hash, str):
        raise clearing_error("PROOF_HASH_INVALID")
    unsigned = {key: value for key, value in proof.items() if key != "proof_hash"}
    if payload_hash(unsigned) != expected_hash:
        raise clearing_error("PROOF_HASH_MISMATCH", 409)
    entries_raw = input_payload.get("entries")
    if not isinstance(entries_raw, list):
        raise clearing_error("PROOF_INPUT_INVALID")
    entries = tuple(_entry(value) for value in entries_raw)
    policy = ClearingPolicyParameters(
        policy_version=int(cast(str | int, parameters["policy_version"])),
        algorithm_id=str(parameters["algorithm_id"]),
        algorithm_version=str(parameters["algorithm_version"]),
        decimal_scale=int(cast(str | int, parameters["decimal_scale"])),
        rounding_mode=RoundingMode(str(parameters["rounding_mode"])),
        minimum_operation=Decimal(str(parameters["minimum_operation"])),
        max_iterations=int(cast(str | int, parameters["max_iterations"])),
        max_cycle_length=int(cast(str | int, parameters["max_cycle_length"])),
        liquidity_order=tuple(
            str(value) for value in cast(list[object], parameters["liquidity_order"])
        ),
    )
    result = calculate_clearing(
        ClearingInput(cycle_id=str(input_payload["cycle_id"]), entries=entries), policy
    )
    if result.input_hash != proof.get("input_hash"):
        raise clearing_error("PROOF_INPUT_HASH_MISMATCH", 409)
    if result.parameters_hash != proof.get("parameters_hash"):
        raise clearing_error("PROOF_PARAMETERS_HASH_MISMATCH", 409)
    if result.result_hash != proof.get("result_hash") or result.payload() != expected_result:
        raise clearing_error("PROOF_RESULT_MISMATCH", 409)
    return {
        "valid": True,
        "input_hash": result.input_hash,
        "parameters_hash": result.parameters_hash,
        "result_hash": result.result_hash,
        "proof_hash": expected_hash,
    }


def _entry(value: object) -> ClearingInputEntry:
    item = _mapping(value, "PROOF_INPUT_INVALID")
    risk_limit = item.get("risk_limit")
    return ClearingInputEntry(
        obligation_id=str(item["obligation_id"]),
        debtor_member_id=str(item["debtor_member_id"]),
        creditor_member_id=str(item["creditor_member_id"]),
        unit_id=str(item["unit_id"]),
        amount=Decimal(str(item["amount"])),
        obligation_version=int(cast(str | int, item["obligation_version"])),
        liquidity_class=str(item["liquidity_class"]),
        eligible=bool(item["eligible"]),
        exclusion_reason=str(item["exclusion_reason"])
        if item.get("exclusion_reason") is not None
        else None,
        disputed=bool(item["disputed"]),
        frozen=bool(item["frozen"]),
        risk_limit=Decimal(str(risk_limit)) if risk_limit is not None else None,
    )


def _mapping(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise clearing_error(code)
    return value
