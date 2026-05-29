"""Tests for ``chaosprobe summarize`` — pretty-printer for per-strategy
aggregate blocks."""

import json
from pathlib import Path

from click.testing import CliRunner

from chaosprobe.commands.summarize_cmd import _render_strategy, summarize


def _write_summary(tmp_path: Path, strategies: dict) -> Path:
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({"strategies": strategies}))
    return path


class TestRenderStrategy:
    def test_renders_resilience_section_when_present(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "meanResilienceScore": 75.5,
                "stddevResilienceScore": 10.0,
                "p25ResilienceScore": 65.0,
                "harmonicMeanResilienceScore": 70.0,
                "meanResilienceScore_ci95": {"low": 65.0, "high": 85.0, "n": 5},
            },
        }
        lines = _render_strategy("colocate", sdata)
        text = "\n".join(lines)
        assert "## colocate" in text
        assert "resilience: mean=75.5" in text
        assert "stddev=10.0" in text
        assert "95% CI: [65.0, 85.0]" in text

    def test_renders_recovery_section_with_cv(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "meanRecoveryTime_ms": 1200.5,
                "stddevRecoveryTime_ms": 200.0,
                "medianRecoveryTime_ms": 1100.0,
                "maxRecoveryTime_ms": 1800.0,
                "p95RecoveryTime_ms": 1700.0,
                "recoveryTimeCV": 0.167,
                "meanRecoveryTime_ms_ci95": {"low": 1000.0, "high": 1400.0, "n": 5},
            },
        }
        text = "\n".join(_render_strategy("c", sdata))
        assert "recovery: mean=1200.5ms" in text
        assert "CV: 0.167" in text
        assert "max=1800.0ms" in text

    def test_renders_split_section(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "meanDeletionToScheduled_ms": 300.0,
                "deletionToScheduledCV": 0.10,
                "meanDeletionToScheduled_ms_ci95": {"low": 250.0, "high": 350.0, "n": 5},
                "meanScheduledToReady_ms": 900.0,
                "scheduledToReadyCV": 0.20,
                "meanScheduledToReady_ms_ci95": {"low": 800.0, "high": 1000.0, "n": 5},
            },
        }
        text = "\n".join(_render_strategy("c", sdata))
        assert "d2s" in text
        assert "s2r" in text
        assert "mean=300.0ms" in text
        assert "mean=900.0ms" in text

    def test_renders_recovery_histogram_with_bars(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "recoveryTimeHistogram_ms": {
                    "lt_500ms": 2,
                    "500_to_1000ms": 1,
                    "1000_to_2000ms": 0,
                    "2000_to_5000ms": 0,
                    "5000_to_10000ms": 1,
                    "gte_10000ms": 0,
                }
            },
        }
        text = "\n".join(_render_strategy("c", sdata))
        assert "recovery histogram" in text
        # ASCII bar marker present.
        assert "█" in text
        # Empty buckets still show with 0 count and no bar.
        assert "1000_to_2000ms" in text

    def test_renders_scheduler_events(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "schedulerEventCounts": {
                    "Scheduled": {
                        "total": 10,
                        "meanPerIteration": 2.0,
                        "maxPerIteration": 3,
                        "iterationsObserved": 5,
                    },
                    "FailedScheduling": {
                        "total": 1,
                        "meanPerIteration": 0.5,
                        "maxPerIteration": 1,
                        "iterationsObserved": 2,
                    },
                }
            },
        }
        text = "\n".join(_render_strategy("c", sdata))
        assert "scheduler events" in text
        assert "Scheduled" in text
        assert "total=10" in text
        assert "FailedScheduling" in text

    def test_renders_oomkills_and_restarts(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "totalOOMKills": 3,
                "meanOOMKillsPerIteration": 1.0,
                "iterationsWithOOMKills": 2,
                "totalRestarts": 7,
                "meanRestartsPerIteration": 1.75,
                "iterationsWithRestarts": 3,
            },
        }
        text = "\n".join(_render_strategy("c", sdata))
        assert "OOMKills: total=3" in text
        assert "restarts: total=7" in text

    def test_renders_node_pressure(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "nodePressureEvents": {
                    "MemoryPressure": {"iterationsWithEvent": 3, "totalNodeEvents": 5},
                    "DiskPressure": {"iterationsWithEvent": 0, "totalNodeEvents": 0},
                }
            },
        }
        text = "\n".join(_render_strategy("c", sdata))
        assert "node pressure" in text
        # Only fired conditions listed.
        assert "MemoryPressure" in text
        assert "DiskPressure" not in text

    def test_renders_tainted_with_reasons(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "taintedIterations": 2,
                "taintReasonCounts": {"pre_chaos_errors_high": 2},
            },
        }
        text = "\n".join(_render_strategy("c", sdata))
        assert "tainted: 2/5" in text
        assert "pre_chaos_errors_high=2" in text

    def test_empty_aggregate_does_not_crash(self):
        sdata = {"iterations": [], "aggregated": {}}
        lines = _render_strategy("c", sdata)
        # Still shows the header + iteration count.
        assert any("## c" in line for line in lines)


class TestSummarizeCommand:
    def test_summarize_all_strategies(self, tmp_path):
        path = _write_summary(
            tmp_path,
            {
                "colocate": {
                    "iterations": [{}] * 5,
                    "aggregated": {"meanResilienceScore": 75.0, "meanRecoveryTime_ms": 1200},
                },
                "spread": {
                    "iterations": [{}] * 5,
                    "aggregated": {"meanResilienceScore": 90.0, "meanRecoveryTime_ms": 600},
                },
            },
        )
        runner = CliRunner()
        result = runner.invoke(summarize, ["-s", str(path)])
        assert result.exit_code == 0
        assert "## colocate" in result.output
        assert "## spread" in result.output

    def test_summarize_filter_by_strategy(self, tmp_path):
        path = _write_summary(
            tmp_path,
            {
                "colocate": {
                    "iterations": [{}] * 5,
                    "aggregated": {"meanResilienceScore": 75.0},
                },
                "spread": {
                    "iterations": [{}] * 5,
                    "aggregated": {"meanResilienceScore": 90.0},
                },
            },
        )
        runner = CliRunner()
        result = runner.invoke(summarize, ["-s", str(path), "--strategy", "colocate"])
        assert result.exit_code == 0
        assert "## colocate" in result.output
        assert "## spread" not in result.output

    def test_summarize_unknown_strategy_errors(self, tmp_path):
        path = _write_summary(
            tmp_path,
            {"colocate": {"iterations": [{}], "aggregated": {}}},
        )
        runner = CliRunner()
        result = runner.invoke(summarize, ["-s", str(path), "--strategy", "bogus"])
        assert result.exit_code == 1
        assert "not in summary" in result.output

    def test_summarize_empty_summary_errors(self, tmp_path):
        path = tmp_path / "summary.json"
        path.write_text(json.dumps({"strategies": {}}))
        runner = CliRunner()
        result = runner.invoke(summarize, ["-s", str(path)])
        assert result.exit_code == 1

    def test_summarize_writes_to_output_file(self, tmp_path):
        path = _write_summary(
            tmp_path,
            {"colocate": {"iterations": [{}], "aggregated": {"meanResilienceScore": 75.0}}},
        )
        out_path = tmp_path / "out.txt"
        runner = CliRunner()
        result = runner.invoke(summarize, ["-s", str(path), "-o", str(out_path)])
        assert result.exit_code == 0
        assert f"Wrote {out_path}" in result.output
        assert "## colocate" in out_path.read_text()
