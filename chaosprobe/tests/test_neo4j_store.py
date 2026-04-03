"""Tests for the Neo4j graph store and analysis functions.

All tests use mocked Neo4j sessions so no running database is needed.
"""

from unittest.mock import MagicMock, patch

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


def _make_store_with_tx():
    """Create a Neo4jStore with mocked session and transaction."""
    with patch("chaosprobe.storage.neo4j_store._require_neo4j") as mock_req:
        mock_neo4j = MagicMock()
        mock_req.return_value = mock_neo4j
        driver = MagicMock()
        mock_neo4j.GraphDatabase.driver.return_value = driver
        session = MagicMock()
        tx = MagicMock()
        session.begin_transaction.return_value.__enter__ = MagicMock(return_value=tx)
        session.begin_transaction.return_value.__exit__ = MagicMock(return_value=False)
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)

        from chaosprobe.storage.neo4j_store import Neo4jStore
        store = Neo4jStore("bolt://fake", "u", "p")
        return store, tx


def _make_run_data(**overrides):
    """Create a minimal run data dict for testing."""
    data = {
        "runId": "run-001",
        "timestamp": "2026-03-29T12:00:00Z",
        "summary": {
            "totalExperiments": 1,
            "passed": 0,
            "failed": 1,
            "overallVerdict": "FAIL",
            "resilienceScore": 85.0,
        },
        "scenario": {
            "experiments": [
                {
                    "file": "experiment.yaml",
                    "content": {
                        "spec": {
                            "appinfo": {"applabel": "app=checkoutservice"},
                        },
                    },
                },
            ],
        },
        "experiments": [
            {
                "name": "pod-delete",
                "engineName": "pod-delete-abc123",
                "result": {"verdict": "Pass", "phase": "Completed", "failStep": ""},
                "probes": [{"name": "http-probe", "status": "passed"}],
                "probeSuccessPercentage": 100.0,
            },
        ],
        "metrics": {
            "deploymentName": "checkoutservice",
            "timeWindow": {
                "start": "2026-03-29T12:00:00Z",
                "end": "2026-03-29T12:05:00Z",
                "duration_s": 300.0,
            },
            "recovery": {
                "deploymentName": "checkoutservice",
                "recoveryEvents": [
                    {
                        "deletionTime": "2026-03-29T12:01:00Z",
                        "scheduledTime": "2026-03-29T12:01:01Z",
                        "readyTime": "2026-03-29T12:01:03Z",
                        "deletionToScheduled_ms": 1000,
                        "scheduledToReady_ms": 2000,
                        "totalRecovery_ms": 3000,
                    },
                ],
                "summary": {
                    "count": 1,
                    "completedCycles": 1,
                    "incompleteCycles": 0,
                    "meanRecovery_ms": 3000.0,
                    "medianRecovery_ms": 3000.0,
                    "minRecovery_ms": 3000,
                    "maxRecovery_ms": 3000,
                    "p95Recovery_ms": 3000.0,
                },
            },
            "podStatus": {
                "pods": [
                    {
                        "name": "checkoutservice-abc123",
                        "phase": "Running",
                        "node": "worker1",
                        "restartCount": 2,
                        "conditions": {"Ready": {"status": "True"}},
                    },
                ],
                "totalRestarts": 2,
            },
            "nodeInfo": {
                "nodeName": "worker1",
                "allocatable": {"cpu": "2", "memory": "4028928Ki"},
                "capacity": {"cpu": "2", "memory": "4128928Ki"},
            },
            "eventTimeline": [
                {"time": "2026-03-29T12:01:00Z", "type": "DELETED",
                 "pod": "checkoutservice-old", "phase": "Running"},
            ],
            "containerLogs": {
                "pods": {
                    "checkoutservice-abc123": {
                        "containers": {
                            "server": {
                                "current": "INFO starting server on :8080\nERROR connection refused",
                                "previous": "FATAL out of memory",
                            },
                        },
                        "restartCount": 2,
                    },
                },
                "config": {"sinceSeconds": 330, "tailLines": 500},
            },
        },
    }
    data.update(overrides)
    return data


