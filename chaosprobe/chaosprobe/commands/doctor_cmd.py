"""CLI command for data-quality issues in a summary.json.

Scans the per-strategy aggregates and per-iteration fields produced by
``chaosprobe run`` and surfaces anything that should be addressed
before the analysis is defended: tainted iterations, scheduler-overridden
placements, OOMKills, error iterations, missing recovery data.

Designed for a defender who wants to spot issues *before* the committee
asks "but what about iteration 3 of colocate?".
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import click


def _check_strategy(strategy_name: str, sdata: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Run every check against one strategy block.  Returns a list of
    ``(severity, message)`` tuples; severity is ``warn`` or ``error``."""
    issues: List[Tuple[str, str]] = []
    agg = sdata.get("aggregated") or {}
    iters = sdata.get("iterations") or []
    n_iters = len(iters)

    # Tainted iterations (pre-chaos baseline already degraded).
    tainted = agg.get("taintedIterations") or 0
    if tainted:
        reasons = agg.get("taintReasonCounts") or {}
        reason_str = (
            " (" + ", ".join(f"{k}={v}" for k, v in reasons.items()) + ")" if reasons else ""
        )
        issues.append(
            (
                "warn",
                f"{tainted}/{n_iters} iteration(s) tainted{reason_str}",
            )
        )
    if agg.get("allIterationsTainted"):
        issues.append(("error", "every iteration was tainted — results not usable"))

    # Error iterations (infra failure, all-Unknown probes, exceptions).
    errors = agg.get("errors") or 0
    if errors:
        issues.append(("warn", f"{errors}/{n_iters} iteration(s) errored"))

    # Placement match rate < 1.0 means scheduler overrode our intent.
    placement = sdata.get("placement") or {}
    diff = (placement.get("metadata") or {}).get("intendedActualDiff") or {}
    match_rate = diff.get("matchRate")
    if match_rate is not None and match_rate < 1.0:
        mismatched = len(diff.get("mismatched") or [])
        issues.append(
            (
                "warn" if match_rate >= 0.8 else "error",
                f"placement match rate {match_rate:.2f} "
                f"({mismatched} deployment(s) scheduled elsewhere)",
            )
        )

    # OOMKills indicate the strategy hit cgroup memory limits — confounds
    # the resilience signal with raw OOM behaviour.
    total_oom = agg.get("totalOOMKills") or 0
    if total_oom:
        iters_with_oom = agg.get("iterationsWithOOMKills") or 0
        issues.append(
            (
                "warn",
                f"{total_oom} OOMKill(s) across {iters_with_oom} iteration(s) "
                f"— resilience score includes self-OOM behaviour",
            )
        )

    # Node pressure conditions firing during the run.
    pressure = agg.get("nodePressureEvents") or {}
    fired = {
        cond: data
        for cond, data in pressure.items()
        if isinstance(data, dict) and data.get("iterationsWithEvent", 0) > 0
    }
    if fired:
        summary = ", ".join(
            f"{cond} on {data['iterationsWithEvent']}/{n_iters} iter"
            for cond, data in fired.items()
        )
        issues.append(("warn", f"node pressure conditions fired: {summary}"))

    # Missing recovery data — analyses on this strategy will have gaps.
    if n_iters > 0 and agg.get("meanRecoveryTime_ms") is None:
        issues.append(
            (
                "warn",
                "no recovery times collected — recovery-based stats unavailable",
            )
        )

    # Very small sample for CI.
    if n_iters < 3:
        issues.append(
            (
                "warn",
                f"only {n_iters} iteration(s) — CI / Mann-Whitney unreliable below n=3",
            )
        )

    return issues


@click.command("doctor")
@click.option(
    "--summary",
    "-s",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to a summary.json produced by `chaosprobe run`.",
)
@click.option(
    "--strict",
    is_flag=True,
    help="Exit non-zero on any warn-level issue (not just errors).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit findings as JSON instead of a human-readable report.",
)
def doctor(summary: Path, strict: bool, as_json: bool):
    """Report data-quality issues in a summary.json.

    \b
    Examples:
      chaosprobe doctor -s results/20260530-142103/summary.json
      chaosprobe doctor -s summary.json --strict
      chaosprobe doctor -s summary.json --json
    """
    raw = json.loads(summary.read_text())
    strategies = raw.get("strategies") or {}

    report: Dict[str, List[Dict[str, str]]] = {}
    error_count = 0
    warn_count = 0
    for name in sorted(strategies.keys()):
        issues = _check_strategy(name, strategies[name])
        if issues:
            report[name] = [{"severity": sev, "message": msg} for sev, msg in issues]
            for sev, _ in issues:
                if sev == "error":
                    error_count += 1
                elif sev == "warn":
                    warn_count += 1

    if as_json:
        click.echo(
            json.dumps(
                {
                    "source": str(summary),
                    "strategiesChecked": len(strategies),
                    "errorCount": error_count,
                    "warnCount": warn_count,
                    "findings": report,
                },
                indent=2,
            )
        )
    else:
        if not report:
            click.echo(f"  ✓ no issues across {len(strategies)} strategies")
        else:
            for name, findings in report.items():
                click.echo(f"\n  {name}")
                for finding in findings:
                    marker = "✗" if finding["severity"] == "error" else "!"
                    click.echo(f"    {marker} {finding['message']}")
            click.echo("")
            click.echo(f"  summary: {error_count} error(s), {warn_count} warning(s)")

    if error_count > 0 or (strict and warn_count > 0):
        sys.exit(1)
