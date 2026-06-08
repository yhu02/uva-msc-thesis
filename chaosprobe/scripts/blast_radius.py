#!/usr/bin/env python3
"""Node-failure blast radius — an availability metric for placement.

When a node is drained (or fails), every pod pinned to that node goes down.
Because ChaosProbe pins each deployment to a node via a ``kubernetes.io/hostname``
nodeSelector, a single-replica service on the drained node has *nowhere to
reschedule* (its only allowed node is cordoned) and stays at zero ready endpoints
for the whole drain. So the *blast radius* of a node drain — how many services it
takes offline — is a direct function of how the placement concentrated services
onto nodes:

  - ``colocate``  packs many services onto few nodes → draining one node knocks
    out a large set at once (wide blast radius).
  - ``spread``    distributes services across nodes → draining one node knocks out
    only the handful that shared it (narrow blast radius).

This is the *availability* counterpart to the east-west latency-tail story (H5):
the same co-location structure that raises the cross-node call fraction also
shrinks the per-node blast radius. One graph property, two consequences.

The metric is read straight from data ChaosProbe already records — the placement
``assignments`` (deployment -> node) and the EndpointSlice ready counts captured
pre- and post-chaos (``metrics.endpointSlices``) — so it needs no probe verdict
(node-drain leaves LitmusChaos probes Unknown; this signal is independent of that).

Per strategy it reports:
  - the placement's worst-case exposure: the most services co-located on any one
    node (what a drain of *that* node would take down);
  - the node actually drained and the services pinned to it (placement-predicted
    blast); and
  - the observed blast: services whose ready endpoints fell to zero, total pods
    lost, and mean recovery time.

Usage
-----
    uv run python scripts/blast_radius.py -s results/<run>/summary.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from typing import Dict, List, Optional, Tuple, TypedDict


class BlastMetrics(TypedDict):
    """Per-strategy node-drain blast-radius metrics (also the JSON shape)."""

    measuredServices: int
    knockedToZero: List[str]
    blastRadius: int
    podsLost: int
    drainedNode: Optional[str]
    servicesOnDrainedNode: List[str]
    maxNodeConcentration: int
    nodeDistribution: Dict[str, List[str]]
    meanRecoveryMs: Optional[float]


def _strategies(summary: dict) -> Dict[str, dict]:
    """Strategy name -> strategy dict, flattening single-/multi-fault shapes."""
    out: Dict[str, dict] = {}
    if summary.get("strategies"):
        out.update(summary["strategies"])
    for fault in (summary.get("faults") or {}).values():
        out.update((fault or {}).get("strategies") or {})
    return out


def _assignments(strategy: dict) -> Dict[str, str]:
    """Deployment -> node assignment the strategy realised (empty if absent)."""
    return dict(((strategy.get("placement") or {}).get("assignments")) or {})


def placement_concentration(assignments: Dict[str, str]) -> Tuple[Dict[str, List[str]], int]:
    """(node -> sorted services, max services co-located on any one node).

    The max is the placement's worst-case node-drain blast radius: drain the
    busiest node and that many services go down at once.
    """
    dist: Dict[str, List[str]] = {}
    for svc, node in assignments.items():
        dist.setdefault(node, []).append(svc)
    sorted_dist = {n: sorted(v) for n, v in dist.items()}
    max_on_node = max((len(v) for v in dist.values()), default=0)
    return sorted_dist, max_on_node


def _ready(phase: Optional[dict], svc: str) -> Optional[int]:
    """Ready endpoint count for ``svc`` in an EndpointSlice phase, or None."""
    services = (phase or {}).get("services") or {}
    val = (services.get(svc) or {}).get("ready")
    return val if isinstance(val, int) else None


def ready_deltas(endpoint_slices: dict, app_services: List[str]) -> Dict[str, tuple]:
    """For each app service, (preChaos ready, postChaos ready) when both are known."""
    pre = endpoint_slices.get("preChaos")
    post = endpoint_slices.get("postChaos")
    deltas: Dict[str, tuple] = {}
    for svc in app_services:
        p = _ready(pre, svc)
        q = _ready(post, svc)
        if p is not None and q is not None:
            deltas[svc] = (p, q)
    return deltas


def blast_metrics(strategy: dict) -> Optional[BlastMetrics]:
    """Blast-radius metrics for one strategy, or None if the data is missing.

    Returns None when there is no placement or no pre/post EndpointSlice snapshot
    to measure against (e.g. a non-node fault, or an aborted iteration).
    """
    assignments = _assignments(strategy)
    endpoint_slices = (strategy.get("metrics") or {}).get("endpointSlices") or {}
    if not assignments or "preChaos" not in endpoint_slices:
        return None

    deltas = ready_deltas(endpoint_slices, sorted(assignments))
    if not deltas:
        return None

    knocked = sorted(svc for svc, (p, q) in deltas.items() if p > 0 and q == 0)
    pods_lost = sum(max(0, p - q) for (p, q) in deltas.values())

    # The drained node is the one hosting the services that went to zero. Using
    # the mode is robust to a stray service that happened to be unready already.
    drained_nodes = [assignments[svc] for svc in knocked if svc in assignments]
    drained_node = Counter(drained_nodes).most_common(1)[0][0] if drained_nodes else None
    predicted = sorted(s for s, n in assignments.items() if drained_node and n == drained_node)

    node_dist, max_on_node = placement_concentration(assignments)
    recovery = ((strategy.get("metrics") or {}).get("recovery") or {}).get("summary") or {}
    mean_recovery = recovery.get("meanRecovery_ms")

    return {
        "measuredServices": len(deltas),
        "knockedToZero": knocked,
        "blastRadius": len(knocked),
        "podsLost": pods_lost,
        "drainedNode": drained_node,
        "servicesOnDrainedNode": predicted,
        "maxNodeConcentration": max_on_node,
        "nodeDistribution": node_dist,
        "meanRecoveryMs": mean_recovery,
    }


def collect(summary: dict) -> Dict[str, BlastMetrics]:
    """Strategy name -> blast metrics, for strategies that have measurable data."""
    out: Dict[str, BlastMetrics] = {}
    for name, strat in _strategies(summary).items():
        m = blast_metrics(strat)
        if m is not None:
            out[name] = m
    return out


def report(summary: dict) -> Dict[str, BlastMetrics]:
    """Print the per-strategy blast-radius table and return the raw metrics."""
    metrics = collect(summary)
    print("\nNode-drain blast radius by placement\n")
    if not metrics:
        print(
            "  No measurable node-drain data found (need placement assignments and a\n"
            "  pre/post EndpointSlice snapshot). Was this a node-fault run?"
        )
        return metrics

    header = (
        f"  {'strategy':<16}{'drained':>9}{'on node':>9}"
        f"{'blast':>7}{'pods lost':>11}{'max/node':>10}{'recovery ms':>13}"
    )
    print(header)
    # Order by observed blast radius, widest first — the headline contrast.
    for name in sorted(metrics, key=lambda n: metrics[n]["blastRadius"], reverse=True):
        m = metrics[name]
        rec = m["meanRecoveryMs"]
        print(
            f"  {name:<16}"
            f"{(m['drainedNode'] or '-'):>9}"
            f"{len(m['servicesOnDrainedNode']):>9}"
            f"{m['blastRadius']:>7}"
            f"{m['podsLost']:>11}"
            f"{m['maxNodeConcentration']:>10}"
            f"{(('%.0f' % rec) if isinstance(rec, (int, float)) else '-'):>13}"
        )

    print(
        "\n  blast   = services driven to 0 ready endpoints by the drain (observed)\n"
        "  on node = services the placement pinned to the drained node (predicted)\n"
        "  max/node= most services on any single node = worst-case blast if it drained\n"
    )
    blasts = {n: m["blastRadius"] for n, m in metrics.items()}
    if len(set(blasts.values())) > 1:
        widest = max(blasts, key=lambda n: blasts[n])
        narrowest = min(blasts, key=lambda n: blasts[n])
        print(
            f"  Widest blast: {widest} ({blasts[widest]} services); "
            f"narrowest: {narrowest} ({blasts[narrowest]}). "
            "Blast radius tracks placement\n  concentration — the availability axis of co-location."
        )
    else:
        print(
            "  All strategies show the same blast radius here — no separation. With a\n"
            "  single replica the drained node always takes at least productcatalogservice;\n"
            "  a wider contrast needs strategies that differ in co-location (colocate vs spread)."
        )
    return metrics


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("-s", "--summary", required=True, help="path to a run's summary.json")
    ap.add_argument("--json", action="store_true", help="emit raw metrics as JSON too")
    args = ap.parse_args()
    with open(args.summary) as fh:
        summary = json.load(fh)
    metrics = report(summary)
    if args.json:
        print("\n" + json.dumps(metrics, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
