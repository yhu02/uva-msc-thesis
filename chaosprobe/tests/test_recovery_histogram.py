"""Tests for the per-strategy recovery-time histogram aggregation.

Mean + stddev + CV tell us spread; the histogram tells us *shape*.  A
bimodal distribution (mostly fast + a few catastrophic recoveries)
looks the same as unimodal-with-noise by stddev alone but has very
different mechanism implications: bimodal points at a specific
failure-class triggered on a subset of iterations.
"""

from chaosprobe.orchestrator.run_phases import _bucket_recovery_times, aggregate_iterations


def _iter(mean_r):
    return {
        "resilienceScore": 80.0,
        "verdict": "PASS",
        "metrics": {"recovery": {"summary": {"meanRecovery_ms": mean_r}}},
    }


class TestBucketRecoveryTimes:
    def test_known_values_land_in_expected_buckets(self):
        out = _bucket_recovery_times([100, 700, 1500, 3000, 7000, 15000])
        assert out["lt_500ms"] == 1
        assert out["500_to_1000ms"] == 1
        assert out["1000_to_2000ms"] == 1
        assert out["2000_to_5000ms"] == 1
        assert out["5000_to_10000ms"] == 1
        assert out["gte_10000ms"] == 1

    def test_boundary_values_go_to_higher_bucket(self):
        out = _bucket_recovery_times([500, 1000, 2000, 5000, 10000])
        # < boundary lands lower; == boundary lands higher.
        assert out["lt_500ms"] == 0
        assert out["500_to_1000ms"] == 1
        assert out["1000_to_2000ms"] == 1
        assert out["2000_to_5000ms"] == 1
        assert out["5000_to_10000ms"] == 1
        assert out["gte_10000ms"] == 1

    def test_empty_input_all_zero_counts(self):
        out = _bucket_recovery_times([])
        assert all(v == 0 for v in out.values())
        assert set(out.keys()) == {
            "lt_500ms",
            "500_to_1000ms",
            "1000_to_2000ms",
            "2000_to_5000ms",
            "5000_to_10000ms",
            "gte_10000ms",
        }

    def test_total_count_matches_input_length(self):
        values = [123, 456, 789, 1234, 5678, 9999, 10001, 20000]
        out = _bucket_recovery_times(values)
        assert sum(out.values()) == len(values)


class TestHistogramInAggregate:
    def test_histogram_attached_when_recovery_present(self):
        agg = aggregate_iterations(
            [_iter(800), _iter(1500), _iter(3000), _iter(7000), _iter(15000)]
        )
        hist = agg["recoveryTimeHistogram_ms"]
        assert hist["500_to_1000ms"] == 1
        assert hist["1000_to_2000ms"] == 1
        assert hist["2000_to_5000ms"] == 1
        assert hist["5000_to_10000ms"] == 1
        assert hist["gte_10000ms"] == 1
        # No iteration landed in the lt_500ms bucket.
        assert hist["lt_500ms"] == 0

    def test_histogram_absent_when_no_recovery(self):
        agg = aggregate_iterations(
            [
                {
                    "resilienceScore": 80,
                    "verdict": "PASS",
                    "metrics": {},
                }
            ]
        )
        assert "recoveryTimeHistogram_ms" not in agg

    def test_bimodal_distribution_visible(self):
        """A strategy with mostly-fast recoveries plus a few slow ones
        — typical "tail-at-scale" signature — shows up as two non-zero
        buckets with a quiet middle, even when stddev would suggest
        moderate spread."""
        # 4 fast recoveries + 1 catastrophic.
        agg = aggregate_iterations([_iter(200), _iter(300), _iter(250), _iter(280), _iter(8000)])
        hist = agg["recoveryTimeHistogram_ms"]
        assert hist["lt_500ms"] == 4
        assert hist["5000_to_10000ms"] == 1
        # Middle buckets quiet.
        assert hist["500_to_1000ms"] == 0
        assert hist["1000_to_2000ms"] == 0
        assert hist["2000_to_5000ms"] == 0

    def test_bucket_label_order_stable(self):
        agg = aggregate_iterations([_iter(800), _iter(15000)])
        labels = list(agg["recoveryTimeHistogram_ms"].keys())
        assert labels == [
            "lt_500ms",
            "500_to_1000ms",
            "1000_to_2000ms",
            "2000_to_5000ms",
            "5000_to_10000ms",
            "gte_10000ms",
        ]
