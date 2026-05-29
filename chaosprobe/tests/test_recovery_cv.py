"""Tests for the recovery-time coefficient-of-variation aggregation.

CV = stddev / mean.  A strategy that recovers in 1000±100 ms (CV=0.10)
is steadier than one at 200±100 ms (CV=0.50) even though their absolute
stddevs are identical — the CV decouples within-strategy jitter from
scale, the right normalisation for cross-strategy comparison.
"""

from chaosprobe.orchestrator.run_phases import aggregate_iterations


def _iter(mean_r, d2s=None, s2r=None, score=80.0):
    summary = {"meanRecovery_ms": mean_r}
    if d2s is not None:
        summary["meanDeletionToScheduled_ms"] = d2s
    if s2r is not None:
        summary["meanScheduledToReady_ms"] = s2r
    return {
        "resilienceScore": score,
        "verdict": "PASS",
        "metrics": {"recovery": {"summary": summary}},
    }


class TestRecoveryTimeCV:
    def test_steady_strategy_low_cv(self):
        agg = aggregate_iterations([_iter(1000), _iter(1010), _iter(990), _iter(1005), _iter(995)])
        # stddev ~ 8, mean 1000 → CV ≈ 0.008
        assert agg["recoveryTimeCV"] < 0.02
        assert agg["recoveryTimeCV"] > 0.0

    def test_jittery_strategy_high_cv(self):
        agg = aggregate_iterations([_iter(500), _iter(1500), _iter(800), _iter(1800)])
        # stddev ~ 600, mean ~ 1150 → CV ≈ 0.52
        assert agg["recoveryTimeCV"] > 0.3

    def test_no_recovery_data_no_cv_key(self):
        agg = aggregate_iterations(
            [
                {
                    "resilienceScore": 80,
                    "verdict": "PASS",
                    "metrics": {},
                }
            ]
        )
        assert "recoveryTimeCV" not in agg

    def test_single_iteration_zero_stddev_zero_cv(self):
        agg = aggregate_iterations([_iter(1000)])
        # stddev = 0, mean > 0 → CV = 0
        assert agg["recoveryTimeCV"] == 0.0

    def test_zero_mean_cv_is_none(self):
        agg = aggregate_iterations([_iter(0), _iter(0)])
        # mean = 0 → CV undefined → None
        assert agg["recoveryTimeCV"] is None


class TestSplitCVs:
    def test_d2s_cv_emitted_when_d2s_present(self):
        iters = [_iter(1000, d2s=200), _iter(1100, d2s=300), _iter(1200, d2s=250)]
        agg = aggregate_iterations(iters)
        assert "deletionToScheduledCV" in agg
        assert agg["deletionToScheduledCV"] > 0.0

    def test_s2r_cv_emitted_when_s2r_present(self):
        iters = [
            _iter(1000, s2r=800),
            _iter(1100, s2r=820),
            _iter(1200, s2r=810),
        ]
        agg = aggregate_iterations(iters)
        assert "scheduledToReadyCV" in agg
        # Steady s2r → low CV
        assert agg["scheduledToReadyCV"] < 0.05

    def test_d2s_absent_no_cv_block(self):
        agg = aggregate_iterations([_iter(1000), _iter(1100)])
        assert "deletionToScheduledCV" not in agg
        assert "scheduledToReadyCV" not in agg

    def test_d2s_present_but_s2r_missing(self):
        iters = [_iter(1000, d2s=200), _iter(1100, d2s=300)]
        agg = aggregate_iterations(iters)
        assert "deletionToScheduledCV" in agg
        assert "scheduledToReadyCV" not in agg

    def test_d2s_zero_mean_cv_none(self):
        iters = [_iter(1000, d2s=0), _iter(1100, d2s=0)]
        agg = aggregate_iterations(iters)
        assert agg["deletionToScheduledCV"] is None
