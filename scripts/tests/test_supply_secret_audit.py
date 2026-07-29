from __future__ import annotations

import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "supply_secret_audit.py"
SPEC = importlib.util.spec_from_file_location("supply_secret_audit", SCRIPT)
assert SPEC and SPEC.loader
supply_secret_audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = supply_secret_audit
SPEC.loader.exec_module(supply_secret_audit)

PRIVATE_KEY_FIXTURE = (
    b"-----BEGIN "
    + b"PRIVATE KEY-----\n"
    + b"A" * 64
    + b"\n"
    + b"B" * 64
    + b"\n"
    + b"-----END "
    + b"PRIVATE KEY-----\n"
)


class SupplySecretAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="secret-audit-test-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _docker_archive(path: Path, files: dict[str, bytes]) -> None:
        layer_buffer = io.BytesIO()
        with tarfile.open(fileobj=layer_buffer, mode="w") as layer:
            for name, payload in files.items():
                info = tarfile.TarInfo(name=name)
                info.size = len(payload)
                layer.addfile(info, io.BytesIO(payload))
        layer_payload = layer_buffer.getvalue()
        with tarfile.open(path, mode="w") as outer:
            manifest_payload = json.dumps(
                [{"Config": "config.json", "RepoTags": [], "Layers": ["layer.tar"]}]
            ).encode()
            manifest_info = tarfile.TarInfo(name="manifest.json")
            manifest_info.size = len(manifest_payload)
            outer.addfile(manifest_info, io.BytesIO(manifest_payload))
            info = tarfile.TarInfo(name="layer.tar")
            info.size = len(layer_payload)
            outer.addfile(info, io.BytesIO(layer_payload))

    def test_clean_file_passes(self) -> None:
        (self.root / "settings.py").write_text(
            'database_host = "postgres"\n',
            encoding="utf-8",
        )

        report = supply_secret_audit.build_report(
            [
                supply_secret_audit.audit_files(
                    self.root,
                    ["settings.py"],
                    scope="source",
                    strict_literals=True,
                )
            ]
        )

        supply_secret_audit.require_clean(report)
        self.assertEqual(report["status"], "PASSED")

    def test_private_key_is_rejected_without_disclosing_value(self) -> None:
        secret = PRIVATE_KEY_FIXTURE + b"sensitive-private-material\n"
        (self.root / "config.txt").write_bytes(secret)
        report = supply_secret_audit.build_report(
            [
                supply_secret_audit.audit_files(
                    self.root,
                    ["config.txt"],
                    scope="source",
                    strict_literals=False,
                )
            ]
        )

        with self.assertRaises(supply_secret_audit.SecretAuditError) as raised:
            supply_secret_audit.require_clean(report)

        rendered = str(raised.exception)
        self.assertIn("PRIVATE_KEY_PEM:config.txt", rendered)
        self.assertNotIn("sensitive-private-material", rendered)
        self.assertNotIn("sensitive-private-material", str(report))

    def test_high_confidence_token_is_rejected(self) -> None:
        token = "AKIA" + "A" * 16
        (self.root / "config.txt").write_text(token, encoding="utf-8")

        audit = supply_secret_audit.audit_files(
            self.root,
            ["config.txt"],
            scope="source",
            strict_literals=False,
        )

        self.assertEqual([item.rule for item in audit.findings], ["AWS_ACCESS_KEY"])

    def test_strict_literal_rejects_value_but_allows_command_substitution(self) -> None:
        (self.root / "settings.sh").write_text(
            "password = \"" + "actual-passphrase" + "\"\n"
            'access_token="$(read-token)"\n',
            encoding="utf-8",
        )

        audit = supply_secret_audit.audit_files(
            self.root,
            ["settings.sh"],
            scope="node-payload",
            strict_literals=True,
        )

        self.assertEqual([item.rule for item in audit.findings], ["SENSITIVE_LITERAL"])
    def test_public_demo_credential_is_counted_but_allowed(self) -> None:
        (self.root / "demo.txt").write_text(
            'password = "CoopDemo-Farmer-2026!"\n',
            encoding="utf-8",
        )

        audit = supply_secret_audit.audit_files(
            self.root,
            ["demo.txt"],
            scope="source",
            strict_literals=True,
        )

        self.assertEqual(audit.findings, [])
        self.assertGreaterEqual(audit.public_demo_literals, 1)

    def test_runtime_env_rejects_plaintext_and_validates_secret_file_path(self) -> None:
        plaintext = self.root / "plaintext.env"
        plaintext.write_text("POSTGRES_PASSWORD=plain-text-value\n", encoding="utf-8")
        invalid_file = self.root / "invalid-file.env"
        invalid_file.write_text(
            "POSTGRES_PASSWORD_FILE=plain-text-value\n",
            encoding="utf-8",
        )
        safe = self.root / "safe.env"
        safe.write_text(
            "POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password\n"
            "COOP_RELEASE_PUBLIC_KEY=/opt/cooperative/release-public.pem\n",
            encoding="utf-8",
        )

        self.assertEqual(
            supply_secret_audit.audit_env_file(plaintext).findings[0].rule,
            "PLAINTEXT_SECRET_ENV",
        )
        self.assertEqual(
            supply_secret_audit.audit_env_file(invalid_file).findings[0].rule,
            "PLAINTEXT_SECRET_ENV",
        )
        self.assertEqual(supply_secret_audit.audit_env_file(safe).findings, [])

    def test_image_layer_secret_and_secret_filename_are_rejected(self) -> None:
        archive = self.root / "image.tar"
        self._docker_archive(
            archive,
            {
                "app/config.txt": PRIVATE_KEY_FIXTURE,
                "run/secrets/node_signing_seed": b"binary",
            },
        )

        audit = supply_secret_audit.audit_image_archive(archive, scope="image:api")
        rules = {item.rule for item in audit.findings}

        self.assertIn("PRIVATE_KEY_PEM", rules)
        self.assertIn("SECRET_FILENAME", rules)

    def test_clean_image_layer_passes(self) -> None:
        archive = self.root / "image.tar"
        self._docker_archive(
            archive,
            {"app/config.txt": b"service = 'cooperative-clearing'\n"},
        )

        report = supply_secret_audit.build_report(
            [supply_secret_audit.audit_image_archive(archive, scope="image:api")]
        )

        supply_secret_audit.require_clean(report)


    def test_compiled_vendor_binary_marker_is_not_a_key_file(self) -> None:
        archive = self.root / "image.tar"
        self._docker_archive(
            archive,
            {"usr/lib/libvendor.so.1": PRIVATE_KEY_FIXTURE},
        )

        report = supply_secret_audit.build_report(
            [supply_secret_audit.audit_image_archive(archive, scope="image:api")]
        )

        supply_secret_audit.require_clean(report)
    def test_backup_audit_opens_blob_archive(self) -> None:
        (self.root / "database.dump").write_bytes(b"clean-database-dump")
        with tarfile.open(self.root / "blobs.tar.gz", mode="w:gz") as archive:
            payload = PRIVATE_KEY_FIXTURE
            info = tarfile.TarInfo(name="encrypted/blob.bin")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))

        report = supply_secret_audit.audit_backup_directory(self.root)

        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(report["finding_count"], 1)

if __name__ == "__main__":
    unittest.main()