from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "runtime_environment.py"
SPEC = importlib.util.spec_from_file_location("runtime_environment", SCRIPT)
assert SPEC and SPEC.loader
runtime_environment = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime_environment
SPEC.loader.exec_module(runtime_environment)


class RuntimeEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="runtime-environment-test-"
        )
        self.root = Path(self.temporary.name)
        (self.root / ".env.example").write_text(
            "# node settings\n"
            "COOP_ENVIRONMENT=dev\n"
            "COOP_DEMO_DATA_ENABLED=true\n"
            "COMPOSE_PROFILES=demo\n"
            "COOP_RELEASE=0.1.0-dev\n",
            encoding="utf-8",
        )
        self.bundle = self.root / "release-bundle"
        self.bundle.mkdir()
        self.public_key = self.root / "release-public.pem"
        self.public_key.write_text("test public key\n", encoding="utf-8")
        self.policy_sha256 = "a" * 64
        self.production_artifacts = {
            "verified_release_bundle": str(self.bundle),
            "release_public_key": str(self.public_key),
            "license_policy_sha256": self.policy_sha256,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_resolve_uses_process_then_file_then_safe_default(self) -> None:
        self.assertEqual(
            runtime_environment.resolve_environment(self.root, environ={}),
            "dev",
        )
        (self.root / ".env").write_text(
            "COOP_ENVIRONMENT=pilot\n",
            encoding="utf-8",
        )
        self.assertEqual(
            runtime_environment.resolve_environment(self.root, environ={}),
            "pilot",
        )
        (self.root / ".env").write_text(
            "COOP_ENVIRONMENT=dev\n",
            encoding="utf-8",
        )
        self.assertEqual(
            runtime_environment.resolve_environment(
                self.root,
                environ={"COOP_ENVIRONMENT": "test"},
            ),
            "test",
        )

    def test_hardened_process_and_file_disagreement_is_rejected(self) -> None:
        (self.root / ".env").write_text(
            "COOP_ENVIRONMENT=production\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            runtime_environment.EnvironmentContractError,
            "disagrees with persisted",
        ):
            runtime_environment.resolve_environment(
                self.root,
                environ={"COOP_ENVIRONMENT": "dev"},
            )

    def test_legacy_prod_alias_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            runtime_environment.EnvironmentContractError,
            "Unsupported COOP_ENVIRONMENT",
        ):
            runtime_environment.resolve_environment(
                self.root,
                environ={"COOP_ENVIRONMENT": "prod"},
            )

    def test_fresh_production_configuration_is_persistent_and_unique(self) -> None:
        result = runtime_environment.configure_mode(
            self.root,
            mode="production",
            release="1.2.3",
            **self.production_artifacts,
        )

        self.assertEqual(result.environment, "production")
        values = runtime_environment.parse_env_file(self.root / ".env")
        self.assertEqual(values["COOP_ENVIRONMENT"], "production")
        self.assertEqual(values["COOP_DEMO_DATA_ENABLED"], "false")
        self.assertEqual(values["COMPOSE_PROFILES"], "production")
        self.assertEqual(values["COOP_RELEASE"], "1.2.3")
        self.assertEqual(values["COOP_VERIFIED_RELEASE_BUNDLE"], str(self.bundle))
        self.assertEqual(values["COOP_RELEASE_PUBLIC_KEY"], str(self.public_key))
        self.assertEqual(
            values["COOP_RELEASE_LICENSE_POLICY_SHA256"],
            self.policy_sha256,
        )
        lines = (self.root / ".env").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines.count("COOP_ENVIRONMENT=production"), 1)
        self.assertIn("# node settings", lines)

    def test_demo_configuration_cannot_be_promoted_in_place(self) -> None:
        runtime_environment.configure_mode(self.root, mode="demo")
        before = (self.root / ".env").read_bytes()

        with self.assertRaisesRegex(
            runtime_environment.EnvironmentContractError,
            "cannot be promoted",
        ):
            runtime_environment.configure_mode(self.root, mode="production")

        self.assertEqual((self.root / ".env").read_bytes(), before)

    def test_known_demo_credentials_block_production(self) -> None:
        (self.root / ".env").write_text(
            "COOP_ENVIRONMENT=dev\nCOOP_DEMO_DATA_ENABLED=false\n",
            encoding="utf-8",
        )
        secrets = self.root / "secrets"
        secrets.mkdir()
        (secrets / "bootstrap_security_password").write_text(
            runtime_environment.DEMO_BOOTSTRAP_CREDENTIALS[
                "bootstrap_security_password"
            ],
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            runtime_environment.EnvironmentContractError,
            "known demo credentials",
        ):
            runtime_environment.configure_mode(self.root, mode="production")

    def test_hardened_node_cannot_be_downgraded_to_demo(self) -> None:
        (self.root / ".env").write_text(
            "COOP_ENVIRONMENT=pilot\nCOOP_DEMO_DATA_ENABLED=false\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            runtime_environment.EnvironmentContractError,
            "cannot be downgraded",
        ):
            runtime_environment.configure_mode(self.root, mode="demo")

    def test_operational_setting_prefers_process_then_persisted_value(self) -> None:
        (self.root / ".env").write_text(
            "COOP_RELEASE_PUBLIC_KEY=/persisted/key.pem\n",
            encoding="utf-8",
        )
        self.assertEqual(
            runtime_environment.resolve_setting(
                self.root,
                "COOP_RELEASE_PUBLIC_KEY",
                environ={},
            ),
            "/persisted/key.pem",
        )
        self.assertEqual(
            runtime_environment.resolve_setting(
                self.root,
                "COOP_RELEASE_PUBLIC_KEY",
                environ={"COOP_RELEASE_PUBLIC_KEY": "/process/key.pem"},
            ),
            "/process/key.pem",
        )

    def test_production_requires_pinned_operational_artifacts(self) -> None:
        with self.assertRaisesRegex(
            runtime_environment.EnvironmentContractError,
            "verified release bundle and public key",
        ):
            runtime_environment.configure_mode(
                self.root,
                mode="production",
                release="1.2.3",
            )

    def test_cli_contract_is_independent_of_caller_working_directory(self) -> None:
        previous = Path.cwd()
        try:
            os.chdir(self.root.parent)
            self.assertEqual(
                runtime_environment.resolve_environment(self.root, environ={}),
                "dev",
            )
        finally:
            os.chdir(previous)


