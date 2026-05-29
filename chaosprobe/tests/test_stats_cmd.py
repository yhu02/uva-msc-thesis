"""Tests for the ``chaosprobe stats`` CLI command."""

import json
from pathlib import Path

from click.testing import CliRunner

from chaosprobe.commands.stats_cmd import _load_strategies, _resolve_path, stats


def _make_summary(tmp_path: Path, strategies: dict) -> Path:
    """Write a minimal ``summary.json`` capturing per-iteration scores."""
    payload = {
        "strategies": {
            name: {"iterations": [{"resilienceScore": s} for s in scores]}
            for name, scores in strategies.items()
        }
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(payload))
    return path


def _make_recovery_summary(tmp_path: Path, strategies: dict) -> Path:
    """Write a summary.json with metrics.recovery.summary.meanRecovery_ms."""
    payload = {
        "strategies": {
            name: {
                "iterations": [
                    {
                        "metrics": {
                            "recovery": {"summary": {"meanRecovery_ms": v}},
                        },
                    }
                    for v in values
                ]
            }
            for name, values in strategies.items()
        }
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(payload))
    return path


class TestResolvePath:
    def test_returns_value_for_deep_path(self):
        d = {"a": {"b": {"c": 42}}}
        assert _resolve_path(d, "a.b.c") == 42

    def test_returns_none_on_missing_key(self):
        d = {"a": {"b": {"c": 42}}}
        assert _resolve_path(d, "a.b.x") is None

    def test_returns_none_on_non_dict_midway(self):
        d = {"a": {"b": 5}}
        assert _resolve_path(d, "a.b.c") is None

    def test_single_part_path(self):
        assert _resolve_path({"x": 1}, "x") == 1


class TestLoadStrategies:
    def test_extracts_per_strategy_scores(self, tmp_path):
        path = _make_summary(tmp_path, {"colocate": [70, 75, 80], "spread": [60, 65]})
        out = _load_strategies(path, "resilienceScore")
        assert out == {"colocate": [70.0, 75.0, 80.0], "spread": [60.0, 65.0]}

    def test_extracts_nested_recovery_metric(self, tmp_path):
        path = _make_recovery_summary(tmp_path, {"colocate": [1200, 1500], "spread": [800, 900]})
        out = _load_strategies(path, "metrics.recovery.summary.meanRecovery_ms")
        assert out == {"colocate": [1200.0, 1500.0], "spread": [800.0, 900.0]}

    def test_skips_strategies_without_iterations(self, tmp_path):
        path = _make_summary(tmp_path, {"colocate": []})
        out = _load_strategies(path, "resilienceScore")
        assert out == {}

    def test_skips_iterations_without_score(self, tmp_path):
        payload = {
            "strategies": {
                "colocate": {
                    "iterations": [
                        {"resilienceScore": 70},
                        {"resilienceScore": None},
                        {},
                    ]
                }
            }
        }
        path = tmp_path / "summary.json"
        path.write_text(json.dumps(payload))
        out = _load_strategies(path, "resilienceScore")
        assert out == {"colocate": [70.0]}

    def test_skips_iterations_where_path_missing(self, tmp_path):
        payload = {
            "strategies": {
                "colocate": {
                    "iterations": [
                        {"metrics": {"recovery": {"summary": {"meanRecovery_ms": 100}}}},
                        {"metrics": {}},
                        {},
                    ]
                }
            }
        }
        path = tmp_path / "summary.json"
        path.write_text(json.dumps(payload))
        out = _load_strategies(path, "metrics.recovery.summary.meanRecovery_ms")
        assert out == {"colocate": [100.0]}

    def test_handles_missing_strategies_key(self, tmp_path):
        path = tmp_path / "summary.json"
        path.write_text(json.dumps({}))
        out = _load_strategies(path, "resilienceScore")
        assert out == {}

    def test_skips_non_numeric_value(self, tmp_path):
        payload = {
            "strategies": {
                "a": {
                    "iterations": [
                        {"resilienceScore": 42},
                        {"resilienceScore": "not-a-number"},
                    ]
                }
            }
        }
        path = tmp_path / "summary.json"
        path.write_text(json.dumps(payload))
        out = _load_strategies(path, "resilienceScore")
        assert out == {"a": [42.0]}


