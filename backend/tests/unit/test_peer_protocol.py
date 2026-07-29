"""Pure online peer protocol and endpoint policy tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cooperative_clearing.modules.federation.domain.peer_protocol import (
    PEER_PROTOCOL_VERSION,
    PeerOperation,
    PeerRequest,
    validate_request_window,
)
from cooperative_clearing.modules.federation.infrastructure.peer_transport import (
    peer_message_url,
)
from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.shared.core.config import Environment
from cooperative_clearing.shared.domain.errors import DomainError


def test_peer_request_is_canonical_and_bound_to_target() -> None:
    now = datetime(2026, 7, 22, 1, 45, tzinfo=UTC)
    request = PeerRequest(
        message_id=uuid4(),
        source_node_code="node-east-01",
        target_node_code="node-west-01",
        operation=PeerOperation.CATALOG_SEARCH,
        signer_fingerprint="sha256:" + "1" * 64,
        issued_at=now,
        expires_at=now + timedelta(seconds=60),
        payload={"product_code": "CABBAGE.WHITE", "quantity": "10.000"},
    )

    document = request.document()

    assert document["protocol_version"] == PEER_PROTOCOL_VERSION
    assert document["source_node_code"] == "node-east-01"
    assert document["target_node_code"] == "node-west-01"
    assert document["capability"] == "CATALOG"
    assert document["payload_hash"] == payload_hash(request.payload)


def test_peer_request_window_rejects_expiry_and_excessive_ttl() -> None:
    now = datetime.now(UTC)
    with pytest.raises(DomainError, match="PEER_REQUEST_EXPIRED"):
        validate_request_window(
            now=now,
            issued_at=now - timedelta(minutes=2),
            expires_at=now - timedelta(seconds=1),
        )
    with pytest.raises(DomainError, match="PEER_REQUEST_WINDOW_INVALID"):
        validate_request_window(
            now=now,
            issued_at=now,
            expires_at=now + timedelta(minutes=6),
        )


def test_peer_endpoint_requires_https_in_hardened_environments() -> None:
    assert peer_message_url("https://peer.example/base", Environment.PRODUCTION) == (
        "https://peer.example/base/api/v1/federation/peer/messages"
    )
    assert peer_message_url("http://peer:8080", Environment.TEST).startswith("http://peer:8080")
    with pytest.raises(DomainError, match="PEER_ENDPOINT_INVALID"):
        peer_message_url("http://peer:8080", Environment.PRODUCTION)
    with pytest.raises(DomainError, match="PEER_ENDPOINT_INVALID"):
        peer_message_url(
            "https://user:" + "password@peer.example",
            Environment.PRODUCTION,
        )
