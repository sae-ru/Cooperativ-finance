from typing import Any

from cooperative_clearing.tools.capacity import _request


class _Response:
    status = 200

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return b"{}"


def test_request_can_override_host_header(monkeypatch: Any) -> None:
    captured: dict[str, str | None] = {}

    def fake_urlopen(request: Any, *, timeout: float) -> _Response:
        captured["host"] = request.get_header("Host")
        captured["timeout"] = str(timeout)
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    sample = _request("http://example.test/health/live", 2.5, "127.0.0.1")

    assert sample.status_code == 200
    assert sample.error_class is None
    assert captured == {"host": "127.0.0.1", "timeout": "2.5"}
