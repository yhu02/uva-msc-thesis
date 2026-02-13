"""Before/after comparison for ChaosProbe results.

Compares two run outputs (before and after a manifest fix)
to evaluate whether the fix improved resilience.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def compare_runs(
    baseline: Dict[str, Any],
    after_fix: Dict[str, Any],
    improvement_criteria: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compare two run results to evaluate improvement.

    Args:
        baseline: First run output (before fix).
        after_fix: Second run output (after fix).
        improvement_criteria: Optional custom improvement criteria.

    Returns:
        Comparison output dictionary.
    """
    if improvement_criteria is None:
        improvement_criteria = {
            "resilienceScoreIncrease": 10,
            "probeSuccessIncrease": 15,
        }

    now = datetime.now(timezone.utc)
    comparison_id = (
        f"compare-{now.strftime('%Y-%m-%d-%H%M%S')}-"
        f"{uuid.uuid4().hex[:6]}"
    )
    timestamp = now.isoformat()

    # Extract key metrics
    baseline_score = baseline.get("summary", {}).get("resilienceScore", 0)
    afterfix_score = after_fix.get("summary", {}).get("resilienceScore", 0)
    score_change = afterfix_score - baseline_score

    baseline_verdict = baseline.get("summary", {}).get("overallVerdict", "FAIL")
    afterfix_verdict = after_fix.get("summary", {}).get("overallVerdict", "PASS")
    verdict_changed = baseline_verdict != afterfix_verdict

    # Compare individual experiments
    experiment_improvements = _compare_experiments(
        baseline.get("experiments", []),
        after_fix.get("experiments", []),
    )

    # Evaluate improvement criteria
    criteria_met = _evaluate_improvement_criteria(
        score_change, experiment_improvements, improvement_criteria
    )

    # Determine if fix was effective
    fix_effective = _determine_fix_effectiveness(
        baseline_verdict, afterfix_verdict, score_change, criteria_met
    )

    # Calculate confidence
    confidence = _calculate_confidence(
        verdict_changed, score_change, experiment_improvements
    )

    return {
        "schemaVersion": "2.0.0",
        "comparisonId": comparison_id,
        "timestamp": timestamp,
        "scenario": baseline.get("scenario", {}),
        "baseline": {
            "runId": baseline.get("runId", ""),
            "timestamp": baseline.get("timestamp", ""),
            "results": {
                "resilienceScore": baseline_score,
                "overallVerdict": baseline_verdict,
                "experiments": _summarize_experiments(
                    baseline.get("experiments", [])
                ),
            },
        },
        "afterFix": {
            "runId": after_fix.get("runId", ""),
            "timestamp": after_fix.get("timestamp", ""),
            "results": {
                "resilienceScore": afterfix_score,
                "overallVerdict": afterfix_verdict,
                "experiments": _summarize_experiments(
                    after_fix.get("experiments", [])
                ),
            },
        },
        "comparison": {
            "resilienceScoreChange": score_change,
            "verdictChanged": verdict_changed,
            "previousVerdict": baseline_verdict,
            "newVerdict": afterfix_verdict,
            "experimentImprovements": experiment_improvements,
            "improvementCriteriaMet": criteria_met,
        },
        "conclusion": {
            "fixEffective": fix_effective,
            "confidence": confidence,
            "summary": _generate_summary_text(
                fix_effective,
                baseline_verdict,
                afterfix_verdict,
                baseline_score,
                afterfix_score,
            ),
        },
    }


# ── Experiment comparison ─────────────────────────────────────


