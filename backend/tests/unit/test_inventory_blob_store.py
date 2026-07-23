from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest

from cooperative_clearing.modules.inventory.infrastructure.blob_store import EncryptedBlobStore
from cooperative_clearing.shared.domain.errors import DomainError


async def chunks(content: bytes) -> AsyncIterator[bytes]:
    yield content[:5]
    yield content[5:]


async def test_blob_store_encrypts_and_verifies_content(tmp_path: Path) -> None:
    key_file = tmp_path / "key"
    key_file.write_text("ab" * 32, encoding="ascii")
    root = tmp_path / "blobs"
    content = b"receipt evidence payload"
    digest = "339912fab79b3e0ee447301853f0ff75710a36638e7a910c5c1dde2f4753360f"
    cooperative_id = uuid4()
    store = EncryptedBlobStore(root, key_file)

    stored = await store.put(
        cooperative_id=cooperative_id,
        expected_sha256=digest,
        expected_size=len(content),
        chunks=chunks(content),
    )
    encrypted = (root / stored.storage_key).read_bytes()
    assert content not in encrypted
    assert (
        store.read_verified(
            cooperative_id=cooperative_id,
            storage_key=stored.storage_key,
            expected_sha256=digest,
            expected_size=len(content),
        )
        == content
    )

    (root / stored.storage_key).write_bytes(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))
    with pytest.raises(DomainError, match="EVIDENCE_CONTENT_CORRUPT"):
        store.read_verified(
            cooperative_id=cooperative_id,
            storage_key=stored.storage_key,
            expected_sha256=digest,
            expected_size=len(content),
        )
