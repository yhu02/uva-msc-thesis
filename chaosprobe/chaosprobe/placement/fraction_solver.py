"""Fraction-targeting placement solver (M1a — solver-feasibility spike).

Implements the greedy edge-cut assignment sketched in ``design/00-DESIGN.md``
§2.3 and the quantization-study enumerator required by ``design/02-WORKPLAN.md``
M1a: given the service dependency graph (inter-service edges + call-volume
weights), choose a service→node assignment whose **cross-node call fraction**
— the weight share of inter-service edges whose endpoints sit on different
nodes — lands as close as possible to a target ``f``, and enumerate which
fractions are reachable at all for a given node count.

The graph is derived exactly the way ``scripts/cross_node_fraction.py`` derives
it (this module is the single source of truth — the script imports from here):
inter-service edges parsed from the east-west route keys of
``aggregated.routeViewAggregate`` in a run's ``summary.json``, restricted to
deployments that actually appear in the recorded per-iteration
``podPlacements``.  Edge weights use the observed Locust call volume
(``locust.totalRequests``) where the route entry carries one; early summaries
record no volume for east-west (latency-prober-only) routes, so those edges
fall back to a uniform weight of 1.0 — making the weighted fraction coincide
with the unweighted metric ``cross_node_fraction.py`` reports.

Acceptance follows the pre-registered rejection rule (DESIGN §2.3): a solution
is **accepted** iff ``|achieved_f − target_f| ≤ 0.05``; misses are reported,
never silently dropped.

Enumeration method (documented per the M1a deliverable): the reachable
fraction set only depends on how the edge-incident services are *partitioned*
across nodes, never on node identity, so the enumerator walks canonical
assignments — set partitions into ≤ N blocks, generated as restricted-growth
strings — instead of all ``N^S`` labelled assignments.  For Online Boutique's
11 services that is 175,275 canonical assignments at N = 4 (vs 4^11 ≈ 4.2 M)
and 677,359 at N = 8, all well inside the default 1,000,000 budget, so
N ∈ {4, 6, 8} are enumerated **exhaustively**; graphs whose canonical count
exceeds the budget fall back to seeded random sampling plus solver refinement
(reported as ``sampled`` — a lower bound on the reachable set, not a census).

CLI::

    python -m chaosprobe.placement.fraction_solver --summary <summary.json> \
        --n-nodes N [--target f | --enumerate [--samples K]] [--seed S]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Hashable, Iterable, List, Mapping, Sequence, Set, Tuple

#: Pre-registered rejection rule (DESIGN §2.3): accept iff |achieved − target| ≤ 0.05.
TARGET_TOLERANCE = 0.05

#: Canonical-assignment budget above which `enumerate_reachable` samples
#: instead of exhaustively walking set partitions.
DEFAULT_EXHAUSTIVE_BUDGET = 1_000_000

#: Decimal places used to deduplicate float fractions in the reachable set.
_FRACTION_DECIMALS = 9

#: Numerical slack for float comparisons inside the search.
_EPS = 1e-12

#: An inter-service edge: (source service, destination service, call-volume weight).
Edge = Tuple[str, str, float]

_POD_SUFFIX = re.compile(r"-[a-z0-9]+-[a-z0-9]+$")  # name-<replicaset>-<pod-id>


# ──────────────────────────────────────────────────────────────────────
# Shared graph extraction (imported by scripts/cross_node_fraction.py)
# ──────────────────────────────────────────────────────────────────────


def deployment_of(pod_name: str) -> str:
    """Strip the ReplicaSet hash + pod id from a pod name to get its Deployment."""
    return _POD_SUFFIX.sub("", pod_name)


def edges_from_route_view(route_view: list) -> Set[Tuple[str, str]]:
    """Inter-service edges (src, dst) parsed from east-west route keys.

    A route key is ``src->dst``, or several comma-joined ``src->dst`` that share a
    destination (e.g. ``checkoutservice->cartservice,frontend->cartservice``).
    """
    edges: Set[Tuple[str, str]] = set()
    for entry in route_view or []:
        route = entry.get("route") or ""
        for part in route.split(","):
            if "->" in part:
                src, dst = part.split("->", 1)
                edges.add((src.strip(), dst.strip()))
    return edges


def strategies_from_summary(summary: dict) -> Dict[str, dict]:
    """Strategy name -> strategy dict, flattening single-/multi-fault shapes.

    Multi-fault summaries reuse strategy names across faults; this name-keyed
    view keeps the last block per name (the per-strategy reporting shape).
    Graph extraction must see *every* block — use :func:`_iter_strategy_blocks`.
    """
    out: Dict[str, dict] = {}
    if summary.get("strategies"):
        out.update(summary["strategies"])
    for fault in (summary.get("faults") or {}).values():
        out.update((fault or {}).get("strategies") or {})
    return out


def _iter_strategy_blocks(summary: dict) -> Iterable[dict]:
    """Every strategy block in a summary, including same-named ones across faults."""
    for strat in (summary.get("strategies") or {}).values():
        yield strat or {}
    for fault in (summary.get("faults") or {}).values():
        for strat in ((fault or {}).get("strategies") or {}).values():
            yield strat or {}


def load_dependency_graph(summary_path: str) -> Tuple[List[Edge], List[str]]:
    """Extract the weighted inter-service dependency graph from a ``summary.json``.

    Edges come from the east-west route keys of every strategy's
    ``aggregated.routeViewAggregate`` (the same parse
    ``scripts/cross_node_fraction.py`` uses).  Per strategy, an edge's weight
    is the sum of its route entries' ``locust.totalRequests`` (call volume);
    entries without a positive request count contribute 1.0 — east-west
    routes carry no Locust volume, so in practice these graphs are uniform-weight.
    Across strategies the per-edge **maximum** is kept, so a strategy whose
    fault suppressed a route cannot dilute the weight and the result does not
    scale with the number of strategies in the run.

    Services are the deployments observed in the recorded per-iteration
    ``podPlacements`` (so an unplaced ``loadgenerator`` endpoint never enters
    the graph); edges with an unplaced endpoint are dropped, mirroring
    ``cross_node_fraction.py``.  If the summary records no placements at all,
    the edge endpoints themselves serve as the service set.

    Returns ``(edges, services)``, both sorted for determinism.
    """
    with open(summary_path) as fh:
        summary = json.load(fh)

    weights: Dict[Tuple[str, str], float] = {}
    placed: Set[str] = set()
    for strat in _iter_strategy_blocks(summary):
        route_view = (strat.get("aggregated") or {}).get("routeViewAggregate") or []
        per_strategy: Dict[Tuple[str, str], float] = {}
        for entry in route_view:
            route = entry.get("route") or ""
            parts = [p for p in route.split(",") if "->" in p]
            if not parts:
                continue
            requests = (entry.get("locust") or {}).get("totalRequests")
            weight = float(requests) if isinstance(requests, (int, float)) and requests > 0 else 1.0
            for part in parts:
                src, dst = part.split("->", 1)
                key = (src.strip(), dst.strip())
                per_strategy[key] = per_strategy.get(key, 0.0) + weight
        for key, weight in per_strategy.items():
            weights[key] = max(weights.get(key, 0.0), weight)
        for iteration in strat.get("iterations") or []:
            for pod_name in (iteration or {}).get("podPlacements") or {}:
                placed.add(deployment_of(pod_name))

    if placed:
        services = sorted(placed)
        weights = {k: w for k, w in weights.items() if k[0] in placed and k[1] in placed}
    else:
        services = sorted({endpoint for key in weights for endpoint in key})
    edges = sorted((src, dst, weight) for (src, dst), weight in weights.items())
    return edges, services


def load_static_topology(path: str) -> Tuple[List[Edge], List[str]]:
    """Load a hand-curated dependency graph from a ``topology.json`` file.

    Static topologies are the M2 solver-gate stand-in for workloads that have
    no measured ``summary.json`` yet (DESIGN §7: hotelReservation is deployed
    and solver-gated in the M2 prep window, before any placement-session run data exists for
    it — see ``scenarios/hotel-reservation/topology.json``). Expected shape::

        {
          "services": ["frontend", "geo", ...],
          "edges": [["frontend", "search"], ...]
        }

    Edges are directed ``[src, dst]`` pairs and receive **uniform weight 1.0**
    (matching the uniform-weight measured graphs — see
    :func:`load_dependency_graph`); any other top-level keys (``workload``,
    ``source``, ``comment``, ...) are ignored as metadata.

    Returns ``(edges, services)``, both sorted for determinism — the same
    shape :func:`load_dependency_graph` returns, so ``solve`` /
    ``enumerate_reachable`` consume either interchangeably.

    Raises ``ValueError`` when the file is structurally unsound: missing or
    empty ``services``/``edges``, non-string or duplicate service names,
    malformed edges, self-loops, duplicate edges, or an edge endpoint that is
    not a declared service.
    """
    with open(path) as fh:
        data = json.load(fh)

    services_raw = data.get("services")
    if not isinstance(services_raw, list) or not services_raw:
        raise ValueError(f"{path}: 'services' must be a non-empty list")
    if not all(isinstance(svc, str) and svc for svc in services_raw):
        raise ValueError(f"{path}: 'services' entries must be non-empty strings")
    if len(set(services_raw)) != len(services_raw):
        raise ValueError(f"{path}: 'services' contains duplicate names")

    edges_raw = data.get("edges")
    if not isinstance(edges_raw, list) or not edges_raw:
        raise ValueError(f"{path}: 'edges' must be a non-empty list")

    known = set(services_raw)
    seen: Set[Tuple[str, str]] = set()
    for edge in edges_raw:
        if (
            not isinstance(edge, list)
            or len(edge) != 2
            or not all(isinstance(endpoint, str) and endpoint for endpoint in edge)
        ):
            raise ValueError(f"{path}: each edge must be a [src, dst] pair of strings: {edge!r}")
        src, dst = edge
        if src == dst:
            raise ValueError(f"{path}: self-loop edge {src!r} -> {dst!r}")
        if (src, dst) in seen:
            raise ValueError(f"{path}: duplicate edge {src!r} -> {dst!r}")
        if src not in known or dst not in known:
            raise ValueError(f"{path}: edge {src!r} -> {dst!r} references an undeclared service")
        seen.add((src, dst))

    edges = sorted((src, dst, 1.0) for src, dst in seen)
    return edges, sorted(services_raw)


# ──────────────────────────────────────────────────────────────────────
# Achieved fraction (single source of truth, shared with the scripts)
# ──────────────────────────────────────────────────────────────────────


def achieved_fraction(assignment: Mapping[str, Hashable], edges: Iterable[Edge]) -> float:
    """Weighted fraction of inter-service edges whose endpoints sit on different nodes.

    ``assignment`` maps service → node (integer index or node name — only
    equality is used).  Edges with an unassigned endpoint are skipped,
    mirroring ``cross_node_fraction.py``; zero-weight edges contribute
    nothing.  Raises ``ValueError`` on a negative weight or when no positive-
    weight edge has both endpoints assigned (the fraction is then undefined).
    """
    cross = 0.0
    total = 0.0
    for src, dst, weight in edges:
        if weight < 0:
            raise ValueError(f"negative edge weight on {src}->{dst}: {weight}")
        if weight == 0 or src not in assignment or dst not in assignment:
            continue
        total += weight
        if assignment[src] != assignment[dst]:
            cross += weight
    if total == 0:
        raise ValueError(
            "cross-node fraction undefined: no positive-weight edge has both endpoints assigned"
        )
    return cross / total


# ──────────────────────────────────────────────────────────────────────
# Solver
# ──────────────────────────────────────────────────────────────────────


@dataclass
class Solution:
    """A solver result: the assignment plus the pre-registered acceptance verdict."""

    assignment: Dict[str, int]
    achieved_f: float
    target_f: float
    accepted: bool
    trace: Dict[str, object] = field(default_factory=dict)


def _usable_edges(edges: Sequence[Edge], services: Sequence[str]) -> List[Edge]:
    """Validate and filter the edge list for the solver/enumerator.

    Returns the positive-weight edges; raises ``ValueError`` on a negative
    weight, an endpoint outside ``services``, or when nothing usable remains.
    """
    service_set = set(services)
    usable: List[Edge] = []
    for src, dst, weight in edges:
        if weight < 0:
            raise ValueError(f"negative edge weight on {src}->{dst}: {weight}")
        if src not in service_set or dst not in service_set:
            raise ValueError(f"edge {src}->{dst} references a service not in `services`")
        if weight > 0:
            usable.append((src, dst, weight))
    if not usable:
        raise ValueError("no positive-weight edges: the cross-node fraction is undefined")
    return usable


def _adjacency(usable: Sequence[Edge]) -> Dict[str, List[Tuple[str, float]]]:
    """Service → [(neighbour, weight)] over non-self edges."""
    adj: Dict[str, List[Tuple[str, float]]] = {}
    for src, dst, weight in usable:
        if src == dst:
            continue  # a self-edge can never cross nodes
        adj.setdefault(src, []).append((dst, weight))
        adj.setdefault(dst, []).append((src, weight))
    return adj


def _greedy_assign(
    order: Sequence[str],
    adj: Dict[str, List[Tuple[str, float]]],
    self_weight: Dict[str, float],
    n_nodes: int,
    target_f: float,
    capacity: Mapping[str, float] | None,
    node_capacity: float | None,
    rng: random.Random,
) -> Tuple[Dict[str, int], List[float], int]:
    """One greedy pass: place services in ``order``, each on the node that
    minimises the gap between the fraction over already-decided edges and the
    target.  Returns ``(assignment, node_loads, capacity_violations)``.
    """
    assignment: Dict[str, int] = {}
    loads = [0.0] * n_nodes
    violations = 0
    decided_cross = 0.0
    decided_total = 0.0

    for svc in order:
        request = capacity.get(svc, 0.0) if capacity is not None else 0.0
        if capacity is not None:
            assert node_capacity is not None  # enforced by solve()
            fits = [n for n in range(n_nodes) if loads[n] + request <= node_capacity + _EPS]
            if not fits:
                # Nothing fits: fall back to the least-loaded node and record
                # the violation rather than failing the whole solve.
                fits = [min(range(n_nodes), key=lambda n: loads[n])]
                violations += 1
        else:
            fits = list(range(n_nodes))

        placed_neighbours = [(o, w) for o, w in adj.get(svc, []) if o in assignment]
        incident = sum(w for _, w in placed_neighbours) + self_weight.get(svc, 0.0)
        best_gap = float("inf")
        best_nodes: List[int] = []
        for node in fits:
            cross_add = sum(w for o, w in placed_neighbours if assignment[o] != node)
            new_total = decided_total + incident
            gap = abs((decided_cross + cross_add) / new_total - target_f) if new_total > 0 else 0.0
            if gap < best_gap - _EPS:
                best_gap, best_nodes = gap, [node]
            elif gap <= best_gap + _EPS:
                best_nodes.append(node)
        node = best_nodes[0] if len(best_nodes) == 1 else rng.choice(best_nodes)
        assignment[svc] = node
        loads[node] += request
        decided_cross += sum(w for o, w in placed_neighbours if assignment[o] != node)
        decided_total += incident
    return assignment, loads, violations


def _local_search(
    assignment: Dict[str, int],
    loads: List[float],
    adj: Dict[str, List[Tuple[str, float]]],
    total_weight: float,
    cross_weight: float,
    n_nodes: int,
    target_f: float,
    capacity: Mapping[str, float] | None,
    node_capacity: float | None,
    max_passes: int,
) -> Tuple[float, int, int]:
    """Refine ``assignment`` in place with single-service moves + pairwise swaps.

    First-improvement hill climbing on ``|fraction − target|``; repeats until a
    full pass yields no move or ``max_passes`` is hit.  Returns the final
    ``(cross_weight, passes, moves)``.
    """
    services = sorted(assignment)
    requests = capacity or {}

    def move_delta(svc: str, dst: int) -> float:
        """Change in cross weight if ``svc`` moves from its node to ``dst``."""
        src = assignment[svc]
        delta = 0.0
        for other, weight in adj.get(svc, []):
            if assignment[other] == src:
                delta += weight
            elif assignment[other] == dst:
                delta -= weight
        return delta

    passes = 0
    moves = 0
    improved = True
    gap = abs(cross_weight / total_weight - target_f)
    while improved and passes < max_passes:
        improved = False
        passes += 1
        # Single-service moves.
        for svc in services:
            request = requests.get(svc, 0.0) if capacity is not None else 0.0
            for dst in range(n_nodes):
                if dst == assignment[svc]:
                    continue
                if capacity is not None:
                    assert node_capacity is not None  # enforced by solve()
                    if loads[dst] + request > node_capacity + _EPS:
                        continue
                delta = move_delta(svc, dst)
                new_gap = abs((cross_weight + delta) / total_weight - target_f)
                if new_gap < gap - _EPS:
                    loads[assignment[svc]] -= request
                    loads[dst] += request
                    assignment[svc] = dst
                    cross_weight += delta
                    gap = new_gap
                    improved = True
                    moves += 1
        # Pairwise swaps.
        for i, svc_a in enumerate(services):
            for svc_b in services[i + 1 :]:
                node_a, node_b = assignment[svc_a], assignment[svc_b]
                if node_a == node_b:
                    continue
                req_a = requests.get(svc_a, 0.0) if capacity is not None else 0.0
                req_b = requests.get(svc_b, 0.0) if capacity is not None else 0.0
                if capacity is not None:
                    assert node_capacity is not None  # enforced by solve()
                    if (
                        loads[node_a] - req_a + req_b > node_capacity + _EPS
                        or loads[node_b] - req_b + req_a > node_capacity + _EPS
                    ):
                        continue
                delta = move_delta(svc_a, node_b)
                assignment[svc_a] = node_b  # tentative, so move_delta sees it
                delta += move_delta(svc_b, node_a)
                assignment[svc_a] = node_a
                new_gap = abs((cross_weight + delta) / total_weight - target_f)
                if new_gap < gap - _EPS:
                    assignment[svc_a], assignment[svc_b] = node_b, node_a
                    loads[node_a] += req_b - req_a
                    loads[node_b] += req_a - req_b
                    cross_weight += delta
                    gap = new_gap
                    improved = True
                    moves += 1
    return cross_weight, passes, moves


def solve(
    edges: Sequence[Edge],
    services: Sequence[str],
    n_nodes: int,
    target_f: float,
    capacity: Dict[str, float] | None = None,
    seed: int = 0,
    node_capacity: float | None = None,
    restarts: int = 8,
    max_local_search_passes: int = 50,
) -> Solution:
    """Greedy edge-cut assignment targeting cross-node fraction ``target_f``.

    Per DESIGN §2.3: services are placed one at a time, each on the node that
    most reduces the gap between the current cut fraction and the target,
    respecting an optional requests-based capacity, then refined by a local
    search (single-service moves + pairwise swaps).  The placement *order* is
    instantiated as heaviest-incident-weight first plus seeded shuffled
    restarts (rather than a per-step argmin over all unplaced services — the
    restarts + local search close the same gap, and the acceptance verdict is
    what is pre-registered, not the search path).  Deterministic for a given
    seed.

    Args:
        edges: ``(src, dst, weight)`` inter-service edges; endpoints must be
            in ``services``; weights must be ≥ 0 with at least one > 0.
        services: All services to assign (isolated ones included).
        n_nodes: Number of nodes (assignment values are ``0 … n_nodes-1``).
        target_f: Target cross-node fraction in [0, 1].
        capacity: Optional per-service resource request (e.g. CPU millicores).
            Services missing from the dict request 0 — the kube-scheduler
            convention.  Requires ``node_capacity``.
        seed: RNG seed (restart shuffles + tie-breaks).
        node_capacity: Uniform per-node budget in the same unit as ``capacity``.
            A service that fits nowhere falls back to the least-loaded node
            and is counted in ``trace["capacityViolations"]``.
        restarts: Number of greedy restarts (≥ 1).
        max_local_search_passes: Safety cap on local-search sweeps per restart.

    Returns:
        A :class:`Solution`; ``accepted`` applies the pre-registered rule
        ``|achieved_f − target_f| ≤ 0.05`` (:data:`TARGET_TOLERANCE`).
    """
    if n_nodes < 1:
        raise ValueError(f"n_nodes must be >= 1, got {n_nodes}")
    if not services:
        raise ValueError("services must be non-empty")
    if not 0.0 <= target_f <= 1.0:
        raise ValueError(f"target_f must be in [0, 1], got {target_f}")
    if restarts < 1:
        raise ValueError(f"restarts must be >= 1, got {restarts}")
    if capacity is not None and (node_capacity is None or node_capacity <= 0):
        raise ValueError("capacity requires a positive node_capacity")

    usable = _usable_edges(edges, services)
    adj = _adjacency(usable)
    self_weight: Dict[str, float] = {}
    for src, dst, weight in usable:
        if src == dst:
            self_weight[src] = self_weight.get(src, 0.0) + weight
    total_weight = sum(weight for _, _, weight in usable)

    rng = random.Random(seed)
    incident_weight = {
        svc: sum(w for _, w in adj.get(svc, [])) + self_weight.get(svc, 0.0) for svc in services
    }
    heaviest_first = sorted(services, key=lambda s: (-incident_weight[s], s))

    best: Tuple[float, Dict[str, int], List[float], int, int, int] | None = None
    best_restart = 0
    restart_gaps: List[float] = []
    warm_start_gap: float | None = None

    # Deterministic warm-start (no-capacity case only): the fully-collapsed
    # assignment — every service on node 0 — is the closed-form optimum for
    # target_f = 0 on a connected graph (every edge intra-node => cut 0) and a
    # strong start for low targets.  Random / heaviest-first restarts reach it
    # only unreliably: collapsing from a spread start requires migrating every
    # service through cut-*increasing* single-moves, a local-minimum trap (the
    # hotelReservation tree topology hit f=0 from only ~22 % of seeds, so the
    # live M1b gate's 3-consecutive rule never converged there).  Seeded as a
    # candidate alongside the restarts; local search refines it toward the
    # target.  With capacity, all-on-one-node may be infeasible, so the
    # warm-start is skipped and the capacity-aware greedy restarts own it.
    if capacity is None and total_weight > 0:
        warm_assignment = {svc: 0 for svc in services}
        warm_loads = [0.0] * n_nodes
        warm_cross, warm_passes, warm_moves = _local_search(
            warm_assignment,
            warm_loads,
            adj,
            total_weight,
            0.0,
            n_nodes,
            target_f,
            capacity,
            node_capacity,
            max_local_search_passes,
        )
        warm_start_gap = abs(warm_cross / total_weight - target_f)
        best = (warm_start_gap, warm_assignment, warm_loads, 0, warm_passes, warm_moves)
        best_restart = -1  # sentinel: the collapsed warm-start won, not a restart

    for restart in range(restarts):
        order = heaviest_first if restart == 0 else rng.sample(list(services), len(services))
        assignment, loads, violations = _greedy_assign(
            order, adj, self_weight, n_nodes, target_f, capacity, node_capacity, rng
        )
        cross = sum(
            w for src, dst, w in usable if src != dst and assignment[src] != assignment[dst]
        )
        cross, passes, moves = _local_search(
            assignment,
            loads,
            adj,
            total_weight,
            cross,
            n_nodes,
            target_f,
            capacity,
            node_capacity,
            max_local_search_passes,
        )
        gap = abs(cross / total_weight - target_f)
        restart_gaps.append(round(gap, 6))
        if best is None or gap < best[0] - _EPS:
            best = (gap, assignment, loads, violations, passes, moves)
            best_restart = restart
        if best[0] <= _EPS:
            break

    assert best is not None  # restarts >= 1 guarantees at least one candidate
    _, assignment, loads, violations, passes, moves = best
    achieved = achieved_fraction(assignment, edges)
    trace: Dict[str, object] = {
        "method": "greedy-edge-cut + local-search",
        "seed": seed,
        "restartsRun": len(restart_gaps),
        "bestRestart": best_restart,
        "restartGaps": restart_gaps,
        "localSearchPasses": passes,
        "localSearchMoves": moves,
        "capacityViolations": violations,
        "warmStartGap": round(warm_start_gap, 6) if warm_start_gap is not None else None,
    }
    if capacity is not None:
        trace["nodeLoads"] = [round(load, 6) for load in loads]
    return Solution(
        assignment=assignment,
        achieved_f=achieved,
        target_f=target_f,
        accepted=abs(achieved - target_f) <= TARGET_TOLERANCE,
        trace=trace,
    )


# ──────────────────────────────────────────────────────────────────────
# Reachable-fraction enumeration (quantization study)
# ──────────────────────────────────────────────────────────────────────


def count_canonical_assignments(n_items: int, max_blocks: int) -> int:
    """Number of set partitions of ``n_items`` into at most ``max_blocks`` blocks.

    ``sum_{k=1..min(n, kmax)} S(n, k)`` (Stirling numbers of the second kind);
    this is exactly how many canonical assignments the exhaustive enumerator
    walks.  ``Bell(n)`` when ``max_blocks >= n_items``.
    """
    if n_items < 1 or max_blocks < 1:
        raise ValueError("n_items and max_blocks must be >= 1")
    # stirling[k] = S(row, k), built row by row.
    stirling = [0] * (n_items + 1)
    stirling[0] = 1  # S(0, 0)
    for row in range(1, n_items + 1):
        for k in range(min(row, n_items), 0, -1):
            stirling[k] = k * stirling[k] + stirling[k - 1]
        stirling[0] = 0
    return sum(stirling[1 : min(n_items, max_blocks) + 1])


def active_services(edges: Sequence[Edge]) -> List[str]:
    """Endpoints of positive-weight non-self edges — the services whose node
    choice can change the fraction.  Isolated services are free riders."""
    return sorted(
        {
            endpoint
            for src, dst, weight in edges
            if weight > 0 and src != dst
            for endpoint in (src, dst)
        }
    )


def enumeration_method(
    n_active: int, n_nodes: int, exhaustive_budget: int = DEFAULT_EXHAUSTIVE_BUDGET
) -> str:
    """``"exhaustive"`` when the canonical-assignment count fits the budget, else ``"sampled"``."""
    if n_nodes < 1:
        raise ValueError(f"n_nodes must be >= 1, got {n_nodes}")
    if n_active == 0:
        return "exhaustive"  # nothing to enumerate: the fraction is constant
    count = count_canonical_assignments(n_active, min(n_nodes, n_active))
    return "exhaustive" if count <= exhaustive_budget else "sampled"


def _iter_restricted_growth_strings(n_items: int, max_blocks: int) -> Iterable[List[int]]:
    """Yield every restricted-growth string of length ``n_items`` with values
    ``< max_blocks`` — one canonical representative per set partition into at
    most ``max_blocks`` blocks.  The yielded list is reused; copy if kept.
    """
    blocks = [0] * n_items
    while True:
        yield blocks
        index = n_items - 1
        while index > 0:
            prefix_max = max(blocks[:index])
            if blocks[index] <= prefix_max and blocks[index] + 1 <= max_blocks - 1:
                break
            index -= 1
        if index == 0:
            return
        blocks[index] += 1
        for j in range(index + 1, n_items):
            blocks[j] = 0


def enumerate_reachable(
    edges: Sequence[Edge],
    services: Sequence[str],
    n_nodes: int,
    samples: int = 20_000,
    seed: int = 0,
    exhaustive_budget: int = DEFAULT_EXHAUSTIVE_BUDGET,
) -> List[float]:
    """Achievable cross-node fractions for this graph on ``n_nodes`` nodes.

    Capacity-unaware (the analytical quantization study of WORKPLAN M1a).
    Node identity never matters to the fraction, so only the *partition* of
    the edge-incident services is enumerated:

    - **exhaustive** (canonical-assignment count ≤ ``exhaustive_budget``):
      walks every set partition into ≤ ``n_nodes`` blocks via
      restricted-growth strings — the exact reachable set.
    - **sampled** (count over budget): ``samples`` seeded random assignments
      plus :func:`solve` refinement toward the grid targets
      {0, 0.25, 0.5, 0.75, 1} — a reachable *subset*, not a census.

    Use :func:`enumeration_method` to know which branch applies.  Fractions
    are deduplicated at 9 decimals and returned sorted.
    """
    if n_nodes < 1:
        raise ValueError(f"n_nodes must be >= 1, got {n_nodes}")
    if samples < 0:
        raise ValueError(f"samples must be >= 0, got {samples}")
    usable = _usable_edges(edges, services)
    total_weight = sum(weight for _, _, weight in usable)
    active = active_services(usable)
    if not active:
        return [0.0]  # only self-edges: no placement can make anything cross

    index_of = {svc: i for i, svc in enumerate(active)}
    indexed = [(index_of[s], index_of[d], w) for s, d, w in usable if s != d]
    fractions: Set[float] = set()

    if enumeration_method(len(active), n_nodes, exhaustive_budget) == "exhaustive":
        for blocks in _iter_restricted_growth_strings(len(active), min(n_nodes, len(active))):
            cross = 0.0
            for i, j, weight in indexed:
                if blocks[i] != blocks[j]:
                    cross += weight
            fractions.add(round(cross / total_weight, _FRACTION_DECIMALS))
    else:
        rng = random.Random(seed)
        for _ in range(samples):
            assignment = [rng.randrange(n_nodes) for _ in active]
            cross = 0.0
            for i, j, weight in indexed:
                if assignment[i] != assignment[j]:
                    cross += weight
            fractions.add(round(cross / total_weight, _FRACTION_DECIMALS))
        for grid_target in (0.0, 0.25, 0.5, 0.75, 1.0):
            solution = solve(edges, services, n_nodes, grid_target, seed=seed)
            fractions.add(round(solution.achieved_f, _FRACTION_DECIMALS))
    return sorted(fractions)


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """The module's CLI surface (also exercised by tests)."""
    parser = argparse.ArgumentParser(
        prog="python -m chaosprobe.placement.fraction_solver",
        description=(
            "M1a fraction-targeting placement solver + reachable-set enumerator "
            "(design/00-DESIGN.md §2.3, 02-WORKPLAN.md M1a). Derives the weighted "
            "inter-service dependency graph from a run's summary.json, then either "
            "solves for a target cross-node fraction (--target) or enumerates the "
            "reachable fraction set for the given node count (--enumerate)."
        ),
    )
    parser.add_argument("--summary", required=True, help="path to a run's summary.json")
    parser.add_argument("--n-nodes", required=True, type=int, help="number of worker nodes")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--target", type=float, help="target cross-node fraction f in [0, 1]")
    mode.add_argument(
        "--enumerate",
        action="store_true",
        help="enumerate the reachable fraction set (quantization study)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=20_000,
        help="random samples when enumeration falls back to sampling (default 20000)",
    )
    parser.add_argument("--seed", type=int, default=0, help="RNG seed (default 0)")
    parser.add_argument(
        "--capacity-file",
        help="JSON file {service: resource request} enabling capacity-aware solving",
    )
    parser.add_argument(
        "--node-capacity",
        type=float,
        help="uniform per-node budget (same unit as --capacity-file; required with it)",
    )
    parser.add_argument(
        "--exhaustive-budget",
        type=int,
        default=DEFAULT_EXHAUSTIVE_BUDGET,
        help="canonical-assignment count above which --enumerate samples (default 1000000)",
    )
    return parser