def _make_full_run_data(**overrides):
    """Create run data with all metric types populated."""
    data = _make_run_data()
    data["metrics"]["latency"] = {
        "timeSeries": [],
        "phases": {
            "pre-chaos": {"sampleCount": 5, "routes": {"/": {"mean_ms": 100}}},
            "during-chaos": {"sampleCount": 10, "routes": {"/": {"mean_ms": 500}}},
            "post-chaos": {"sampleCount": 5, "routes": {"/": {"mean_ms": 120}}},
        },
        "config": {"interval_s": 2.0},
    }
    data["metrics"]["resources"] = {
        "available": True,
        "nodeName": "worker1",
        "phases": {
            "pre-chaos": {
                "sampleCount": 5,
                "node": {"meanCpu_millicores": 200, "maxCpu_millicores": 300,
                         "meanMemory_bytes": 1000000, "maxMemory_bytes": 1200000,
                         "meanCpu_percent": 10.0, "maxCpu_percent": 15.0,
                         "meanMemory_percent": 50.0, "maxMemory_percent": 60.0},
            },
            "during-chaos": {
                "sampleCount": 10,
                "node": {"meanCpu_millicores": 500, "maxCpu_millicores": 900,
                         "meanMemory_bytes": 1500000, "maxMemory_bytes": 1800000,
                         "meanCpu_percent": 25.0, "maxCpu_percent": 45.0,
                         "meanMemory_percent": 75.0, "maxMemory_percent": 90.0},
            },
            "post-chaos": {"sampleCount": 0},
        },
    }
    data["metrics"]["prometheus"] = {
        "available": True,
        "phases": {
            "during-chaos": {
                "sampleCount": 10,
                "metrics": {"cpu_usage": {"mean": 0.5, "max": 0.9}},
            },
        },
    }
    data["metrics"]["redis"] = {
        "phases": {
            "during-chaos": {
                "sampleCount": 5,
                "redis": {"write": {"meanOpsPerSecond": 40.0}},
            },
        },
    }
    data["metrics"]["disk"] = {
        "phases": {
            "during-chaos": {
                "sampleCount": 5,
                "disk": {"write": {"meanOpsPerSecond": 5.0}},
            },
        },
    }
    data["loadGeneration"] = {
        "profile": "steady",
        "stats": {
            "totalRequests": 1000,
            "totalFailures": 50,
            "avgResponseTime_ms": 200.0,
            "p95ResponseTime_ms": 500.0,
            "p99ResponseTime_ms": 800.0,
            "requestsPerSecond": 10.0,
        },
    }
    data.update(overrides)
    return data


def _get_tx_calls(tx, cypher_substring):
    """Return tx.run calls whose first arg contains the given substring."""
    return [c for c in tx.run.call_args_list if cypher_substring in str(c)]


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

            test_routes = [
                ("frontend", "cartservice", "cartservice:7070", "grpc", "Cart"),
                ("cartservice", "redis-cart", "redis-cart:6379", "tcp", "Redis"),
            ]
            store.sync_service_dependencies(routes=test_routes)

            # One MERGE per route + one final EXPOSES query
            assert session.run.call_count == len(test_routes) + 1

    def test_skips_when_no_routes(self):
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
            store.sync_service_dependencies()  # No routes

            # Should not have called session.run at all
            assert session.run.call_count == 0


# ---------------------------------------------------------------------------
# Neo4jStore — sync_run
# ---------------------------------------------------------------------------


