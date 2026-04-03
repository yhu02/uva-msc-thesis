"""Tests for the inter-service latency measurement module."""

import threading


from chaosprobe.metrics.latency import (
    ContinuousLatencyProber,
    LatencyResult,
    LatencySample,
)


class TestLatencySample:
    def test_create_ok_sample(self):
        sample = LatencySample(
            source="frontend",
            target="productcatalogservice",
            route="/",
            protocol="http",
            latency_ms=45.3,
            status="ok",
            timestamp="2026-03-24T12:00:00+00:00",
        )
        assert sample.status == "ok"
        assert sample.latency_ms == 45.3
        assert sample.error is None

    def test_create_error_sample(self):
        sample = LatencySample(
            source="frontend",
            target="productcatalogservice",
            route="/",
            protocol="http",
            latency_ms=0,
            status="error",
            timestamp="2026-03-24T12:00:00+00:00",
            error="Connection refused",
        )
        assert sample.status == "error"
        assert sample.error == "Connection refused"


class TestLatencyResult:
    def test_summary_with_samples(self):
        result = LatencyResult(
            source="frontend",
            target="productcatalogservice",
            route="/",
            protocol="http",
            description="Homepage",
        )
        for ms in [10.0, 20.0, 30.0, 40.0, 50.0]:
            result.samples.append(LatencySample(
                source="frontend", target="productcatalogservice",
                route="/", protocol="http", latency_ms=ms,
                status="ok", timestamp="2026-03-24T12:00:00+00:00",
            ))

        summary = result.summary()
        assert summary["sampleCount"] == 5
        assert summary["errorCount"] == 0
        assert summary["errorRate"] == 0.0
        assert summary["mean_ms"] == 30.0
        assert summary["median_ms"] == 30.0
        assert summary["min_ms"] == 10.0
        assert summary["max_ms"] == 50.0
        assert summary["source"] == "frontend"
        assert summary["target"] == "productcatalogservice"

    def test_summary_with_errors(self):
        result = LatencyResult(
            source="frontend",
            target="backend",
            route="/api",
            protocol="http",
            description="API call",
        )
        result.samples.append(LatencySample(
            source="frontend", target="backend", route="/api",
            protocol="http", latency_ms=25.0, status="ok",
            timestamp="2026-03-24T12:00:00+00:00",
        ))
        result.samples.append(LatencySample(
            source="frontend", target="backend", route="/api",
            protocol="http", latency_ms=0, status="error",
            timestamp="2026-03-24T12:00:01+00:00",
            error="timeout",
        ))

        summary = result.summary()
        assert summary["sampleCount"] == 2
        assert summary["errorCount"] == 1
        assert summary["errorRate"] == 0.5
        assert summary["mean_ms"] == 25.0

    def test_summary_all_errors(self):
        result = LatencyResult(
            source="a", target="b", route="/", protocol="http",
            description="test",
        )
        result.samples.append(LatencySample(
            source="a", target="b", route="/", protocol="http",
            latency_ms=0, status="error",
            timestamp="2026-03-24T12:00:00+00:00",
            error="failed",
        ))

        summary = result.summary()
        assert summary["mean_ms"] is None
        assert summary["errorCount"] == 1
        assert summary["errorRate"] == 1.0

    def test_summary_empty(self):
        result = LatencyResult(
            source="a", target="b", route="/", protocol="http",
            description="test",
        )
        summary = result.summary()
        assert summary["sampleCount"] == 0
        assert summary["errorRate"] == 0.0
        assert summary["mean_ms"] is None

    def test_summary_single_sample_stddev(self):
        result = LatencyResult(
            source="a", target="b", route="/", protocol="http",
            description="test",
        )
        result.samples.append(LatencySample(
            source="a", target="b", route="/", protocol="http",
            latency_ms=42.0, status="ok",
            timestamp="2026-03-24T12:00:00+00:00",
        ))
        summary = result.summary()
        assert summary["stddev_ms"] == 0.0


class TestContinuousLatencyProber:
    def test_phase_splitting(self):
        prober = ContinuousLatencyProber.__new__(ContinuousLatencyProber)
        prober._lock = __import__("threading").Lock()

        series = [
            {"phase": "pre-chaos", "routes": {
                "/": {"latency_ms": 50, "status": "ok"},
            }},
            {"phase": "pre-chaos", "routes": {
                "/": {"latency_ms": 55, "status": "ok"},
            }},
            {"phase": "during-chaos", "routes": {
                "/": {"latency_ms": 200, "status": "ok"},
            }},
            {"phase": "during-chaos", "routes": {
                "/": {"latency_ms": None, "status": "error", "error": "timeout"},
            }},
            {"phase": "post-chaos", "routes": {
                "/": {"latency_ms": 60, "status": "ok"},
            }},
        ]

        phases = prober._split_phases(series)
        assert phases["pre-chaos"]["sampleCount"] == 2
        assert phases["during-chaos"]["sampleCount"] == 2
        assert phases["post-chaos"]["sampleCount"] == 1

        pre_route = phases["pre-chaos"]["routes"]["/"]
        assert pre_route["mean_ms"] == 52.5
        assert pre_route["errorCount"] == 0

        during_route = phases["during-chaos"]["routes"]["/"]
        assert during_route["mean_ms"] == 200.0
        assert during_route["errorCount"] == 1

    def test_phase_splitting_empty(self):
        prober = ContinuousLatencyProber.__new__(ContinuousLatencyProber)
        prober._lock = __import__("threading").Lock()

        phases = prober._split_phases([])
        assert phases["pre-chaos"]["sampleCount"] == 0
        assert phases["during-chaos"]["sampleCount"] == 0
        assert phases["post-chaos"]["sampleCount"] == 0

    def test_current_phase_transitions(self):
        import time
        prober = ContinuousLatencyProber.__new__(ContinuousLatencyProber)
        prober._lock = threading.Lock()
        prober._chaos_start_time = None
        prober._chaos_end_time = None

        now = time.time()
        assert prober._current_phase(now) == "pre-chaos"

        prober._chaos_start_time = now - 10
        assert prober._current_phase(now) == "during-chaos"

        prober._chaos_end_time = now - 5
        assert prober._current_phase(now) == "post-chaos"
