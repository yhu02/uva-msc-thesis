"""Tests for the throughput measurement module."""

import threading


from chaosprobe.metrics.base import ContinuousProberBase
from chaosprobe.metrics.throughput import (
    ContinuousRedisProber,
    ContinuousDiskProber,
    ThroughputResult,
    ThroughputSample,
    _parse_dd_elapsed_seconds,
)


class TestThroughputSample:
    def test_create_ok_sample(self):
        sample = ThroughputSample(
            operation="write", target="redis",
            ops_per_second=5000.0, latency_ms=0.2,
            status="ok", timestamp="2026-03-24T12:00:00+00:00",
        )
        assert sample.status == "ok"
        assert sample.ops_per_second == 5000.0
        assert sample.latency_ms == 0.2

    def test_create_disk_sample_with_bytes(self):
        sample = ThroughputSample(
            operation="write", target="disk",
            ops_per_second=100.0, latency_ms=10.0,
            bytes_per_second=104857600.0,
            status="ok", timestamp="2026-03-24T12:00:00+00:00",
        )
        assert sample.bytes_per_second == 104857600.0

    def test_create_error_sample(self):
        sample = ThroughputSample(
            operation="read", target="redis",
            ops_per_second=0, latency_ms=0,
            status="error", timestamp="2026-03-24T12:00:00+00:00",
            error="Connection refused",
        )
        assert sample.status == "error"


class TestThroughputResult:
    def test_summary_with_samples(self):
        result = ThroughputResult(
            target="redis", operation="write",
            description="Redis SET benchmark",
        )
        for ops in [4000.0, 5000.0, 6000.0, 4500.0, 5500.0]:
            result.samples.append(ThroughputSample(
                operation="write", target="redis",
                ops_per_second=ops, latency_ms=1000 / ops,
                status="ok", timestamp="2026-03-24T12:00:00+00:00",
            ))

        summary = result.summary()
        assert summary["sampleCount"] == 5
        assert summary["errorCount"] == 0
        assert summary["meanOpsPerSecond"] == 5000.0
        assert summary["minOpsPerSecond"] == 4000.0
        assert summary["maxOpsPerSecond"] == 6000.0
        assert summary["target"] == "redis"
        assert summary["operation"] == "write"

    def test_summary_with_errors(self):
        result = ThroughputResult(
            target="disk", operation="read",
            description="Disk read benchmark",
        )
        result.samples.append(ThroughputSample(
            operation="read", target="disk",
            ops_per_second=100.0, latency_ms=10.0,
            bytes_per_second=50000000.0,
            status="ok", timestamp="2026-03-24T12:00:00+00:00",
        ))
        result.samples.append(ThroughputSample(
            operation="read", target="disk",
            ops_per_second=0, latency_ms=0,
            status="error", timestamp="2026-03-24T12:00:01+00:00",
            error="timeout",
        ))

        summary = result.summary()
        assert summary["sampleCount"] == 2
        assert summary["errorCount"] == 1
        assert summary["meanOpsPerSecond"] == 100.0
        assert summary["meanBytesPerSecond"] == 50000000.0

    def test_summary_all_errors(self):
        result = ThroughputResult(
            target="redis", operation="write",
            description="test",
        )
        result.samples.append(ThroughputSample(
            operation="write", target="redis",
            ops_per_second=0, latency_ms=0,
            status="error", timestamp="2026-03-24T12:00:00+00:00",
        ))

        summary = result.summary()
        assert summary["meanOpsPerSecond"] is None
        assert summary["errorCount"] == 1

    def test_summary_empty(self):
        result = ThroughputResult(
            target="redis", operation="write",
            description="test",
        )
        summary = result.summary()
        assert summary["sampleCount"] == 0
        assert summary["meanOpsPerSecond"] is None

    def test_summary_disk_with_bytes(self):
        result = ThroughputResult(
            target="disk", operation="write",
            description="Sequential write",
        )
        for ops, bps in [(100, 100_000_000), (120, 120_000_000)]:
            result.samples.append(ThroughputSample(
                operation="write", target="disk",
                ops_per_second=float(ops), latency_ms=10.0,
                bytes_per_second=float(bps),
                status="ok", timestamp="2026-03-24T12:00:00+00:00",
            ))

        summary = result.summary()
        assert summary["meanBytesPerSecond"] == 110_000_000.0


