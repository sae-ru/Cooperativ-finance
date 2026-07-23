"""Bounded process metrics without user-controlled label cardinality."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import Lock

DEFAULT_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


@dataclass(frozen=True, slots=True)
class RequestMetricKey:
    method: str
    route: str
    status_class: str


class RequestMetrics:
    def __init__(self, buckets: tuple[float, ...] = DEFAULT_DURATION_BUCKETS) -> None:
        if not buckets or tuple(sorted(set(buckets))) != buckets:
            raise ValueError("duration buckets must be non-empty, unique, and sorted")
        self._buckets = buckets
        self._requests: dict[RequestMetricKey, int] = defaultdict(int)
        self._duration_sum: dict[RequestMetricKey, float] = defaultdict(float)
        self._duration_buckets: dict[tuple[RequestMetricKey, float], int] = defaultdict(int)
        self._lock = Lock()

    def observe(
        self, *, method: str, route: str, status_code: int, duration_seconds: float
    ) -> None:
        key = RequestMetricKey(
            method=method.upper()[:16],
            route=_bounded_route(route),
            status_class=f"{max(0, min(status_code, 999)) // 100}xx",
        )
        duration = max(0.0, duration_seconds)
        with self._lock:
            self._requests[key] += 1
            self._duration_sum[key] += duration
            for bucket in self._buckets:
                if duration <= bucket:
                    self._duration_buckets[(key, bucket)] += 1

    def render_prometheus(self, *, release: str, node_code: str) -> str:
        with self._lock:
            requests = dict(self._requests)
            duration_sum = dict(self._duration_sum)
            duration_buckets = dict(self._duration_buckets)

        lines = [
            "# HELP coop_build_info Static process build information.",
            "# TYPE coop_build_info gauge",
            f'coop_build_info{{node="{_escape(node_code)}",release="{_escape(release)}"}} 1',
            "# HELP coop_http_requests_total Completed HTTP requests.",
            "# TYPE coop_http_requests_total counter",
        ]
        for key in sorted(requests, key=_sort_key):
            labels = _labels(key)
            lines.append(f"coop_http_requests_total{{{labels}}} {requests[key]}")

        lines.extend(
            [
                "# HELP coop_http_request_duration_seconds HTTP request duration.",
                "# TYPE coop_http_request_duration_seconds histogram",
            ]
        )
        for key in sorted(requests, key=_sort_key):
            labels = _labels(key)
            for bucket in self._buckets:
                count = duration_buckets.get((key, bucket), 0)
                lines.append(
                    f'coop_http_request_duration_seconds_bucket{{{labels},le="{bucket:g}"}} {count}'
                )
            lines.append(
                f'coop_http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {requests[key]}'
            )
            lines.append(
                f"coop_http_request_duration_seconds_sum{{{labels}}} "
                f"{duration_sum.get(key, 0.0):.9f}"
            )
            lines.append(f"coop_http_request_duration_seconds_count{{{labels}}} {requests[key]}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()
            self._duration_sum.clear()
            self._duration_buckets.clear()


def _bounded_route(route: str) -> str:
    value = route.strip() or "unmatched"
    if len(value) > 200:
        return "oversized-route"
    return value


def _sort_key(key: RequestMetricKey) -> tuple[str, str, str]:
    return key.route, key.method, key.status_class


def _labels(key: RequestMetricKey) -> str:
    return (
        f'method="{_escape(key.method)}",route="{_escape(key.route)}",'
        f'status_class="{_escape(key.status_class)}"'
    )


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


request_metrics = RequestMetrics()
