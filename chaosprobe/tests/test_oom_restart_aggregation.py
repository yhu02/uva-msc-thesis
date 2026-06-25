"""Tests for the per-strategy OOMKill / restart roll-up in
``aggregate_iterations``.

Both counts already existed per iteration in ``metrics.podStatus``; the
per-strategy summary now exposes totals, per-iteration mean, max, and
iterations-with-event counts so "colocate produced 4× more OOMKills than
spread" is a directly readable number.
"""

from chaosprobe.orchestrator.run_phases import aggregate_iterations


def _iter(oom=0, restarts=0, score=80.0, mean_r=1000.0):
    return {
        "resilienceScore": score,
        "verdict": "PASS",
        "metrics": {
            "recovery": {"summary": {"meanRecovery_ms": mean_r}},
            "podStatus": {
                "totalOOMKills": oom,
                "totalRestarts": restarts,
            },
        },
    }


class TestOOMAggregation:
    def test_totals_summed_and_mean_max_emitted(self):
        agg = aggregate_iterations([_iter(oom=0), _iter(oom=2), _iter(oom=3)])
        assert agg["totalOOMKills"] == 5
        assert agg["meanOOMKillsPerIteration"] == round(5 / 3, 2)
        assert agg["maxOOMKillsPerIteration"] == 3
        assert agg["iterationsWithOOMKills"] == 2  # 0 doesn't count

    def test_no_pod_status_no_oom_block(self):
        """If no iteration carries podStatus.totalOOMKills, the entire
        OOMKill block is absent."""
        agg = aggregate_iterations(
            [
                {
                    "resilienceScore": 80,
                    "verdict": "PASS",
                    "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1000}}},
                }
            ]
        )
        assert "totalOOMKills" not in agg
        assert "meanOOMKillsPerIteration" not in agg

    def test_iterations_with_oom_zero_when_all_zero(self):
        agg = aggregate_iterations([_iter(oom=0), _iter(oom=0)])
        assert agg["totalOOMKills"] == 0
        assert agg["iterationsWithOOMKills"] == 0


class TestRestartAggregation:
    def test_totals_summed_and_mean_max_emitted(self):
        agg = aggregate_iterations([_iter(restarts=0), _iter(restarts=4), _iter(restarts=2)])
        assert agg["totalRestarts"] == 6
        assert agg["meanRestartsPerIteration"] == 2.0
        assert agg["maxRestartsPerIteration"] == 4
        assert agg["iterationsWithRestarts"] == 2

    def test_no_pod_status_no_restart_block(self):
        agg = aggregate_iterations(
            [
                {
                    "resilienceScore": 80,
                    "verdict": "PASS",
                    "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1000}}},
                }
            ]
        )
        assert "totalRestarts" not in agg
        assert "iterationsWithRestarts" not in agg


class TestBothBlocksCoexist:
    def test_oom_and_restarts_both_emitted_independently(self):
        agg = aggregate_iterations([_iter(oom=1, restarts=3), _iter(oom=2, restarts=0)])
        assert agg["totalOOMKills"] == 3
        assert agg["totalRestarts"] == 3
        assert agg["iterationsWithOOMKills"] == 2
        assert agg["iterationsWithRestarts"] == 1

    def test_string_values_skipped_no_crash(self):
        """If something upstream wrote a string into totalOOMKills (e.g.
        an error sentinel), the iteration is silently skipped rather than
        crashing the aggregation."""
        iters = [
            _iter(oom=1, restarts=2),
            {
                "resilienceScore": 80,
                "verdict": "PASS",
                "metrics": {
                    "recovery": {"summary": {"meanRecovery_ms": 1000}},
                    "podStatus": {"totalOOMKills": "n/a", "totalRestarts": "n/a"},
                },
            },
        ]
        agg = aggregate_iterations(iters)
        assert agg["totalOOMKills"] == 1
        assert agg["totalRestarts"] == 2