class TestContinuousRedisProber:
    def test_phase_splitting(self):
        prober = ContinuousRedisProber.__new__(ContinuousRedisProber)
        prober._lock = threading.Lock()

        series = [
            {
                "phase": "pre-chaos",
                "redis": {
                    "write": {"ops_per_second": 5000, "latency_ms": 0.2, "status": "ok"},
                    "read": {"ops_per_second": 8000, "latency_ms": 0.12, "status": "ok"},
                },
            },
            {
                "phase": "during-chaos",
                "redis": {
                    "write": {"ops_per_second": 2000, "latency_ms": 0.5, "status": "ok"},
                    "read": {"ops_per_second": 3000, "latency_ms": 0.33, "status": "ok"},
                },
            },
            {
                "phase": "during-chaos",
                "redis": {
                    "write": {"ops_per_second": 2500, "latency_ms": 0.4, "status": "ok"},
                },
            },
            {
                "phase": "post-chaos",
                "redis": {
                    "write": {"ops_per_second": 4800, "latency_ms": 0.21, "status": "ok"},
                },
            },
        ]

        phases = prober._split_phases(series, "redis")
        assert phases["pre-chaos"]["sampleCount"] == 1
        assert phases["during-chaos"]["sampleCount"] == 2
        assert phases["post-chaos"]["sampleCount"] == 1

        # Check redis write during chaos
        redis_write = phases["during-chaos"]["redis"]["write"]
        assert redis_write["meanOpsPerSecond"] == 2250.0  # (2000 + 2500) / 2
        assert redis_write["sampleCount"] == 2

    def test_phase_splitting_empty(self):
        prober = ContinuousRedisProber.__new__(ContinuousRedisProber)
        prober._lock = threading.Lock()

        phases = prober._split_phases([], "redis")
        assert phases["pre-chaos"]["sampleCount"] == 0
        assert phases["during-chaos"]["sampleCount"] == 0
        assert phases["post-chaos"]["sampleCount"] == 0


class TestContinuousDiskProber:
    def test_phase_splitting(self):
        prober = ContinuousDiskProber.__new__(ContinuousDiskProber)
        prober._lock = threading.Lock()

        series = [
            {
                "phase": "pre-chaos",
                "disk": {
                    "write": {"ops_per_second": 100, "latency_ms": 10.0, "bytes_per_second": 100000000, "status": "ok"},
                },
            },
            {
                "phase": "during-chaos",
                "disk": {
                    "write": {"ops_per_second": 30, "latency_ms": 33.0, "bytes_per_second": 30000000, "status": "ok"},
                },
            },
        ]

        phases = prober._split_phases(series, "disk")
        assert phases["pre-chaos"]["sampleCount"] == 1
        assert phases["during-chaos"]["sampleCount"] == 1
        assert phases["during-chaos"]["disk"]["write"]["meanOpsPerSecond"] == 30.0


class TestDdElapsedParser:
    def test_parses_gnu_dd(self):
        out = (
            "4+0 records in\n"
            "4+0 records out\n"
            "262144 bytes (262 kB, 256 KiB) copied, 0.00213 s, 123 MB/s\n"
        )
        assert _parse_dd_elapsed_seconds(out) == 0.00213

    def test_parses_busybox_dd(self):
        out = (
            "4+0 records in\n"
            "4+0 records out\n"
            "262144 bytes (256.0KB) copied, 0.000876 seconds, 285.0MB/s\n"
        )
        assert _parse_dd_elapsed_seconds(out) == 0.000876

    def test_parses_scientific_notation(self):
        out = "262144 bytes (256 KiB) copied, 1.5e-05 s, 16 GB/s"
        assert _parse_dd_elapsed_seconds(out) == 1.5e-05

    def test_parses_zero_elapsed(self):
        # Some dd versions round sub-microsecond ops to 0.
        out = "262144 bytes (256.0KB) copied, 0 seconds, 0MB/s"
        assert _parse_dd_elapsed_seconds(out) == 0.0

    def test_returns_none_on_missing_summary(self):
        assert _parse_dd_elapsed_seconds("dd: /some/path: Read-only file system") is None
        assert _parse_dd_elapsed_seconds("") is None

    def test_returns_none_on_garbled_float(self):
        out = "262144 bytes copied, NaNsOmething s, foo"
        # Regex requires digits up front, so this should not match.
        assert _parse_dd_elapsed_seconds(out) is None


