"""Pure signed-envelope rules for online node-to-node federation calls."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from cooperative_clearing.modules.federation.domain.types import federation_error
from cooperative_clearing.modules.journal.domain.crypto import payload_hash, utc_timestamp
from cooperative_clearing.modules.node.domain.node_code import NodeCode

PEER_PROTOCOL_VERSION = "CC-PEER-1"
PEER_MAX_TTL_SECONDS = 300
PEER_CLOCK_SKEW_SECONDS = 60


class PeerOperation(StrEnum):
    CATALOG_SEARCH = "CATALOG_SEARCH"
    GOODS_RESERVE = "GOODS_RESERVE"
    LOGISTICS_RESERVE = "LOGISTICS_RESERVE"
    GOODS_COMMIT = "GOODS_COMMIT"
    LOGISTICS_COMMIT = "LOGISTICS_COMMIT"
    GOODS_RELEASE = "GOODS_RELEASE"
    LOGISTICS_RELEASE = "LOGISTICS_RELEASE"
    CLEARING_SNAPSHOT = "CLEARING_SNAPSHOT"
    CLEARING_PREPARE = "CLEARING_PREPARE"
    CLEARING_PROPOSAL = "CLEARING_PROPOSAL"
    CLEARING_STATUS = "CLEARING_STATUS"
    CLEARING_COMMIT = "CLEARING_COMMIT"
    CLEARING_RELEASE = "CLEARING_RELEASE"


OPERATION_CAPABILITY = {
    PeerOperation.CATALOG_SEARCH: "CATALOG",
    PeerOperation.GOODS_RESERVE: "CATALOG",
    PeerOperation.LOGISTICS_RESERVE: "LOGISTICS",
    PeerOperation.GOODS_COMMIT: "CATALOG",
    PeerOperation.LOGISTICS_COMMIT: "LOGISTICS",
    PeerOperation.GOODS_RELEASE: "CATALOG",
    PeerOperation.LOGISTICS_RELEASE: "LOGISTICS",
    PeerOperation.CLEARING_SNAPSHOT: "CLEARING",
    PeerOperation.CLEARING_PREPARE: "CLEARING",
    PeerOperation.CLEARING_PROPOSAL: "CLEARING",
    PeerOperation.CLEARING_STATUS: "CLEARING",
    PeerOperation.CLEARING_COMMIT: "CLEARING",
    PeerOperation.CLEARING_RELEASE: "CLEARING",
}


@dataclass(frozen=True, slots=True)
class PeerRequest:
    message_id: UUID
    source_node_code: str
    target_node_code: str
    operation: PeerOperation
    signer_fingerprint: str
    issued_at: datetime
    expires_at: datetime
    payload: dict[str, object]

    def document(self) -> dict[str, object]:
        body = {
            "protocol_version": PEER_PROTOCOL_VERSION,
            "message_id": str(self.message_id),
            "source_node_code": str(NodeCode(self.source_node_code)),
            "target_node_code": str(NodeCode(self.target_node_code)),
            "capability": OPERATION_CAPABILITY[self.operation],
            "operation": self.operation.value,
            "signer_fingerprint": self.signer_fingerprint,
            "issued_at": utc_timestamp(self.issued_at),
            "expires_at": utc_timestamp(self.expires_at),
            "payload": self.payload,
        }
        return {**body, "payload_hash": payload_hash(self.payload)}


@dataclass(frozen=True, slots=True)
class PeerResponse:
    message_id: UUID
    request_hash: str
    source_node_code: str
    target_node_code: str
    operation: PeerOperation
    signer_fingerprint: str
    signed_at: datetime
    expires_at: datetime
    payload: dict[str, object]

    def document(self) -> dict[str, object]:
        body = {
            "protocol_version": PEER_PROTOCOL_VERSION,
            "message_id": str(self.message_id),
            "request_hash": self.request_hash,
            "source_node_code": str(NodeCode(self.source_node_code)),
            "target_node_code": str(NodeCode(self.target_node_code)),
            "capability": OPERATION_CAPABILITY[self.operation],
            "operation": self.operation.value,
            "signer_fingerprint": self.signer_fingerprint,
            "signed_at": utc_timestamp(self.signed_at),
            "expires_at": utc_timestamp(self.expires_at),
            "payload": self.payload,
        }
        return {**body, "payload_hash": payload_hash(self.payload)}


def validate_request_window(*, now: datetime, issued_at: datetime, expires_at: datetime) -> None:
    current = now.astimezone(UTC)
    issued = issued_at.astimezone(UTC)
    expiry = expires_at.astimezone(UTC)
    if issued > current + timedelta(seconds=PEER_CLOCK_SKEW_SECONDS):
        raise federation_error("PEER_REQUEST_FROM_FUTURE", 422)
    if expiry <= current:
        raise federation_error("PEER_REQUEST_EXPIRED", 422)
    if expiry <= issued or (expiry - issued).total_seconds() > PEER_MAX_TTL_SECONDS:
        raise federation_error("PEER_REQUEST_WINDOW_INVALID", 422)


def validate_response_window(*, now: datetime, signed_at: datetime, expires_at: datetime) -> None:
    current = now.astimezone(UTC)
    signed = signed_at.astimezone(UTC)
    expiry = expires_at.astimezone(UTC)
    if signed > current + timedelta(seconds=PEER_CLOCK_SKEW_SECONDS):
        raise federation_error("PEER_RESPONSE_FROM_FUTURE", 422)
    if expiry <= current:
        raise federation_error("PEER_RESPONSE_EXPIRED", 422)
    if expiry <= signed or (expiry - signed).total_seconds() > PEER_MAX_TTL_SECONDS:
        raise federation_error("PEER_RESPONSE_WINDOW_INVALID", 422)
