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
        assert parse_memory_quantity("100Mi") == 100 * 1024**2

    def test_gibibytes(self):
        assert parse_memory_quantity("2Gi") == 2 * 1024**3

    def test_tebibytes(self):
        assert parse_memory_quantity("1Ti") == 1024**4

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


# ---------------------------------------------------------------------------
# Helpers for aggregator / phase-splitter tests
# ---------------------------------------------------------------------------


def _make_entry(phase, nodes):
    """Return a synthetic time-series entry as emitted by the probe loop."""
    aggregated = ContinuousResourceProber._aggregate_node_metrics(nodes)
    stats = ContinuousResourceProber._node_stats(nodes)
    return {
        "phase": phase,
        "nodes": nodes,
        "node": aggregated,
        "nodeStats": stats,
        "pods": [],
    }


def _node(name, cpu_mc, mem_b, cpu_pct=None, mem_pct=None):
    n = {"name": name, "cpu_millicores": cpu_mc, "memory_bytes": mem_b}
    if cpu_pct is not None:
        n["cpu_percent"] = cpu_pct
    if mem_pct is not None:
        n["memory_percent"] = mem_pct
    return n


class TestAggregateNodeMetrics:
    def test_sums_absolute_units(self):
        nodes = [
            _node("a", 500, 2_000_000_000, 25.0, 50.0),
            _node("b", 300, 1_000_000_000, 15.0, 25.0),
        ]
        agg = ContinuousResourceProber._aggregate_node_metrics(nodes)
        assert agg["cpu_millicores"] == 800.0
        assert agg["memory_bytes"] == 3_000_000_000
        assert agg["nodeCount"] == 2

    def test_averages_percent(self):
        nodes = [
            _node("a", 500, 2_000_000_000, 20.0, 40.0),
            _node("b", 500, 2_000_000_000, 40.0, 80.0),
        ]
        agg = ContinuousResourceProber._aggregate_node_metrics(nodes)
        assert agg["cpu_percent"] == 30.0
        assert agg["memory_percent"] == 60.0

    def test_empty_list(self):
        assert ContinuousResourceProber._aggregate_node_metrics([]) == {}

    def test_missing_percent_on_some_nodes(self):
        nodes = [
            _node("a", 100, 10, cpu_pct=40.0, mem_pct=50.0),
            _node("b", 100, 10),  # no capacity known
        ]
        agg = ContinuousResourceProber._aggregate_node_metrics(nodes)
        # Only node 'a' contributes to percent average
        assert agg["cpu_percent"] == 40.0
        assert agg["memory_percent"] == 50.0
        # But absolute units still sum across all nodes
        assert agg["cpu_millicores"] == 200.0


class TestNodeStats:
    def test_spread_across_nodes(self):
        nodes = [
            _node("a", 100, 1, cpu_pct=10.0, mem_pct=20.0),
            _node("b", 100, 1, cpu_pct=30.0, mem_pct=40.0),
            _node("c", 100, 1, cpu_pct=50.0, mem_pct=60.0),
        ]
        stats = ContinuousResourceProber._node_stats(nodes)
        assert stats["maxCpu_percent"] == 50.0
        assert stats["minCpu_percent"] == 10.0
        assert "stddevCpu_percent" in stats
        assert stats["maxMemory_percent"] == 60.0

    def test_single_node_no_stddev(self):
        nodes = [_node("a", 100, 1, cpu_pct=50.0, mem_pct=50.0)]
        stats = ContinuousResourceProber._node_stats(nodes)
        assert stats["maxCpu_percent"] == 50.0
        assert "stddevCpu_percent" not in stats

    def test_empty_nodes(self):
        assert ContinuousResourceProber._node_stats([]) == {}


