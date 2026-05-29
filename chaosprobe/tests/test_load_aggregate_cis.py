"""Tests for the bootstrap CIs on ``loadGenerationAggregate``.

Without CIs on offered RPS / error rate / response time, a reviewer cannot
rule out load drift across iterations as the cause of inter-strategy
score differences.  These tests pin the CI shape next to the existing
point estimates.
"""

from chaosprobe.orchestrator.run_phases import aggregate_iterations


def _iter(rps=10.0, err=0.0, resp=120.0, score=80.0, mean_r=1000.0):
    return {
        "resilienceScore": score,
        "verdict": "PASS",
        "metrics": {"recovery": {"summary": {"meanRecovery_ms": mean_r}}},
        "loadGeneration": {
            "stats": {
                "requestsPerSecond": rps,
                "errorRate": err,
                "p95ResponseTime_ms": resp,
            }
        },
    }


class TestLoadAggregateCIs:
    def test_rps_ci_attached(self):
        agg = aggregate_iterations([_iter(rps=9.0), _iter(rps=10.0), _iter(rps=11.0)])
        load = agg["loadGenerationAggregate"]
        ci = load["meanRequestsPerSecond_ci95"]
        assert ci["n"] == 3
        assert ci["low"] <= load["meanRequestsPerSecond"] <= ci["high"]

    def test_error_rate_ci_attached(self):
        agg = aggregate_iterations([_iter(err=0.01), _iter(err=0.02), _iter(err=0.03)])
        load = agg["loadGenerationAggregate"]
        ci = load["meanErrorRate_ci95"]
        assert ci["n"] == 3
        assert ci["low"] <= load["meanErrorRate"] <= ci["high"]

    def test_response_time_ci_attached(self):
        agg = aggregate_iterations([_iter(resp=100.0), _iter(resp=120.0), _iter(resp=140.0)])
        load = agg["loadGenerationAggregate"]
        ci = load["meanResponseTime_ms_ci95"]
        assert ci["n"] == 3
        assert ci["low"] <= load["meanResponseTime_ms"] <= ci["high"]

    def test_no_load_data_no_load_aggregate(self):
        """Iteration without loadGeneration.stats → no loadGenerationAggregate
        key, no crash."""
        agg = aggregate_iterations(
            [
                {
                    "resilienceScore": 80.0,
                    "verdict": "PASS",
                    "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1000}}},
                }
            ]
        )
        assert "loadGenerationAggregate" not in agg

    def test_partial_load_data_only_rps_ci(self):
        """Iterations with rps only → only the rps CI emitted, not error/
        response."""
        iters = [
            {
                "resilienceScore": 80.0,
                "verdict": "PASS",
                "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1000}}},
                "loadGeneration": {"stats": {"requestsPerSecond": 10.0}},
            },
            {
                "resilienceScore": 85.0,
                "verdict": "PASS",
                "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1100}}},
                "loadGeneration": {"stats": {"requestsPerSecond": 11.0}},
            },
        ]
        load = aggregate_iterations(iters)["loadGenerationAggregate"]
        assert "meanRequestsPerSecond_ci95" in load
        assert "meanErrorRate_ci95" not in load
        assert "meanResponseTime_ms_ci95" not in load

    def test_single_iteration_ci_collapses(self):
        load = aggregate_iterations([_iter(rps=10.0)])["loadGenerationAggregate"]
        ci = load["meanRequestsPerSecond_ci95"]
        assert ci["n"] == 1
        assert ci["low"] == ci["high"] == load["meanRequestsPerSecond"]
