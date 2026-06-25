"""Tests for ``aggregate_iterations`` — multi-iteration per-strategy
roll-up.

Focuses on the bootstrap-CI additions (``meanResilienceScore_ci95``,
``meanRecoveryTime_ms_ci95``, ``meanDeletionToScheduled_ms_ci95``,
``meanScheduledToReady_ms_ci95``).  The thesis's H9 (scheduling latency
dominates recovery) needs CI bars on the recovery and split metrics,
not just on the resilience score.
"""

from chaosprobe.orchestrator.run_phases import aggregate_iterations


def _iter(
    score=80.0,
    verdict="PASS",
    mean_r=1000.0,
    max_r=2000.0,
    p95_r=1900.0,
    d2s=300.0,
    s2r=700.0,
    pre_chaos_healthy=True,
):
    """Build a single iteration_result dict with recovery summary."""
    return {
        "resilienceScore": score,
        "verdict": verdict,
        "preChaosHealthy": pre_chaos_healthy,
        "metrics": {
            "recovery": {
                "summary": {
                    "meanRecovery_ms": mean_r,
                    "maxRecovery_ms": max_r,
                    "p95Recovery_ms": p95_r,
                    "meanDeletionToScheduled_ms": d2s,
                    "meanScheduledToReady_ms": s2r,
                },
            },
        },
    }


class TestAggregateIterationsRecoveryCIs:
    def test_recovery_ci_attached_when_recovery_times_present(self):
        agg = aggregate_iterations(
            [
                _iter(mean_r=1000),
                _iter(mean_r=1100),
                _iter(mean_r=1200),
            ]
        )
        ci = agg["meanRecoveryTime_ms_ci95"]
        assert ci["n"] == 3
        assert ci["low"] <= agg["meanRecoveryTime_ms"] <= ci["high"]
        assert ci["n_resamples"] > 0

    def test_d2s_ci_attached_when_d2s_present(self):
        agg = aggregate_iterations(
            [
                _iter(d2s=200),
                _iter(d2s=300),
                _iter(d2s=400),
            ]
        )
        ci = agg["meanDeletionToScheduled_ms_ci95"]
        assert ci["n"] == 3
        assert ci["low"] <= agg["meanDeletionToScheduled_ms"] <= ci["high"]

    def test_s2r_ci_attached_when_s2r_present(self):
        agg = aggregate_iterations(
            [
                _iter(s2r=600),
                _iter(s2r=800),
                _iter(s2r=1000),
            ]
        )
        ci = agg["meanScheduledToReady_ms_ci95"]
        assert ci["n"] == 3
        assert ci["low"] <= agg["meanScheduledToReady_ms"] <= ci["high"]

    def test_no_recovery_summary_no_recovery_cis(self):
        agg = aggregate_iterations(
            [
                {"resilienceScore": 80.0, "verdict": "PASS", "metrics": {}},
                {"resilienceScore": 90.0, "verdict": "PASS", "metrics": {}},
            ]
        )
        assert "meanRecoveryTime_ms_ci95" not in agg
        assert "meanDeletionToScheduled_ms_ci95" not in agg
        assert "meanScheduledToReady_ms_ci95" not in agg

    def test_resilience_ci_still_attached(self):
        """The pre-existing meanResilienceScore_ci95 must keep working
        unchanged when the new CIs are added."""
        agg = aggregate_iterations([_iter(score=70), _iter(score=80), _iter(score=90)])
        ci = agg["meanResilienceScore_ci95"]
        assert ci["n"] == 3
        assert ci["low"] <= agg["meanResilienceScore"] <= ci["high"]

    def test_partial_d2s_only_s2r_missing(self):
        """If only d2s is present, only the d2s CI shows up; s2r block is
        omitted."""
        agg = aggregate_iterations(
            [
                {
                    "resilienceScore": 80.0,
                    "verdict": "PASS",
                    "metrics": {
                        "recovery": {
                            "summary": {
                                "meanRecovery_ms": 1000,
                                "meanDeletionToScheduled_ms": 300,
                            }
                        }
                    },
                },
                {
                    "resilienceScore": 85.0,
                    "verdict": "PASS",
                    "metrics": {
                        "recovery": {
                            "summary": {
                                "meanRecovery_ms": 1100,
                                "meanDeletionToScheduled_ms": 350,
                            }
                        }
                    },
                },
            ]
        )
        assert "meanDeletionToScheduled_ms_ci95" in agg
        assert "meanScheduledToReady_ms_ci95" not in agg

    def test_single_iteration_ci_collapses_to_point(self):
        """With n=1 the bootstrap CI degenerates to (point, point) — the
        helper must still return a usable dict instead of crashing."""
        agg = aggregate_iterations([_iter(mean_r=1234)])
        ci = agg["meanRecoveryTime_ms_ci95"]
        assert ci["n"] == 1
        assert ci["low"] == ci["high"] == agg["meanRecoveryTime_ms"]


