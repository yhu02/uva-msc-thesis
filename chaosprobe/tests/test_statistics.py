"""Tests for the bootstrap CI / Mann-Whitney statistical helpers."""

import math

import pytest

from chaosprobe.metrics.statistics import (
    bootstrap_ci,
    mann_whitney_u,
    pairwise_comparisons,
)


class TestBootstrapCI:
    def test_empty_sample_returns_degenerate(self):
        out = bootstrap_ci([], statistic="mean")
        assert out["n"] == 0
        assert out["point"] is None
        assert out["ci_low"] is None
        assert out["ci_high"] is None
        assert out["n_resamples"] == 0

    def test_single_value_collapses(self):
        out = bootstrap_ci([5.0], statistic="mean")
        assert out["n"] == 1
        assert out["point"] == 5.0
        assert out["ci_low"] == 5.0
        assert out["ci_high"] == 5.0
        assert out["n_resamples"] == 0

    def test_mean_point_estimate(self):
        out = bootstrap_ci([1.0, 2.0, 3.0, 4.0, 5.0], statistic="mean", n_resamples=500)
        assert out["point"] == 3.0
        assert out["ci_low"] <= 3.0 <= out["ci_high"]
        assert out["n"] == 5

    def test_median_statistic(self):
        out = bootstrap_ci([1, 2, 3, 4, 5], statistic="median", n_resamples=500)
        assert out["point"] == 3.0

    def test_min_statistic(self):
        out = bootstrap_ci([4, 1, 9, 2], statistic="min", n_resamples=200)
        assert out["point"] == 1.0

    def test_p25_statistic(self):
        out = bootstrap_ci([1, 2, 3, 4, 5], statistic="p25", n_resamples=200)
        # 25th percentile of [1..5] (linear interp) = 2.0
        assert out["point"] == 2.0

    def test_unknown_statistic_raises(self):
        with pytest.raises(ValueError):
            bootstrap_ci([1.0, 2.0], statistic="weird", n_resamples=10)

    def test_reproducible_with_seed(self):
        out_a = bootstrap_ci([1.0, 2.0, 3.0], statistic="mean", seed=99, n_resamples=200)
        out_b = bootstrap_ci([1.0, 2.0, 3.0], statistic="mean", seed=99, n_resamples=200)
        assert out_a == out_b


class TestMannWhitneyU:
    def test_empty_samples_degenerate(self):
        out = mann_whitney_u([], [1.0])
        assert out["p_two_sided"] == 1.0
        assert out["u_statistic"] is None
        out2 = mann_whitney_u([1.0], [])
        assert out2["p_two_sided"] == 1.0

    def test_clearly_separated_samples_significant(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [10.0, 11.0, 12.0, 13.0, 14.0]
        out = mann_whitney_u(a, b)
        assert out["p_two_sided"] < 0.05
        assert out["n_a"] == 5
        assert out["n_b"] == 5

    def test_identical_samples_p_one(self):
        a = [1.0, 2.0, 3.0]
        out = mann_whitney_u(a, list(a))
        assert out["p_two_sided"] >= 0.5

    def test_single_constant_sample_zero_var(self):
        # When everything ties, var_u <= 0 and the helper returns p=1.0.
        out = mann_whitney_u([5.0, 5.0, 5.0], [5.0, 5.0, 5.0])
        assert out["p_two_sided"] == 1.0
        assert out["z"] == 0.0

    def test_handles_ties_with_correction(self):
        a = [1, 2, 3, 4, 5]
        b = [3, 4, 5, 6, 7]
        out = mann_whitney_u(a, b)
        assert 0.0 < out["p_two_sided"] <= 1.0
        assert math.isfinite(out["z"])

    def test_continuity_correction_is_conservative(self):
        # n=3 fully-separated samples: the exact two-sided p is 0.10 and the
        # continuity-corrected normal approximation gives ~0.081 — NOT
        # significant at alpha=0.05.  Regression guard against the old bug
        # that applied the correction in the wrong direction and returned
        # ~0.029, overstating significance.
        out = mann_whitney_u([1, 2, 3], [4, 5, 6])
        assert out["p_two_sided"] == pytest.approx(0.081, abs=0.005)
        assert out["p_two_sided"] > 0.05
        # z is the corrected deviation magnitude (non-negative).
        assert out["z"] == pytest.approx(1.746, abs=0.005)

    def test_correction_clamps_at_zero_for_near_equal(self):
        # When |U - mean_u| <= 0.5 the corrected deviation clamps at 0, so z is
        # 0 and the two-sided p is 1.0 — no evidence of a difference.  (var_u is
        # still > 0 here, so this exercises the corrected branch, not the
        # degenerate zero-variance branch.)
        out = mann_whitney_u([1, 2, 3, 100], [1, 2, 3, 100])
        assert out["z"] == 0.0
        assert out["p_two_sided"] == 1.0


class TestPairwiseComparisons:
    def test_two_label_pair(self):
        # n=5 fully-separated samples — genuinely significant at alpha=0.05
        # (corrected two-sided p ~0.012).  n=3 separation is NOT significant
        # (exact p=0.10), so a smaller fixture would not exercise this path.
        rows = pairwise_comparisons(
            {"a": [1, 2, 3, 4, 5], "b": [10, 11, 12, 13, 14]},
            holm_bonferroni=False,
        )
        assert len(rows) == 1
        row = rows[0]
        assert {row["a"], row["b"]} == {"a", "b"}
        assert row["p_raw"] < 0.05
        assert row["significant_05"] is True

    def test_holm_correction_monotonic(self):
        samples = {
            "x": [1, 2, 3, 4, 5],
            "y": [3, 4, 5, 6, 7],
            "z": [10, 11, 12, 13, 14],
            "w": [10, 12, 14, 16, 18],
        }
        rows = pairwise_comparisons(samples, holm_bonferroni=True)
        assert len(rows) == 6  # C(4,2) = 6
        # Check Holm-adjusted p-values are non-decreasing in raw-p order.
        # (After Holm correction, sort order may differ; re-sort by raw to verify.)
        by_raw = sorted(rows, key=lambda r: r["p_raw"])
        prev = -1.0
        for row in by_raw:
            assert row["p_holm"] >= prev - 1e-9
            prev = row["p_holm"]

    def test_holm_caps_at_one(self):
        # Two indistinguishable samples → p_holm should be capped at 1.0.
        samples = {"a": [5, 5, 5], "b": [5, 5, 5]}
        rows = pairwise_comparisons(samples, holm_bonferroni=True)
        assert rows[0]["p_holm"] <= 1.0

    def test_default_significant_05_threshold(self):
        # n=5 separated samples so the significant path is exercised with a
        # statistically valid sample size (corrected two-sided p ~0.012).
        rows = pairwise_comparisons(
            {"hi": [100, 101, 102, 103, 104], "lo": [1, 2, 3, 4, 5]},
            holm_bonferroni=False,
        )
        assert rows[0]["significant_05"] is True
