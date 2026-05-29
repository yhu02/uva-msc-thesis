"""CLI command for per-strategy sample-size (power) analysis.

Given a summary.json, computes for every strategy: "how many iterations
would I have needed to detect a target delta at α=0.05 with 80% power?"
Answers the most common defence question — "but with n=3 your CIs are
too wide" — with a concrete number.

Uses the t-test sample-size approximation:

    n ≈ ( (z_α/2 + z_β) · σ / δ )²    + small-sample adjustment

This is an approximation: Mann-Whitney U power is comparable to t-test
power on roughly-normal data; on heavy-tailed distributions it's a
conservative underestimate.  The output explicitly labels it as an
approximation so the defender doesn't over-claim.
"""

import json
import math
from pathlib import Path
from typing import Any, Dict

import click

# z-values for common two-sided α and one-sided β (= 1 - power).
_Z_HALF_ALPHA = {0.10: 1.645, 0.05: 1.96, 0.01: 2.576}
_Z_BETA = {0.80: 0.8416, 0.90: 1.2816, 0.95: 1.6449}


def _required_n(stddev: float, delta: float, alpha: float, power: float) -> int:
    """Two-sample, equal-n t-test sample size for a target mean
    difference ``delta`` with observed within-group stddev ``stddev``.

    Uses the normal-distribution approximation; the +2 fudge factor is
    a small-sample correction that keeps the answer conservative for
    n < 20 (where the t-distribution's heavier tails matter).
    """
    if delta <= 0 or stddev <= 0:
        return 0
    z_a = _Z_HALF_ALPHA.get(alpha, 1.96)
    z_b = _Z_BETA.get(power, 0.8416)
    n = ((z_a + z_b) * stddev / delta) ** 2
    # Two-sample, equal-n: multiply by 2 for "per group" using the
    # two-sample variance.  Add 2 for the small-sample correction.
    return max(2, int(math.ceil(2 * n)) + 2)


def _analyse_strategy(
    name: str,
    sdata: Dict[str, Any],
    metric: str,
    delta: float,
    alpha: float,
    power: float,
) -> Dict[str, Any]:
    """Per-strategy power calculation against a single metric.

    Pulls per-iteration samples from ``iterations[].resilienceScore``
    (or recovery) and the observed standard deviation from the strategy
    aggregate.
    """
    agg = sdata.get("aggregated") or {}
    if metric == "resilience":
        mean = agg.get("meanResilienceScore")
        stddev = agg.get("stddevResilienceScore")
    elif metric == "recovery":
        mean = agg.get("meanRecoveryTime_ms")
        stddev = agg.get("stddevRecoveryTime_ms")
    else:
        raise ValueError(f"unknown metric: {metric}")
    current_n = len(sdata.get("iterations") or [])
    result: Dict[str, Any] = {
        "currentN": current_n,
        "currentMean": mean,
        "currentStddev": stddev,
        "targetDelta": delta,
        "alpha": alpha,
        "power": power,
    }
    if mean is None or stddev is None:
        result["requiredN"] = None
        result["status"] = "no-data"
        return result
    if stddev == 0:
        result["requiredN"] = 2
        result["status"] = "trivial (stddev=0)"
        return result
    n_req = _required_n(stddev, delta, alpha, power)
    result["requiredN"] = n_req
    if current_n >= n_req:
        result["status"] = "achieved"
    else:
        result["status"] = "insufficient"
    return result


@click.command("power")
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
    help="Metric to compute power for.",
)
@click.option(
    "--target-delta",
    "-d",
    type=float,
    default=10.0,
    show_default=True,
    help=(
        "Smallest difference (in metric units) you want to be able to detect "
        "with the given power.  Default 10 — picked for resilienceScore."
    ),
)
@click.option(
    "--alpha",
    type=click.Choice(["0.01", "0.05", "0.10"]),
    default="0.05",
    show_default=True,
    help="Two-sided significance level.",
)
@click.option(
    "--power",
    type=click.Choice(["0.80", "0.90", "0.95"]),
    default="0.80",
    show_default=True,
    help="Target statistical power (1 − β).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit per-strategy results as JSON.",
)
def power(
    summary: Path,
    metric: str,
    target_delta: float,
    alpha: str,
    power: str,
    as_json: bool,
):
    """Compute required sample size per strategy for a target effect.

    \b
    Examples:
      chaosprobe power -s summary.json --target-delta 10
      chaosprobe power -s summary.json --metric recovery -d 200
      chaosprobe power -s summary.json -d 5 --power 0.90 --json
    """
    alpha_f = float(alpha)
    power_f = float(power)
    raw = json.loads(summary.read_text())
    strategies = raw.get("strategies") or {}

    results: Dict[str, Dict[str, Any]] = {}
    for name in sorted(strategies.keys()):
        results[name] = _analyse_strategy(
            name, strategies[name], metric, target_delta, alpha_f, power_f
        )

    if as_json:
        click.echo(
            json.dumps(
                {
                    "source": str(summary),
                    "metric": metric,
                    "targetDelta": target_delta,
                    "alpha": alpha_f,
                    "power": power_f,
                    "perStrategy": results,
                    "note": (
                        "Approximate two-sample t-test sample size.  "
                        "Mann-Whitney U power is comparable on roughly-normal data; "
                        "underestimate on heavy-tailed distributions."
                    ),
                },
                indent=2,
            )
        )
        return

    metric_label = {"resilience": "resilienceScore", "recovery": "meanRecovery_ms"}[metric]
    click.echo(
        f"Power analysis for {metric_label} at α={alpha_f}, power={power_f}, "
        f"target Δ={target_delta} (approximate)"
    )
    header = f"  {'strategy':<20} {'currentN':>8} {'stddev':>10} " f"{'requiredN':>10}  status"
    click.echo(header)
    for name, r in results.items():
        stddev_str = f"{r['currentStddev']:.2f}" if r.get("currentStddev") is not None else "—"
        req_str = str(r.get("requiredN")) if r.get("requiredN") is not None else "—"
        click.echo(
            f"  {name:<20} {r['currentN']:>8} {stddev_str:>10} " f"{req_str:>10}  {r['status']}"
        )
    click.echo("")
    click.echo(
        "  note: approximate two-sample t-test sample size.  "
        "Comparable to Mann-Whitney on roughly-normal data; conservative "
        "underestimate on heavy-tailed distributions."
    )