class DeploymentScriptContractTests(unittest.TestCase):
    root: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[2]

    def test_start_scripts_use_signed_images_for_production(self) -> None:
        scripts = {
            relative: (self.root / relative).read_text(encoding="utf-8")
            for relative in ("start.bat", "start.sh")
        }
        for text in scripts.values():
            self.assertIn("COOP_ENVIRONMENT=production", text)
            self.assertIn("--load-images", text)
            self.assertIn("--no-build --pull never", text)
            self.assertIn("COOP_RELEASE_LICENSE_POLICY_SHA256", text)
        self.assertIn('if not "%~6"=="" goto production_usage', scripts["start.bat"])
        self.assertIn('[ "$#" -gt 5 ]', scripts["start.sh"])

    def test_operational_scripts_use_canonical_production_value(self) -> None:
        for relative in (
            "scripts/backup-node.ps1",
            "scripts/backup-node.sh",
            "scripts/update-node.ps1",
            "scripts/update-node.sh",
            "scripts/collect-production-evidence.ps1",
            "scripts/collect-production-evidence.sh",
        ):
            text = (self.root / relative).read_text(encoding="utf-8")
            self.assertIn("runtime_environment.py", text)
            self.assertNotIn('"prod"', text)
            self.assertNotIn("'prod'", text)


    def test_successful_update_rotates_persisted_release_context(self) -> None:
        for relative in ("scripts/update-node.ps1", "scripts/update-node.sh"):
            text = (self.root / relative).read_text(encoding="utf-8")
            self.assertIn("--verified-release-bundle", text)
            self.assertIn("--release-public-key", text)
            self.assertIn("--license-policy-sha256", text)

if __name__ == "__main__":
    unittest.main()
