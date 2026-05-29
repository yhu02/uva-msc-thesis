"""Tests for the ``chaosprobe inspect`` single-iteration CLI command."""

import json
from pathlib import Path

from click.testing import CliRunner

from chaosprobe.commands.inspect_cmd import (
    _find_iteration,
    _format_iteration,
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
