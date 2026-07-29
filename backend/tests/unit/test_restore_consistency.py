import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pyotp

from cooperative_clearing.modules.identity.application.security import MfaSecretCipher
from cooperative_clearing.modules.identity.infrastructure.models import AuthenticationFactor
from cooperative_clearing.modules.inventory.infrastructure.blob_store import EncryptedBlobStore
from cooperative_clearing.modules.inventory.infrastructure.models import EvidenceBlob
from cooperative_clearing.modules.journal.domain.crypto import NodeSigner
from cooperative_clearing.modules.node.infrastructure.models import NodeKeyRecord
from cooperative_clearing.modules.operations.application.restore_consistency import (
    _FailureCollector,
    _verify_evidence,
    _verify_mfa,
    _verify_node_signing_key,
)
from cooperative_clearing.shared.core.config import Settings


async def _chunks(content: bytes) -> AsyncIterator[bytes]:
    yield content


def _secret(path: Path, value: str) -> Path:
    path.write_text(value, encoding="ascii")
    return path


async def test_evidence_verification_detects_tampering_and_orphans(tmp_path: Path) -> None:
    key_file = _secret(tmp_path / "blob.key", "ab" * 32)
    root = tmp_path / "blobs"
    store = EncryptedBlobStore(root, key_file)
    cooperative_id = uuid4()
    content = b"restored evidence"
    digest = hashlib.sha256(content).hexdigest()
    stored = await store.put(
        cooperative_id=cooperative_id,
        expected_sha256=digest,
        expected_size=len(content),
        chunks=_chunks(content),
    )
    evidence = EvidenceBlob(
        id=uuid4(),
        cooperative_id=cooperative_id,
        expected_sha256=digest,
        expected_size=len(content),
        status="READY",
        storage_key=stored.storage_key,
        encryption_algorithm=stored.encryption_algorithm,
    )
    settings = Settings(blob_root=root, blob_encryption_key_file=key_file)
    failures = _FailureCollector()

    assert _verify_evidence(settings, [evidence], failures) == (1, 0)
    assert failures.count == 0

    encrypted_path = root / stored.storage_key
    encrypted = encrypted_path.read_bytes()
    encrypted_path.write_bytes(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))
    orphan = root / str(uuid4()) / "aa" / f"{'0' * 64}.ccb"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"orphan")
    failures = _FailureCollector()

    assert _verify_evidence(settings, [evidence], failures) == (0, 1)
    assert {failure.code for failure in failures.items} == {
        "EVIDENCE_CONTENT_CORRUPT",
        "ORPHAN_EVIDENCE_BLOB",
    }


def test_installed_node_and_mfa_keys_must_match_restored_records(tmp_path: Path) -> None:
    node_key = _secret(tmp_path / "node.key", "11" * 32)
    mfa_key = _secret(tmp_path / "mfa.key", "22" * 32)
    signer = NodeSigner.from_seed_hex("33" * 32)
    record = NodeKeyRecord(
        id=uuid4(),
        node_id=uuid4(),
        purpose="NODE_SIGNING",
        algorithm="Ed25519",
        public_key=signer.public_key_bytes,
        fingerprint=signer.fingerprint,
        status="ACTIVE",
    )
    settings = Settings(node_signing_seed_file=node_key, mfa_encryption_key_file=mfa_key)
    failures = _FailureCollector()

    _verify_node_signing_key(settings, [record], failures)

    assert {failure.code for failure in failures.items} == {
        "NODE_SIGNING_FINGERPRINT_MISMATCH",
        "NODE_SIGNING_PUBLIC_KEY_MISMATCH",
    }

    cipher = MfaSecretCipher(mfa_key)
    factor_id = uuid4()
    user_id = uuid4()
    nonce, ciphertext = cipher.encrypt(
        factor_id=factor_id,
        user_id=user_id,
        secret=pyotp.random_base32(),
    )
    factor = AuthenticationFactor(
        id=factor_id,
        user_id=user_id,
        factor_type="TOTP",
        status="ACTIVE",
        secret_nonce=nonce,
        secret_ciphertext=ciphertext,
        encryption_key_version="v1",
    )
    wrong_mfa_key = _secret(tmp_path / "wrong-mfa.key", "44" * 32)
    failures = _FailureCollector()

    assert _verify_mfa(
        Settings(mfa_encryption_key_file=wrong_mfa_key), [factor], failures
    ) == 0
    assert [failure.code for failure in failures.items] == ["MFA_SECRET_UNAVAILABLE"]
