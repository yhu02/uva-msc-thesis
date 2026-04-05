"""Tests for ML-ready export pipeline."""

import pytest
from unittest.mock import patch

from chaosprobe.output.ml_export import (
    export_run_to_rows,
    write_dataset,
)


def _make_run_data(strategy="baseline", fault_type="pod-delete",
                   target="productcatalogservice"):
    """Build a minimal run output dict with enough data to produce rows."""
    return {
        "schemaVersion": "2.0.0",
        "runId": f"run-test-{strategy}",
        "timestamp": "2026-04-02T01:38:51+00:00",
        "scenario": {
            "directory": "/tmp/test",
            "manifests": [],
            "experiments": [
                {
                    "file": "experiment.yaml",
                    "spec": {
                        "spec": {
                            "appinfo": {
                                "appns": "online-boutique",
                                "applabel": f"app={target}",
                            },
                            "experiments": [
                                {
                                    "name": fault_type,
                                    "spec": {
                                        "components": {
                                            "env": [
                                                {"name": "TOTAL_CHAOS_DURATION", "value": "30"},
                                                {"name": "CHAOS_INTERVAL", "value": "10"},
                                                {"name": "PODS_AFFECTED_PERC", "value": "100"},
                                            ]
                                        }
                                    },
                                }
                            ],
                        }
                    },
                }
            ],
        },
        "placement": {"strategy": strategy, "assignments": {}},
        "summary": {
            "resilienceScore": 83.0,
            "overallVerdict": "PASS",
        },
        "metrics": {
            "timeWindow": {
                "start": "2026-04-02T01:35:00+00:00",
                "end": "2026-04-02T01:35:30+00:00",
                "duration_s": 30,
            },
            "latency": {
                "timeSeries": [
                    {
                        "timestamp": "2026-04-02T01:35:05+00:00",
                        "phase": "pre-chaos",
                        "routes": {"/": {"latency_ms": 45, "status": "ok"}},
                    },
                    {
                        "timestamp": "2026-04-02T01:35:15+00:00",
                        "phase": "during-chaos",
                        "routes": {"/": {"latency_ms": 200, "status": "ok"}},
                    },
                    {
                        "timestamp": "2026-04-02T01:35:25+00:00",
                        "phase": "post-chaos",
                        "routes": {"/": {"latency_ms": 50, "status": "ok"}},
                    },
                ],
                "phases": {},
            },
            "recovery": {
                "recoveryEvents": [
                    {
                        "deletionTime": "2026-04-02T01:35:10+00:00",
                        "readyTime": "2026-04-02T01:35:12+00:00",
                        "totalRecovery_ms": 2000,
                    }
                ],
                "summary": {},
            },
            "eventTimeline": [
                {"time": "2026-04-02T01:35:10+00:00", "type": "DELETED",
                 "pod": "test-abc", "phase": "Running"},
                {"time": "2026-04-02T01:35:10+00:00", "type": "ADDED",
                 "pod": "test-def", "phase": "Pending"},
            ],
        },
    }


class TestExportRunToRows:
    def test_produces_rows(self):
        data = _make_run_data()
        rows = export_run_to_rows(data, resolution_s=5.0)
        assert len(rows) > 0

    def test_rows_have_required_columns(self):
        data = _make_run_data()
        rows = export_run_to_rows(data, resolution_s=5.0)
        for row in rows:
            assert "timestamp" in row
            assert "phase" in row
            assert "anomaly_label" in row
            assert "run_id" in row
            assert "strategy" in row

    def test_run_metadata_propagated(self):
        data = _make_run_data(strategy="colocate")
        rows = export_run_to_rows(data, resolution_s=5.0)
        for row in rows:
            assert row["run_id"] == "run-test-colocate"
            assert row["strategy"] == "colocate"
            assert row["resilience_score"] == 83.0
            assert row["overall_verdict"] == "PASS"

    def test_no_metrics_returns_empty(self):
        data = {"runId": "test", "summary": {}, "scenario": {}}
        rows = export_run_to_rows(data)
        assert rows == []

    def test_latency_columns_present(self):
        data = _make_run_data()
        rows = export_run_to_rows(data, resolution_s=5.0)
        all_cols = set()
        for row in rows:
            all_cols.update(row.keys())
        assert "latency:/:ms" in all_cols

    def test_recovery_signal_present(self):
        data = _make_run_data()
        rows = export_run_to_rows(data, resolution_s=5.0)
        rec_rows = [r for r in rows if r.get("recovery_in_progress") == 1]
        assert len(rec_rows) >= 1


class TestWriteDataset:
    def test_csv_output(self, tmp_path):
        data = _make_run_data()
        rows = export_run_to_rows(data, resolution_s=5.0)
        out_path = str(tmp_path / "test.csv")
        result = write_dataset(rows, out_path, format="csv")

        assert result.endswith("test.csv")
        content = open(result).read()
        assert "timestamp" in content
        assert "anomaly_label" in content

    def test_empty_rows(self, tmp_path):
        out_path = str(tmp_path / "empty.csv")
        result = write_dataset([], out_path, format="csv")
        assert result.endswith("empty.csv")

    def test_parquet_import_error(self, tmp_path):
        """Verify helpful error when pyarrow is not installed."""
        rows = [{"a": 1}]
        out_path = str(tmp_path / "test.parquet")
        with patch.dict("sys.modules", {"pyarrow": None, "pyarrow.parquet": None}):
            with pytest.raises(ImportError, match="pyarrow"):
                write_dataset(rows, out_path, format="parquet")

    def test_csv_creates_parent_dirs(self, tmp_path):
        rows = [{"x": 1, "y": 2}]
        out_path = str(tmp_path / "nested" / "dir" / "out.csv")
        result = write_dataset(rows, out_path, format="csv")
        assert "out.csv" in result
