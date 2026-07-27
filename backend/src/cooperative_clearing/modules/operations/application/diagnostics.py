"""PII-free encrypted diagnostic bundles for local operators."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import struct
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

BUNDLE_FORMAT = "cooperative-clearing-diagnostic-v1"
ENCRYPTED_FORMAT = "cooperative-clearing-diagnostic-encrypted-v1"
MAGIC = b"CCDIAG1\n"
MAX_HEADER_BYTES = 16_384
SCRYPT_LENGTH = 32
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1

INCLUDED_FILES = (
    "manifest.json",
    "operations.json",
    "host-readiness.json",
    "metrics.prom",
)
EXCLUDED_CATEGORIES = (
    "raw_logs",
    "personal_data",
    "secrets",
    "tokens",
    "private_keys",
    "signed_payloads",
)


@dataclass(frozen=True, slots=True)
class DiagnosticArtifact:
    filename: str
    content_type: str
    payload: bytes


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def build_plain_bundle(
    *,
    node_code: str,
    release: str,
    generated_at: datetime,
    operations: dict[str, object],
    host_readiness: dict[str, object],
    metrics: str,
) -> bytes:
    entries = {
        "operations.json": _json_bytes(operations),
        "host-readiness.json": _json_bytes(host_readiness),
        "metrics.prom": metrics.encode("utf-8"),
    }
    manifest = {
        "format": BUNDLE_FORMAT,
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "node_code": node_code,
        "release": release,
        "contents": [
            {
                "name": name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
            for name, payload in sorted(entries.items())
        ],
        "excluded": list(EXCLUDED_CATEGORIES),
    }
    entries["manifest.json"] = _json_bytes(manifest)

    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name in INCLUDED_FILES:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, entries[name])
    return output.getvalue()


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(
        salt=salt,
        length=SCRYPT_LENGTH,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    ).derive(passphrase.encode("utf-8"))


def encrypt_bundle(plain_bundle: bytes, passphrase: str) -> bytes:
    if len(passphrase) < 16 or len(passphrase) > 128:
        raise ValueError("diagnostic passphrase length must be between 16 and 128")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    header = {
        "format": ENCRYPTED_FORMAT,
        "cipher": "AES-256-GCM",
        "kdf": "scrypt",
        "scrypt_n": SCRYPT_N,
        "scrypt_r": SCRYPT_R,
        "scrypt_p": SCRYPT_P,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "plaintext_sha256": hashlib.sha256(plain_bundle).hexdigest(),
    }
    header_bytes = _json_bytes(header)
    key = _derive_key(passphrase, salt)
    encrypted = AESGCM(key).encrypt(nonce, plain_bundle, header_bytes)
    return MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes + encrypted


def decrypt_bundle(encrypted_bundle: bytes, passphrase: str) -> bytes:
    prefix = len(MAGIC)
    if not encrypted_bundle.startswith(MAGIC) or len(encrypted_bundle) < prefix + 4:
        raise ValueError("invalid diagnostic bundle header")
    header_length = struct.unpack(">I", encrypted_bundle[prefix : prefix + 4])[0]
    if header_length <= 0 or header_length > MAX_HEADER_BYTES:
        raise ValueError("invalid diagnostic bundle header length")
    header_start = prefix + 4
    header_end = header_start + header_length
    if header_end >= len(encrypted_bundle):
        raise ValueError("truncated diagnostic bundle")
    header_bytes = encrypted_bundle[header_start:header_end]
    try:
        header = json.loads(header_bytes)
        if (
            not isinstance(header, dict)
            or header.get("format") != ENCRYPTED_FORMAT
            or header.get("cipher") != "AES-256-GCM"
            or header.get("kdf") != "scrypt"
            or header.get("scrypt_n") != SCRYPT_N
            or header.get("scrypt_r") != SCRYPT_R
            or header.get("scrypt_p") != SCRYPT_P
        ):
            raise ValueError("unsupported diagnostic bundle parameters")
        salt = base64.b64decode(header["salt"], validate=True)
        nonce = base64.b64decode(header["nonce"], validate=True)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid diagnostic bundle metadata") from exc
    if len(salt) != 16 or len(nonce) != 12:
        raise ValueError("invalid diagnostic bundle salt or nonce")
    key = _derive_key(passphrase, salt)
    try:
        plain = AESGCM(key).decrypt(nonce, encrypted_bundle[header_end:], header_bytes)
    except InvalidTag as exc:
        raise ValueError("wrong passphrase or corrupted diagnostic bundle") from exc
    if hashlib.sha256(plain).hexdigest() != header.get("plaintext_sha256"):
        raise ValueError("diagnostic bundle checksum mismatch")
    return plain


def build_encrypted_artifact(
    *,
    node_code: str,
    release: str,
    generated_at: datetime,
    operations: dict[str, object],
    host_readiness: dict[str, object],
    metrics: str,
    passphrase: str,
) -> DiagnosticArtifact:
    plain = build_plain_bundle(
        node_code=node_code,
        release=release,
        generated_at=generated_at,
        operations=operations,
        host_readiness=host_readiness,
        metrics=metrics,
    )
    timestamp = generated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return DiagnosticArtifact(
        filename=f"cooperative-clearing-diagnostic-{timestamp}.ccdiag",
        content_type="application/vnd.cooperative-clearing.diagnostic",
        payload=encrypt_bundle(plain, passphrase),
    )
