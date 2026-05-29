"""Tests for the ``chaosprobe inspect`` single-iteration CLI command."""

import json
from pathlib import Path

from click.testing import CliRunner

from chaosprobe.commands.inspect_cmd import (
    _collect_worst,
    _find_iteration,
    _format_iteration,
    _format_worst,
    _probe_verdict_summary,
    inspect,
)


def _iter_record(iteration, score=80, verdict="ALL_PASS", **extra):
    base = {
        "iteration": iteration,
        "verdict": verdict,
        "resilienceScore": score,
        "probeVerdicts": {"liveness": "Pass", "readiness": "Pass"},
        "unknownProbeCount": 0,
        "preChaosHealthy": True,
        "preChaosTaintReasons": [],
        "experimentDuration_s": 42.5,
        "runId": "abc123",
        "metrics": {"recovery": {"recoveryTime_ms": 1500}},
    }
    base.update(extra)
    return base


def _summary(strategies):
    return {"strategies": strategies}


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "summary.json"
    p.write_text(json.dumps(payload))
    return p


class TestFindIteration:
    def test_returns_record_for_matching_iteration(self):
        raw = _summary({"spread": {"iterations": [_iter_record(1), _iter_record(2)]}})
        ir = _find_iteration(raw, "spread", 2)
        assert ir is not None and ir["iteration"] == 2

    def test_returns_none_for_missing_strategy(self):
        raw = _summary({"spread": {"iterations": [_iter_record(1)]}})
        assert _find_iteration(raw, "colocate", 1) is None

    def test_returns_none_for_missing_iteration(self):
        raw = _summary({"spread": {"iterations": [_iter_record(1)]}})
        assert _find_iteration(raw, "spread", 5) is None


class TestProbeVerdictSummary:
    def test_counts_each_verdict(self):
        ir = {"probeVerdicts": {"a": "Pass", "b": "Pass", "c": "Fail"}}
        assert _probe_verdict_summary(ir) == "Fail=1 Pass=2"

    def test_empty_returns_dash(self):
        assert _probe_verdict_summary({}) == "—"


class TestFormatIteration:
    def test_headline_view_includes_score_and_verdict(self):
        out = _format_iteration("spread", _iter_record(3, score=72, verdict="DEGRADED"))
        assert "strategy: spread" in out
        assert "iteration: 3" in out
        assert "verdict: DEGRADED" in out
        assert "score: 72" in out

    def test_recovery_split_emitted_when_metrics_present(self):
        ir = _iter_record(
            1,
            metrics={
                "recovery": {
                    "recoveryTime_ms": 1200,
                    "deletionToScheduled_ms": 500,
                    "scheduledToReady_ms": 700,
                }
            },
        )
        out = _format_iteration("spread", ir)
        assert "recovery: 1200 ms" in out
        assert "d2s=500 ms" in out
        assert "s2r=700 ms" in out

    def test_detail_sections_listed_when_present(self):
        ir = _iter_record(1, anomalyLabels=["spike"], podPlacements=[{"pod": "x"}])
        out = _format_iteration("spread", ir)
        assert "detail sections present:" in out
        assert "anomalyLabels" in out
        assert "podPlacements" in out

    def test_empty_detail_sections_not_listed(self):
        ir = _iter_record(1, anomalyLabels=None, podPlacements=[], metrics={})
        out = _format_iteration("spread", ir)
        assert "detail sections present" not in out


