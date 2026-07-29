from __future__ import annotations

import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "release_bundle.py"
SPEC = importlib.util.spec_from_file_location("release_bundle", SCRIPT)
assert SPEC and SPEC.loader
release_bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_bundle)


class ReleaseBundleVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="release-bundle-test-")
        self.root = Path(self.temporary.name)
        self.private_key = self.root / "private.pem"
        self.public_key = self.root / "public.pem"
        release_bundle.generate_keypair(self.private_key, self.public_key)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def descriptor(bundle: Path, relative: str) -> dict[str, object]:
        path = release_bundle.safe_path(bundle, relative)
        return {
            "path": relative,
            "sha256": release_bundle.sha256_file(path),
            "size": path.stat().st_size,
        }

    @staticmethod
    def write_docker_archive(path: Path, files: dict[str, bytes]) -> None:
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
    def make_bundle(
        self,
        *,
        release: str = "1.2.3",
        component_license: str = "MIT",
        component_status: str = "allowed",
        platform: str = "linux/amd64",
    ) -> Path:
        bundle = self.root / f"bundle-{len(list(self.root.glob('bundle-*')))}"
        bundle.mkdir()
        (bundle / "images").mkdir()
        (bundle / "metadata/sbom").mkdir(parents=True)
        (bundle / "metadata/licenses").mkdir(parents=True)

        policy = {
            "allowed": ["MIT"],
            "denied": ["AGPL-3.0"],
            "format": release_bundle.LICENSE_POLICY_FORMAT,
            "version": "1",
        }
        policy_path = bundle / "metadata/license-policy.json"
        policy_path.write_bytes(release_bundle.canonical_json(policy))
        policy_descriptor = self.descriptor(bundle, "metadata/license-policy.json")
        policy_digest = policy_descriptor["sha256"]

        payload = []
        for relative in release_bundle.NODE_PAYLOAD:
            target_relative = f"node/{relative}"
            target = release_bundle.safe_path(bundle, target_relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"fixture:{relative}\n", encoding="utf-8")
            payload.append(self.descriptor(bundle, target_relative))

        image_rows = []
        summary = {
            "allowed": 1 if component_status == "allowed" else 0,
            "blocked": 1 if component_status == "blocked" else 0,
            "review_required": 1 if component_status == "review_required" else 0,
        }
        total = {key: value * 4 for key, value in summary.items()}
        normalized_platform = release_bundle.normalize_platform(platform)
        _, platform_architecture = normalized_platform.split("/", 1)
        for role in release_bundle.REQUIRED_IMAGE_ROLES:
            image_id = f"sha256:{release_bundle.sha256_bytes(role.encode())}"
            reference = (
                "postgres:18-alpine"
                if role == "postgres"
                else f"cooperative-clearing/{role}:{release}"
            )
            archive_relative = f"images/{role}.oci.tar"
            self.write_docker_archive(
                release_bundle.safe_path(bundle, archive_relative),
                {f"app/{role}.txt": f"archive:{role}".encode()},
            )
            sbom = {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "serialNumber": "urn:uuid:00000000-0000-0000-0000-000000000000",
                "version": 1,
                "components": [
                    {
                        "type": "library",
                        "name": f"{role}-component",
                        "version": "1",
                        "licenses": [{"license": {"id": component_license}}],
                    }
                ],
            }
            sbom_relative = f"metadata/sbom/{role}.cdx.json"
            release_bundle.safe_path(bundle, sbom_relative).write_bytes(
                release_bundle.canonical_json(sbom)
            )
            report = {
                "format": release_bundle.LICENSE_REPORT_FORMAT,
                "version": "1",
                "image_role": role,
                "image": reference,
                "policy_sha256": policy_digest,
                "summary": summary,
                "components": [
                    {
                        "component": f"{role}-component",
                        "version": "1",
                        "license": component_license,
                        "status": component_status,
                    }
                ],
            }
            report_relative = f"metadata/licenses/{role}.json"
            release_bundle.safe_path(bundle, report_relative).write_bytes(
                release_bundle.canonical_json(report)
            )
            image_rows.append(
                {
                    "role": role,
                    "reference": reference,
                    "image_id": image_id,
                    "repo_digests": [],
                    "architecture": platform_architecture,
                    "os": "linux",
                    "layer_digests": [],
                    "archive_format": "docker-image-archive",
                    "archive": self.descriptor(bundle, archive_relative),
                    "sbom": {
                        **self.descriptor(bundle, sbom_relative),
                        "component_count": 1,
                    },
                    "licenses": {
                        **self.descriptor(bundle, report_relative),
                        "summary": summary,
                    },
                }
            )

        secret_scopes = [
            release_bundle.ScopeAudit(
                scope="source",
                files_scanned=1,
                bytes_scanned=1,
            ),
            release_bundle.audit_file_scope(
                bundle,
                [str(row["path"]) for row in payload],
                scope="node-payload",
                strict_literals=True,
            ),
        ]
        secret_scopes.extend(
            release_bundle.audit_image_scope(
                bundle / f"images/{role}.oci.tar",
                scope=f"image:{role}",
            )
            for role in release_bundle.REQUIRED_IMAGE_ROLES
        )
        secret_report = release_bundle.clean_secret_audit_report(secret_scopes)
        secret_report_path = bundle / "metadata/secret-audit.json"
        secret_report_path.write_bytes(release_bundle.canonical_json(secret_report))
        secret_audit = {
            **self.descriptor(bundle, "metadata/secret-audit.json"),
            "format": release_bundle.SECRET_AUDIT_REPORT_FORMAT,
            "status": "PASSED",
            "finding_count": 0,
            "scope_count": len(secret_scopes),
        }

        manifest = {
            "format": release_bundle.BUNDLE_FORMAT,
            "version": "2",
            "release": release,
            "created_at": "2026-07-22T00:00:00+00:00",
            "source": {"commit": "0" * 40, "dirty": False, "dirty_entries": []},
            "compatibility": {
                "format": release_bundle.COMPATIBILITY_CONTRACT_FORMAT,
                "database_schema_revision": "0021_logistics_contacts",
                "migration_strategy": "expand-contract",
                "clean_install": True,
                "supported_upgrades": [
                    {
                        "source_release": "1.2.2",
                        "source_schema_revision": "0020_previous",
                        "rollback_mode": "alembic-downgrade",
                        "post_backup_events": "preserved",
                    }
                ],
                "protocols": {
                    "peer": "CC-PEER-1",
                    "sync": "1.0",
                    "federated_clearing": "1.0.0",
                },
            },
            "signature": {
                "algorithm": release_bundle.SIGNATURE_ALGORITHM,
                "encoding": "raw",
                "public_key_fingerprint": release_bundle.public_key_fingerprint(
                    self.public_key
                ),
            },
            "license_policy": policy_descriptor,
            "license_summary": total,
            "secret_audit": secret_audit,
            "platform": {
                "format": release_bundle.PLATFORM_CONTRACT_FORMAT,
                "qualification": "full-release-gate",
                "qualified_platforms": [normalized_platform],
                "excluded_platforms": [
                    {
                        "platform": f"linux/{architecture}",
                        "reason": "not-qualified-for-this-release",
                    }
                    for architecture in release_bundle.KNOWN_RELEASE_ARCHITECTURES
                    if architecture != platform_architecture
                ],
            },
            "images": image_rows,
            "node_payload": payload,
        }
        manifest_bytes = release_bundle.canonical_json(manifest)
        (bundle / "release-manifest.json").write_bytes(manifest_bytes)
        (bundle / "release-manifest.sig").write_bytes(
            release_bundle.sign_value(self.private_key, manifest_bytes)
        )
        release_bundle.write_checksums(bundle)
        return bundle

    def arguments(self, bundle: Path, **overrides: object) -> SimpleNamespace:
        values: dict[str, object] = {
            "bundle": str(bundle),
            "public_key": str(self.public_key),
            "expected_release": "1.2.3",
            "expected_policy_sha256": None,
            "expected_platform": None,
            "installed_release": None,
            "installed_schema": None,
            "load_images": False,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def resign_bundle(self, bundle: Path, manifest: dict[str, object]) -> None:
        manifest_bytes = release_bundle.canonical_json(manifest)
        (bundle / "release-manifest.json").write_bytes(manifest_bytes)
        (bundle / "release-manifest.sig").write_bytes(
            release_bundle.sign_value(self.private_key, manifest_bytes)
        )
        release_bundle.write_checksums(bundle)

    def test_valid_bundle_is_verified(self) -> None:
        bundle = self.make_bundle()

        result = release_bundle.verify_bundle(self.arguments(bundle))

        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["image_count"], 4)
        self.assertEqual(result["platform"], "linux/amd64")
        self.assertEqual(result["license_summary"]["blocked"], 0)

    def test_tampered_archive_is_rejected(self) -> None:
        bundle = self.make_bundle()
        with (bundle / "images/backend.oci.tar").open("ab") as target:
            target.write(b"tamper")

        with self.assertRaisesRegex(release_bundle.BundleError, "Checksum"):
            release_bundle.verify_bundle(self.arguments(bundle))

    def test_missing_secret_audit_report_is_rejected(self) -> None:
        bundle = self.make_bundle()
        manifest = release_bundle.load_json(bundle / "release-manifest.json")
        del manifest["secret_audit"]
        self.resign_bundle(bundle, manifest)

        with self.assertRaisesRegex(release_bundle.BundleError, "Secret audit report"):
            release_bundle.verify_bundle(self.arguments(bundle))

    def test_signed_clean_report_cannot_hide_secret_in_image(self) -> None:
        bundle = self.make_bundle()
        archive = bundle / "images/backend.oci.tar"
        self.write_docker_archive(
            archive,
            {"app/private.pem": b"-----BEGIN " + b"PRIVATE KEY-----\nsecret\n"},
        )
        manifest = release_bundle.load_json(bundle / "release-manifest.json")
        manifest["images"][0]["archive"] = self.descriptor(
            bundle,
            "images/backend.oci.tar",
        )
        self.resign_bundle(bundle, manifest)

        with self.assertRaisesRegex(
            release_bundle.BundleError,
            "Plaintext secret audit failed",
        ):
            release_bundle.verify_bundle(self.arguments(bundle))
    def test_file_missing_from_checksum_inventory_is_rejected(self) -> None:
        bundle = self.make_bundle()
        (bundle / "unexpected.txt").write_text("unexpected", encoding="utf-8")

        with self.assertRaisesRegex(release_bundle.BundleError, "inventory mismatch"):
            release_bundle.verify_bundle(self.arguments(bundle))

    def test_unsafe_checksum_path_is_rejected(self) -> None:
        bundle = self.make_bundle()
        with (bundle / "checksums.txt").open("a", encoding="utf-8") as target:
            target.write(f"{'0' * 64}  ../escape\n")

        with self.assertRaisesRegex(release_bundle.BundleError, "Unsafe bundle path"):
            release_bundle.verify_bundle(self.arguments(bundle))

    def test_wrong_public_key_is_rejected(self) -> None:
        bundle = self.make_bundle()
        other_private = self.root / "other-private.pem"
        other_public = self.root / "other-public.pem"
        release_bundle.generate_keypair(other_private, other_public)

        with self.assertRaisesRegex(release_bundle.BundleError, "signature is invalid"):
            release_bundle.verify_bundle(
                self.arguments(bundle, public_key=str(other_public))
            )

    def test_blocked_license_is_rejected_even_when_signed(self) -> None:
        bundle = self.make_bundle(
            component_license="AGPL-3.0",
            component_status="blocked",
        )

        with self.assertRaisesRegex(release_bundle.BundleError, "blocked license"):
            release_bundle.verify_bundle(self.arguments(bundle))

    def test_incorrect_license_classification_is_rejected(self) -> None:
        bundle = self.make_bundle(
            component_license="AGPL-3.0",
            component_status="allowed",
        )

        with self.assertRaisesRegex(
            release_bundle.BundleError, "classification mismatch"
        ):
            release_bundle.verify_bundle(self.arguments(bundle))

    def test_independent_policy_digest_is_enforced(self) -> None:
        bundle = self.make_bundle()

        with self.assertRaisesRegex(release_bundle.BundleError, "approved digest"):
            release_bundle.verify_bundle(
                self.arguments(bundle, expected_policy_sha256="0" * 64)
            )

    def test_expected_release_is_enforced(self) -> None:
        bundle = self.make_bundle()

        with self.assertRaisesRegex(release_bundle.BundleError, "Expected release"):
            release_bundle.verify_bundle(
                self.arguments(bundle, expected_release="9.9.9")
            )

    def test_supported_upgrade_transition_is_verified(self) -> None:
        bundle = self.make_bundle()

        result = release_bundle.verify_bundle(
            self.arguments(
                bundle,
                installed_release="1.2.2",
                installed_schema="0020_previous",
            )
        )

        self.assertEqual(result["database_schema_revision"], "0021_logistics_contacts")
        self.assertEqual(result["transition"]["rollback_mode"], "alembic-downgrade")

    def test_incompatible_installed_release_is_rejected(self) -> None:
        bundle = self.make_bundle()

        with self.assertRaisesRegex(release_bundle.BundleError, "incompatible"):
            release_bundle.verify_bundle(
                self.arguments(
                    bundle,
                    installed_release="1.2.1",
                    installed_schema="0020_previous",
                    load_images=True,
                )
            )

    def test_incompatible_installed_schema_is_rejected(self) -> None:
        bundle = self.make_bundle()

        with self.assertRaisesRegex(release_bundle.BundleError, "incompatible"):
            release_bundle.verify_bundle(
                self.arguments(
                    bundle,
                    installed_release="1.2.2",
                    installed_schema="0019_older",
                )
            )

    def test_installed_transition_arguments_must_be_paired(self) -> None:
        bundle = self.make_bundle()

        with self.assertRaisesRegex(release_bundle.BundleError, "supplied together"):
            release_bundle.verify_bundle(
                self.arguments(bundle, installed_release="1.2.2")
            )

    def test_unsupported_manifest_version_is_rejected(self) -> None:
        bundle = self.make_bundle()
        manifest = release_bundle.load_json(bundle / "release-manifest.json")
        manifest["version"] = "3"
        self.resign_bundle(bundle, manifest)

        with self.assertRaisesRegex(release_bundle.BundleError, "Unsupported"):
            release_bundle.verify_bundle(self.arguments(bundle))

    def test_missing_compatibility_contract_is_rejected(self) -> None:
        bundle = self.make_bundle()
        manifest = release_bundle.load_json(bundle / "release-manifest.json")
        del manifest["compatibility"]
        self.resign_bundle(bundle, manifest)

        with self.assertRaisesRegex(release_bundle.BundleError, "contract is missing"):
            release_bundle.verify_bundle(self.arguments(bundle))

    def test_unsupported_rollback_contract_is_rejected(self) -> None:
        bundle = self.make_bundle()
        manifest = release_bundle.load_json(bundle / "release-manifest.json")
        manifest["compatibility"]["supported_upgrades"][0]["rollback_mode"] = "restore"
        self.resign_bundle(bundle, manifest)

        with self.assertRaisesRegex(release_bundle.BundleError, "transition is invalid"):
            release_bundle.verify_bundle(self.arguments(bundle))

    def test_missing_platform_contract_is_rejected(self) -> None:
        bundle = self.make_bundle()
        manifest = release_bundle.load_json(bundle / "release-manifest.json")
        del manifest["platform"]
        self.resign_bundle(bundle, manifest)

        with self.assertRaisesRegex(release_bundle.BundleError, "platform contract"):
            release_bundle.verify_bundle(self.arguments(bundle))

    def test_mixed_image_architecture_is_rejected(self) -> None:
        bundle = self.make_bundle()
        manifest = release_bundle.load_json(bundle / "release-manifest.json")
        manifest["images"][0]["architecture"] = "arm64"
        self.resign_bundle(bundle, manifest)

        with self.assertRaisesRegex(release_bundle.BundleError, "image platforms"):
            release_bundle.verify_bundle(self.arguments(bundle))

    def test_arm64_must_be_qualified_or_explicitly_excluded(self) -> None:
        bundle = self.make_bundle()
        manifest = release_bundle.load_json(bundle / "release-manifest.json")
        manifest["platform"]["excluded_platforms"] = []
        self.resign_bundle(bundle, manifest)

        with self.assertRaisesRegex(release_bundle.BundleError, "amd64 and arm64"):
            release_bundle.verify_bundle(self.arguments(bundle))

    def test_expected_platform_is_enforced(self) -> None:
        bundle = self.make_bundle()

        with self.assertRaisesRegex(release_bundle.BundleError, "Expected platform"):
            release_bundle.verify_bundle(
                self.arguments(bundle, expected_platform="linux/arm64")
            )

    def test_load_rejects_wrong_docker_host_before_import(self) -> None:
        bundle = self.make_bundle()

        with (
            patch.object(
                release_bundle,
                "docker_host_platform",
                return_value="linux/arm64",
            ),
            self.assertRaisesRegex(release_bundle.BundleError, "Docker host platform"),
        ):
            release_bundle.verify_bundle(self.arguments(bundle, load_images=True))

    def test_builder_rejects_mixed_image_platforms(self) -> None:
        images = {
            role: {"os": "linux", "architecture": "amd64"}
            for role in release_bundle.REQUIRED_IMAGE_ROLES
        }
        images["frontend"]["architecture"] = "arm64"

        with self.assertRaisesRegex(release_bundle.BundleError, "do not all match"):
            release_bundle.build_platform_contract("linux/amd64", images)
    def test_create_bundle_emits_platform_v2(self) -> None:
        source_root = self.root / "source"
        source_root.mkdir()
        output = self.root / "created-release"
        policy_path = self.root / "policy.json"
        policy_path.write_text("{}", encoding="utf-8")
        image = {
            "id": f"sha256:{'1' * 64}",
            "repo_digests": [],
            "architecture": "amd64",
            "os": "linux",
            "layers": [],
        }

        def fake_run(command: list[str], **_: object) -> bytes:
            if command[:3] == ["docker", "image", "save"]:
                self.write_docker_archive(
                    Path(command[command.index("--output") + 1]),
                    {"app/content.txt": b"archive"},
                )
                return b""
            raise AssertionError(f"Unexpected command: {command}")

        arguments = SimpleNamespace(
            release="create-platform-test",
            root=str(source_root),
            output=str(output),
            private_key=str(self.private_key),
            license_policy=str(policy_path),
            allow_dirty=True,
            backend_image="backend:test",
            frontend_image="frontend:test",
            gateway_image="gateway:test",
            postgres_image="postgres:test",
            frontend_audit_image="frontend-audit:test",
            qualified_platform="linux/amd64",
            upgrade_from=["1.2.2@0036_previous"],
        )
        with (
            patch.object(
                release_bundle,
                "load_policy",
                return_value={
                    "format": release_bundle.LICENSE_POLICY_FORMAT,
                    "version": "1",
                    "allowed": ["MIT"],
                    "denied": [],
                },
            ),
            patch.object(
                release_bundle,
                "source_metadata",
                return_value={"commit": "0" * 40, "dirty": True, "dirty_entries": []},
            ),
            patch.object(release_bundle, "git_source_paths", return_value=[]),
            patch.object(release_bundle, "inspect_image", return_value=image),
            patch.object(release_bundle, "copy_node_payload", return_value=[]),
            patch.object(
                release_bundle,
                "build_sbom",
                return_value={
                    "bomFormat": "CycloneDX",
                    "specVersion": "1.6",
                    "components": [],
                },
            ),
            patch.object(
                release_bundle,
                "build_license_report",
                return_value={
                    "summary": {
                        "allowed": 0,
                        "blocked": 0,
                        "review_required": 0,
                    },
                    "components": [],
                },
            ),
            patch.object(release_bundle, "schema_revision", return_value="0037"),
            patch.object(release_bundle, "protocol_metadata", return_value={}),
            patch.object(release_bundle, "run_checked", side_effect=fake_run),
        ):
            manifest = release_bundle.create_bundle(arguments)

        self.assertEqual(manifest["format"], release_bundle.BUNDLE_FORMAT)
        self.assertEqual(manifest["version"], "2")
        self.assertEqual(
            manifest["platform"]["qualified_platforms"],
            ["linux/amd64"],
        )
        self.assertEqual(manifest["secret_audit"]["status"], "PASSED")
        self.assertEqual(
            manifest["compatibility"]["supported_upgrades"][0]["source_release"],
            "1.2.2",
        )

    def test_arm64_bundle_is_valid_when_all_images_are_arm64(self) -> None:
        bundle = self.make_bundle(platform="linux/arm64")

        result = release_bundle.verify_bundle(
            self.arguments(bundle, expected_platform="linux/arm64")
        )

        self.assertEqual(result["platform"], "linux/arm64")

if __name__ == "__main__":
    unittest.main()