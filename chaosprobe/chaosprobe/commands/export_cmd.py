"""CLI command for per-iteration flat-CSV export.

``chaosprobe stats`` and ``summarize`` consume the *aggregated*
per-strategy block.  For external analysis (R, SPSS, pandas, custom
notebooks) it's often more convenient to have one row per
``(strategy, iteration)`` with the headline per-iteration metrics —
resilience score, recovery time, recovery split, error rate, etc.

``chaosprobe export -s summary.json -o data.csv`` produces exactly that.
"""

import csv
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

# Dotted-path → CSV column name.  Order here is preserved in output.
_ITERATION_FIELDS: List[tuple] = [
    ("resilienceScore", "resilience_score"),
    ("verdict", "verdict"),
    ("preChaosHealthy", "pre_chaos_healthy"),
    ("experimentDuration_s", "experiment_duration_s"),
    ("metrics.recovery.summary.meanRecovery_ms", "mean_recovery_ms"),
    ("metrics.recovery.summary.medianRecovery_ms", "median_recovery_ms"),
    ("metrics.recovery.summary.maxRecovery_ms", "max_recovery_ms"),
    ("metrics.recovery.summary.p95Recovery_ms", "p95_recovery_ms"),
    (
        "metrics.recovery.summary.meanDeletionToScheduled_ms",
        "mean_deletion_to_scheduled_ms",
    ),
    (
        "metrics.recovery.summary.meanScheduledToReady_ms",
        "mean_scheduled_to_ready_ms",
    ),
    ("metrics.podStatus.totalOOMKills", "total_oom_kills"),
    ("metrics.podStatus.totalRestarts", "total_restarts"),
    ("loadGeneration.stats.requestsPerSecond", "rps"),
    ("loadGeneration.stats.errorRate", "error_rate"),
    ("loadGeneration.stats.p95ResponseTime_ms", "p95_response_time_ms"),
]


def _resolve_path(d: Dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _iteration_row(strategy: str, idx: int, it: Dict[str, Any]) -> Dict[str, Any]:
    """Build one CSV row from an iteration dict."""
    row: Dict[str, Any] = {"strategy": strategy, "iteration": idx}
    for path, col in _ITERATION_FIELDS:
        val = _resolve_path(it, path)
        # Verdict and pre_chaos_healthy are bool/str — leave as is.  Other
        # fields may be int/float; cast None → empty string so downstream
        # CSV consumers can distinguish "no data" from "zero".
        row[col] = "" if val is None else val
    return row


def _format_csv(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return ""
    fieldnames = ["strategy", "iteration"] + [col for _, col in _ITERATION_FIELDS]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().rstrip("\n")


@click.command("export")
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
    help="Only export iterations for this strategy.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write CSV to file (default: stdout).",
)
def export(summary: Path, strategy: Optional[str], output: Optional[Path]):
    """Export per-iteration metrics as a flat CSV.

    One row per ``(strategy, iteration)`` with the headline iteration
    fields (resilience score, recovery times, recovery split, OOM /
    restart counts, Locust RPS / error rate).  Convenient for external
    analysis in R, SPSS, pandas, or a thesis appendix.

    \b
    Examples:
      chaosprobe export -s summary.json -o iterations.csv
      chaosprobe export -s summary.json --strategy colocate -o colocate.csv
    """
    raw = json.loads(summary.read_text())
    strategies = raw.get("strategies") or {}

    if strategy and strategy not in strategies:
        click.echo(
            f"Error: strategy '{strategy}' not in summary. "
            f"Available: {', '.join(sorted(strategies.keys()))}",
            err=True,
        )
        raise click.exceptions.Exit(code=1)

    targets = [strategy] if strategy else sorted(strategies.keys())
    rows: List[Dict[str, Any]] = []
    for name in targets:
        sdata = strategies[name]
        iters = sdata.get("iterations") or []
        for idx, it in enumerate(iters, 1):
            if isinstance(it, dict):
                rows.append(_iteration_row(name, idx, it))

    if not rows:
        click.echo("Error: no iterations found to export.", err=True)
        raise click.exceptions.Exit(code=1)

    rendered = _format_csv(rows)
    if output:
        output.write_text(rendered + "\n")
        click.echo(f"Wrote {len(rows)} row(s) to {output}")
    else:
        click.echo(rendered)