def _print_enumeration(edges: List[Edge], services: List[str], args: argparse.Namespace) -> None:
    active = active_services(edges)
    method = enumeration_method(len(active), args.n_nodes, args.exhaustive_budget)
    started = time.perf_counter()
    reachable = enumerate_reachable(
        edges,
        services,
        args.n_nodes,
        samples=args.samples,
        seed=args.seed,
        exhaustive_budget=args.exhaustive_budget,
    )
    elapsed = time.perf_counter() - started
    if method == "exhaustive":
        count = count_canonical_assignments(len(active), min(args.n_nodes, len(active)))
        detail = f"set partitions into <={args.n_nodes} blocks; {count} canonical assignments"
    else:
        detail = f"{args.samples} seeded samples + solver refinement; NOT a census"
    print(f"\nReachable cross-node fractions at N={args.n_nodes}")
    print(f"  method: {method} ({detail})")
    print(f"  elapsed: {elapsed:.2f}s")
    print(f"  {len(reachable)} reachable fraction(s)")
    if len(reachable) <= 50:
        print("  " + ", ".join(f"{f:.3f}" for f in reachable))
    else:
        print(f"  range: {reachable[0]:.3f} .. {reachable[-1]:.3f} (list elided)")
    print(f"\n  {'target':>8}{'nearest':>10}{'|gap|':>8}{'within ±0.05':>14}")
    for grid_target in (0.0, 0.25, 0.5, 0.75, 1.0):
        nearest = min(reachable, key=lambda f: abs(f - grid_target))
        gap = abs(nearest - grid_target)
        verdict = "yes" if gap <= TARGET_TOLERANCE else "NO"
        print(f"  {grid_target:>8.2f}{nearest:>10.3f}{gap:>8.3f}{verdict:>14}")