class TestInspectCommand:
    def test_default_emits_headline_view(self, tmp_path):
        summary = _write(
            tmp_path,
            _summary({"spread": {"iterations": [_iter_record(1), _iter_record(2)]}}),
        )
        result = CliRunner().invoke(
            inspect,
            ["-s", str(summary), "--strategy", "spread", "-i", "2"],
        )
        assert result.exit_code == 0, result.output
        assert "strategy: spread" in result.output
        assert "iteration: 2" in result.output

    def test_json_emits_raw_record(self, tmp_path):
        summary = _write(
            tmp_path,
            _summary({"spread": {"iterations": [_iter_record(1)]}}),
        )
        result = CliRunner().invoke(
            inspect,
            ["-s", str(summary), "--strategy", "spread", "-i", "1", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["iteration"] == 1
        assert payload["resilienceScore"] == 80

    def test_missing_iteration_exits_one(self, tmp_path):
        summary = _write(
            tmp_path,
            _summary({"spread": {"iterations": [_iter_record(1)]}}),
        )
        result = CliRunner().invoke(
            inspect,
            ["-s", str(summary), "--strategy", "spread", "-i", "99"],
        )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_missing_strategy_exits_one(self, tmp_path):
        summary = _write(
            tmp_path,
            _summary({"spread": {"iterations": [_iter_record(1)]}}),
        )
        result = CliRunner().invoke(
            inspect,
            ["-s", str(summary), "--strategy", "colocate", "-i", "1"],
        )
        assert result.exit_code != 0
        assert "not found" in result.output


class TestCollectWorst:
    def test_sorts_ascending_and_caps_at_limit(self):
        raw = {
            "strategies": {
                "spread": {
                    "iterations": [
                        _iter_record(1, score=88),
                        _iter_record(2, score=92),
                    ]
                },
                "colocate": {
                    "iterations": [
                        _iter_record(1, score=55),
                        _iter_record(2, score=42),
                    ]
                },
            }
        }
        rows = _collect_worst(raw, limit=3)
        assert [r["score"] for r in rows] == [42, 55, 88]
        assert rows[0]["strategy"] == "colocate"
        assert rows[0]["iteration"] == 2

    def test_skips_iterations_without_numeric_score(self):
        raw = {
            "strategies": {
                "spread": {
                    "iterations": [
                        _iter_record(1, score=88),
                        {"iteration": 2, "resilienceScore": None},
                        {"iteration": 3},
                    ]
                }
            }
        }
        rows = _collect_worst(raw, limit=5)
        assert len(rows) == 1
        assert rows[0]["iteration"] == 1

    def test_breaks_ties_by_strategy_then_iteration(self):
        raw = {
            "strategies": {
                "b": {"iterations": [_iter_record(2, score=70)]},
                "a": {"iterations": [_iter_record(1, score=70)]},
            }
        }
        rows = _collect_worst(raw, limit=2)
        # Same score, "a" sorts before "b".
        assert rows[0]["strategy"] == "a"
        assert rows[1]["strategy"] == "b"


class TestFormatWorst:
    def test_emits_header_and_rows(self):
        out = _format_worst(
            [
                {"strategy": "colocate", "iteration": 3, "score": 42, "verdict": "FAIL"},
                {"strategy": "spread", "iteration": 1, "score": 88, "verdict": "ALL_PASS"},
            ]
        )
        assert "strategy" in out and "iter" in out and "score" in out
        assert "colocate" in out and "42" in out and "FAIL" in out
        assert "spread" in out and "88" in out

    def test_empty_message(self):
        assert _format_worst([]) == "no iterations with a numeric resilienceScore"


class TestInspectWorst:
    def test_worst_flag_lists_lowest_n(self, tmp_path):
        summary = _write(
            tmp_path,
            _summary(
                {
                    "spread": {
                        "iterations": [
                            _iter_record(1, score=88),
                            _iter_record(2, score=92),
                        ]
                    },
                    "colocate": {
                        "iterations": [
                            _iter_record(1, score=55),
                            _iter_record(2, score=42),
                        ]
                    },
                }
            ),
        )
        result = CliRunner().invoke(inspect, ["-s", str(summary), "--worst", "2"])
        assert result.exit_code == 0, result.output
        # Worst-2 should include the 42 and the 55, in that order.
        idx_42 = result.output.find("42")
        idx_55 = result.output.find("55")
        assert idx_42 != -1 and idx_55 != -1 and idx_42 < idx_55

    def test_worst_with_json(self, tmp_path):
        summary = _write(
            tmp_path,
            _summary({"spread": {"iterations": [_iter_record(1, score=88)]}}),
        )
        result = CliRunner().invoke(inspect, ["-s", str(summary), "--worst", "3", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, list) and payload[0]["strategy"] == "spread"

    def test_worst_with_strategy_is_an_error(self, tmp_path):
        summary = _write(
            tmp_path,
            _summary({"spread": {"iterations": [_iter_record(1)]}}),
        )
        result = CliRunner().invoke(
            inspect,
            ["-s", str(summary), "--worst", "1", "--strategy", "spread"],
        )
        assert result.exit_code != 0
        assert "exclusive" in result.output

    def test_worst_must_be_positive(self, tmp_path):
        summary = _write(
            tmp_path,
            _summary({"spread": {"iterations": [_iter_record(1)]}}),
        )
        result = CliRunner().invoke(inspect, ["-s", str(summary), "--worst", "0"])
        assert result.exit_code != 0
        assert "positive" in result.output

    def test_missing_required_flags_errors(self, tmp_path):
        summary = _write(
            tmp_path,
            _summary({"spread": {"iterations": [_iter_record(1)]}}),
        )
        # No --worst, --strategy, or --iteration → friendly error
        result = CliRunner().invoke(inspect, ["-s", str(summary)])
        assert result.exit_code != 0
        assert "--worst" in result.output or "required" in result.output
