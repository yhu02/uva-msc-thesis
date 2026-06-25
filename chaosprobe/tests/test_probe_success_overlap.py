"""Tests for the per-strategy, per-probe Wilson-CI overlap helper used
by ``chaosprobe compare``.
"""

from chaosprobe.output.comparison import _compare_probe_success_rates, compare_runs


def _run_with_strategies(strategies):
    return {
        "summary": {"resilienceScore": 80, "overallVerdict": "PASS"},
        "strategies": strategies,
        "experiments": [],
        "metrics": {},
    }


def _strategy_with_probes(probe_cis):
    return {
        "aggregated": {
            "probeSuccessRates": {
                name: {"ci_low": low, "ci_high": high, "point": (low + high) / 2}
                for name, (low, high) in probe_cis.items()
            }
        }
    }


class TestCompareProbeSuccessRates:
    def test_significant_when_probe_cis_disjoint(self):
        baseline = {"colocate": _strategy_with_probes({"cart": (0.4, 0.6)})}
        after_fix = {"colocate": _strategy_with_probes({"cart": (0.8, 0.95)})}
        out = _compare_probe_success_rates(baseline, after_fix)
        assert out["colocate"]["cart"]["interpretation"] == "significant"
        assert out["colocate"]["cart"]["intervalsOverlap"] is False

    def test_indistinguishable_when_cis_heavily_overlap(self):
        baseline = {"colocate": _strategy_with_probes({"frontend": (0.5, 0.9)})}
        after_fix = {"colocate": _strategy_with_probes({"frontend": (0.55, 0.85)})}
        out = _compare_probe_success_rates(baseline, after_fix)
        assert out["colocate"]["frontend"]["interpretation"] == "indistinguishable"

    def test_strategy_with_no_probe_block_skipped(self):
        baseline = {
            "colocate": _strategy_with_probes({"cart": (0.4, 0.6)}),
            "spread": {"aggregated": {}},  # No probeSuccessRates
        }
        after_fix = {
            "colocate": _strategy_with_probes({"cart": (0.8, 0.95)}),
            "spread": {"aggregated": {}},
        }
        out = _compare_probe_success_rates(baseline, after_fix)
        assert "spread" not in out
        assert "colocate" in out

    def test_probe_on_only_one_side_skipped(self):
        """Probe present in baseline but not after_fix (or vice versa)
        is skipped from comparison — only joint probes are reported."""
        baseline = {"colocate": _strategy_with_probes({"cart": (0.4, 0.6), "frontend": (0.5, 0.7)})}
        after_fix = {"colocate": _strategy_with_probes({"cart": (0.8, 0.95)})}  # No frontend
        out = _compare_probe_success_rates(baseline, after_fix)
        assert set(out["colocate"].keys()) == {"cart"}

    def test_probe_with_none_ci_bounds_skipped(self):
        baseline = {
            "colocate": {
                "aggregated": {
                    "probeSuccessRates": {"cart": {"ci_low": None, "ci_high": None, "point": None}}
                }
            }
        }
        after_fix = {"colocate": _strategy_with_probes({"cart": (0.8, 0.95)})}
        out = _compare_probe_success_rates(baseline, after_fix)
        assert out == {}

    def test_empty_inputs_return_empty(self):
        assert _compare_probe_success_rates({}, {}) == {}

    def test_compare_runs_attaches_block_when_present(self):
        baseline = {"colocate": _strategy_with_probes({"cart": (0.4, 0.6)})}
        after_fix = {"colocate": _strategy_with_probes({"cart": (0.8, 0.95)})}
        out = compare_runs(
            _run_with_strategies(baseline),
            _run_with_strategies(after_fix),
        )
        assert "probeSuccessRatesOverlap" in out["comparison"]
        cart = out["comparison"]["probeSuccessRatesOverlap"]["colocate"]["cart"]
        assert cart["interpretation"] == "significant"

    def test_compare_runs_omits_block_when_no_probe_rates(self):
        out = compare_runs(
            _run_with_strategies({}),
            _run_with_strategies({}),
        )
        assert "probeSuccessRatesOverlap" not in out["comparison"]

    def test_per_probe_classification_independent(self):
        """Different probes within the same strategy can land in
        different magnitude buckets — the helper must classify each
        independently."""
        baseline = {"colocate": _strategy_with_probes({"cart": (0.4, 0.6), "frontend": (0.5, 0.9)})}
        after_fix = {
            "colocate": _strategy_with_probes({"cart": (0.8, 0.95), "frontend": (0.55, 0.85)})
        }
        out = _compare_probe_success_rates(baseline, after_fix)
        assert out["colocate"]["cart"]["interpretation"] == "significant"
        assert out["colocate"]["frontend"]["interpretation"] == "indistinguishable"