class TestContinuousResourceProberPhaseSplitting:
    def _make_prober(self):
        prober = ContinuousResourceProber.__new__(ContinuousResourceProber)
        prober._lock = threading.Lock()
        return prober

    def test_phase_splitting_with_data(self):
        prober = self._make_prober()

        series = [
            _make_entry(
                "pre-chaos",
                [
                    _node("n1", 200, 500_000_000, 10.0, 25.0),
                    _node("n2", 300, 500_000_000, 15.0, 25.0),
                ],
            ),
            _make_entry(
                "during-chaos",
                [
                    _node("n1", 900, 1_750_000_000, 45.0, 87.5),
                    _node("n2", 900, 1_750_000_000, 45.0, 87.5),
                ],
            ),
            _make_entry(
                "during-chaos",
                [
                    _node("n1", 800, 1_500_000_000, 40.0, 75.0),
                    _node("n2", 800, 1_500_000_000, 40.0, 75.0),
                ],
            ),
            _make_entry(
                "post-chaos",
                [
                    _node("n1", 300, 600_000_000, 15.0, 30.0),
                    _node("n2", 300, 600_000_000, 15.0, 30.0),
                ],
            ),
        ]

        phases = prober._split_phases(series)

        assert phases["pre-chaos"]["sampleCount"] == 1
        assert phases["during-chaos"]["sampleCount"] == 2
        assert phases["post-chaos"]["sampleCount"] == 1

        # Cluster-wide aggregate during chaos
        # tick 1: cpu_millicores sum = 1800, tick 2: sum = 1600 -> mean 1700
        during_node = phases["during-chaos"]["node"]
        assert during_node["meanCpu_millicores"] == 1700.0
        assert during_node["maxCpu_millicores"] == 1800.0
        # percentages are tick averages across nodes, then meaned across ticks
        assert during_node["meanCpu_percent"] == 42.5
        assert during_node["maxCpu_percent"] == 45.0
        # Per-node breakdown present
        per_node = phases["during-chaos"]["perNode"]
        assert set(per_node.keys()) == {"n1", "n2"}
        assert per_node["n1"]["maxCpu_millicores"] == 900.0
        assert per_node["n1"]["sampleCount"] == 2

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

    def test_peak_node_percent_reported(self):
        """The hottest single node across a phase is surfaced."""
        prober = self._make_prober()
        series = [
            _make_entry(
                "during-chaos",
                [
                    _node("n1", 500, 1_000_000_000, 20.0, 40.0),
                    _node("n2", 500, 1_000_000_000, 95.0, 90.0),  # hot
                ],
            ),
        ]
        phases = prober._split_phases(series)
        assert phases["during-chaos"]["node"]["peakNodeCpu_percent"] == 95.0
        assert phases["during-chaos"]["node"]["peakNodeMemory_percent"] == 90.0


class TestContinuousResourceProberPhaseTransitions:
    def test_current_phase_transitions(self):
        prober = ContinuousResourceProber.__new__(ContinuousResourceProber)
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
        prober._node_capacity = {
            "node-1": {"cpu_millicores": 4000.0, "memory_bytes": 8_000_000_000},
            "node-2": {"cpu_millicores": 4000.0, "memory_bytes": 8_000_000_000},
        }
        prober._deployment_name = "checkoutservice"
        prober.namespace = "online-boutique"
        prober.interval = 5.0
        prober._time_series = [
            _make_entry(
                "pre-chaos",
                [
                    _node("node-1", 500, 2_000_000_000, 12.5, 25.0),
                    _node("node-2", 200, 1_000_000_000, 5.0, 12.5),
                ],
            ),
        ]
        prober._time_series[0]["timestamp"] = "2026-03-29T12:00:00+00:00"
        prober._time_series[0]["elapsed_s"] = 0.0

        result = prober.result()
        assert result["available"] is True
        assert result["nodeNames"] == ["node-1", "node-2"]
        assert result["nodeCapacity"]["node-1"]["cpu_millicores"] == 4000.0
        assert "timeSeries" in result
        assert "phases" in result
        assert "probeErrors" not in result  # no errors

    def test_result_includes_probe_errors(self):
        prober = ContinuousResourceProber.__new__(ContinuousResourceProber)
        prober._lock = threading.Lock()
        prober._probe_errors = 3
        prober._node_capacity = {
            "node-1": {"cpu_millicores": 2000.0, "memory_bytes": 4_000_000_000},
        }
        prober._deployment_name = "svc"
        prober.namespace = "ns"
        prober.interval = 5.0
        prober._time_series = [
            _make_entry(
                "pre-chaos",
                [_node("node-1", 100, 500_000_000, 5.0, 12.5)],
            ),
        ]

        result = prober.result()
        assert result["probeErrors"] == 3


