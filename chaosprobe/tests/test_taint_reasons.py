"""Tests for the per-iteration ``preChaosTaintReasons`` list and the
per-strategy ``taintReasonCounts`` roll-up in ``aggregate_iterations``.
"""

from chaosprobe.orchestrator.run_phases import aggregate_iterations


def _iter(score=80.0, verdict="PASS", reasons=None, healthy=None):
    """Build an iteration result; ``healthy`` defaults to ``not reasons``."""
    if reasons is None:
        reasons = []
    if healthy is None:
        healthy = not reasons
    return {
        "resilienceScore": score,
        "verdict": verdict,
        "preChaosHealthy": healthy,
        "preChaosTaintReasons": reasons,
        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1000}}},
    }


class TestTaintReasonCounts:
    def test_block_absent_when_no_taint(self):
        agg = aggregate_iterations([_iter(), _iter()])
        assert "taintReasonCounts" not in agg

    def test_single_reason_counted_per_iteration(self):
        agg = aggregate_iterations(
            [
                _iter(reasons=["pre_chaos_errors_high"]),
                _iter(),
                _iter(reasons=["pre_chaos_errors_high"]),
            ]
        )
        assert agg["taintReasonCounts"] == {"pre_chaos_errors_high": 2}

    def test_multiple_reasons_per_iteration_each_count_once(self):
        """An iteration where both gates fire should contribute one
        increment to each reason."""
        agg = aggregate_iterations(
            [
                _iter(reasons=["pre_chaos_errors_high", "pre_chaos_latency_degraded"]),
            ]
        )
        assert agg["taintReasonCounts"] == {
            "pre_chaos_errors_high": 1,
            "pre_chaos_latency_degraded": 1,
        }

    def test_exception_path_reason(self):
        agg = aggregate_iterations(
            [_iter(verdict="ERROR", reasons=["iteration_exception"], healthy=False)]
        )
        assert agg["taintReasonCounts"] == {"iteration_exception": 1}

    def test_mixed_reasons_across_strategy(self):
        agg = aggregate_iterations(
            [
                _iter(reasons=["pre_chaos_errors_high"]),
                _iter(reasons=["pre_chaos_latency_degraded"]),
                _iter(reasons=["pre_chaos_errors_high"]),
            ]
        )
        assert agg["taintReasonCounts"] == {
            "pre_chaos_errors_high": 2,
            "pre_chaos_latency_degraded": 1,
        }

    def test_non_list_reasons_skipped(self):
        agg = aggregate_iterations(
            [
                _iter(),
                {  # Malformed: reasons is a string, not a list.
                    "resilienceScore": 80,
                    "verdict": "PASS",
                    "preChaosHealthy": False,
                    "preChaosTaintReasons": "not-a-list",
                    "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1000}}},
                },
            ]
        )
        assert "taintReasonCounts" not in agg

    def test_non_string_entries_skipped(self):
        agg = aggregate_iterations([_iter(reasons=["pre_chaos_errors_high", 42, None, ""])])
        # "" is a string but empty — it does get counted as a key.  The
        # 42 and None get skipped by the isinstance check.
        counts = agg["taintReasonCounts"]
        assert counts.get("pre_chaos_errors_high") == 1
        assert 42 not in counts
        assert None not in counts


class TestPreChaosHealthyConsistency:
    def test_healthy_flag_derived_from_empty_reasons(self):
        """A healthy iteration has empty reasons AND preChaosHealthy=True.
        The aggregate's `taintedIterations` should reflect the bool, not
        the reasons list."""
        agg = aggregate_iterations(
            [
                _iter(),
                _iter(reasons=["pre_chaos_errors_high"]),
            ]
        )
        # The existing taintedIterations counter still works.
        assert agg["taintedIterations"] == 1
        # And the new reason-count block agrees.
        assert agg["taintReasonCounts"]["pre_chaos_errors_high"] == 1
