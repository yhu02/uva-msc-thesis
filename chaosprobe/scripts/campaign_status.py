#!/usr/bin/env python3
"""Interim status for the multi-session E2 campaign.

A single session's numbers are misleading: with one run the score's run-to-run
variance is structurally zero, so ICC_strategy looks far larger than it is, and
the paired tests read k/k=1 (p=1.0). This script aggregates **across** the clean
campaign sessions in ``campaign-results/`` and reports the three things that
actually become estimable as sessions accumulate:

1. **H1 — ICC_strategy with run-to-run variance now visible** (via
   ``icc_bootstrap``). Watch it fall toward its true value as N grows.
2. **H2 — spread-vs-colocate conntrack flush, paired by session** (via
   ``wilcoxon_signed_rank`` + the exact sign test). The "k/k runs" claim only
   becomes significant around N>=6.
3. **H7-refinement — does the flush track a cross-node fraction?** Compares the
   *global* graph fraction (which session 1 showed fails) against the
   *target-scoped* fraction (edges incident on the chaos victim), to see whether
   the target-neighbourhood metric predicts the flush where the global one does
   not.

It also prints a blunt sufficiency gate: how many sessions are in, and whether
that is enough for the sign test / a stable ICC.

Churn (pod-delete) runs only; baseline excluded.

Usage
-----
    uv run python scripts/campaign_status.py [--results-dir campaign-results] \
        [--target productcatalogservice]
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics as st
from typing import Dict, List, Optional, Tuple

from cross_node_fraction import (
    _spearman,
    cross_node_fraction,
    edges_from_route_view,
    target_scoped_cross_node_fraction,
)
from fault_taxonomy import is_churn

from chaosprobe.metrics.statistics import icc_bootstrap, wilcoxon_signed_rank

DEFAULT_TARGET = "productcatalogservice"  # the pod-delete chaos victim


def _phase_mean(strategy: dict, metric: str, phase: str) -> Optional[float]:
    phases = ((strategy.get("metrics") or {}).get("prometheus") or {}).get("phases") or {}
    entry = ((phases.get(phase) or {}).get("metrics") or {}).get(metric)
    return entry.get("mean") if isinstance(entry, dict) else None


def _flush_pct(strategy: dict) -> Optional[float]:
    """Conntrack flush % during the kill cycle: (pre - during) / pre * 100."""
    pre = _phase_mean(strategy, "conntrack_entries_per_node", "pre-chaos")
    dur = _phase_mean(strategy, "conntrack_entries_per_node", "during-chaos")
    if pre and dur is not None and pre > 0:
        return (pre - dur) / pre * 100
    return None


def _churn_strategies(summary: dict):
    """Yield (strategy_name, strategy_dict) for churn faults, baseline excluded."""
    for fault_name, fault in (summary.get("faults") or {}).items():
        if not is_churn(fault_name):
            continue
        for sname, s in ((fault or {}).get("strategies") or {}).items():
            if sname == "baseline":
                continue
            yield sname, s


def _mean_fraction(strategy: dict, target: Optional[str]) -> Optional[float]:
    """Mean (over iterations) cross-node fraction; target-scoped if target given."""
    edges = edges_from_route_view(
        (strategy.get("aggregated") or {}).get("routeViewAggregate") or []
    )
    fracs: List[float] = []
    for it in strategy.get("iterations") or []:
        placements = it.get("podPlacements") or {}
        f = (
            target_scoped_cross_node_fraction(placements, edges, target)
            if target is not None
            else cross_node_fraction(placements, edges)
        )
        if f is not None:
            fracs.append(f)
    return st.mean(fracs) if fracs else None


def collect(summaries: List[Tuple[str, dict]], target: str) -> dict:
    """Aggregate the per-(run, strategy) data the three analyses need."""
    cells: Dict[Tuple[str, str], List[float]] = {}
    flush_pairs: List[Tuple[str, float, float]] = []  # (run, spread_flush, colocate_flush)
    frac_flush: List[Tuple[float, float, float]] = []  # (global_frac, target_frac, flush)
    for run, summary in summaries:
        run_flush: Dict[str, float] = {}
        for sname, s in _churn_strategies(summary):
            scores = ((s.get("experiment") or {}).get("perIterationScores")) or []
            if scores:
                cells[(sname, run)] = list(scores)
            flush = _flush_pct(s)
            if flush is not None:
                run_flush[sname] = flush
                gf = _mean_fraction(s, None)
                tf = _mean_fraction(s, target)
                if gf is not None and tf is not None:
                    frac_flush.append((gf, tf, flush))
        if "spread" in run_flush and "colocate" in run_flush:
            flush_pairs.append((run, run_flush["spread"], run_flush["colocate"]))
    return {"cells": cells, "flush_pairs": flush_pairs, "frac_flush": frac_flush}


def report(summaries: List[Tuple[str, dict]], target: str) -> dict:
    data = collect(summaries, target)
    n = len(summaries)
    print(f"\nChaosProbe campaign status — {n} session(s), churn only\n")

    # H1 — ICC with run-to-run variance now visible.
    icc = icc_bootstrap(data["cells"])
    print("H1  aggregate-score ICC_strategy:")
    if icc["icc"] is None:
        print("  n/a — not enough data yet")
    else:
        note = "run-to-run variance still 0 (need >=2 sessions)" if n < 2 else "run-to-run visible"
        print(f"  ICC = {icc['icc']}  CI [{icc['ci_low']}, {icc['ci_high']}]   [{note}]")

    # H2 — paired spread-vs-colocate flush.
    pairs = data["flush_pairs"]
    print("\nH2  conntrack flush, spread vs colocate (paired by session):")
    wins = sum(1 for _, sp, co in pairs if sp > co)
    print(f"  spread > colocate in {wins}/{len(pairs)} sessions")
    if pairs:
        w = wilcoxon_signed_rank([sp for _, sp, _ in pairs], [co for _, _, co in pairs])
        sgn = w["sign_test"]
        print(
            f"  Wilcoxon p={w['p_two_sided']}; "
            f"sign test {sgn['n_pos']}/{sgn['n']} p={sgn['p_two_sided']}"
        )

    # H7-refinement — global vs target-scoped fraction as a flush predictor.
    ff = data["frac_flush"]
    print("\nH7  does conntrack flush track a cross-node fraction?")
    if len(ff) >= 3:
        rg = _spearman([(g, fl) for g, _, fl in ff])
        rt = _spearman([(t, fl) for _, t, fl in ff])
        print(f"  Spearman(global frac, flush)        = {_fmt(rg)}")
        print(f"  Spearman(target-scoped frac, flush) = {_fmt(rt)}   (target={target})")
    else:
        print(f"  n/a — need >=3 (strategy,run) cells, have {len(ff)}")

    # Sufficiency gate.
    print("\nSufficiency:")
    sign_ok = bool(pairs) and wins == len(pairs) and len(pairs) >= 6
    print(f"  sessions={n}; sign test {'significant' if sign_ok else 'not yet (need 6/6+)'}")
    if n < 6:
        print(f"  -> ~{6 - n} more clean session(s) for a significant H2 sign test")
    return data


def _fmt(rho: Optional[float]) -> str:
    return f"{rho:.2f}" if rho is not None and not math.isnan(rho) else "n/a"


def _load(results_dir: str) -> List[Tuple[str, dict]]:  # pragma: no cover - filesystem glob
    out: List[Tuple[str, dict]] = []
    for path in sorted(glob.glob(os.path.join(results_dir, "*", "summary.json"))):
        run = os.path.basename(os.path.dirname(path))
        with open(path) as fh:
            out.append((run, json.load(fh)))
    return out


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--results-dir", default="campaign-results")
    ap.add_argument("--target", default=DEFAULT_TARGET)
    args = ap.parse_args()
    report(_load(args.results_dir), args.target)


if __name__ == "__main__":  # pragma: no cover
    main()
