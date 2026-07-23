import json
import logging

from cooperative_clearing.shared.core.logging import JsonFormatter


def test_json_formatter_uses_configured_service() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="node_started",
        args=(),
        exc_info=None,
    )

    payload = json.loads(
        JsonFormatter(service="worker", release="test-release", node_id="node-test").format(record)
    )

    assert payload["service"] == "worker"
    assert payload["event"] == "node_started"
    assert payload["release"] == "test-release"
    assert payload["node_id"] == "node-test"
