"""Pure validation tests for nested distributed-clearing artifacts."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cooperative_clearing.modules.federation.application.peer_clearing import (
    _artifact,
    _artifact_list,
    _datetime,
    _hash,
    _node_list,
    _positive_int,
    _uuid,
    _wire_artifact,
)
from cooperative_clearing.shared.domain.errors import DomainError

DIGEST = "sha256:" + "a" * 64
FINGERPRINT = "sha256:" + "b" * 64


def test_nested_artifacts_round_trip_through_the_wire_contract() -> None:
    payload: dict[str, object] = {"node_code": "node-a", "receipt_hash": DIGEST}
    wire = _wire_artifact(payload, DIGEST, b"signed", FINGERPRINT)

    artifact = _artifact({"receipt": wire}, "receipt", "receipt_hash")
    artifacts = _artifact_list(
        {"receipts": [wire]}, "receipts", hash_field="receipt_hash", maximum=2
    )

    assert artifact.payload == payload
    assert artifact.artifact_hash == DIGEST
    assert artifact.signature == b"signed"
    assert artifact.signer_fingerprint == FINGERPRINT
    assert artifacts == (artifact,)


@pytest.mark.parametrize(
    ("wire", "code"),
    [
        (None, "FEDERATED_ARTIFACT_INVALID"),
        ({"payload": "not-an-object"}, "FEDERATED_ARTIFACT_INVALID"),
        (
            {
                "payload": {"receipt_hash": "sha256:" + "c" * 64},
                "hash": DIGEST,
                "signature_base64": "c2lnbmVk",
                "signer_fingerprint": FINGERPRINT,
            },
            "FEDERATED_ARTIFACT_HASH_MISMATCH",
        ),
        (
            {
                "payload": {"receipt_hash": DIGEST},
                "hash": DIGEST,
                "signature_base64": "!not-base64!",
                "signer_fingerprint": FINGERPRINT,
            },
            "FEDERATED_ARTIFACT_SIGNATURE_INVALID",
        ),
    ],
)
def test_nested_artifact_rejects_malformed_evidence(wire: object, code: str) -> None:
    with pytest.raises(DomainError, match=code):
        _artifact({"receipt": wire}, "receipt", "receipt_hash")


def test_scalar_and_participant_validators_accept_only_canonical_values() -> None:
    identifier = uuid4()
    moment = datetime(2035, 1, 2, 3, 4, 5, tzinfo=UTC)

    assert _uuid({"id": str(identifier)}, "id") == identifier
    assert _hash({"hash": DIGEST}, "hash") == DIGEST
    assert _positive_int({"version": 2}, "version") == 2
    assert _datetime({"at": "2035-01-02T03:04:05Z"}, "at") == moment
    assert _node_list({"nodes": ["NODE-B", "node-a"]}, "nodes") == ("node-a", "node-b")

    invalid_calls = (
        lambda: _uuid({"id": "not-a-uuid"}, "id"),
        lambda: _hash({"hash": "sha256:short"}, "hash"),
        lambda: _positive_int({"version": True}, "version"),
        lambda: _datetime({"at": "2035-01-02T03:04:05"}, "at"),
        lambda: _node_list({"nodes": ["node-a", "NODE-A"]}, "nodes"),
        lambda: _artifact_list({}, "items", hash_field="receipt_hash", maximum=2),
    )
    for call in invalid_calls:
        with pytest.raises(DomainError):
            call()
