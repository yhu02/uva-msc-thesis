"""v2 complete-block session driver (M2 plumbing for the C1–C3 campaigns).

Implements the pre-registered session design of
``v2-design/01-PREREGISTRATION.md`` §Session design on top of the v1
iteration pipeline: ``chaosprobe run --v2-levels ...`` replaces the v1
named-strategy axis with **solver-targeted cross-node-fraction conditions**.
Every session is a complete block visiting all requested f-levels in a
randomized order drawn from ``--v2-order-seed`` (recorded, per the
pre-registration — v1 fixed the order, making order effects constant but
unmeasurable), with each level's placement computed by
:mod:`chaosprobe.placement.fraction_solver` under ``--v2-solver-seed`` and
realized through the replica-level affinity engine
(:mod:`chaosprobe.placement.affinity_engine`, r ∈ {1, 3} ×
{packed, anti-affine} — the WORKPLAN C1/C2 cells).

Each condition rides the SAME per-strategy iteration pipeline as a v1
strategy (fault injection, every collector including the conntrack prober,
taint/doctor metadata): ``strategy_runner._apply_placement`` dispatches to
:func:`apply_condition` when the :class:`~chaosprobe.orchestrator.strategy_runner.RunContext`
carries a session, and ``strategy_runner._run_iterations`` calls
:func:`annotate_iteration` after every iteration so the per-iteration live
achieved fraction (recomputed from the recorded ``podPlacements``, never the
solver's claim) lands in the session metadata and the pre-registered
rejection rule (|live − target| > 0.05) taints — never silently drops —
out-of-tolerance iterations.

Between conditions the driver restores default scheduling
(:func:`affinity_engine.restore`) and waits for namespace quiescence
(:func:`chaosprobe.orchestrator.quiescence.wait_for_quiescence`, the M1b
barrier) so a condition never starts on a cluster still churning from the
previous one.

**A/A convenience:** an A/A pair is simply two runs with identical
``--v2-*`` arguments *including* ``--v2-solver-seed`` (identical placements
per level); ``--v2-order-seed`` may differ between the two runs (the visit
order may differ, the placements do not).  No special mode exists.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import click

import chaosprobe.placement.fraction_solver as fs
from chaosprobe.config.topology import ServiceRoute
from chaosprobe.orchestrator import quiescence
from chaosprobe.placement import affinity_engine as engine

#: Pre-registered f-level acceptance tolerance (single source: the solver's).
TOLERANCE = fs.TARGET_TOLERANCE

#: Default per-apply rollout timeout (matches the M1b gate's default).
DEFAULT_ROLLOUT_TIMEOUT = 300.0

#: Pinned-cell assignment strategies. The V2-H1 dose-response sweep needs the
#: fraction solver (it *is* the f knob); V2-H3 uses the capacity-feasible
#: round-robin packing registered in the pre-registration (§V2-H3 packed-cell
#: semantics) — f is irrelevant to the replication-rescue design.
PACKED_ASSIGNMENT_SOLVER = "solver"
PACKED_ASSIGNMENT_ROUND_ROBIN = "round-robin"
PACKED_ASSIGNMENTS = (PACKED_ASSIGNMENT_SOLVER, PACKED_ASSIGNMENT_ROUND_ROBIN)


@dataclass(frozen=True)
class V2Condition:
    """One f-level of the complete block, named for the strategy pipeline."""

    name: str
    target_f: float


@dataclass
class V2Session:
    """Everything one complete-block session needs, plus its growing record.

    ``conditions`` is the block in its randomized *applied* order;
    ``per_level`` accumulates one record per executed condition (the
    ``v2Session.perLevel`` entries of ``summary.json``).
    """

    namespace: str
    levels: Tuple[float, ...]
    conditions: List[V2Condition]
    order_seed: int
    solver_seed: int
    replicas: int
    mode: str
    workers: Tuple[str, ...]
    edges: List[fs.Edge]
    services: List[str]
    api: engine.K8sApi
    settle_seconds: float = quiescence.DEFAULT_SETTLE_SECONDS
    settle_timeout: float = quiescence.DEFAULT_SETTLE_TIMEOUT
    rollout_timeout: float = DEFAULT_ROLLOUT_TIMEOUT
    packed_assignment: str = PACKED_ASSIGNMENT_SOLVER
    per_level: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def pinned(self) -> bool:
        """True when the (r, mode) cell pins each service to one node.

        Mirrors the affinity engine's semantics: r = 1 always pins (the two
        modes are physically identical with a single replica), and r = 3
        packed pins; only r = 3 anti-affine lets the scheduler choose, so
        no per-service node — and no live cross-node fraction — exists.
        """
        return self.replicas == 1 or self.mode == engine.MODE_PACKED

    def condition(self, name: str) -> Optional[V2Condition]:
        """The condition with this pipeline name, or ``None``."""
        for cond in self.conditions:
            if cond.name == name:
                return cond
        return None


# ──────────────────────────────────────────────────────────────────────
# CLI-surface parsing (pure; exercised by the run command's validation)
# ──────────────────────────────────────────────────────────────────────


def condition_name(level: float) -> str:
    """Pipeline-safe condition name for an f-level (``0.25`` → ``f-025``).

    The name is appended to ChaosEngine resource names, so it must stay a
    DNS-1123-safe fragment — no dots.
    """
    return f"f-{int(round(level * 100)):03d}"


def parse_levels(spec: str) -> Tuple[float, ...]:
    """The complete-block f-level grid from a comma-separated ``--v2-levels``.

    Levels must be floats in [0, 1]; duplicates (including levels closer
    than the 1%-resolution condition name can distinguish) are rejected —
    a complete block visits each level once.
    """
    tokens = [token.strip() for token in spec.split(",") if token.strip()]
    if not tokens:
        raise ValueError(f"--v2-levels must list at least one fraction: '{spec}'")
    levels: List[float] = []
    for token in tokens:
        try:
            level = float(token)
        except ValueError as exc:
            raise ValueError(f"--v2-levels entry '{token}' is not a number") from exc
        if not 0.0 <= level <= 1.0:
            raise ValueError(f"--v2-levels entry '{token}' is outside [0, 1]")
        levels.append(level)
    names = [condition_name(level) for level in levels]
    if len(set(names)) != len(names):
        raise ValueError(
            f"--v2-levels contains duplicate or indistinguishable levels "
            f"(<1% apart): '{spec}' — a complete block visits each level once"
        )
    return tuple(levels)


def parse_workers(spec: str) -> Tuple[str, ...]:
    """Ordered worker names from a comma-separated ``--v2-workers``.

    Order matters: solver node index *i* maps to the *i*-th name (the same
    convention as the M1b gate's ``--workers``).
    """
    workers = tuple(worker.strip() for worker in spec.split(",") if worker.strip())
    if not workers:
        raise ValueError(f"--v2-workers must list at least one node name: '{spec}'")
    if len(set(workers)) != len(workers):
        raise ValueError(f"--v2-workers contains duplicate node names: '{spec}'")
    return workers


def ordered_conditions(levels: Sequence[float], order_seed: int) -> List[V2Condition]:
    """The complete block in its randomized applied order.

    Deterministic for a given ``order_seed`` — the order is recorded in
    ``v2Session.orderApplied`` so Page's L (V2-H1) can model it.
    """
    block = [V2Condition(name=condition_name(level), target_f=level) for level in levels]
    rng = random.Random(order_seed)
    return rng.sample(block, len(block))


def edges_from_routes(
    service_routes: Sequence[ServiceRoute], services: Sequence[str]
) -> List[fs.Edge]:
    """Uniform-weight inter-service edges from the scenario topology.

    Routes are the manifest-derived ``(source, target, host, protocol,
    description)`` tuples; only edges whose endpoints are both deployable
    services enter the solver graph (mirroring
    :func:`fraction_solver.load_dependency_graph`'s placed-endpoint filter).
    Weights are uniform 1.0 — the manifests carry no call volume, matching
    the v1-summary fallback the solver's graph extraction documents.
    """
    service_set = set(services)
    seen: Set[Tuple[str, str]] = set()
    edges: List[fs.Edge] = []
    for source, target, _host, _protocol, _desc in service_routes:
        if not source or not target or source not in service_set or target not in service_set:
            continue
        key = (source, target)
        if key in seen:
            continue
        seen.add(key)
        edges.append((source, target, 1.0))
    return sorted(edges)


def discover_services(mutator: Any) -> List[str]:
    """Deployable application services for the session, from the v1 mutator.

    Chaos infrastructure and the load generator are excluded with the same
    set the affinity engine uses (replicating ``loadgenerator`` would scale
    the offered load with r); zero-replica deployments cannot host an edge.
    """
    return sorted(
        dep.name
        for dep in mutator.get_deployments()
        if dep.replicas > 0 and dep.name not in engine.EXCLUDED_DEPLOYMENTS
    )


def build_session(
    namespace: str,
    *,
    levels: Tuple[float, ...],
    order_seed: int,
    solver_seed: int,
    replicas: int,
    mode: str,
    workers: Tuple[str, ...],
    edges: List[fs.Edge],
    services: List[str],
    api: engine.K8sApi,
    settle_seconds: float = quiescence.DEFAULT_SETTLE_SECONDS,
    settle_timeout: float = quiescence.DEFAULT_SETTLE_TIMEOUT,
    rollout_timeout: float = DEFAULT_ROLLOUT_TIMEOUT,
    packed_assignment: str = PACKED_ASSIGNMENT_SOLVER,
) -> V2Session:
    """Validate the (r, mode, workers, graph) combination and build the session."""
    if replicas not in engine.SUPPORTED_REPLICAS:
        raise ValueError(
            f"--v2-replicas must be one of {sorted(engine.SUPPORTED_REPLICAS)}, got {replicas}"
        )
    if mode not in engine.MODES:
        raise ValueError(f"--v2-mode must be one of {engine.MODES}, got '{mode}'")
    if packed_assignment not in PACKED_ASSIGNMENTS:
        raise ValueError(
            f"packed_assignment must be one of {PACKED_ASSIGNMENTS}, got '{packed_assignment}'"
        )
    if mode == engine.MODE_ANTI_AFFINE and replicas > 1 and len(workers) < replicas:
        raise ValueError(
            f"anti-affine r={replicas} needs >= {replicas} distinct workers, got {len(workers)}"
        )
    if not services:
        raise ValueError(f"no deployable application services found in namespace '{namespace}'")
    if not edges:
        raise ValueError(
            "no inter-service edges in the scenario topology — the cross-node "
            "fraction is undefined, so v2 conditions cannot be targeted"
        )
    return V2Session(
        namespace=namespace,
        levels=levels,
        conditions=ordered_conditions(levels, order_seed),
        order_seed=order_seed,
        solver_seed=solver_seed,
        replicas=replicas,
        mode=mode,
        workers=workers,
        edges=edges,
        services=services,
        api=api,
        settle_seconds=settle_seconds,
        settle_timeout=settle_timeout,
        rollout_timeout=rollout_timeout,
        packed_assignment=packed_assignment,
    )


# ──────────────────────────────────────────────────────────────────────
# Condition execution (the "placement step" of the strategy pipeline)
# ──────────────────────────────────────────────────────────────────────


def apply_condition(session: V2Session, condition: V2Condition) -> Dict[str, Any]:
    """Restore → quiesce → solve → apply → verify one condition; record it.

    The session-level restore + M1b quiescence barrier guarantee the
    condition starts from default scheduling on a settled namespace.  For
    pinned cells (r = 1, or r = 3 packed) the fraction solver computes the
    service→node assignment under the session's solver seed and the live
    achieved fraction is recomputed from Running+Ready pods — never trusted
    from the solver.  For r = 3 anti-affine the scheduler chooses the nodes
    (no assignment, no live fraction) and acceptance rests on
    :func:`affinity_engine.verify_placement` alone.

    Returns the ``strategy_result["placement"]`` dict for the pipeline; the
    full per-level record is stored on ``session.per_level``.
    """
    api = session.api
    namespace = session.namespace

    # Committed to the session up front and mutated in place, so a step that
    # raises mid-cycle (apply timeout, verify API error) still leaves its
    # partial evidence (settle record, solver fields) in v2Session instead of
    # the condition being mislabelled "condition_not_executed".  The
    # incomplete marker is overwritten by the real verdict at the end.
    record: Dict[str, Any] = {
        "condition": condition.name,
        "targetF": condition.target_f,
        "replicas": session.replicas,
        "mode": session.mode,
        "solverSeed": session.solver_seed,
        "accepted": False,
        "rejectionReasons": ["condition_apply_incomplete"],
        "perIteration": [],
    }
    session.per_level[condition.name] = record

    click.echo("    Restoring default scheduling before condition...")
    engine.restore(api, namespace, timeout=session.rollout_timeout)
    click.echo(f"    Waiting for namespace quiescence (window {session.settle_seconds:.0f}s)...")
    settle = quiescence.wait_for_quiescence(
        api,
        namespace,
        settle_seconds=session.settle_seconds,
        timeout=session.settle_timeout,
    )
    if not settle["quiescent"]:
        click.echo(
            f"    WARNING: namespace not quiescent after {settle['waitedSeconds']}s "
            f"(notReady={settle['notReady']}) — proceeding; recorded in v2Session.",
            err=True,
        )
    record["settle"] = settle

    assignment: Optional[Dict[str, str]] = None
    round_robin = session.packed_assignment == PACKED_ASSIGNMENT_ROUND_ROBIN
    if session.pinned and round_robin:
        # V2-H3 packed-cell semantics: capacity-feasible round-robin packing,
        # independent of the condition's f (the replication design does not
        # vary the cross-node fraction). The achieved f is recorded for the
        # record but is NOT a target to be hit, so it never rejects the cell.
        assignment = engine.packed_round_robin(session.services, session.workers)
        record["packedAssignmentMethod"] = PACKED_ASSIGNMENT_ROUND_ROBIN
        record["solverAchievedF"] = round(fs.achieved_fraction(assignment, session.edges), 6)
        record["solverAccepted"] = None
        click.echo(
            f"    Round-robin packing: {len(assignment)} services over "
            f"{len(session.workers)} workers (achieved "
            f"f={record['solverAchievedF']:.4f}, not gated on target)"
        )
    elif session.pinned:
        solution = fs.solve(
            session.edges,
            session.services,
            len(session.workers),
            condition.target_f,
            seed=session.solver_seed,
        )
        assignment = {svc: session.workers[idx] for svc, idx in solution.assignment.items()}
        record["packedAssignmentMethod"] = PACKED_ASSIGNMENT_SOLVER
        record["solverAchievedF"] = round(solution.achieved_f, 6)
        record["solverAccepted"] = solution.accepted
        click.echo(
            f"    Solver: target f={condition.target_f:.2f} → achieved "
            f"f={solution.achieved_f:.4f} ({'accepted' if solution.accepted else 'REJECTED'})"
        )
    else:
        record["solverAchievedF"] = None
        record["solverAccepted"] = None
        click.echo(
            "    r=3 anti-affine: scheduler chooses the nodes (no solver assignment, "
            "no live fraction)"
        )
    record["assignment"] = assignment

    applied = engine.apply_placement(
        api,
        namespace,
        assignment,
        session.replicas,
        session.mode,
        list(session.workers),
        timeout=session.rollout_timeout,
        # Anti-affine cells take the session's own service set so the engine
        # never re-discovers (and resurrects) deployments the session excluded.
        services=None if session.pinned else list(session.services),
    )
    record["schedulingLatencySeconds"] = round(applied.duration_seconds, 3)
    record["pendingDeployments"] = applied.pending

    verification = engine.verify_placement(api, namespace, session.replicas, session.mode)
    record["verification"] = verification.to_dict()

    reasons: List[str] = []
    if not verification.passed:
        reasons.append("placement_verification_failed")

    live_f: Optional[float] = None
    if session.pinned:
        live = engine.live_service_nodes(api, namespace, session.services)
        unverifiable = sorted(svc for svc, nodes in live.items() if len(nodes) != 1)
        if unverifiable:
            reasons.append("live_fraction_unverifiable:" + ",".join(unverifiable))
        else:
            live_f = fs.achieved_fraction(
                {svc: nodes[0] for svc, nodes in live.items()}, session.edges
            )
            # The round-robin packing has no f target (V2-H3 does not vary the
            # cross-node fraction), so its live f is recorded but never gated;
            # acceptance rests on verify_placement / live_fraction_unverifiable.
            if not round_robin:
                record["gap"] = round(abs(live_f - condition.target_f), 6)
                if abs(live_f - condition.target_f) > TOLERANCE:
                    reasons.append("fraction_target_missed")
    record["liveAchievedF"] = round(live_f, 6) if live_f is not None else None

    record["accepted"] = not reasons
    record["rejectionReasons"] = reasons
    if reasons:
        click.echo(
            f"    WARNING: condition {condition.name} REJECTED "
            f"({'; '.join(reasons)}) — its iterations will run but be tainted.",
            err=True,
        )
    else:
        live_str = f"{live_f:.4f}" if live_f is not None else "n/a"
        click.echo(f"    Condition {condition.name} accepted (live f={live_str}).")

    return {
        "strategy": condition.name,
        "description": (
            f"v2 condition: target f={condition.target_f:.2f}, "
            f"r={session.replicas}, mode={session.mode}"
        ),
        "assignments": dict(assignment) if assignment else {},
        "v2": record,
    }


# ──────────────────────────────────────────────────────────────────────
# Per-iteration live fraction + taint (the pre-registered rejection rule)
# ──────────────────────────────────────────────────────────────────────


def iteration_live_fraction(
    session: V2Session, pod_placements: Mapping[str, str]
) -> Optional[float]:
    """The live cross-node fraction implied by one iteration's pod→node map.

    Only defined for pinned cells where every observed service's pods sit on
    exactly one distinct node (r = 1, or r = 3 packed); returns ``None`` for
    anti-affine cells, an empty map, a service spanning several nodes, or a
    map covering none of the graph's edges.  Services absent from the map
    simply drop their incident edges from the fraction (the same convention
    as :func:`fraction_solver.achieved_fraction`).
    """
    if not session.pinned or not pod_placements:
        return None
    service_set = set(session.services)
    nodes_by_service: Dict[str, Set[str]] = {}
    for pod_name, node in pod_placements.items():
        if not node:
            continue
        service = fs.deployment_of(pod_name)
        if service not in service_set:
            continue
        nodes_by_service.setdefault(service, set()).add(node)
    assignment: Dict[str, str] = {}
    for service, nodes in nodes_by_service.items():
        if len(nodes) != 1:
            return None
        assignment[service] = next(iter(nodes))
    try:
        return fs.achieved_fraction(assignment, session.edges)
    except ValueError:
        return None


def annotate_iteration(
    session: V2Session, condition_name_: str, iteration_result: Dict[str, Any]
) -> None:
    """Record the iteration's live achieved fraction and apply the taint rule.

    Mirrors the existing taint conventions: a violation sets
    ``preChaosHealthy = False`` and appends to ``preChaosTaintReasons`` (so
    :func:`aggregate_iterations`' healthy-only statistics and ``doctor``
    exclude — never drop — the iteration), plus the ``tainted`` /
    ``taintReasons`` flags the unknown-probe retry path uses.

    Taints when (a) the condition itself was rejected at apply time, or
    (b) a pinned iteration's live fraction is unverifiable or misses the
    target by more than the pre-registered tolerance.

    The target-drift check is skipped for the round-robin packed assignment
    (V2-H3): that design does not target a cross-node fraction, so the live f
    it achieves (≈0.87, not the condition's nominal target) is not a drift —
    this mirrors the acceptance gate in :func:`apply_condition`. The
    unverifiable check (each service's replicas on exactly one node) still
    applies — it is a packing-integrity check, not an f-target check.

    The per-iteration record's ``taintReasons`` carries the **complete** taint
    for the iteration: the pre-chaos taints the strategy runner already set on
    ``iteration_result`` (``app_ready_timeout``, ``iteration_exception``,
    ``pre_chaos_errors_high`` …) folded together with the v2-placement reasons
    judged here.  This is the only per-iteration taint channel persisted for a
    node-drain session (its ``<condition>.json`` flattens metrics to the top
    level and leaves ``iterations`` empty), so a downstream analysis that reads
    ``perIteration[].taintReasons`` (``scripts/c2_h3_anova.py``) sees every
    tainted iteration — honouring the registered "no result is ever quoted
    from a tainted iteration" rule for both channels, not just the v2 one.
    """
    record = session.per_level.get(condition_name_)
    if record is None:
        return  # placement step never ran (errored before apply) — nothing to judge
    live_f = iteration_live_fraction(session, iteration_result.get("podPlacements") or {})
    round_robin = session.packed_assignment == PACKED_ASSIGNMENT_ROUND_ROBIN
    reasons: List[str] = []
    if not record["accepted"]:
        reasons.append("v2_condition_rejected")
    if session.pinned:
        if live_f is None:
            reasons.append("v2_live_fraction_unverifiable")
        elif not round_robin and abs(live_f - record["targetF"]) > TOLERANCE:
            reasons.append("v2_live_fraction_drifted")
    # Pre-chaos taints the strategy runner set before this hook ran (the
    # non-v2 channel) are recorded too — deduped, prior reasons first — so the
    # persisted perIteration record is the complete taint for the iteration.
    prior_reasons = list(iteration_result.get("preChaosTaintReasons") or [])
    for reason in iteration_result.get("taintReasons") or []:
        if reason not in prior_reasons:
            prior_reasons.append(reason)
    all_reasons = prior_reasons + [r for r in reasons if r not in prior_reasons]
    record["perIteration"].append(
        {
            "iteration": iteration_result.get("iteration"),
            "liveAchievedF": round(live_f, 6) if live_f is not None else None,
            "taintReasons": all_reasons,
        }
    )
    if not reasons:
        return  # nothing new to propagate back into iteration_result
    click.echo(
        f"    WARNING: v2 rejection rule tainted iteration "
        f"{iteration_result.get('iteration')} of {condition_name_}: {'; '.join(reasons)}",
        err=True,
    )
    iteration_result["preChaosHealthy"] = False
    pre_chaos_reasons = iteration_result.setdefault("preChaosTaintReasons", [])
    pre_chaos_reasons.extend(reason for reason in reasons if reason not in pre_chaos_reasons)
    iteration_result["tainted"] = True
    taint_reasons = iteration_result.setdefault("taintReasons", [])
    taint_reasons.extend(reason for reason in reasons if reason not in taint_reasons)


# ──────────────────────────────────────────────────────────────────────
# Session metadata (summary.json → v2Session)
# ──────────────────────────────────────────────────────────────────────


def session_metadata(session: V2Session) -> Dict[str, Any]:
    """The ``v2Session`` block of ``summary.json``.

    Everything the A/A comparison and the C1 analysis need: the block
    definition, the applied order (and both seeds that produced it), the
    (r, mode, workers) cell, and one per-level record carrying the solver's
    achieved fraction, the post-apply live fraction, every iteration's live
    fraction, and the acceptance verdict with its reasons.  Conditions that
    never reached their placement step (an earlier fatal error) appear as
    explicit not-executed entries rather than being silently absent.
    """
    per_level: List[Dict[str, Any]] = []
    for cond in session.conditions:
        record = session.per_level.get(cond.name)
        if record is None:
            record = {
                "condition": cond.name,
                "targetF": cond.target_f,
                "accepted": False,
                "rejectionReasons": ["condition_not_executed"],
                "perIteration": [],
            }
        per_level.append(record)
    return {
        "levels": list(session.levels),
        "orderApplied": [cond.target_f for cond in session.conditions],
        "conditionOrder": [cond.name for cond in session.conditions],
        "orderSeed": session.order_seed,
        "solverSeed": session.solver_seed,
        "replicas": session.replicas,
        "mode": session.mode,
        "packedAssignment": session.packed_assignment,
        "workers": list(session.workers),
        "tolerance": TOLERANCE,
        "perLevel": per_level,
    }