class TestProbeLoopIntegration:
    """End-to-end coverage of a single probe tick against mocked K8s APIs."""

    def _mock_clients(self):
        mock_core = MagicMock()
        mock_custom = MagicMock()

        # list_node -> two nodes with different capacities
        node_a = MagicMock()
        node_a.metadata.name = "worker-a"
        node_a.status.allocatable = {"cpu": "4", "memory": "8Gi"}
        node_b = MagicMock()
        node_b.metadata.name = "worker-b"
        node_b.status.allocatable = {"cpu": "4", "memory": "8Gi"}
        mock_core.list_node.return_value = MagicMock(items=[node_a, node_b])

        # list_cluster_custom_object -> metrics for both nodes
        mock_custom.list_cluster_custom_object.return_value = {
            "items": [
                {
                    "metadata": {"name": "worker-a"},
                    "usage": {"cpu": "2000m", "memory": "4Gi"},
                },
                {
                    "metadata": {"name": "worker-b"},
                    "usage": {"cpu": "400m", "memory": "1Gi"},
                },
            ]
        }

        # list_namespaced_custom_object -> pod metrics
        mock_custom.list_namespaced_custom_object.return_value = {
            "items": [
                {
                    "metadata": {
                        "name": "checkoutservice-abc",
                        "labels": {"app": "checkoutservice"},
                    },
                    "containers": [
                        {"usage": {"cpu": "100m", "memory": "256Mi"}},
                    ],
                },
                {
                    "metadata": {
                        "name": "cartservice-xyz",
                        "labels": {"app": "cartservice"},
                    },
                    "containers": [
                        {"usage": {"cpu": "50m", "memory": "128Mi"}},
                    ],
                },
            ]
        }

        return mock_core, mock_custom

    @patch("chaosprobe.metrics.resources.ensure_k8s_config")
    @patch("chaosprobe.metrics.resources.client")
    def test_single_tick_captures_all_nodes(self, mock_client, mock_config):
        mock_core, mock_custom = self._mock_clients()
        mock_client.CoreV1Api.return_value = mock_core
        mock_client.CustomObjectsApi.return_value = mock_custom

        prober = ContinuousResourceProber("online-boutique", "checkoutservice", interval=0.01)
        # Drive one iteration manually instead of starting the thread.
        prober._refresh_node_capacity()
        # metrics-server is reachable
        assert prober._check_metrics_server() is True

        node_metrics = prober._fetch_all_node_metrics()
        assert [n["name"] for n in node_metrics] == ["worker-a", "worker-b"]
        # worker-a used 2000m of 4000m → 50%
        a = next(n for n in node_metrics if n["name"] == "worker-a")
        assert a["cpu_percent"] == 50.0
        b = next(n for n in node_metrics if n["name"] == "worker-b")
        assert b["cpu_percent"] == 10.0

        agg = ContinuousResourceProber._aggregate_node_metrics(node_metrics)
        # Sum of absolute units, mean of percents
        assert agg["cpu_millicores"] == 2400.0
        assert agg["cpu_percent"] == 30.0
        assert agg["nodeCount"] == 2

        pods = prober._fetch_pod_metrics()
        assert {p["pod"] for p in pods} == {
            "checkoutservice-abc",
            "cartservice-xyz",
        }
        # Deployment label propagates so callers can filter
        check = next(p for p in pods if p["pod"] == "checkoutservice-abc")
        assert check["deployment"] == "checkoutservice"


class TestContinuousResourceProberApiUnavailable:
    @patch("chaosprobe.metrics.resources.ensure_k8s_config")
    @patch("chaosprobe.metrics.resources.client")
    def test_start_without_metrics_server(self, mock_client, mock_config):
        """When metrics-server is not installed, start() should not spawn a thread."""
        mock_core = MagicMock()
        mock_custom = MagicMock()
        mock_client.CoreV1Api.return_value = mock_core
        mock_client.CustomObjectsApi.return_value = mock_custom

        # One node is present so capacity discovery succeeds
        mock_node = MagicMock()
        mock_node.metadata.name = "node-1"
        mock_node.status.allocatable = {"cpu": "4", "memory": "8Gi"}
        mock_core.list_node.return_value = MagicMock(items=[mock_node])

        # metrics-server rejects the probe
        mock_custom.list_cluster_custom_object.side_effect = ApiException(status=404)

        prober = ContinuousResourceProber("online-boutique", "checkoutservice")
        prober.start()

        assert prober._metrics_available is False
        assert prober._thread is None  # no thread spawned

    @patch("chaosprobe.metrics.resources.ensure_k8s_config")
    @patch("chaosprobe.metrics.resources.client")
    def test_start_without_nodes(self, mock_client, mock_config):
        """When no cluster nodes are visible, start() should disable itself."""
        mock_core = MagicMock()
        mock_client.CoreV1Api.return_value = mock_core
        mock_client.CustomObjectsApi.return_value = MagicMock()

        mock_core.list_node.return_value = MagicMock(items=[])

        prober = ContinuousResourceProber("online-boutique", "checkoutservice")
        prober.start()

        assert prober._metrics_available is False

    @patch("chaosprobe.metrics.resources.ensure_k8s_config")
    @patch("chaosprobe.metrics.resources.client")
    def test_start_handles_list_node_api_exception(self, mock_client, mock_config):
        """list_node ApiException leaves _node_capacity empty → probing disabled."""
        mock_core = MagicMock()
        mock_client.CoreV1Api.return_value = mock_core
        mock_client.CustomObjectsApi.return_value = MagicMock()

        mock_core.list_node.side_effect = ApiException(status=500)

        prober = ContinuousResourceProber("online-boutique", "checkoutservice")
        prober.start()

        assert prober._metrics_available is False
        assert prober._thread is None
