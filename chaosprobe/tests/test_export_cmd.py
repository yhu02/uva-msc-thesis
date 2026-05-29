"""Tests for ``chaosprobe export`` — per-iteration flat CSV."""

import csv
import json
from io import StringIO

from click.testing import CliRunner

from chaosprobe.commands.export_cmd import _iteration_row, _resolve_path, export


class TestResolvePath:
    def test_deep_path(self):
        d = {"a": {"b": {"c": 42}}}
        assert _resolve_path(d, "a.b.c") == 42

    def test_missing_path_returns_none(self):
        d = {"a": {"b": {}}}
        assert _resolve_path(d, "a.b.c") is None

    def test_non_dict_midway_returns_none(self):
        d = {"a": {"b": 5}}
        assert _resolve_path(d, "a.b.c") is None


class TestIterationRow:
    def test_row_pulls_resilience_score(self):
        it = {"resilienceScore": 75}
        row = _iteration_row("colocate", 1, it)
        assert row["strategy"] == "colocate"
        assert row["iteration"] == 1
        assert row["resilience_score"] == 75

    def test_row_handles_nested_recovery(self):
        it = {
            "metrics": {
                "recovery": {
                    "summary": {
                        "meanRecovery_ms": 1200,
                        "meanDeletionToScheduled_ms": 300,
                        "meanScheduledToReady_ms": 900,
                    }
                }
            }
        }
        row = _iteration_row("c", 1, it)
        assert row["mean_recovery_ms"] == 1200
        assert row["mean_deletion_to_scheduled_ms"] == 300
        assert row["mean_scheduled_to_ready_ms"] == 900

    def test_row_handles_pod_status_and_load(self):
        it = {
            "metrics": {"podStatus": {"totalOOMKills": 2, "totalRestarts": 5}},
            "loadGeneration": {
                "stats": {
                    "requestsPerSecond": 10.5,
                    "errorRate": 0.02,
                    "p95ResponseTime_ms": 250,
                }
            },
        }
        row = _iteration_row("c", 1, it)
        assert row["total_oom_kills"] == 2
        assert row["total_restarts"] == 5
        assert row["rps"] == 10.5
        assert row["error_rate"] == 0.02
        assert row["p95_response_time_ms"] == 250

    def test_missing_fields_render_as_empty(self):
        row = _iteration_row("c", 1, {})
        # Every non-strategy/iteration field is "" when source is absent.
        for col in ("resilience_score", "mean_recovery_ms", "rps"):
            assert row[col] == ""


class TestExportCommand:
    def _make_summary(self, tmp_path, strategies):
        path = tmp_path / "summary.json"
        path.write_text(json.dumps({"strategies": strategies}))
        return path

    def test_stdout_output(self, tmp_path):
        path = self._make_summary(
            tmp_path,
            {
                "colocate": {
                    "iterations": [
                        {
                            "resilienceScore": 80,
                            "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1000}}},
                        },
                        {
                            "resilienceScore": 85,
                            "metrics": {"recovery": {"summary": {"meanRecovery_ms": 900}}},
                        },
                    ]
                }
            },
        )
        runner = CliRunner()
        result = runner.invoke(export, ["-s", str(path)])
        assert result.exit_code == 0
        rows = list(csv.DictReader(StringIO(result.output)))
        assert len(rows) == 2
        assert rows[0]["strategy"] == "colocate"
        assert rows[0]["iteration"] == "1"
        assert rows[0]["resilience_score"] == "80"
        assert rows[0]["mean_recovery_ms"] == "1000"

    def test_strategy_filter(self, tmp_path):
        path = self._make_summary(
            tmp_path,
            {
                "colocate": {"iterations": [{"resilienceScore": 80}]},
                "spread": {"iterations": [{"resilienceScore": 95}]},
            },
        )
        runner = CliRunner()
        result = runner.invoke(export, ["-s", str(path), "--strategy", "spread"])
        assert result.exit_code == 0
        rows = list(csv.DictReader(StringIO(result.output)))
        assert len(rows) == 1
        assert rows[0]["strategy"] == "spread"
        assert rows[0]["resilience_score"] == "95"

    def test_unknown_strategy_errors(self, tmp_path):
        path = self._make_summary(tmp_path, {"colocate": {"iterations": [{"resilienceScore": 80}]}})
        runner = CliRunner()
        result = runner.invoke(export, ["-s", str(path), "--strategy", "bogus"])
        assert result.exit_code == 1

    def test_empty_strategies_errors(self, tmp_path):
        path = tmp_path / "summary.json"
        path.write_text(json.dumps({"strategies": {}}))
        runner = CliRunner()
        result = runner.invoke(export, ["-s", str(path)])
        assert result.exit_code == 1

    def test_output_file(self, tmp_path):
        path = self._make_summary(
            tmp_path,
            {"colocate": {"iterations": [{"resilienceScore": 80}, {"resilienceScore": 85}]}},
        )
        out_path = tmp_path / "out.csv"
        runner = CliRunner()
        result = runner.invoke(export, ["-s", str(path), "-o", str(out_path)])
        assert result.exit_code == 0
        assert f"Wrote 2 row(s) to {out_path}" in result.output
        contents = out_path.read_text()
        assert contents.startswith("strategy,iteration,resilience_score")

    def test_non_dict_iterations_skipped(self, tmp_path):
        path = self._make_summary(
            tmp_path,
            {
                "colocate": {
                    "iterations": [
                        {"resilienceScore": 80},
                        "not-a-dict",
                        None,
                        {"resilienceScore": 85},
                    ]
                }
            },
        )
        runner = CliRunner()
        result = runner.invoke(export, ["-s", str(path)])
        assert result.exit_code == 0
        rows = list(csv.DictReader(StringIO(result.output)))
        assert len(rows) == 2

    def test_iteration_indices_sequential(self, tmp_path):
        path = self._make_summary(
            tmp_path,
            {"colocate": {"iterations": [{} for _ in range(5)]}},
        )
        runner = CliRunner()
        result = runner.invoke(export, ["-s", str(path)])
        assert result.exit_code == 0
        rows = list(csv.DictReader(StringIO(result.output)))
        assert [r["iteration"] for r in rows] == ["1", "2", "3", "4", "5"]


