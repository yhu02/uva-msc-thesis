"""Tests for the resource utilization measurement module."""

import threading
import time
from unittest.mock import MagicMock, patch

from kubernetes.client.rest import ApiException

from chaosprobe.metrics.resources import (
    ContinuousResourceProber,
    parse_cpu_quantity,
    parse_memory_quantity,
)


class TestParseCpuQuantity:
    def test_millicores(self):
        assert parse_cpu_quantity("250m") == 250.0

    def test_nanocores(self):
        assert parse_cpu_quantity("500000n") == 0.5

    def test_whole_cores(self):
        assert parse_cpu_quantity("2") == 2000.0

    def test_fractional_millicores(self):
        assert parse_cpu_quantity("100m") == 100.0

    def test_zero(self):
        assert parse_cpu_quantity("0") == 0.0

    def test_large_nanocores(self):
        assert parse_cpu_quantity("1500000000n") == 1500.0

    def test_whitespace_stripped(self):
        assert parse_cpu_quantity("  412m  ") == 412.0

    def test_microcores(self):
        assert parse_cpu_quantity("196250u") == 196.25

    def test_microcores_large(self):
        assert parse_cpu_quantity("1000000u") == 1000.0


class TestParseMemoryQuantity:
    def test_kibibytes(self):
        assert parse_memory_quantity("1024Ki") == 1024 * 1024

    def test_mebibytes(self):
        assert parse_memory_quantity("100Mi") == 100 * 1024 ** 2

    def test_gibibytes(self):
        assert parse_memory_quantity("2Gi") == 2 * 1024 ** 3

    def test_tebibytes(self):
        assert parse_memory_quantity("1Ti") == 1024 ** 4

    def test_decimal_si_kilo(self):
        assert parse_memory_quantity("1000k") == 1_000_000

    def test_decimal_si_mega(self):
        assert parse_memory_quantity("100M") == 100_000_000

    def test_decimal_si_giga(self):
        assert parse_memory_quantity("2G") == 2_000_000_000

    def test_raw_bytes(self):
        assert parse_memory_quantity("1073741824") == 1073741824

    def test_zero(self):
        assert parse_memory_quantity("0") == 0

    def test_whitespace_stripped(self):
        assert parse_memory_quantity("  4096Ki  ") == 4096 * 1024


class TestContinuousResourceProberPhaseSplitting:
    def _make_prober(self):
        prober = ContinuousResourceProber.__new__(ContinuousResourceProber)
        prober._lock = threading.Lock()
        return prober

    def test_phase_splitting_with_data(self):
        prober = self._make_prober()

        series = [
            {
                "phase": "pre-chaos",
                "node": {"cpu_millicores": 500, "memory_bytes": 1_000_000_000,
                         "cpu_percent": 25.0, "memory_percent": 50.0},
                "pods": [{"pod": "svc-abc", "cpu_millicores": 100, "memory_bytes": 200_000_000}],
            },
            {
                "phase": "during-chaos",
                "node": {"cpu_millicores": 1800, "memory_bytes": 3_500_000_000,
                         "cpu_percent": 90.0, "memory_percent": 87.5},
                "pods": [{"pod": "svc-abc", "cpu_millicores": 50, "memory_bytes": 100_000_000}],
            },
            {
                "phase": "during-chaos",
                "node": {"cpu_millicores": 1600, "memory_bytes": 3_000_000_000,
                         "cpu_percent": 80.0, "memory_percent": 75.0},
                "pods": [{"pod": "svc-abc", "cpu_millicores": 60, "memory_bytes": 120_000_000}],
            },
            {
                "phase": "post-chaos",
                "node": {"cpu_millicores": 600, "memory_bytes": 1_200_000_000,
                         "cpu_percent": 30.0, "memory_percent": 60.0},
                "pods": [{"pod": "svc-abc", "cpu_millicores": 90, "memory_bytes": 180_000_000}],
            },
        ]

        phases = prober._split_phases(series)

        assert phases["pre-chaos"]["sampleCount"] == 1
        assert phases["during-chaos"]["sampleCount"] == 2
        assert phases["post-chaos"]["sampleCount"] == 1

        # During-chaos node aggregation: mean of 1800 and 1600 = 1700
        during_node = phases["during-chaos"]["node"]
        assert during_node["meanCpu_millicores"] == 1700.0
        assert during_node["maxCpu_millicores"] == 1800.0
        assert during_node["meanCpu_percent"] == 85.0
        assert during_node["maxCpu_percent"] == 90.0

    def test_phase_splitting_empty(self):
        prober = self._make_prober()
        phases = prober._split_phases([])

        assert phases["pre-chaos"]["sampleCount"] == 0
        assert phases["during-chaos"]["sampleCount"] == 0
        assert phases["post-chaos"]["sampleCount"] == 0

    def test_phase_splitting_no_node_data(self):
        """Entries without 'node' key should not crash aggregation."""
        prober = self._make_prober()
        series = [
            {"phase": "during-chaos", "pods": []},
        ]
        phases = prober._split_phases(series)
        assert phases["during-chaos"]["sampleCount"] == 1
        assert "node" not in phases["during-chaos"]


