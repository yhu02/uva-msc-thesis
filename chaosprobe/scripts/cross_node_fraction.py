#!/usr/bin/env python3
"""Cross-node call fraction — a graph-derived placement metric (P1/P2 roadmap).

For a given pod placement, the *cross-node call fraction* is the fraction of the
service dependency graph's inter-service edges whose two endpoints sit on
DIFFERENT nodes. It is computable from data ChaosProbe already records — the
per-iteration actual ``podPlacements`` (pod->node) plus the inter-service edges
encoded in the east-west route keys of ``aggregated.routeViewAggregate`` — and
requires no chaos to compute. The hypothesis it serves: a placement's east-west
tail-latency penalty under load is predictable from this graph metric (placement
-> cross-node fraction -> east-west tail).

This script reports, per strategy, the mean cross-node fraction (averaged over the
run's iterations, using the *actual* placement the scheduler realised) alongside
the during-load median east-west p95, and the rank correlation between them.

HONEST CAVEAT (printed in the output): with only `colocate` forcing node-locality,
the spreading strategies (default/spread/baseline) tie near the graph's intrinsic
cross-node fraction, so a *gradient* — the evidence that the fraction is a
CONTINUOUS predictor, not just a colocate-vs-rest binary — needs the
intermediate-fraction strategies (`dependency-aware`, `best-fit`, `random`,
`adversarial`) in the run. Read the correlation accordingly.

Usage
-----
    uv run python scripts/cross_node_fraction.py -s results/<run>/summary.json
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from typing import Dict, List, Optional, Set, Tuple

# Shared with the v2/M1a fraction-targeting solver — the package module is the
# single source of truth for graph extraction and the fraction computation.
from chaosprobe.placement.fraction_solver import (
    achieved_fraction,
    deployment_of,
    edges_from_route_view,
    strategies_from_summary,
)


def cross_node_fraction(
    pod_placements: Dict[str, str], edges: Set[Tuple[str, str]]
) -> Optional[float]:
    """Fraction of edges whose endpoints are on different nodes.

    Unweighted view of :func:`chaosprobe.placement.fraction_solver.achieved_fraction`
    (uniform edge weights). Edges with an endpoint that was not placed (e.g. an
    unmanaged ``loadgenerator``) are skipped. Returns None when no edge has both
    endpoints placed.
    """
    dep_to_node: Dict[str, str] = {deployment_of(p): n for p, n in pod_placements.items()}
    try:
        return achieved_fraction(dep_to_node, [(a, b, 1.0) for a, b in edges])
    except ValueError:
        return None


def target_scoped_cross_node_fraction(
    pod_placements: Dict[str, str], edges: Set[Tuple[str, str]], target: str
) -> Optional[float]:
    """Cross-node fraction over only the edges incident on ``target``.

    The global fraction (above) is a whole-graph metric. Session-1 data showed it
    does NOT predict the conntrack flush: ``dependency-aware`` and ``random`` have
    high global fractions yet don't flush. The hypothesis this serves is that the
    flush is driven by whether the *killed service's own* dependents span nodes —
    so we restrict to edges touching ``target`` (the chaos victim) and ask what
    fraction of those cross a node boundary. Returns None if no incident edge has
    both endpoints placed.
    """
    incident = {(a, b) for (a, b) in edges if a == target or b == target}
    return cross_node_fraction(pod_placements, incident)


def east_west_median_p95(route_view: list) -> Optional[float]:
    """Median during-chaos p95 over the east-west (inter-service) routes."""
    vals = [
        ((e.get("latencyProber") or {}).get("during-chaos") or {}).get("meanP95_ms")
        for e in route_view or []
        if "->" in (e.get("route") or "")
    ]
    vals = [v for v in vals if isinstance(v, (int, float))]
    return st.median(vals) if vals else None


def _spearman(pairs: List[Tuple[float, float]]) -> Optional[float]:
    """Spearman rank correlation; None if fewer than 3 points."""
    n = len(pairs)
    if n < 3:
        return None

    def rank(values: List[float]) -> List[float]:
        order = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    rx, ry = rank([a for a, _ in pairs]), rank([b for _, b in pairs])
    mx, my = st.mean(rx), st.mean(ry)
    dx = [a - mx for a in rx]
    dy = [b - my for b in ry]
    den = (sum(a * a for a in dx) * sum(b * b for b in dy)) ** 0.5
    return (sum(a * b for a, b in zip(dx, dy)) / den) if den else None


def report(summary: dict) -> None:
    strats = strategies_from_summary(summary)
    print("\nCross-node call fraction vs east-west tail (during load)\n")
    print(f"  {'strategy':<18}{'cross-node frac':>16}{'EW median p95 (ms)':>20}")
    pairs: List[Tuple[float, float]] = []
    for name, s in strats.items():
        agg = s.get("aggregated") or {}
        rva = agg.get("routeViewAggregate") or []
        edges = edges_from_route_view(rva)
        fracs = []
        for it in s.get("iterations") or []:
            f = cross_node_fraction(it.get("podPlacements") or {}, edges)
            if f is not None:
                fracs.append(f)
        frac = st.mean(fracs) if fracs else None
        p95 = east_west_median_p95(rva)
        if frac is not None and p95 is not None:
            pairs.append((frac, p95))
        print(
            f"  {name:<18}"
            f"{('%.3f' % frac) if frac is not None else '-':>16}"
            f"{('%.1f' % p95) if p95 is not None else '-':>20}"
        )
    rho = _spearman(pairs)
    print(
        f"\n  Spearman(cross-node fraction, EW p95) over {len(pairs)} strategies = "
        f"{('%.2f' % rho) if rho is not None else 'n/a (need >=3 placed strategies)'}"
    )
    distinct = len({round(f, 1) for f, _ in pairs})
    if pairs and distinct < 3:
        print(
            "  NOTE: only "
            f"{distinct} distinct fraction value(s) — the spreading strategies tie near\n"
            "  the graph's intrinsic cross-node fraction, so this is a colocate-vs-rest\n"
            "  contrast, NOT a gradient. A continuous-predictor test needs the\n"
            "  intermediate-fraction strategies (dependency-aware, best-fit, random,\n"
            "  adversarial) in the run."
        )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("-s", "--summary", required=True, help="path to a run's summary.json")
    args = ap.parse_args()
    with open(args.summary) as fh:
        summary = json.load(fh)
    report(summary)


if __name__ == "__main__":  # pragma: no cover
    main()
