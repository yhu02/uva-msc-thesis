"""CLI command for pretty-printing the per-strategy aggregate block.

``aggregate_iterations`` produces a large per-strategy summary —
``meanResilienceScore`` + CI, recovery split + CIs, CV, histogram, OOM /
restart counts, scheduler event counts, node-pressure events, taint
reason counts, load aggregates, route view aggregates.  Most of this is
useful for defence but lives nested inside ``summary.json`` and is
painful to inspect without ``jq`` or a custom script.

``chaosprobe summarize`` is the read-only view: pick the strategy you
want, pick the sections you care about, get human-readable output.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import click


def _fmt_num(v: Any, decimals: int = 1) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:.{decimals}f}"
    return str(v)


def _fmt_ci(ci: Optional[Dict[str, Any]]) -> str:
    if not isinstance(ci, dict):
        return "—"
    low = ci.get("low")
    high = ci.get("high")
    n = ci.get("n")
    if low is None or high is None:
        return "—"
    return f"[{_fmt_num(low)}, {_fmt_num(high)}] (n={n})"


def _render_strategy(name: str, sdata: Dict[str, Any]) -> List[str]:
    """Build the per-strategy block.  Sections are emitted only when
    the corresponding aggregate fields are present."""
    agg = sdata.get("aggregated") or {}
    iters = sdata.get("iterations") or []
    lines: List[str] = []
    lines.append(f"## {name}")
    lines.append(f"  iterations: {len(iters)}")

    # Resilience score.
    if "meanResilienceScore" in agg:
        line = f"  resilience: mean={_fmt_num(agg['meanResilienceScore'])}"
        line += f", stddev={_fmt_num(agg.get('stddevResilienceScore'))}"
        line += f", p25={_fmt_num(agg.get('p25ResilienceScore'))}"
        line += f", harmonic={_fmt_num(agg.get('harmonicMeanResilienceScore'))}"
        lines.append(line)
        lines.append(f"    95% CI: {_fmt_ci(agg.get('meanResilienceScore_ci95'))}")

    # Recovery time.
    if agg.get("meanRecoveryTime_ms") is not None:
        line = f"  recovery: mean={_fmt_num(agg['meanRecoveryTime_ms'])}ms"
        line += f", stddev={_fmt_num(agg.get('stddevRecoveryTime_ms'))}ms"
        line += f", median={_fmt_num(agg.get('medianRecoveryTime_ms'))}ms"
        line += f", max={_fmt_num(agg.get('maxRecoveryTime_ms'))}ms"
        line += f", p95={_fmt_num(agg.get('p95RecoveryTime_ms'))}ms"
        lines.append(line)
        if agg.get("recoveryTimeCV") is not None:
            lines.append(f"    CV: {_fmt_num(agg['recoveryTimeCV'], 3)}")
        lines.append(f"    95% CI: {_fmt_ci(agg.get('meanRecoveryTime_ms_ci95'))}")

    # Recovery split.
    if agg.get("meanDeletionToScheduled_ms") is not None:
        lines.append(
            f"  d2s (deletion→scheduled): "
            f"mean={_fmt_num(agg['meanDeletionToScheduled_ms'])}ms, "
            f"CV={_fmt_num(agg.get('deletionToScheduledCV'), 3)}, "
            f"CI={_fmt_ci(agg.get('meanDeletionToScheduled_ms_ci95'))}"
        )
    if agg.get("meanScheduledToReady_ms") is not None:
        lines.append(
            f"  s2r (scheduled→ready):     "
            f"mean={_fmt_num(agg['meanScheduledToReady_ms'])}ms, "
            f"CV={_fmt_num(agg.get('scheduledToReadyCV'), 3)}, "
            f"CI={_fmt_ci(agg.get('meanScheduledToReady_ms_ci95'))}"
        )

    # Recovery histogram.
    hist = agg.get("recoveryTimeHistogram_ms")
    if isinstance(hist, dict) and any(v > 0 for v in hist.values()):
        lines.append("  recovery histogram (per iteration):")
        max_count = max(hist.values()) or 1
        for label, count in hist.items():
            bar = "█" * int((count / max_count) * 20)
            lines.append(f"    {label:<18} {count:>3}  {bar}")

    # Load generation.
    load_agg = agg.get("loadGenerationAggregate") or {}
    if load_agg:
        line = "  load:"
        if "meanRequestsPerSecond" in load_agg:
            line += f" rps={_fmt_num(load_agg['meanRequestsPerSecond'], 2)}"
        if "meanErrorRate" in load_agg:
            line += f", errorRate={_fmt_num(load_agg['meanErrorRate'], 4)}"
        if "meanResponseTime_ms" in load_agg:
            line += f", responseTime={_fmt_num(load_agg['meanResponseTime_ms'])}ms"
        lines.append(line)

    failure_classes = agg.get("loadFailureClasses") or []
    if failure_classes:
        lines.append("  load failures (top by occurrences):")
        for cls in failure_classes[:5]:
            err = cls.get("error", "")[:40]
            name_ = cls.get("name", "")[:20]
            occ = cls.get("totalOccurrences", 0)
            obs = cls.get("iterationsObserved", 0)
            lines.append(f"    {err:<40} {name_:<20} total={occ} iters={obs}")

    # Scheduler events.
    sched = agg.get("schedulerEventCounts") or {}
    if sched:
        lines.append("  scheduler events:")
        for reason in sorted(sched.keys()):
            d = sched[reason]
            lines.append(
                f"    {reason:<20} total={d.get('total', 0)} "
                f"mean/iter={d.get('meanPerIteration', 0)} "
                f"max/iter={d.get('maxPerIteration', 0)} "
                f"(in {d.get('iterationsObserved', 0)} iter)"
            )

    # OOMKills / restarts.
    if agg.get("totalOOMKills") or agg.get("totalRestarts"):
        if agg.get("totalOOMKills"):
            lines.append(
                f"  OOMKills: total={agg['totalOOMKills']} "
                f"mean/iter={_fmt_num(agg.get('meanOOMKillsPerIteration'), 2)} "
                f"in {agg.get('iterationsWithOOMKills', 0)} iter"
            )
        if agg.get("totalRestarts"):
            lines.append(
                f"  restarts: total={agg['totalRestarts']} "
                f"mean/iter={_fmt_num(agg.get('meanRestartsPerIteration'), 2)} "
                f"in {agg.get('iterationsWithRestarts', 0)} iter"
            )

    # Node pressure.
    pressure = agg.get("nodePressureEvents") or {}
    fired = {
        c: d
        for c, d in pressure.items()
        if isinstance(d, dict) and d.get("iterationsWithEvent", 0) > 0
    }
    if fired:
        lines.append("  node pressure:")
        for cond, d in fired.items():
            lines.append(
                f"    {cond:<20} iter={d.get('iterationsWithEvent', 0)} "
                f"total_events={d.get('totalNodeEvents', 0)}"
            )

    # Tainted iterations.
    if agg.get("taintedIterations"):
        reasons = agg.get("taintReasonCounts") or {}
        lines.append(
            f"  tainted: {agg['taintedIterations']}/{len(iters)} iter"
            + (" (" + ", ".join(f"{k}={v}" for k, v in reasons.items()) + ")" if reasons else "")
        )

    # Experiment duration.
    if agg.get("meanExperimentDuration_s") is not None:
        line = f"  experimentDuration: mean={_fmt_num(agg['meanExperimentDuration_s'])}s"
        if "stddevExperimentDuration_s" in agg:
            line += f", stddev={_fmt_num(agg['stddevExperimentDuration_s'])}s"
        lines.append(line)

    lines.append("")
    return lines


@click.command("summarize")
@click.option(
    "--summary",
    "-s",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to a summary.json produced by `chaosprobe run`.",
)
@click.option(
    "--strategy",
    default=None,
    help="Only render this strategy (default: all present).",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write rendered text to file (default: stdout).",
)
def summarize(summary: Path, strategy: Optional[str], output: Optional[Path]):
    """Pretty-print the per-strategy aggregate block from summary.json.

    \b
    Examples:
      chaosprobe summarize -s results/20260530-142103/summary.json
      chaosprobe summarize -s summary.json --strategy colocate
    """
    raw = json.loads(summary.read_text())
    strategies = raw.get("strategies") or {}
    if not strategies:
        click.echo("Error: summary has no strategies block.", err=True)
        raise click.exceptions.Exit(code=1)

    if strategy and strategy not in strategies:
        click.echo(
            f"Error: strategy '{strategy}' not in summary. "
            f"Available: {', '.join(sorted(strategies.keys()))}",
            err=True,
        )
        raise click.exceptions.Exit(code=1)

    targets = [strategy] if strategy else sorted(strategies.keys())
    lines: List[str] = []
    for name in targets:
        lines.extend(_render_strategy(name, strategies[name]))

    rendered = "\n".join(lines).rstrip("\n")
    if output:
        output.write_text(rendered + "\n")
        click.echo(f"Wrote {output}")
    else:
        click.echo(rendered)
