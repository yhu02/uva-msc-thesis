"""CLI command for per-strategy statistical analysis.

Reads per-strategy iteration samples from a ``summary.json`` produced by
``chaosprobe run`` and emits bootstrap confidence intervals plus pairwise
Mann-Whitney comparisons with Holm-Bonferroni correction.  The metric
under analysis is selectable via ``--metric``; ``--all-metrics`` runs
every supported metric in one invocation.
"""

import csv
import io
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
    # The recovery split — d2s is the scheduler-decision leg, s2r is the
    # kubelet+CRI bring-up leg.  Exposing them as first-class metric keys
    # lets a defender run the same bootstrap + Holm-corrected pairwise
    # pipeline directly against the mechanism breakdown instead of only
    # the combined recovery total.
    "d2s": (
        "metrics.recovery.summary.meanDeletionToScheduled_ms",
        "meanDeletionToScheduled_ms",
    ),
    "s2r": (
        "metrics.recovery.summary.meanScheduledToReady_ms",
        "meanScheduledToReady_ms",
    ),
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
    """Extract ``{strategy: [sample, ...]}`` from a summary file."""
    raw = json.loads(summary_path.read_text())
    return _load_strategies_from_dict(raw, metric_path)


def _load_strategies_from_dict(raw: Dict[str, Any], metric_path: str) -> Dict[str, List[float]]:
    """Same as ``_load_strategies`` but takes an already-parsed dict so the
    file isn't re-read once per metric in ``--all-metrics`` mode."""
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
            f"{'p_raw':>8} {'p_holm':>8} {'delta':>7}  {'magnitude':<11}  sig"
        )
        lines.append(header)
        for row in pairwise_rows:
            sig = "✓" if row.get("significant_05") else " "
            # cliffs_delta / effect_size_magnitude are added by
            # pairwise_comparisons when available (see PR #53).  Render
            # "-" when absent so this code degrades gracefully against
            # older library versions.
            delta_str = (
                f"{row['cliffs_delta']:>7}" if row.get("cliffs_delta") is not None else f"{'-':>7}"
            )
            magnitude = row.get("effect_size_magnitude") or "-"
            lines.append(
                f"  {row['a']!s:<20} {row['b']!s:<20} "
                f"{row['mean_a']!s:>10} {row['mean_b']!s:>10} "
                f"{row['p_raw']!s:>8} {row.get('p_holm', '-')!s:>8} "
                f"{delta_str} {magnitude:<11}  {sig}"
            )
    return "\n".join(lines)


