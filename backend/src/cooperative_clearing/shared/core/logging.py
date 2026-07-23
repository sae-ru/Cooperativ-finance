"""Structured local logging without secret or PII interpolation."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from cooperative_clearing.shared.core.request_context import get_request_id


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service: str, release: str, node_id: str) -> None:
        super().__init__()
        self.service = service
        self.release = release
        self.node_id = node_id

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", self.service),
            "release": getattr(record, "release", self.release),
            "node_id": getattr(record, "node_id", self.node_id),
            "request_id": get_request_id(),
            "event": record.getMessage(),
        }
        fields = getattr(record, "event_fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(
    *, level: str, service: str, release: str = "unknown", node_id: str = "unavailable"
) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service=service, release=release, node_id=node_id))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def event_fields(**fields: object) -> dict[str, object]:
    return {"event_fields": fields}
