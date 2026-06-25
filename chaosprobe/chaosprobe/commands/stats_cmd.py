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
    ci_rows: Dict[str, Dict[str, object]],
    pairwise_rows: List[Dict[str, object]],
    confidence: float,
    metric_label: str,
    baseline_relative: Optional[Dict[str, Dict[str, float]]] = None,
    baseline_name: Optional[str] = None,
) -> str:
    """Render the analysis as a fixed-width text report.

    When ``baseline_relative`` is provided, also emits a per-strategy
    "vs <baseline>" block with the mean delta and percent change.
    """
    lines: List[str] = []
    lines.append(f"Bootstrap {int(confidence * 100)}% CI for {metric_label} (mean):")
    lines.append(f"  {'strategy':<20} {'n':>3}  {'mean':>10}  {'CI low':>10}  {'CI high':>10}")
    for name in sorted(samples.keys()):
        ci = ci_rows[name]
        lines.append(
            f"  {name:<20} {ci['n']:>3}  "
            f"{ci['point']!s:>10}  {ci['ci_low']!s:>10}  {ci['ci_high']!s:>10}"
        )

    if baseline_relative and baseline_name:
        lines.append("")
        lines.append(f"Relative to {baseline_name} (mean delta, percent change):")
        lines.append(f"  {'strategy':<20} {'delta':>10} {'percent':>10}")
        for name in sorted(baseline_relative.keys()):
            rel = baseline_relative[name]
            lines.append(f"  {name:<20} {rel['delta']!s:>10} {rel['percent']!s:>9}%")

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


def _format_markdown(analyses: Dict[str, Dict[str, Any]], confidence: float) -> str:
    """Render the analysis as GFM markdown tables — suitable for
    pasting into a thesis document or slide notes.

    One CI table + one pairwise table per metric.  Pairwise table
    includes Cliff's delta and effect-size magnitude alongside the
    p-values so a defender can defend both statistical and practical
    significance from a single artifact.
    """
    parts: List[str] = []
    pct = int(confidence * 100)
    for label, analysis in analyses.items():
        parts.append(f"### {label}\n")
        parts.append(f"**Bootstrap {pct}% CI (mean):**\n")
        parts.append("| strategy | n | mean | CI low | CI high |")
        parts.append("|---|---:|---:|---:|---:|")
        for name in sorted(analysis["ci"].keys()):
            ci = analysis["ci"][name]
            parts.append(
                f"| {name} | {ci['n']} | {ci['point']} | " f"{ci['ci_low']} | {ci['ci_high']} |"
            )
        parts.append("")
        parts.append("**Pairwise Mann-Whitney U (Holm-Bonferroni adjusted):**\n")
        if not analysis["pairwise"]:
            parts.append("_(no pairs — need at least two strategies with samples)_")
        else:
            parts.append(
                "| a | b | mean_a | mean_b | p_raw | p_holm | "
                "Cliff's δ | magnitude | sig (α=.05) |"
            )
            parts.append("|---|---|---:|---:|---:|---:|---:|---|---:|")
            for row in analysis["pairwise"]:
                sig = "✓" if row.get("significant_05") else ""
                delta = row.get("cliffs_delta")
                delta_cell = f"{delta}" if delta is not None else "—"
                magnitude = row.get("effect_size_magnitude") or "—"
                parts.append(
                    f"| {row['a']} | {row['b']} | "
                    f"{row.get('mean_a', '—')} | {row.get('mean_b', '—')} | "
                    f"{row.get('p_raw', '—')} | {row.get('p_holm', '—')} | "
                    f"{delta_cell} | {magnitude} | {sig} |"
                )
        parts.append("")
    return "\n".join(parts).rstrip("\n")


# Ordered worst-to-best for ">= threshold" filtering.
_EFFECT_SIZE_ORDER = ("negligible", "small", "medium", "large")


