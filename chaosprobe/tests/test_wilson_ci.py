"""Tests for the Wilson score interval helper and its use in the
per-probe success-rate roll-up.
"""

from chaosprobe.metrics.statistics import wilson_ci
from chaosprobe.orchestrator.run_phases import aggregate_iterations


class TestWilsonCI:
    def test_zero_total_returns_none_bounds(self):
        out = wilson_ci(0, 0)
        assert out["point"] is None
        assert out["ci_low"] is None
        assert out["ci_high"] is None

    def test_full_success_low_n_does_not_collapse(self):
        """Normal-approximation interval collapses to (1.0, 1.0) at p̂=1.
        Wilson keeps a real lower bound — this is the load-bearing
        property at thesis n's (n=5 iterations per probe)."""
        out = wilson_ci(5, 5)
        assert out["point"] == 1.0
        assert out["ci_low"] < 1.0  # not collapsed
        assert out["ci_high"] == 1.0

    def test_full_failure_low_n_does_not_collapse(self):
        out = wilson_ci(0, 5)
        assert out["point"] == 0.0
        assert out["ci_low"] == 0.0
        assert out["ci_high"] > 0.0

    def test_half_success(self):
        out = wilson_ci(5, 10)
        assert out["point"] == 0.5
        assert out["ci_low"] < 0.5 < out["ci_high"]

    def test_bounds_in_unit_interval(self):
        """Wilson never reports a probability outside [0, 1]."""
        for s in (0, 1, 2, 3, 4, 5):
            out = wilson_ci(s, 5)
            assert 0.0 <= out["ci_low"] <= 1.0
            assert 0.0 <= out["ci_high"] <= 1.0

    def test_larger_n_tighter_interval(self):
        """The CI width must shrink as n grows — pin the monotonicity so
        a future bug can't silently widen the bounds at high n."""
        narrow_n5 = wilson_ci(2, 5)
        narrow_n50 = wilson_ci(20, 50)  # same point, 10x n
        width_5 = narrow_n5["ci_high"] - narrow_n5["ci_low"]
        width_50 = narrow_n50["ci_high"] - narrow_n50["ci_low"]
        assert width_50 < width_5

    def test_unknown_confidence_falls_back_to_95(self):
        out = wilson_ci(3, 5, confidence=0.123)
        assert out["confidence"] == 0.95


class TestProbeSuccessRatesInAggregate:
    def _iter(self, verdicts):
        return {
            "resilienceScore": 80.0,
            "verdict": "PASS",
            "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1000}}},
            "probeVerdicts": verdicts,
        }

    def test_block_attached_when_probe_tally_present(self):
        iters = [
            self._iter({"frontend-availability": "Pass", "cart-availability": "Pass"}),
            self._iter({"frontend-availability": "Pass", "cart-availability": "Fail"}),
            self._iter({"frontend-availability": "Fail", "cart-availability": "Fail"}),
        ]
        agg = aggregate_iterations(iters)
        rates = agg["probeSuccessRates"]
        assert set(rates.keys()) == {"frontend-availability", "cart-availability"}

        frontend = rates["frontend-availability"]
        assert frontend["successes"] == 2
        assert frontend["total"] == 3
        assert frontend["point"] == round(2 / 3, 4)
        assert frontend["ci_low"] < frontend["point"] < frontend["ci_high"]

    def test_unknown_verdicts_excluded_from_denominator(self):
        """Unknown verdicts (probe didn't fire / inconclusive) are not
        counted in the denominator — including them would systematically
        bias the success rate downward."""
        iters = [
            self._iter({"p": "Pass"}),
            self._iter({"p": "Unknown"}),
            self._iter({"p": "Unknown"}),
        ]
        agg = aggregate_iterations(iters)
        rates = agg["probeSuccessRates"]["p"]
        assert rates["successes"] == 1
        assert rates["total"] == 1  # Two Unknowns excluded
        assert rates["unknown"] == 2

    def test_all_unknown_emits_block_with_none_bounds(self):
        iters = [self._iter({"p": "Unknown"}), self._iter({"p": "Unknown"})]
        agg = aggregate_iterations(iters)
        rates = agg["probeSuccessRates"]["p"]
        assert rates["total"] == 0
        assert rates["point"] is None
        assert rates["ci_low"] is None
        assert rates["unknown"] == 2

    def test_no_probe_verdicts_no_block(self):
        agg = aggregate_iterations(
            [
                {
                    "resilienceScore": 80,
                    "verdict": "PASS",
                    "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1000}}},
                }
            ]
        )
        assert "probeSuccessRates" not in agg
        assert "probeVerdictTally" not in agg
