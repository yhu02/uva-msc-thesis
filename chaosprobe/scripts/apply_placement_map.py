#!/usr/bin/env python3
"""Apply an explicit service→node placement map (M1a live-validation helper).

The M1a exit criterion validates the analytical
reachable-fraction enumerator against live reality on the 4-worker cluster:
solve → **apply** → schedule → verify.  This script is the *apply + verify*
half of that loop:

- ``--map`` / ``--map-file`` supply ``{"service": "workerN", ...}`` — e.g. a
  :mod:`chaosprobe.placement.fraction_solver` assignment with node indices
  mapped to real node names.
- Each Deployment is pinned via the base mutator's own nodeSelector patching
  (``kubernetes.io/hostname`` + the managed annotation, Recreate rollout) so
  the conventions stay identical to ``chaosprobe placement apply``.
- ``--wait`` blocks until the rollouts are ready, then the live pod placements
  are read back and the **achieved cross-node fraction** is printed using the
  same :func:`~chaosprobe.placement.fraction_solver.achieved_fraction`
  implementation the solver reports — one computation, no drift.
- ``--restore`` removes the nodeSelector pins (the mutator's
  ``clear_placement``).

The dependency graph for the fraction comes from ``--summary`` (the weighted
``routeViewAggregate``-derived graph, recommended — it is what the enumerator
used) or, without one, from live env-var discovery with uniform weights.

Usage
-----
    uv run python scripts/apply_placement_map.py \
        --map '{"frontend": "worker1", "cartservice": "worker2"}' \
        -n online-boutique --wait --summary results/<run>/summary.json \
        [--target 0.5]
    uv run python scripts/apply_placement_map.py --restore -n online-boutique
"""

from __future__ import annotations

import argparse
import json
from typing import Dict, List, Tuple

from chaosprobe.placement.fraction_solver import (
    TARGET_TOLERANCE,
    Edge,
    achieved_fraction,
    deployment_of,
    load_dependency_graph,
)
from chaosprobe.placement.mutator import PlacementMutator

#: Recorded in the chaosprobe.io/placement-strategy annotation so
#: ``clear_placement`` recognises the pins as ChaosProbe-managed.
STRATEGY_NAME = "fraction-target"


def parse_map(map_json: str | None, map_file: str | None) -> Dict[str, str]:
    """Service → node-name map from ``--map`` JSON or ``--map-file``."""
    if (map_json is None) == (map_file is None):
        raise ValueError("exactly one of --map / --map-file is required")
    if map_file is not None:
        with open(map_file) as fh:
            raw = json.load(fh)
    else:
        raw = json.loads(map_json or "")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("placement map must be a non-empty JSON object")
    return {str(svc): str(node) for svc, node in raw.items()}


def apply_map(mutator: PlacementMutator, mapping: Dict[str, str], wait: bool, timeout: int) -> None:
    """Pin each deployment to its mapped node using the base mutator's patching."""
    for svc, node in sorted(mapping.items()):
        mutator._patch_deployment_placement(svc, node, STRATEGY_NAME)
    if wait:
        mutator._wait_for_rollouts(sorted(mapping), timeout)


def live_assignment(mutator: PlacementMutator, services: List[str]) -> Dict[str, str]:
    """Read back the live ``{service: node}`` assignment from running pods.

    A multi-replica service reports the node of its lexically-last pod; for
    the single-replica M1a loop the pod→service mapping is one-to-one.
    """
    pods = mutator.observe_pod_placements(sorted(services))
    return {deployment_of(pod): node for pod, node in sorted(pods.items())}


def graph_edges(mutator: PlacementMutator, summary: str | None) -> Tuple[List[Edge], str]:
    """The fraction's edge list and a label naming its provenance."""
    if summary:
        return load_dependency_graph(summary)[0], f"summary {summary}"
    return (
        [(src, dst, 1.0) for src, dst in sorted(set(mutator.get_service_dependencies()))],
        "live env-var discovery (uniform weights)",
    )


def report(
    mapping: Dict[str, str],
    actual: Dict[str, str],
    edges: List[Edge],
    source: str,
    target: float | None,
) -> None:
    """Print intended vs actual placement and the achieved cross-node fraction."""
    print(f"\n  {'service':<26}{'intended':>12}{'actual':>12}")
    for svc in sorted(mapping):
        landed = actual.get(svc, "-")
        marker = "" if landed == mapping[svc] else "  <-- MISMATCH"
        print(f"  {svc:<26}{mapping[svc]:>12}{landed:>12}{marker}")
    print(f"\n  dependency graph: {len(edges)} edges from {source}")
    try:
        achieved = achieved_fraction(actual, edges)
    except ValueError as exc:
        print(f"  achieved cross-node fraction: undefined ({exc})")
        return
    print(f"  achieved cross-node fraction: {achieved:.4f}")
    if target is not None:
        gap = abs(achieved - target)
        verdict = "ACCEPTED" if gap <= TARGET_TOLERANCE else "REJECTED"
        print(f"  target {target:.4f} -> gap {gap:.4f} -> {verdict} (rule: <= {TARGET_TOLERANCE})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--map", help='JSON object: {"service": "nodeName", ...}')
    parser.add_argument("--map-file", help="path to a JSON file with the same shape as --map")
    parser.add_argument("-n", "--namespace", default="online-boutique", help="app namespace")
    parser.add_argument("--wait", action="store_true", help="wait for rollouts before verifying")
    parser.add_argument("--timeout", type=int, default=300, help="rollout wait timeout (s)")
    parser.add_argument(
        "--summary", help="summary.json supplying the weighted dependency graph (recommended)"
    )
    parser.add_argument(
        "--target", type=float, help="optional target f to grade the achieved fraction against"
    )
    parser.add_argument(
        "--restore", action="store_true", help="remove the nodeSelector pins and exit"
    )
    return parser


def main(argv: List[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.restore and (args.map or args.map_file):
        raise SystemExit("--restore takes no --map/--map-file")

    mutator = PlacementMutator(args.namespace)
    if args.restore:
        cleared = mutator.clear_placement(wait=args.wait, timeout=args.timeout)
        print(f"Restored default scheduling for {len(cleared)} deployment(s)")
        return

    try:
        mapping = parse_map(args.map, args.map_file)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Pinning {len(mapping)} deployment(s) in namespace '{args.namespace}'")
    apply_map(mutator, mapping, args.wait, args.timeout)
    actual = live_assignment(mutator, sorted(mapping))
    edges, source = graph_edges(mutator, args.summary)
    report(mapping, actual, edges, source, args.target)


if __name__ == "__main__":  # pragma: no cover
    main()
