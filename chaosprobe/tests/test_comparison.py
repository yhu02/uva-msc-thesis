"""Unit tests for output.comparison.compare_runs and helpers."""

from chaosprobe.output.comparison import (
    _calculate_confidence,
    _compare_experiments,
    _compare_metrics,
    _determine_fix_effectiveness,
    _evaluate_improvement_criteria,
    compare_runs,
)


def _run(score: float, verdict: str, experiments=None, metrics=None):
    """Build a minimal run-result dict suitable for compare_runs."""
    return {
        "runId": f"run-{score}",
        "timestamp": "2026-05-27T00:00:00+00:00",
        "scenario": {},
        "summary": {"resilienceScore": score, "overallVerdict": verdict},
        "experiments": experiments or [],
        "metrics": metrics or {},
    }


class TestCompareRunsTopLevel:
    def test_score_change_and_verdict_change(self):
        baseline = _run(40.0, "FAIL")
        after = _run(85.0, "PASS")
        result = compare_runs(baseline, after)

        assert result["comparison"]["resilienceScoreChange"] == 45.0
        assert result["comparison"]["verdictChanged"] is True
        assert result["comparison"]["previousVerdict"] == "FAIL"
        assert result["comparison"]["newVerdict"] == "PASS"
        # FAIL→PASS always counts as effective
        assert result["conclusion"]["fixEffective"] is True
        assert 0.0 < result["conclusion"]["confidence"] <= 0.99

    def test_no_improvement_yields_not_effective(self):
        baseline = _run(50.0, "FAIL")
        after = _run(48.0, "FAIL")
        result = compare_runs(baseline, after)

        assert result["comparison"]["resilienceScoreChange"] == -2.0
        assert result["conclusion"]["fixEffective"] is False
        assert "did not fully resolve" in result["conclusion"]["summary"]

    def test_default_criteria_applied(self):
        # Score change below the default 10-point threshold AND verdict unchanged
        baseline = _run(60.0, "FAIL")
        after = _run(65.0, "FAIL")
        result = compare_runs(baseline, after)
        criteria = result["comparison"]["improvementCriteriaMet"]
        assert criteria["resilienceScoreIncrease"]["met"] is False
        assert result["conclusion"]["fixEffective"] is False

    def test_custom_criteria_override_defaults(self):
        baseline = _run(60.0, "FAIL")
        after = _run(65.0, "FAIL")
        # Lower the bar so a 5-point bump qualifies
        result = compare_runs(
            baseline,
            after,
            improvement_criteria={"resilienceScoreIncrease": 5, "probeSuccessIncrease": 0},
        )
        assert (
            result["comparison"]["improvementCriteriaMet"]["resilienceScoreIncrease"]["met"] is True
        )

    def test_missing_summary_keys_fall_back_to_defaults(self):
        baseline = {"summary": {}, "experiments": [], "metrics": {}}
        after = {"summary": {}, "experiments": [], "metrics": {}}
        result = compare_runs(baseline, after)
        assert result["comparison"]["resilienceScoreChange"] == 0


class TestCompareExperiments:
    def test_matches_by_name_and_computes_probe_delta(self):
        baseline = [
            {
                "name": "exp1",
                "result": {"verdict": "Fail", "probeSuccessPercentage": 20},
            },
            {
                "name": "exp2",
                "result": {"verdict": "Pass", "probeSuccessPercentage": 100},
            },
        ]
        after = [
            {
                "name": "exp1",
                "result": {"verdict": "Pass", "probeSuccessPercentage": 80},
            },
            {
                "name": "exp2",
                "result": {"verdict": "Pass", "probeSuccessPercentage": 100},
            },
        ]
        result = _compare_experiments(baseline, after)
        by_name = {e["experimentName"]: e for e in result}

        assert by_name["exp1"]["probeSuccessChange"] == 60
        assert by_name["exp1"]["verdictChanged"] is True
        assert by_name["exp2"]["probeSuccessChange"] == 0
        assert by_name["exp2"]["verdictChanged"] is False

    def test_skips_experiments_present_only_in_after(self):
        baseline = [{"name": "exp1", "result": {"probeSuccessPercentage": 0}}]
        after = [
            {"name": "exp1", "result": {"probeSuccessPercentage": 50}},
            {"name": "newly-added", "result": {"probeSuccessPercentage": 100}},
        ]
        result = _compare_experiments(baseline, after)
        assert {e["experimentName"] for e in result} == {"exp1"}


