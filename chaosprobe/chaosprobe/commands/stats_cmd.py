"""CLI command for resilience-score statistical analysis.

Reads per-strategy iteration scores from a ``summary.json`` produced by
``chaosprobe run`` and emits bootstrap confidence intervals plus pairwise
Mann-Whitney comparisons with Holm-Bonferroni correction.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

import click

from chaosprobe.metrics.statistics import bootstrap_ci, pairwise_comparisons


def _load_strategies(summary_path: Path) -> Dict[str, List[float]]:
    """Extract ``{strategy: [resilienceScore, ...]}`` from a summary file."""
    raw = json.loads(summary_path.read_text())
    strategies = raw.get("strategies") or {}
    out: Dict[str, List[float]] = {}
    for name, sdata in strategies.items():
        iters = sdata.get("iterations") or []
        scores = [
            float(it["resilienceScore"])
            for it in iters
            if isinstance(it, dict) and it.get("resilienceScore") is not None
        ]
        if scores:
            out[name] = scores
    return out


def _format_text(
    samples: Dict[str, List[float]],
    ci_rows: Dict[str, Dict[str, float]],
    pairwise_rows: List[Dict[str, object]],
    confidence: float,
) -> str:
    """Render the analysis as a fixed-width text report."""
    lines: List[str] = []
    lines.append(f"Bootstrap {int(confidence * 100)}% CI for resilienceScore (mean):")
    lines.append(f"  {'strategy':<20} {'n':>3}  {'mean':>8}  {'CI low':>8}  {'CI high':>8}")
    for name in sorted(samples.keys()):
        ci = ci_rows[name]
        lines.append(
            f"  {name:<20} {ci['n']:>3}  "
            f"{ci['point']!s:>8}  {ci['ci_low']!s:>8}  {ci['ci_high']!s:>8}"
        )

    lines.append("")
    lines.append("Pairwise Mann-Whitney U (Holm-Bonferroni adjusted):")
    if not pairwise_rows:
        lines.append("  (no pairs — need at least two strategies with samples)")
    else:
        header = (
            f"  {'a':<20} {'b':<20} {'mean_a':>8} {'mean_b':>8} " f"{'p_raw':>8} {'p_holm':>8}  sig"
        )
        lines.append(header)
        for row in pairwise_rows:
            sig = "✓" if row.get("significant_05") else " "
            lines.append(
                f"  {row['a']!s:<20} {row['b']!s:<20} "
                f"{row['mean_a']!s:>8} {row['mean_b']!s:>8} "
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
    confidence: float,
    n_resamples: int,
    seed: int,
    as_json: bool,
    output: Optional[Path],
):
    """Compute CI and pairwise significance for resilience scores.

    \b
    Examples:
      chaosprobe stats -s results/20260530-142103/summary.json
      chaosprobe stats -s results/.../summary.json --json -o stats.json
    """
    samples = _load_strategies(summary)
    if not samples:
        click.echo("Error: no strategies with resilienceScore found.", err=True)
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
            "confidence": confidence,
            "n_resamples": n_resamples,
            "ci": ci_rows,
            "pairwise": pairwise_rows,
        }
        rendered = json.dumps(payload, indent=2)
    else:
        rendered = _format_text(samples, ci_rows, pairwise_rows, confidence)

    if output:
        output.write_text(rendered + "\n")
        click.echo(f"Wrote {output}")
    else:
        click.echo(rendered)