class TestAggregateIterationsAllError:
    """When every iteration is ERROR there is no valid measurement: the
    roll-up must report ``null`` score statistics plus ``allIterationsError``
    rather than fabricating a 0.0 mean from the excluded ERROR scores."""

    def test_all_error_reports_null_mean_and_flag(self):
        agg = aggregate_iterations(
            [_iter(score=0.0, verdict="ERROR"), _iter(score=0.0, verdict="ERROR")]
        )
        assert agg["allIterationsError"] is True
        assert agg["meanResilienceScore"] is None
        assert agg["errors"] == 2
        assert agg["totalExperiments"] == 2
        assert agg["passed"] == 0
        # Raw per-iteration scores are still surfaced for debugging; they are
        # simply not summarised into a (meaningless) mean.
        assert agg["perIterationScores"] == [0.0, 0.0]
        # No fabricated point estimates leak through.
        assert "meanResilienceScore_ci95" not in agg
        assert "harmonicMeanResilienceScore" not in agg

    def test_one_valid_iteration_is_not_all_error(self):
        agg = aggregate_iterations(
            [_iter(score=70.0, verdict="PASS"), _iter(score=0.0, verdict="ERROR")]
        )
        assert agg.get("allIterationsError") is not True
        # The single valid iteration drives the mean; the ERROR is excluded.
        assert agg["meanResilienceScore"] == 70.0
        assert agg["errors"] == 1


class TestAggregateIterationsPassRate:
    """passRate / overallVerdict exclude ERROR iterations from the denominator,
    so a transient infra ERROR cannot mislabel an otherwise-passing strategy as
    FAIL (REVIEW.md W1)."""

    def test_error_iteration_excluded_from_passrate(self):
        # Two valid PASS + one ERROR: every *valid* iteration passed.
        agg = aggregate_iterations(
            [
                _iter(score=80.0, verdict="PASS"),
                _iter(score=90.0, verdict="PASS"),
                _iter(score=0.0, verdict="ERROR"),
            ]
        )
        assert agg["passRate"] == 1.0
        assert agg["overallVerdict"] == "PASS"
        assert agg["passed"] == 2
        assert agg["errors"] == 1

    def test_real_failure_still_lowers_passrate(self):
        # A genuine FAIL (not an ERROR) is still counted against the strategy.
        agg = aggregate_iterations(
            [
                _iter(score=80.0, verdict="PASS"),
                _iter(score=10.0, verdict="FAIL"),
            ]
        )
        assert agg["passRate"] == 0.5
        assert agg["overallVerdict"] == "FAIL"

    def test_all_error_passrate_zero(self):
        agg = aggregate_iterations(
            [_iter(score=0.0, verdict="ERROR"), _iter(score=0.0, verdict="ERROR")]
        )
        assert agg["passRate"] == 0.0
        assert agg["overallVerdict"] == "FAIL"