class TestContinuousResourceProberPhaseTransitions:
    def test_current_phase_transitions(self):
        prober = ContinuousResourceProber.__new__(ContinuousResourceProber)
        prober._lock = threading.Lock()
        prober._chaos_start_time = None
        prober._chaos_end_time = None

        now = time.time()
        assert prober._current_phase(now) == "pre-chaos"

        prober._chaos_start_time = now - 10
        assert prober._current_phase(now) == "during-chaos"

        prober._chaos_end_time = now - 5
        assert prober._current_phase(now) == "post-chaos"


class TestContinuousResourceProberResult:
    def test_result_when_no_data(self):
        prober = ContinuousResourceProber.__new__(ContinuousResourceProber)
        prober._lock = threading.Lock()
        prober._time_series = []
        prober._metrics_available = False
        prober._probe_errors = 0

        result = prober.result()
        assert result["available"] is False
        assert "reason" in result

    def test_result_with_data(self):
        prober = ContinuousResourceProber.__new__(ContinuousResourceProber)
        prober._lock = threading.Lock()
        prober._probe_errors = 0
        prober._node_name = "node-1"
        prober._node_capacity_cpu = 4000.0
        prober._node_capacity_mem = 8_000_000_000
        prober._deployment_name = "checkoutservice"
        prober.namespace = "online-boutique"
        prober.interval = 5.0
        prober._time_series = [
            {
                "phase": "pre-chaos",
                "timestamp": "2026-03-29T12:00:00+00:00",
                "elapsed_s": 0.0,
                "node": {"cpu_millicores": 500, "memory_bytes": 2_000_000_000},
                "pods": [],
            },
        ]

        result = prober.result()
        assert result["available"] is True
        assert result["nodeName"] == "node-1"
        assert result["nodeCapacity"]["cpu_millicores"] == 4000.0
        assert "timeSeries" in result
        assert "phases" in result
        assert "probeErrors" not in result  # no errors

    def test_result_includes_probe_errors(self):
        prober = ContinuousResourceProber.__new__(ContinuousResourceProber)
        prober._lock = threading.Lock()
        prober._probe_errors = 3
        prober._node_name = "node-1"
        prober._node_capacity_cpu = 2000.0
        prober._node_capacity_mem = 4_000_000_000
        prober._deployment_name = "svc"
        prober.namespace = "ns"
        prober.interval = 5.0
        prober._time_series = [
            {"phase": "pre-chaos", "node": {"cpu_millicores": 100, "memory_bytes": 500_000_000}, "pods": []},
        ]

        result = prober.result()
        assert result["probeErrors"] == 3


class TestContinuousResourceProberApiUnavailable:
    @patch("chaosprobe.metrics.resources.ensure_k8s_config")
    @patch("chaosprobe.metrics.resources.client")
    def test_start_without_metrics_server(self, mock_client, mock_config):
        """When metrics-server is not installed, start() should not spawn a thread."""
        mock_core = MagicMock()
        mock_custom = MagicMock()
        mock_client.CoreV1Api.return_value = mock_core
        mock_client.CustomObjectsApi.return_value = mock_custom

        # Simulate a pod on a node
        mock_pod = MagicMock()
        mock_pod.spec.node_name = "node-1"
        mock_core.list_namespaced_pod.return_value = MagicMock(items=[mock_pod])

        # Simulate node capacity
        mock_node = MagicMock()
        mock_node.status.allocatable = {"cpu": "4", "memory": "8Gi"}
        mock_core.read_node.return_value = mock_node

        # Simulate metrics-server not available
        mock_custom.get_cluster_custom_object.side_effect = ApiException(status=404)

        prober = ContinuousResourceProber("online-boutique", "checkoutservice")
        prober.start()

        assert prober._metrics_available is False
        assert prober._thread is None  # no thread spawned

    @patch("chaosprobe.metrics.resources.ensure_k8s_config")
    @patch("chaosprobe.metrics.resources.client")
    def test_start_without_pods(self, mock_client, mock_config):
        """When no pods found, start() should disable itself."""
        mock_core = MagicMock()
        mock_client.CoreV1Api.return_value = mock_core
        mock_client.CustomObjectsApi.return_value = MagicMock()

        # No pods
        mock_core.list_namespaced_pod.return_value = MagicMock(items=[])

        prober = ContinuousResourceProber("online-boutique", "checkoutservice")
        prober.start()

        assert prober._metrics_available is False
