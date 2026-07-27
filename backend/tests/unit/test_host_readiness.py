from datetime import UTC, datetime, timedelta

from cooperative_clearing.modules.operations.application.readiness import (
    BACKUP_STATUS_FORMAT,
    HOST_PROBE_FORMAT,
    DiskUsage,
    InfrastructureFacts,
    build_host_readiness,
    readiness_metrics,
)
from cooperative_clearing.shared.core.config import Environment, Settings


def facts(now: datetime, *, nearest_days: int = 90, expired: int = 0) -> InfrastructureFacts:
    return InfrastructureFacts(
        database_time=now - timedelta(seconds=1),
        active_certificates=2,
        expired_certificates=expired,
        expiring_certificates=0,
        nearest_certificate_expiry=now + timedelta(days=nearest_days),
    )


def test_host_readiness_is_operational_for_fresh_local_evidence() -> None:
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    readiness = build_host_readiness(
        Settings(),
        now=now,
        usage=DiskUsage(total=1_000, used=400, free=600),
        facts=facts(now),
        backup_status={
            "format": BACKUP_STATUS_FORMAT,
            "completed_at": (now - timedelta(hours=2)).isoformat(),
            "backup_kind": "FULL",
        },
        host_probe={
            "format": HOST_PROBE_FORMAT,
            "generated_at": (now - timedelta(seconds=30)).isoformat(),
            "clock_status": "SYNCED",
            "ups_status": "ONLINE",
            "disk_total_bytes": 2_000,
            "disk_free_bytes": 1_000,
        },
    )

    assert readiness.status == "OPERATIONAL"
    assert {check.name for check in readiness.checks} == {
        "storage",
        "clock",
        "backup",
        "certificates",
        "ups",
    }
    assert all(check.status == "OK" for check in readiness.checks)
    metrics = readiness_metrics(readiness)
    assert 'coop_host_readiness{status="operational"} 1' in metrics
    assert 'coop_host_check_severity{name="storage"} 0' in metrics


def test_hardened_host_readiness_fails_closed_on_critical_signals() -> None:
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    settings = Settings(
        environment=Environment.PRODUCTION,
        demo_data_enabled=False,
    )
    critical_facts = facts(now, nearest_days=-1, expired=1)
    critical_facts = InfrastructureFacts(
        database_time=now - timedelta(minutes=2),
        active_certificates=critical_facts.active_certificates,
        expired_certificates=critical_facts.expired_certificates,
        expiring_certificates=critical_facts.expiring_certificates,
        nearest_certificate_expiry=critical_facts.nearest_certificate_expiry,
    )
    readiness = build_host_readiness(
        settings,
        now=now,
        usage=DiskUsage(total=10_000, used=9_800, free=200),
        facts=critical_facts,
        backup_status={
            "format": BACKUP_STATUS_FORMAT,
            "completed_at": (now - timedelta(hours=1)).isoformat(),
            "backup_kind": "DATA_ONLY",
        },
        host_probe={
            "format": HOST_PROBE_FORMAT,
            "generated_at": now.isoformat(),
            "clock_status": "UNSYNCED",
            "ups_status": "LOW_BATTERY",
            "disk_total_bytes": 10_000,
            "disk_free_bytes": 100,
        },
    )

    assert readiness.status == "CRITICAL"
    assert {check.code for check in readiness.checks} == {
        "HOST_DISK_CRITICAL",
        "CLOCK_UNSAFE",
        "BACKUP_DATA_ONLY",
        "CERTIFICATE_EXPIRED",
        "UPS_LOW_BATTERY",
    }


def test_stale_host_probe_is_not_reported_as_current() -> None:
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    readiness = build_host_readiness(
        Settings(host_probe_stale_seconds=60),
        now=now,
        usage=DiskUsage(total=1_000, used=100, free=900),
        facts=facts(now),
        backup_status=None,
        host_probe={
            "format": HOST_PROBE_FORMAT,
            "generated_at": (now - timedelta(minutes=5)).isoformat(),
            "clock_status": "SYNCED",
            "ups_status": "ONLINE",
            "disk_total_bytes": 1_000,
            "disk_free_bytes": 900,
        },
    )

    by_name = {check.name: check for check in readiness.checks}
    assert readiness.status == "ATTENTION"
    assert by_name["clock"].code == "CLOCK_SYNC_UNKNOWN"
    assert by_name["ups"].code == "UPS_PROBE_MISSING"
    assert "host_free_percent" not in by_name["storage"].metrics
