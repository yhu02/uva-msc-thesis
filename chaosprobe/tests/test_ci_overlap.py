"""Tests for the CI-overlap helpers added to ``chaosprobe compare``.

The before/after compare previously surfaced only point-estimate changes.
A reviewer's first question is "is the gap inside the noise?" — answered
by checking whether the two confidence intervals overlap.
"""

from chaosprobe.output.comparison import (
    _compare_strategies_ci_overlap,
    _interval_overlap,
    compare_runs,
)


def _run_with_strategies(strategies):
    """Minimal run dict carrying just the strategies section under test."""
    return {
        "summary": {"resilienceScore": 80, "overallVerdict": "PASS"},
        "strategies": strategies,
        "experiments": [],
        "metrics": {},
    }


def _strategy(low, high):
    return {
        "aggregated": {
            "meanResilienceScore_ci95": {"low": low, "high": high, "n": 5, "n_resamples": 2000}
        }
    }


class TestIntervalOverlap:
    def test_disjoint_a_below_b(self):
        out = _interval_overlap(10, 20, 30, 40)
        assert out["overlaps"] is False
        assert out["gap"] == 10.0
        assert out["overlapAmount"] == 0.0

    def test_disjoint_b_below_a(self):
        out = _interval_overlap(30, 40, 10, 20)
        assert out["overlaps"] is False
        assert out["gap"] == 10.0

    def test_overlap_partial(self):
        out = _interval_overlap(10, 25, 20, 35)
        assert out["overlaps"] is True
        assert out["overlapAmount"] == 5.0
        assert out["gap"] == 0.0

    def test_overlap_nested(self):
        out = _interval_overlap(10, 50, 20, 30)
        assert out["overlaps"] is True
        assert out["overlapAmount"] == 10.0

    def test_touch_treated_as_overlap(self):
        """Touching intervals are conservatively called overlapping."""
        out = _interval_overlap(10, 20, 20, 30)
        assert out["overlaps"] is True
        assert out["overlapAmount"] == 0.0


class TestCompareStrategiesCIOverlap:
    def test_significant_when_intervals_disjoint(self):
        baseline = {"colocate": _strategy(40, 50)}
        after_fix = {"colocate": _strategy(70, 80)}
        out = _compare_strategies_ci_overlap(baseline, after_fix)
        assert out["colocate"]["interpretation"] == "significant"
        assert out["colocate"]["intervalsOverlap"] is False
        assert out["colocate"]["gap"] == 20.0

    def test_indistinguishable_when_intervals_heavily_overlap(self):
        baseline = {"x": _strategy(40, 60)}
        after_fix = {"x": _strategy(45, 55)}
        out = _compare_strategies_ci_overlap(baseline, after_fix)
        assert out["x"]["interpretation"] == "indistinguishable"
        assert out["x"]["intervalsOverlap"] is True

    def test_directional_when_intervals_overlap_slightly(self):
        # overlap of 1 over smaller width of 10 = 10% → directional, not
        # indistinguishable (threshold is 50%).
        baseline = {"x": _strategy(40, 50)}
        after_fix = {"x": _strategy(49, 60)}
        out = _compare_strategies_ci_overlap(baseline, after_fix)
        assert out["x"]["interpretation"] == "directional"

    def test_strategy_only_on_one_side_omitted(self):
        baseline = {"colocate": _strategy(40, 50), "spread": _strategy(60, 70)}
        after_fix = {"colocate": _strategy(70, 80)}  # no spread
        out = _compare_strategies_ci_overlap(baseline, after_fix)
        assert "spread" not in out

    def test_strategy_without_ci_omitted(self):
        baseline = {
            "colocate": {"aggregated": {"meanResilienceScore": 50}},  # no _ci95
        }
        after_fix = {"colocate": _strategy(70, 80)}
        out = _compare_strategies_ci_overlap(baseline, after_fix)
        assert out == {}

    def test_empty_inputs(self):
        assert _compare_strategies_ci_overlap({}, {}) == {}

    def test_full_compare_runs_attaches_block_when_present(self):
        out = compare_runs(
            _run_with_strategies({"colocate": _strategy(40, 50)}),
            _run_with_strategies({"colocate": _strategy(70, 80)}),
        )
        assert "strategiesCIOverlap" in out["comparison"]
        assert out["comparison"]["strategiesCIOverlap"]["colocate"]["interpretation"] == (
            "significant"
        )

    def test_full_compare_runs_omits_block_when_no_strategies(self):
        out = compare_runs(
            _run_with_strategies({}),
            _run_with_strategies({}),
        )
        assert "strategiesCIOverlap" not in out["comparison"]
