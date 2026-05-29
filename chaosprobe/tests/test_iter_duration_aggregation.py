"""Tests for per-iteration ``experimentDuration_s`` aggregation in
``aggregate_iterations``.
"""

from chaosprobe.orchestrator.run_phases import aggregate_iterations


def _iter(duration=None, mean_r=1000.0):
    out = {
        "resilienceScore": 80.0,
        "verdict": "PASS",
        "metrics": {"recovery": {"summary": {"meanRecovery_ms": mean_r}}},
    }
    if duration is not None:
        out["experimentDuration_s"] = duration
    return out


class TestExperimentDurationAggregation:
    def test_mean_min_max_emitted_when_durations_present(self):
        agg = aggregate_iterations(
            [_iter(duration=120.0), _iter(duration=130.0), _iter(duration=125.0)]
        )
        assert agg["meanExperimentDuration_s"] == 125.0
        assert agg["minExperimentDuration_s"] == 120.0
        assert agg["maxExperimentDuration_s"] == 130.0
        assert "stddevExperimentDuration_s" in agg

    def test_no_duration_no_block(self):
        agg = aggregate_iterations([_iter()])
        assert "meanExperimentDuration_s" not in agg
        assert "stddevExperimentDuration_s" not in agg

    def test_single_iteration_no_stddev(self):
        """stddev needs >=2 points; we omit it for a single iteration but
        still emit mean / min / max."""
        agg = aggregate_iterations([_iter(duration=120.0)])
        assert agg["meanExperimentDuration_s"] == 120.0
        assert agg["minExperimentDuration_s"] == 120.0
        assert agg["maxExperimentDuration_s"] == 120.0
        assert "stddevExperimentDuration_s" not in agg

    def test_non_numeric_duration_silently_skipped(self):
        iters = [
            _iter(duration=120.0),
            _iter(duration="oops"),
            _iter(duration=130.0),
        ]
        agg = aggregate_iterations(iters)
        # Only the two valid durations contribute.
        assert agg["meanExperimentDuration_s"] == 125.0
        assert agg["minExperimentDuration_s"] == 120.0

    def test_mixed_present_and_absent_only_present_used(self):
        iters = [_iter(duration=100.0), _iter(), _iter(duration=200.0)]
        agg = aggregate_iterations(iters)
        assert agg["meanExperimentDuration_s"] == 150.0
        assert agg["maxExperimentDuration_s"] == 200.0
