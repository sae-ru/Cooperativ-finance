"""Versioned canonical JSON and Ed25519 signing for journal evidence."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from cooperative_clearing.shared.domain.errors import DomainError

CANONICALIZATION_PROFILE = "RFC8785-JCS-1"
SIGNATURE_ALGORITHM = "Ed25519"
HASH_ALGORITHM = "SHA-256"
GENESIS_PREVIOUS_HASH = None


def canonicalize(value: object) -> bytes:
    """Return the one accepted canonical representation for signed JSON."""

    try:
        return rfc8785.dumps(value)  # type: ignore[arg-type]
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as exc:
        raise DomainError(
            code="CANONICALIZATION_FAILED",
            message_key="errors.journal.canonicalization_failed",
            status_code=422,
        ) from exc


def sha256_ref(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def payload_hash(payload: object) -> str:
    return sha256_ref(canonicalize(payload))


def utc_timestamp(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class NodeSigner:
    """In-memory signing adapter; private key material is never persisted."""

    _private_key: Ed25519PrivateKey

    @classmethod
    def from_seed_hex(cls, seed_hex: str) -> "NodeSigner":
        try:
            seed = bytes.fromhex(seed_hex)
        except ValueError as exc:
            raise DomainError(
                code="NODE_SIGNING_SEED_INVALID",
                message_key="errors.journal.node_signing_seed_invalid",
                status_code=503,
            ) from exc
        if len(seed) != 32:
            raise DomainError(
                code="NODE_SIGNING_SEED_INVALID",
                message_key="errors.journal.node_signing_seed_invalid",
                status_code=503,
            )
        return cls(Ed25519PrivateKey.from_private_bytes(seed))

    @property
    def public_key_bytes(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def fingerprint(self) -> str:
        return sha256_ref(self.public_key_bytes)

    def sign(self, message: bytes) -> bytes:
        return self._private_key.sign(message)

    def verify(self, signature: bytes, message: bytes) -> bool:
        return verify_signature(self.public_key_bytes, signature, message)


def verify_signature(public_key: bytes, signature: bytes, message: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
    except (InvalidSignature, ValueError):
        return False
    return True
