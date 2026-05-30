"""Tests for the inter-service latency measurement module."""

import threading
from unittest.mock import MagicMock, patch

from chaosprobe.metrics.latency import (
    ContinuousLatencyProber,
    LatencyProber,
    LatencyResult,
    LatencySample,
    _aggregate_latency_samples,
    _bucket_for_sample,
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
            result.samples.append(
                LatencySample(
                    source="frontend",
                    target="productcatalogservice",
                    route="/",
                    protocol="http",
                    latency_ms=ms,
                    status="ok",
                    timestamp="2026-03-24T12:00:00+00:00",
                )
            )

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
        result.samples.append(
            LatencySample(
                source="frontend",
                target="backend",
                route="/api",
                protocol="http",
                latency_ms=25.0,
                status="ok",
                timestamp="2026-03-24T12:00:00+00:00",
            )
        )
        result.samples.append(
            LatencySample(
                source="frontend",
                target="backend",
                route="/api",
                protocol="http",
                latency_ms=0,
                status="error",
                timestamp="2026-03-24T12:00:01+00:00",
                error="timeout",
            )
        )

        summary = result.summary()
        assert summary["sampleCount"] == 2
        assert summary["errorCount"] == 1
        assert summary["errorRate"] == 0.5
        assert summary["mean_ms"] == 25.0

    def test_summary_all_errors(self):
        result = LatencyResult(
            source="a",
            target="b",
            route="/",
            protocol="http",
            description="test",
        )
        result.samples.append(
            LatencySample(
                source="a",
                target="b",
                route="/",
                protocol="http",
                latency_ms=0,
                status="error",
                timestamp="2026-03-24T12:00:00+00:00",
                error="failed",
            )
        )

        summary = result.summary()
        assert summary["mean_ms"] is None
        assert summary["errorCount"] == 1
        assert summary["errorRate"] == 1.0

    def test_summary_empty(self):
        result = LatencyResult(
            source="a",
            target="b",
            route="/",
            protocol="http",
            description="test",
        )
        summary = result.summary()
        assert summary["sampleCount"] == 0
        assert summary["errorRate"] == 0.0
        assert summary["mean_ms"] is None

    def test_summary_single_sample_stddev(self):
        result = LatencyResult(
            source="a",
            target="b",
            route="/",
            protocol="http",
            description="test",
        )
        result.samples.append(
            LatencySample(
                source="a",
                target="b",
                route="/",
                protocol="http",
                latency_ms=42.0,
                status="ok",
                timestamp="2026-03-24T12:00:00+00:00",
            )
        )
        summary = result.summary()
        assert summary["stddev_ms"] == 0.0


class TestContinuousLatencyProber:
    def test_phase_splitting(self):
        prober = ContinuousLatencyProber.__new__(ContinuousLatencyProber)
        prober._lock = __import__("threading").Lock()

        series = [
            {
                "phase": "pre-chaos",
                "routes": {
                    "/": {"latency_ms": 50, "status": "ok"},
                },
            },
            {
                "phase": "pre-chaos",
                "routes": {
                    "/": {"latency_ms": 55, "status": "ok"},
                },
            },
            {
                "phase": "during-chaos",
                "routes": {
                    "/": {"latency_ms": 200, "status": "ok"},
                },
            },
            {
                "phase": "during-chaos",
                "routes": {
                    "/": {"latency_ms": None, "status": "error", "error": "timeout"},
                },
            },
            {
                "phase": "post-chaos",
                "routes": {
                    "/": {"latency_ms": 60, "status": "ok"},
                },
            },
        ]

        phases = prober._aggregate_phases(series)
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

        phases = prober._aggregate_phases([])
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
            (
                "pod-b",
                "node-2",
                _mk_latency_sample(
                    latency_ms=0,
                    status="error",
                    error="connection refused",
                ),
            ),
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
            (
                "pod-a",
                "node-1",
                _mk_latency_sample(
                    latency_ms=0,
                    status="error",
                    error="timeout",
                ),
            ),
            (
                "pod-b",
                "node-2",
                _mk_latency_sample(
                    latency_ms=0,
                    status="error",
                    error="connection refused",
                ),
            ),
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
        prober._service_routes = None
        prober.namespace = "test"
        prober._prober = MagicMock()
        return prober

    def test_target_timeout_does_not_evict(self):
        """Pods that successfully exec but get target timeouts must NOT be evicted."""
        prober = self._make_prober()

        # Target is down: exec succeeds but HTTP probe returns error
        prober._prober._measure_http_from_pod = lambda *a, **kw: LatencySample(
            source="probe-pod",
            target="frontend",
            route="/",
            protocol="http",
            latency_ms=0,
            status="error",
            timestamp="now",
            error="timeout",
            exec_failed=False,
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
            source="probe-pod",
            target="frontend",
            route="/",
            protocol="http",
            latency_ms=0,
            status="error",
            timestamp="now",
            error="pod not found",
            exec_failed=True,
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
            source="probe-pod",
            target="frontend",
            route="/",
            protocol="http",
            latency_ms=0,
            status="error",
            timestamp="now",
            error="pod not found",
            exec_failed=True,
        )
        for _ in range(2):
            prober._run_all_probes()
        assert prober._pod_consecutive_errors["pod-a"] == 2

        # One successful exec resets the counter
        prober._prober._measure_http_from_pod = lambda *a, **kw: LatencySample(
            source="probe-pod",
            target="frontend",
            route="/",
            protocol="http",
            latency_ms=100,
            status="ok",
            timestamp="now",
            exec_failed=False,
        )
        prober._run_all_probes()
        assert prober._pod_consecutive_errors["pod-a"] == 0

        # 2 more exec failures don't trigger eviction (not 3 consecutive)
        prober._prober._measure_http_from_pod = lambda *a, **kw: LatencySample(
            source="probe-pod",
            target="frontend",
            route="/",
            protocol="http",
            latency_ms=0,
            status="error",
            timestamp="now",
            error="pod not found",
            exec_failed=True,
        )
        for _ in range(2):
            prober._run_all_probes()

        assert len(prober._probe_points) == 2  # not evicted


class TestServiceRouteProbing:
    """`_run_all_probes` probes east-west service routes via TCP (the
    correct probe for gRPC/TCP backends), keyed by the source->target
    label and merged alongside the HTTP routes."""

    def _make_prober(self, http_routes, service_routes):
        from unittest.mock import MagicMock

        prober = ContinuousLatencyProber.__new__(ContinuousLatencyProber)
        prober._lock = threading.Lock()
        prober._probe_points = [("pod-a", "node1")]
        prober._pod_consecutive_errors = {}
        prober._max_consecutive_errors = 3
        prober._http_routes = http_routes
        prober._service_routes = service_routes
        prober.namespace = "test"
        prober._prober = MagicMock()
        return prober

    @staticmethod
    def _tcp_ok(source, target, host):
        return LatencySample(
            source=source,
            target=target,
            route=host,
            protocol="tcp",
            latency_ms=2.5,
            status="ok",
            timestamp="now",
        )

    def test_service_route_probed_via_tcp_and_keyed_by_label(self):
        prober = self._make_prober(
            http_routes=None,
            service_routes=[
                ("checkout", "currency", "currency:7000", "grpc", "checkout->currency")
            ],
        )
        prober._prober._measure_tcp_from_pod = lambda pod, host, source, target: self._tcp_ok(
            source, target, host
        )

        result = prober._run_all_probes()

        assert "checkout->currency" in result
        assert result["checkout->currency"]["status"] == "ok"

    def test_tcp_called_with_host_source_and_target(self):
        prober = self._make_prober(
            http_routes=None,
            service_routes=[
                ("checkout", "currency", "currency:7000", "grpc", "checkout->currency")
            ],
        )
        calls = []

        def fake_tcp(pod, host, source, target):
            calls.append((pod, host, source, target))
            return self._tcp_ok(source, target, host)

        prober._prober._measure_tcp_from_pod = fake_tcp

        prober._run_all_probes()

        assert calls == [("pod-a", "currency:7000", "checkout", "currency")]

    def test_http_and_service_routes_both_probed(self):
        prober = self._make_prober(
            http_routes=[("frontend", "/", "homepage", "GET")],
            service_routes=[
                ("checkout", "currency", "currency:7000", "grpc", "checkout->currency")
            ],
        )
        prober._prober._measure_http_from_pod = lambda *a, **kw: LatencySample(
            source="probe-pod",
            target="frontend",
            route="/",
            protocol="http",
            latency_ms=5.0,
            status="ok",
            timestamp="now",
            status_code=200,
        )
        prober._prober._measure_tcp_from_pod = lambda pod, host, source, target: self._tcp_ok(
            source, target, host
        )

        result = prober._run_all_probes()

        assert "/" in result  # north-south HTTP route
        assert "checkout->currency" in result  # east-west service route

    def test_no_routes_at_all_returns_empty(self):
        prober = self._make_prober(http_routes=None, service_routes=None)
        assert prober._run_all_probes() == {}

    def test_probe_future_exception_marks_pod_failed(self):
        """If a per-pod probe future raises, the pod is recorded as failed
        (so it can be evicted) rather than crashing the tick."""
        prober = self._make_prober(
            http_routes=[("frontend", "/", "homepage", "GET")],
            service_routes=None,
        )

        def boom(*_a, **_k):
            raise RuntimeError("exec blew up")

        prober._prober._measure_http_from_pod = boom

        prober._run_all_probes()

        assert prober._pod_consecutive_errors["pod-a"] == 1

    def test_init_stores_service_routes(self):
        """The constructor records both route sets so the probe loop can
        reach the east-west service routes."""
        with patch("chaosprobe.metrics.latency.LatencyProber"):
            prober = ContinuousLatencyProber(
                "ns",
                http_routes=[("frontend", "/", "homepage", "GET")],
                service_routes=[("a", "b", "b:1", "grpc", "a->b")],
            )
        assert prober._service_routes == [("a", "b", "b:1", "grpc", "a->b")]
        assert prober._http_routes == [("frontend", "/", "homepage", "GET")]

    def test_refused_service_route_records_error(self):
        """A refused gRPC port yields an error sample (a genuine signal),
        not a silent pass — this is what unblocks the taint detector."""
        prober = self._make_prober(
            http_routes=None,
            service_routes=[("a", "b", "b:1", "grpc", "a->b")],
        )
        prober._prober._measure_tcp_from_pod = lambda *a, **kw: LatencySample(
            source="a",
            target="b",
            route="b:1",
            protocol="tcp",
            latency_ms=0,
            status="error",
            timestamp="now",
            error="connection refused",
        )

        result = prober._run_all_probes()

        assert result["a->b"]["status"] == "error"


def _mk_sample_with_code(status_code, status="ok"):
    return LatencySample(
        source="frontend",
        target="productcatalogservice",
        route="/",
        protocol="http",
        latency_ms=12.3,
        status=status,
        timestamp="2026-03-24T12:00:00+00:00",
        status_code=status_code,
    )


class TestStatusCodeField:
    """The new `status_code` field round-trips through LatencySample and
    feeds the per-route statusCodeDistribution in `LatencyResult.summary`."""

    def test_status_code_round_trips_through_sample(self):
        sample = _mk_sample_with_code(200)
        assert sample.status_code == 200

    def test_status_code_defaults_to_none_for_backwards_compat(self):
        """Callers that construct LatencySample without the new field — e.g.
        the TCP-probe path, or any external caller pinned to the prior
        signature — still get a valid sample with status_code=None."""
        sample = LatencySample(
            source="frontend",
            target="checkoutservice",
            route="/cart",
            protocol="http",
            latency_ms=42.0,
            status="ok",
            timestamp="2026-03-24T12:00:00+00:00",
        )
        assert sample.status_code is None


class TestBucketForSample:
    """`_bucket_for_sample` classifies into the six fixed buckets used by
    `LatencyResult._status_code_distribution`."""

    def test_2xx_bucket(self):
        assert _bucket_for_sample(_mk_sample_with_code(200)) == "2xx"
        assert _bucket_for_sample(_mk_sample_with_code(299)) == "2xx"

    def test_3xx_bucket(self):
        assert _bucket_for_sample(_mk_sample_with_code(301)) == "3xx"

    def test_4xx_bucket(self):
        assert _bucket_for_sample(_mk_sample_with_code(404, status="error")) == "4xx"

    def test_5xx_bucket(self):
        assert _bucket_for_sample(_mk_sample_with_code(503, status="error")) == "5xx"

    def test_non_standard_code_buckets_as_error(self):
        """Sub-200 / 600+ codes are non-standard HTTP — surface them as
        `error` instead of silently grouping into the wrong tier."""
        assert _bucket_for_sample(_mk_sample_with_code(100)) == "error"
        assert _bucket_for_sample(_mk_sample_with_code(700, status="error")) == "error"

    def test_timeout_status_without_code_buckets_as_timeout(self):
        sample = LatencySample(
            source="x",
            target="y",
            route="/",
            protocol="http",
            latency_ms=0,
            status="timeout",
            timestamp="2026-03-24T12:00:00+00:00",
        )
        assert _bucket_for_sample(sample) == "timeout"

    def test_other_status_without_code_buckets_as_error(self):
        sample = LatencySample(
            source="x",
            target="y",
            route="/",
            protocol="http",
            latency_ms=0,
            status="error",
            timestamp="2026-03-24T12:00:00+00:00",
            error="connection refused",
        )
        assert _bucket_for_sample(sample) == "error"


class TestStatusCodeDistribution:
    """The summary surface exposes a fixed-order counter so consumers can
    correlate per-strategy 5xx rates against placement decisions."""

    def test_distribution_keys_are_fixed_order(self):
        """Six buckets, fixed key order — consumers should be able to iterate
        the dict deterministically."""
        result = LatencyResult(
            source="frontend",
            target="productcatalogservice",
            route="/",
            protocol="http",
            description="Homepage",
        )
        result.samples.append(_mk_sample_with_code(200))
        keys = list(result.summary()["statusCodeDistribution"].keys())
        assert keys == ["2xx", "3xx", "4xx", "5xx", "timeout", "error"]

    def test_distribution_counts_mixed_samples(self):
        result = LatencyResult(
            source="frontend",
            target="productcatalogservice",
            route="/",
            protocol="http",
            description="Homepage",
        )
        # 3x 200, 1x 503, 1x 404, 1x timeout, 1x error-without-code
        for _ in range(3):
            result.samples.append(_mk_sample_with_code(200))
        result.samples.append(_mk_sample_with_code(503, status="error"))
        result.samples.append(_mk_sample_with_code(404, status="error"))
        result.samples.append(
            LatencySample(
                source="frontend",
                target="productcatalogservice",
                route="/",
                protocol="http",
                latency_ms=0,
                status="timeout",
                timestamp="2026-03-24T12:00:00+00:00",
            )
        )
        result.samples.append(
            LatencySample(
                source="frontend",
                target="productcatalogservice",
                route="/",
                protocol="http",
                latency_ms=0,
                status="error",
                timestamp="2026-03-24T12:00:00+00:00",
                error="exec failed",
            )
        )

        dist = result.summary()["statusCodeDistribution"]
        assert dist == {
            "2xx": 3,
            "3xx": 0,
            "4xx": 1,
            "5xx": 1,
            "timeout": 1,
            "error": 1,
        }

    def test_distribution_with_no_samples(self):
        result = LatencyResult(
            source="frontend",
            target="productcatalogservice",
            route="/",
            protocol="http",
            description="Homepage",
        )
        # No samples → all buckets zero, no exception
        dist = result.summary()["statusCodeDistribution"]
        assert dist == {b: 0 for b in ("2xx", "3xx", "4xx", "5xx", "timeout", "error")}

    def test_distribution_present_in_empty_ok_path(self):
        """When the only samples are errors (no OK latencies), the early
        return path still includes statusCodeDistribution."""
        result = LatencyResult(
            source="frontend",
            target="productcatalogservice",
            route="/",
            protocol="http",
            description="Homepage",
        )
        result.samples.append(_mk_sample_with_code(503, status="error"))
        summary = result.summary()
        assert summary["mean_ms"] is None  # no OK samples
        assert summary["statusCodeDistribution"]["5xx"] == 1


class TestMeasureTcpFromPod:
    @staticmethod
    def _prober():
        p = LatencyProber.__new__(LatencyProber)
        p.namespace = "online-boutique"
        p.timeout_seconds = 5
        p.core_api = MagicMock()
        return p

    def test_host_passed_as_argv_not_interpolated_into_script(self):
        captured = {}

        def fake_stream(*_args, **kwargs):
            captured["cmd"] = kwargs["command"]
            return "1000000 3000000"

        with patch("chaosprobe.metrics.latency.stream", side_effect=fake_stream):
            sample = self._prober()._measure_tcp_from_pod(
                "pod-1", "weird'host:9999", "frontend", "cart"
            )

        cmd = captured["cmd"]
        # host/port are standalone argv elements, never baked into the script
        assert cmd[3] == "weird'host"
        assert cmd[5] == "9999"
        assert "weird'host" not in cmd[2]
        assert "sys.argv" in cmd[2]
        # output still parses correctly
        assert sample.status == "ok"
        assert sample.latency_ms == 2.0

    def test_default_port_80_when_host_has_no_port(self):
        captured = {}

        def fake_stream(*_args, **kwargs):
            captured["cmd"] = kwargs["command"]
            return "1000000 2000000"

        with patch("chaosprobe.metrics.latency.stream", side_effect=fake_stream):
            self._prober()._measure_tcp_from_pod("pod-1", "frontend.ns.svc", "a", "b")

        assert captured["cmd"][5] == "80"
