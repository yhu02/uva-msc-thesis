"""Remediation action log for ML-driven anomaly remediation.

Generates structured ``(anomaly_state, action_taken, outcome)`` tuples
from multi-strategy experiment runs.  This enables reinforcement-learning
or supervised models to learn which placement action best remediates a
given anomaly under specific conditions.
"""

from typing import Any, Dict, List


def generate_remediation_log(
    summary_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Generate remediation action logs from a multi-strategy experiment run.

    Uses the ``baseline`` strategy as the anomaly reference state and
    treats each non-baseline strategy as a remediation action.

    Parameters
    ----------
    summary_data:
        The full summary.json dict produced by ``chaosprobe run``.
        Must contain ``strategies`` with at least ``baseline`` + one other.

    Returns
    -------
    List of remediation log entries, one per non-baseline strategy.
    Each entry pairs the baseline anomaly state with the action's outcome.
    """
    strategies = summary_data.get("strategies", {})
    if not strategies:
        return []

    # Find baseline reference
    baseline = strategies.get("baseline")
    if not baseline or baseline.get("status") != "completed":
        return []

    baseline_metrics = _extract_state(baseline)

    log: List[Dict[str, Any]] = []
    for strategy_name, strategy_data in strategies.items():
        if strategy_name == "baseline":
            continue
        if strategy_data.get("status") != "completed":
            continue

        action_metrics = _extract_state(strategy_data)

        # Determine outcome
        outcome = _determine_outcome(baseline_metrics, action_metrics)

        entry: Dict[str, Any] = {
            "baselineState": baseline_metrics,
            "actionTaken": {
                "type": "placement",
                "strategy": strategy_name,
                "placement": strategy_data.get("placement", {}),
            },
            "resultState": action_metrics,
            "outcome": outcome,
        }
        log.append(entry)

    return log


def _extract_state(strategy_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract a compact state vector from strategy data."""
    experiment = strategy_data.get("experiment", {})
    metrics = strategy_data.get("metrics", {})
    recovery = metrics.get("recovery", {}).get("summary", {}) if metrics else {}

    state: Dict[str, Any] = {
        "verdict": experiment.get("overallVerdict", "UNKNOWN"),
        "resilienceScore": experiment.get(
            "resilienceScore", experiment.get("meanResilienceScore", 0)
        ),
        "meanRecovery_ms": recovery.get("meanRecovery_ms"),
        "p95Recovery_ms": recovery.get("p95Recovery_ms"),
        "maxRecovery_ms": recovery.get("maxRecovery_ms"),
    }

    # Include aggregated metrics if multi-iteration
    aggregated = strategy_data.get("aggregated")
    if aggregated:
        state["passRate"] = aggregated.get("passRate")
        state["meanResilienceScore"] = aggregated.get("meanResilienceScore")
        state["meanRecoveryTime_ms"] = aggregated.get("meanRecoveryTime_ms")

    return state


def _determine_outcome(
    baseline: Dict[str, Any],
    action: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare action state to baseline to determine remediation outcome."""
    b_score = baseline.get("resilienceScore", 0) or 0
    a_score = action.get("resilienceScore", 0) or 0
    score_delta = a_score - b_score

    b_recovery = baseline.get("meanRecovery_ms")
    a_recovery = action.get("meanRecovery_ms")
    recovery_delta = None
    if b_recovery is not None and a_recovery is not None:
        recovery_delta = round(a_recovery - b_recovery, 1)

    # Classification
    if a_score > b_score and score_delta >= 10:
        classification = "improved"
    elif a_score < b_score and score_delta <= -10:
        classification = "degraded"
    else:
        classification = "neutral"

    recovery_improved = recovery_delta is not None and recovery_delta < 0

    return {
        "classification": classification,
        "resilienceScoreDelta": round(score_delta, 1),
        "recoveryTimeDelta_ms": recovery_delta,
        "resilienceImproved": score_delta > 0,
        "recoveryImproved": recovery_improved,
    }
