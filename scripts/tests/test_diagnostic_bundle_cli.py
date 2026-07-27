from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
import warnings
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_SOURCE = ROOT / "backend" / "src"
sys.path.insert(0, str(BACKEND_SOURCE))

from cooperative_clearing.modules.operations.application.diagnostics import (  # noqa: E402
    build_encrypted_artifact,
)

SCRIPT = ROOT / "scripts" / "diagnostic_bundle.py"
SPEC = importlib.util.spec_from_file_location("diagnostic_bundle_script", SCRIPT)
assert SPEC and SPEC.loader
diagnostic_bundle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = diagnostic_bundle
SPEC.loader.exec_module(diagnostic_bundle)


class DiagnosticBundleCliTests(unittest.TestCase):
    def test_decrypt_to_directory_verifies_inventory_and_checksums(self) -> None:
        with tempfile.TemporaryDirectory(prefix="diagnostic-bundle-") as temporary:
            root = Path(temporary)
            passphrase = "diagnostic passphrase 2026"
            artifact = build_encrypted_artifact(
                node_code="node-test-01",
                release="1.2.3",
                generated_at=datetime(2026, 7, 27, 12, tzinfo=UTC),
                operations={"signed_events": 3},
                host_readiness={"status": "OPERATIONAL", "checks": []},
                metrics="coop_operational_records 3\n",
                passphrase=passphrase,
            )
            source = root / artifact.filename
            source.write_bytes(artifact.payload)
            secret = root / "passphrase.txt"
            secret.write_text(passphrase + "\n", encoding="utf-8")
            output = root / "decoded"

            diagnostic_bundle.decrypt_to_directory(source, output, secret)

            self.assertEqual(
                {path.name for path in output.iterdir()},
                diagnostic_bundle.EXPECTED_FILES,
            )
            self.assertIn(
                b'"format": "cooperative-clearing-diagnostic-v1"',
                (output / "manifest.json").read_bytes(),
            )

    def test_duplicate_archive_names_are_rejected(self) -> None:
        payload = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(payload, "w") as archive:
                for name in diagnostic_bundle.EXPECTED_FILES:
                    archive.writestr(name, b"{}")
                archive.writestr("metrics.prom", b"duplicate")

        with self.assertRaisesRegex(
            diagnostic_bundle.DiagnosticBundleError,
            "inventory mismatch",
        ):
            diagnostic_bundle.verify_archive(payload.getvalue())

    def test_oversized_encrypted_input_is_rejected_before_decryption(self) -> None:
        with tempfile.TemporaryDirectory(prefix="diagnostic-bundle-") as temporary:
            root = Path(temporary)
            source = root / "oversized.ccdiag"
            source.write_bytes(b"x" * (diagnostic_bundle.MAX_ENCRYPTED_BYTES + 1))
            secret = root / "passphrase.txt"
            secret.write_text("diagnostic passphrase 2026\n", encoding="utf-8")

            with self.assertRaisesRegex(
                diagnostic_bundle.DiagnosticBundleError,
                "too large",
            ):
                diagnostic_bundle.decrypt_to_directory(source, root / "output", secret)

    def test_wrong_passphrase_does_not_create_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="diagnostic-bundle-") as temporary:
            root = Path(temporary)
            artifact = build_encrypted_artifact(
                node_code="node-test-01",
                release="1.2.3",
                generated_at=datetime(2026, 7, 27, 12, tzinfo=UTC),
                operations={},
                host_readiness={},
                metrics="",
                passphrase="correct diagnostic passphrase",
            )
            source = root / artifact.filename
            source.write_bytes(artifact.payload)
            secret = root / "passphrase.txt"
            secret.write_text("wrong diagnostic passphrase\n", encoding="utf-8")
            output = root / "decoded"

            with self.assertRaises(diagnostic_bundle.DiagnosticBundleError):
                diagnostic_bundle.decrypt_to_directory(source, output, secret)

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
