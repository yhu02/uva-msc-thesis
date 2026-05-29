"""Tests for the ``chaosprobe stats`` CLI command."""

import json
from pathlib import Path

from click.testing import CliRunner

from chaosprobe.commands.stats_cmd import _load_strategies, stats


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


class TestLoadStrategies:
    def test_extracts_per_strategy_scores(self, tmp_path):
        path = _make_summary(tmp_path, {"colocate": [70, 75, 80], "spread": [60, 65]})
        out = _load_strategies(path)
        assert out == {"colocate": [70.0, 75.0, 80.0], "spread": [60.0, 65.0]}

    def test_skips_strategies_without_iterations(self, tmp_path):
        path = _make_summary(tmp_path, {"colocate": []})
        out = _load_strategies(path)
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
        out = _load_strategies(path)
        assert out == {"colocate": [70.0]}

    def test_handles_missing_strategies_key(self, tmp_path):
        path = tmp_path / "summary.json"
        path.write_text(json.dumps({}))
        out = _load_strategies(path)
        assert out == {}


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
        assert set(payload["ci"].keys()) == {"colocate", "spread"}
        assert len(payload["pairwise"]) == 1

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
        assert "Bootstrap 90% CI" in result.output
