"""Tests for graph/analysis.py functions."""

from unittest.mock import MagicMock

from chaosprobe.graph.analysis import (
    blast_radius_report,
    colocation_impact,
    strategy_summary,
    topology_comparison,
)


class TestBlastRadiusReport:
    def test_basic(self):
        store = MagicMock()
        store.get_blast_radius.return_value = [
            {"service": "frontend", "hops": 1},
            {"service": "cartservice", "hops": 2},
        ]
        result = blast_radius_report(store, "checkout", max_hops=3)
        assert result["targetService"] == "checkout"
        assert result["maxHops"] == 3
        assert result["totalAffected"] == 2
        assert len(result["affectedServices"]) == 2
        store.get_blast_radius.assert_called_once_with("checkout", max_hops=3)

    def test_empty(self):
        store = MagicMock()
        store.get_blast_radius.return_value = []
        result = blast_radius_report(store, "isolated")
        assert result["totalAffected"] == 0
        assert result["affectedServices"] == []


class TestTopologyComparison:
    def test_multiple_runs(self):
        store = MagicMock()
        store.get_topology.side_effect = [
            {"node1": ["nginx"]},
            {"node2": ["redis"]},
        ]
        result = topology_comparison(store, ["run1", "run2"])
        assert "run1" in result["runs"]
        assert "run2" in result["runs"]
        assert store.get_topology.call_count == 2

    def test_empty_runs(self):
        store = MagicMock()
        result = topology_comparison(store, [])
        assert result["runs"] == {}


class TestColocationImpact:
    def test_basic(self):
        store = MagicMock()
        store.get_colocation_analysis.return_value = [
            {"node": "node1", "deployments": ["a", "b", "c"]},
            {"node": "node2", "deployments": ["d"]},
        ]
        result = colocation_impact(store, "run1")
        assert result["runId"] == "run1"
        assert result["sharedNodes"] == 2
        assert result["maxDensity"] == 3

    def test_empty_groups(self):
        store = MagicMock()
        store.get_colocation_analysis.return_value = []
        result = colocation_impact(store, "run1")
        assert result["sharedNodes"] == 0
        assert result["maxDensity"] == 0


class TestStrategySummary:
    def test_aggregation(self):
        store = MagicMock()
        store.compare_strategies_graph.return_value = [
            {"strategy": "spread", "resilience_score": 80, "mean_recovery_ms": 100},
            {"strategy": "spread", "resilience_score": 90, "mean_recovery_ms": 200},
            {"strategy": "colocated", "resilience_score": 50, "mean_recovery_ms": 500},
        ]
        result = strategy_summary(store, run_ids=["r1", "r2", "r3"])
        assert "spread" in result["strategies"]
        assert result["strategies"]["spread"]["runCount"] == 2
        assert result["strategies"]["spread"]["avgResilienceScore"] == 85.0
        assert result["strategies"]["spread"]["avgRecoveryMs"] == 150.0
        assert result["strategies"]["colocated"]["runCount"] == 1

    def test_missing_recovery(self):
        store = MagicMock()
        store.compare_strategies_graph.return_value = [
            {"strategy": "spread", "resilience_score": 80, "mean_recovery_ms": None},
        ]
        result = strategy_summary(store)
        assert result["strategies"]["spread"]["avgRecoveryMs"] is None

    def test_no_rows(self):
        store = MagicMock()
        store.compare_strategies_graph.return_value = []
        result = strategy_summary(store)
        assert result["strategies"] == {}
