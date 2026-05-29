"""CLI command for per-strategy statistical analysis.

Reads per-strategy iteration samples from a ``summary.json`` produced by
``chaosprobe run`` and emits bootstrap confidence intervals plus pairwise
Mann-Whitney comparisons with Holm-Bonferroni correction.  The metric
under analysis is selectable via ``--metric``.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

from chaosprobe.metrics.statistics import bootstrap_ci, pairwise_comparisons

# (json path inside each iteration dict, label used in headers/JSON)
_METRIC_SPECS: Dict[str, Tuple[str, str]] = {
    "resilience": ("resilienceScore", "resilienceScore"),
    "recovery": ("metrics.recovery.summary.meanRecovery_ms", "meanRecovery_ms"),
}


def _resolve_path(d: Dict[str, Any], path: str) -> Any:
    """Walk a dotted path through nested dicts; return ``None`` if any
    hop is missing or non-dict."""
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _load_strategies(summary_path: Path, metric_path: str) -> Dict[str, List[float]]:
    """Extract ``{strategy: [sample, ...]}`` from a summary file.

    ``metric_path`` is a dotted path inside each iteration dict — e.g.
    ``"resilienceScore"`` or ``"metrics.recovery.summary.meanRecovery_ms"``.
    Iterations where the path resolves to ``None`` or a non-numeric value
    are skipped.
    """
    raw = json.loads(summary_path.read_text())
    strategies = raw.get("strategies") or {}
    out: Dict[str, List[float]] = {}
    for name, sdata in strategies.items():
        iters = sdata.get("iterations") or []
        samples: List[float] = []
        for it in iters:
            if not isinstance(it, dict):
                continue
            value = _resolve_path(it, metric_path)
            if value is None:
                continue
            try:
                samples.append(float(value))
            except (TypeError, ValueError):
                continue
        if samples:
            out[name] = samples
    return out


def _format_text(
    samples: Dict[str, List[float]],
    ci_rows: Dict[str, Dict[str, float]],
    pairwise_rows: List[Dict[str, object]],
    confidence: float,
    metric_label: str,
) -> str:
    """Render the analysis as a fixed-width text report."""
    lines: List[str] = []
    lines.append(f"Bootstrap {int(confidence * 100)}% CI for {metric_label} (mean):")
    lines.append(f"  {'strategy':<20} {'n':>3}  {'mean':>10}  {'CI low':>10}  {'CI high':>10}")
    for name in sorted(samples.keys()):
        ci = ci_rows[name]
        lines.append(
            f"  {name:<20} {ci['n']:>3}  "
            f"{ci['point']!s:>10}  {ci['ci_low']!s:>10}  {ci['ci_high']!s:>10}"
        )

    lines.append("")
    lines.append(f"Pairwise Mann-Whitney U on {metric_label} (Holm-Bonferroni adjusted):")
    if not pairwise_rows:
        lines.append("  (no pairs — need at least two strategies with samples)")
    else:
        header = (
            f"  {'a':<20} {'b':<20} {'mean_a':>10} {'mean_b':>10} "
            f"{'p_raw':>8} {'p_holm':>8}  sig"
        )
        lines.append(header)
        for row in pairwise_rows:
            sig = "✓" if row.get("significant_05") else " "
            lines.append(
                f"  {row['a']!s:<20} {row['b']!s:<20} "
                f"{row['mean_a']!s:>10} {row['mean_b']!s:>10} "
                f"{row['p_raw']!s:>8} {row.get('p_holm', '-')!s:>8}  {sig}"
            )
    return "\n".join(lines)


@click.command("stats")
@click.option(
    "--summary",
    "-s",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to a summary.json produced by `chaosprobe run`.",
)
@click.option(
    "--metric",
    "-m",
    type=click.Choice(sorted(_METRIC_SPECS.keys())),
    default="resilience",
    show_default=True,
    help="Metric to analyse: resilience (resilienceScore) or recovery (meanRecovery_ms).",
)
@click.option(
    "--confidence",
    type=float,
    default=0.95,
    show_default=True,
    help="Two-sided confidence level for bootstrap CI.",
)
@click.option(
    "--n-resamples",
    type=int,
    default=2000,
    show_default=True,
    help="Bootstrap resample count.",
)
@click.option(
    "--seed",
    type=int,
    default=42,
    show_default=True,
    help="Bootstrap RNG seed (use -1 for nondeterministic).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the full analysis as JSON instead of text.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write output to file (default: stdout).",
)
def stats(
    summary: Path,
    metric: str,
    confidence: float,
    n_resamples: int,
    seed: int,
    as_json: bool,
    output: Optional[Path],
):
    """Compute CI and pairwise significance for a per-strategy metric.

    \b
    Examples:
      chaosprobe stats -s results/20260530-142103/summary.json
      chaosprobe stats -s results/.../summary.json --metric recovery
      chaosprobe stats -s results/.../summary.json --json -o stats.json
    """
    metric_path, metric_label = _METRIC_SPECS[metric]
    samples = _load_strategies(summary, metric_path)
    if not samples:
        click.echo(f"Error: no strategies with {metric_label} found.", err=True)
        sys.exit(1)

    actual_seed = None if seed == -1 else seed
    ci_rows = {
        name: bootstrap_ci(
            values,
            statistic="mean",
            confidence=confidence,
            n_resamples=n_resamples,
            seed=actual_seed,
        )
        for name, values in samples.items()
    }
    pairwise_rows = pairwise_comparisons(samples, holm_bonferroni=True) if len(samples) >= 2 else []

    if as_json:
        payload = {
            "source": str(summary),
            "metric": metric_label,
            "confidence": confidence,
            "n_resamples": n_resamples,
            "ci": ci_rows,
            "pairwise": pairwise_rows,
        }
        rendered = json.dumps(payload, indent=2)
    else:
        rendered = _format_text(samples, ci_rows, pairwise_rows, confidence, metric_label)

    if output:
        output.write_text(rendered + "\n")
        click.echo(f"Wrote {output}")
    else:
        click.echo(rendered)
