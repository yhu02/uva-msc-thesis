"""CLI command: recommend a placement strategy from a summary.json.

Turns the comparative evidence ChaosProbe already collects (per-strategy
resilience / recovery samples) into an explicit, statistically-justified
recommendation — closing the ``run -> compare -> decide`` feedback loop
that ``stats`` stops one step short of (it reports the pairwise table but
leaves the verdict to the reader).

The decision reuses the same primitives as ``chaosprobe stats``: a
Holm-adjusted pairwise Mann-Whitney U with Cliff's-delta effect sizes,
plus a bootstrap CI per strategy.  The only new logic is ranking by metric
direction and turning the leader-vs-runner-up comparison into a verdict.
"""

import json
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

import click

from chaosprobe.metrics.statistics import bootstrap_ci, pairwise_comparisons

# metric key -> (dotted path inside each iteration dict, label, higher_is_better)
_METRIC_SPECS: Dict[str, Tuple[str, str, bool]] = {
    "resilience": ("resilienceScore", "resilienceScore", True),
    "recovery": ("metrics.recovery.summary.meanRecovery_ms", "meanRecovery_ms", False),
}

# Methodology-control strategies: these swap the destructive fault for a trivial
# no-op (see orchestrator/strategy_runner _baseline handling), so their scores
# reflect "no real chaos" rather than placement resilience. They are not
# deployable placements and must not be *recommended* — excluded by default.
_CONTROL_STRATEGIES = frozenset({"baseline"})


def _as_float(value: object) -> float:
    """Narrow a numeric pairwise-row field (typed ``object``) to ``float``.

    Boundary helper: ``pairwise_comparisons`` returns ``Dict[str, object]``
    rows whose p-value / effect-size fields are always numbers; assert that
    here and coerce.
    """
    assert isinstance(value, (int, float)), f"expected numeric value, got {value!r}"
    return float(value)


def _resolve_path(d: Dict[str, Any], path: str) -> Any:
    """Walk a dotted path through nested dicts; ``None`` if any hop is missing."""
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _samples_by_strategy(raw: Dict[str, Any], metric: str) -> Dict[str, List[float]]:
    """Extract ``{strategy: [sample, ...]}`` for *metric* from a summary dict.

    Strategies with no usable samples for the metric are omitted.
    """
    metric_path = _METRIC_SPECS[metric][0]
    strategies = raw.get("strategies") or {}
    out: Dict[str, List[float]] = {}
    for name, sdata in strategies.items():
        if not isinstance(sdata, dict):
            continue
        samples: List[float] = []
        for it in sdata.get("iterations") or []:
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


def _find_comparison(
    pairwise: List[Dict[str, object]], a: str, b: str
) -> Optional[Dict[str, object]]:
    """Return the pairwise row comparing *a* and *b* (either order), else None."""
    for row in pairwise:
        if {row["a"], row["b"]} == {a, b}:
            return row
    return None


def _recommend(
    samples_by_label: Dict[str, List[float]],
    higher_is_better: bool,
    alpha: float,
) -> Dict[str, Any]:
    """Rank strategies and render a recommendation verdict.

    Returns a dict with ``recommended``, ``status``, ``ranking``,
    ``decisiveComparison`` and ``rationale``.  ``status`` is one of
    ``significant`` / ``tentative`` / ``single-strategy`` / ``no-data``.
    """
    # Rank by mean respecting metric direction; deterministic name tiebreak.
    ranked_names = sorted(
        samples_by_label.keys(),
        key=lambda n: (
            -mean(samples_by_label[n]) if higher_is_better else mean(samples_by_label[n]),
            n,
        ),
    )
    ranking: List[Dict[str, Any]] = []
    for n in ranked_names:
        s = samples_by_label[n]
        ci = bootstrap_ci(s)
        ranking.append(
            {
                "name": n,
                "n": len(s),
                "mean": round(mean(s), 4),
                "ciLow": ci["ci_low"],
                "ciHigh": ci["ci_high"],
            }
        )

    if not ranked_names:
        return {
            "recommended": None,
            "status": "no-data",
            "ranking": [],
            "decisiveComparison": None,
            "rationale": "No strategy in the summary has samples for this metric.",
        }

    leader = ranked_names[0]
    if len(ranked_names) == 1:
        return {
            "recommended": leader,
            "status": "single-strategy",
            "ranking": ranking,
            "decisiveComparison": None,
            "rationale": f"Only '{leader}' has data for this metric; no comparison possible.",
        }

    runner_up = ranked_names[1]
    pairwise = pairwise_comparisons(samples_by_label, holm_bonferroni=True)
    row = _find_comparison(pairwise, leader, runner_up)
    # pairwise_comparisons emits a row for every pair of the strategies we
    # passed in, so the leader-vs-runner-up row is always present.
    assert row is not None
    p = _as_float(row.get("p_holm", row["p_raw"]))
    cliffs = row["cliffs_delta"]
    magnitude = row["effect_size_magnitude"]
    significant = p < alpha

    decisive = {
        "a": leader,
        "b": runner_up,
        "p": p,
        "cliffsDelta": cliffs,
        "magnitude": magnitude,
        "significant": significant,
    }

    if significant:
        status = "significant"
        rationale = (
            f"'{leader}' is significantly better than runner-up '{runner_up}' "
            f"(p={p}, Cliff's delta={cliffs} {magnitude})."
        )
    else:
        status = "tentative"
        rationale = (
            f"'{leader}' leads on mean but the difference vs '{runner_up}' is not "
            f"significant at alpha={alpha} (p={p}). Collect more iterations — "
            f"see `chaosprobe power`."
        )

    return {
        "recommended": leader,
        "status": status,
        "ranking": ranking,
        "decisiveComparison": decisive,
        "rationale": rationale,
    }


