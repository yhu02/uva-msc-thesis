"""``chaosprobe inspect`` — pretty-print one iteration's full detail.

When summarize / stats flag a strategy as anomalous, the next question
is always "*which* iteration was the outlier, and what happened in
it?".  ``inspect -s summary.json --strategy spread --iteration 3``
extracts a single iteration's record, prints the headline fields
(verdict, score, pre-chaos health, taint reasons, recovery split,
probe verdicts), then lists the section keys (metrics, snapshots,
cascade timeline, anomaly labels) that exist on the record so the
defender knows what's available without dumping all of it.

Use ``--json`` to get the raw record for piping into ``jq`` or other
tools.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import click


def _find_iteration(raw: Dict[str, Any], strategy: str, iteration: int) -> Optional[Dict[str, Any]]:
    """Look up one iteration by 1-based ``iteration`` field.  Returns
    None if the strategy or iteration is absent."""
    strategies = raw.get("strategies") or {}
    sdata = strategies.get(strategy)
    if sdata is None:
        return None
    for ir in sdata.get("iterations") or []:
        if ir.get("iteration") == iteration:
            return ir
    return None


def _fmt_optional(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def _probe_verdict_summary(ir: Dict[str, Any]) -> str:
    """One-line counts of probe verdicts: ``Pass=3 Fail=1 Unknown=0``."""
    verdicts = ir.get("probeVerdicts") or {}
    counts: Dict[str, int] = {}
    for v in verdicts.values():
        if isinstance(v, str):
            counts[v] = counts.get(v, 0) + 1
    if not counts:
        return "—"
    return " ".join(f"{k}={v}" for k, v in sorted(counts.items()))


_HEADLINE_KEYS = [
    ("iteration", "iteration"),
    ("verdict", "verdict"),
    ("resilienceScore", "score"),
    ("preChaosHealthy", "preChaosHealthy"),
    ("preChaosTaintReasons", "taintReasons"),
    ("unknownProbeCount", "unknownProbes"),
    ("experimentDuration_s", "experimentDuration_s"),
    ("runId", "runId"),
]

_DETAIL_KEYS = [
    "metrics",
    "podPlacements",
    "preIterationSnapshot",
    "postIterationSnapshot",
    "anomalyLabels",
    "cascadeTimeline",
]


def _format_iteration(strategy: str, ir: Dict[str, Any]) -> str:
    lines: List[str] = [f"strategy: {strategy}"]
    for key, label in _HEADLINE_KEYS:
        if key in ir:
            lines.append(f"  {label}: {_fmt_optional(ir.get(key))}")
    lines.append(f"  probeVerdicts: {_probe_verdict_summary(ir)}")

    # Recovery split if metrics.recovery is present.
    metrics = ir.get("metrics") or {}
    recovery = metrics.get("recovery") if isinstance(metrics, dict) else None
    if isinstance(recovery, dict):
        rec_t = recovery.get("recoveryTime_ms")
        d2s = recovery.get("deletionToScheduled_ms")
        s2r = recovery.get("scheduledToReady_ms")
        if rec_t is not None or d2s is not None or s2r is not None:
            lines.append(
                f"  recovery: {_fmt_optional(rec_t)} ms "
                f"(d2s={_fmt_optional(d2s)} ms, s2r={_fmt_optional(s2r)} ms)"
            )

    present = [k for k in _DETAIL_KEYS if k in ir and ir.get(k) not in (None, [], {})]
    if present:
        lines.append(f"  detail sections present: {', '.join(present)}")
        lines.append("  (use --json to dump the raw record)")
    return "\n".join(lines)


@click.command("inspect")
@click.option(
    "--summary",
    "-s",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to a summary.json.",
)
@click.option(
    "--strategy",
    required=True,
    help="Strategy name (e.g. spread, colocate, random:42).",
)
@click.option(
    "--iteration",
    "-i",
    type=int,
    required=True,
    help="1-based iteration number.",
)
@click.option(
    "--json",
    "json_out",
    is_flag=True,
    help="Dump the raw iteration record as JSON instead of the headline view.",
)
def inspect(summary: Path, strategy: str, iteration: int, json_out: bool):
    """Pretty-print one iteration's record from a summary.json.

    Headline mode shows the fields a defender usually needs first:
    verdict, score, pre-chaos health, taint reasons, probe verdict counts,
    recovery split, and which heavy detail sections exist.  ``--json``
    dumps the raw record for ``jq``/programmatic use.

    \b
    Examples:
      chaosprobe inspect -s summary.json --strategy spread -i 3
      chaosprobe inspect -s summary.json --strategy colocate -i 7 --json
    """
    raw = json.loads(summary.read_text())
    ir = _find_iteration(raw, strategy, iteration)
    if ir is None:
        raise click.ClickException(
            f"strategy {strategy!r} iteration {iteration} not found in {summary}"
        )

    if json_out:
        click.echo(json.dumps(ir, indent=2, default=str))
    else:
        click.echo(_format_iteration(strategy, ir))
