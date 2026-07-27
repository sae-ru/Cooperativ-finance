#!/usr/bin/env python3
"""Resolve and persist the node runtime environment without ambiguous aliases."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

ALLOWED_ENVIRONMENTS = frozenset(
    {"dev", "test", "staging-node", "pilot", "production"}
)
HARDENED_ENVIRONMENTS = frozenset({"staging-node", "pilot", "production"})
RELEASE_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")
SHA256_PATTERN = re.compile(r"^[0-9A-Fa-f]{64}$")
ENV_LINE_PATTERN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
OPERATIONAL_SETTINGS = frozenset(
    {
        "COOP_RELEASE",
        "COOP_RELEASE_LICENSE_POLICY_SHA256",
        "COOP_RELEASE_PUBLIC_KEY",
        "COOP_VERIFIED_RELEASE_BUNDLE",
    }
)

MODE_VALUES = {
    "demo": {
        "COOP_ENVIRONMENT": "dev",
        "COOP_DEMO_DATA_ENABLED": "true",
        "COMPOSE_PROFILES": "demo",
    },
    "production": {
        "COOP_ENVIRONMENT": "production",
        "COOP_DEMO_DATA_ENABLED": "false",
        "COMPOSE_PROFILES": "production",
    },
}

DEMO_BOOTSTRAP_CREDENTIALS = {
    "bootstrap_registrar_password": "CoopDemo-Registrar-2026!",
    "bootstrap_security_password": "CoopDemo-Security-2026!",
    "bootstrap_auditor_password": "CoopDemo-Auditor-2026!",
}


class EnvironmentContractError(RuntimeError):
    """The requested runtime environment violates the deployment contract."""


@dataclass(frozen=True, slots=True)
class ConfigurationResult:
    environment: str
    demo_data_enabled: bool
    compose_profile: str
    release: str | None


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        match = ENV_LINE_PATTERN.fullmatch(raw_line.strip())
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def validate_environment(value: str) -> str:
    normalized = value.strip()
    if normalized not in ALLOWED_ENVIRONMENTS:
        allowed = ", ".join(sorted(ALLOWED_ENVIRONMENTS))
        raise EnvironmentContractError(
            f"Unsupported COOP_ENVIRONMENT {value!r}; expected one of: {allowed}"
        )
    return normalized


def resolve_environment(
    root: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if environ is None else environ
    explicit_raw = source.get("COOP_ENVIRONMENT", "").strip()
    configured_raw = parse_env_file(root / ".env").get("COOP_ENVIRONMENT", "").strip()
    explicit = validate_environment(explicit_raw) if explicit_raw else None
    configured = validate_environment(configured_raw) if configured_raw else None
    if (
        explicit is not None
        and configured is not None
        and explicit != configured
        and (
            explicit in HARDENED_ENVIRONMENTS
            or configured in HARDENED_ENVIRONMENTS
        )
    ):
        raise EnvironmentContractError(
            "COOP_ENVIRONMENT disagrees with persisted .env for a hardened node"
        )
    return explicit or configured or "dev"


def resolve_setting(
    root: Path,
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    if name not in OPERATIONAL_SETTINGS:
        raise EnvironmentContractError(f"Unsupported operational setting: {name}")
    source = os.environ if environ is None else environ
    return source.get(name, "").strip() or parse_env_file(root / ".env").get(
        name, ""
    ).strip()


def _contains_known_demo_credentials(root: Path) -> list[str]:
    found: list[str] = []
    secrets = root / "secrets"
    for filename, known_value in DEMO_BOOTSTRAP_CREDENTIALS.items():
        path = secrets / filename
        if path.is_file() and path.read_text(encoding="utf-8-sig").strip() == known_value:
            found.append(filename)
    return found


def _write_env_file(path: Path, updates: Mapping[str, str], template: Path) -> None:
    if path.is_file():
        original_lines = path.read_text(encoding="utf-8-sig").splitlines()
    elif template.is_file():
        original_lines = template.read_text(encoding="utf-8-sig").splitlines()
    else:
        original_lines = []

    retained: list[str] = []
    for line in original_lines:
        match = ENV_LINE_PATTERN.fullmatch(line.strip())
        if match and match.group(1) in updates:
            continue
        retained.append(line)
    while retained and not retained[-1].strip():
        retained.pop()
    if retained:
        retained.append("")
    retained.extend(f"{key}={value}" for key, value in updates.items())
    payload = "\n".join(retained) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def configure_mode(
    root: Path,
    *,
    mode: str,
    release: str | None = None,
    verified_release_bundle: str | None = None,
    release_public_key: str | None = None,
    license_policy_sha256: str | None = None,
) -> ConfigurationResult:
    if mode not in MODE_VALUES:
        raise EnvironmentContractError(f"Unsupported deployment mode: {mode}")
    if release is not None and not RELEASE_PATTERN.fullmatch(release):
        raise EnvironmentContractError(f"Invalid release identifier: {release}")

    env_path = root / ".env"
    existing = parse_env_file(env_path)
    existing_environment_raw = existing.get("COOP_ENVIRONMENT", "").strip()
    existing_environment = (
        validate_environment(existing_environment_raw)
        if existing_environment_raw
        else None
    )
    existing_demo = existing.get("COOP_DEMO_DATA_ENABLED", "").strip().lower()

    if mode == "demo" and existing_environment in HARDENED_ENVIRONMENTS:
        raise EnvironmentContractError(
            "A hardened node cannot be downgraded to demo mode in place"
        )
    if mode == "production" and existing_demo == "true":
        raise EnvironmentContractError(
            "A demo-configured node cannot be promoted to production in place; "
            "use a clean installation and restore only approved production data"
        )
    if mode == "production":
        demo_secrets = _contains_known_demo_credentials(root)
        if demo_secrets:
            raise EnvironmentContractError(
                "Production bootstrap refused known demo credentials: "
                + ", ".join(sorted(demo_secrets))
            )
        if not verified_release_bundle or not release_public_key:
            raise EnvironmentContractError(
                "Production requires a verified release bundle and public key"
            )
        bundle_path = Path(verified_release_bundle).expanduser()
        public_key_path = Path(release_public_key).expanduser()
        if not bundle_path.is_absolute():
            bundle_path = root / bundle_path
        if not public_key_path.is_absolute():
            public_key_path = root / public_key_path
        bundle_path = bundle_path.resolve()
        public_key_path = public_key_path.resolve()
        if not bundle_path.is_dir():
            raise EnvironmentContractError(
                f"Verified release bundle does not exist: {bundle_path}"
            )
        if not public_key_path.is_file():
            raise EnvironmentContractError(
                f"Release public key does not exist: {public_key_path}"
            )
        if not license_policy_sha256 or not SHA256_PATTERN.fullmatch(
            license_policy_sha256
        ):
            raise EnvironmentContractError(
                "Production requires a 64-character license-policy SHA-256"
            )

    updates = dict(MODE_VALUES[mode])
    if release is not None:
        updates["COOP_RELEASE"] = release
    if mode == "production":
        assert license_policy_sha256 is not None
        updates.update(
            {
                "COOP_VERIFIED_RELEASE_BUNDLE": str(bundle_path),
                "COOP_RELEASE_PUBLIC_KEY": str(public_key_path),
                "COOP_RELEASE_LICENSE_POLICY_SHA256": license_policy_sha256.lower(),
            }
        )
    _write_env_file(env_path, updates, root / ".env.example")

    return ConfigurationResult(
        environment=updates["COOP_ENVIRONMENT"],
        demo_data_enabled=updates["COOP_DEMO_DATA_ENABLED"] == "true",
        compose_profile=updates["COMPOSE_PROFILES"],
        release=release or existing.get("COOP_RELEASE"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("--root", required=True)

    configure = subparsers.add_parser("configure")
    configure.add_argument("--root", required=True)
    configure.add_argument("--mode", choices=sorted(MODE_VALUES), required=True)
    configure.add_argument("--release")
    configure.add_argument("--verified-release-bundle")
    configure.add_argument("--release-public-key")
    configure.add_argument("--license-policy-sha256")

    get = subparsers.add_parser("get")
    get.add_argument("--root", required=True)
    get.add_argument("--name", choices=sorted(OPERATIONAL_SETTINGS), required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        root = Path(args.root).resolve()
        if args.command == "resolve":
            print(resolve_environment(root))
        elif args.command == "get":
            print(resolve_setting(root, args.name))
        else:
            result = configure_mode(
                root,
                mode=args.mode,
                release=args.release,
                verified_release_bundle=(
                    args.verified_release_bundle
                    or resolve_setting(root, "COOP_VERIFIED_RELEASE_BUNDLE")
                ),
                release_public_key=(
                    args.release_public_key
                    or resolve_setting(root, "COOP_RELEASE_PUBLIC_KEY")
                ),
                license_policy_sha256=(
                    args.license_policy_sha256
                    or resolve_setting(
                        root,
                        "COOP_RELEASE_LICENSE_POLICY_SHA256",
                    )
                ),
            )
            print(json.dumps(asdict(result), sort_keys=True))
    except EnvironmentContractError as exc:
        print(f"runtime-environment: ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
