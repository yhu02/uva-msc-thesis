"""Tests for ``_build_comparison_table_impl`` — the cross-strategy comparison
table that is both printed and written to summary.json's ``comparison`` view.

The builder selects healthy-only vs overall mean under taint and must render
all-ERROR strategies without crashing (their score stats are ``None``). Both are
research-validity-critical, yet the function previously had no direct test
(REVIEW.md W4) — and the taint-mean selection here duplicates the rule in
``output/charts.py:_normalize_strategy``, so pinning both to the same cases keeps
them from silently diverging.
"""

from chaosprobe.orchestrator.run_phases import _build_comparison_table_impl


def _multi(aggregated, status="completed"):
    """A multi-iteration strategy result (drives the ``iterations > 1`` path)."""
    return {"status": status, "aggregated": aggregated}


class TestBuildComparisonTable:
    def test_healthy_uses_overall_mean(self):
        strategies = {
            "spread": _multi(
                {
                    "passRate": 1.0,
                    "meanResilienceScore": 82.0,
                    "stddevResilienceScore": 3.0,
                    "minResilienceScore": 79.0,
                    "maxResilienceScore": 85.0,
                    "taintedIterations": 0,
                }
            )
        }
        row = _build_comparison_table_impl(strategies, iterations=3)[0]
        assert row["strategy"] == "spread"
        assert row["verdict"] == "PASS"
        assert row["resilienceScore"] == 82.0
        assert row["stddevScore"] == 3.0
        assert row["scoreRange"] == "79-85"

    def test_partial_taint_uses_healthy_only_mean(self):
        strategies = {
            "colocate": _multi(
                {
                    "passRate": 1.0,
                    "meanResilienceScore": 40.0,
                    "stddevResilienceScore": 20.0,
                    "meanResilienceScore_healthyOnly": 75.0,
                    "stddevResilienceScore_healthyOnly": 5.0,
                    "taintedIterations": 1,
                    "allIterationsTainted": False,
                }
            )
        }
        row = _build_comparison_table_impl(strategies, iterations=3)[0]
        assert row["resilienceScore"] == 75.0
        assert row["stddevScore"] == 5.0

    def test_all_tainted_falls_back_to_overall_mean(self):
        strategies = {
            "colocate": _multi(
                {
                    "passRate": 0.0,
                    "meanResilienceScore": 30.0,
                    "stddevResilienceScore": 10.0,
                    "taintedIterations": 3,
                    "allIterationsTainted": True,
                }
            )
        }
        row = _build_comparison_table_impl(strategies, iterations=3)[0]
        assert row["resilienceScore"] == 30.0
        assert row["verdict"] == "FAIL"

    def test_all_error_completed_strategy_renders_zero_not_none(self):
        # Every iteration errored: aggregate_iterations leaves the score stats
        # None, but the strategy status stays "completed", so the line-754
        # early-out is skipped.  The row must render 0.0 (not None) so the
        # downstream ``:.1f`` summary formatting cannot raise TypeError (W3).
        strategies = {
            "default": _multi(
                {
                    "passRate": 0.0,
                    "meanResilienceScore": None,
                    "stddevResilienceScore": None,
                    "minResilienceScore": None,
                    "maxResilienceScore": None,
                    "allIterationsError": True,
                    "taintedIterations": 0,
                }
            )
        }
        row = _build_comparison_table_impl(strategies, iterations=3)[0]
        assert row["resilienceScore"] == 0.0
        assert row["stddevScore"] == 0.0
        # The original bug raised here; assert it now formats cleanly.
        assert f"{row['resilienceScore']:.1f}" == "0.0"

    def test_error_status_strategy_short_circuits(self):
        row = _build_comparison_table_impl({"spread": {"status": "error"}}, iterations=3)[0]
        assert row["verdict"] == "ERROR"
        assert row["resilienceScore"] == 0.0

    def test_single_iteration_uses_experiment(self):
        strategies = {
            "spread": {
                "status": "completed",
                "experiment": {"overallVerdict": "PASS", "resilienceScore": 88.0},
                "metrics": {
                    "recovery": {"summary": {"meanRecovery_ms": 1200.0, "maxRecovery_ms": 1500.0}}
                },
            }
        }
        row = _build_comparison_table_impl(strategies, iterations=1)[0]
        assert row["verdict"] == "PASS"
        assert row["resilienceScore"] == 88.0
        assert row["avgRecovery_ms"] == 1200.0
        assert row["maxRecovery_ms"] == 1500.0