def _format_csv(analyses: Dict[str, Dict[str, Any]]) -> str:
    """Emit one CSV with two sections: CI rows then pairwise rows.

    CSV columns:
      * section=ci: metric, strategy, n, mean, ci_low, ci_high
      * section=pairwise: metric, a, b, mean_a, mean_b, p_raw, p_holm,
        significant_05

    A ``section`` column lets a single file carry both blocks for
    ingestion in thesis tables / spreadsheets without juggling multiple
    output files.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "section",
            "metric",
            "strategy",
            "a",
            "b",
            "n",
            "mean",
            "mean_a",
            "mean_b",
            "ci_low",
            "ci_high",
            "p_raw",
            "p_holm",
            "cliffs_delta",
            "effect_size_magnitude",
            "significant_05",
        ]
    )
    for label, analysis in analyses.items():
        for name in sorted(analysis["ci"].keys()):
            ci = analysis["ci"][name]
            writer.writerow(
                [
                    "ci",
                    label,
                    name,
                    "",
                    "",
                    ci["n"],
                    ci["point"],
                    "",
                    "",
                    ci["ci_low"],
                    ci["ci_high"],
                    "",
                    "",
                    "",
                    "",
                    "",
                ]
            )
        for row in analysis["pairwise"]:
            writer.writerow(
                [
                    "pairwise",
                    label,
                    "",
                    row["a"],
                    row["b"],
                    "",
                    "",
                    row.get("mean_a"),
                    row.get("mean_b"),
                    "",
                    "",
                    row.get("p_raw"),
                    row.get("p_holm", ""),
                    row.get("cliffs_delta", ""),
                    row.get("effect_size_magnitude", ""),
                    row.get("significant_05", ""),
                ]
            )
    return buf.getvalue().rstrip("\n")


def _analyse_metric(
    raw_summary: Dict[str, Any],
    metric_path: str,
    metric_label: str,
    confidence: float,
    n_resamples: int,
    seed: Optional[int],
    pair: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Run CI + pairwise for a single metric.  Returns ``None`` when no
    strategies carry the metric — caller decides whether to error or skip.

    When ``pair`` is set, only strategies whose name is in the list are
    included in the analysis.
    """
    samples = _load_strategies_from_dict(raw_summary, metric_path)
    if pair:
        samples = {name: values for name, values in samples.items() if name in pair}
    if not samples:
        return None
    ci_rows = {
        name: bootstrap_ci(
            values,
            statistic="mean",
            confidence=confidence,
            n_resamples=n_resamples,
            seed=seed,
        )
        for name, values in samples.items()
    }
    pairwise_rows = pairwise_comparisons(samples, holm_bonferroni=True) if len(samples) >= 2 else []
    return {
        "samples": samples,
        "ci": ci_rows,
        "pairwise": pairwise_rows,
        "metric_label": metric_label,
    }


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
    "--all-metrics",
    "all_metrics",
    is_flag=True,
    help="Run every supported metric in one invocation; supersedes --metric.",
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
    "--csv",
    "as_csv",
    is_flag=True,
    help="Emit CI + pairwise rows as a single CSV for thesis tables.",
)
@click.option(
    "--pair",
    "pair",
    type=str,
    default=None,
    help=(
        "Filter analysis to a comma-separated strategy pair "
        "(e.g. 'colocate,spread').  Useful for focused defence "
        "answers — drops everything else from CI + pairwise output."
    ),
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
    all_metrics: bool,
    confidence: float,
    n_resamples: int,
    seed: int,
    as_json: bool,
    as_csv: bool,
    pair: Optional[str],
    output: Optional[Path],
):
    """Compute CI and pairwise significance for a per-strategy metric.

    \b
    Examples:
      chaosprobe stats -s results/20260530-142103/summary.json
      chaosprobe stats -s results/.../summary.json --metric recovery
      chaosprobe stats -s results/.../summary.json --all-metrics
      chaosprobe stats -s results/.../summary.json --json -o stats.json
    """
    actual_seed = None if seed == -1 else seed
    raw_summary = json.loads(summary.read_text())

    pair_list: Optional[List[str]] = None
    if pair:
        pair_list = [s.strip() for s in pair.split(",") if s.strip()]
        if len(pair_list) < 2:
            click.echo(
                "Error: --pair needs at least two comma-separated strategy names.",
                err=True,
            )
            sys.exit(2)

    metric_keys = sorted(_METRIC_SPECS.keys()) if all_metrics else [metric]
    analyses: Dict[str, Dict[str, Any]] = {}
    for key in metric_keys:
        metric_path, metric_label = _METRIC_SPECS[key]
        analysis = _analyse_metric(
            raw_summary,
            metric_path,
            metric_label,
            confidence,
            n_resamples,
            actual_seed,
            pair=pair_list,
        )
        if analysis is not None:
            analyses[metric_label] = analysis

    if not analyses:
        if pair_list:
            click.echo(
                f"Error: no strategies in --pair set "
                f"({', '.join(pair_list)}) carry the requested metric.",
                err=True,
            )
        elif all_metrics:
            click.echo(
                "Error: summary has no strategies with any supported metric.",
                err=True,
            )
        else:
            _, label = _METRIC_SPECS[metric]
            click.echo(f"Error: no strategies with {label} found.", err=True)
        sys.exit(1)

    if as_json and as_csv:
        click.echo("Error: --json and --csv are mutually exclusive.", err=True)
        sys.exit(2)

    if as_json:
        payload: Dict[str, Any] = {
            "source": str(summary),
            "confidence": confidence,
            "n_resamples": n_resamples,
        }
        if all_metrics:
            payload["metrics"] = {
                label: {"ci": a["ci"], "pairwise": a["pairwise"]} for label, a in analyses.items()
            }
        else:
            # Single-metric mode keeps the pre-existing shape.
            single = next(iter(analyses.values()))
            payload["metric"] = single["metric_label"]
            payload["ci"] = single["ci"]
            payload["pairwise"] = single["pairwise"]
        rendered = json.dumps(payload, indent=2)
    elif as_csv:
        rendered = _format_csv(analyses)
    else:
        parts: List[str] = []
        for label, analysis in analyses.items():
            parts.append(
                _format_text(
                    analysis["samples"],
                    analysis["ci"],
                    analysis["pairwise"],
                    confidence,
                    label,
                )
            )
        rendered = "\n\n".join(parts)

    if output:
        output.write_text(rendered + "\n")
        click.echo(f"Wrote {output}")
    else:
        click.echo(rendered)
