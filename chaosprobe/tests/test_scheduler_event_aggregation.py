"""Tests for the per-strategy scheduler-event roll-up in
``aggregate_iterations``.
"""

from chaosprobe.orchestrator.run_phases import aggregate_iterations


def _iter_with_events(events=None, score=80.0, mean_r=1000.0):
    """Iteration result carrying a list of scheduler events."""
    return {
        "resilienceScore": score,
        "verdict": "PASS",
        "metrics": {
            "recovery": {
                "summary": {"meanRecovery_ms": mean_r},
                "schedulerEvents": events or [],
            },
        },
    }


class TestSchedulerEventAggregation:
    def test_no_events_no_aggregate_key(self):
        agg = aggregate_iterations([_iter_with_events([])])
        assert "schedulerEventCounts" not in agg
        assert "schedulerEventIterationsCovered" not in agg

    def test_reason_totals_summed_across_iterations(self):
        agg = aggregate_iterations(
            [
                _iter_with_events(
                    [
                        {"reason": "Scheduled"},
                        {"reason": "Scheduled"},
                        {"reason": "Pulling"},
                    ]
                ),
                _iter_with_events(
                    [
                        {"reason": "Scheduled"},
                        {"reason": "FailedScheduling"},
                    ]
                ),
            ]
        )
        counts = agg["schedulerEventCounts"]
        assert counts["Scheduled"]["total"] == 3
        assert counts["Pulling"]["total"] == 1
        assert counts["FailedScheduling"]["total"] == 1

    def test_mean_per_iteration_denominator_excludes_empty_iterations(self):
        """`meanPerIteration` should be the mean over iterations *that
        carried any events* — not over all iterations.  Otherwise a single
        probe-only run zero-biases the per-strategy attribution."""
        agg = aggregate_iterations(
            [
                _iter_with_events([{"reason": "Scheduled"}, {"reason": "Scheduled"}]),
                _iter_with_events([]),  # no events — must not pull the mean down
                _iter_with_events([{"reason": "Scheduled"}]),
            ]
        )
        scheduled = agg["schedulerEventCounts"]["Scheduled"]
        # Two iterations carried events for Scheduled: counts were 2 and 1.
        assert scheduled["meanPerIteration"] == 1.5
        assert scheduled["maxPerIteration"] == 2
        assert scheduled["iterationsObserved"] == 2
        assert agg["schedulerEventIterationsCovered"] == 2

    def test_events_without_reason_skipped(self):
        agg = aggregate_iterations(
            [
                _iter_with_events(
                    [
                        {"reason": "Scheduled"},
                        {"reason": ""},
                        {},  # missing reason entirely
                    ]
                )
            ]
        )
        assert agg["schedulerEventCounts"]["Scheduled"]["total"] == 1
        assert "" not in agg["schedulerEventCounts"]

    def test_non_dict_events_dont_crash(self):
        agg = aggregate_iterations(
            [
                _iter_with_events(
                    [
                        {"reason": "Scheduled"},
                        "not-a-dict",  # robust against malformed inputs
                        None,
                    ]
                )
            ]
        )
        assert agg["schedulerEventCounts"]["Scheduled"]["total"] == 1

    def test_real_thesis_reasons_round_trip(self):
        """All nine reasons in `_SCHEDULER_EVENT_REASONS` should round-trip
        through the aggregation without being silently dropped."""
        reasons = [
            "Scheduled",
            "FailedScheduling",
            "BackOff",
            "FailedCreate",
            "FailedMount",
            "Pulling",
            "Pulled",
            "Failed",
            "Killing",
        ]
        agg = aggregate_iterations([_iter_with_events([{"reason": r} for r in reasons])])
        for r in reasons:
            assert agg["schedulerEventCounts"][r]["total"] == 1
