#!/usr/bin/env python3
"""E1 — does placement become a user-visible lever under node failure?

The positive contrast to the churn null (H1-H3): under node-drain, placement
should move user-visible availability, and the effect should *interact* with
replica count -- present with several replicas, absent at one. This script
reconstructs that 2-factor design from run outputs and runs an Aligned Rank
Transform factorial ANOVA (the non-parametric test for an interaction on a
bounded, non-normal availability response).

Design
------
factor A = placement strategy (spread, colocate, ...)
factor B = replica count, inferred from the pre-chaos ready count of the
           measured services (a 3-replica deployment reads ready=3 pre-chaos)
value    = availability at the outage trough = mean over measured services of
           ready_trough / ready_pre

One observation per (run, strategy); the two replica levels come from separate
runs (e.g. ``run -r 1`` vs ``run -r 3``). With only one replica level present,
the replica and interaction effects are undefined and reported as such, so the
script degrades cleanly until the multi-replica campaign exists.

Usage
-----
    uv run python scripts/node_drain_interaction.py [--results-dir results]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from typing import Dict, List, Optional, Tuple

from chaosprobe.metrics.statistics import art_anova

NODE_DRAIN = "node-drain"


def _strategies(summary: dict) -> Dict[str, dict]:
    """Flatten the single-fault and multi-fault summary shapes (node-drain only)."""
    out: Dict[str, dict] = {}
    out.update(summary.get("strategies") or {})
    for fault_name, fault in (summary.get("faults") or {}).items():
        if fault and NODE_DRAIN in fault_name:
            out.update((fault or {}).get("strategies") or {})
    return out


def _ready(phase: Optional[dict], svc: str) -> Optional[float]:
    if not isinstance(phase, dict):
        return None
    entry = (phase.get("services") or {}).get(svc)
    if isinstance(entry, dict) and isinstance(entry.get("ready"), (int, float)):
        return float(entry["ready"])
    return None


def _trough_phase(endpoint_slices: dict) -> str:
    """The during-chaos snapshot catches the transient outage; else fall to post."""
    return "duringChaos" if isinstance(endpoint_slices.get("duringChaos"), dict) else "postChaos"


def availability(strategy: dict) -> Optional[Tuple[float, int]]:
    """Trough availability ratio and inferred replica count, or None if absent.

    Availability = mean over measured services of ``ready_trough / ready_pre``
    (1.0 = fully served, 0.0 = full outage).  Replica count = the largest
    pre-chaos ready count seen (a 3-replica service reads 3 pre-chaos).
    """
    endpoint_slices = ((strategy.get("metrics") or {}).get("endpointSlices")) or {}
    pre = endpoint_slices.get("preChaos")
    if not isinstance(pre, dict):
        return None
    trough = endpoint_slices.get(_trough_phase(endpoint_slices))
    ratios: List[float] = []
    replicas = 0
    for svc in pre.get("services") or {}:
        ready_pre = _ready(pre, svc)
        ready_trough = _ready(trough, svc)
        if ready_pre is None or ready_pre <= 0 or ready_trough is None:
            continue
        ratios.append(min(1.0, ready_trough / ready_pre))
        replicas = max(replicas, int(round(ready_pre)))
    if not ratios:
        return None
    return sum(ratios) / len(ratios), replicas


def extract(summary: dict) -> List[Tuple[str, int, float]]:
    """One ``(placement, replicas, availability)`` row per measurable strategy."""
    rows: List[Tuple[str, int, float]] = []
    for sname, strategy in _strategies(summary).items():
        if sname == "baseline":
            continue
        av = availability(strategy)
        if av is None:
            continue
        avail, replicas = av
        rows.append((sname, replicas, avail))
    return rows


def report(rows: List[Tuple[str, int, float]]) -> dict:
    """Run the placement x replicas ART ANOVA and print the effect table."""
    if not rows:
        print("No measurable node-drain availability data.")
        return {}
    art = art_anova([(placement, replicas, value) for placement, replicas, value in rows])
    print(f"\nE1: placement x replicas ART ANOVA  (n={art['n']} strategy-cells)\n")
    print(f"  placements:     {art['levels_a']}")
    print(f"  replica levels: {art['levels_b']}")
    for key, label in (
        ("factor_a", "placement"),
        ("factor_b", "replicas"),
        ("interaction", "placement x replicas"),
    ):
        eff = art[key]
        if eff["f"] is None:
            print(f"  {label:<22} F=  n/a   (insufficient design)")
        else:
            print(f"  {label:<22} F={eff['f']:<8} df=({eff['df1']},{eff['df2']}) p={eff['p']}")
    inter = art["interaction"]
    if inter["f"] is not None and inter["p"] is not None and inter["p"] < 0.05:
        print(
            "\n  => significant interaction: placement is a user-visible lever whose "
            "effect depends on replica count (E1 supported)."
        )
    return art


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--results-dir", default="results")
    args = ap.parse_args()
    rows: List[Tuple[str, int, float]] = []
    for path in sorted(glob.glob(os.path.join(args.results_dir, "*", "summary.json"))):
        with open(path) as fh:
            rows.extend(extract(json.load(fh)))
    report(rows)


if __name__ == "__main__":  # pragma: no cover
    main()
