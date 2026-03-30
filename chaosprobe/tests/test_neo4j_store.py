"""Tests for the Neo4j graph store and analysis functions.

All tests use mocked Neo4j sessions so no running database is needed.
"""

from unittest.mock import MagicMock, patch, call

import pytest

from chaosprobe.graph.analysis import (
    blast_radius_report,
    colocation_impact,
    critical_path_analysis,
    strategy_summary,
    topology_comparison,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store():
    """Create a Neo4jStore-like mock with a working session context manager."""
    store = MagicMock()
    session = MagicMock()
    store._driver.session.return_value.__enter__ = MagicMock(return_value=session)
    store._driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return store, session


# ---------------------------------------------------------------------------
# Neo4jStore unit tests (import guard)
# ---------------------------------------------------------------------------


class TestRequireNeo4j:
    def test_import_error_message(self):
        with patch.dict("sys.modules", {"neo4j": None}):
            from chaosprobe.storage.neo4j_store import _require_neo4j
            with pytest.raises(ImportError, match="uv pip install chaosprobe"):
                _require_neo4j()


# ---------------------------------------------------------------------------
# Neo4jStore — sync_topology
# ---------------------------------------------------------------------------


class TestSyncTopology:
    def test_merges_nodes_and_deployments(self):
        with patch("chaosprobe.storage.neo4j_store._require_neo4j") as mock_req:
            mock_neo4j = MagicMock()
            mock_req.return_value = mock_neo4j
            driver = MagicMock()
            mock_neo4j.GraphDatabase.driver.return_value = driver
            session = MagicMock()
            driver.session.return_value.__enter__ = MagicMock(return_value=session)
            driver.session.return_value.__exit__ = MagicMock(return_value=False)

            from chaosprobe.storage.neo4j_store import Neo4jStore
            store = Neo4jStore("bolt://fake", "u", "p")

            nodes = [
                {"name": "worker1", "cpu": 4000, "memory": 8000000, "control_plane": False},
                {"name": "cp1", "cpu": 2000, "memory": 4000000, "control_plane": True},
            ]
            deps = [
                {"name": "frontend", "namespace": "online-boutique", "replicas": 1},
            ]
            store.sync_topology(nodes, deps)

            # 2 node merges + 1 deployment merge = 3 session.run calls
            assert session.run.call_count == 3


class TestSyncServiceDependencies:
    def test_creates_service_nodes_and_edges(self):
        with patch("chaosprobe.storage.neo4j_store._require_neo4j") as mock_req:
            mock_neo4j = MagicMock()
            mock_req.return_value = mock_neo4j
            driver = MagicMock()
            mock_neo4j.GraphDatabase.driver.return_value = driver
            session = MagicMock()
            driver.session.return_value.__enter__ = MagicMock(return_value=session)
            driver.session.return_value.__exit__ = MagicMock(return_value=False)

            from chaosprobe.storage.neo4j_store import Neo4jStore
            store = Neo4jStore("bolt://fake", "u", "p")
            store.sync_service_dependencies()

            # One MERGE per route + one final EXPOSES query
            from chaosprobe.metrics.latency import ONLINE_BOUTIQUE_ROUTES
            assert session.run.call_count == len(ONLINE_BOUTIQUE_ROUTES) + 1


# ---------------------------------------------------------------------------
# Neo4jStore — sync_run
# ---------------------------------------------------------------------------


class TestSyncRun:
    def _make_run_data(self, **overrides):
        data = {
            "runId": "run-001",
            "timestamp": "2026-03-29T12:00:00Z",
            "summary": {
                "overallVerdict": "PASS",
                "resilienceScore": 85.0,
            },
            "experiments": [
                {
                    "spec": {
                        "spec": {
                            "appinfo": {"applabel": "app=checkoutservice"},
                        },
                    },
                },
            ],
            "metrics": {
                "recovery": {
                    "summary": {
                        "meanRecovery_ms": 1500.0,
                        "maxRecovery_ms": 2000.0,
                    },
                },
            },
        }
        data.update(overrides)
        return data

    def test_creates_experiment_and_strategy_nodes(self):
        with patch("chaosprobe.storage.neo4j_store._require_neo4j") as mock_req:
            mock_neo4j = MagicMock()
            mock_req.return_value = mock_neo4j
            driver = MagicMock()
            mock_neo4j.GraphDatabase.driver.return_value = driver
            session = MagicMock()
            driver.session.return_value.__enter__ = MagicMock(return_value=session)
            driver.session.return_value.__exit__ = MagicMock(return_value=False)

            from chaosprobe.storage.neo4j_store import Neo4jStore
            store = Neo4jStore("bolt://fake", "u", "p")
            store.sync_run(self._make_run_data())

            # experiment MERGE + strategy MERGE + TARGETED_BY = 3 calls
            assert session.run.call_count == 3

    def test_creates_scheduled_on_edges_with_placement(self):
        with patch("chaosprobe.storage.neo4j_store._require_neo4j") as mock_req:
            mock_neo4j = MagicMock()
            mock_req.return_value = mock_neo4j
            driver = MagicMock()
            mock_neo4j.GraphDatabase.driver.return_value = driver
            session = MagicMock()
            driver.session.return_value.__enter__ = MagicMock(return_value=session)
            driver.session.return_value.__exit__ = MagicMock(return_value=False)

            from chaosprobe.storage.neo4j_store import Neo4jStore
            store = Neo4jStore("bolt://fake", "u", "p")

            run_data = self._make_run_data(
                placement={
                    "strategy": "colocate",
                    "seed": None,
                    "assignments": {
                        "frontend": "worker1",
                        "cartservice": "worker1",
                    },
                },
            )
            store.sync_run(run_data)

            # experiment + strategy + targeted_by + 2 scheduled_on = 5
            assert session.run.call_count == 5

    def test_infers_strategy_from_scenario_metadata(self):
        with patch("chaosprobe.storage.neo4j_store._require_neo4j") as mock_req:
            mock_neo4j = MagicMock()
            mock_req.return_value = mock_neo4j
            driver = MagicMock()
            mock_neo4j.GraphDatabase.driver.return_value = driver
            session = MagicMock()
            driver.session.return_value.__enter__ = MagicMock(return_value=session)
            driver.session.return_value.__exit__ = MagicMock(return_value=False)

            from chaosprobe.storage.neo4j_store import Neo4jStore
            store = Neo4jStore("bolt://fake", "u", "p")
            run_data = self._make_run_data(
                scenario={"metadata": {"strategy": "spread"}},
            )
            store.sync_run(run_data)

            # Verify strategy name was "spread" in the MERGE call
            cypher_calls = [str(c) for c in session.run.call_args_list]
            assert any("spread" in c for c in cypher_calls)


# ---------------------------------------------------------------------------
# Neo4jStore — ensure_schema
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    def test_creates_six_constraints(self):
        with patch("chaosprobe.storage.neo4j_store._require_neo4j") as mock_req:
            mock_neo4j = MagicMock()
            mock_req.return_value = mock_neo4j
            driver = MagicMock()
            mock_neo4j.GraphDatabase.driver.return_value = driver
            session = MagicMock()
            driver.session.return_value.__enter__ = MagicMock(return_value=session)
            driver.session.return_value.__exit__ = MagicMock(return_value=False)

            from chaosprobe.storage.neo4j_store import Neo4jStore
            store = Neo4jStore("bolt://fake", "u", "p")
            store.ensure_schema()

            assert session.run.call_count == 6
            for c in session.run.call_args_list:
                assert "CREATE CONSTRAINT" in c[0][0]


# ---------------------------------------------------------------------------
# Graph analysis functions
# ---------------------------------------------------------------------------


class TestBlastRadiusReport:
    def test_returns_structured_report(self):
        store = MagicMock()
        store.get_blast_radius.return_value = [
            {"name": "frontend", "hops": 1},
            {"name": "checkoutservice", "hops": 2},
        ]

        report = blast_radius_report(store, "productcatalogservice", max_hops=3)

        assert report["targetService"] == "productcatalogservice"
        assert report["totalAffected"] == 2
        assert len(report["affectedServices"]) == 2
        store.get_blast_radius.assert_called_once_with(
            "productcatalogservice", max_hops=3,
        )

    def test_empty_blast_radius(self):
        store = MagicMock()
        store.get_blast_radius.return_value = []

        report = blast_radius_report(store, "redis-cart")
        assert report["totalAffected"] == 0
        assert report["affectedServices"] == []


class TestTopologyComparison:
    def test_returns_per_run_topology(self):
        store = MagicMock()
        store.get_topology.side_effect = [
            {"nodes": [{"node": "w1", "deployments": ["frontend"]}], "unscheduled": []},
            {"nodes": [{"node": "w2", "deployments": ["frontend"]}], "unscheduled": []},
        ]

        result = topology_comparison(store, ["run-1", "run-2"])
        assert "run-1" in result["runs"]
        assert "run-2" in result["runs"]
        assert store.get_topology.call_count == 2


class TestColocationImpact:
    def test_computes_density(self):
        store = MagicMock()
        store.get_colocation_analysis.return_value = [
            {"node": "worker1", "deployments": ["frontend", "cartservice", "redis-cart"]},
            {"node": "worker2", "deployments": ["adservice", "emailservice"]},
        ]

        result = colocation_impact(store, "run-1")
        assert result["sharedNodes"] == 2
        assert result["maxDensity"] == 3
        assert len(result["groups"]) == 2

    def test_empty_colocation(self):
        store = MagicMock()
        store.get_colocation_analysis.return_value = []

        result = colocation_impact(store, "run-1")
        assert result["sharedNodes"] == 0
        assert result["maxDensity"] == 0


class TestCriticalPathAnalysis:
    def test_returns_longest_chain(self):
        store, session = _make_store()
        record = MagicMock()
        record.__getitem__ = lambda self, k: {
            "chain": ["frontend", "checkoutservice", "paymentservice"],
            "depth": 2,
        }[k]
        session.run.return_value.single.return_value = record

        result = critical_path_analysis(store)
        assert result["depth"] == 2
        assert result["chain"] == ["frontend", "checkoutservice", "paymentservice"]

    def test_empty_graph(self):
        store, session = _make_store()
        session.run.return_value.single.return_value = None

        result = critical_path_analysis(store)
        assert result["chain"] == []
        assert result["depth"] == 0


class TestStrategySummary:
    def test_aggregates_by_strategy(self):
        store = MagicMock()
        store.compare_strategies_graph.return_value = [
            {"strategy": "colocate", "run_id": "r1", "resilience_score": 80.0, "mean_recovery_ms": 1000.0},
            {"strategy": "colocate", "run_id": "r2", "resilience_score": 90.0, "mean_recovery_ms": 1200.0},
            {"strategy": "spread", "run_id": "r3", "resilience_score": 95.0, "mean_recovery_ms": None},
        ]

        result = strategy_summary(store, run_ids=["r1", "r2", "r3"])
        strats = result["strategies"]

        assert strats["colocate"]["runCount"] == 2
        assert strats["colocate"]["avgResilienceScore"] == 85.0
        assert strats["colocate"]["avgRecoveryMs"] == 1100.0

        assert strats["spread"]["runCount"] == 1
        assert strats["spread"]["avgResilienceScore"] == 95.0
        assert strats["spread"]["avgRecoveryMs"] is None

    def test_empty_results(self):
        store = MagicMock()
        store.compare_strategies_graph.return_value = []

        result = strategy_summary(store)
        assert result["strategies"] == {}