class TestSyncRun:
    def test_creates_experiment_and_strategy_nodes(self):
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())

        # Verify experiment node was created
        assert len(_get_tx_calls(tx, "ChaosRun")) > 0
        # Verify strategy node was created
        assert len(_get_tx_calls(tx, "PlacementStrategy")) > 0

    def test_creates_targeted_by_edges(self):
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())

        targeted = _get_tx_calls(tx, "TARGETED_BY")
        # checkoutservice from both applabel and deploymentName (deduplicated = 1)
        assert len(targeted) == 1

    def test_creates_scheduled_on_edges_with_placement(self):
        store, tx = _make_store_with_tx()
        run_data = _make_run_data(
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

        scheduled = _get_tx_calls(tx, "SCHEDULED_ON")
        assert len(scheduled) == 2

    def test_infers_strategy_from_scenario_metadata(self):
        store, tx = _make_store_with_tx()
        run_data = _make_run_data(
            scenario={"metadata": {"strategy": "spread"}},
        )
        store.sync_run(run_data)

        cypher_calls = [str(c) for c in tx.run.call_args_list]
        assert any("spread" in c for c in cypher_calls)

    def test_stores_recovery_cycles(self):
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())

        # Should have DETACH DELETE + 1 CREATE for the single recovery event
        cycle_creates = _get_tx_calls(tx, "RecoveryCycle")
        assert len(cycle_creates) >= 2  # delete + create

    def test_stores_experiment_results(self):
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())

        result_creates = _get_tx_calls(tx, "ExperimentResult")
        assert len(result_creates) >= 2  # delete + create

    def test_stores_pod_snapshots(self):
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())

        pod_creates = _get_tx_calls(tx, "PodSnapshot")
        assert len(pod_creates) >= 2  # delete + create

        # Verify RUNNING_ON edge is created for pods with a node
        running_on_calls = _get_tx_calls(tx, "RUNNING_ON")
        assert len(running_on_calls) >= 1

    def test_stores_container_logs(self):
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())

        log_calls = _get_tx_calls(tx, "ContainerLog")
        assert len(log_calls) >= 2  # delete + create

        # Verify HAS_CONTAINER_LOG in log creation (via PodSnapshot or fallback)
        edge_calls = _get_tx_calls(tx, "HAS_CONTAINER_LOG")
        assert len(edge_calls) >= 1  # create (clear is now DETACH DELETE by run_id)

    def test_container_logs_linked_via_pod_snapshot(self):
        """Container logs should attempt to link through PodSnapshot."""
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())

        # The log creation query should reference PodSnapshot for linking
        log_creates = _get_tx_calls(tx, "PodSnapshot {run_id:")
        # At least PodSnapshot create + ContainerLog linking + BELONGS_TO
        assert len(log_creates) >= 3

    def test_pod_snapshot_belongs_to_deployment(self):
        """PodSnapshot should be linked to its parent Deployment."""
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())

        belongs_to_calls = _get_tx_calls(tx, "BELONGS_TO")
        assert len(belongs_to_calls) >= 1

    def test_probe_results_stored_as_nodes(self):
        """Probes should be stored as individual ProbeResult nodes."""
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())

        probe_calls = _get_tx_calls(tx, "ProbeResult")
        # At least the clear + 1 probe create
        assert len(probe_calls) >= 2

        # Verify HAS_PROBE relationship
        has_probe_calls = _get_tx_calls(tx, "HAS_PROBE")
        assert len(has_probe_calls) >= 1

    def test_experiment_result_no_json_probes(self):
        """ExperimentResult should NOT store probes as JSON blob."""
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())

        # The ExperimentResult CREATE should not contain a 'probes:' property
        result_creates = [
            c for c in tx.run.call_args_list
            if "CREATE (r:ExperimentResult" in str(c)
        ]
        for call in result_creates:
            cypher = str(call)
            assert "probes: $probes" not in cypher

    def test_stores_metrics_phases_for_all_types(self):
        store, tx = _make_store_with_tx()
        store.sync_run(_make_full_run_data())

        phase_creates = _get_tx_calls(tx, "MetricsPhase")
        # delete (1) + latency (3 phases) + resources (2 non-empty) +
        # prometheus (1) + redis (1) + disk (1)
        assert len(phase_creates) >= 8

    def test_stores_load_generation_data(self):
        store, tx = _make_store_with_tx()
        store.sync_run(_make_full_run_data())

        # Load gen stats stored on the experiment node
        exp_calls = _get_tx_calls(tx, "load_profile")
        assert len(exp_calls) >= 1

    def test_stores_enriched_experiment_properties(self):
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())

        # Verify enriched properties are in the experiment MERGE
        exp_calls = _get_tx_calls(tx, "median_recovery_ms")
        assert len(exp_calls) >= 1

        time_calls = _get_tx_calls(tx, "time_window_start")
        assert len(time_calls) >= 1

        restart_calls = _get_tx_calls(tx, "total_restarts")
        assert len(restart_calls) >= 1

    def test_stores_node_info(self):
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())

        node_calls = _get_tx_calls(tx, "node_name")
        assert len(node_calls) >= 1
        node_calls = _get_tx_calls(tx, "node_capacity_cpu")
        assert len(node_calls) >= 1

    def test_stores_event_timeline_as_json(self):
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())

        timeline_calls = _get_tx_calls(tx, "event_timeline")
        assert len(timeline_calls) >= 1

    def test_handles_empty_metrics(self):
        """sync_run should not fail when metrics sections are missing."""
        store, tx = _make_store_with_tx()
        run_data = _make_run_data()
        # Strip most metrics
        run_data["metrics"] = {"deploymentName": "test"}
        run_data["experiments"] = []
        store.sync_run(run_data)

        # Should still create experiment + strategy nodes
        assert len(_get_tx_calls(tx, "ChaosRun")) > 0

    def test_idempotent_resync(self):
        """Calling sync_run twice should clear and recreate child nodes."""
        store, tx = _make_store_with_tx()
        store.sync_run(_make_run_data())
        first_count = tx.run.call_count

        tx.reset_mock()
        store.sync_run(_make_run_data())
        second_count = tx.run.call_count

        # Same number of calls both times (DETACH DELETE + recreate)
        assert first_count == second_count


