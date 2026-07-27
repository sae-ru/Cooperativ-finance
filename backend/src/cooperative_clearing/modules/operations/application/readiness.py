"""Local host-readiness checks without external monitoring dependencies."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple

from sqlalchemy import text

from cooperative_clearing.shared.core.config import Environment, Settings
from cooperative_clearing.shared.infrastructure.database import Database

BACKUP_STATUS_FORMAT = "cooperative-clearing-backup-status-v1"
HOST_PROBE_FORMAT = "cooperative-clearing-host-probe-v1"
MAX_STATUS_BYTES = 65_536
HARDENED_ENVIRONMENTS = {
    Environment.STAGING,
    Environment.PILOT,
    Environment.PRODUCTION,
}

CheckStatus = Literal["OK", "WARNING", "CRITICAL", "UNKNOWN"]


@dataclass(frozen=True, slots=True)
class InfrastructureFacts:
    database_time: datetime
    active_certificates: int
    expired_certificates: int
    expiring_certificates: int
    nearest_certificate_expiry: datetime | None


@dataclass(frozen=True, slots=True)
class HostCheck:
    name: str
    status: CheckStatus
    code: str
    observed_at: datetime
    metrics: dict[str, int | str | bool | None]


@dataclass(frozen=True, slots=True)
class HostReadiness:
    generated_at: datetime
    status: Literal["OPERATIONAL", "ATTENTION", "CRITICAL"]
    checks: tuple[HostCheck, ...]


class DiskUsage(NamedTuple):
    total: int
    used: int
    free: int


FACTS_QUERY = text(
    """
    SELECT
      clock_timestamp() AS database_time,
      count(*) FILTER (WHERE status IN ('ACTIVE','ROTATING')) AS active_certificates,
      count(*) FILTER (
        WHERE status IN ('ACTIVE','ROTATING') AND valid_until <= clock_timestamp()
      ) AS expired_certificates,
      count(*) FILTER (
        WHERE status IN ('ACTIVE','ROTATING')
          AND valid_until > clock_timestamp()
          AND valid_until <= clock_timestamp() + make_interval(days => :warning_days)
      ) AS expiring_certificates,
      min(valid_until) FILTER (WHERE status IN ('ACTIVE','ROTATING'))
        AS nearest_certificate_expiry
    FROM federation.node_certificates
    """
)


def _load_status(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_STATUS_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _status_rank(status: CheckStatus) -> int:
    return {"OK": 0, "UNKNOWN": 1, "WARNING": 2, "CRITICAL": 3}[status]


def _worst(*statuses: CheckStatus) -> CheckStatus:
    return max(statuses, key=_status_rank)


def _disk_check(
    settings: Settings,
    *,
    now: datetime,
    usage: DiskUsage,
    host_probe: dict[str, Any] | None,
) -> HostCheck:
    free_percent = 0 if usage.total <= 0 else (usage.free * 100) // usage.total
    status: CheckStatus = "OK"
    code = "DISK_OK"
    if free_percent <= settings.disk_critical_percent:
        status, code = "CRITICAL", "DISK_CRITICAL"
    elif free_percent <= settings.disk_warning_percent:
        status, code = "WARNING", "DISK_LOW"

    metrics: dict[str, int | str | bool | None] = {
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "free_percent": free_percent,
    }
    if host_probe is not None:
        host_free = host_probe.get("disk_free_bytes")
        host_total = host_probe.get("disk_total_bytes")
        if (
            isinstance(host_free, int)
            and not isinstance(host_free, bool)
            and isinstance(host_total, int)
            and not isinstance(host_total, bool)
            and host_total > 0
            and 0 <= host_free <= host_total
        ):
            host_percent = (host_free * 100) // host_total
            metrics.update(
                {
                    "host_free_bytes": host_free,
                    "host_total_bytes": host_total,
                    "host_free_percent": host_percent,
                }
            )
            if host_percent <= settings.disk_critical_percent:
                status, code = "CRITICAL", "HOST_DISK_CRITICAL"
            elif (
                host_percent <= settings.disk_warning_percent
                and _status_rank(status) < _status_rank("WARNING")
            ):
                status, code = "WARNING", "HOST_DISK_LOW"
    return HostCheck("storage", status, code, now, metrics)


def _clock_check(
    settings: Settings,
    *,
    now: datetime,
    facts: InfrastructureFacts,
    host_probe: dict[str, Any] | None,
) -> HostCheck:
    drift_seconds = int(abs((now - facts.database_time).total_seconds()))
    sync_status = host_probe.get("clock_status") if host_probe is not None else None
    status: CheckStatus = "OK"
    code = "CLOCK_OK"
    if drift_seconds >= settings.clock_drift_critical_seconds or sync_status == "UNSYNCED":
        status, code = "CRITICAL", "CLOCK_UNSAFE"
    elif drift_seconds >= settings.clock_drift_warning_seconds:
        status, code = "WARNING", "CLOCK_DRIFT"
    elif sync_status not in {"SYNCED", "CONFIGURED"}:
        status, code = "UNKNOWN", "CLOCK_SYNC_UNKNOWN"
    return HostCheck(
        "clock",
        status,
        code,
        now,
        {
            "database_drift_seconds": drift_seconds,
            "host_clock_status": sync_status if isinstance(sync_status, str) else "UNKNOWN",
        },
    )


def _backup_check(
    settings: Settings,
    *,
    now: datetime,
    backup_status: dict[str, Any] | None,
) -> HostCheck:
    hardened = settings.environment in HARDENED_ENVIRONMENTS
    if backup_status is None:
        return HostCheck(
            "backup",
            "WARNING" if hardened else "UNKNOWN",
            "BACKUP_STATUS_MISSING",
            now,
            {"age_hours": None, "backup_kind": None},
        )
    completed_at = _timestamp(backup_status.get("completed_at"))
    kind = backup_status.get("backup_kind")
    if (
        backup_status.get("format") != BACKUP_STATUS_FORMAT
        or completed_at is None
        or completed_at > now
        or kind not in {"FULL", "DATA_ONLY"}
    ):
        return HostCheck(
            "backup",
            "CRITICAL",
            "BACKUP_STATUS_INVALID",
            now,
            {"age_hours": None, "backup_kind": None},
        )
    age_hours = int((now - completed_at).total_seconds() // 3600)
    status: CheckStatus = "OK"
    code = "BACKUP_OK"
    if kind != "FULL":
        status = "CRITICAL" if hardened else "WARNING"
        code = "BACKUP_DATA_ONLY"
    elif age_hours >= settings.backup_critical_hours:
        status, code = "CRITICAL", "BACKUP_OVERDUE"
    elif age_hours >= settings.backup_warning_hours:
        status, code = "WARNING", "BACKUP_AGING"
    return HostCheck(
        "backup",
        status,
        code,
        completed_at,
        {"age_hours": age_hours, "backup_kind": str(kind)},
    )


def _certificate_check(
    settings: Settings,
    *,
    now: datetime,
    facts: InfrastructureFacts,
) -> HostCheck:
    nearest_days = (
        None
        if facts.nearest_certificate_expiry is None
        else int((facts.nearest_certificate_expiry - now).total_seconds() // 86_400)
    )
    status: CheckStatus = "OK"
    code = "CERTIFICATES_OK"
    if facts.expired_certificates:
        status, code = "CRITICAL", "CERTIFICATE_EXPIRED"
    elif nearest_days is not None and nearest_days <= settings.certificate_critical_days:
        status, code = "CRITICAL", "CERTIFICATE_EXPIRING"
    elif facts.expiring_certificates:
        status, code = "WARNING", "CERTIFICATE_RENEWAL_DUE"
    return HostCheck(
        "certificates",
        status,
        code,
        now,
        {
            "active": facts.active_certificates,
            "expired": facts.expired_certificates,
            "expiring": facts.expiring_certificates,
            "nearest_expiry_days": nearest_days,
        },
    )


def _ups_check(
    settings: Settings,
    *,
    now: datetime,
    host_probe: dict[str, Any] | None,
) -> HostCheck:
    value = host_probe.get("ups_status") if host_probe is not None else None
    hardened = settings.environment in HARDENED_ENVIRONMENTS
    mapping: dict[object, tuple[CheckStatus, str]] = {
        "ONLINE": ("OK", "UPS_ONLINE"),
        "ON_BATTERY": ("WARNING", "UPS_ON_BATTERY"),
        "LOW_BATTERY": ("CRITICAL", "UPS_LOW_BATTERY"),
        "NOT_CONFIGURED": (
            "WARNING" if hardened else "UNKNOWN",
            "UPS_NOT_CONFIGURED",
        ),
        "UNKNOWN": ("WARNING" if hardened else "UNKNOWN", "UPS_UNKNOWN"),
    }
    status, code = mapping.get(
        value,
        ("WARNING" if hardened else "UNKNOWN", "UPS_PROBE_MISSING"),
    )
    return HostCheck(
        "ups",
        status,
        code,
        now,
        {"ups_status": value if isinstance(value, str) else "UNKNOWN"},
    )


def build_host_readiness(
    settings: Settings,
    *,
    now: datetime,
    usage: DiskUsage,
    facts: InfrastructureFacts,
    backup_status: dict[str, Any] | None,
    host_probe: dict[str, Any] | None,
) -> HostReadiness:
    probe_time = _timestamp(host_probe.get("generated_at")) if host_probe else None
    if (
        host_probe is None
        or host_probe.get("format") != HOST_PROBE_FORMAT
        or probe_time is None
        or probe_time > now
        or (now - probe_time).total_seconds() > settings.host_probe_stale_seconds
    ):
        host_probe = None

    checks = (
        _disk_check(settings, now=now, usage=usage, host_probe=host_probe),
        _clock_check(settings, now=now, facts=facts, host_probe=host_probe),
        _backup_check(settings, now=now, backup_status=backup_status),
        _certificate_check(settings, now=now, facts=facts),
        _ups_check(settings, now=now, host_probe=host_probe),
    )
    worst = _worst(*(check.status for check in checks))
    overall: Literal["OPERATIONAL", "ATTENTION", "CRITICAL"]
    if worst == "CRITICAL":
        overall = "CRITICAL"
    elif worst == "OK":
        overall = "OPERATIONAL"
    else:
        overall = "ATTENTION"
    return HostReadiness(generated_at=now, status=overall, checks=checks)


class GetHostReadiness:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    async def execute(self) -> HostReadiness:
        async with self.database.session() as session:
            row = (
                await session.execute(
                    FACTS_QUERY,
                    {"warning_days": self.settings.certificate_warning_days},
                )
            ).mappings().one()
        now = datetime.now(UTC)
        usage_value = shutil.disk_usage(self.settings.blob_root)
        facts = InfrastructureFacts(
            database_time=row["database_time"],
            active_certificates=int(row["active_certificates"]),
            expired_certificates=int(row["expired_certificates"]),
            expiring_certificates=int(row["expiring_certificates"]),
            nearest_certificate_expiry=row["nearest_certificate_expiry"],
        )
        root = self.settings.operations_state_root
        return build_host_readiness(
            self.settings,
            now=now,
            usage=DiskUsage(*usage_value),
            facts=facts,
            backup_status=_load_status(root / "backup-status.json"),
            host_probe=_load_status(root / "host-probe.json"),
        )


def readiness_metrics(readiness: HostReadiness) -> str:
    status = readiness.status.lower()
    lines = [
        "# HELP coop_host_readiness Current local host readiness state.",
        "# TYPE coop_host_readiness gauge",
        f'coop_host_readiness{{status="{status}"}} 1',
        "# HELP coop_host_check_severity Local host check severity from 0 to 3.",
        "# TYPE coop_host_check_severity gauge",
    ]
    severity = {"OK": 0, "UNKNOWN": 1, "WARNING": 2, "CRITICAL": 3}
    for check in readiness.checks:
        lines.append(
            f'coop_host_check_severity{{name="{check.name}"}} {severity[check.status]}'
        )
    return "\n".join(lines) + "\n"


def readiness_payload(readiness: HostReadiness) -> dict[str, object]:
    return {
        "generated_at": readiness.generated_at.isoformat(),
        "status": readiness.status,
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "code": check.code,
                "observed_at": check.observed_at.isoformat(),
                "metrics": check.metrics,
            }
            for check in readiness.checks
        ],
    }
