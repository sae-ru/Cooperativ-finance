from cooperative_clearing.shared.core.metrics import RequestMetrics


def test_request_metrics_are_bounded_and_render_prometheus_histograms() -> None:
    metrics = RequestMetrics((0.1, 1.0))
    metrics.observe(
        method="get",
        route='/api/v1/items/{item_id}"\nunsafe',
        status_code=201,
        duration_seconds=0.25,
    )
    metrics.observe(
        method="GET",
        route='/api/v1/items/{item_id}"\nunsafe',
        status_code=503,
        duration_seconds=-1,
    )

    rendered = metrics.render_prometheus(release='r"1', node_code="node\\one")

    assert 'release="r\\"1"' in rendered
    assert 'node="node\\\\one"' in rendered
    assert 'route="/api/v1/items/{item_id}\\"\\nunsafe"' in rendered
    assert 'status_class="2xx"} 1' in rendered
    assert 'status_class="5xx"} 1' in rendered
    assert 'status_class="2xx",le="0.1"} 0' in rendered
    assert 'status_class="2xx",le="1"} 1' in rendered
    assert 'status_class="2xx"} 0.250000000' in rendered


def test_request_metrics_reject_invalid_buckets_and_collapse_oversized_route() -> None:
    try:
        RequestMetrics((1.0, 0.1))
    except ValueError as error:
        assert str(error) == "duration buckets must be non-empty, unique, and sorted"
    else:
        raise AssertionError("invalid buckets were accepted")

    metrics = RequestMetrics((1.0,))
    metrics.observe(method="POST", route="/" + "x" * 500, status_code=404, duration_seconds=0)
    rendered = metrics.render_prometheus(release="test", node_code="node")
    assert 'route="oversized-route"' in rendered
