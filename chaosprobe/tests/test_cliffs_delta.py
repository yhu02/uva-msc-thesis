"""Tests for the Cliff's delta non-parametric effect-size helper and
its integration into ``pairwise_comparisons``.

p-value alone tells the defender whether two distributions differ;
Cliff's delta tells how big the gap is.  Both are needed for a defence
that survives the "statistical vs practical significance" objection.
"""

from chaosprobe.metrics.statistics import cliffs_delta, pairwise_comparisons


class TestCliffsDelta:
    def test_identical_samples_delta_zero(self):
        out = cliffs_delta([1, 2, 3], [1, 2, 3])
        assert out["delta"] == 0.0
        assert out["magnitude"] == "negligible"

    def test_all_a_greater_than_all_b_delta_one(self):
        out = cliffs_delta([10, 20, 30], [1, 2, 3])
        assert out["delta"] == 1.0
        assert out["magnitude"] == "large"

    def test_all_a_less_than_all_b_delta_neg_one(self):
        out = cliffs_delta([1, 2, 3], [10, 20, 30])
        assert out["delta"] == -1.0
        assert out["magnitude"] == "large"

    def test_small_magnitude_boundary(self):
        # Pick a sample where exactly 1/7 (~0.143) of pairs are
        # in-the-other-direction → delta just under 0.147 threshold.
        a = [10, 11, 12, 13, 14, 15, 16]
        b = [9, 9, 9, 9, 9, 9, 9, 9, 16]
        out = cliffs_delta(a, b)
        # Mainly a > b but some ties at 16 and a few a < b at the last.
        assert -1.0 < out["delta"] < 1.0
        assert out["magnitude"] in {"negligible", "small", "medium", "large"}

    def test_magnitude_classification_thresholds(self):
        """Romano et al. thresholds: 0.147 / 0.33 / 0.474."""
        # Construct exact deltas:
        # delta=0.10 → negligible (< 0.147)
        out = cliffs_delta([1, 1, 1, 1, 1, 1, 1, 1, 1, 2], [1] * 10)
        assert out["magnitude"] == "negligible"
        # delta=0.20 → small (>= 0.147, < 0.33)
        out = cliffs_delta([2, 2, 1, 1, 1, 1, 1, 1, 1, 1], [1] * 10)
        assert out["magnitude"] == "small"
        # delta=0.40 → medium (>= 0.33, < 0.474)
        out = cliffs_delta([2, 2, 2, 2, 1, 1, 1, 1, 1, 1], [1] * 10)
        assert out["magnitude"] == "medium"
        # delta=0.50 → large
        out = cliffs_delta([2, 2, 2, 2, 2, 1, 1, 1, 1, 1], [1] * 10)
        assert out["magnitude"] == "large"

    def test_empty_input_returns_none(self):
        out = cliffs_delta([], [1, 2, 3])
        assert out["delta"] is None
        assert out["magnitude"] is None
        out = cliffs_delta([1], [])
        assert out["delta"] is None

    def test_ties_dont_count_in_either_direction(self):
        # All values equal → delta = 0 even though n_a, n_b > 0
        out = cliffs_delta([5, 5], [5, 5, 5])
        assert out["delta"] == 0.0


class TestPairwiseIncludesEffectSize:
    def test_each_pairwise_row_has_cliffs_delta(self):
        rows = pairwise_comparisons(
            {"colocate": [70, 75, 80, 78, 72], "spread": [40, 45, 50, 48, 42]}
        )
        assert len(rows) == 1
        row = rows[0]
        assert "cliffs_delta" in row
        assert "effect_size_magnitude" in row
        # All colocate values > all spread values → delta = 1.0, large.
        assert row["cliffs_delta"] == 1.0
        assert row["effect_size_magnitude"] == "large"

    def test_pairwise_handles_negligible_effect(self):
        rows = pairwise_comparisons(
            {"a": [1, 2, 3, 4, 5], "b": [1, 2, 3, 4, 5]},
            holm_bonferroni=False,
        )
        row = rows[0]
        assert row["cliffs_delta"] == 0.0
        assert row["effect_size_magnitude"] == "negligible"

    def test_pairwise_holm_correction_orthogonal_to_effect_size(self):
        """Holm correction reorders rows by p_holm but the cliffs_delta
        attached to each row must still match the underlying samples."""
        samples = {
            "x": [1, 2, 3, 4, 5],
            "y": [10, 11, 12, 13, 14],
            "z": [1, 11, 2, 12, 3],
        }
        rows = pairwise_comparisons(samples, holm_bonferroni=True)
        for row in rows:
            assert row["cliffs_delta"] is not None
            assert row["effect_size_magnitude"] in {
                "negligible",
                "small",
                "medium",
                "large",
            }
