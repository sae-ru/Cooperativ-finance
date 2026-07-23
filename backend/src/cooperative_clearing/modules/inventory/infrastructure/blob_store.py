"""Encrypted, content-addressed local evidence storage."""

import hashlib
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from cooperative_clearing.shared.core.secrets import read_text_secret
from cooperative_clearing.shared.domain.errors import DomainError

MAGIC = b"CCB1"
NONCE_BYTES = 12
TAG_BYTES = 16


@dataclass(frozen=True, slots=True)
class StoredBlob:
    storage_key: str
    size: int
    sha256: str
    encryption_algorithm: str = "AES-256-GCM-v1"


class EncryptedBlobStore:
    def __init__(self, root: Path, key_file: Path) -> None:
        self.root = root
        secret = read_text_secret(key_file, minimum_length=64)
        try:
            self.key = bytes.fromhex(secret)
        except ValueError as exc:
            raise _error("BLOB_ENCRYPTION_KEY_INVALID", 503) from exc
        if len(self.key) != 32:
            raise _error("BLOB_ENCRYPTION_KEY_INVALID", 503)

    async def put(
        self,
        *,
        cooperative_id: UUID,
        expected_sha256: str,
        expected_size: int,
        chunks: AsyncIterator[bytes],
    ) -> StoredBlob:
        digest = expected_sha256.casefold()
        storage_key = f"{cooperative_id}/{digest[:2]}/{digest}.ccb"
        target = self._path(storage_key)
        staging = self.root / ".staging"
        staging.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = staging / f"{uuid4()}.tmp"
        nonce = os.urandom(NONCE_BYTES)
        encryptor = Cipher(algorithms.AES(self.key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(self._aad(cooperative_id, digest))
        calculated = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as stream:
                os.chmod(temporary, 0o600)
                stream.write(MAGIC)
                stream.write(nonce)
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > expected_size:
                        raise _error("EVIDENCE_SIZE_MISMATCH", 422)
                    calculated.update(chunk)
                    stream.write(encryptor.update(chunk))
                stream.write(encryptor.finalize())
                stream.write(encryptor.tag)
                stream.flush()
                os.fsync(stream.fileno())
            if size != expected_size:
                raise _error("EVIDENCE_SIZE_MISMATCH", 422)
            if calculated.hexdigest() != digest:
                raise _error("EVIDENCE_HASH_MISMATCH", 422)
            if target.exists():
                temporary.unlink()
            else:
                temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return StoredBlob(storage_key=storage_key, size=size, sha256=digest)

    def read_verified(
        self,
        *,
        cooperative_id: UUID,
        storage_key: str,
        expected_sha256: str,
        expected_size: int,
    ) -> bytes:
        path = self._path(storage_key)
        try:
            encrypted = path.read_bytes()
        except OSError as exc:
            raise _error("EVIDENCE_CONTENT_UNAVAILABLE", 503) from exc
        if len(encrypted) < len(MAGIC) + NONCE_BYTES + TAG_BYTES or not encrypted.startswith(MAGIC):
            raise _error("EVIDENCE_CONTENT_CORRUPT", 503)
        offset = len(MAGIC)
        nonce = encrypted[offset : offset + NONCE_BYTES]
        ciphertext_and_tag = encrypted[offset + NONCE_BYTES :]
        ciphertext, tag = ciphertext_and_tag[:-TAG_BYTES], ciphertext_and_tag[-TAG_BYTES:]
        decryptor = Cipher(algorithms.AES(self.key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(
            self._aad(cooperative_id, expected_sha256.casefold())
        )
        try:
            content = decryptor.update(ciphertext) + decryptor.finalize()
        except (InvalidTag, ValueError) as exc:
            raise _error("EVIDENCE_CONTENT_CORRUPT", 503) from exc
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_sha256:
            raise _error("EVIDENCE_CONTENT_CORRUPT", 503)
        return content

    def _path(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        root = self.root.resolve()
        if root not in candidate.parents:
            raise _error("EVIDENCE_STORAGE_KEY_INVALID", 500)
        return candidate

    @staticmethod
    def _aad(cooperative_id: UUID, digest: str) -> bytes:
        return f"cooperative-clearing:evidence:v1:{cooperative_id}:{digest}".encode()


def _error(code: str, status_code: int) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.inventory.{code.lower()}",
        status_code=status_code,
    )
