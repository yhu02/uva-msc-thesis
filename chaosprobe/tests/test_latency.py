"""Tests for the inter-service latency measurement module."""

import threading

from chaosprobe.metrics.latency import (
    ContinuousLatencyProber,
    LatencyResult,
    LatencySample,
    _aggregate_latency_samples,
)


def _mk_latency_sample(latency_ms=50.0, status="ok", error=None):
    return LatencySample(
        source="frontend",
        target="productcatalogservice",
        route="/",
        protocol="http",
        latency_ms=latency_ms,
        status=status,
        timestamp="2026-03-24T12:00:00+00:00",
        error=error,
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
        prober._expected_chaos_duration = None
        prober._post_chaos_buffer = 15.0

        now = time.time()
        assert prober._current_phase(now) == "pre-chaos"

        prober._chaos_start_time = now - 10
        assert prober._current_phase(now) == "during-chaos"

        prober._chaos_end_time = now - 5
        assert prober._current_phase(now) == "post-chaos"

    def test_current_phase_chaos_duration_cap(self):
        """during-chaos is capped at expected_chaos_duration + dynamic buffer."""
        import time
        prober = ContinuousLatencyProber.__new__(ContinuousLatencyProber)
        prober._lock = threading.Lock()
        prober._chaos_end_time = None
        prober._expected_chaos_duration = 120.0
        prober._post_chaos_buffer = 15.0

        now = time.time()
        # Dynamic buffer for 120s chaos: max(15, 120*0.15)=18s, clamped to 18s
        prober._chaos_start_time = now - 130  # 130s ago, within 120+18 buffer
        assert prober._current_phase(now) == "during-chaos"

        prober._chaos_start_time = now - 140  # 140s ago, exceeds 120+18=138s cap
        assert prober._current_phase(now) == "post-chaos"


class TestAggregateLatencySamples:
    def test_single_pod_ok(self):
        per_pod = [("pod-a", "node-1", _mk_latency_sample(50.0))]
        entry = _aggregate_latency_samples(per_pod)
        assert entry["status"] == "ok"
        assert entry["latency_ms"] == 50.0
        assert entry["probeCount"] == 1
        assert entry["errorCount"] == 0
        assert entry["stddevLatency_ms"] == 0.0
        assert entry["minLatency_ms"] == 50.0
        assert entry["maxLatency_ms"] == 50.0
        assert entry["perPod"]["pod-a"]["node"] == "node-1"
        assert entry["perPod"]["pod-a"]["latency_ms"] == 50.0
        assert entry["perPod"]["pod-a"]["status"] == "ok"
        assert entry["perNode"]["node-1"]["podCount"] == 1
        assert entry["perNode"]["node-1"]["mean_ms"] == 50.0
        assert entry["perNode"]["node-1"]["stddev_ms"] == 0.0

    def test_multi_pod_spread_across_nodes(self):
        per_pod = [
            ("pod-a", "node-1", _mk_latency_sample(40.0)),
            ("pod-b", "node-2", _mk_latency_sample(60.0)),
            ("pod-c", "node-3", _mk_latency_sample(80.0)),
        ]
        entry = _aggregate_latency_samples(per_pod)
        assert entry["status"] == "ok"
        assert entry["latency_ms"] == 60.0  # mean of 40,60,80
        assert entry["probeCount"] == 3
        assert entry["errorCount"] == 0
        assert entry["minLatency_ms"] == 40.0
        assert entry["maxLatency_ms"] == 80.0
        assert entry["stddevLatency_ms"] == 20.0
        assert set(entry["perPod"].keys()) == {"pod-a", "pod-b", "pod-c"}
        assert entry["perPod"]["pod-b"]["latency_ms"] == 60.0
        assert set(entry["perNode"].keys()) == {"node-1", "node-2", "node-3"}
        assert entry["perNode"]["node-2"]["mean_ms"] == 60.0
        assert entry["perNode"]["node-2"]["podCount"] == 1

    def test_multi_pods_same_node_aggregated(self):
        """Multiple pods on the same node collapse into one perNode entry."""
        per_pod = [
            ("pod-a", "node-1", _mk_latency_sample(40.0)),
            ("pod-b", "node-1", _mk_latency_sample(60.0)),
            ("pod-c", "node-2", _mk_latency_sample(100.0)),
        ]
        entry = _aggregate_latency_samples(per_pod)
        assert entry["probeCount"] == 3
        # Three perPod entries, two perNode buckets
        assert len(entry["perPod"]) == 3
        assert set(entry["perNode"].keys()) == {"node-1", "node-2"}
        # node-1 has 2 pods: mean=50, stddev=sqrt(200)=~14.14
        assert entry["perNode"]["node-1"]["podCount"] == 2
        assert entry["perNode"]["node-1"]["mean_ms"] == 50.0
        assert entry["perNode"]["node-1"]["stddev_ms"] == 14.14
        # node-2 has 1 pod
        assert entry["perNode"]["node-2"]["podCount"] == 1
        assert entry["perNode"]["node-2"]["mean_ms"] == 100.0
        assert entry["perNode"]["node-2"]["stddev_ms"] == 0.0

    def test_partial_failure(self):
        per_pod = [
            ("pod-a", "node-1", _mk_latency_sample(30.0)),
            ("pod-b", "node-2", _mk_latency_sample(
                latency_ms=0, status="error", error="connection refused",
            )),
        ]
        entry = _aggregate_latency_samples(per_pod)
        assert entry["status"] == "ok"  # at least one probe succeeded
        assert entry["latency_ms"] == 30.0  # mean over ok samples only
        assert entry["probeCount"] == 1
        assert entry["errorCount"] == 1
        assert entry["perPod"]["pod-a"]["status"] == "ok"
        assert entry["perPod"]["pod-b"]["status"] == "error"
        assert entry["perPod"]["pod-b"]["latency_ms"] is None
        assert entry["perPod"]["pod-b"]["error"] == "connection refused"
        assert entry["perNode"]["node-2"]["errorCount"] == 1
        assert entry["perNode"]["node-2"]["mean_ms"] is None

    def test_all_failed(self):
        per_pod = [
            ("pod-a", "node-1", _mk_latency_sample(
                latency_ms=0, status="error", error="timeout",
            )),
            ("pod-b", "node-2", _mk_latency_sample(
                latency_ms=0, status="error", error="connection refused",
            )),
        ]
        entry = _aggregate_latency_samples(per_pod)
        assert entry["status"] == "error"
        assert entry["latency_ms"] is None
        assert entry["probeCount"] == 0
        assert entry["errorCount"] == 2
        assert "error" in entry
        assert entry["perPod"]["pod-a"]["error"] == "timeout"
        assert entry["perPod"]["pod-b"]["error"] == "connection refused"

    def test_empty(self):
        entry = _aggregate_latency_samples([])
        assert entry["status"] == "error"
        assert entry["latency_ms"] is None
        assert entry["probeCount"] == 0
        assert entry["errorCount"] == 0
        assert entry["perPod"] == {}
        assert entry["perNode"] == {}
