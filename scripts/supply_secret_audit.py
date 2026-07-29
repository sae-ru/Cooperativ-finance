#!/usr/bin/env python3
"""Redacted, high-confidence secret audit for source, release images and env files."""

from __future__ import annotations

import argparse
import json
import re
import tarfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable

REPORT_FORMAT = "cooperative-clearing-secret-audit-v1"
CHUNK_SIZE = 1024 * 1024
OVERLAP_SIZE = 2048
LITERAL_SCAN_LIMIT = 16 * 1024 * 1024

PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    b"-----BEGIN (?P<label>"
    + b"(?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?"
    + b"PRIVATE KEY"
    + b")-----\\r?\\n"
    + b"(?:[A-Za-z0-9+/=]{16,}\\r?\\n){2,}"
    + b"-----END (?P=label)-----"
)
TOKEN_PATTERNS = (
    ("AWS_ACCESS_KEY", re.compile(rb"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])")),
    ("GITHUB_TOKEN", re.compile(rb"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{36,}")),
    (
        "GITHUB_FINE_GRAINED_TOKEN",
        re.compile(rb"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{40,}"),
    ),
    (
        "OPENAI_API_KEY",
        re.compile(rb"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    ),
)
SENSITIVE_LITERAL_PATTERN = re.compile(
    rb"""(?ix)
    \b(?:password|passwd|secret|private_key|signing_seed|encryption_key|
       api_token|access_token|refresh_token)\b
    \s*(?:=|:)\s*f?["']([^"'\r\n]{8,})["']
    """
)
CREDENTIAL_URL_PATTERN = re.compile(
    rb"(?i)\b(?:postgres(?:ql)?|mysql|redis|amqp|https?)://[^/\s:@]+:[^@\s/]+@"
)
KNOWN_PUBLIC_DEMO_FRAGMENTS = (
    b"CoopDemo-Registrar-2026!",
    b"CoopDemo-Security-2026!",
    b"CoopDemo-Auditor-2026!",
    b"CoopDemo-Farmer-2026!",
    b"demoServiceCredentialForLocalTestingOnly2026ABC",
)
FORBIDDEN_SECRET_FILENAMES = frozenset(
    {
        "node_signing_seed",
        "blob_encryption_key",
        "mfa_encryption_key",
        "postgres_password",
        "postgres_app_password",
        "postgres_migrator_password",
        "bootstrap_registrar_password",
        "bootstrap_security_password",
        "bootstrap_auditor_password",
    }
)
SENSITIVE_ENV_NAME = re.compile(
    r"(?i)(?:PASSWORD|PASSPHRASE|PRIVATE_KEY|SIGNING_SEED|ENCRYPTION_KEY|"
    r"ACCESS_TOKEN|REFRESH_TOKEN|API_TOKEN|CLIENT_SECRET|SECRET)$"
)
PUBLIC_ENV_EXCEPTIONS = frozenset({"COOP_RELEASE_PUBLIC_KEY"})
IMAGE_PLAINTEXT_SUFFIXES = frozenset(
    {
        ".bat",
        ".cfg",
        ".cjs",
        ".cmd",
        ".conf",
        ".env",
        ".htm",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".jsx",
        ".mjs",
        ".properties",
        ".ps1",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".xml",
        ".yaml",
        ".yml",
    }
)


class SecretAuditError(RuntimeError):
    """A scanned artifact contains a high-confidence plaintext secret."""


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    origin: str
    offset: int | None = None


@dataclass(slots=True)
class ScopeAudit:
    scope: str
    files_scanned: int = 0
    bytes_scanned: int = 0
    public_demo_literals: int = 0
    findings: list[Finding] = field(default_factory=list)

    def as_report(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "files_scanned": self.files_scanned,
            "bytes_scanned": self.bytes_scanned,
            "public_demo_literals": self.public_demo_literals,
            "findings": [asdict(item) for item in self.findings],
        }


def _safe_origin(value: str) -> str:
    return str(PurePosixPath(value.replace("\\", "/")))


def _path_findings(origin: str) -> list[Finding]:
    name = PurePosixPath(origin).name.casefold()
    findings: list[Finding] = []
    if name in FORBIDDEN_SECRET_FILENAMES:
        findings.append(Finding("SECRET_FILENAME", origin))
    if name.endswith((".key", ".p12", ".pfx")):
        findings.append(Finding("PRIVATE_KEY_FILE", origin))
    if "private" in name and name.endswith(".pem"):
        findings.append(Finding("PRIVATE_KEY_FILE", origin))
    return findings


def _content_findings(
    data: bytes,
    origin: str,
    *,
    base_offset: int,
    strict_literals: bool,
    scan_credential_urls: bool,
    scan_private_key_blocks: bool,
) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    public_demo_literals = sum(data.count(value) for value in KNOWN_PUBLIC_DEMO_FRAGMENTS)
    private_key = (
        PRIVATE_KEY_BLOCK_PATTERN.search(data)
        if scan_private_key_blocks
        else None
    )
    if private_key:
        findings.append(
            Finding("PRIVATE_KEY_PEM", origin, base_offset + private_key.start())
        )
    for code, pattern in TOKEN_PATTERNS:
        match = pattern.search(data)
        if match:
            findings.append(Finding(code, origin, base_offset + match.start()))
    url_match = CREDENTIAL_URL_PATTERN.search(data) if scan_credential_urls else None
    if url_match is not None:
        findings.append(
            Finding("CREDENTIAL_IN_URL", origin, base_offset + url_match.start())
        )
    if strict_literals:
        for match in SENSITIVE_LITERAL_PATTERN.finditer(data):
            value = match.group(1)
            if any(fragment in value for fragment in KNOWN_PUBLIC_DEMO_FRAGMENTS):
                continue
            if (
                value.startswith((b"${", b"$(", b"{{", b"<"))
                or b"{" in value
                or b"example" in value.lower()
                or b"placeholder" in value.lower()
            ):
                continue
            findings.append(
                Finding("SENSITIVE_LITERAL", origin, base_offset + match.start(1))
            )
    return findings, public_demo_literals

def scan_stream(
    stream: BinaryIO,
    *,
    size: int,
    origin: str,
    strict_literals: bool,
    scan_credential_urls: bool = True,
    scan_private_key_blocks: bool = True,
) -> tuple[list[Finding], int, int]:
    findings = _path_findings(origin)
    demo_count = 0
    scanned = 0
    tail = b""
    literal_buffer = bytearray()
    while True:
        chunk = stream.read(CHUNK_SIZE)
        if not chunk:
            break
        scanned += len(chunk)
        if len(literal_buffer) < LITERAL_SCAN_LIMIT:
            remaining = LITERAL_SCAN_LIMIT - len(literal_buffer)
            literal_buffer.extend(chunk[:remaining])
        combined = tail + chunk
        base_offset = max(0, scanned - len(chunk) - len(tail))
        chunk_findings, chunk_demo_count = _content_findings(
            combined,
            origin,
            base_offset=base_offset,
            strict_literals=False,
            scan_credential_urls=scan_credential_urls,
            scan_private_key_blocks=scan_private_key_blocks,
        )
        findings.extend(chunk_findings)
        demo_count += chunk_demo_count
        tail = combined[-OVERLAP_SIZE:]
    if strict_literals and size <= LITERAL_SCAN_LIMIT:
        literal_findings, _ = _content_findings(
            bytes(literal_buffer),
            origin,
            base_offset=0,
            strict_literals=True,
            scan_credential_urls=scan_credential_urls,
            scan_private_key_blocks=scan_private_key_blocks,
        )
        findings.extend(literal_findings)
    unique = {
        (item.rule, item.origin, item.offset): item
        for item in findings
    }
    return list(unique.values()), demo_count, scanned


def audit_files(
    root: Path,
    relative_paths: Iterable[str],
    *,
    scope: str,
    strict_literals: bool,
) -> ScopeAudit:
    audit = ScopeAudit(scope=scope)
    resolved_root = root.resolve()
    for relative in sorted(set(relative_paths)):
        path = resolved_root.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file() or path.is_symlink():
            continue
        origin = _safe_origin(relative)
        with path.open("rb") as source:
            findings, demo_count, scanned = scan_stream(
                source,
                size=path.stat().st_size,
                origin=origin,
                strict_literals=strict_literals,
            )
        audit.files_scanned += 1
        audit.bytes_scanned += scanned
        audit.public_demo_literals += demo_count
        audit.findings.extend(findings)
    return audit


def _image_plaintext_context(origin: str) -> bool:
    normalized = "/" + origin.lstrip("./")
    name = PurePosixPath(origin).name.casefold()
    suffix = PurePosixPath(origin).suffix.casefold()
    return (
        normalized.startswith("/app/")
        or "/site-packages/cooperative_clearing/" in normalized
        or normalized.startswith("/usr/share/nginx/html/")
        or suffix in IMAGE_PLAINTEXT_SUFFIXES
        or name in {".env", "environment"}
    )

def _image_compiled_binary(origin: str) -> bool:
    name = PurePosixPath(origin).name.casefold()
    suffix = PurePosixPath(origin).suffix.casefold()
    return suffix in {".a", ".o", ".pyc", ".pyo", ".so"} or ".so." in name

def audit_image_archive(archive: Path, *, scope: str) -> ScopeAudit:
    audit = ScopeAudit(scope=scope)
    with tarfile.open(archive, mode="r:*") as outer:
        try:
            manifest_member = outer.getmember("manifest.json")
            manifest_source = outer.extractfile(manifest_member)
            if manifest_source is None:
                raise tarfile.ReadError("manifest.json is not a regular file")
            manifest = json.load(manifest_source)
        except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
            raise tarfile.ReadError(f"invalid Docker image manifest: {exc}") from exc
        if not isinstance(manifest, list) or not manifest:
            raise tarfile.ReadError("Docker image manifest is empty")
        layer_names: set[str] = set()
        for image in manifest:
            if not isinstance(image, dict) or not isinstance(image.get("Layers"), list):
                raise tarfile.ReadError("Docker image manifest has no layer inventory")
            for layer_name in image["Layers"]:
                if not isinstance(layer_name, str) or not layer_name:
                    raise tarfile.ReadError(
                        "Docker image manifest has an invalid layer path"
                    )
                pure = PurePosixPath(layer_name)
                if pure.is_absolute() or ".." in pure.parts or "\\" in layer_name:
                    raise tarfile.ReadError(
                        "Docker image manifest has an unsafe layer path"
                    )
                layer_names.add(layer_name)
        if not layer_names:
            raise tarfile.ReadError("Docker image manifest has no layers")

        present_files: set[str] = set()
        for outer_member in outer:
            if not outer_member.isfile():
                continue
            present_files.add(outer_member.name)
            outer_source = outer.extractfile(outer_member)
            if outer_source is None:
                continue
            if outer_member.name not in layer_names:
                origin = _safe_origin(f"archive/{outer_member.name}")
                findings, demo_count, scanned = scan_stream(
                    outer_source,
                    size=outer_member.size,
                    origin=origin,
                    strict_literals=False,
                )
                audit.files_scanned += 1
                audit.bytes_scanned += scanned
                audit.public_demo_literals += demo_count
                audit.findings.extend(findings)
                continue
            with tarfile.open(fileobj=outer_source, mode="r|*") as layer:
                for member in layer:
                    if not member.isfile():
                        continue
                    source = layer.extractfile(member)
                    if source is None:
                        continue
                    origin = _safe_origin(
                        f"layer/{outer_member.name}/{member.name}"
                    )
                    plaintext_context = _image_plaintext_context(member.name)
                    findings, demo_count, scanned = scan_stream(
                        source,
                        size=member.size,
                        origin=origin,
                        strict_literals=plaintext_context,
                        scan_credential_urls=plaintext_context,
                        scan_private_key_blocks=not _image_compiled_binary(
                            member.name
                        ),
                    )
                    audit.files_scanned += 1
                    audit.bytes_scanned += scanned
                    audit.public_demo_literals += demo_count
                    audit.findings.extend(findings)
        missing_layers = layer_names - present_files
        if missing_layers:
            raise tarfile.ReadError(
                "Docker image archive is missing declared layers: "
                + ", ".join(sorted(missing_layers))
            )
    return audit

def audit_tar_archive(
    archive: Path,
    *,
    scope: str,
    strict_literals: bool,
) -> ScopeAudit:
    audit = ScopeAudit(scope=scope)
    with tarfile.open(archive, mode="r:*") as container:
        for member in container:
            if not member.isfile():
                continue
            source = container.extractfile(member)
            if source is None:
                continue
            origin = _safe_origin(member.name)
            findings, demo_count, scanned = scan_stream(
                source,
                size=member.size,
                origin=origin,
                strict_literals=strict_literals,
            )
            audit.files_scanned += 1
            audit.bytes_scanned += scanned
            audit.public_demo_literals += demo_count
            audit.findings.extend(findings)
    return audit

def is_plaintext_secret_setting(name: str, value: str) -> bool:
    if name in PUBLIC_ENV_EXCEPTIONS:
        return False
    secret_name = name[:-5] if name.endswith("_FILE") else name
    if not SENSITIVE_ENV_NAME.search(secret_name):
        return False
    normalized = value.replace("\\", "/")
    return not (
        name.endswith("_FILE")
        and (
            normalized.startswith("/run/secrets/")
            or "/secrets/" in normalized
            or normalized.startswith("./secrets/")
        )
    )

def audit_env_file(path: Path, *, scope: str = "runtime-env") -> ScopeAudit:
    audit = ScopeAudit(scope=scope)
    audit.files_scanned = 1
    payload = path.read_bytes()
    audit.bytes_scanned = len(payload)
    for line_number, raw_line in enumerate(
        payload.decode("utf-8-sig", errors="strict").splitlines(),
        1,
    ):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not is_plaintext_secret_setting(name, value):
            continue
        audit.findings.append(
            Finding(
                "PLAINTEXT_SECRET_ENV",
                _safe_origin(f"{path.name}:{name}"),
                line_number,
            )
        )
    return audit


def build_report(scopes: Iterable[ScopeAudit]) -> dict[str, object]:
    rows = sorted((item.as_report() for item in scopes), key=lambda item: str(item["scope"]))
    finding_count = sum(len(item["findings"]) for item in rows)
    return {
        "format": REPORT_FORMAT,
        "status": "PASSED" if finding_count == 0 else "FAILED",
        "finding_count": finding_count,
        "scopes": rows,
    }


def audit_backup_directory(directory: Path) -> dict[str, object]:
    required = ("database.dump", "blobs.tar.gz")
    missing = [name for name in required if not (directory / name).is_file()]
    if missing:
        raise SecretAuditError(
            "Backup secret audit is missing required files: " + ", ".join(missing)
        )
    scopes = [
        audit_files(
            directory,
            ["database.dump"],
            scope="backup:database",
            strict_literals=False,
        ),
        audit_tar_archive(
            directory / "blobs.tar.gz",
            scope="backup:blobs",
            strict_literals=True,
        ),
    ]
    if (directory / "runtime.env").is_file():
        scopes.append(
            audit_env_file(
                directory / "runtime.env",
                scope="backup:runtime-env",
            )
        )
    if (directory / "recovery.bundle.enc").is_file():
        scopes.append(
            audit_files(
                directory,
                ["recovery.bundle.enc"],
                scope="backup:encrypted-recovery",
                strict_literals=False,
            )
        )
    return build_report(scopes)

def require_clean(report: dict[str, object]) -> None:
    if report.get("status") == "PASSED":
        return
    summaries: list[str] = []
    for scope in report.get("scopes", []):
        if not isinstance(scope, dict):
            continue
        for finding in scope.get("findings", []):
            if isinstance(finding, dict):
                summaries.append(
                    f"{finding.get('rule')}:{finding.get('origin')}"
                )
    raise SecretAuditError(
        "Plaintext secret audit failed: " + ", ".join(sorted(summaries))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    env = subparsers.add_parser("env")
    env.add_argument("--file", required=True)
    image = subparsers.add_parser("image-archive")
    image.add_argument("--archive", required=True)
    image.add_argument("--scope", required=True)
    backup = subparsers.add_parser("backup")
    backup.add_argument("--directory", required=True)
    args = parser.parse_args()
    try:
        if args.command == "env":
            report = build_report([audit_env_file(Path(args.file).resolve())])
        elif args.command == "image-archive":
            report = build_report(
                [
                    audit_image_archive(
                        Path(args.archive).resolve(),
                        scope=args.scope,
                    )
                ]
            )
        else:
            report = audit_backup_directory(Path(args.directory).resolve())
        require_clean(report)
    except (OSError, UnicodeError, tarfile.TarError, SecretAuditError) as exc:
        print(f"supply-secret-audit: ERROR: {exc}")
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