def _filter_pairwise_by_effect_size(
    rows: List[Dict[str, object]],
    min_magnitude: str,
) -> List[Dict[str, object]]:
    """Drop pairwise rows whose Cliff's delta magnitude is below the
    requested threshold.

    Rows without an ``effect_size_magnitude`` field (older library
    versions or degenerate samples) are kept — better than silently
    dropping data the user might still want to see.
    """
    if min_magnitude not in _EFFECT_SIZE_ORDER:
        return rows
    cutoff = _EFFECT_SIZE_ORDER.index(min_magnitude)
    out: List[Dict[str, object]] = []
    for row in rows:
        mag = row.get("effect_size_magnitude")
        if not isinstance(mag, str) or mag not in _EFFECT_SIZE_ORDER:
            out.append(row)
            continue
        if _EFFECT_SIZE_ORDER.index(mag) >= cutoff:
            out.append(row)
    return out


_SORT_KEYS = {
    "p_holm": (lambda r: r.get("p_holm", float("inf")), False),
    "p_raw": (lambda r: r.get("p_raw", float("inf")), False),
    "delta": (lambda r: abs(r.get("cliffs_delta") or 0.0), True),
}


def _sort_pairwise(rows: List[Dict[str, object]], sort_key: str) -> List[Dict[str, object]]:
    """Reorder pairwise rows by the requested key.

    Default for callers is ``p_holm`` ascending (matches the library
    default).  ``delta`` sorts by absolute Cliff's delta descending —
    largest practical effect first.  Unknown keys are no-ops.
    """
    spec = _SORT_KEYS.get(sort_key)
    if spec is None:
        return rows
    key_fn, reverse = spec
    return sorted(rows, key=key_fn, reverse=reverse)


def _compute_baseline_relative(
    samples: Dict[str, List[float]],
    baseline_name: str,
) -> Optional[Dict[str, Dict[str, float]]]:
    """For each strategy, compute the mean delta and percent change
    relative to ``baseline_name``.

    Returns ``{strategy: {delta, percent}}`` for every strategy that has
    samples and is not the baseline itself.  Returns ``None`` if the
    baseline strategy isn't present in ``samples`` — caller decides
    whether to error or skip.
    """
    if baseline_name not in samples:
        return None
    baseline_vals = samples[baseline_name]
    if not baseline_vals:
        return None
    baseline_mean = sum(baseline_vals) / len(baseline_vals)
    if baseline_mean == 0:
        return None
    out: Dict[str, Dict[str, float]] = {}
    for name, values in samples.items():
        if name == baseline_name or not values:
            continue
        mean = sum(values) / len(values)
        delta = mean - baseline_mean
        out[name] = {
            "delta": round(delta, 2),
            "percent": round(100.0 * delta / abs(baseline_mean), 2),
        }
    return out


