"""Tests for the database storage module."""

import json
import os
import pytest
import tempfile

from chaosprobe.storage.sqlite import SQLiteStore


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_results.db")


@pytest.fixture
def store(db_path):
    s = SQLiteStore(db_path=db_path)
    yield s
    s.close()


@pytest.fixture
def sample_run_data():
    return {
        "schemaVersion": "2.0.0",
        "runId": "run-2026-03-20-120000-abc123",
        "timestamp": "2026-03-20T12:00:00+00:00",
        "scenario": {"directory": "/scenarios/online-boutique"},
        "infrastructure": {"namespace": "online-boutique"},
        "experiments": [
            {
                "name": "pod-delete",
                "result": {
                    "verdict": "Pass",
                    "probeSuccessPercentage": 95.0,
                },
            }
        ],
        "summary": {
            "totalExperiments": 1,
            "passed": 1,
            "failed": 0,
            "resilienceScore": 95.0,
            "overallVerdict": "PASS",
        },
        "metrics": {
            "recovery": {
                "summary": {
                    "meanRecovery_ms": 1500.0,
                    "medianRecovery_ms": 1400.0,
                    "minRecovery_ms": 1000.0,
                    "maxRecovery_ms": 2500.0,
                    "p95Recovery_ms": 2200.0,
                },
            },
        },
        "placement": {
            "strategy": "colocate",
            "assignments": {"checkoutservice": "worker1", "cartservice": "worker1"},
        },
    }


class TestSQLiteStore:

    def test_save_and_get_run(self, store, sample_run_data):
        run_id = store.save_run(sample_run_data)
        assert run_id == "run-2026-03-20-120000-abc123"

        retrieved = store.get_run(run_id)
        assert retrieved is not None
        assert retrieved["runId"] == run_id
        assert retrieved["summary"]["resilienceScore"] == 95.0

    def test_get_nonexistent_run(self, store):
        result = store.get_run("nonexistent")
        assert result is None

    def test_list_runs(self, store, sample_run_data):
        store.save_run(sample_run_data)

        # Add a second run
        run2 = sample_run_data.copy()
        run2["runId"] = "run-2026-03-20-130000-def456"
        run2["timestamp"] = "2026-03-20T13:00:00+00:00"
        run2["placement"] = {"strategy": "spread", "assignments": {}}
        store.save_run(run2)

        runs = store.list_runs()
        assert len(runs) == 2
        # Most recent first
        assert runs[0]["id"] == "run-2026-03-20-130000-def456"

    def test_list_runs_filter_strategy(self, store, sample_run_data):
        store.save_run(sample_run_data)

        run2 = sample_run_data.copy()
        run2["runId"] = "run-2"
        run2["placement"] = {"strategy": "spread"}
        store.save_run(run2)

        runs = store.list_runs(strategy="colocate")
        assert len(runs) == 1
        assert runs[0]["strategy"] == "colocate"

    def test_list_runs_filter_scenario(self, store, sample_run_data):
        store.save_run(sample_run_data)

        runs = store.list_runs(scenario="online-boutique")
        assert len(runs) == 1

        runs = store.list_runs(scenario="nonexistent")
        assert len(runs) == 0

    def test_get_metrics(self, store, sample_run_data):
        store.save_run(sample_run_data)
        run_id = sample_run_data["runId"]

        metrics = store.get_metrics(run_id)
        assert len(metrics) > 0

        # Check specific metric
        mean_recovery = [m for m in metrics if m["metric_name"] == "meanRecovery_ms"]
        assert len(mean_recovery) == 1
        assert mean_recovery[0]["metric_value"] == 1500.0

    def test_get_metrics_by_name(self, store, sample_run_data):
        store.save_run(sample_run_data)
        run_id = sample_run_data["runId"]

        metrics = store.get_metrics(run_id, metric_name="resilienceScore")
        assert len(metrics) == 1
        assert metrics[0]["metric_value"] == 95.0

    def test_compare_strategies(self, store, sample_run_data):
        store.save_run(sample_run_data)

        run2 = sample_run_data.copy()
        run2["runId"] = "run-2"
        run2["placement"] = {"strategy": "spread"}
        run2["summary"]["resilienceScore"] = 80.0
        store.save_run(run2)

        comparison = store.compare_strategies()
        strategies = comparison["strategies"]
        assert "colocate" in strategies
        assert "spread" in strategies
        assert strategies["colocate"]["avgResilienceScore"] == 95.0
        assert strategies["spread"]["avgResilienceScore"] == 80.0

    def test_export_csv(self, store, sample_run_data, tmp_path):
        store.save_run(sample_run_data)

        csv_path = str(tmp_path / "export.csv")
        result = store.export_csv(csv_path)
        assert os.path.exists(result)

        with open(result) as f:
            lines = f.readlines()
        assert len(lines) == 2  # header + 1 data row
        assert "run-2026-03-20-120000-abc123" in lines[1]

    def test_save_run_with_load_stats(self, store, sample_run_data):
        sample_run_data["loadGeneration"] = {
            "profile": "steady",
            "stats": {
                "totalRequests": 1000,
                "totalFailures": 5,
                "avgResponseTime_ms": 45.0,
                "p50ResponseTime_ms": 40.0,
                "p95ResponseTime_ms": 120.0,
                "p99ResponseTime_ms": 180.0,
                "requestsPerSecond": 33.3,
                "errorRate": 0.005,
                "duration_seconds": 30.0,
            },
        }
        store.save_run(sample_run_data)

        conn = store._get_conn()
        rows = conn.execute(
            "SELECT * FROM load_stats WHERE run_id = ?",
            (sample_run_data["runId"],),
        ).fetchall()
        assert len(rows) == 1
        assert dict(rows[0])["total_requests"] == 1000

    def test_idempotent_save(self, store, sample_run_data):
        """Test that saving the same run twice is idempotent."""
        store.save_run(sample_run_data)
        store.save_run(sample_run_data)  # Should not raise

        runs = store.list_runs()
        assert len(runs) == 1

    def test_schema_creation(self, db_path):
        """Test that schema is created on first connection."""
        store = SQLiteStore(db_path=db_path)
        conn = store._get_conn()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {row["name"] for row in tables}
        assert "runs" in table_names
        assert "metrics" in table_names
        assert "pod_placements" in table_names
        assert "load_stats" in table_names
        store.close()
