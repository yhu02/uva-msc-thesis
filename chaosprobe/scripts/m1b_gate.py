#!/usr/bin/env python3
"""M1b live GO/NO-GO gate (decidable predicates).

Runs the full M1b exit-criteria gate against
a live cluster and emits the **committed verification artifact**
(per-phase, per-level,
per-attempt outcomes with timestamps and achieved values), plus a PASS/FAIL
summary line per criterion.

Phases
------
- **A — fraction gate.** For each target ``f`` in the level grid: solve
  (:func:`chaosprobe.placement.fraction_solver.solve` at ``n_nodes =
  len(--workers)``, solver node index *i* ↔ the *i*-th worker name), apply
  at r = 1 via the affinity engine, wait for the rollout, then recompute the
  achieved fraction from **live pods** with the shared
  :func:`~chaosprobe.placement.fraction_solver.achieved_fraction`.  An
  *attempt* is one full solve→apply→schedule→verify cycle from a restored
  (clean) app state; a level passes on **3 consecutive** in-tolerance
  (±0.05) attempts, the counter **resets on a miss**, and the level aborts
  as FAIL after 6 total attempts.
- **B — replication gate.** r = 3 **anti-affine** for all services
  simultaneously (every service's 3 ready replicas must occupy 3 distinct
  nodes — the explicit "schedulable at the pinned N" criterion), then r = 3
  **packed** on a capacity-feasible deterministic round-robin of services
  over the workers (:func:`packed_assignment`): each service's 3 replicas
  on ONE node, services distributed ACROSS nodes — the C2 per-service
  packing semantics.  Scheduling latencies are recorded.
- **C — capacity record.** Sum live ``resources.requests`` (cpu/memory) on
  the gate's worker nodes against their allocatable, per DESIGN §7.1; the
  criterion is ≥ 30 % headroom on both resources while the heaviest cell
  (r = 3) is still deployed.

After every ``engine.restore`` the gate waits for **quiescence**
(:func:`wait_for_quiescence`): all deployments ready, no pod-restart-count
change, and no new ``Unhealthy`` event for an uninterrupted
``--settle-seconds`` window (capped by ``--settle-timeout``) — so the next
apply never lands on a cluster still churning from the previous arm (the
cascading 1 s gRPC readiness-probe-timeout signature).  When an apply or
verify step times out or fails, a diagnostics snapshot
(:func:`collect_diagnostics`) is embedded in the artifact: per-deployment
ready/desired counts, pod phases/restarts/waiting reasons, and the last
Unhealthy / FailedScheduling events.

``--restore-on-exit`` always restores default scheduling — also on
exception or Ctrl-C — and the artifact is written in the same ``finally``,
so an aborted gate still leaves its partial record.

Usage
-----
    uv run python scripts/m1b_gate.py -n online-boutique \\
        --summary results/<run>/summary.json \\
        --workers worker1,worker2,...,worker8 \\
        [-o m1b-gate-artifact.json] [--restore-on-exit]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import chaosprobe.placement.fraction_solver as fs
from chaosprobe.metrics.resources import parse_cpu_quantity, parse_memory_quantity

# The quiescence barrier lives in ``chaosprobe.orchestrator.quiescence`` so
# the session driver can reuse it; the gate re-exports the original names
# (tests and callers keep importing them from this script).
from chaosprobe.orchestrator.quiescence import (
    DEFAULT_SETTLE_SECONDS,
    DEFAULT_SETTLE_TIMEOUT,
    wait_for_quiescence,
)
from chaosprobe.orchestrator.quiescence import event_time as _event_time
from chaosprobe.orchestrator.quiescence import (  # noqa: F401 — re-exported
    quiescence_snapshot as _quiescence_snapshot,
)
from chaosprobe.placement import affinity_engine as engine

#: Artifact schema identifier (bump on breaking shape changes).
#: adds per-restore ``settle`` records, failure ``diagnostics``
#: snapshots, and the phase-B ``packedAssignmentMethod`` field.
SCHEMA = "chaosprobe/m1b-gate-artifact/v2"

#: M1b capacity criterion: ≥30 % headroom at the heaviest cell.
HEADROOM_FLOOR = 0.30

#: Default fraction level grid (DESIGN §2.3, Knob A).
DEFAULT_LEVELS = (0.0, 0.25, 0.5, 0.75, 1.0)

#: How phase B's packed assignment is built (recorded in the artifact).
PACKED_ASSIGNMENT_METHOD = "round-robin"

#: Event reasons captured into a failure diagnostics snapshot.
_DIAGNOSTIC_EVENT_REASONS = ("FailedScheduling", "Unhealthy")

#: Newest-first cap on diagnostic events kept in the artifact.
_DIAGNOSTIC_EVENT_LIMIT = 10


@dataclass
class GateConfig:
    """Knobs of the gate run (defaults are the standard values)."""

    levels: Tuple[float, ...] = DEFAULT_LEVELS
    tolerance: float = fs.TARGET_TOLERANCE
    consecutive: int = 3
    max_attempts: int = 6
    seed: int = 0
    timeout: float = 300.0
    settle_seconds: float = DEFAULT_SETTLE_SECONDS
    settle_timeout: float = DEFAULT_SETTLE_TIMEOUT


def _now() -> str:
    """UTC timestamp for the artifact."""
    return datetime.now(timezone.utc).isoformat()


def _iso(stamp: Optional[datetime]) -> Optional[str]:
    """ISO timestamp for the artifact, passing ``None`` through."""
    return stamp.isoformat() if stamp else None


def parse_workers(spec: str) -> List[str]:
    """Ordered worker names from a comma-separated ``--workers`` value.

    Order matters: solver node index *i* maps to the *i*-th name.
    """
    workers = [w.strip() for w in spec.split(",") if w.strip()]
    if not workers:
        raise ValueError("--workers must list at least one node name")
    if len(set(workers)) != len(workers):
        raise ValueError(f"--workers contains duplicate node names: {spec}")
    return workers


def parse_levels(spec: str) -> Tuple[float, ...]:
    """Fraction level grid from a comma-separated ``--levels`` value."""
    try:
        levels = tuple(float(part) for part in spec.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError(f"--levels must be comma-separated floats: {spec}") from exc
    if not levels or any(not 0.0 <= level <= 1.0 for level in levels):
        raise ValueError(f"--levels values must be in [0, 1]: {spec}")
    return levels


# ──────────────────────────────────────────────────────────────────────
# Failure diagnostics (the quiescence barrier is imported above)
# ──────────────────────────────────────────────────────────────────────


def collect_diagnostics(api: engine.K8sApi, namespace: str) -> Dict[str, Any]:
    """Failure snapshot for the artifact when an apply/verify times out or fails.

    Captures per-deployment ready/desired counts, every pod's phase, node,
    restart count and container waiting reasons, and the newest
    :data:`_DIAGNOSTIC_EVENT_LIMIT` ``Unhealthy`` / ``FailedScheduling``
    events — so the failure is diagnosable from the artifact alone instead
    of post-hoc kubectl archaeology.
    """
    deployments: Dict[str, Dict[str, int]] = {}
    for dep in api.apps.list_namespaced_deployment(namespace).items:
        status = dep.status
        deployments[dep.metadata.name] = {
            "desired": dep.spec.replicas if dep.spec.replicas is not None else 1,
            "ready": (status.ready_replicas or 0) if status else 0,
        }

    pods: List[Dict[str, Any]] = []
    for pod in api.core.list_namespaced_pod(namespace).items:
        statuses = (pod.status.container_statuses or []) if pod.status else []
        waiting = sorted(
            {
                cs.state.waiting.reason
                for cs in statuses
                if cs.state and cs.state.waiting and cs.state.waiting.reason
            }
        )
        pods.append(
            {
                "pod": pod.metadata.name,
                "phase": (pod.status.phase or "") if pod.status else "",
                "node": pod.spec.node_name if pod.spec else None,
                "restarts": sum(cs.restart_count or 0 for cs in statuses),
                "waitingReasons": waiting,
            }
        )

    events: List[Dict[str, Any]] = []
    for event in api.core.list_namespaced_event(namespace).items:
        if event.reason not in _DIAGNOSTIC_EVENT_REASONS:
            continue
        involved = event.involved_object
        events.append(
            {
                "reason": event.reason,
                "object": f"{involved.kind}/{involved.name}" if involved else "",
                "count": event.count or 1,
                "message": event.message or "",
                "lastSeenAt": _iso(_event_time(event)),
            }
        )
    events.sort(key=lambda entry: entry["lastSeenAt"] or "", reverse=True)

    return {
        "capturedAt": _now(),
        "deployments": deployments,
        "pods": pods,
        "events": events[:_DIAGNOSTIC_EVENT_LIMIT],
    }


# ──────────────────────────────────────────────────────────────────────
# Phase A — fraction gate (3-consecutive state machine per level)
# ──────────────────────────────────────────────────────────────────────


#: Distinct solver seeds tried per attempt before applying (first accepted
#: solution wins; best-gap fallback when none is accepted).  The solver's
#: own ``restarts=8`` are *within-seed* restarts; tree-shaped graphs (the
#: hotelReservation static topology) need a between-seed sweep too — the
#: same documented remedy the topology unit test uses (a single seed can
#: stall in a 1/16 local optimum at f=0).  Solves are in-memory and cheap;
#: a live apply of a solver-rejected assignment can never pass the gate.
SOLVER_SEED_SWEEP = 5


def solve_with_seed_sweep(
    edges: List[fs.Edge],
    services: List[str],
    n_nodes: int,
    level: float,
    base_seed: int,
    sweep: int = SOLVER_SEED_SWEEP,
) -> Tuple[fs.Solution, List[int]]:
    """Solve at ``level``, sweeping up to ``sweep`` seeds from ``base_seed``.

    Returns the first *accepted* solution (±0.05 rule), or —
    when every seed misses — the best-gap solution, plus the list of seeds
    actually tried.  Seeds step by :data:`SOLVER_SEED_SWEEP` so consecutive
    attempts never re-try each other's seeds.
    """
    best: Optional[fs.Solution] = None
    tried: List[int] = []
    for offset in range(sweep):
        seed = base_seed + offset
        tried.append(seed)
        solution = fs.solve(edges, services, n_nodes, level, seed=seed)
        if best is None or abs(solution.achieved_f - level) < abs(best.achieved_f - level):
            best = solution
        if solution.accepted:
            return solution, tried
    assert best is not None  # sweep >= 1 always sets it
    return best, tried


def run_attempt(
    api: engine.K8sApi,
    namespace: str,
    edges: List[fs.Edge],
    services: List[str],
    workers: Sequence[str],
    level: float,
    attempt_no: int,
    cfg: GateConfig,
) -> Dict[str, Any]:
    """One full solve→apply→schedule→verify cycle at one f-level.

    Starts from a restored (unpinned, single-replica) app state — the
    "from a clean app deploy" start — and judges the attempt
    on the fraction recomputed from live pods, never the solver's claim.
    """
    record: Dict[str, Any] = {"attempt": attempt_no, "target": level, "startedAt": _now()}
    engine.restore(api, namespace, timeout=cfg.timeout)
    record["settle"] = wait_for_quiescence(
        api, namespace, settle_seconds=cfg.settle_seconds, timeout=cfg.settle_timeout
    )
    solution, seeds_tried = solve_with_seed_sweep(
        edges,
        services,
        len(workers),
        level,
        base_seed=cfg.seed + (attempt_no - 1) * SOLVER_SEED_SWEEP,
    )
    assignment = {svc: workers[idx] for svc, idx in solution.assignment.items()}
    record["assignment"] = assignment
    record["solverAchievedF"] = round(solution.achieved_f, 6)
    record["solverAccepted"] = solution.accepted
    record["solverSeedsTried"] = seeds_tried

    applied = engine.apply_placement(
        api, namespace, assignment, 1, engine.MODE_PACKED, workers, timeout=cfg.timeout
    )
    record["schedulingLatencySeconds"] = round(applied.duration_seconds, 3)
    record["pendingDeployments"] = applied.pending

    live = engine.live_service_nodes(api, namespace, sorted(assignment))
    unverifiable = sorted(svc for svc, nodes in live.items() if len(nodes) != 1)
    if unverifiable:
        record["liveAchievedF"] = None
        record["inTolerance"] = False
        record["reason"] = "services without exactly one ready node: " + ", ".join(unverifiable)
    else:
        achieved = fs.achieved_fraction({svc: nodes[0] for svc, nodes in live.items()}, edges)
        record["liveAchievedF"] = round(achieved, 6)
        record["gap"] = round(abs(achieved - level), 6)
        record["inTolerance"] = abs(achieved - level) <= cfg.tolerance
    if applied.pending or unverifiable:
        record["diagnostics"] = collect_diagnostics(api, namespace)
    record["finishedAt"] = _now()
    return record


def run_level(
    api: engine.K8sApi,
    namespace: str,
    edges: List[fs.Edge],
    services: List[str],
    workers: Sequence[str],
    level: float,
    cfg: GateConfig,
) -> Dict[str, Any]:
    """The per-level state machine: pass on ``cfg.consecutive`` consecutive
    in-tolerance attempts (counter resets on a miss), abort as FAIL after
    ``cfg.max_attempts`` total attempts."""
    attempts: List[Dict[str, Any]] = []
    consecutive = 0
    best_streak = 0
    passed = False
    for attempt_no in range(1, cfg.max_attempts + 1):
        record = run_attempt(api, namespace, edges, services, workers, level, attempt_no, cfg)
        consecutive = consecutive + 1 if record["inTolerance"] else 0
        best_streak = max(best_streak, consecutive)
        record["consecutiveAfter"] = consecutive
        attempts.append(record)
        if consecutive >= cfg.consecutive:
            passed = True
            break
    return {
        "target": level,
        "passed": passed,
        "attempts": attempts,
        "totalAttempts": len(attempts),
        "bestConsecutive": best_streak,
        "requiredConsecutive": cfg.consecutive,
        "maxAttempts": cfg.max_attempts,
    }


def run_phase_a(
    api: engine.K8sApi,
    namespace: str,
    edges: List[fs.Edge],
    services: List[str],
    workers: Sequence[str],
    cfg: GateConfig,
) -> Dict[str, Any]:
    """Phase A: the solver gate across the whole f-level grid."""
    levels = [
        run_level(api, namespace, edges, services, workers, level, cfg) for level in cfg.levels
    ]
    return {
        "tolerance": cfg.tolerance,
        "nNodes": len(workers),
        "levels": levels,
        "passed": all(level["passed"] for level in levels),
    }


# ──────────────────────────────────────────────────────────────────────
# Phase B — replication gate (r = 3 anti-affine, then r = 3 packed)
# ──────────────────────────────────────────────────────────────────────


def packed_assignment(services: Sequence[str], workers: Sequence[str]) -> Dict[str, str]:
    """Capacity-feasible packed assignment: sorted service *i* → worker *i mod W*.

    Phase B's packed arm asserts the C2 **per-service** packing semantics —
    every service's 3 replicas co-scheduled on ONE node — with services
    distributed ACROSS nodes, *not* all services on one node.  The solver's
    f = 0 assignment it replaces satisfied f = 0 by stacking all services on
    a single worker, which at r = 3 needs ~3× the whole app's requests on
    one node — unschedulable by arithmetic on this cluster's 4 GiB workers
    (the observed live-run failure).

    A deterministic round-robin is used instead of
    ``fs.solve(..., capacity=...)`` because the solver's capacity support
    takes one *uniform* ``node_capacity`` and optimises the cut fraction,
    not balance — at f = 0 it fills a node to its budget before spilling,
    the worst shape for a churn-prone cluster.  Round-robin minimises the
    per-node service count (⌈S/W⌉), needs no live capacity reads, and
    :func:`engine.verify_placement` still proves the packing (each
    service's replicas on exactly its pinned node) from live pods.

    Thin wrapper over :func:`affinity_engine.packed_round_robin` — the single
    source the live session orchestrator (H3) shares with this gate.
    """
    return engine.packed_round_robin(services, workers)


def run_phase_b(
    api: engine.K8sApi,
    namespace: str,
    edges: List[fs.Edge],
    services: List[str],
    workers: Sequence[str],
    cfg: GateConfig,
) -> Dict[str, Any]:
    """Phase B: r = 3 anti-affine for all services (3 distinct nodes each —
    the explicit M1b schedulability criterion), then r = 3 packed on the
    capacity-feasible round-robin assignment (each service on 1 node,
    services spread over the workers).  Each arm starts from a restored,
    **quiescent** state; a failing arm embeds a diagnostics snapshot.
    Leaves the packed r = 3 state deployed so Phase C records capacity at
    the heaviest cell."""
    out: Dict[str, Any] = {}

    engine.restore(api, namespace, timeout=cfg.timeout)
    settle = wait_for_quiescence(
        api, namespace, settle_seconds=cfg.settle_seconds, timeout=cfg.settle_timeout
    )
    applied = engine.apply_placement(
        api, namespace, None, 3, engine.MODE_ANTI_AFFINE, workers, timeout=cfg.timeout
    )
    verification = engine.verify_placement(api, namespace, 3, engine.MODE_ANTI_AFFINE)
    out["antiAffine"] = {
        "appliedAt": _now(),
        "settle": settle,
        "services": applied.applied,
        "pendingDeployments": applied.pending,
        "schedulingLatencySeconds": round(applied.duration_seconds, 3),
        "verification": verification.to_dict(),
        "passed": verification.passed,
    }
    if applied.pending or not verification.passed:
        out["antiAffine"]["diagnostics"] = collect_diagnostics(api, namespace)

    engine.restore(api, namespace, timeout=cfg.timeout)
    settle = wait_for_quiescence(
        api, namespace, settle_seconds=cfg.settle_seconds, timeout=cfg.settle_timeout
    )
    assignment = packed_assignment(services, workers)
    applied = engine.apply_placement(
        api, namespace, assignment, 3, engine.MODE_PACKED, workers, timeout=cfg.timeout
    )
    packed_verification = engine.verify_placement(api, namespace, 3, engine.MODE_PACKED)
    out["packed"] = {
        "appliedAt": _now(),
        "settle": settle,
        "assignment": assignment,
        "packedAssignmentMethod": PACKED_ASSIGNMENT_METHOD,
        "achievedF": round(fs.achieved_fraction(assignment, edges), 6),
        "pendingDeployments": applied.pending,
        "schedulingLatencySeconds": round(applied.duration_seconds, 3),
        "verification": packed_verification.to_dict(),
        "passed": packed_verification.passed,
    }
    if applied.pending or not packed_verification.passed:
        out["packed"]["diagnostics"] = collect_diagnostics(api, namespace)

    out["passed"] = bool(out["antiAffine"]["passed"] and out["packed"]["passed"])
    return out


# ──────────────────────────────────────────────────────────────────────
# Phase C — capacity record (DESIGN §7.1 method)
# ──────────────────────────────────────────────────────────────────────


def _pod_requests(pod: Any) -> Tuple[int, int]:
    """Summed container ``resources.requests`` of one pod (cpu m, memory B)."""
    cpu_m = 0
    mem_b = 0
    for container in pod.spec.containers or []:
        requests = container.resources.requests if container.resources else None
        if not requests:
            continue
        cpu_m += int(parse_cpu_quantity(requests.get("cpu", "0")))
        mem_b += parse_memory_quantity(requests.get("memory", "0"))
    return cpu_m, mem_b


def run_phase_c(api: engine.K8sApi, namespace: str, workers: Sequence[str]) -> Dict[str, Any]:
    """Phase C: live request sums vs per-node allocatable on the workers.

    Records both the app namespace's request sums (the DESIGN §7.1 figure)
    and the all-namespace sums actually scheduled on the workers — the
    headroom criterion (≥ 30 % on cpu *and* memory) uses the latter, since
    that is the room the scheduler really has left.
    """
    worker_set = set(workers)
    totals = {"cpuMillicores": 0, "memoryBytes": 0}
    app_totals = {"cpuMillicores": 0, "memoryBytes": 0}
    for pod in api.core.list_pod_for_all_namespaces().items:
        node = pod.spec.node_name if pod.spec else None
        phase = (pod.status.phase or "") if pod.status else ""
        if node not in worker_set or phase in ("Succeeded", "Failed"):
            continue
        cpu_m, mem_b = _pod_requests(pod)
        totals["cpuMillicores"] += cpu_m
        totals["memoryBytes"] += mem_b
        if pod.metadata.namespace == namespace:
            app_totals["cpuMillicores"] += cpu_m
            app_totals["memoryBytes"] += mem_b

    per_node: Dict[str, Dict[str, int]] = {}
    alloc_cpu = 0
    alloc_mem = 0
    for node in api.core.list_node().items:
        if node.metadata.name not in worker_set:
            continue
        alloc = node.status.allocatable or {}
        cpu_m = int(parse_cpu_quantity(alloc.get("cpu", "0")))
        mem_b = parse_memory_quantity(alloc.get("memory", "0"))
        per_node[node.metadata.name] = {"cpuMillicores": cpu_m, "memoryBytes": mem_b}
        alloc_cpu += cpu_m
        alloc_mem += mem_b

    missing = sorted(worker_set - set(per_node))
    headroom_cpu = 1.0 - totals["cpuMillicores"] / alloc_cpu if alloc_cpu else 0.0
    headroom_mem = 1.0 - totals["memoryBytes"] / alloc_mem if alloc_mem else 0.0
    passed = not missing and headroom_cpu >= HEADROOM_FLOOR and headroom_mem >= HEADROOM_FLOOR
    return {
        "recordedAt": _now(),
        "appNamespaceRequests": app_totals,
        "allNamespaceRequestsOnWorkers": totals,
        "allocatablePerWorker": per_node,
        "missingWorkers": missing,
        "headroom": {"cpu": round(headroom_cpu, 4), "memory": round(headroom_mem, 4)},
        "headroomFloor": HEADROOM_FLOOR,
        "passed": passed,
    }


# ──────────────────────────────────────────────────────────────────────
# Orchestration + artifact
# ──────────────────────────────────────────────────────────────────────


def summary_lines(artifact: Dict[str, Any]) -> List[str]:
    """One PASS/FAIL line per criterion."""
    lines: List[str] = []
    phase_a = artifact.get("phaseA")
    if phase_a:
        for level in phase_a["levels"]:
            verdict = "PASS" if level["passed"] else "FAIL"
            lines.append(
                f"{verdict}  phase-A f={level['target']:.2f}  "
                f"best streak {level['bestConsecutive']}/{level['requiredConsecutive']} "
                f"in {level['totalAttempts']} attempt(s)"
            )
    phase_b = artifact.get("phaseB")
    if phase_b:
        for key, label in (("antiAffine", "anti-affine"), ("packed", "packed     ")):
            block = phase_b[key]
            verdict = "PASS" if block["passed"] else "FAIL"
            services = block["verification"]["services"]
            ok = sum(1 for svc in services if svc["ok"])
            lines.append(
                f"{verdict}  phase-B r=3 {label}  {ok}/{len(services)} services verified "
                f"(latency {block['schedulingLatencySeconds']:.1f}s)"
            )
    phase_c = artifact.get("phaseC")
    if phase_c:
        verdict = "PASS" if phase_c["passed"] else "FAIL"
        headroom = phase_c["headroom"]
        lines.append(
            f"{verdict}  phase-C capacity  headroom cpu {headroom['cpu']:.0%} "
            f"memory {headroom['memory']:.0%} (floor {HEADROOM_FLOOR:.0%})"
        )
    verdict = "PASS" if artifact.get("passed") else "FAIL"
    lines.append(f"OVERALL: {verdict}")
    return lines


def build_parser() -> argparse.ArgumentParser:
    """The gate's CLI surface (also exercised by tests)."""
    parser = argparse.ArgumentParser(
        description=(
            "M1b live GO/NO-GO gate (M1b exit criteria). "
            "Phase A: solver fraction gate at r=1 (3 consecutive in-tolerance "
            "attempts per f-level, abort after 6). Phase B: r=3 anti-affine "
            "(3 distinct nodes per service) then r=3 packed (each service on 1 "
            "node, services round-robined across the workers). Phase C: live "
            "request sums vs allocatable (>=30% headroom). Every restore is "
            "followed by a quiescence barrier before the next apply. Emits "
            "the committed verification artifact + PASS/FAIL summary lines."
        ),
    )
    parser.add_argument("-n", "--namespace", default="online-boutique", help="app namespace")
    graph_source = parser.add_mutually_exclusive_group(required=True)
    graph_source.add_argument(
        "--summary",
        help="summary.json supplying the weighted dependency graph (the enumerator's graph)",
    )
    graph_source.add_argument(
        "--topology",
        help=(
            "static topology.json supplying the dependency graph (uniform weights; "
            "the M2 stand-in for workloads with no measured summary — DESIGN §7, "
            "e.g. scenarios/hotel-reservation/topology.json)"
        ),
    )
    parser.add_argument(
        "--workers",
        required=True,
        help="ordered comma-separated worker node names (solver index i -> i-th name)",
    )
    parser.add_argument(
        "--levels",
        default=",".join(str(level) for level in DEFAULT_LEVELS),
        help="comma-separated target fractions (default: the standard grid)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=fs.TARGET_TOLERANCE,
        help="per-level acceptance tolerance (default: 0.05)",
    )
    parser.add_argument(
        "--consecutive", type=int, default=3, help="required consecutive in-tolerance attempts"
    )
    parser.add_argument(
        "--max-attempts", type=int, default=6, help="abort a level as FAIL after this many"
    )
    parser.add_argument("--seed", type=int, default=0, help="base solver seed (default 0)")
    parser.add_argument(
        "--timeout", type=float, default=300.0, help="per-apply rollout timeout (s)"
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=DEFAULT_SETTLE_SECONDS,
        help="quiescence window after each restore: all deployments ready, no "
        "pod-restart change, no new Unhealthy event for this long before the "
        "next apply (default 60)",
    )
    parser.add_argument(
        "--settle-timeout",
        type=float,
        default=DEFAULT_SETTLE_TIMEOUT,
        help="overall cap on each quiescence wait (s); on timeout the gate "
        "proceeds and records the unsettled state in the artifact (default 300)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="m1b-gate-artifact.json",
        help="artifact path (default m1b-gate-artifact.json)",
    )
    parser.add_argument(
        "--restore-on-exit",
        action="store_true",
        help="always restore default scheduling on exit (also on exception/SIGINT)",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    """Run the gate; returns 0 on overall PASS, 1 otherwise."""
    args = build_parser().parse_args(argv)
    try:
        workers = parse_workers(args.workers)
        levels = parse_levels(args.levels)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    cfg = GateConfig(
        levels=levels,
        tolerance=args.tolerance,
        consecutive=args.consecutive,
        max_attempts=args.max_attempts,
        seed=args.seed,
        timeout=args.timeout,
        settle_seconds=args.settle_seconds,
        settle_timeout=args.settle_timeout,
    )
    graph_path = args.summary or args.topology
    if args.summary:
        edges, services = fs.load_dependency_graph(args.summary)
    else:
        edges, services = fs.load_static_topology(args.topology)
    if not edges:
        raise SystemExit(f"no inter-service edges found in {graph_path}")

    api = engine.K8sApi.from_cluster()
    artifact: Dict[str, Any] = {
        "schema": SCHEMA,
        "startedAt": _now(),
        "namespace": args.namespace,
        "workers": workers,
        "summary": args.summary,
        "topology": args.topology,
        "config": {
            "levels": list(levels),
            "tolerance": cfg.tolerance,
            "consecutive": cfg.consecutive,
            "maxAttempts": cfg.max_attempts,
            "seed": cfg.seed,
            "timeoutSeconds": cfg.timeout,
            "settleSeconds": cfg.settle_seconds,
            "settleTimeoutSeconds": cfg.settle_timeout,
            # An "attempt" starts from engine.restore() — default scheduling,
            # single replica — followed by the quiescence barrier; not a full
            # app redeploy.  Recorded for the "from a clean app deploy" term.
            "attemptProtocol": ("restore-to-default,quiesce,solve,apply(r=1),schedule,verify-live"),
        },
    }
    started = time.monotonic()
    try:
        artifact["phaseA"] = run_phase_a(api, args.namespace, edges, services, workers, cfg)
        artifact["phaseB"] = run_phase_b(api, args.namespace, edges, services, workers, cfg)
        artifact["phaseC"] = run_phase_c(api, args.namespace, workers)
        artifact["passed"] = bool(
            artifact["phaseA"]["passed"]
            and artifact["phaseB"]["passed"]
            and artifact["phaseC"]["passed"]
        )
    except BaseException as exc:  # record + restore even on Ctrl-C / SystemExit
        artifact["aborted"] = repr(exc)
        artifact["passed"] = False
        raise
    finally:
        if args.restore_on_exit:
            try:
                engine.restore(api, args.namespace, timeout=cfg.timeout)
            except Exception as exc:  # never mask the original failure
                print(f"WARNING: restore-on-exit failed: {exc}", file=sys.stderr)
        artifact["finishedAt"] = _now()
        artifact["elapsedSeconds"] = round(time.monotonic() - started, 1)
        with open(args.output, "w") as fh:
            json.dump(artifact, fh, indent=2)
        print(f"\nGate artifact written to {args.output}")
        for line in summary_lines(artifact):
            print(line)
    return 0 if artifact["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
