import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from cooperative_clearing.modules.federation.application.service import (
    challenge_message,
    rotation_message,
)
from cooperative_clearing.modules.federation.domain.package import (
    REQUIRED_PACKAGE_FILES,
    build_package_archive,
    decode_package_archive,
    parse_event_lines,
)
from cooperative_clearing.modules.journal.domain.crypto import (
    NodeSigner,
    canonicalize,
    verify_signature,
)
from cooperative_clearing.shared.domain.errors import DomainError

SIGNER = NodeSigner.from_seed_hex("11" * 32)


def manifest() -> dict[str, object]:
    now = datetime(2026, 7, 21, 10, tzinfo=UTC)
    return {
        "package_id": "10000000-0000-0000-0000-000000000001",
        "source_node_code": "REMOTE-01",
        "source_node_id": "10000000-0000-0000-0000-000000000002",
        "target_node_code": "LOCAL-01",
        "target_node_id": "10000000-0000-0000-0000-000000000003",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "protocol_version": "1.0",
        "sequence_first": 1,
        "sequence_last": 1,
        "base_checkpoint_hash": None,
        "event_count": 1,
        "blob_count": 0,
        "required_capabilities": ["TEST_EXCHANGE"],
        "contract_id": "10000000-0000-0000-0000-000000000004",
    }


def event() -> dict[str, object]:
    return {
        "envelope": {"event_id": "10000000-0000-0000-0000-000000000005"},
        "event_hash": "sha256:" + "0" * 64,
        "key_fingerprint": "sha256:" + "1" * 64,
        "signature": "AA==",
    }


def build() -> bytes:
    archive, _ = build_package_archive(
        manifest_base=manifest(),
        events=[event()],
        certificate={"node_id": "REMOTE-01"},
        revocations={"revocations": []},
        signer=SIGNER,
    )
    return archive


def zip_payload(entries: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return output.getvalue()


def test_package_archive_is_deterministic_and_round_trips() -> None:
    first = build()
    second = build()
    assert first == second
    decoded = decode_package_archive(
        first, maximum_bytes=1_000_000, maximum_files=20, maximum_ratio=100
    )
    assert decoded.manifest["package_id"] == manifest()["package_id"]
    assert set(decoded.files) == REQUIRED_PACKAGE_FILES
    assert parse_event_lines(decoded.events_bytes, 10) == [event()]
    assert verify_signature(
        SIGNER.public_key_bytes, decoded.signature, canonicalize(decoded.manifest)
    )


def test_decoder_rejects_path_traversal() -> None:
    entries = {name: b"{}" for name in REQUIRED_PACKAGE_FILES}
    entries["../escape"] = b"forbidden"
    with pytest.raises(DomainError) as error:
        decode_package_archive(
            zip_payload(entries), maximum_bytes=100_000, maximum_files=20, maximum_ratio=100
        )
    assert error.value.code == "SYNC_ARCHIVE_PATH_INVALID"


def test_decoder_rejects_compression_bomb_ratio() -> None:
    entries = {name: b"{}" for name in REQUIRED_PACKAGE_FILES}
    entries["events.ndjson"] = b"0" * 50_000
    with pytest.raises(DomainError) as error:
        decode_package_archive(
            zip_payload(entries), maximum_bytes=100_000, maximum_files=20, maximum_ratio=2
        )
    assert error.value.code == "SYNC_ARCHIVE_RATIO_INVALID"


def test_event_lines_must_be_canonical_and_bounded() -> None:
    with pytest.raises(DomainError) as error:
        parse_event_lines(b'{"b":2, "a":1}\n', 10)
    assert error.value.code == "SYNC_EVENT_NOT_CANONICAL"
    with pytest.raises(DomainError) as error:
        parse_event_lines(b"{}\n{}\n", 1)
    assert error.value.code == "SYNC_EVENT_COUNT_INVALID"


def test_challenge_and_rotation_messages_bind_every_security_input() -> None:
    challenge_id = UUID("10000000-0000-0000-0000-000000000010")
    response: dict[str, object] = {"release": "sha256:" + "a" * 64, "receipt": "PASS"}
    challenge = challenge_message(challenge_id, "nonce", response)
    signature = SIGNER.sign(challenge)
    assert verify_signature(SIGNER.public_key_bytes, signature, challenge)
    assert not verify_signature(
        SIGNER.public_key_bytes,
        signature,
        challenge_message(challenge_id, "different", response),
    )

    rotation = rotation_message(
        node_id=UUID("10000000-0000-0000-0000-000000000011"),
        old_fingerprint="sha256:" + "b" * 64,
        new_fingerprint="sha256:" + "c" * 64,
        reason="SCHEDULED",
        valid_from=datetime(2026, 8, 1, tzinfo=UTC),
        valid_until=datetime(2027, 8, 1, tzinfo=UTC),
    )
    rotation_signature = SIGNER.sign(rotation)
    assert verify_signature(SIGNER.public_key_bytes, rotation_signature, rotation)
    decoded = json.loads(rotation)
    assert decoded["reason"] == "SCHEDULED"