def _merge_summaries(raws: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pool ``iterations`` per strategy across multiple summary dicts.

    Strategies present in only some inputs are still included.  All
    other top-level keys (schemaVersion, runMetadata, etc.) are taken
    from the first input — they are not meaningful in the merged view
    and stats only looks at ``strategies[*].iterations``.  Order of
    iterations within a strategy is the input order; iteration numbers
    are not rewritten (stats only consumes the per-iteration metric
    values, not the iteration field).
    """
    if not raws:
        return {"strategies": {}}
    merged_strategies: Dict[str, Dict[str, Any]] = {}
    for raw in raws:
        for name, sdata in (raw.get("strategies") or {}).items():
            if name not in merged_strategies:
                merged_strategies[name] = {"iterations": []}
            iters = sdata.get("iterations") or []
            merged_strategies[name]["iterations"].extend(iters)
    out = dict(raws[0])
    out["strategies"] = merged_strategies
    return out


def _analyse_metric(
    raw_summary: Dict[str, Any],
    metric_path: str,
    metric_label: str,
    confidence: float,
    n_resamples: int,
    seed: Optional[int],
    pair: Optional[List[str]] = None,
    min_effect_size: Optional[str] = None,
    sort_key: str = "p_holm",
    baseline: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Run CI + pairwise for a single metric.  Returns ``None`` when no
    strategies carry the metric — caller decides whether to error or skip.

    When ``pair`` is set, only strategies whose name is in the list are
    included in the analysis.  When ``min_effect_size`` is set, pairwise
    rows below that Cliff's delta magnitude are dropped.  ``sort_key``
    reorders pairwise rows; defaults to ``p_holm`` ascending (matches the
    library default).  When ``baseline`` is set, the analysis also
    includes per-strategy ``baselineRelative`` deltas + percent change.
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
    if min_effect_size:
        pairwise_rows = _filter_pairwise_by_effect_size(pairwise_rows, min_effect_size)
    pairwise_rows = _sort_pairwise(pairwise_rows, sort_key)
    out: Dict[str, Any] = {
        "samples": samples,
        "ci": ci_rows,
        "pairwise": pairwise_rows,
        "metric_label": metric_label,
    }
    if baseline:
        rel = _compute_baseline_relative(samples, baseline)
        if rel is not None:
            out["baselineRelative"] = rel
            out["baselineName"] = baseline
    return out


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
    "--markdown",
    "as_markdown",
    is_flag=True,
    help=(
        "Emit GitHub-flavored markdown tables (one CI + one pairwise per "
        "metric).  Convenient for pasting into thesis documents or slide "
        "notes.  Mutually exclusive with --json and --csv."
    ),
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
    "--effect-size-min",
    "min_effect_size",
    type=click.Choice(["negligible", "small", "medium", "large"]),
    default=None,
    help=(
        "Drop pairwise rows whose Cliff's delta magnitude is below "
        "this threshold.  Cuts the noise when scanning a large pairwise "
        "matrix for practically-meaningful differences."
    ),
)
@click.option(
    "--sort",
    "sort_key",
    type=click.Choice(["p_holm", "p_raw", "delta"]),
    default="p_holm",
    show_default=True,
    help=(
        "Pairwise sort key.  ``p_holm`` / ``p_raw`` ascending (smallest p "
        "first); ``delta`` sorts by absolute Cliff's delta descending "
        "(largest practical effect first)."
    ),
)
@click.option(
    "--baseline",
    default=None,
    type=str,
    help=(
        "Strategy name to use as the baseline for relative comparisons "
        "(typically 'baseline').  When set, output includes the per-"
        "strategy delta + percent change vs the baseline's mean."
    ),
)
@click.option(
    "--merge",
    "merge_paths",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    multiple=True,
    default=(),
    help=(
        "Additional summary.json files whose per-strategy iterations are "
        "pooled with --summary's before CI / pairwise computation.  Use to "
        "tighten CIs by combining multiple runs of the same experiment.  "
        "Strategies present in only some inputs are still included."
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
    as_markdown: bool,
    pair: Optional[str],
    min_effect_size: Optional[str],
    sort_key: str,
    baseline: Optional[str],
    merge_paths: Tuple[Path, ...],
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
    if merge_paths:
        merged_raws = [raw_summary] + [json.loads(p.read_text()) for p in merge_paths]
        raw_summary = _merge_summaries(merged_raws)

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
            min_effect_size=min_effect_size,
            sort_key=sort_key,
            baseline=baseline,
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

    exclusive_flags = sum([as_json, as_csv, as_markdown])
    if exclusive_flags > 1:
        click.echo(
            "Error: --json, --csv, and --markdown are mutually exclusive.",
            err=True,
        )
        sys.exit(2)

    if as_json:
        payload: Dict[str, Any] = {
            "source": str(summary),
            "confidence": confidence,
            "n_resamples": n_resamples,
        }
        if all_metrics:
            metrics_block: Dict[str, Any] = {}
            for label, a in analyses.items():
                block = {"ci": a["ci"], "pairwise": a["pairwise"]}
                if "baselineRelative" in a:
                    block["baselineRelative"] = a["baselineRelative"]
                    block["baselineName"] = a["baselineName"]
                metrics_block[label] = block
            payload["metrics"] = metrics_block
        else:
            # Single-metric mode keeps the pre-existing shape.
            single = next(iter(analyses.values()))
            payload["metric"] = single["metric_label"]
            payload["ci"] = single["ci"]
            payload["pairwise"] = single["pairwise"]
            if "baselineRelative" in single:
                payload["baselineRelative"] = single["baselineRelative"]
                payload["baselineName"] = single["baselineName"]
        rendered = json.dumps(payload, indent=2)
    elif as_csv:
        rendered = _format_csv(analyses)
    elif as_markdown:
        rendered = _format_markdown(analyses, confidence)
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
                    baseline_relative=analysis.get("baselineRelative"),
                    baseline_name=analysis.get("baselineName"),
                )
            )
        rendered = "\n\n".join(parts)

    if output:
        output.write_text(rendered + "\n")
        click.echo(f"Wrote {output}")
    else:
        click.echo(rendered)
