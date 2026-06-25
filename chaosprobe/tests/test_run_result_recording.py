"""Unit tests for the per-strategy result-recording helpers extracted from ``run``.

The error-result construction and the dual-view storage + pass/fail accounting
were inline in the ~440-line ``run`` command's nested loop, so the
"what counts as a pass" rule and the multi-fault flat-key format had no unit
coverage.
"""

from chaosprobe.commands.run_cmd import _error_strategy_result, _record_strategy_result


def _empty_overall(fault_label):
    return {"faults": {fault_label: {"strategies": {}}}, "strategies": {}}


class TestErrorStrategyResult:
    def test_shape(self):
        r = _error_strategy_result("colocate", "fault1", "boom")
        assert r == {
            "strategy": "colocate",
            "fault": "fault1",
            "status": "error",
            "placement": None,
            "experiment": None,
            "metrics": None,
            "error": "boom",
        }


class TestRecordStrategyResult:
    def test_passing_result_counts_as_pass_and_is_stored_both_views(self):
        overall = _empty_overall("f")
        result = {"strategy": "spread", "status": "completed"}
        counted = _record_strategy_result(overall, "f", "spread", result, True, multi_fault=False)
        assert counted is True
        assert overall["faults"]["f"]["strategies"]["spread"] is result
        assert overall["strategies"]["spread"] is result  # bare key when single-fault
        assert result["fault"] == "f"

    def test_failed_verdict_counts_as_failure(self):
        overall = _empty_overall("f")
        result = {"strategy": "colocate", "status": "completed"}
        assert (
            _record_strategy_result(overall, "f", "colocate", result, False, multi_fault=False)
            is False
        )

    def test_error_status_never_counts_as_pass(self):
        overall = _empty_overall("f")
        result = {"strategy": "default", "status": "error"}
        # Even if strategy_passed were True, an errored result is a failure.
        assert (
            _record_strategy_result(overall, "f", "default", result, True, multi_fault=False)
            is False
        )

    def test_multi_fault_uses_namespaced_flat_key(self):
        overall = _empty_overall("cpuhog")
        result = {"strategy": "spread", "status": "completed"}
        _record_strategy_result(overall, "cpuhog", "spread", result, True, multi_fault=True)
        assert overall["strategies"]["cpuhog__spread"] is result
        assert "spread" not in overall["strategies"]
