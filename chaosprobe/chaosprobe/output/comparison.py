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
    comparison_id = f"compare-{now.strftime('%Y-%m-%d-%H%M%S')}-" f"{uuid.uuid4().hex[:6]}"
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

    # Compare metrics (recovery, latency, throughput, resources)
    metrics_comparison = _compare_metrics(
        baseline.get("metrics", {}),
        after_fix.get("metrics", {}),
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
    confidence = _calculate_confidence(verdict_changed, score_change, experiment_improvements)

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
                "experiments": _summarize_experiments(baseline.get("experiments", [])),
            },
        },
        "afterFix": {
            "runId": after_fix.get("runId", ""),
            "timestamp": after_fix.get("timestamp", ""),
            "results": {
                "resilienceScore": afterfix_score,
                "overallVerdict": afterfix_verdict,
                "experiments": _summarize_experiments(after_fix.get("experiments", [])),
            },
        },
        "comparison": {
            "resilienceScoreChange": score_change,
            "verdictChanged": verdict_changed,
            "previousVerdict": baseline_verdict,
            "newVerdict": afterfix_verdict,
            "experimentImprovements": experiment_improvements,
            "improvementCriteriaMet": criteria_met,
            "metrics": metrics_comparison,
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


def _compare_metrics(
    baseline_metrics: Dict[str, Any],
    afterfix_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare metrics between baseline and after-fix runs.

    Compares recovery times, latency, throughput, and resource utilization
    when data is present in both runs.
    """
    result: Dict[str, Any] = {}

    # Recovery time comparison
    b_rec = baseline_metrics.get("recovery", {}).get("summary", {})
    a_rec = afterfix_metrics.get("recovery", {}).get("summary", {})
    if b_rec.get("meanRecovery_ms") is not None and a_rec.get("meanRecovery_ms") is not None:
        b_mean = b_rec["meanRecovery_ms"]
        a_mean = a_rec["meanRecovery_ms"]
        result["recovery"] = {
            "baseline": {
                "meanRecovery_ms": b_mean,
                "p95Recovery_ms": b_rec.get("p95Recovery_ms"),
            },
            "afterFix": {
                "meanRecovery_ms": a_mean,
                "p95Recovery_ms": a_rec.get("p95Recovery_ms"),
            },
            "meanChange_ms": round(a_mean - b_mean, 1),
            "improved": a_mean < b_mean,
        }

    # Latency comparison (during-chaos phase)
    b_lat = baseline_metrics.get("latency", {})
    a_lat = afterfix_metrics.get("latency", {})
    if b_lat and a_lat:
        b_during = b_lat.get("phases", {}).get("during-chaos", {}).get("routes", {})
        a_during = a_lat.get("phases", {}).get("during-chaos", {}).get("routes", {})
        if b_during and a_during:
            route_changes = []
            for route in set(b_during) & set(a_during):
                b_mean = b_during[route].get("mean_ms")
                a_mean = a_during[route].get("mean_ms")
                if b_mean is not None and a_mean is not None:
                    route_changes.append(
                        {
                            "route": route,
                            "baseline_ms": round(b_mean, 1),
                            "afterFix_ms": round(a_mean, 1),
                            "change_ms": round(a_mean - b_mean, 1),
                            "improved": a_mean < b_mean,
                        }
                    )
            if route_changes:
                result["latency"] = {
                    "routes": route_changes,
                    "allImproved": all(r["improved"] for r in route_changes),
                }

    # Throughput comparison (during-chaos phase)
    for target in ("redis", "disk"):
        b_tp = baseline_metrics.get(target, {})
        a_tp = afterfix_metrics.get(target, {})
        if b_tp and a_tp:
            b_during = b_tp.get("phases", {}).get("during-chaos", {}).get(target, {})
            a_during = a_tp.get("phases", {}).get("during-chaos", {}).get(target, {})
            if b_during and a_during:
                op_changes = []
                for op in set(b_during) & set(a_during):
                    b_ops = b_during[op].get("meanOpsPerSecond")
                    a_ops = a_during[op].get("meanOpsPerSecond")
                    if b_ops is not None and a_ops is not None:
                        op_changes.append(
                            {
                                "operation": op,
                                "baseline_ops": round(b_ops, 1),
                                "afterFix_ops": round(a_ops, 1),
                                "change_ops": round(a_ops - b_ops, 1),
                                "improved": a_ops > b_ops,
                            }
                        )
                if op_changes:
                    result[target] = {
                        "operations": op_changes,
                        "allImproved": all(o["improved"] for o in op_changes),
                    }

    # Resource utilization comparison (during-chaos phase)
    b_res = baseline_metrics.get("resources", {})
    a_res = afterfix_metrics.get("resources", {})
    if b_res.get("available") and a_res.get("available"):
        b_node = b_res.get("phases", {}).get("during-chaos", {}).get("node", {})
        a_node = a_res.get("phases", {}).get("during-chaos", {}).get("node", {})
        if b_node and a_node:
            b_cpu = b_node.get("meanCpu_percent")
            a_cpu = a_node.get("meanCpu_percent")
            b_mem = b_node.get("meanMemory_percent")
            a_mem = a_node.get("meanMemory_percent")
            if b_cpu is not None and a_cpu is not None:
                result["resources"] = {
                    "baseline": {
                        "meanCpu_percent": round(b_cpu, 1),
                        "meanMemory_percent": round(b_mem, 1) if b_mem is not None else None,
                    },
                    "afterFix": {
                        "meanCpu_percent": round(a_cpu, 1),
                        "meanMemory_percent": round(a_mem, 1) if a_mem is not None else None,
                    },
                    "cpuChange_percent": round(a_cpu - b_cpu, 1),
                    "memoryChange_percent": (
                        round(a_mem - b_mem, 1)
                        if (b_mem is not None and a_mem is not None)
                        else None
                    ),
                }

    return result


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

        baseline_probe = baseline_exp.get("result", {}).get("probeSuccessPercentage", 0)
        afterfix_probe = afterfix_exp.get("result", {}).get("probeSuccessPercentage", 0)

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
    avg_probe_change = sum(probe_changes) / len(probe_changes) if probe_changes else 0

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
            "probeSuccessPercentage": e.get("result", {}).get("probeSuccessPercentage", 0),
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
