from cooperative_clearing.tools.capacity import Sample, build_report, percentile


def test_percentile_uses_nearest_rank() -> None:
    assert percentile([], 95) == 0
    assert percentile([50, 10, 20, 30, 40], 50) == 30
    assert percentile([50, 10, 20, 30, 40], 95) == 50


def test_capacity_report_enforces_all_thresholds() -> None:
    report = build_report(
        endpoint="/health/live",
        concurrency=2,
        samples=[
            Sample(status_code=200, latency_ms=10, error_class=None),
            Sample(status_code=503, latency_ms=300, error_class="HTTP_ERROR"),
        ],
        duration_seconds=1,
        max_error_rate=0,
        max_p95_ms=250,
        min_rps=3,
    )

    assert report.passed is False
    assert report.successes == 1
    assert report.error_rate == 0.5
    assert report.latency_p50_ms == 10
    assert report.latency_p95_ms == 300
    assert report.failures == [
        "ERROR_RATE_EXCEEDED",
        "P95_LATENCY_EXCEEDED",
        "MIN_RPS_NOT_REACHED",
    ]
