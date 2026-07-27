#!/usr/bin/env python3
"""Decrypt and verify an encrypted Cooperative Clearing diagnostic bundle."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import struct
import sys
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"CCDIAG1\n"
ENCRYPTED_FORMAT = "cooperative-clearing-diagnostic-encrypted-v1"
PLAIN_FORMAT = "cooperative-clearing-diagnostic-v1"
EXPECTED_FILES = {
    "manifest.json",
    "operations.json",
    "host-readiness.json",
    "metrics.prom",
}
MAX_HEADER_BYTES = 16_384
MAX_ARCHIVE_BYTES = 5_242_880
MAX_ENCRYPTED_BYTES = 6_291_456
MAX_ENTRY_BYTES = 1_048_576
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1


class DiagnosticBundleError(RuntimeError):
    """The encrypted diagnostic bundle is invalid or cannot be authenticated."""


def decrypt_payload(payload: bytes, passphrase: str) -> bytes:
    prefix = len(MAGIC)
    if not payload.startswith(MAGIC) or len(payload) < prefix + 4:
        raise DiagnosticBundleError("invalid encrypted header")
    header_length = struct.unpack(">I", payload[prefix : prefix + 4])[0]
    if header_length <= 0 or header_length > MAX_HEADER_BYTES:
        raise DiagnosticBundleError("invalid encrypted header length")
    header_start = prefix + 4
    header_end = header_start + header_length
    if header_end >= len(payload):
        raise DiagnosticBundleError("truncated encrypted bundle")
    header_bytes = payload[header_start:header_end]
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
            raise DiagnosticBundleError("unsupported encryption parameters")
        salt = base64.b64decode(header["salt"], validate=True)
        nonce = base64.b64decode(header["nonce"], validate=True)
    except (
        DiagnosticBundleError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise DiagnosticBundleError("invalid encrypted metadata") from exc
    if len(salt) != 16 or len(nonce) != 12:
        raise DiagnosticBundleError("invalid encrypted salt or nonce")
    key = Scrypt(
        salt=salt,
        length=32,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    ).derive(passphrase.encode("utf-8"))
    try:
        plain = AESGCM(key).decrypt(nonce, payload[header_end:], header_bytes)
    except InvalidTag as exc:
        raise DiagnosticBundleError("wrong passphrase or corrupted bundle") from exc
    if len(plain) > MAX_ARCHIVE_BYTES:
        raise DiagnosticBundleError("diagnostic archive is too large")
    if hashlib.sha256(plain).hexdigest() != header.get("plaintext_sha256"):
        raise DiagnosticBundleError("diagnostic archive checksum mismatch")
    return plain


def verify_archive(plain: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(plain)) as archive:
            archive_names = archive.namelist()
            names = set(archive_names)
            if names != EXPECTED_FILES or len(archive_names) != len(EXPECTED_FILES):
                raise DiagnosticBundleError("diagnostic archive inventory mismatch")
            if any(item.file_size > MAX_ENTRY_BYTES for item in archive.infolist()):
                raise DiagnosticBundleError("diagnostic archive entry is too large")
            entries = {name: archive.read(name) for name in EXPECTED_FILES}
    except DiagnosticBundleError:
        raise
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise DiagnosticBundleError("invalid diagnostic archive") from exc
    try:
        manifest = json.loads(entries["manifest.json"])
        if not isinstance(manifest, dict) or manifest.get("format") != PLAIN_FORMAT:
            raise DiagnosticBundleError("invalid diagnostic manifest")
        contents = manifest["contents"]
        if not isinstance(contents, list):
            raise DiagnosticBundleError("invalid diagnostic manifest contents")
        expected = {
            item["name"]: item["sha256"]
            for item in contents
            if isinstance(item, dict) and "name" in item and "sha256" in item
        }
        expected_names = EXPECTED_FILES - {"manifest.json"}
        if len(contents) != len(expected_names) or set(expected) != expected_names:
            raise DiagnosticBundleError("invalid diagnostic manifest inventory")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise DiagnosticBundleError("invalid diagnostic manifest") from exc
    for name in EXPECTED_FILES - {"manifest.json"}:
        if expected.get(name) != hashlib.sha256(entries[name]).hexdigest():
            raise DiagnosticBundleError(f"diagnostic checksum mismatch: {name}")
    return entries


def decrypt_to_directory(input_path: Path, output_dir: Path, passphrase_file: Path) -> None:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise DiagnosticBundleError("output directory must be empty")
    secret = passphrase_file.read_text(encoding="utf-8-sig").rstrip("\r\n")
    if len(secret) < 16 or len(secret) > 128:
        raise DiagnosticBundleError("passphrase length must be between 16 and 128")
    if input_path.stat().st_size > MAX_ENCRYPTED_BYTES:
        raise DiagnosticBundleError("encrypted diagnostic bundle is too large")
    entries = verify_archive(decrypt_payload(input_path.read_bytes(), secret))
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in entries.items():
        (output_dir / name).write_bytes(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--passphrase-file", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        decrypt_to_directory(
            Path(args.input).resolve(),
            Path(args.output_dir).resolve(),
            Path(args.passphrase_file).resolve(),
        )
    except (DiagnosticBundleError, OSError) as exc:
        print(f"diagnostic-bundle: ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
