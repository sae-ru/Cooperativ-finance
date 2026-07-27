import io
import json
import zipfile
from datetime import UTC, datetime

import pytest

from cooperative_clearing.modules.operations.application.diagnostics import (
    EXCLUDED_CATEGORIES,
    build_encrypted_artifact,
    build_plain_bundle,
    decrypt_bundle,
)


def plain_bundle() -> bytes:
    return build_plain_bundle(
        node_code="node-demo-01",
        release="1.2.3",
        generated_at=datetime(2026, 7, 27, 12, tzinfo=UTC),
        operations={"signed_events": 12, "active_sessions": 1},
        host_readiness={
            "status": "ATTENTION",
            "checks": [{"name": "backup", "code": "BACKUP_STATUS_MISSING"}],
        },
        metrics='coop_operational_records{kind="signed_events"} 12\n',
    )

def test_plain_diagnostic_bundle_is_bounded_and_reproducible() -> None:
    first = plain_bundle()
    second = plain_bundle()

    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "operations.json",
            "host-readiness.json",
            "metrics.prom",
        }
        manifest = json.loads(archive.read("manifest.json"))
        operations_payload = archive.read("operations.json")
        readiness_payload = archive.read("host-readiness.json")
    assert manifest["excluded"] == list(EXCLUDED_CATEGORIES)
    assert b"password" not in operations_payload.lower()
    assert b"token" not in readiness_payload.lower()


def test_encrypted_diagnostic_bundle_round_trip_and_tamper_rejection() -> None:
    passphrase = "correct horse battery staple"
    artifact = build_encrypted_artifact(
        node_code="node-demo-01",
        release="1.2.3",
        generated_at=datetime(2026, 7, 27, 12, tzinfo=UTC),
        operations={"signed_events": 12, "active_sessions": 1},
        host_readiness={"status": "ATTENTION", "checks": []},
        metrics='coop_operational_records{kind="signed_events"} 12\n',
        passphrase=passphrase,
    )

    plain = decrypt_bundle(artifact.payload, passphrase)
    assert artifact.filename == "cooperative-clearing-diagnostic-20260727T120000Z.ccdiag"
    assert zipfile.is_zipfile(io.BytesIO(plain))
    assert passphrase.encode() not in artifact.payload

    tampered = artifact.payload[:-1] + bytes([artifact.payload[-1] ^ 1])
    with pytest.raises(ValueError, match="wrong passphrase or corrupted"):
        decrypt_bundle(tampered, passphrase)
    with pytest.raises(ValueError, match="wrong passphrase or corrupted"):
        decrypt_bundle(artifact.payload, "wrong passphrase value")
