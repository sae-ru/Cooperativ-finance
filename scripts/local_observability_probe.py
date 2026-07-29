#!/usr/bin/env python3
"""Verify local health, metrics, operations, network isolation, and bounded logs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

REPORT_FORMAT = "cooperative-clearing-local-observability-v1"
NETWORK_FORMAT = "cooperative-clearing-network-isolation-v1"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
REQUIRED_NETWORKS = {"edge", "app", "web", "data"}
REQUIRED_METRICS = {
    "coop_build_info",
    "coop_http_requests_total",
    "coop_operational_records",
    "coop_host_check_severity",
}
REQUIRED_LOG_MARKERS = {"api", "gateway"}
MAX_JSON_BYTES = 1_048_576
MAX_METRICS_BYTES = 2_097_152
MAX_LOG_BYTES = 8_388_608


class ObservabilityError(RuntimeError):
    """The local observability contract is unavailable or unsafe."""


def _local_origin(base_url: str, *, allowed_hosts: set[str] | None = None) -> str:
    parsed = urlsplit(base_url)
    local_hosts = LOOPBACK_HOSTS | (allowed_hosts or set())
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in local_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ObservabilityError("base URL must be a loopback HTTP(S) origin")
    try:
        parsed.port
    except ValueError as exc:
        raise ObservabilityError("base URL contains an invalid port") from exc
    return base_url.rstrip("/")


def read_password(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ObservabilityError("operator password file is unavailable") from exc
    if not raw or len(raw) > 4096 or b"\x00" in raw:
        raise ObservabilityError("operator password file is invalid")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ObservabilityError("operator password file is invalid") from exc
    value = decoded.strip()
    if len(value) < 12 or len(value) > 256 or len(decoded.splitlines()) != 1:
        raise ObservabilityError("operator password file is invalid")
    return value


def _request(
    opener: Callable[..., Any],
    *,
    url: str,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    token: str | None = None,
    maximum_bytes: int = MAX_JSON_BYTES,
) -> tuple[int, str, bytes]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with opener(request, timeout=10) as response:
            content = response.read(maximum_bytes + 1)
            status = int(getattr(response, "status", response.getcode()))
            content_type = str(response.headers.get("Content-Type", ""))
    except HTTPError as exc:
        path = urlsplit(url).path
        raise ObservabilityError(
            f"local endpoint {path} rejected request with HTTP {exc.code}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ObservabilityError("local endpoint is unavailable") from exc
    if len(content) > maximum_bytes:
        raise ObservabilityError("local endpoint response is oversized")
    if status != 200:
        raise ObservabilityError(f"local endpoint returned HTTP {status}")
    return status, content_type, content


def _json(content: bytes, *, endpoint: str) -> dict[str, object]:
    try:
        value = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ObservabilityError(f"{endpoint} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ObservabilityError(f"{endpoint} returned an invalid envelope")
    return value


def _envelope_data(value: dict[str, object], *, endpoint: str) -> dict[str, object]:
    data = value.get("data")
    if not isinstance(data, dict):
        raise ObservabilityError(f"{endpoint} returned an invalid data envelope")
    return data


def _network_evidence(path: Path) -> tuple[dict[str, bool], str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ObservabilityError("network isolation evidence is unavailable") from exc
    if not raw or len(raw) > 16_384:
        raise ObservabilityError("network isolation evidence is invalid")
    value = _json(raw, endpoint="network evidence")
    networks = value.get("networks")
    if value.get("format") != NETWORK_FORMAT or not isinstance(networks, dict):
        raise ObservabilityError("network isolation evidence is invalid")
    normalized = {name: networks.get(name) is True for name in REQUIRED_NETWORKS}
    if not all(normalized.values()) or value.get("egress_probe") != "BLOCKED":
        raise ObservabilityError("test topology still has an external egress path")
    return normalized, hashlib.sha256(raw).hexdigest()


def _log_evidence(path: Path, *, forbidden_values: tuple[str, ...]) -> tuple[int, int, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ObservabilityError("local runtime logs are unavailable") from exc
    if not raw or len(raw) > MAX_LOG_BYTES or b"\x00" in raw:
        raise ObservabilityError("local runtime logs are empty, oversized, or invalid")
    text = raw.decode("utf-8", errors="replace")
    if any(value and value in text for value in forbidden_values):
        raise ObservabilityError("operator password leaked into local runtime logs")
    missing = {marker for marker in REQUIRED_LOG_MARKERS if marker not in text.casefold()}
    if missing:
        raise ObservabilityError("local runtime logs do not contain required services")
    return len(raw), len(text.splitlines()), hashlib.sha256(raw).hexdigest()


def _metric_families(metrics: str) -> set[str]:
    result: set[str] = set()
    for line in metrics.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        result.add(stripped.split("{", 1)[0].split(" ", 1)[0])
    return result


def _temporary_password() -> str:
    return f"Local-observability-{secrets.token_urlsafe(32)}"


def run_probe(
    *,
    base_url: str,
    login: str,
    password: str,
    expected_schema: str,
    network_evidence: Path,
    logs: Path,
    opener: Callable[..., Any] = urlopen,
    allowed_hosts: set[str] | None = None,
    new_password_factory: Callable[[], str] = _temporary_password,
) -> dict[str, object]:
    origin = _local_origin(base_url, allowed_hosts=allowed_hosts)
    live = _json(
        _request(opener, url=f"{origin}/health/live")[2], endpoint="health/live"
    )
    ready = _json(
        _request(opener, url=f"{origin}/health/ready")[2], endpoint="health/ready"
    )
    if live.get("status") != "LIVE" or ready.get("status") != "READY":
        raise ObservabilityError("local health checks are not ready")

    login_data = _envelope_data(
        _json(
            _request(
                opener,
                url=f"{origin}/api/v1/auth/login",
                method="POST",
                payload={"login": login, "password": password},
            )[2],
            endpoint="auth/login",
        ),
        endpoint="auth/login",
    )
    token = login_data.get("access_token")
    if not isinstance(token, str) or len(token) < 16:
        raise ObservabilityError("local operator authentication returned no access token")
    principal = login_data.get("principal")
    if not isinstance(principal, dict) or not isinstance(
        principal.get("must_change_password"), bool
    ):
        raise ObservabilityError("local operator authentication returned no password state")

    rotated_password: str | None = None
    if principal["must_change_password"]:
        rotated_password = new_password_factory()
        if (
            not isinstance(rotated_password, str)
            or len(rotated_password) < 16
            or len(rotated_password) > 256
            or rotated_password == password
            or len(rotated_password.splitlines()) != 1
        ):
            raise ObservabilityError("generated operator password is invalid")
        changed = _envelope_data(
            _json(
                _request(
                    opener,
                    url=f"{origin}/api/v1/auth/change-password",
                    method="POST",
                    payload={
                        "current_password": password,
                        "new_password": rotated_password,
                    },
                    token=token,
                )[2],
                endpoint="auth/change-password",
            ),
            endpoint="auth/change-password",
        )
        token = changed.get("access_token")
        changed_principal = changed.get("principal")
        if (
            not isinstance(token, str)
            or len(token) < 16
            or not isinstance(changed_principal, dict)
            or changed_principal.get("must_change_password") is not False
        ):
            raise ObservabilityError("operator password change returned an invalid session")

    snapshot = _envelope_data(
        _json(
            _request(
                opener,
                url=f"{origin}/api/v1/operations/snapshot",
                token=token,
            )[2],
            endpoint="operations/snapshot",
        ),
        endpoint="operations/snapshot",
    )
    if snapshot.get("schema_revision") != expected_schema:
        raise ObservabilityError("operational snapshot has an unexpected schema revision")
    if not isinstance(snapshot.get("signed_events"), int):
        raise ObservabilityError("operational snapshot has no signed-event count")

    readiness = _envelope_data(
        _json(
            _request(
                opener,
                url=f"{origin}/api/v1/operations/host-readiness",
                token=token,
            )[2],
            endpoint="operations/host-readiness",
        ),
        endpoint="operations/host-readiness",
    )
    checks = readiness.get("checks")
    if not isinstance(checks, list) or {
        item.get("name") for item in checks if isinstance(item, dict)
    } != {"storage", "clock", "backup", "certificates", "ups"}:
        raise ObservabilityError("host readiness does not contain the bounded local checks")

    _, content_type, raw_metrics = _request(
        opener,
        url=f"{origin}/api/v1/operations/metrics",
        token=token,
        maximum_bytes=MAX_METRICS_BYTES,
    )
    if not content_type.startswith("text/plain"):
        raise ObservabilityError("operations metrics have an invalid content type")
    try:
        metrics = raw_metrics.decode("utf-8")
    except UnicodeError as exc:
        raise ObservabilityError("operations metrics are not UTF-8") from exc
    families = _metric_families(metrics)
    if not REQUIRED_METRICS <= families:
        raise ObservabilityError("operations metrics are missing required local families")
    if "http://" in metrics or "https://" in metrics:
        raise ObservabilityError("operations metrics contain an external destination")

    networks, network_sha256 = _network_evidence(network_evidence)
    log_bytes, log_lines, log_sha256 = _log_evidence(
        logs,
        forbidden_values=tuple(
            value for value in (password, rotated_password) if value is not None
        ),
    )
    return {
        "format": REPORT_FORMAT,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "PASSED",
        "local_origin": origin,
        "schema_revision": expected_schema,
        "signed_events": snapshot["signed_events"],
        "host_readiness_status": readiness.get("status"),
        "metric_family_count": len(families),
        "required_metric_families": sorted(REQUIRED_METRICS),
        "internal_networks": networks,
        "egress_probe": "BLOCKED",
        "network_evidence_sha256": network_sha256,
        "local_log_bytes": log_bytes,
        "local_log_lines": log_lines,
        "local_log_sha256": log_sha256,
        "telemetry_export": "DISABLED",
        "bootstrap_password_rotated": rotated_password is not None,
    }


def _write_report(path: Path, report: dict[str, object]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--allow-internal-host", action="append", default=[])
    parser.add_argument("--login", default="security")
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--expected-schema", required=True)
    parser.add_argument("--network-evidence", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        allowed_hosts = set(args.allow_internal_host)
        if any(
            not value
            or len(value) > 63
            or not value.replace("-", "").isalnum()
            for value in allowed_hosts
        ):
            raise ObservabilityError("allowed internal host is invalid")
        report = run_probe(
            base_url=args.base_url,
            login=args.login,
            password=read_password(args.password_file),
            expected_schema=args.expected_schema,
            network_evidence=args.network_evidence,
            logs=args.logs,
            allowed_hosts=allowed_hosts,
        )
        if str(args.report) == "-":
            print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
            return 0
        _write_report(args.report, report)
    except (ObservabilityError, OSError, UnicodeError) as exc:
        print(f"local-observability: ERROR: {exc}", file=os.sys.stderr)
        return 1
    print(args.report.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())