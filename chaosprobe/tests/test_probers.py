"""Tests for prober wiring in ``create_and_start_probers``.

Focus: the east-west ``service_routes`` are threaded through to the
``ContinuousLatencyProber`` so gRPC/TCP backends get probed.
"""

from unittest.mock import patch

from chaosprobe.orchestrator.probers import create_and_start_probers


def test_threads_service_routes_to_latency_prober():
    service_routes = [("checkout", "currency", "currency:7000", "grpc", "checkout->currency")]
    http_routes = [("frontend", "/", "homepage", "GET")]

    with (
        patch("chaosprobe.metrics.latency.ContinuousLatencyProber") as latency_cls,
        patch("chaosprobe.metrics.recovery.RecoveryWatcher"),
        patch("chaosprobe.metrics.prometheus.ContinuousPrometheusProber"),
        patch("chaosprobe.metrics.resources.ContinuousResourceProber"),
        patch("chaosprobe.metrics.throughput.ContinuousDiskProber"),
        patch("chaosprobe.metrics.throughput.ContinuousRedisProber"),
    ):
        probers = create_and_start_probers(
            "ns",
            "frontend",
            measure_latency=True,
            measure_redis=False,
            measure_disk=False,
            measure_resources=False,
            measure_prometheus=False,
            prometheus_url=(),
            http_routes=http_routes,
            service_routes=service_routes,
            expected_chaos_duration=10.0,
        )

    latency_cls.assert_called_once()
    _args, kwargs = latency_cls.call_args
    assert kwargs["service_routes"] == service_routes
    assert kwargs["http_routes"] == http_routes
    assert probers["latency"] is latency_cls.return_value