class TestStatsCommand:
    def test_text_output_includes_ci_and_pairwise(self, tmp_path):
        summary = _make_summary(
            tmp_path,
            {
                "colocate": [70, 75, 80, 78, 72],
                "spread": [40, 45, 50, 48, 42],
            },
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary)])

        assert result.exit_code == 0
        assert "Bootstrap 95% CI" in result.output
        assert "resilienceScore" in result.output
        assert "colocate" in result.output
        assert "spread" in result.output
        assert "Pairwise Mann-Whitney" in result.output

    def test_json_output_structure(self, tmp_path):
        summary = _make_summary(
            tmp_path,
            {"colocate": [70, 75, 80], "spread": [40, 45, 50]},
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "ci" in payload
        assert "pairwise" in payload
        assert payload["confidence"] == 0.95
        assert payload["metric"] == "resilienceScore"
        assert set(payload["ci"].keys()) == {"colocate", "spread"}
        assert len(payload["pairwise"]) == 1

    def test_recovery_metric_flag(self, tmp_path):
        summary = _make_recovery_summary(
            tmp_path,
            {
                "colocate": [1200, 1500, 1800, 1700, 1300],
                "spread": [800, 900, 850, 750, 820],
            },
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--metric", "recovery"])
        assert result.exit_code == 0
        assert "meanRecovery_ms" in result.output
        assert "colocate" in result.output

    def test_recovery_metric_json(self, tmp_path):
        summary = _make_recovery_summary(tmp_path, {"a": [100, 200, 300], "b": [400, 500, 600]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--metric", "recovery", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["metric"] == "meanRecovery_ms"

    def test_invalid_metric_choice_rejected(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--metric", "bogus"])
        assert result.exit_code != 0
        assert "bogus" in result.output

    def test_writes_to_output_file(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [4, 5, 6]})
        out_path = tmp_path / "stats.txt"
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "-o", str(out_path)])

        assert result.exit_code == 0
        assert f"Wrote {out_path}" in result.output
        assert "Bootstrap" in out_path.read_text()

    def test_empty_summary_errors_out(self, tmp_path):
        empty = tmp_path / "summary.json"
        empty.write_text(json.dumps({"strategies": {}}))
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(empty)])

        assert result.exit_code == 1
        assert "no strategies" in result.output.lower()

    def test_recovery_empty_summary_errors_with_recovery_label(self, tmp_path):
        # Resilience scores present but no recovery metrics.
        summary = _make_summary(tmp_path, {"a": [1, 2, 3]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--metric", "recovery"])
        assert result.exit_code == 1
        assert "meanRecovery_ms" in result.output

    def test_single_strategy_no_pairwise(self, tmp_path):
        summary = _make_summary(tmp_path, {"colocate": [70, 75, 80]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary)])

        assert result.exit_code == 0
        assert "Bootstrap" in result.output
        assert "(no pairs" in result.output

    def test_nondeterministic_seed_accepted(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [10, 11, 12]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--seed", "-1", "--n-resamples", "50"])
        assert result.exit_code == 0

    def test_custom_confidence_propagates_to_header(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [10, 11, 12]})
        runner = CliRunner()
        result = runner.invoke(
            stats, ["-s", str(summary), "--confidence", "0.9", "--n-resamples", "50"]
        )
        assert result.exit_code == 0


def _make_both_metrics_summary(tmp_path: Path) -> Path:
    """A summary with both resilienceScore and meanRecovery_ms per
    iteration for two strategies."""
    payload = {
        "strategies": {
            "colocate": {
                "iterations": [
                    {
                        "resilienceScore": 70,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1500}}},
                    },
                    {
                        "resilienceScore": 75,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1700}}},
                    },
                    {
                        "resilienceScore": 80,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1300}}},
                    },
                ]
            },
            "spread": {
                "iterations": [
                    {
                        "resilienceScore": 40,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 800}}},
                    },
                    {
                        "resilienceScore": 45,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 850}}},
                    },
                ]
            },
        }
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(payload))
    return path


