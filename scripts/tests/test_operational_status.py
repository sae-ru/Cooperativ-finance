from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "operational_status.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("operational_status", SCRIPT)
assert SPEC and SPEC.loader
operational_status = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = operational_status
SPEC.loader.exec_module(operational_status)


class OperationalStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="operational-status-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_probe_writes_only_bounded_host_signals(self) -> None:
        with patch.dict(
            operational_status.os.environ,
            {
                "COOP_HOST_CLOCK_STATUS": "SYNCED",
                "COOP_UPS_STATUS": "ONLINE",
            },
            clear=False,
        ):
            output = operational_status.write_host_probe(
                self.root,
                now=datetime(2026, 7, 27, 12, tzinfo=UTC),
            )

        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["format"], operational_status.HOST_PROBE_FORMAT)
        self.assertEqual(payload["clock_status"], "SYNCED")
        self.assertEqual(payload["ups_status"], "ONLINE")
        self.assertGreater(payload["disk_total_bytes"], 0)
        self.assertNotIn(str(self.root), output.read_text(encoding="utf-8"))

    def test_probe_reads_local_env_with_non_empty_process_overrides(self) -> None:
        (self.root / ".env").write_text(
            "COOP_HOST_CLOCK_STATUS=SYNCED\nCOOP_UPS_STATUS=ONLINE\n",
            encoding="utf-8",
        )
        with patch.dict(operational_status.os.environ, {}, clear=True):
            output = operational_status.write_host_probe(
                self.root,
                now=datetime(2026, 7, 27, 12, tzinfo=UTC),
            )
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["clock_status"], "SYNCED")
        self.assertEqual(payload["ups_status"], "ONLINE")

        values = operational_status.probe_environment(
            self.root,
            {"COOP_UPS_STATUS": "LOW_BATTERY", "COOP_HOST_CLOCK_STATUS": ""},
        )
        self.assertEqual(values["COOP_HOST_CLOCK_STATUS"], "SYNCED")
        self.assertEqual(values["COOP_UPS_STATUS"], "LOW_BATTERY")

    def test_completed_backup_is_recorded_without_source_paths(self) -> None:
        backup = self.root / "backups" / "node-20260727T120000Z"
        backup.mkdir(parents=True)
        (backup / "manifest.env").write_text(
            "format=cooperative-clearing-backup-v1\n"
            "backup_id=node-20260727T120000Z\n"
            "backup_kind=FULL\n"
            "release=1.2.3\n",
            encoding="utf-8",
        )
        (backup / "COMPLETE").write_text("20260727T120000Z\n", encoding="ascii")
        (backup / "SHA256SUMS").write_text(
            "0" * 64 + "  database.dump\n",
            encoding="ascii",
        )

        output = operational_status.record_backup(self.root, backup)
        payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(payload["format"], operational_status.BACKUP_STATUS_FORMAT)
        self.assertEqual(payload["backup_kind"], "FULL")
        self.assertEqual(payload["release"], "1.2.3")
        self.assertNotIn(str(backup), output.read_text(encoding="utf-8"))

    def test_probe_loop_stops_after_requested_iteration(self) -> None:
        stop_event = Mock()
        stop_event.is_set.side_effect = [False, True]
        with patch.object(operational_status, "write_host_probe") as write_probe:
            operational_status.run_probe_loop(
                self.root,
                interval_seconds=60,
                stop_event=stop_event,
                monitor_id="monitor-01",
            )

        write_probe.assert_called_once_with(self.root, monitor_id="monitor-01")
        stop_event.wait.assert_called_once_with(60)

    def test_start_probe_reuses_a_fresh_owned_monitor(self) -> None:
        operations = self.root / ".operations"
        operations.mkdir()
        monitor_id = "monitor-01"
        now = datetime.now(UTC)
        (operations / "host-probe-monitor.json").write_text(
            json.dumps({"pid": 4242, "monitor_id": monitor_id}),
            encoding="utf-8",
        )
        (operations / "host-probe.json").write_text(
            json.dumps(
                {
                    "generated_at": now.isoformat(),
                    "monitor_id": monitor_id,
                }
            ),
            encoding="utf-8",
        )

        with patch.object(operational_status, "_process_exists", return_value=True):
            output, started = operational_status.start_probe_monitor(
                self.root,
                interval_seconds=60,
            )

        self.assertFalse(started)
        self.assertEqual(output, operations / "host-probe-monitor.json")

    def test_stop_probe_requires_and_uses_fresh_ownership_evidence(self) -> None:
        operations = self.root / ".operations"
        operations.mkdir()
        monitor_id = "monitor-01"
        (operations / "host-probe-monitor.json").write_text(
            json.dumps(
                {
                    "pid": 4242,
                    "monitor_id": monitor_id,
                    "interval_seconds": 60,
                }
            ),
            encoding="utf-8",
        )
        (operations / "host-probe.json").write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(UTC).isoformat(),
                    "monitor_id": monitor_id,
                }
            ),
            encoding="utf-8",
        )

        with (
            patch.object(
                operational_status,
                "_process_exists",
                side_effect=[True, False, False],
            ),
            patch.object(operational_status, "_terminate_process") as terminate,
        ):
            output, stopped = operational_status.stop_probe_monitor(self.root)

        self.assertTrue(stopped)
        self.assertEqual(output, operations / "host-probe-monitor.json")
        terminate.assert_called_once_with(4242)
        self.assertFalse(output.exists())

    def test_stop_probe_rejects_an_unowned_pid(self) -> None:
        operations = self.root / ".operations"
        operations.mkdir()
        (operations / "host-probe-monitor.json").write_text(
            json.dumps(
                {"pid": 4242, "monitor_id": "monitor-01", "interval_seconds": 60}
            ),
            encoding="utf-8",
        )
        (operations / "host-probe.json").write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(UTC).isoformat(),
                    "monitor_id": "different-monitor",
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(operational_status.StatusError, "ownership"):
            operational_status.stop_probe_monitor(self.root)

    def test_incomplete_backup_is_rejected(self) -> None:
        backup = self.root / "backup"
        backup.mkdir()
        with self.assertRaisesRegex(operational_status.StatusError, "incomplete"):
            operational_status.record_backup(self.root, backup)


if __name__ == "__main__":
    unittest.main()