class TestEvaluateCriteria:
    def test_required_increases_met(self):
        improvements = [
            {"probeSuccessChange": 30},
            {"probeSuccessChange": 20},
        ]
        result = _evaluate_improvement_criteria(
            score_change=25.0,
            experiment_improvements=improvements,
            criteria={"resilienceScoreIncrease": 10, "probeSuccessIncrease": 15},
        )
        assert result["resilienceScoreIncrease"]["met"] is True
        assert result["probeSuccessIncrease"]["met"] is True
        assert result["probeSuccessIncrease"]["actual"] == 25  # mean of 30 and 20

    def test_no_experiments_yields_zero_probe_change(self):
        result = _evaluate_improvement_criteria(
            score_change=0,
            experiment_improvements=[],
            criteria={"resilienceScoreIncrease": 10, "probeSuccessIncrease": 0},
        )
        assert result["probeSuccessIncrease"]["actual"] == 0
        assert result["probeSuccessIncrease"]["met"] is True


class TestDetermineFixEffectiveness:
    def test_fail_to_pass_is_effective(self):
        criteria = {
            "resilienceScoreIncrease": {"met": False},
            "probeSuccessIncrease": {"met": False},
        }
        assert _determine_fix_effectiveness("FAIL", "PASS", 0, criteria) is True

    def test_big_score_change_is_effective(self):
        criteria = {
            "resilienceScoreIncrease": {"met": False},
            "probeSuccessIncrease": {"met": False},
        }
        assert _determine_fix_effectiveness("FAIL", "FAIL", 25.0, criteria) is True

    def test_all_criteria_met_is_effective(self):
        criteria = {
            "resilienceScoreIncrease": {"met": True},
            "probeSuccessIncrease": {"met": True},
        }
        assert _determine_fix_effectiveness("FAIL", "FAIL", 5.0, criteria) is True

    def test_otherwise_not_effective(self):
        criteria = {
            "resilienceScoreIncrease": {"met": False},
            "probeSuccessIncrease": {"met": True},
        }
        assert _determine_fix_effectiveness("FAIL", "FAIL", 5.0, criteria) is False


class TestCalculateConfidence:
    def test_starts_at_baseline_05(self):
        assert _calculate_confidence(False, 0, []) == 0.5

    def test_verdict_change_adds_quarter(self):
        assert _calculate_confidence(True, 0, []) == 0.75

    def test_clamped_to_099(self):
        # Verdict change + large score + all-positive probe deltas
        improvements = [{"probeSuccessChange": 10}, {"probeSuccessChange": 20}]
        c = _calculate_confidence(True, 1000.0, improvements)
        assert c == 0.99


class TestCompareMetricsRecovery:
    def test_emits_recovery_block_when_both_present(self):
        baseline = {"recovery": {"summary": {"meanRecovery_ms": 5000, "p95Recovery_ms": 8000}}}
        after = {"recovery": {"summary": {"meanRecovery_ms": 3000, "p95Recovery_ms": 4500}}}
        result = _compare_metrics(baseline, after)
        assert result["recovery"]["meanChange_ms"] == -2000.0
        assert result["recovery"]["improved"] is True

    def test_skips_recovery_when_missing(self):
        result = _compare_metrics({}, {})
        assert "recovery" not in result

    def test_emits_latency_block_for_shared_routes(self):
        baseline = {
            "latency": {
                "phases": {
                    "during-chaos": {
                        "routes": {
                            "/cart": {"mean_ms": 800},
                            "/checkout": {"mean_ms": 1200},
                        }
                    }
                }
            }
        }
        after = {
            "latency": {
                "phases": {
                    "during-chaos": {
                        "routes": {
                            "/cart": {"mean_ms": 400},
                            "/checkout": {"mean_ms": 600},
                        }
                    }
                }
            }
        }
        result = _compare_metrics(baseline, after)
        assert result["latency"]["allImproved"] is True
        routes = {r["route"]: r for r in result["latency"]["routes"]}
        assert routes["/cart"]["change_ms"] == -400.0
        assert routes["/checkout"]["improved"] is True

    def test_resources_block_requires_availability_flag(self):
        baseline = {
            "resources": {
                "available": False,
                "phases": {"during-chaos": {"node": {"meanCpu_percent": 50}}},
            }
        }
        after = {
            "resources": {
                "available": False,
                "phases": {"during-chaos": {"node": {"meanCpu_percent": 70}}},
            }
        }
        # Without `available` truthy, resources is skipped
        assert _compare_metrics(baseline, after) == {}
