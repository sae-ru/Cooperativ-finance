#!/usr/bin/env python3
"""Write bounded host and backup status markers for the local operations API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

from runtime_environment import parse_env_file

HOST_PROBE_FORMAT = "cooperative-clearing-host-probe-v1"
BACKUP_STATUS_FORMAT = "cooperative-clearing-backup-status-v1"
BACKUP_FORMAT = "cooperative-clearing-backup-v1"
SAFE_IDENTIFIER = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$")
ALLOWED_CLOCK = {"SYNCED", "CONFIGURED", "UNSYNCED", "UNKNOWN"}
ALLOWED_UPS = {"ONLINE", "ON_BATTERY", "LOW_BATTERY", "NOT_CONFIGURED", "UNKNOWN"}
DEFAULT_PROBE_INTERVAL_SECONDS = 60
MIN_PROBE_INTERVAL_SECONDS = 30
MAX_PROBE_INTERVAL_SECONDS = 3600


class StatusError(RuntimeError):
    """A local status source violates its bounded format."""


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
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


def _command(*arguments: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def detect_clock_status(environ: dict[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    override = source.get("COOP_HOST_CLOCK_STATUS", "").strip().upper()
    if override:
        if override not in ALLOWED_CLOCK:
            raise StatusError("unsupported COOP_HOST_CLOCK_STATUS")
        return override
    if platform.system() == "Linux":
        result = _command("timedatectl", "show", "--property=NTPSynchronized", "--value")
        if result is not None and result.returncode == 0:
            value = result.stdout.strip().lower()
            if value == "yes":
                return "SYNCED"
            if value == "no":
                return "UNSYNCED"
    if platform.system() == "Windows":
        result = _command("w32tm", "/query", "/status")
        if result is not None and result.returncode == 0:
            return "CONFIGURED"
    return "UNKNOWN"


def detect_ups_status(environ: dict[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    override = source.get("COOP_UPS_STATUS", "").strip().upper()
    if override:
        if override not in ALLOWED_UPS:
            raise StatusError("unsupported COOP_UPS_STATUS")
        return override
    ups_name = source.get("COOP_UPS_NAME", "").strip()
    if not ups_name:
        return "NOT_CONFIGURED"
    result = _command("upsc", ups_name, "ups.status")
    if result is None or result.returncode != 0:
        return "UNKNOWN"
    tokens = set(result.stdout.upper().split())
    if "LB" in tokens:
        return "LOW_BATTERY"
    if "OB" in tokens:
        return "ON_BATTERY"
    if "OL" in tokens:
        return "ONLINE"
    return "UNKNOWN"


def probe_environment(root: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    values = parse_env_file(root / ".env")
    values.update({key: value for key, value in source.items() if value.strip()})
    return values


def write_host_probe(
    root: Path,
    *,
    now: datetime | None = None,
    monitor_id: str | None = None,
) -> Path:
    root = root.resolve()
    usage = shutil.disk_usage(root)
    generated_at = (now or datetime.now(UTC)).astimezone(UTC)
    environ = probe_environment(root)
    destination = root / ".operations" / "host-probe.json"
    payload: dict[str, Any] = {
        "format": HOST_PROBE_FORMAT,
        "generated_at": generated_at.isoformat(),
        "disk_total_bytes": usage.total,
        "disk_free_bytes": usage.free,
        "clock_status": detect_clock_status(environ),
        "ups_status": detect_ups_status(environ),
    }
    if monitor_id is not None:
        payload["monitor_id"] = monitor_id
    _write_atomic(destination, payload)
    return destination


def run_probe_loop(
    root: Path,
    *,
    interval_seconds: int,
    stop_event: Event,
    monitor_id: str,
) -> None:
    try:
        while not stop_event.is_set():
            try:
                write_host_probe(root, monitor_id=monitor_id)
            except (OSError, StatusError) as exc:
                print(f"operational-status: probe failed: {exc}", file=sys.stderr, flush=True)
            stop_event.wait(interval_seconds)
    finally:
        monitor_path = root.resolve() / ".operations" / "host-probe-monitor.json"
        record = _monitor_record(monitor_path)
        if record is not None and record.get("monitor_id") == monitor_id:
            monitor_path.unlink(missing_ok=True)


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _terminate_process(pid: int) -> None:
    if os.name != "nt":
        os.kill(pid, signal.SIGTERM)
        return

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_terminate = 0x0001
    synchronize = 0x00100000
    handle = kernel32.OpenProcess(process_terminate | synchronize, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel32.TerminateProcess(handle, 0):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


def _monitor_record(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _iso_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def start_probe_monitor(root: Path, *, interval_seconds: int) -> tuple[Path, bool]:
    root = root.resolve()
    state_root = root / ".operations"
    monitor_path = state_root / "host-probe-monitor.json"
    probe_path = state_root / "host-probe.json"
    record = _monitor_record(monitor_path)
    probe = _monitor_record(probe_path)
    if record is not None and probe is not None:
        pid = record.get("pid")
        monitor_id = record.get("monitor_id")
        probe_time = _iso_timestamp(probe.get("generated_at"))
        fresh = (
            probe_time is not None
            and (datetime.now(UTC) - probe_time).total_seconds() <= interval_seconds * 3
        )
        if (
            isinstance(pid, int)
            and isinstance(monitor_id, str)
            and probe.get("monitor_id") == monitor_id
            and fresh
            and _process_exists(pid)
        ):
            return monitor_path, False

    monitor_id = uuid.uuid4().hex
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "watch",
        "--root",
        str(root),
        "--interval",
        str(interval_seconds),
        "--monitor-id",
        monitor_id,
    ]
    process_options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        process_options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        process_options["start_new_session"] = True
    process = subprocess.Popen(command, **process_options)
    _write_atomic(
        monitor_path,
        {
            "format": "cooperative-clearing-host-probe-monitor-v1",
            "pid": process.pid,
            "monitor_id": monitor_id,
            "interval_seconds": interval_seconds,
            "started_at": datetime.now(UTC).isoformat(),
        },
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        probe = _monitor_record(probe_path)
        if probe is not None and probe.get("monitor_id") == monitor_id:
            return monitor_path, True
        if process.poll() is not None:
            monitor_path.unlink(missing_ok=True)
            raise StatusError("host probe monitor stopped during startup")
        time.sleep(0.1)
    if process.poll() is None:
        _terminate_process(process.pid)
    monitor_path.unlink(missing_ok=True)
    raise StatusError("host probe monitor did not report readiness")


def stop_probe_monitor(root: Path) -> tuple[Path, bool]:
    root = root.resolve()
    monitor_path = root / ".operations" / "host-probe-monitor.json"
    probe_path = root / ".operations" / "host-probe.json"
    record = _monitor_record(monitor_path)
    probe = _monitor_record(probe_path)
    if record is None or probe is None:
        return monitor_path, False
    pid = record.get("pid")
    monitor_id = record.get("monitor_id")
    interval = record.get("interval_seconds")
    probe_time = _iso_timestamp(probe.get("generated_at"))
    if (
        not isinstance(pid, int)
        or not isinstance(monitor_id, str)
        or not isinstance(interval, int)
        or probe.get("monitor_id") != monitor_id
        or probe_time is None
        or (datetime.now(UTC) - probe_time).total_seconds() > interval * 3
        or not _process_exists(pid)
    ):
        raise StatusError("host probe ownership cannot be confirmed")
    _terminate_process(pid)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _process_exists(pid):
        time.sleep(0.1)
    if _process_exists(pid):
        raise StatusError("host probe monitor did not stop")
    monitor_path.unlink(missing_ok=True)
    return monitor_path, True

def _manifest(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _backup_timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise StatusError("invalid backup timestamp") from exc


def record_backup(root: Path, backup_dir: Path) -> Path:
    root = root.resolve()
    backup_dir = backup_dir.resolve()
    manifest_path = backup_dir / "manifest.env"
    complete_path = backup_dir / "COMPLETE"
    checksums_path = backup_dir / "SHA256SUMS"
    if not manifest_path.is_file() or not complete_path.is_file() or not checksums_path.is_file():
        raise StatusError("backup is incomplete")
    values = _manifest(manifest_path)
    if values.get("format") != BACKUP_FORMAT:
        raise StatusError("unsupported backup format")
    backup_id = values.get("backup_id", "")
    release = values.get("release", "")
    backup_kind = values.get("backup_kind", "")
    if not SAFE_IDENTIFIER.fullmatch(backup_id) or not SAFE_IDENTIFIER.fullmatch(release):
        raise StatusError("invalid backup identifier")
    if backup_kind not in {"FULL", "DATA_ONLY"}:
        raise StatusError("invalid backup kind")
    completed_at = _backup_timestamp(complete_path.read_text(encoding="ascii").strip())
    destination = root / ".operations" / "backup-status.json"
    _write_atomic(
        destination,
        {
            "format": BACKUP_STATUS_FORMAT,
            "completed_at": completed_at.isoformat(),
            "backup_id": backup_id,
            "backup_kind": backup_kind,
            "release": release,
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "checksums_sha256": hashlib.sha256(checksums_path.read_bytes()).hexdigest(),
        },
    )
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe")
    probe.add_argument("--root", required=True)
    watch = commands.add_parser("watch")
    watch.add_argument("--root", required=True)
    watch.add_argument(
        "--interval",
        type=int,
        choices=range(MIN_PROBE_INTERVAL_SECONDS, MAX_PROBE_INTERVAL_SECONDS + 1),
        default=DEFAULT_PROBE_INTERVAL_SECONDS,
    )
    watch.add_argument("--monitor-id", required=True)
    start = commands.add_parser("start-probe")
    start.add_argument("--root", required=True)
    start.add_argument(
        "--interval",
        type=int,
        choices=range(MIN_PROBE_INTERVAL_SECONDS, MAX_PROBE_INTERVAL_SECONDS + 1),
        default=DEFAULT_PROBE_INTERVAL_SECONDS,
    )
    stop = commands.add_parser("stop-probe")
    stop.add_argument("--root", required=True)
    backup = commands.add_parser("record-backup")
    backup.add_argument("--root", required=True)
    backup.add_argument("--backup-dir", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "probe":
            output = write_host_probe(Path(args.root))
        elif args.command == "watch":
            stop_event = Event()

            def stop_monitor(_signum: int, _frame: object) -> None:
                stop_event.set()

            signal.signal(signal.SIGINT, stop_monitor)
            signal.signal(signal.SIGTERM, stop_monitor)
            run_probe_loop(
                Path(args.root),
                interval_seconds=args.interval,
                stop_event=stop_event,
                monitor_id=args.monitor_id,
            )
            return 0
        elif args.command == "start-probe":
            output, started = start_probe_monitor(
                Path(args.root),
                interval_seconds=args.interval,
            )
            state = "started" if started else "already running"
            print(f"{output} ({state})")
            return 0
        elif args.command == "stop-probe":
            output, stopped = stop_probe_monitor(Path(args.root))
            print(f"{output} ({'stopped' if stopped else 'not running'})")
            return 0
        else:
            output = record_backup(Path(args.root), Path(args.backup_dir))
    except (OSError, StatusError) as exc:
        print(f"operational-status: ERROR: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