class TestContinuousDiskProberSerializesError:
    def test_probe_loop_preserves_error_in_timeseries(self, monkeypatch):
        """Regression: the continuous disk prober used to drop ``sample.error``
        when serializing to the time series, making field failures invisible."""
        import time as _time
        from unittest.mock import MagicMock

        prober = ContinuousDiskProber.__new__(ContinuousDiskProber)
        prober._lock = threading.Lock()
        prober._stop_event = threading.Event()
        prober._time_series = []
        prober._probe_errors = 0
        prober._start_time = _time.time()
        prober._chaos_start_time = None
        prober._chaos_end_time = None
        prober._expected_chaos_duration = None
        prober._post_chaos_buffer = 15.0
        prober.interval = 10.0
        prober.namespace = "test-ns"
        prober._disk_target = "redis-cart"
        prober._disk_path = "/tmp/chaosprobe_disktest"
        prober._block_size_kb = 64
        prober._block_count = 4
        prober._exclude_services = []

        # Build a fake prober whose disk benchmark returns an error sample
        # with a populated error field.
        err_sample = ThroughputSample(
            operation="write", target="disk",
            ops_per_second=0, latency_ms=0,
            status="error", timestamp="2026-04-22T18:00:00+00:00",
            error="dd elapsed<=0 (workload too small): copied, 0 seconds",
        )
        err_result = ThroughputResult(target="disk", operation="write", description="")
        err_result.samples.append(err_sample)

        fake_prober = MagicMock()
        fake_prober.measure_disk_throughput.return_value = [err_result]
        fake_prober._cache_lock = threading.Lock()
        fake_prober._exec_pod_cache = {}
        prober._prober = fake_prober

        # Run one loop iteration by stopping immediately after the first wait.
        def _wait_then_stop(timeout):
            prober._stop_event.set()
            return True
        monkeypatch.setattr(prober._stop_event, "wait", _wait_then_stop)

        prober._probe_loop()

        assert len(prober._time_series) == 1
        entry = prober._time_series[0]
        write_entry = entry["disk"]["write"]
        assert write_entry["status"] == "error"
        assert "error" in write_entry
        assert "elapsed<=0" in write_entry["error"]


class TestContinuousProberBase:
    def test_current_phase_transitions(self):
        import time
        prober = ContinuousRedisProber.__new__(ContinuousRedisProber)
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

    def test_current_phase_uses_now_parameter(self):
        """now parameter must drive the phase decision, not just timestamp presence."""
        import time
        prober = ContinuousRedisProber.__new__(ContinuousRedisProber)
        prober._lock = threading.Lock()
        prober._expected_chaos_duration = None
        prober._post_chaos_buffer = 15.0

        base = time.time()
        prober._chaos_start_time = base + 10
        prober._chaos_end_time = base + 20

        # now before chaos start → pre-chaos
        assert prober._current_phase(base) == "pre-chaos"
        # now during chaos → during-chaos
        assert prober._current_phase(base + 15) == "during-chaos"
        # now after chaos end → post-chaos
        assert prober._current_phase(base + 25) == "post-chaos"

    def test_aggregate_operations(self):
        entries = [
            {"redis": {"write": {"ops_per_second": 100, "latency_ms": 10, "status": "ok"}}},
            {"redis": {"write": {"ops_per_second": 200, "latency_ms": 5, "status": "ok"}}},
            {"redis": {"write": {"ops_per_second": None, "latency_ms": None, "status": "error"}}},
        ]

        result = ContinuousProberBase._aggregate_operations(entries, "redis")
        assert "write" in result
        assert result["write"]["meanOpsPerSecond"] == 150.0
        assert result["write"]["sampleCount"] == 2
        assert result["write"]["errorCount"] == 1
