from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

SCRIPT = Path(__file__).resolve().parents[1] / "local_observability_probe.py"
SPEC = importlib.util.spec_from_file_location("local_observability_probe", SCRIPT)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self.body = body
        self.status = 200
        self.headers = {"Content-Type": content_type}

    def read(self, maximum: int) -> bytes:
        return self.body[:maximum]

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class FakeOpener:
    def __init__(self, routes: dict[tuple[str, str], FakeResponse]) -> None:
        self.routes = routes

    def __call__(self, request: object, *, timeout: int) -> FakeResponse:
        del timeout
        method = request.get_method()
        path = urlsplit(request.full_url).path
        return self.routes[(method, path)]


def response(value: dict[str, object]) -> FakeResponse:
    return FakeResponse(json.dumps(value).encode("utf-8"))


class LocalObservabilityProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="local-observability-")
        self.root = Path(self.temporary.name)
        self.network = self.root / "network.json"
        self.logs = self.root / "runtime.log"
        self.password = "Local-observability-test-2026!"
        self.network.write_text(
            json.dumps(
                {
                    "format": probe.NETWORK_FORMAT,
                    "networks": {name: True for name in probe.REQUIRED_NETWORKS},
                    "egress_probe": "BLOCKED",
                }
            ),
            encoding="utf-8",
        )
        self.logs.write_text("api request completed\ngateway local request\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def routes(self) -> dict[tuple[str, str], FakeResponse]:
        checks = [
            {"name": name, "status": "OK", "code": "OK", "metrics": {}}
            for name in ("storage", "clock", "backup", "certificates", "ups")
        ]
        metrics = "\n".join(
            [
                'coop_build_info{node="test",release="test"} 1',
                'coop_http_requests_total{route="/health/live"} 1',
                'coop_operational_records{kind="signed_events"} 17',
                'coop_host_check_severity{name="storage"} 0',
                "",
            ]
        ).encode("utf-8")
        return {
            ("GET", "/health/live"): response({"status": "LIVE"}),
            ("GET", "/health/ready"): response({"status": "READY"}),
            ("POST", "/api/v1/auth/login"): response(
                {
                    "data": {
                        "access_token": "local-access-token-value",
                        "principal": {"must_change_password": False},
                    }
                }
            ),
            ("GET", "/api/v1/operations/snapshot"): response(
                {
                    "data": {
                        "schema_revision": "0039_participant_address_events",
                        "signed_events": 17,
                    }
                }
            ),
            ("GET", "/api/v1/operations/host-readiness"): response(
                {"data": {"status": "ATTENTION", "checks": checks}}
            ),
            ("GET", "/api/v1/operations/metrics"): FakeResponse(
                metrics, "text/plain; version=0.0.4"
            ),
        }

    def run_probe(
        self, routes: dict[tuple[str, str], FakeResponse] | None = None
    ) -> dict[str, object]:
        return probe.run_probe(
            base_url="http://127.0.0.1:18088",
            login="security",
            password=self.password,
            expected_schema="0039_participant_address_events",
            network_evidence=self.network,
            logs=self.logs,
            opener=FakeOpener(routes or self.routes()),
        )

    def test_probe_reports_local_contract_without_password_or_token(self) -> None:
        report = self.run_probe()
        encoded = json.dumps(report)
        self.assertEqual(report["status"], "PASSED")
        self.assertEqual(report["telemetry_export"], "DISABLED")
        self.assertEqual(report["signed_events"], 17)
        self.assertNotIn(self.password, encoded)
        self.assertNotIn("local-access-token-value", encoded)

    def test_probe_rotates_bootstrap_password_without_reporting_secrets(self) -> None:
        routes = self.routes()
        routes[("POST", "/api/v1/auth/login")] = response(
            {
                "data": {
                    "access_token": "bootstrap-access-token-value",
                    "principal": {"must_change_password": True},
                }
            }
        )
        routes[("POST", "/api/v1/auth/change-password")] = response(
            {
                "data": {
                    "access_token": "rotated-access-token-value",
                    "principal": {"must_change_password": False},
                }
            }
        )
        rotated = "Rotated-local-observability-password-2026!"
        report = probe.run_probe(
            base_url="http://127.0.0.1:18088",
            login="security",
            password=self.password,
            expected_schema="0039_participant_address_events",
            network_evidence=self.network,
            logs=self.logs,
            opener=FakeOpener(routes),
            new_password_factory=lambda: rotated,
        )
        encoded = json.dumps(report)
        self.assertTrue(report["bootstrap_password_rotated"])
        self.assertNotIn(self.password, encoded)
        self.assertNotIn(rotated, encoded)

    def test_probe_rejects_non_loopback_origin(self) -> None:
        with self.assertRaisesRegex(probe.ObservabilityError, "loopback"):
            probe.run_probe(
                base_url="https://telemetry.example.test",
                login="security",
                password=self.password,
                expected_schema="0039_participant_address_events",
                network_evidence=self.network,
                logs=self.logs,
                opener=FakeOpener(self.routes()),
            )

    def test_probe_accepts_explicit_internal_operator_host(self) -> None:
        report = probe.run_probe(
            base_url="http://gateway:8080",
            login="security",
            password=self.password,
            expected_schema="0039_participant_address_events",
            network_evidence=self.network,
            logs=self.logs,
            opener=FakeOpener(self.routes()),
            allowed_hosts={"gateway"},
        )
        self.assertEqual(report["local_origin"], "http://gateway:8080")

    def test_probe_rejects_non_internal_network(self) -> None:
        value = json.loads(self.network.read_text(encoding="utf-8"))
        value["networks"]["edge"] = False
        self.network.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(probe.ObservabilityError, "egress"):
            self.run_probe()

    def test_probe_rejects_missing_metric_family(self) -> None:
        routes = self.routes()
        routes[("GET", "/api/v1/operations/metrics")] = FakeResponse(
            b"coop_build_info 1\n", "text/plain"
        )
        with self.assertRaisesRegex(probe.ObservabilityError, "metrics"):
            self.run_probe(routes)

    def test_probe_rejects_password_in_logs(self) -> None:
        self.logs.write_text(f"api {self.password}\ngateway request\n", encoding="utf-8")
        with self.assertRaisesRegex(probe.ObservabilityError, "password leaked"):
            self.run_probe()

    def test_probe_rejects_rotated_password_in_logs(self) -> None:
        rotated = "Rotated-local-observability-password-2026!"
        self.logs.write_text(f"api {rotated}\ngateway request\n", encoding="utf-8")
        routes = self.routes()
        routes[("POST", "/api/v1/auth/login")] = response(
            {
                "data": {
                    "access_token": "bootstrap-access-token-value",
                    "principal": {"must_change_password": True},
                }
            }
        )
        routes[("POST", "/api/v1/auth/change-password")] = response(
            {
                "data": {
                    "access_token": "rotated-access-token-value",
                    "principal": {"must_change_password": False},
                }
            }
        )
        with self.assertRaisesRegex(probe.ObservabilityError, "password leaked"):
            probe.run_probe(
                base_url="http://127.0.0.1:18088",
                login="security",
                password=self.password,
                expected_schema="0039_participant_address_events",
                network_evidence=self.network,
                logs=self.logs,
                opener=FakeOpener(routes),
                new_password_factory=lambda: rotated,
            )

    def test_password_file_must_be_single_line(self) -> None:
        path = self.root / "password"
        path.write_text("first-password-value\nsecond-password-value\n", encoding="utf-8")
        with self.assertRaisesRegex(probe.ObservabilityError, "invalid"):
            probe.read_password(path)


if __name__ == "__main__":
    unittest.main()