"""Read-only HTTP capacity smoke for a deployed local node."""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import uuid4

SAFE_ENDPOINTS = {"/health/live", "/api/v1/system/status"}


@dataclass(frozen=True, slots=True)
class Sample:
    status_code: int
    latency_ms: float
    error_class: str | None


@dataclass(frozen=True, slots=True)
class CapacityReport:
    generated_at: str
    endpoint: str
    requests: int
    concurrency: int
    duration_seconds: float
    requests_per_second: float
    successes: int
    errors: int
    error_rate: float
    latency_min_ms: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_max_ms: float
    thresholds: dict[str, float]
    passed: bool
    failures: list[str]


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile_value / 100.0) * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def build_report(
    *,
    endpoint: str,
    concurrency: int,
    samples: list[Sample],
    duration_seconds: float,
    max_error_rate: float,
    max_p95_ms: float,
    min_rps: float,
    host_header: str | None = None,
) -> CapacityReport:
    latencies = [sample.latency_ms for sample in samples]
    errors = sum(sample.error_class is not None or sample.status_code >= 400 for sample in samples)
    requests = len(samples)
    successes = requests - errors
    error_rate = errors / requests if requests else 1.0
    rps = requests / duration_seconds if duration_seconds > 0 else 0.0
    p95 = percentile(latencies, 95)
    failures: list[str] = []
    if error_rate > max_error_rate:
        failures.append("ERROR_RATE_EXCEEDED")
    if p95 > max_p95_ms:
        failures.append("P95_LATENCY_EXCEEDED")
    if rps < min_rps:
        failures.append("MIN_RPS_NOT_REACHED")
    return CapacityReport(
        generated_at=datetime.now(UTC).isoformat(),
        endpoint=endpoint,
        requests=requests,
        concurrency=concurrency,
        duration_seconds=round(duration_seconds, 6),
        requests_per_second=round(rps, 3),
        successes=successes,
        errors=errors,
        error_rate=round(error_rate, 6),
        latency_min_ms=round(min(latencies, default=0.0), 3),
        latency_p50_ms=round(percentile(latencies, 50), 3),
        latency_p95_ms=round(p95, 3),
        latency_p99_ms=round(percentile(latencies, 99), 3),
        latency_max_ms=round(max(latencies, default=0.0), 3),
        thresholds={
            "max_error_rate": max_error_rate,
            "max_p95_ms": max_p95_ms,
            "min_rps": min_rps,
        },
        passed=not failures,
        failures=failures,
    )


def _request(url: str, timeout_seconds: float, host_header: str | None = None) -> Sample:
    started = time.perf_counter()
    headers = {"Accept": "application/json", "X-Request-ID": str(uuid4())}
    if host_header:
        headers["Host"] = host_header
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response.read(1024)
            status_code = response.status
        error_class = None if status_code < 400 else "HTTP_ERROR"
    except urllib.error.HTTPError as error:
        status_code = error.code
        error_class = "HTTP_ERROR"
    except (TimeoutError, urllib.error.URLError):
        status_code = 0
        error_class = "TRANSPORT_ERROR"
    return Sample(
        status_code=status_code,
        latency_ms=(time.perf_counter() - started) * 1000,
        error_class=error_class,
    )


def run(
    *,
    base_url: str,
    endpoint: str,
    requests: int,
    concurrency: int,
    timeout_seconds: float,
    max_error_rate: float,
    max_p95_ms: float,
    min_rps: float,
    host_header: str | None = None,
) -> CapacityReport:
    url = base_url.rstrip("/") + endpoint
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="capacity") as pool:
        samples = list(
            pool.map(lambda _: _request(url, timeout_seconds, host_header), range(requests))
        )
    duration = time.perf_counter() - started
    return build_report(
        endpoint=endpoint,
        concurrency=concurrency,
        samples=samples,
        duration_seconds=duration,
        max_error_rate=max_error_rate,
        max_p95_ms=max_p95_ms,
        min_rps=min_rps,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--endpoint", choices=sorted(SAFE_ENDPOINTS), default="/health/live")
    parser.add_argument("--host-header")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--max-error-rate", type=float, default=0.0)
    parser.add_argument("--max-p95-ms", type=float, default=250.0)
    parser.add_argument("--min-rps", type=float, default=10.0)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.concurrency > args.requests:
        raise SystemExit("requests and concurrency must be positive; concurrency <= requests")
    if args.timeout_seconds <= 0 or not 0 <= args.max_error_rate <= 1:
        raise SystemExit("timeout must be positive and error rate must be between 0 and 1")
    if args.max_p95_ms <= 0 or args.min_rps < 0:
        raise SystemExit("latency must be positive and minimum RPS must be non-negative")
    report = run(
        base_url=args.base_url,
        endpoint=args.endpoint,
        requests=args.requests,
        concurrency=args.concurrency,
        timeout_seconds=args.timeout_seconds,
        max_error_rate=args.max_error_rate,
        max_p95_ms=args.max_p95_ms,
        min_rps=args.min_rps,
        host_header=args.host_header,
    )
    print(json.dumps(asdict(report), ensure_ascii=True, separators=(",", ":")))
    raise SystemExit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