def _print_solution(edges: List[Edge], services: List[str], args: argparse.Namespace) -> None:
    capacity: Dict[str, float] | None = None
    if args.capacity_file:
        with open(args.capacity_file) as fh:
            capacity = {str(k): float(v) for k, v in json.load(fh).items()}
    solution = solve(
        edges,
        services,
        args.n_nodes,
        args.target,
        capacity=capacity,
        seed=args.seed,
        node_capacity=args.node_capacity,
    )
    print(f"\nFraction-targeting solve at N={args.n_nodes}, target f={solution.target_f:.3f}")
    print(f"  {'service':<26}{'node':>6}")
    for svc in sorted(solution.assignment):
        print(f"  {svc:<26}{solution.assignment[svc]:>6}")
    gap = abs(solution.achieved_f - solution.target_f)
    print(f"\n  achieved f = {solution.achieved_f:.4f} (gap {gap:.4f})")
    verdict = "ACCEPTED" if solution.accepted else "REJECTED"
    print(f"  {verdict} (pre-registered rule: |achieved - target| <= {TARGET_TOLERANCE})")
    trace = solution.trace
    print(
        f"  trace: restarts={trace['restartsRun']} best={trace['bestRestart']} "
        f"local-search moves={trace['localSearchMoves']} "
        f"capacity violations={trace['capacityViolations']}"
    )


def main(argv: List[str] | None = None) -> None:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    edges, services = load_dependency_graph(args.summary)
    if not edges:
        raise SystemExit(f"no inter-service edges found in {args.summary}")
    print(f"Dependency graph: {len(services)} services, {len(edges)} edges (from {args.summary})")
    try:
        if args.enumerate:
            _print_enumeration(edges, services, args)
        else:
            _print_solution(edges, services, args)
    except ValueError as exc:  # invalid --target / --n-nodes / capacity combination
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":  # pragma: no cover
    main()