def _fmt(value: object) -> str:
    """Format a possibly-None numeric for the ranking table."""
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return "—"


@click.command("recommend")
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
    type=click.Choice(["resilience", "recovery"]),
    default="resilience",
    show_default=True,
    help="Metric the recommendation is based on.",
)
@click.option(
    "--alpha",
    type=click.Choice(["0.01", "0.05", "0.10"]),
    default="0.05",
    show_default=True,
    help="Two-sided significance level for the leader-vs-runner-up test.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the recommendation as JSON.",
)
@click.option(
    "--include-control",
    is_flag=True,
    help=(
        "Include methodology-control strategies (e.g. 'baseline', which injects "
        "no real fault) in the ranking. Excluded by default — they are not "
        "deployable placements and their scores are no-chaos artifacts."
    ),
)
def recommend(summary: Path, metric: str, alpha: str, as_json: bool, include_control: bool):
    """Recommend a placement strategy from a multi-strategy summary.json.

    \b
    Examples:
      chaosprobe recommend -s summary.json
      chaosprobe recommend -s summary.json --metric recovery
      chaosprobe recommend -s summary.json --alpha 0.01 --json
    """
    alpha_f = float(alpha)
    _path, metric_label, higher_is_better = _METRIC_SPECS[metric]
    raw = json.loads(summary.read_text())
    samples = _samples_by_strategy(raw, metric)

    # Drop methodology controls (e.g. 'baseline') unless explicitly asked for —
    # they inject no real fault, so recommending them as a placement is wrong.
    excluded_controls: List[str] = []
    if not include_control:
        excluded_controls = sorted(n for n in samples if n in _CONTROL_STRATEGIES)
        samples = {n: s for n, s in samples.items() if n not in _CONTROL_STRATEGIES}

    result = _recommend(samples, higher_is_better, alpha_f)
    result["metric"] = metric_label
    result["excludedControls"] = excluded_controls

    if as_json:
        click.echo(json.dumps({"source": str(summary), **result}, indent=2))
        return

    direction = "higher is better" if higher_is_better else "lower is better"
    click.echo(f"Placement recommendation by {metric_label} ({direction}):")
    click.echo("")
    if excluded_controls:
        click.echo(
            f"  (excluded control strategy: {', '.join(excluded_controls)} — "
            "injects no real fault; pass --include-control to include)"
        )
        click.echo("")
    if not result["ranking"]:
        click.echo("  No placement strategy has data for this metric.")
        return

    click.echo(f"  {'rank':>4}  {'strategy':<20} {'n':>3} {'mean':>10}  {'95% CI':>20}")
    for i, r in enumerate(result["ranking"], 1):
        ci = f"[{_fmt(r['ciLow'])}, {_fmt(r['ciHigh'])}]"
        click.echo(f"  {i:>4}  {r['name']:<20} {r['n']:>3} {r['mean']:>10.2f}  {ci:>20}")
    click.echo("")
    click.echo(f"  -> Recommended: {result['recommended']}  [{result['status']}]")
    click.echo(f"     {result['rationale']}")