# ---------------------------------------------------------------------------
# Neo4jStore — ensure_schema
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    def test_creates_constraints_and_indexes(self):
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

            # 5 constraints + 11 indexes = 16 calls
            assert session.run.call_count == 16
            constraint_calls = [c for c in session.run.call_args_list
                                if "CREATE CONSTRAINT" in c[0][0]]
            index_calls = [c for c in session.run.call_args_list
                           if "CREATE INDEX" in c[0][0]]
            assert len(constraint_calls) == 5
            assert len(index_calls) == 11


# ---------------------------------------------------------------------------
# Neo4jStore — status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_counts_all_node_types(self):
        with patch("chaosprobe.storage.neo4j_store._require_neo4j") as mock_req:
            mock_neo4j = MagicMock()
            mock_req.return_value = mock_neo4j
            driver = MagicMock()
            mock_neo4j.GraphDatabase.driver.return_value = driver
            session = MagicMock()
            driver.session.return_value.__enter__ = MagicMock(return_value=session)
            driver.session.return_value.__exit__ = MagicMock(return_value=False)
            session.run.return_value.single.return_value = {"c": 0}

            from chaosprobe.storage.neo4j_store import Neo4jStore
            store = Neo4jStore("bolt://fake", "u", "p")
            result = store.status()

            # Should query all 14 node types
            assert session.run.call_count == 14
            assert "RecoveryCycle" in result
            assert "MetricsPhase" in result
            assert "PodSnapshot" in result
            assert "ExperimentResult" in result
            assert "ProbeResult" in result
            assert "MetricsSample" in result
            assert "AnomalyLabel" in result
            assert "CascadeEvent" in result


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


# ---------------------------------------------------------------------------
# Neo4jStore — sync cascade timeline
# ---------------------------------------------------------------------------


class TestSyncCascadeTimeline:
    def test_stores_cascade_events(self):
        store, tx = _make_store_with_tx()
        data = _make_run_data()
        data["cascadeTimeline"] = [
            {"targetService": "frontend", "affectedRoutes": ["/"], "summary": {}},
            {"targetService": "checkout", "affectedRoutes": ["/cart"], "summary": {}},
        ]
        store.sync_run(data)

        cascade_calls = _get_tx_calls(tx, "CascadeEvent")
        # 1 DETACH DELETE + 2 CREATE
        assert len(cascade_calls) >= 3

    def test_empty_cascade_timeline(self):
        store, tx = _make_store_with_tx()
        data = _make_run_data()
        data["cascadeTimeline"] = []
        store.sync_run(data)

        create_cascade = [c for c in tx.run.call_args_list
                          if "CREATE (c:CascadeEvent" in str(c)]
        assert len(create_cascade) == 0


# ---------------------------------------------------------------------------
# Neo4jStore — session ID support
# ---------------------------------------------------------------------------


class TestSessionId:
    def test_session_id_stored_on_chaosrun(self):
        store, tx = _make_store_with_tx()
        data = _make_run_data()
        data["sessionId"] = "20260402-013423"
        store.sync_run(data)

        merge_calls = _get_tx_calls(tx, "MERGE (e:ChaosRun")
        assert len(merge_calls) >= 1
        call_str = str(merge_calls[0])
        assert "session_id" in call_str

    def test_missing_session_id_defaults_empty(self):
        store, tx = _make_store_with_tx()
        data = _make_run_data()
        # no sessionId key
        store.sync_run(data)

        merge_calls = _get_tx_calls(tx, "MERGE (e:ChaosRun")
        assert len(merge_calls) >= 1


# ---------------------------------------------------------------------------
# Neo4jStore — scenario stored as JSON
# ---------------------------------------------------------------------------


class TestScenarioStorage:
    def test_scenario_json_set_on_chaosrun(self):
        store, tx = _make_store_with_tx()
        data = _make_run_data()
        store.sync_run(data)

        scenario_calls = _get_tx_calls(tx, "scenario_json")
        assert len(scenario_calls) >= 1
