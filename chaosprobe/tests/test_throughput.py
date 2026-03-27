"""Tests for the throughput measurement module."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from chaosprobe.metrics.throughput import (
    ContinuousRedisProber,
    ContinuousDiskProber,
    ThroughputProber,
    ThroughputResult,
    ThroughputSample,
    _ContinuousProberBase,
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

        phases = prober._split_phases(series)
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

        phases = prober._split_phases([])
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

        phases = prober._split_phases(series)
        assert phases["pre-chaos"]["sampleCount"] == 1
        assert phases["during-chaos"]["sampleCount"] == 1
        assert phases["during-chaos"]["disk"]["write"]["meanOpsPerSecond"] == 30.0


class TestContinuousProberBase:
    def test_current_phase_transitions(self):
        import time
        prober = ContinuousRedisProber.__new__(ContinuousRedisProber)
        prober._lock = threading.Lock()
        prober._chaos_start_time = None
        prober._chaos_end_time = None

        now = time.time()
        assert prober._current_phase(now) == "pre-chaos"

        prober._chaos_start_time = now - 10
        assert prober._current_phase(now) == "during-chaos"

        prober._chaos_end_time = now - 5
        assert prober._current_phase(now) == "post-chaos"

    def test_aggregate_operations(self):
        entries = [
            {"redis": {"write": {"ops_per_second": 100, "latency_ms": 10, "status": "ok"}}},
            {"redis": {"write": {"ops_per_second": 200, "latency_ms": 5, "status": "ok"}}},
            {"redis": {"write": {"ops_per_second": None, "latency_ms": None, "status": "error"}}},
        ]

        result = _ContinuousProberBase._aggregate_operations(entries, "redis")
        assert "write" in result
        assert result["write"]["meanOpsPerSecond"] == 150.0
        assert result["write"]["sampleCount"] == 2
        assert result["write"]["errorCount"] == 1