def _compare_experiments(
    baseline_exps: List[Dict[str, Any]],
    afterfix_exps: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Compare individual experiments between runs."""
    improvements = []

    baseline_by_name = {e["name"]: e for e in baseline_exps}
    afterfix_by_name = {e["name"]: e for e in afterfix_exps}

    for name, baseline_exp in baseline_by_name.items():
        afterfix_exp = afterfix_by_name.get(name)
        if not afterfix_exp:
            continue

        baseline_probe = baseline_exp.get("result", {}).get(
            "probeSuccessPercentage", 0
        )
        afterfix_probe = afterfix_exp.get("result", {}).get(
            "probeSuccessPercentage", 0
        )

        baseline_verdict = baseline_exp.get("result", {}).get("verdict", "Awaited")
        afterfix_verdict = afterfix_exp.get("result", {}).get("verdict", "Awaited")

        improvements.append(
            {
                "experimentName": name,
                "probeSuccessChange": afterfix_probe - baseline_probe,
                "verdictChanged": baseline_verdict != afterfix_verdict,
                "previousVerdict": baseline_verdict,
                "newVerdict": afterfix_verdict,
            }
        )

    return improvements



# ── Criteria evaluation ──────────────────────────────────────


def _evaluate_improvement_criteria(
    score_change: float,
    experiment_improvements: List[Dict[str, Any]],
    criteria: Dict[str, Any],
) -> Dict[str, Any]:
    """Evaluate if improvement criteria are met."""
    required_score_increase = criteria.get("resilienceScoreIncrease", 10)
    required_probe_increase = criteria.get("probeSuccessIncrease", 15)

    probe_changes = [e["probeSuccessChange"] for e in experiment_improvements]
    avg_probe_change = (
        sum(probe_changes) / len(probe_changes) if probe_changes else 0
    )

    return {
        "resilienceScoreIncrease": {
            "required": required_score_increase,
            "actual": score_change,
            "met": score_change >= required_score_increase,
        },
        "probeSuccessIncrease": {
            "required": required_probe_increase,
            "actual": avg_probe_change,
            "met": avg_probe_change >= required_probe_increase,
        },
    }


def _determine_fix_effectiveness(
    baseline_verdict: str,
    afterfix_verdict: str,
    score_change: float,
    criteria_met: Dict[str, Any],
) -> bool:
    """Determine if the fix was effective."""
    if baseline_verdict == "FAIL" and afterfix_verdict == "PASS":
        return True
    if score_change >= 20:
        return True
    if all(c["met"] for c in criteria_met.values()):
        return True
    return False


def _calculate_confidence(
    verdict_changed: bool,
    score_change: float,
    experiment_improvements: List[Dict[str, Any]],
) -> float:
    """Calculate confidence in the fix effectiveness determination."""
    confidence = 0.5

    if verdict_changed:
        confidence += 0.25
    if score_change > 0:
        confidence += min(0.15, score_change / 100)

    improvements = [e["probeSuccessChange"] for e in experiment_improvements]
    if improvements and all(i > 0 for i in improvements):
        confidence += 0.10

    return min(0.99, round(confidence, 2))


# ── Helpers ───────────────────────────────────────────────────


def _summarize_experiments(
    experiments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Summarize experiments for comparison output."""
    return [
        {
            "name": e["name"],
            "verdict": e.get("result", {}).get("verdict", "Awaited"),
            "probeSuccessPercentage": e.get("result", {}).get(
                "probeSuccessPercentage", 0
            ),
        }
        for e in experiments
    ]


def _generate_summary_text(
    fix_effective: bool,
    baseline_verdict: str,
    afterfix_verdict: str,
    baseline_score: float,
    afterfix_score: float,
) -> str:
    """Generate a human-readable summary of the comparison."""
    if fix_effective:
        return (
            f"The applied fix successfully improved resilience. "
            f"Score: {baseline_score:.1f}% → {afterfix_score:.1f}%, "
            f"verdict: {baseline_verdict} → {afterfix_verdict}."
        )
    else:
        return (
            f"The applied fix did not fully resolve the resilience issue. "
            f"Score: {baseline_score:.1f}% → {afterfix_score:.1f}%, "
            f"verdict remains {afterfix_verdict}. Additional fixes may be required."
        )
