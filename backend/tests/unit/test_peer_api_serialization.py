"""Regression tests for signed peer response serialization."""

import json

from cooperative_clearing.api.peer import _canonical_peer_response
from cooperative_clearing.modules.federation.application.peer_protocol import (
    SignedPeerResponse,
)


def test_signed_peer_response_preserves_canonical_timestamps() -> None:
    document: dict[str, object] = {
        "protocol_version": "CC-PEER-1",
        "message_id": "0f1d80a7-a5c6-4b9d-89c2-42c948411f41",
        "request_hash": "sha256:" + "1" * 64,
        "source_node_code": "node-b",
        "target_node_code": "node-a",
        "capability": "CLEARING",
        "operation": "CLEARING_SNAPSHOT",
        "signer_fingerprint": "sha256:" + "2" * 64,
        "signed_at": "2026-07-22T02:17:56.000000Z",
        "expires_at": "2026-07-22T02:18:56.000000Z",
        "payload": {"status": "COLLECTING_SNAPSHOTS"},
        "payload_hash": "sha256:" + "3" * 64,
    }

    response = _canonical_peer_response(SignedPeerResponse(document, b"x" * 64))
    wire = json.loads(bytes(response.body))

    assert {key: value for key, value in wire.items() if key != "signature_base64"} == document
    assert wire["signed_at"] == "2026-07-22T02:17:56.000000Z"
    assert wire["expires_at"] == "2026-07-22T02:18:56.000000Z"