class TestJSONLFormat:
    def _make_summary(self, tmp_path, strategies):
        path = tmp_path / "summary.json"
        path.write_text(json.dumps({"strategies": strategies}))
        return path

    def test_jsonl_output_one_object_per_line(self, tmp_path):
        path = self._make_summary(
            tmp_path,
            {
                "colocate": {
                    "iterations": [
                        {
                            "resilienceScore": 80,
                            "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1000}}},
                        },
                        {
                            "resilienceScore": 85,
                            "metrics": {"recovery": {"summary": {"meanRecovery_ms": 900}}},
                        },
                    ]
                }
            },
        )
        runner = CliRunner()
        result = runner.invoke(export, ["-s", str(path), "--format", "jsonl"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 2
        # Each line should be valid JSON.
        rows = [json.loads(line) for line in lines]
        assert rows[0]["strategy"] == "colocate"
        assert rows[0]["resilience_score"] == 80
        assert rows[0]["mean_recovery_ms"] == 1000

    def test_jsonl_preserves_numeric_types(self, tmp_path):
        """CSV stringifies everything; JSONL preserves int/float so
        downstream consumers don't have to re-cast."""
        path = self._make_summary(
            tmp_path,
            {
                "colocate": {
                    "iterations": [
                        {"resilienceScore": 80, "loadGeneration": {"stats": {"errorRate": 0.025}}}
                    ],
                }
            },
        )
        runner = CliRunner()
        result = runner.invoke(export, ["-s", str(path), "--format", "jsonl"])
        row = json.loads(result.output.strip())
        # Numeric type preserved.
        assert isinstance(row["resilience_score"], int)
        assert isinstance(row["error_rate"], float)

    def test_jsonl_missing_fields_render_as_empty_string(self, tmp_path):
        """Same convention as CSV: missing fields become "" so
        consumers can distinguish "no data" from "zero"."""
        path = self._make_summary(tmp_path, {"colocate": {"iterations": [{"resilienceScore": 80}]}})
        runner = CliRunner()
        result = runner.invoke(export, ["-s", str(path), "--format", "jsonl"])
        row = json.loads(result.output.strip())
        assert row["mean_recovery_ms"] == ""
        assert row["resilience_score"] == 80

    def test_default_format_remains_csv(self, tmp_path):
        path = self._make_summary(tmp_path, {"colocate": {"iterations": [{"resilienceScore": 80}]}})
        runner = CliRunner()
        result = runner.invoke(export, ["-s", str(path)])
        assert result.exit_code == 0
        # CSV starts with the header.
        assert result.output.startswith("strategy,iteration,resilience_score")

    def test_invalid_format_rejected(self, tmp_path):
        path = self._make_summary(tmp_path, {"colocate": {"iterations": [{"resilienceScore": 80}]}})
        runner = CliRunner()
        result = runner.invoke(export, ["-s", str(path), "--format", "bogus"])
        assert result.exit_code != 0

    def test_jsonl_to_file(self, tmp_path):
        path = self._make_summary(
            tmp_path,
            {"colocate": {"iterations": [{"resilienceScore": 80}, {"resilienceScore": 85}]}},
        )
        out_path = tmp_path / "out.jsonl"
        runner = CliRunner()
        result = runner.invoke(export, ["-s", str(path), "--format", "jsonl", "-o", str(out_path)])
        assert result.exit_code == 0
        contents = out_path.read_text().strip()
        assert len(contents.split("\n")) == 2
