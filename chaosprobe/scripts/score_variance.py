#!/usr/bin/env python3
"""H1 — is the aggregate resilience score a usable instrument for ranking placements?

The thesis's headline negative result (formerly "M4") is that the aggregate
probe-based resilience score does not reproducibly discriminate placement
strategies. This script quantifies *how badly* by partitioning the variance of
the per-iteration score and turning the partition into a power statement, so the
"the score can't rank placements" claim is a measured number reproducible from a
committed artifact rather than an assertion.

What it computes
----------------
One observation per iteration; cells are (strategy x run); churn (pod-delete)
runs only; baseline and cpu-hog excluded.

1. Variance partition of the per-iteration score into
       between-strategy   (the signal we want)
       run-to-run         (same strategy, different run)
       iteration          (same strategy and run, different iteration)
   and the intraclass correlation ICC_strategy = between / total — the share of
   score variance actually attributable to the strategy. A small ICC means the
   instrument is dominated by noise.

   NOTE: this is a *descriptive* variance partition (run-to-run is the variance
   of cell means, so it carries a little within-cell sampling error), not a
   fitted mixed-effects model. The conclusion (between-strategy is a few percent
   of the total) is robust to that nuance.

2. Power: for the focal colocate-vs-spread contrast, Cohen's d on the
   per-iteration score and the iterations/strategy needed for 80% power
   (alpha=.05, two-sided); the same for the widest observed pairwise gap; and
   the minimum detectable effect at the iteration count actually run.

Caveat the thesis should disclose: the pooled run set mixes probe counts (7 vs
12 probes -> different score granularity) and code versions, so the run-to-run
component partly reflects instrument changes. That is itself a fair source of
non-reproducibility, but it is worth stating.

Usage
-----
    uv run python scripts/score_variance.py [--results-dir results]
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics as st
from collections import defaultdict

from fault_taxonomy import is_churn

# z for a two-sided alpha=0.05 test and 80% power
_Z = 1.959964 + 0.841621
FOCAL = ("colocate", "spread")


def collect(results_dir: str) -> dict[tuple[str, str], list[float]]:
    """(strategy, run) -> flattened per-iteration resilience scores (churn only)."""
    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    for path in sorted(glob.glob(os.path.join(results_dir, "*", "summary.json"))):
        run = os.path.basename(os.path.dirname(path))
        with open(path) as fh:
            summary = json.load(fh)
        for fault_name, fault in (summary.get("faults") or {}).items():
            if not is_churn(fault_name):
                continue
            for sname, s in ((fault or {}).get("strategies") or {}).items():
                if sname == "baseline":
                    continue
                pis = ((s or {}).get("experiment") or {}).get("perIterationScores")
                if pis:
                    cells[(sname, run)].extend(pis)
    return cells


def decompose(cells: dict[tuple[str, str], list[float]]) -> dict:
    strategies = sorted({s for s, _ in cells})

    # iteration variance: pooled within-cell
    within = [st.pvariance(v) for v in cells.values() if len(v) >= 2]
    sig2_iter = st.mean(within) if within else 0.0

    # run-to-run within strategy + strategy grand means
    strat_means: dict[str, float] = {}
    run_vars: list[float] = []
    for strat in strategies:
        cell_means = [st.mean(v) for (s, _), v in cells.items() if s == strat]
        strat_means[strat] = st.mean(cell_means)
        if len(cell_means) >= 2:
            run_vars.append(st.pvariance(cell_means))
    sig2_run = st.mean(run_vars) if run_vars else 0.0
    sig2_strat = st.pvariance(list(strat_means.values()))

    total = sig2_strat + sig2_run + sig2_iter
    return {
        "strategies": strategies,
        "strat_means": strat_means,
        "sig2_strat": sig2_strat,
        "sig2_run": sig2_run,
        "sig2_iter": sig2_iter,
        "total": total,
        "icc": sig2_strat / total if total else float("nan"),
        "n_obs": sum(len(v) for v in cells.values()),
    }


def _n_for_power(d: float) -> float:
    return 2 * _Z * _Z / (d * d) if d > 0 else float("inf")


def report(cells: dict[tuple[str, str], list[float]], n_iter: int) -> None:
    r = decompose(cells)
    t = r["total"]
    print(
        f"\nH1: resilience-score variance partition  " f"(n={r['n_obs']} iterations, churn only)\n"
    )
    print(f"  {'source':<22}{'sigma^2':>9}{'% total':>9}{'sd':>7}")
    for name, key in (
        ("between-strategy (signal)", "sig2_strat"),
        ("run-to-run (noise)", "sig2_run"),
        ("iteration (noise)", "sig2_iter"),
    ):
        v = r[key]
        print(f"  {name:<22}{v:>9.1f}{100 * v / t:>8.1f}%{math.sqrt(v):>7.1f}")
    print(f"\n  ICC_strategy (signal fraction) = {r['icc']:.3f}")
    print(
        "  strategy means: "
        + ", ".join(f"{k} {v:.1f}" for k, v in sorted(r["strat_means"].items()))
    )

    sd_within = math.sqrt(r["sig2_run"] + r["sig2_iter"])
    print(f"\n=== Power on the score (within-strategy sd = {sd_within:.1f}) ===")
    sm = r["strat_means"]
    if all(f in sm for f in FOCAL):
        gap = abs(sm[FOCAL[0]] - sm[FOCAL[1]])
        d = gap / sd_within
        print(
            f"  focal {FOCAL[0]} ({sm[FOCAL[0]]:.1f}) vs {FOCAL[1]} ({sm[FOCAL[1]]:.1f}): "
            f"gap={gap:.1f}  d={d:.3f}  ->  n={_n_for_power(d):.0f} iterations/strategy "
            f"for 80% power"
        )
    lo = min(sm, key=sm.get)
    hi = max(sm, key=sm.get)
    dmax = (sm[hi] - sm[lo]) / sd_within
    print(
        f"  widest gap {hi} ({sm[hi]:.1f}) vs {lo} ({sm[lo]:.1f}): "
        f"d={dmax:.2f}  ->  n={_n_for_power(dmax):.0f}/strategy"
    )
    mde = _Z * math.sqrt(2 / n_iter)
    print(
        f"  at n={n_iter} iterations/run, min detectable effect = "
        f"{mde:.2f} sd = {mde * sd_within:.0f} score points\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--results-dir", default="results")
    ap.add_argument(
        "--n-iter",
        type=int,
        default=3,
        help="iterations/run to evaluate the minimum detectable effect at",
    )
    args = ap.parse_args()
    report(collect(args.results_dir), args.n_iter)


if __name__ == "__main__":
    main()