class TestAllMetricsFlag:
    def test_text_output_contains_both_blocks(self, tmp_path):
        summary = _make_both_metrics_summary(tmp_path)
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--all-metrics"])
        assert result.exit_code == 0
        assert "resilienceScore" in result.output
        assert "meanRecovery_ms" in result.output
        # Each block has its own pairwise header.
        assert result.output.count("Pairwise Mann-Whitney") == 2

    def test_json_output_groups_by_metric_label(self, tmp_path):
        summary = _make_both_metrics_summary(tmp_path)
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--all-metrics", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "metrics" in payload
        assert set(payload["metrics"].keys()) == {"resilienceScore", "meanRecovery_ms"}
        for block in payload["metrics"].values():
            assert "ci" in block
            assert "pairwise" in block

    def test_all_metrics_skips_metric_missing_data(self, tmp_path):
        """If only resilience is present, --all-metrics still succeeds
        and emits only the resilience block."""
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [4, 5, 6]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--all-metrics", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert set(payload["metrics"].keys()) == {"resilienceScore"}

    def test_all_metrics_errors_when_no_data_at_all(self, tmp_path):
        empty = tmp_path / "summary.json"
        empty.write_text(json.dumps({"strategies": {}}))
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(empty), "--all-metrics"])
        assert result.exit_code == 1
        assert "any supported metric" in result.output.lower()

    def test_metric_flag_ignored_when_all_metrics_set(self, tmp_path):
        """--metric defaults to resilience, but --all-metrics should still
        emit both blocks."""
        summary = _make_both_metrics_summary(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            stats, ["-s", str(summary), "--metric", "resilience", "--all-metrics"]
        )
        assert result.exit_code == 0
        assert "meanRecovery_ms" in result.output
        assert "resilienceScore" in result.output


class TestCSVOutput:
    def test_csv_header_and_ci_rows(self, tmp_path):
        import csv
        from io import StringIO

        summary = _make_summary(tmp_path, {"colocate": [70, 75, 80], "spread": [40, 45, 50]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--csv"])
        assert result.exit_code == 0

        rows = list(csv.reader(StringIO(result.output)))
        header = rows[0]
        assert header[:6] == ["section", "metric", "strategy", "a", "b", "n"]

        ci_rows = [r for r in rows[1:] if r[0] == "ci"]
        assert {r[2] for r in ci_rows} == {"colocate", "spread"}
        for r in ci_rows:
            assert r[1] == "resilienceScore"

    def test_csv_pairwise_section(self, tmp_path):
        import csv
        from io import StringIO

        summary = _make_summary(tmp_path, {"colocate": [70, 75, 80], "spread": [40, 45, 50]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--csv"])
        rows = list(csv.reader(StringIO(result.output)))
        pairwise_rows = [r for r in rows[1:] if r[0] == "pairwise"]
        assert len(pairwise_rows) == 1
        assert {pairwise_rows[0][3], pairwise_rows[0][4]} == {"colocate", "spread"}

    def test_csv_all_metrics_carries_metric_column(self, tmp_path):
        import csv
        from io import StringIO

        summary = _make_both_metrics_summary(tmp_path)
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--all-metrics", "--csv"])
        assert result.exit_code == 0
        rows = list(csv.reader(StringIO(result.output)))
        metrics_in_data = {r[1] for r in rows[1:]}
        assert metrics_in_data == {"resilienceScore", "meanRecovery_ms"}

    def test_csv_and_json_mutually_exclusive(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [4, 5, 6]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--csv", "--json"])
        assert result.exit_code == 2
        assert "mutually exclusive" in result.output.lower()

    def test_csv_writes_to_output_file(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [4, 5, 6]})
        out_path = tmp_path / "stats.csv"
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--csv", "-o", str(out_path)])
        assert result.exit_code == 0
        contents = out_path.read_text()
        assert contents.startswith("section,metric,strategy")
        assert "ci" in contents
        assert "pairwise" in contents
