"""Tests for the ``chaosprobe diff`` two-summary comparison command."""

import json
from pathlib import Path

from click.testing import CliRunner

from chaosprobe.commands.diff_cmd import (
    _build_report,
    _cis_overlap,
    _compare_strategy,
    _format_text,
    _has_disjoint_ci,
    diff,
)


def _strategy(mean_res, ci_res, mean_rec=None, ci_rec=None):
    agg = {
        "meanResilienceScore": mean_res,
        "meanResilienceScore_ci95": ci_res,
    }
    if mean_rec is not None:
        agg["meanRecoveryTime_ms"] = mean_rec
        agg["meanRecoveryTime_ms_ci95"] = ci_rec
    return {"iterations": [{"resilienceScore": mean_res}] * 5, "aggregated": agg}


def _summary(strategies):
    return {"strategies": strategies}


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload))
    return p


class TestCisOverlap:
    def test_overlapping(self):
        assert _cis_overlap({"low": 80, "high": 90}, {"low": 85, "high": 95}) is True

    def test_disjoint(self):
        assert _cis_overlap({"low": 80, "high": 84}, {"low": 85, "high": 90}) is False

    def test_touching_counts_as_overlap(self):
        assert _cis_overlap({"low": 80, "high": 85}, {"low": 85, "high": 90}) is True

    def test_missing_returns_none(self):
        assert _cis_overlap(None, {"low": 1, "high": 2}) is None
        assert _cis_overlap({"low": None, "high": 5}, {"low": 1, "high": 2}) is None


class TestCompareStrategy:
    def test_emits_row_per_available_metric(self):
        a = _strategy(80, {"low": 78, "high": 82}, 1000, {"low": 950, "high": 1050})
        b = _strategy(75, {"low": 73, "high": 77}, 1100, {"low": 1050, "high": 1150})
        rows = _compare_strategy(a, b)
        assert {r["metric"] for r in rows} == {"resilience", "recovery (ms)"}
        res = next(r for r in rows if r["metric"] == "resilience")
        assert res["delta"] == -5
        assert res["pct"] == -6.25
        assert res["ci_overlap"] is False

    def test_skips_metric_when_missing_on_either_side(self):
        a = _strategy(80, {"low": 78, "high": 82})
        b = _strategy(75, {"low": 73, "high": 77}, 1100, {"low": 1050, "high": 1150})
        rows = _compare_strategy(a, b)
        # recovery missing on A → no recovery row
        assert {r["metric"] for r in rows} == {"resilience"}

    def test_zero_baseline_yields_none_pct(self):
        a = _strategy(0, {"low": -1, "high": 1})
        b = _strategy(5, {"low": 4, "high": 6})
        rows = _compare_strategy(a, b)
        assert rows[0]["pct"] is None


class TestBuildReport:
    def test_partitions_into_only_a_only_b_common(self):
        a = _summary(
            {
                "spread": _strategy(85, {"low": 83, "high": 87}),
                "colocate": _strategy(60, {"low": 58, "high": 62}),
            }
        )
        b = _summary(
            {
                "spread": _strategy(86, {"low": 84, "high": 88}),
                "random": _strategy(70, {"low": 68, "high": 72}),
            }
        )
        report = _build_report(a, b)
        assert report["onlyInA"] == ["colocate"]
        assert report["onlyInB"] == ["random"]
        assert set(report["common"]) == {"spread"}

    def test_no_common_strategies(self):
        a = _summary({"x": _strategy(85, {"low": 83, "high": 87})})
        b = _summary({"y": _strategy(70, {"low": 68, "high": 72})})
        report = _build_report(a, b)
        assert report["common"] == {}


class TestFormatText:
    def test_emits_strategy_block_per_common_strategy(self):
        report = _build_report(
            _summary({"spread": _strategy(85, {"low": 83, "high": 87})}),
            _summary({"spread": _strategy(75, {"low": 73, "high": 77})}),
        )
        out = _format_text(report)
        assert "spread:" in out
        assert "resilience:" in out
        assert "85.0 → 75.0" in out
        assert "CHANGED" in out

    def test_only_in_a_surfaced_above_common(self):
        report = _build_report(
            _summary(
                {
                    "spread": _strategy(85, {"low": 83, "high": 87}),
                    "colocate": _strategy(60, {"low": 58, "high": 62}),
                }
            ),
            _summary({"spread": _strategy(86, {"low": 84, "high": 88})}),
        )
        out = _format_text(report)
        assert "only in A: colocate" in out
        assert out.index("only in A") < out.index("spread:")

    def test_no_overlapping_strategies(self):
        report = _build_report(
            _summary({"x": _strategy(85, {"low": 83, "high": 87})}),
            _summary({"y": _strategy(70, {"low": 68, "high": 72})}),
        )
        out = _format_text(report)
        assert "only in A: x" in out
        assert "only in B: y" in out

    def test_no_overlap_and_no_solos(self):
        out = _format_text(_build_report({}, {}))
        assert "no overlapping strategies" in out


class TestHasDisjointCi:
    def test_true_when_any_metric_disjoint(self):
        report = _build_report(
            _summary({"spread": _strategy(85, {"low": 83, "high": 87})}),
            _summary({"spread": _strategy(70, {"low": 68, "high": 72})}),
        )
        assert _has_disjoint_ci(report) is True

    def test_false_when_all_overlap(self):
        report = _build_report(
            _summary({"spread": _strategy(85, {"low": 80, "high": 90})}),
            _summary({"spread": _strategy(86, {"low": 81, "high": 91})}),
        )
        assert _has_disjoint_ci(report) is False


class TestDiffCommand:
    def test_writes_text_report_to_file(self, tmp_path):
        a = _write(tmp_path, "a.json", _summary({"spread": _strategy(85, {"low": 83, "high": 87})}))
        b = _write(tmp_path, "b.json", _summary({"spread": _strategy(86, {"low": 84, "high": 88})}))
        out = tmp_path / "diff.txt"
        result = CliRunner().invoke(diff, ["--a", str(a), "--b", str(b), "-o", str(out)])
        assert result.exit_code == 0, result.output
        text = out.read_text()
        assert "spread:" in text
        assert "stable" in text

    def test_json_output(self, tmp_path):
        a = _write(tmp_path, "a.json", _summary({"spread": _strategy(85, {"low": 83, "high": 87})}))
        b = _write(tmp_path, "b.json", _summary({"spread": _strategy(86, {"low": 84, "high": 88})}))
        result = CliRunner().invoke(diff, ["--a", str(a), "--b", str(b), "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "common" in payload and "spread" in payload["common"]
        assert payload["onlyInA"] == []
        assert payload["onlyInB"] == []

    def test_strict_exits_one_on_disjoint_ci(self, tmp_path):
        a = _write(tmp_path, "a.json", _summary({"spread": _strategy(85, {"low": 83, "high": 87})}))
        b = _write(tmp_path, "b.json", _summary({"spread": _strategy(70, {"low": 68, "high": 72})}))
        result = CliRunner().invoke(diff, ["--a", str(a), "--b", str(b), "--strict"])
        assert result.exit_code == 1, result.output
        # Output is still emitted before the non-zero exit.
        assert "CHANGED" in result.output

    def test_strict_exits_zero_on_stable_runs(self, tmp_path):
        a = _write(tmp_path, "a.json", _summary({"spread": _strategy(85, {"low": 83, "high": 87})}))
        b = _write(tmp_path, "b.json", _summary({"spread": _strategy(86, {"low": 84, "high": 88})}))
        result = CliRunner().invoke(diff, ["--a", str(a), "--b", str(b), "--strict"])
        assert result.exit_code == 0, result.output
