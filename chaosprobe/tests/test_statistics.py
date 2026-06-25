"""Tests for the bootstrap CI / Mann-Whitney statistical helpers."""

import math

import pytest

from chaosprobe.metrics.statistics import (
    _betai,
    _f_sf,
    _icc_point,
    _two_factor_f,
    art_anova,
    bootstrap_ci,
    icc_bootstrap,
    mann_whitney_u,
    page_trend_test,
    pairwise_comparisons,
    sign_test,
    tost_equivalence_correlation,
    wilcoxon_signed_rank,
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


class TestTostEquivalenceCorrelation:
    def test_small_n_degenerate(self):
        out = tost_equivalence_correlation(0.0, n=3)
        assert out["se"] is None
        assert out["p_tost"] is None
        assert out["equivalent"] is False
        assert out["bounds"] == [-0.3, 0.3]

    def test_invalid_sesoi_degenerate(self):
        # n>3 but an out-of-range SESOI exercises the right side of the guard.
        out = tost_equivalence_correlation(0.0, n=50, sesoi=1.5)
        assert out["p_tost"] is None
        assert out["equivalent"] is False

    def test_near_zero_large_n_is_equivalent(self):
        out = tost_equivalence_correlation(0.0, n=100, sesoi=0.3)
        assert out["equivalent"] is True
        assert out["p_tost"] == pytest.approx(0.0011, abs=0.0005)
        assert out["se"] == pytest.approx(0.1015, abs=0.001)

    def test_strong_correlation_not_equivalent(self):
        out = tost_equivalence_correlation(0.8, n=100, sesoi=0.3)
        assert out["equivalent"] is False
        assert out["p_tost"] > 0.05

    def test_rho_at_boundary_clamped(self):
        # rho == 1.0 must not blow up atanh.
        out = tost_equivalence_correlation(1.0, n=50, sesoi=0.3)
        assert math.isfinite(out["z"])
        assert out["equivalent"] is False


class TestSignTest:
    def test_unequal_length_raises(self):
        with pytest.raises(ValueError):
            sign_test([1, 2], [1])

    def test_all_positive(self):
        out = sign_test([5, 6, 7], [1, 2, 3])
        assert out["n_pos"] == 3
        assert out["n_neg"] == 0
        assert out["n"] == 3
        assert out["p_two_sided"] == 0.25

    def test_eight_zero_split_is_significant(self):
        out = sign_test([1, 2, 3, 4, 5, 6, 7, 8], [0, 0, 0, 0, 0, 0, 0, 0])
        assert out["n_pos"] == 8
        assert out["p_two_sided"] == pytest.approx(0.0078, abs=0.0005)

    def test_ties_dropped(self):
        out = sign_test([1, 1, 2], [1, 1, 1])
        assert out["n"] == 1
        assert out["p_two_sided"] == 1.0

    def test_all_ties_p_one(self):
        out = sign_test([5, 5], [5, 5])
        assert out["n"] == 0
        assert out["p_two_sided"] == 1.0


class TestWilcoxonSignedRank:
    def test_unequal_length_raises(self):
        with pytest.raises(ValueError):
            wilcoxon_signed_rank([1, 2], [1])

    def test_all_zero_diffs_degenerate(self):
        out = wilcoxon_signed_rank([3, 3, 3], [3, 3, 3])
        assert out["w_statistic"] is None
        assert out["z"] == 0.0
        assert out["p_two_sided"] == 1.0
        assert out["n_nonzero"] == 0
        assert out["sign_test"]["n"] == 0

    def test_separated_samples_with_tied_abs(self):
        # Every difference is -9: abs ranks all tie, exercising the tie term.
        out = wilcoxon_signed_rank([1, 2, 3, 4, 5], [10, 11, 12, 13, 14])
        assert out["w_statistic"] == 0
        assert out["w_plus"] == 0.0
        assert out["w_minus"] == 15.0
        assert out["n_nonzero"] == 5
        assert out["z"] == pytest.approx(2.087, abs=0.01)
        assert out["p_two_sided"] < 0.05

    def test_distinct_abs_no_ties(self):
        out = wilcoxon_signed_rank([1, 2, 3, 4, 5, 6, 7], [0, 4, 0, 8, 0, 12, 0])
        # diffs: +1, -2, +3, -4, +5, -6, +7 -> abs ranks 1..7
        assert out["w_plus"] == 16.0
        assert out["w_minus"] == 12.0
        assert out["w_statistic"] == 12.0
        assert out["n_nonzero"] == 7
        assert out["p_two_sided"] > 0.05

    def test_zero_diffs_dropped_and_sign_test_attached(self):
        out = wilcoxon_signed_rank([5, 5, 1, 2], [5, 5, 0, 5])
        assert out["n_nonzero"] == 2
        assert out["sign_test"]["n_pos"] == 1
        assert out["sign_test"]["n_neg"] == 1


class TestIccBootstrap:
    def test_empty_cells_degenerate(self):
        out = icc_bootstrap({})
        assert out["icc"] is None
        assert out["ci_low"] is None
        assert out["n_strategies"] == 0
        assert out["n_obs"] == 0

    def test_single_strategy_icc_zero(self):
        cells = {("a", "r1"): [1, 2], ("a", "r2"): [3, 4]}
        out = icc_bootstrap(cells, n_resamples=100)
        assert out["icc"] == 0.0

    def test_point_estimate_reconciles(self):
        cells = {
            ("colocate", "r1"): [60, 70],
            ("colocate", "r2"): [62, 68],
            ("spread", "r1"): [71, 69],
            ("spread", "r2"): [73, 67],
        }
        point = _icc_point(cells)
        assert point["sig2_iter"] == pytest.approx(11.0)
        assert point["sig2_strat"] == pytest.approx(6.25)
        assert point["icc"] == pytest.approx(0.3623, abs=0.001)
        out = icc_bootstrap(cells, n_resamples=200)
        assert out["icc"] == pytest.approx(0.3623, abs=0.001)
        assert out["ci_low"] is not None and out["ci_high"] is not None
        assert out["ci_low"] <= out["ci_high"]

    def test_single_observation_cell_nan_icc(self):
        out = icc_bootstrap({("a", "r1"): [5]}, n_resamples=10)
        assert out["icc"] is None
        assert out["n_obs"] == 1

    def test_strategy_with_only_empty_cells_skipped(self):
        # A strategy whose every cell is empty contributes no mean and is
        # skipped, leaving no strategy means -> NaN ICC (collapsed to None).
        point = _icc_point({("a", "r1"): []})
        assert math.isnan(point["icc"])

    def test_reproducible_with_seed(self):
        cells = {
            ("c", "r1"): [60, 70],
            ("c", "r2"): [62, 68],
            ("s", "r1"): [71, 69],
            ("s", "r2"): [73, 67],
        }
        a = icc_bootstrap(cells, seed=7, n_resamples=150)
        b = icc_bootstrap(cells, seed=7, n_resamples=150)
        assert a == b


class TestFDistribution:
    def test_betai_bounds(self):
        assert _betai(2.0, 3.0, 0.0) == 0.0
        assert _betai(2.0, 3.0, 1.0) == 1.0

    def test_f_sf_non_positive_is_one(self):
        assert _f_sf(0.0, 1, 4) == 1.0
        assert _f_sf(-2.0, 1, 4) == 1.0

    def test_f_sf_known_value(self):
        # F(1,4)=16 corresponds to a two-sided t(4)=4: p ~ 0.016.
        assert _f_sf(16.0, 1, 4) == pytest.approx(0.016, abs=0.003)

    def test_f_sf_betai_high_x_branch(self):
        # Small F pushes x large, exercising the symmetric betacf branch.
        p = _f_sf(0.01, 4, 4)
        assert 0.9 < p <= 1.0


class TestTwoFactorF:
    def test_known_balanced_decomposition(self):
        rows = [
            (0, 0, 1.0),
            (0, 0, 3.0),
            (0, 1, 2.0),
            (0, 1, 4.0),
            (1, 0, 5.0),
            (1, 0, 7.0),
            (1, 1, 6.0),
            (1, 1, 8.0),
        ]
        out = _two_factor_f(rows)
        assert out["factor_a"]["f"] == pytest.approx(16.0)
        assert out["factor_a"]["p"] == pytest.approx(0.016, abs=0.003)
        assert out["factor_b"]["f"] == pytest.approx(1.0)
        assert out["interaction"]["f"] == pytest.approx(0.0)
        assert out["interaction"]["p"] == 1.0

    def test_single_level_factor_gives_none(self):
        rows = [(0, 0, 1.0), (0, 1, 2.0), (0, 0, 3.0), (0, 1, 4.0)]
        out = _two_factor_f(rows)
        assert out["factor_a"]["f"] is None  # df_a == 0

    def test_no_replicates_zero_error_df(self):
        rows = [(0, 0, 1.0), (0, 1, 2.0), (1, 0, 3.0), (1, 1, 4.0)]
        out = _two_factor_f(rows)
        assert out["interaction"]["f"] is None  # df_error == 0

    def test_zero_within_cell_variance_gives_none(self):
        rows = [
            (0, 0, 1.0),
            (0, 0, 1.0),
            (0, 1, 2.0),
            (0, 1, 2.0),
            (1, 0, 3.0),
            (1, 0, 3.0),
            (1, 1, 4.0),
            (1, 1, 4.0),
        ]
        out = _two_factor_f(rows)
        assert out["factor_a"]["f"] is None  # ss_error == 0


class TestArtAnova:
    def test_degenerate_single_level(self):
        out = art_anova([(0, 0, 1.0), (0, 1, 2.0)])
        assert out["factor_a"]["f"] is None
        assert out["interaction"]["f"] is None
        assert out["n"] == 2

    def test_strong_interaction_detected(self):
        # Availability: low at 1 replica regardless of placement; at 3
        # replicas spread stays up while colocate collapses -> interaction.
        data = [
            (1, "spread", 0.00),
            (1, "spread", 0.05),
            (1, "colocate", 0.00),
            (1, "colocate", 0.02),
            (3, "spread", 0.98),
            (3, "spread", 0.95),
            (3, "colocate", 0.00),
            (3, "colocate", 0.05),
        ]
        out = art_anova(data)
        assert out["n"] == 8
        assert out["levels_a"] == [1, 3]
        assert out["interaction"]["f"] is not None
        assert 0.0 <= out["interaction"]["p"] <= 1.0
        assert out["interaction"]["p"] < 0.05

    def test_no_replicates_effects_none(self):
        data = [(1, "s", 0.1), (1, "c", 0.2), (3, "s", 0.9), (3, "c", 0.3)]
        out = art_anova(data)
        assert out["interaction"]["f"] is None


class TestPageTrendTest:
    def test_perfect_increasing_hand_computed(self):
        # 3 blocks, each [1,2,3] -> within-block ranks [1,2,3]; R=[3,6,9].
        # L = 1*3 + 2*6 + 3*9 = 42; E[L]=3*3*16/4=36; Var=3*9*4*8/144=6;
        # z = (42-36)/sqrt(6) = 2.449; p = SF(2.449) = 0.0072.
        out = page_trend_test([[1, 2, 3], [1, 2, 3], [1, 2, 3]])
        assert out["l_statistic"] == 42.0
        assert out["rank_sums"] == [3.0, 6.0, 9.0]
        assert out["z"] == 2.449
        assert out["p_one_sided"] == 0.0072
        assert out["n_blocks"] == 3 and out["k"] == 3

    def test_ties_use_average_ranks_with_tie_corrected_variance(self):
        # block2 all-equal -> ranks [2,2,2] (spread 0); block1 [1,2,3] (spread 2).
        # R=[3,4,5]; L=26; E[L]=24; S_c=2; Var=(S_c/(k-1))*Σspread=(2/2)*2=2;
        # z=(26-24)/sqrt(2)=1.414 (the tie correction shrinks Var from the
        # no-tie 4 to 2 — the constant block carries no null variability).
        out = page_trend_test([[1, 2, 3], [5, 5, 5]])
        assert out["l_statistic"] == 26.0
        assert out["rank_sums"] == [3.0, 4.0, 5.0]
        assert out["z"] == 1.414
        assert out["p_one_sided"] == round(0.5 * math.erfc(1.41421356 / math.sqrt(2)), 4)

    def test_all_tied_blocks_have_no_null_variability(self):
        # Every block constant -> no within-block spread -> Var[L]=0 -> z/p None,
        # but L is still reported.
        out = page_trend_test([[5, 5, 5], [7, 7, 7]])
        assert out["z"] is None and out["p_one_sided"] is None
        assert out["l_statistic"] is not None and out["k"] == 3

    def test_decreasing_trend_large_one_sided_p(self):
        # each block [3,2,1] -> ranks [3,2,1]; R=[9,6,3]; L=30 < E[L]=36 -> z<0.
        out = page_trend_test([[3, 2, 1], [3, 2, 1], [3, 2, 1]])
        assert out["l_statistic"] == 30.0
        assert out["z"] == -2.449
        assert out["p_one_sided"] > 0.99

    def test_empty_and_too_few_treatments_are_none(self):
        empty = page_trend_test([])
        assert empty["l_statistic"] is None and empty["n_blocks"] == 0 and empty["k"] == 0
        single = page_trend_test([[1.0], [2.0]])  # k < 2 -> no trend defined
        assert single["l_statistic"] is None and single["k"] == 1

    def test_unequal_block_lengths_raise(self):
        with pytest.raises(ValueError):
            page_trend_test([[1, 2], [1, 2, 3]])
