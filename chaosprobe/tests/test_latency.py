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
        """during-chaos is capped at 5x expected_chaos_duration as a safety net.

        Previously this cap was ``expected + ~18s`` which fired far too
        early — LitmusChaos's workflow wrapper typically takes 2-3x the
        configured chaos duration to fully complete (helper-pod
        scheduling + cleanup), so the old cap mislabeled the tail of
        actual chaos cycles as post-chaos.  See the trace from
        results/20260520-191703 documented at metrics/base.py:_current_phase.
        """
        import time
        prober = ContinuousLatencyProber.__new__(ContinuousLatencyProber)
        prober._lock = threading.Lock()
        prober._chaos_end_time = None
        prober._expected_chaos_duration = 120.0
        prober._post_chaos_buffer = 15.0

        now = time.time()
        # 5x cap for 120s chaos = 600s.  Well within → during-chaos.
        prober._chaos_start_time = now - 500
        assert prober._current_phase(now) == "during-chaos"

        # Past the 5x cap → post-chaos as safety net.
        prober._chaos_start_time = now - 700
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


class TestEvictionLogic:
    """Tests for pod eviction in ContinuousLatencyProber._run_all_probes."""

    def _make_prober(self):
        from unittest.mock import MagicMock

        prober = ContinuousLatencyProber.__new__(ContinuousLatencyProber)
        prober._lock = threading.Lock()
        prober._probe_points = [("pod-a", "node1"), ("pod-b", "node1")]
        prober._pod_consecutive_errors = {}
        prober._max_consecutive_errors = 3
        prober._http_routes = [("frontend", "/", "homepage", "GET")]
        prober.namespace = "test"
        prober._prober = MagicMock()
        return prober

    def test_target_timeout_does_not_evict(self):
        """Pods that successfully exec but get target timeouts must NOT be evicted."""
        prober = self._make_prober()

        # Target is down: exec succeeds but HTTP probe returns error
        prober._prober._measure_http_from_pod = lambda *a, **kw: LatencySample(
            source="probe-pod", target="frontend", route="/",
            protocol="http", latency_ms=0, status="error",
            timestamp="now", error="timeout", exec_failed=False,
        )

        # Run 5 ticks (more than _max_consecutive_errors=3)
        for _ in range(5):
            prober._run_all_probes()

        # Pods must NOT be evicted — they're alive, target is just down
        assert len(prober._probe_points) == 2
        assert prober._pod_consecutive_errors.get("pod-a", 0) == 0
        assert prober._pod_consecutive_errors.get("pod-b", 0) == 0

    def test_exec_failure_evicts_after_threshold(self):
        """Pods whose exec fails (pod dead) must be evicted after threshold."""
        prober = self._make_prober()

        # Pod is dead: exec fails, sample marked exec_failed=True
        prober._prober._measure_http_from_pod = lambda *a, **kw: LatencySample(
            source="probe-pod", target="frontend", route="/",
            protocol="http", latency_ms=0, status="error",
            timestamp="now", error="pod not found", exec_failed=True,
        )

        # Run exactly 3 ticks (= threshold)
        for _ in range(3):
            prober._run_all_probes()

        # Both pods should be evicted
        assert len(prober._probe_points) == 0

    def test_exec_failure_resets_on_success(self):
        """A successful exec resets the consecutive failure counter."""
        prober = self._make_prober()

        # 2 consecutive exec failures
        prober._prober._measure_http_from_pod = lambda *a, **kw: LatencySample(
            source="probe-pod", target="frontend", route="/",
            protocol="http", latency_ms=0, status="error",
            timestamp="now", error="pod not found", exec_failed=True,
        )
        for _ in range(2):
            prober._run_all_probes()
        assert prober._pod_consecutive_errors["pod-a"] == 2

        # One successful exec resets the counter
        prober._prober._measure_http_from_pod = lambda *a, **kw: LatencySample(
            source="probe-pod", target="frontend", route="/",
            protocol="http", latency_ms=100, status="ok",
            timestamp="now", exec_failed=False,
        )
        prober._run_all_probes()
        assert prober._pod_consecutive_errors["pod-a"] == 0

        # 2 more exec failures don't trigger eviction (not 3 consecutive)
        prober._prober._measure_http_from_pod = lambda *a, **kw: LatencySample(
            source="probe-pod", target="frontend", route="/",
            protocol="http", latency_ms=0, status="error",
            timestamp="now", error="pod not found", exec_failed=True,
        )
        for _ in range(2):
            prober._run_all_probes()

        assert len(prober._probe_points) == 2  # not evicted
