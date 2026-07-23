from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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

    def make_bundle(
        self,
        *,
        release: str = "1.2.3",
        component_license: str = "MIT",
        component_status: str = "allowed",
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
        for role in release_bundle.REQUIRED_IMAGE_ROLES:
            image_id = f"sha256:{release_bundle.sha256_bytes(role.encode())}"
            reference = (
                "postgres:18-alpine"
                if role == "postgres"
                else f"cooperative-clearing/{role}:{release}"
            )
            archive_relative = f"images/{role}.oci.tar"
            release_bundle.safe_path(bundle, archive_relative).write_bytes(
                f"archive:{role}".encode()
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
                    "architecture": "amd64",
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

        manifest = {
            "format": release_bundle.BUNDLE_FORMAT,
            "version": "1",
            "release": release,
            "created_at": "2026-07-22T00:00:00+00:00",
            "source": {"commit": "0" * 40, "dirty": False, "dirty_entries": []},
            "compatibility": {
                "database_schema_revision": "0018_inter_node_clearing",
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
            "load_images": False,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_valid_bundle_is_verified(self) -> None:
        bundle = self.make_bundle()

        result = release_bundle.verify_bundle(self.arguments(bundle))

        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["image_count"], 4)
        self.assertEqual(result["license_summary"]["blocked"], 0)

    def test_tampered_archive_is_rejected(self) -> None:
        bundle = self.make_bundle()
        with (bundle / "images/backend.oci.tar").open("ab") as target:
            target.write(b"tamper")

        with self.assertRaisesRegex(release_bundle.BundleError, "Checksum"):
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


if __name__ == "__main__":
    unittest.main()