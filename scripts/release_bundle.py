#!/usr/bin/env python3
"""Build and verify signed offline release bundles.

The verifier deliberately has no dependency on the application code. It can
run from a checked-out node directory or directly from removable media.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

BUNDLE_FORMAT = "cooperative-clearing-release-v1"
LICENSE_REPORT_FORMAT = "cooperative-clearing-license-report-v1"
LICENSE_POLICY_FORMAT = "cooperative-clearing-license-policy-v1"
SIGNATURE_ALGORITHM = "Ed25519"
RELEASE_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

REQUIRED_IMAGE_ROLES = ("backend", "frontend", "gateway", "postgres")
NODE_PAYLOAD = (
    ".env.example",
    "LICENSE",
    "README.md",
    "compose.yaml",
    "start.bat",
    "start.sh",
    "backend/openapi.json",
    "frontend/openapi.json",
    "infra/contracts/openapi-0.1.0.json",
    "docs/deployment.md",
    "docs/evidence_templates/production_readiness_decision.md",
    "docs/pilot_runbook.md",
    "docs/production_readiness.md",
    "docs/recovery_runbook.md",
    "docs/release_runbook.md",
    "docs/security.md",
    "docs/testing_strategy.md",
    "docs/threat_model.md",
    "docs/implemented_slice_17.md",
    "docs/implemented_slice_28.md",
    "docs/implemented_slice_29.md",
    "docs/observability.md",
    "infra/postgres/init-runtime-role.sh",
    "scripts/backup-node.ps1",
    "scripts/backup-node.sh",
    "scripts/bootstrap-node.ps1",
    "scripts/bootstrap-node.sh",
    "scripts/collect-production-evidence.ps1",
    "scripts/collect-production-evidence.sh",
    "scripts/diagnostic_bundle.py",
    "scripts/openapi_compat.py",
    "scripts/operational_status.py",
    "scripts/release_bundle.py",
    "scripts/runtime_environment.py",
    "scripts/restore-node.ps1",
    "scripts/restore-node.sh",
    "scripts/rollback-node.ps1",
    "scripts/rollback-node.sh",
    "scripts/update-node.ps1",
    "scripts/update-node.sh",
    "scripts/verify-backup.ps1",
    "scripts/verify-backup.sh",
    "scripts/verify-stack.ps1",
    "scripts/verify-stack.sh",
)


class BundleError(RuntimeError):
    """A release bundle violates its signed contract."""


def fail(message: str) -> NoReturn:
    raise BundleError(message)


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"Cannot read JSON file {path}: {exc}")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        fail("Bundle path must be a non-empty string")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
        fail(f"Unsafe bundle path: {relative!r}")
    candidate = root.joinpath(*pure.parts)
    try:
        candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        fail(f"Bundle path escapes its root: {relative!r}")
    return candidate


def descriptor(root: Path, relative: str) -> dict[str, Any]:
    path = safe_path(root, relative)
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def run_checked(
    command: list[str],
    *,
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
) -> bytes:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            input=input_bytes,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        fail(f"Cannot execute {command[0]}: {exc}")
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        fail(f"Command failed ({' '.join(command)}): {error or result.returncode}")
    return result.stdout


def cryptography_backend() -> tuple[Any, Any, Any] | None:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError:
        return None
    return serialization, Ed25519PrivateKey, Ed25519PublicKey


def enforce_private_key_permissions(path: Path) -> None:
    if os.name == "nt":
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        fail(f"Private key permissions are too broad ({mode:o}); expected 0600 or stricter")


def public_key_der(public_key_path: Path) -> bytes:
    backend = cryptography_backend()
    if backend:
        serialization, _, _ = backend
        try:
            public_key = serialization.load_pem_public_key(public_key_path.read_bytes())
            return public_key.public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        except (OSError, ValueError, TypeError) as exc:
            fail(f"Cannot load Ed25519 public key: {exc}")
    return run_checked(
        ["openssl", "pkey", "-pubin", "-in", str(public_key_path), "-outform", "DER"]
    )


def public_key_fingerprint(public_key_path: Path) -> str:
    return f"sha256:{sha256_bytes(public_key_der(public_key_path))}"


def public_key_from_private(private_key_path: Path) -> bytes:
    backend = cryptography_backend()
    if backend:
        serialization, _, _ = backend
        try:
            private_key = serialization.load_pem_private_key(
                private_key_path.read_bytes(), password=None
            )
            return private_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        except (OSError, ValueError, TypeError) as exc:
            fail(f"Cannot load Ed25519 private key: {exc}")
    return run_checked(
        ["openssl", "pkey", "-in", str(private_key_path), "-pubout"]
    )


def sign_value(private_key_path: Path, value: bytes) -> bytes:
    enforce_private_key_permissions(private_key_path)
    backend = cryptography_backend()
    if backend:
        serialization, _, _ = backend
        try:
            private_key = serialization.load_pem_private_key(
                private_key_path.read_bytes(), password=None
            )
            return private_key.sign(value)
        except (OSError, ValueError, TypeError) as exc:
            fail(f"Cannot sign release manifest: {exc}")
    with tempfile.TemporaryDirectory(prefix="coop-release-sign-") as temp:
        message = Path(temp, "manifest")
        message.write_bytes(value)
        return run_checked(
            [
                "openssl",
                "pkeyutl",
                "-sign",
                "-rawin",
                "-inkey",
                str(private_key_path),
                "-in",
                str(message),
            ]
        )


def verify_signature(public_key_path: Path, value: bytes, signature: bytes) -> None:
    backend = cryptography_backend()
    if backend:
        serialization, _, _ = backend
        try:
            public_key = serialization.load_pem_public_key(public_key_path.read_bytes())
            public_key.verify(signature, value)
            return
        except Exception as exc:
            fail(f"Release manifest signature is invalid: {exc}")
    with tempfile.TemporaryDirectory(prefix="coop-release-verify-") as temp:
        message_path = Path(temp, "manifest")
        signature_path = Path(temp, "signature")
        message_path.write_bytes(value)
        signature_path.write_bytes(signature)
        run_checked(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-rawin",
                "-inkey",
                str(public_key_path),
                "-sigfile",
                str(signature_path),
                "-in",
                str(message_path),
            ]
        )


def generate_keypair(private_key_path: Path, public_key_path: Path) -> None:
    if private_key_path.exists() or public_key_path.exists():
        fail("Refusing to overwrite an existing signing key")
    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    public_key_path.parent.mkdir(parents=True, exist_ok=True)
    backend = cryptography_backend()
    if backend:
        serialization, Ed25519PrivateKey, _ = backend
        private_key = Ed25519PrivateKey.generate()
        private_key_path.write_bytes(
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        public_key_path.write_bytes(
            private_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
    else:
        run_checked(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "Ed25519",
                "-out",
                str(private_key_path),
            ]
        )
        public_key_path.write_bytes(public_key_from_private(private_key_path))
    if os.name != "nt":
        private_key_path.chmod(0o600)
        public_key_path.chmod(0o644)


def git_value(root: Path, *arguments: str) -> str:
    return run_checked(["git", *arguments], cwd=root).decode("utf-8").strip()


def source_metadata(root: Path, allow_dirty: bool) -> dict[str, Any]:
    commit = git_value(root, "rev-parse", "HEAD")
    dirty_output = git_value(root, "status", "--short", "--untracked-files=all")
    dirty_entries = [line for line in dirty_output.splitlines() if line]
    if dirty_entries and not allow_dirty:
        fail("Source tree is dirty; commit the release or pass --allow-dirty for a test build")
    return {
        "commit": commit,
        "dirty": bool(dirty_entries),
        "dirty_entries": dirty_entries,
    }


def read_assignment(path: Path, name: str) -> str:
    pattern = re.compile(rf"^{re.escape(name)}\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)
    match = pattern.search(path.read_text(encoding="utf-8"))
    if not match:
        fail(f"Cannot find {name} in {path}")
    return match.group(1)


def schema_revision(root: Path) -> str:
    revisions: list[tuple[str, str]] = []
    for path in (root / "backend" / "alembic" / "versions").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)", text, re.MULTILINE)
        if match:
            revisions.append((path.name, match.group(1)))
    if not revisions:
        fail("No database schema revision was found")
    return sorted(revisions)[-1][1]


def protocol_metadata(root: Path) -> dict[str, str]:
    peer = root / "backend/src/cooperative_clearing/modules/federation/domain/peer_protocol.py"
    sync = root / "backend/src/cooperative_clearing/modules/federation/application/sync.py"
    clearing = (
        root
        / "backend/src/cooperative_clearing/modules/federation/domain/federated_clearing.py"
    )
    return {
        "peer": read_assignment(peer, "PEER_PROTOCOL_VERSION"),
        "sync": read_assignment(sync, "SYNC_PROTOCOL_VERSION"),
        "federated_clearing": read_assignment(clearing, "FEDERATED_ALGORITHM_VERSION"),
    }


def inspect_image(reference: str) -> dict[str, Any]:
    raw = run_checked(["docker", "image", "inspect", reference])
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"Docker returned invalid metadata for {reference}: {exc}")
    if not isinstance(values, list) or len(values) != 1:
        fail(f"Expected exactly one Docker image for {reference}")
    value = values[0]
    image_id = value.get("Id")
    if not isinstance(image_id, str) or not IMAGE_ID_PATTERN.fullmatch(image_id):
        fail(f"Docker image {reference} has an invalid content ID")
    rootfs = value.get("RootFS") if isinstance(value.get("RootFS"), dict) else {}
    layers = rootfs.get("Layers") if isinstance(rootfs.get("Layers"), list) else []
    return {
        "id": image_id,
        "repo_digests": sorted(
            item for item in value.get("RepoDigests", []) if isinstance(item, str)
        ),
        "architecture": value.get("Architecture", "unknown"),
        "os": value.get("Os", "unknown"),
        "layers": [item for item in layers if isinstance(item, str)],
    }


def parse_control_records(value: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for block in re.split(r"\n\s*\n", value.strip()):
        record: dict[str, str] = {}
        current = ""
        for line in block.splitlines():
            if line[:1].isspace() and current:
                record[current] = f"{record[current]} {line.strip()}".strip()
                continue
            if ":" not in line:
                continue
            key, field_value = line.split(":", 1)
            current = key.strip()
            record[current] = field_value.strip()
        if record:
            records.append(record)
    return records


def collect_os_components(reference: str) -> list[dict[str, Any]]:
    script = (
        "if [ -f /lib/apk/db/installed ]; then "
        "printf '__COOP_APK__\\n'; cat /lib/apk/db/installed; "
        "elif [ -f /var/lib/dpkg/status ]; then "
        "printf '__COOP_DPKG__\\n'; cat /var/lib/dpkg/status; "
        "else printf '__COOP_UNKNOWN__\\n'; fi"
    )
    raw = run_checked(
        ["docker", "run", "--rm", "--entrypoint", "sh", reference, "-c", script]
    ).decode("utf-8", errors="replace")
    marker, _, payload = raw.partition("\n")
    if marker not in {"__COOP_APK__", "__COOP_DPKG__", "__COOP_UNKNOWN__"}:
        fail(f"Cannot identify package database in image {reference}")
    if marker == "__COOP_UNKNOWN__":
        return []
    components: list[dict[str, Any]] = []
    for record in parse_control_records(payload):
        if marker == "__COOP_APK__":
            name = record.get("P", "")
            version = record.get("V", "")
            license_name = record.get("L", "UNKNOWN")
            package_type = "apk"
        else:
            name = record.get("Package", "")
            version = record.get("Version", "")
            license_name = "UNKNOWN"
            package_type = "deb"
        if not name or not version:
            continue
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "licenses": [{"license": {"id": license_name}}],
                "properties": [
                    {"name": "cooperative-clearing:package-type", "value": package_type}
                ],
            }
        )
    return components


def collect_python_components(reference: str) -> list[dict[str, Any]]:
    program = (
        "import importlib.metadata as m,json;"
        "rows=[];"
        "\nfor d in m.distributions():"
        "\n n=d.metadata.get('Name');"
        "\n v=d.version;"
        "\n l=d.metadata.get('License-Expression') or d.metadata.get('License') or 'UNKNOWN';"
        "\n rows.append({'name':n,'version':v,'license':l}) if n else None;"
        "\nprint(json.dumps(rows,sort_keys=True))"
    )
    raw = run_checked(
        ["docker", "run", "--rm", "--entrypoint", "python", reference, "-c", program]
    )
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"Cannot read Python package inventory from {reference}: {exc}")
    return [
        {
            "type": "library",
            "name": row["name"],
            "version": row["version"],
            "licenses": [{"license": {"id": row.get("license") or "UNKNOWN"}}],
            "properties": [
                {"name": "cooperative-clearing:package-type", "value": "python"}
            ],
        }
        for row in rows
        if isinstance(row, dict) and row.get("name") and row.get("version")
    ]


def collect_frontend_components(audit_image: str) -> list[dict[str, Any]]:
    raw = run_checked(
        [
            "docker",
            "run",
            "--rm",
            audit_image,
            "sh",
            "-lc",
            "pnpm licenses list --json --prod",
        ]
    )
    try:
        inventory = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"Cannot read frontend license inventory from {audit_image}: {exc}")
    if not isinstance(inventory, dict):
        fail("Frontend license inventory must be an object")
    components: list[dict[str, Any]] = []
    for license_group, packages in inventory.items():
        if not isinstance(packages, list):
            continue
        for package in packages:
            if not isinstance(package, dict) or not package.get("name"):
                continue
            versions = package.get("versions")
            if not isinstance(versions, list) or not versions:
                versions = ["unknown"]
            for version in versions:
                components.append(
                    {
                        "type": "library",
                        "name": str(package["name"]),
                        "version": str(version),
                        "licenses": [
                            {
                                "license": {
                                    "id": str(package.get("license") or license_group)
                                }
                            }
                        ],
                        "properties": [
                            {
                                "name": "cooperative-clearing:package-type",
                                "value": "node",
                            }
                        ],
                    }
                )
    return components


def component_key(component: dict[str, Any]) -> tuple[str, str, str]:
    package_type = ""
    for item in component.get("properties", []):
        if item.get("name") == "cooperative-clearing:package-type":
            package_type = str(item.get("value", ""))
    return (
        package_type,
        str(component.get("name", "")).lower(),
        str(component.get("version", "")),
    )


def build_sbom(
    role: str,
    reference: str,
    image: dict[str, Any],
    frontend_audit_image: str,
) -> dict[str, Any]:
    components = collect_os_components(reference)
    if role == "backend":
        components.extend(collect_python_components(reference))
    if role == "frontend":
        components.extend(collect_frontend_components(frontend_audit_image))
    deduplicated = {component_key(component): component for component in components}
    sorted_components = [
        deduplicated[key] for key in sorted(deduplicated, key=lambda item: tuple(item))
    ]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "container",
                "name": reference,
                "version": image["id"],
            },
            "properties": [
                {"name": "cooperative-clearing:image-role", "value": role},
                {"name": "cooperative-clearing:image-id", "value": image["id"]},
                {
                    "name": "cooperative-clearing:architecture",
                    "value": str(image["architecture"]),
                },
                {"name": "cooperative-clearing:os", "value": str(image["os"])},
            ],
        },
        "components": sorted_components,
    }


def license_id(component: dict[str, Any]) -> str:
    licenses = component.get("licenses")
    if not isinstance(licenses, list) or not licenses:
        return "UNKNOWN"
    first = licenses[0]
    if not isinstance(first, dict):
        return "UNKNOWN"
    license_value = first.get("license")
    if not isinstance(license_value, dict):
        return "UNKNOWN"
    return str(license_value.get("id") or license_value.get("name") or "UNKNOWN")


def license_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", value)
        if token.upper() not in {"AND", "OR", "WITH"}
    }


def classify_license(value: str, policy: dict[str, Any]) -> str:
    allowed = set(policy["allowed"])
    denied = set(policy["denied"])
    tokens = license_tokens(value)
    if value in denied or tokens & denied:
        return "blocked"
    if value in allowed or (tokens and tokens <= allowed):
        return "allowed"
    return "review_required"


def build_license_report(
    role: str, reference: str, sbom: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    entries = []
    counts = {"allowed": 0, "blocked": 0, "review_required": 0}
    for component in sbom["components"]:
        value = license_id(component)
        status = classify_license(value, policy)
        counts[status] += 1
        entries.append(
            {
                "component": component["name"],
                "version": component["version"],
                "license": value,
                "status": status,
            }
        )
    entries.sort(key=lambda item: (item["status"], item["component"].lower(), item["version"]))
    return {
        "format": LICENSE_REPORT_FORMAT,
        "version": "1",
        "image_role": role,
        "image": reference,
        "policy_sha256": policy["_sha256"],
        "summary": counts,
        "components": entries,
    }


def load_policy(path: Path) -> dict[str, Any]:
    value = load_json(path)
    if not isinstance(value, dict) or value.get("format") != LICENSE_POLICY_FORMAT:
        fail(f"Invalid license policy format in {path}")
    for name in ("allowed", "denied"):
        if not isinstance(value.get(name), list) or not all(
            isinstance(item, str) and item for item in value[name]
        ):
            fail(f"License policy field {name} must be a string list")
    value["_sha256"] = sha256_file(path)
    return value


def copy_node_payload(root: Path, bundle: Path) -> list[dict[str, Any]]:
    rows = []
    for source_relative in NODE_PAYLOAD:
        source = safe_path(root, source_relative)
        if not source.is_file():
            fail(f"Required node payload is missing: {source_relative}")
        target_relative = f"node/{source_relative}"
        target = safe_path(bundle, target_relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        rows.append(descriptor(bundle, target_relative))
    return rows


def write_checksums(bundle: Path) -> None:
    rows = []
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink():
            fail(f"Refusing to checksum symlink {path}")
        if not path.is_file() or path.name == "checksums.txt":
            continue
        relative = path.relative_to(bundle).as_posix()
        rows.append(f"{sha256_file(path)}  {relative}")
    (bundle / "checksums.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")


def signing_fingerprint(private_key_path: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="coop-release-public-") as temp:
        public_key_path = Path(temp, "release-public.pem")
        public_key_path.write_bytes(public_key_from_private(private_key_path))
        return public_key_fingerprint(public_key_path)


def create_bundle(args: argparse.Namespace) -> dict[str, Any]:
    release = args.release
    if not RELEASE_PATTERN.fullmatch(release):
        fail("Invalid release identifier")
    root = Path(args.root).resolve()
    output = Path(args.output).resolve()
    private_key = Path(args.private_key).resolve()
    policy_path = Path(args.license_policy).resolve()
    if output.exists():
        fail(f"Output path already exists: {output}")
    if not private_key.is_file():
        fail(f"Signing key does not exist: {private_key}")
    if private_key.is_relative_to(root):
        fail("Release private key must be stored outside the source tree")
    policy = load_policy(policy_path)
    source = source_metadata(root, args.allow_dirty)

    references = {
        "backend": args.backend_image or f"cooperative-clearing/backend:{release}",
        "frontend": args.frontend_image or f"cooperative-clearing/frontend:{release}",
        "gateway": args.gateway_image or f"cooperative-clearing/gateway:{release}",
        "postgres": args.postgres_image,
    }
    images = {role: inspect_image(reference) for role, reference in references.items()}
    inspect_image(args.frontend_audit_image)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.building-{os.getpid()}")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir()
    try:
        (temporary / "images").mkdir()
        (temporary / "metadata" / "sbom").mkdir(parents=True)
        (temporary / "metadata" / "licenses").mkdir(parents=True)
        policy_relative = "metadata/license-policy.json"
        safe_path(temporary, policy_relative).write_bytes(
            canonical_json({key: value for key, value in policy.items() if key != "_sha256"})
        )
        policy_descriptor = descriptor(temporary, policy_relative)
        policy["_sha256"] = policy_descriptor["sha256"]
        node_payload = copy_node_payload(root, temporary)

        image_rows = []
        total_licenses = {"allowed": 0, "blocked": 0, "review_required": 0}
        for role in REQUIRED_IMAGE_ROLES:
            reference = references[role]
            image = images[role]
            archive_relative = f"images/{role}.oci.tar"
            archive_path = safe_path(temporary, archive_relative)
            run_checked(["docker", "image", "save", "--output", str(archive_path), reference])

            sbom = build_sbom(role, reference, image, args.frontend_audit_image)
            sbom_relative = f"metadata/sbom/{role}.cdx.json"
            safe_path(temporary, sbom_relative).write_bytes(canonical_json(sbom))

            report = build_license_report(role, reference, sbom, policy)
            report_relative = f"metadata/licenses/{role}.json"
            safe_path(temporary, report_relative).write_bytes(canonical_json(report))
            for status in total_licenses:
                total_licenses[status] += report["summary"][status]
            if report["summary"]["blocked"]:
                fail(f"Image {role} contains licenses blocked by release policy")

            image_rows.append(
                {
                    "role": role,
                    "reference": reference,
                    "image_id": image["id"],
                    "repo_digests": image["repo_digests"],
                    "architecture": image["architecture"],
                    "os": image["os"],
                    "layer_digests": image["layers"],
                    "archive_format": "docker-image-archive",
                    "archive": descriptor(temporary, archive_relative),
                    "sbom": {
                        **descriptor(temporary, sbom_relative),
                        "component_count": len(sbom["components"]),
                    },
                    "licenses": {
                        **descriptor(temporary, report_relative),
                        "summary": report["summary"],
                    },
                }
            )

        manifest = {
            "format": BUNDLE_FORMAT,
            "version": "1",
            "release": release,
            "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "source": source,
            "compatibility": {
                "database_schema_revision": schema_revision(root),
                "protocols": protocol_metadata(root),
            },
            "signature": {
                "algorithm": SIGNATURE_ALGORITHM,
                "public_key_fingerprint": signing_fingerprint(private_key),
                "encoding": "raw",
            },
            "license_policy": policy_descriptor,
            "license_summary": total_licenses,
            "images": image_rows,
            "node_payload": node_payload,
        }
        manifest_path = temporary / "release-manifest.json"
        manifest_bytes = canonical_json(manifest)
        manifest_path.write_bytes(manifest_bytes)
        signature = sign_value(private_key, manifest_bytes)
        if len(signature) != 64:
            fail(f"Unexpected Ed25519 signature length: {len(signature)}")
        (temporary / "release-manifest.sig").write_bytes(signature)
        write_checksums(temporary)
        temporary.rename(output)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return manifest


def scan_bundle_files(bundle: Path) -> set[str]:
    files: set[str] = set()
    for path in bundle.rglob("*"):
        relative = path.relative_to(bundle).as_posix()
        if path.is_symlink():
            fail(f"Bundle contains forbidden symlink: {relative}")
        if path.is_file():
            files.add(relative)
        elif not path.is_dir():
            fail(f"Bundle contains unsupported filesystem entry: {relative}")
    return files


def parse_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        fail(f"Cannot read checksum inventory: {exc}")
    for line_number, line in enumerate(lines, 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            fail(f"Invalid checksums.txt line {line_number}")
        digest, relative = match.groups()
        safe_path(path.parent, relative)
        if relative == "checksums.txt" or relative in checksums:
            fail(f"Invalid or duplicate checksum path: {relative}")
        checksums[relative] = digest
    if not checksums:
        fail("Checksum inventory is empty")
    return checksums


def verify_descriptor(bundle: Path, value: Any, label: str) -> str:
    if not isinstance(value, dict):
        fail(f"{label} descriptor must be an object")
    relative = value.get("path")
    digest = value.get("sha256")
    size = value.get("size")
    if not isinstance(relative, str):
        fail(f"{label} descriptor has no path")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        fail(f"{label} descriptor has an invalid SHA-256")
    if not isinstance(size, int) or size < 0:
        fail(f"{label} descriptor has an invalid size")
    path = safe_path(bundle, relative)
    if not path.is_file() or path.is_symlink():
        fail(f"{label} file is missing or unsafe: {relative}")
    if path.stat().st_size != size or sha256_file(path) != digest:
        fail(f"{label} file does not match signed metadata: {relative}")
    return relative


def validate_sbom(bundle: Path, image: dict[str, Any]) -> None:
    relative = verify_descriptor(bundle, image.get("sbom"), "SBOM")
    sbom = load_json(safe_path(bundle, relative))
    if (
        not isinstance(sbom, dict)
        or sbom.get("bomFormat") != "CycloneDX"
        or sbom.get("specVersion") != "1.6"
        or not isinstance(sbom.get("components"), list)
    ):
        fail(f"Invalid SBOM document: {relative}")
    expected_count = image["sbom"].get("component_count")
    if expected_count != len(sbom["components"]):
        fail(f"SBOM component count mismatch: {relative}")



def validate_policy(bundle: Path, value: Any, expected_digest: str | None) -> dict[str, Any]:
    relative = verify_descriptor(bundle, value, "License policy")
    digest = value["sha256"]
    if expected_digest and digest != expected_digest:
        fail(
            f"License policy digest {digest} does not match independently approved digest "
            f"{expected_digest}"
        )
    policy = load_policy(safe_path(bundle, relative))
    policy["_sha256"] = digest
    if set(policy["allowed"]) & set(policy["denied"]):
        fail("License policy contains the same identifier in allowed and denied lists")
    return policy


def validate_license_report(
    bundle: Path, image: dict[str, Any], policy: dict[str, Any]
) -> dict[str, int]:
    relative = verify_descriptor(bundle, image.get("licenses"), "License report")
    report = load_json(safe_path(bundle, relative))
    if (
        not isinstance(report, dict)
        or report.get("format") != LICENSE_REPORT_FORMAT
        or report.get("image_role") != image.get("role")
        or report.get("image") != image.get("reference")
        or report.get("policy_sha256") != policy["_sha256"]
    ):
        fail(f"Invalid license report: {relative}")
    summary = report.get("summary")
    expected = image["licenses"].get("summary")
    components = report.get("components")
    if not isinstance(summary, dict) or summary != expected:
        fail(f"License summary mismatch: {relative}")
    if not isinstance(components, list):
        fail(f"License component inventory is missing: {relative}")
    actual = {"allowed": 0, "blocked": 0, "review_required": 0}
    for entry in components:
        if not isinstance(entry, dict) or not isinstance(entry.get("license"), str):
            fail(f"Invalid license component entry: {relative}")
        status = classify_license(entry["license"], policy)
        if entry.get("status") != status:
            fail(f"License classification mismatch in {relative}")
        actual[status] += 1
    if summary != actual:
        fail(f"License counts do not match component inventory: {relative}")
    if actual["blocked"]:
        fail(f"Release contains a blocked license in {relative}")
    return actual


def verify_bundle(args: argparse.Namespace) -> dict[str, Any]:
    bundle = Path(args.bundle).resolve()
    public_key = Path(args.public_key).resolve()
    if not bundle.is_dir():
        fail(f"Bundle directory does not exist: {bundle}")
    if not public_key.is_file():
        fail(f"Release public key does not exist: {public_key}")

    actual_files = scan_bundle_files(bundle)
    required_top = {
        "release-manifest.json",
        "release-manifest.sig",
        "checksums.txt",
    }
    missing_top = required_top - actual_files
    if missing_top:
        fail(f"Bundle is missing required files: {', '.join(sorted(missing_top))}")

    checksums = parse_checksums(bundle / "checksums.txt")
    expected_files = actual_files - {"checksums.txt"}
    if set(checksums) != expected_files:
        missing = sorted(expected_files - set(checksums))
        extra = sorted(set(checksums) - expected_files)
        fail(f"Checksum inventory mismatch; missing={missing}, extra={extra}")
    for relative, expected_digest in sorted(checksums.items()):
        path = safe_path(bundle, relative)
        if path.is_symlink() or sha256_file(path) != expected_digest:
            fail(f"Checksum verification failed: {relative}")

    manifest_path = bundle / "release-manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    signature = (bundle / "release-manifest.sig").read_bytes()
    if len(signature) != 64:
        fail("Release signature must be a raw 64-byte Ed25519 signature")
    verify_signature(public_key, manifest_bytes, signature)

    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("format") != BUNDLE_FORMAT:
        fail("Unsupported release manifest format")
    release = manifest.get("release")
    if not isinstance(release, str) or not RELEASE_PATTERN.fullmatch(release):
        fail("Signed manifest contains an invalid release identifier")
    if args.expected_release and release != args.expected_release:
        fail(f"Expected release {args.expected_release}, bundle contains {release}")

    signature_metadata = manifest.get("signature")
    if (
        not isinstance(signature_metadata, dict)
        or signature_metadata.get("algorithm") != SIGNATURE_ALGORITHM
        or signature_metadata.get("encoding") != "raw"
    ):
        fail("Signed manifest has unsupported signature metadata")
    actual_fingerprint = public_key_fingerprint(public_key)
    if signature_metadata.get("public_key_fingerprint") != actual_fingerprint:
        fail("Release key fingerprint does not match signed manifest")

    policy = validate_policy(
        bundle,
        manifest.get("license_policy"),
        args.expected_policy_sha256,
    )

    payload = manifest.get("node_payload")
    if not isinstance(payload, list):
        fail("Signed node payload inventory is missing")
    payload_paths = []
    for row in payload:
        payload_paths.append(verify_descriptor(bundle, row, "Node payload"))
    expected_payload = {f"node/{relative}" for relative in NODE_PAYLOAD}
    if len(payload_paths) != len(set(payload_paths)) or set(payload_paths) != expected_payload:
        fail("Signed node payload differs from the installation contract")

    image_rows = manifest.get("images")
    if not isinstance(image_rows, list) or len(image_rows) != len(REQUIRED_IMAGE_ROLES):
        fail("Signed image inventory is incomplete")
    roles = [row.get("role") for row in image_rows if isinstance(row, dict)]
    if sorted(roles) != sorted(REQUIRED_IMAGE_ROLES) or len(roles) != len(set(roles)):
        fail("Signed image roles are invalid")

    aggregate = {"allowed": 0, "blocked": 0, "review_required": 0}
    archive_rows: list[tuple[str, str, str, str]] = []
    for image in image_rows:
        role = image["role"]
        reference = image.get("reference")
        image_id = image.get("image_id")
        if not isinstance(reference, str) or not reference:
            fail(f"Image {role} has no reference")
        if not isinstance(image_id, str) or not IMAGE_ID_PATTERN.fullmatch(image_id):
            fail(f"Image {role} has an invalid content ID")
        if image.get("archive_format") != "docker-image-archive":
            fail(f"Image {role} has an unsupported archive format")
        archive_relative = verify_descriptor(bundle, image.get("archive"), "Image archive")
        if archive_relative != f"images/{role}.oci.tar":
            fail(f"Image {role} archive path violates the release contract")
        validate_sbom(bundle, image)
        counts = validate_license_report(bundle, image, policy)
        for status in aggregate:
            aggregate[status] += counts[status]
        archive_rows.append(
            (role, reference, image_id, archive_relative)
        )

    if manifest.get("license_summary") != aggregate:
        fail("Aggregate license summary does not match image reports")

    if args.load_images:
        for role, reference, expected_id, archive_relative in sorted(archive_rows):
            archive_path = safe_path(bundle, archive_relative)
            expected_archive_hash = checksums[archive_relative]
            if archive_path.is_symlink() or sha256_file(archive_path) != expected_archive_hash:
                fail(f"Image archive changed after verification: {archive_relative}")
            run_checked(["docker", "image", "load", "--input", str(archive_path)])
            loaded = inspect_image(reference)
            if loaded["id"] != expected_id:
                fail(
                    f"Loaded image {role} has ID {loaded['id']}, expected {expected_id}"
                )

    return {
        "status": "VERIFIED",
        "release": release,
        "public_key_fingerprint": actual_fingerprint,
        "image_count": len(image_rows),
        "node_payload_count": len(payload_paths),
        "license_summary": aggregate,
        "images_loaded": bool(args.load_images),
    }


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    keygen = subparsers.add_parser(
        "generate-keypair", help="create an Ed25519 release signing keypair"
    )
    keygen.add_argument("--private-key", required=True)
    keygen.add_argument("--public-key", required=True)

    create = subparsers.add_parser(
        "create", help="build and sign an offline release bundle"
    )
    create.add_argument("--release", required=True)
    create.add_argument("--output", required=True)
    create.add_argument("--private-key", required=True)
    create.add_argument("--root", default=str(root))
    create.add_argument(
        "--license-policy",
        default=str(root / "infra" / "release" / "license-policy.json"),
    )
    create.add_argument("--backend-image")
    create.add_argument("--frontend-image")
    create.add_argument("--gateway-image")
    create.add_argument("--postgres-image", default="postgres:18-alpine")
    create.add_argument(
        "--frontend-audit-image",
        default="cooperative-clearing/frontend-test:local",
    )
    create.add_argument("--allow-dirty", action="store_true")

    verify = subparsers.add_parser(
        "verify", help="verify a signed bundle before installation"
    )
    verify.add_argument("--bundle", required=True)
    verify.add_argument("--public-key", required=True)
    verify.add_argument("--expected-release")
    verify.add_argument("--expected-policy-sha256")
    verify.add_argument("--load-images", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "generate-keypair":
            generate_keypair(Path(args.private_key).resolve(), Path(args.public_key).resolve())
            result = {
                "status": "CREATED",
                "public_key_fingerprint": public_key_fingerprint(
                    Path(args.public_key).resolve()
                ),
            }
        elif args.command == "create":
            manifest = create_bundle(args)
            result = {
                "status": "CREATED",
                "release": manifest["release"],
                "output": str(Path(args.output).resolve()),
                "public_key_fingerprint": manifest["signature"][
                    "public_key_fingerprint"
                ],
                "image_count": len(manifest["images"]),
                "license_summary": manifest["license_summary"],
            }
        else:
            result = verify_bundle(args)
    except BundleError as exc:
        print(f"release-bundle: ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
