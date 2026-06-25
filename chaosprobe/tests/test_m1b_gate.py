"""Tests for scripts/m1b_gate.py — the M1b live GO/NO-GO gate.

Pure-Python per CONTRIBUTING: the affinity engine and Kubernetes APIs are
MagicMocks; no cluster is touched.  Covers the per-level state machine
(consecutive counter resets on a miss, abort after max attempts), all three
phases, the capacity-feasible packed assignment, the quiescence barrier's
state machine (ready / not-ready / restart / Unhealthy-event paths), the
failure diagnostics snapshot, the artifact shape, restore-on-exit on
success *and* on exception, and the CLI argument validation.
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaosprobe.orchestrator import quiescence
from chaosprobe.placement.affinity_engine import ApplyResult, ServiceCheck, VerificationResult
from chaosprobe.placement.fraction_solver import Solution

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "m1b_gate.py"
_spec = importlib.util.spec_from_file_location("m1b_gate", _SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
sys.modules["m1b_gate"] = gate  # dataclasses resolve annotations via sys.modules
_spec.loader.exec_module(gate)

WORKERS = ["w1", "w2", "w3", "w4", "w5", "w6", "w7", "w8"]
EDGES = [("a", "b", 1.0), ("b", "c", 1.0)]
SERVICES = ["a", "b", "c"]


def _cfg(**overrides):
    return gate.GateConfig(**overrides)


def _solution(assignment, achieved, target):
    return Solution(
        assignment=assignment,
        achieved_f=achieved,
        target_f=target,
        accepted=abs(achieved - target) <= 0.05,
    )


def _apply_result(services, pending=(), duration=1.5):
    return ApplyResult(applied=sorted(services), pending=list(pending), duration_seconds=duration)


def _verification(r, mode, oks):
    checks = [
        ServiceCheck(
            service=svc,
            ok=ok,
            reason="" if ok else "boom",
            ready_replicas=r,
            nodes=["w1"],
            assigned_node=None,
        )
        for svc, ok in oks.items()
    ]
    return VerificationResult(r=r, mode=mode, passed=all(oks.values()), services=checks)


# ── CLI parsing helpers ───────────────────────────────────────────────


def test_parse_workers_preserves_order():
    assert gate.parse_workers(" w2 , w1 ,w3") == ["w2", "w1", "w3"]


@pytest.mark.parametrize("spec", ["", " , ", "w1,w2,w1"])
def test_parse_workers_rejects_empty_or_duplicates(spec):
    with pytest.raises(ValueError):
        gate.parse_workers(spec)


def test_parse_levels_default_grid():
    assert gate.parse_levels("0,0.25,0.5,0.75,1.0") == (0.0, 0.25, 0.5, 0.75, 1.0)


@pytest.mark.parametrize("spec", ["abc", "0.5,oops"])
def test_parse_levels_rejects_non_floats(spec):
    with pytest.raises(ValueError, match="comma-separated floats"):
        gate.parse_levels(spec)


@pytest.mark.parametrize("spec", ["", "1.5", "-0.1,0.5"])
def test_parse_levels_rejects_out_of_range(spec):
    with pytest.raises(ValueError, match="in \\[0, 1\\]"):
        gate.parse_levels(spec)


# ── Phase A: one attempt ──────────────────────────────────────────────

_SETTLE = {"quiescent": True, "waitedSeconds": 0.0}
_DIAGNOSTICS = {"capturedAt": "t", "deployments": {}, "pods": [], "events": []}


def _wire_attempt(monkeypatch, live_nodes, solver_assignment=None, pending=()):
    """Wire the engine + solver + barrier mocks for one run_attempt call."""
    solver_assignment = solver_assignment or {"a": 0, "b": 1, "c": 1}
    restore = MagicMock()
    apply_mock = MagicMock(return_value=_apply_result(solver_assignment, pending=pending))
    solve = MagicMock(return_value=_solution(solver_assignment, 0.5, 0.5))
    quiesce = MagicMock(return_value=dict(_SETTLE))
    diagnostics = MagicMock(return_value=dict(_DIAGNOSTICS))
    monkeypatch.setattr(gate.engine, "restore", restore)
    monkeypatch.setattr(gate.engine, "apply_placement", apply_mock)
    monkeypatch.setattr(gate.engine, "live_service_nodes", MagicMock(return_value=live_nodes))
    monkeypatch.setattr(gate.fs, "solve", solve)
    monkeypatch.setattr(gate, "wait_for_quiescence", quiesce)
    monkeypatch.setattr(gate, "collect_diagnostics", diagnostics)
    return restore, apply_mock, solve, quiesce, diagnostics


def test_run_attempt_in_tolerance(monkeypatch):
    # live: a|b cross (edge a->b), b|c together (edge b->c) -> f = 0.5
    restore, apply_mock, solve, quiesce, diagnostics = _wire_attempt(
        monkeypatch, {"a": ["w1"], "b": ["w2"], "c": ["w2"]}
    )
    api = MagicMock()
    record = gate.run_attempt(api, "ns", EDGES, SERVICES, WORKERS, 0.5, 1, _cfg())
    assert record["inTolerance"] is True
    assert record["liveAchievedF"] == 0.5
    assert record["gap"] == 0.0
    assert record["assignment"] == {"a": "w1", "b": "w2", "c": "w2"}
    assert record["solverAchievedF"] == 0.5
    assert record["schedulingLatencySeconds"] == 1.5
    assert record["settle"] == _SETTLE
    assert "diagnostics" not in record  # clean attempt: no failure snapshot
    restore.assert_called_once_with(api, "ns", timeout=300.0)
    quiesce.assert_called_once_with(api, "ns", settle_seconds=60.0, timeout=300.0)
    diagnostics.assert_not_called()
    solve.assert_called_once_with(EDGES, SERVICES, 8, 0.5, seed=0)
    apply_mock.assert_called_once_with(
        api,
        "ns",
        {"a": "w1", "b": "w2", "c": "w2"},
        1,
        gate.engine.MODE_PACKED,
        WORKERS,
        timeout=300.0,
    )


def test_run_attempt_judges_on_live_pods_not_solver(monkeypatch):
    # Solver promised f=0.5, but live pods all landed on one node -> f=0.0.
    _wire_attempt(monkeypatch, {"a": ["w1"], "b": ["w1"], "c": ["w1"]})
    record = gate.run_attempt(MagicMock(), "ns", EDGES, SERVICES, WORKERS, 0.5, 1, _cfg())
    assert record["liveAchievedF"] == 0.0
    assert record["inTolerance"] is False
    assert "diagnostics" not in record  # a fraction miss is not a timeout


def test_run_attempt_unverifiable_service_is_a_miss(monkeypatch):
    _wire_attempt(monkeypatch, {"a": ["w1"], "b": [], "c": ["w1", "w2"]})
    record = gate.run_attempt(MagicMock(), "ns", EDGES, SERVICES, WORKERS, 0.5, 1, _cfg())
    assert record["inTolerance"] is False
    assert record["liveAchievedF"] is None
    assert "b, c" in record["reason"]
    assert record["diagnostics"] == _DIAGNOSTICS  # unverifiable -> snapshot


def test_run_attempt_pending_deployments_capture_diagnostics(monkeypatch):
    _, _, _, _, diagnostics = _wire_attempt(
        monkeypatch, {"a": ["w1"], "b": ["w2"], "c": ["w2"]}, pending=["b"]
    )
    api = MagicMock()
    record = gate.run_attempt(api, "ns", EDGES, SERVICES, WORKERS, 0.5, 1, _cfg())
    assert record["pendingDeployments"] == ["b"]
    assert record["diagnostics"] == _DIAGNOSTICS
    diagnostics.assert_called_once_with(api, "ns")


def test_run_attempt_passes_settle_knobs_from_config(monkeypatch):
    _, _, _, quiesce, _ = _wire_attempt(monkeypatch, {"a": ["w1"], "b": ["w2"], "c": ["w2"]})
    api = MagicMock()
    cfg = _cfg(settle_seconds=5.0, settle_timeout=42.0)
    gate.run_attempt(api, "ns", EDGES, SERVICES, WORKERS, 0.5, 1, cfg)
    quiesce.assert_called_once_with(api, "ns", settle_seconds=5.0, timeout=42.0)


def test_run_attempt_seed_advances_per_attempt(monkeypatch):
    """Attempt N starts its sweep at base + (N-1)*SWEEP: attempts never overlap."""
    _, _, solve, _, _ = _wire_attempt(monkeypatch, {"a": ["w1"], "b": ["w2"], "c": ["w2"]})
    gate.run_attempt(MagicMock(), "ns", EDGES, SERVICES, WORKERS, 0.5, 4, _cfg(seed=10))
    assert solve.call_args.kwargs["seed"] == 10 + 3 * gate.SOLVER_SEED_SWEEP
    # accepted on the first sweep seed -> exactly one solve call
    assert solve.call_count == 1


def test_seed_sweep_returns_first_accepted_and_seeds_tried():
    calls = []

    def fake_solve(edges, services, n_nodes, level, seed):
        calls.append(seed)
        achieved = 0.0625 if seed < 102 else 0.0
        return _solution({"a": 0}, achieved, level)

    real_solve = gate.fs.solve
    gate.fs.solve = fake_solve
    try:
        solution, tried = gate.solve_with_seed_sweep([("a", "b", 1.0)], ["a", "b"], 8, 0.0, 100)
    finally:
        gate.fs.solve = real_solve
    assert solution.accepted and solution.achieved_f == 0.0
    assert tried == [100, 101, 102] and calls == tried


def test_seed_sweep_exhausted_returns_best_gap():
    gaps = {200: 0.0625, 201: 0.125, 202: 0.0625, 203: 0.1875, 204: 0.0625}

    def fake_solve(edges, services, n_nodes, level, seed):
        return _solution({"a": 0}, gaps[seed], level)

    real_solve = gate.fs.solve
    gate.fs.solve = fake_solve
    try:
        solution, tried = gate.solve_with_seed_sweep([("a", "b", 1.0)], ["a", "b"], 8, 0.0, 200)
    finally:
        gate.fs.solve = real_solve
    assert not solution.accepted
    assert solution.achieved_f == 0.0625  # best gap among the sweep
    assert tried == [200, 201, 202, 203, 204]


def test_run_attempt_records_seeds_tried(monkeypatch):
    _, _, _, _, _ = _wire_attempt(monkeypatch, {"a": ["w1"], "b": ["w2"], "c": ["w2"]})
    record = gate.run_attempt(MagicMock(), "ns", EDGES, SERVICES, WORKERS, 0.5, 1, _cfg(seed=0))
    assert record["solverSeedsTried"] == [0]


# ── Phase A: the per-level state machine ──────────────────────────────


def _scripted_attempts(monkeypatch, hits):
    """Replace run_attempt with a script of in-tolerance outcomes."""
    outcomes = iter(hits)

    def fake_attempt(api, ns, edges, services, workers, level, attempt_no, cfg):
        return {"attempt": attempt_no, "target": level, "inTolerance": next(outcomes)}

    monkeypatch.setattr(gate, "run_attempt", fake_attempt)


def test_run_level_passes_on_three_consecutive(monkeypatch):
    _scripted_attempts(monkeypatch, [True, True, True])
    result = gate.run_level(MagicMock(), "ns", EDGES, SERVICES, WORKERS, 0.25, _cfg())
    assert result["passed"] is True
    assert result["totalAttempts"] == 3
    assert [a["consecutiveAfter"] for a in result["attempts"]] == [1, 2, 3]


def test_run_level_counter_resets_on_miss(monkeypatch):
    _scripted_attempts(monkeypatch, [True, True, False, True, True, True])
    result = gate.run_level(MagicMock(), "ns", EDGES, SERVICES, WORKERS, 0.5, _cfg())
    assert result["passed"] is True
    assert result["totalAttempts"] == 6
    assert [a["consecutiveAfter"] for a in result["attempts"]] == [1, 2, 0, 1, 2, 3]


def test_run_level_aborts_after_max_attempts(monkeypatch):
    _scripted_attempts(monkeypatch, [True, True, False, True, False, True])
    result = gate.run_level(MagicMock(), "ns", EDGES, SERVICES, WORKERS, 0.75, _cfg())
    assert result["passed"] is False
    assert result["totalAttempts"] == 6  # aborted at max_attempts
    assert result["bestConsecutive"] == 2
    assert result["requiredConsecutive"] == 3


def test_run_phase_a_aggregates_levels(monkeypatch):
    levels = iter([{"passed": True, "target": 0.0}, {"passed": False, "target": 1.0}])
    monkeypatch.setattr(gate, "run_level", lambda api, ns, e, s, w, level, cfg: next(levels))
    result = gate.run_phase_a(MagicMock(), "ns", EDGES, SERVICES, WORKERS, _cfg(levels=(0.0, 1.0)))
    assert result["passed"] is False
    assert result["nNodes"] == 8
    assert [level["target"] for level in result["levels"]] == [0.0, 1.0]


def test_run_phase_a_all_pass(monkeypatch):
    monkeypatch.setattr(
        gate, "run_level", lambda api, ns, e, s, w, level, cfg: {"passed": True, "target": level}
    )
    result = gate.run_phase_a(MagicMock(), "ns", EDGES, SERVICES, WORKERS, _cfg())
    assert result["passed"] is True
    assert len(result["levels"]) == 5


# ── packed_assignment ─────────────────────────────────────────────────


def test_packed_assignment_round_robins_sorted_services_over_workers():
    services = [f"svc{i:02d}" for i in range(11)]
    workers = ["w1", "w2", "w3", "w4"]
    assignment = gate.packed_assignment(list(reversed(services)), workers)
    assert sorted(assignment) == services  # every service assigned exactly once
    # sorted service i -> worker i mod W, regardless of input order
    assert assignment["svc00"] == "w1"
    assert assignment["svc03"] == "w4"
    assert assignment["svc04"] == "w1"
    assert assignment["svc10"] == "w3"
    # capacity feasibility: services spread evenly, max ceil(11/4) per node
    per_node = {w: sum(1 for n in assignment.values() if n == w) for w in workers}
    assert per_node == {"w1": 3, "w2": 3, "w3": 3, "w4": 2}


def test_packed_assignment_fewer_services_than_workers():
    assignment = gate.packed_assignment(["b", "a"], WORKERS)
    assert assignment == {"a": "w1", "b": "w2"}


def test_packed_assignment_rejects_empty_workers():
    with pytest.raises(ValueError, match="non-empty"):
        gate.packed_assignment(["a"], [])


# ── Phase B ───────────────────────────────────────────────────────────


def _wire_phase_b(monkeypatch, anti_ok=True, packed_ok=True, pending=()):
    restore = MagicMock()
    apply_mock = MagicMock(return_value=_apply_result(SERVICES, pending=pending, duration=7.0))
    verify = MagicMock(
        side_effect=[
            _verification(3, "anti-affine", {svc: anti_ok for svc in SERVICES}),
            _verification(3, "packed", {svc: packed_ok for svc in SERVICES}),
        ]
    )
    quiesce = MagicMock(return_value=dict(_SETTLE))
    diagnostics = MagicMock(return_value=dict(_DIAGNOSTICS))
    monkeypatch.setattr(gate.engine, "restore", restore)
    monkeypatch.setattr(gate.engine, "apply_placement", apply_mock)
    monkeypatch.setattr(gate.engine, "verify_placement", verify)
    monkeypatch.setattr(gate, "wait_for_quiescence", quiesce)
    monkeypatch.setattr(gate, "collect_diagnostics", diagnostics)
    return restore, apply_mock, verify, quiesce, diagnostics


def test_run_phase_b_passes_both_arms(monkeypatch):
    restore, apply_mock, verify, quiesce, diagnostics = _wire_phase_b(monkeypatch)
    api = MagicMock()
    result = gate.run_phase_b(api, "ns", EDGES, SERVICES, WORKERS, _cfg())
    assert result["passed"] is True
    assert result["antiAffine"]["passed"] is True
    assert result["antiAffine"]["schedulingLatencySeconds"] == 7.0
    # packed arm: per-service packing on the round-robin assignment —
    # services distributed ACROSS nodes, never all on one node.
    assert result["packed"]["assignment"] == {"a": "w1", "b": "w2", "c": "w3"}
    assert result["packed"]["packedAssignmentMethod"] == "round-robin"
    assert result["packed"]["achievedF"] == 1.0  # both edges cross under round-robin
    assert result["antiAffine"]["settle"] == _SETTLE
    assert result["packed"]["settle"] == _SETTLE
    assert "diagnostics" not in result["antiAffine"]
    assert "diagnostics" not in result["packed"]
    # anti-affine arm: assignment=None (the scheduler chooses)
    anti_call = apply_mock.call_args_list[0]
    assert anti_call.args[2] is None
    assert anti_call.args[3:5] == (3, gate.engine.MODE_ANTI_AFFINE)
    packed_call = apply_mock.call_args_list[1]
    assert packed_call.args[2] == {"a": "w1", "b": "w2", "c": "w3"}
    assert packed_call.args[3:5] == (3, gate.engine.MODE_PACKED)
    assert restore.call_count == 2  # clean state before each arm
    assert quiesce.call_count == 2  # quiescence barrier after each restore
    quiesce.assert_called_with(api, "ns", settle_seconds=60.0, timeout=300.0)
    diagnostics.assert_not_called()


@pytest.mark.parametrize("anti_ok, packed_ok", [(False, True), (True, False)])
def test_run_phase_b_fails_when_either_arm_fails(monkeypatch, anti_ok, packed_ok):
    _wire_phase_b(monkeypatch, anti_ok=anti_ok, packed_ok=packed_ok)
    result = gate.run_phase_b(MagicMock(), "ns", EDGES, SERVICES, WORKERS, _cfg())
    assert result["passed"] is False
    assert result["antiAffine"]["passed"] is anti_ok
    assert result["packed"]["passed"] is packed_ok
    # the failing arm (and only it) embeds the diagnostics snapshot
    assert ("diagnostics" in result["antiAffine"]) is not anti_ok
    assert ("diagnostics" in result["packed"]) is not packed_ok


def test_run_phase_b_pending_deployments_capture_diagnostics(monkeypatch):
    _, _, _, _, diagnostics = _wire_phase_b(monkeypatch, pending=["a"])
    result = gate.run_phase_b(MagicMock(), "ns", EDGES, SERVICES, WORKERS, _cfg())
    assert result["passed"] is True  # verification mocked green
    assert result["antiAffine"]["diagnostics"] == _DIAGNOSTICS  # apply timed out
    assert result["packed"]["diagnostics"] == _DIAGNOSTICS
    assert diagnostics.call_count == 2


# ── quiescence barrier ────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc)
_OLD = _NOW - timedelta(days=1)
_SOON = _NOW + timedelta(hours=1)  # always inside any window opened during a test


class _FakeTime:
    """Deterministic stand-in for the gate's ``time`` module."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _snap(not_ready=(), restarts=0, last_unhealthy=None):
    return {
        "notReady": sorted(not_ready),
        "restarts": restarts,
        "lastUnhealthyAt": last_unhealthy,
    }


def _wire_quiescence(monkeypatch, snapshots):
    """Patch quiescence_snapshot (last entry repeats) + fake the clock.

    The barrier moved into ``chaosprobe.orchestrator.quiescence`` (reused by
    the session driver), so its internals are patched on that module; the
    script re-exports the public names unchanged.
    """
    clock = _FakeTime()
    monkeypatch.setattr(quiescence, "time", clock)
    feed = list(snapshots)

    def fake_snapshot(api, namespace):
        return feed.pop(0) if len(feed) > 1 else feed[0]

    monkeypatch.setattr(quiescence, "quiescence_snapshot", fake_snapshot)
    return clock


def test_wait_for_quiescence_clean_window_passes(monkeypatch):
    _wire_quiescence(monkeypatch, [_snap()])
    record = gate.wait_for_quiescence(MagicMock(), "ns", settle_seconds=10, poll_seconds=5)
    assert record["quiescent"] is True
    assert record["polls"] == 3  # window opens at t=0, satisfied at t=10
    assert record["waitedSeconds"] == 10.0
    assert record["windowResets"] == 0
    assert record["notReady"] == []
    assert record["lastUnhealthyAt"] is None
    assert record["settleSeconds"] == 10
    assert record["timeoutSeconds"] == gate.DEFAULT_SETTLE_TIMEOUT


def test_wait_for_quiescence_waits_for_readiness(monkeypatch):
    _wire_quiescence(monkeypatch, [_snap(not_ready=["frontend"]), _snap()])
    record = gate.wait_for_quiescence(MagicMock(), "ns", settle_seconds=5, poll_seconds=5)
    assert record["quiescent"] is True
    assert record["windowResets"] == 0  # window never opened while not ready
    assert record["waitedSeconds"] == 10.0  # 5s not ready + 5s window


def test_wait_for_quiescence_restart_change_resets_the_window(monkeypatch):
    # restarts 5 -> 6 (churn resets the open window) -> stable at 6
    _wire_quiescence(monkeypatch, [_snap(restarts=5), _snap(restarts=6), _snap(restarts=6)])
    record = gate.wait_for_quiescence(MagicMock(), "ns", settle_seconds=10, poll_seconds=5)
    assert record["quiescent"] is True
    assert record["windowResets"] == 1
    assert record["waitedSeconds"] == 20.0  # reset at t=5, clean window 10..20
    assert record["restarts"] == 6


def test_wait_for_quiescence_new_unhealthy_event_resets_the_window(monkeypatch):
    # an Unhealthy event lands inside the open window, then ages out
    _wire_quiescence(monkeypatch, [_snap(), _snap(last_unhealthy=_SOON), _snap()])
    record = gate.wait_for_quiescence(MagicMock(), "ns", settle_seconds=10, poll_seconds=5)
    assert record["quiescent"] is True
    assert record["windowResets"] == 1


def test_wait_for_quiescence_pre_window_unhealthy_event_is_ignored(monkeypatch):
    _wire_quiescence(monkeypatch, [_snap(last_unhealthy=_OLD)])
    record = gate.wait_for_quiescence(MagicMock(), "ns", settle_seconds=10, poll_seconds=5)
    assert record["quiescent"] is True
    assert record["windowResets"] == 0
    assert record["lastUnhealthyAt"] == _OLD.isoformat()


def test_wait_for_quiescence_times_out_and_reports_state(monkeypatch):
    _wire_quiescence(monkeypatch, [_snap(not_ready=["cartservice", "adservice"], restarts=7)])
    record = gate.wait_for_quiescence(
        MagicMock(), "ns", settle_seconds=60, timeout=10, poll_seconds=5
    )
    assert record["quiescent"] is False
    assert record["polls"] == 3  # t=0, 5, 10 (deadline)
    assert record["notReady"] == ["adservice", "cartservice"]
    assert record["restarts"] == 7
    assert record["timeoutSeconds"] == 10


def test_wait_for_quiescence_timeout_with_window_still_open(monkeypatch):
    # quiet but the window is shorter than the deadline allows
    _wire_quiescence(monkeypatch, [_snap()])
    record = gate.wait_for_quiescence(
        MagicMock(), "ns", settle_seconds=60, timeout=10, poll_seconds=5
    )
    assert record["quiescent"] is False
    assert record["windowResets"] == 0


def _q_dep(name, desired=1, generation=1, observed=1, ready=1, updated=1, available=1):
    dep = MagicMock()
    dep.metadata.name = name
    dep.metadata.generation = generation
    dep.spec.replicas = desired
    if observed is None:
        dep.status = None
    else:
        dep.status.observed_generation = observed
        dep.status.ready_replicas = ready
        dep.status.updated_replicas = updated
        dep.status.available_replicas = available
    return dep


def _q_pod(
    name="p", restarts=(), waiting=None, phase="Running", node="w1", has_status=True, has_spec=True
):
    pod = MagicMock()
    pod.metadata.name = name
    if has_spec:
        pod.spec.node_name = node
    else:
        pod.spec = None
    if not has_status:
        pod.status = None
        return pod
    pod.status.phase = phase
    statuses = []
    for i, count in enumerate(restarts):
        cs = MagicMock()
        cs.restart_count = count
        if waiting is not None and i == 0:
            cs.state.waiting.reason = waiting
        else:
            cs.state.waiting = None
        statuses.append(cs)
    pod.status.container_statuses = statuses or None
    return pod


def _q_event(
    reason="Unhealthy",
    last=None,
    event_time=None,
    created=None,
    message="probe failed",
    kind="Pod",
    name="p1",
    count=3,
    involved=True,
):
    event = MagicMock()
    event.reason = reason
    event.last_timestamp = last
    event.event_time = event_time
    event.metadata.creation_timestamp = created
    event.message = message
    event.count = count
    if involved:
        event.involved_object.kind = kind
        event.involved_object.name = name
    else:
        event.involved_object = None
    return event


def _q_api(deps=(), pods=(), events=()):
    api = MagicMock()
    api.apps.list_namespaced_deployment.return_value = MagicMock(items=list(deps))
    api.core.list_namespaced_pod.return_value = MagicMock(items=list(pods))
    api.core.list_namespaced_event.return_value = MagicMock(items=list(events))
    return api


def test_quiescence_snapshot_all_settled():
    api = _q_api(
        deps=[_q_dep("a"), _q_dep("b", desired=None)],  # replicas=None defaults to 1
        pods=[_q_pod(restarts=(1, 2)), _q_pod(restarts=(3,))],
        events=[_q_event(reason="Scheduled", last=_NOW)],  # non-Unhealthy: ignored
    )
    snap = gate._quiescence_snapshot(api, "ns")
    assert snap == {"notReady": [], "restarts": 6, "lastUnhealthyAt": None}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"generation": 2, "observed": 1},
        {"ready": 0},
        {"updated": 0},
        {"available": 0},
        {"observed": None},  # no status at all
    ],
)
def test_quiescence_snapshot_flags_unsettled_deployments(kwargs):
    api = _q_api(deps=[_q_dep("b", **kwargs), _q_dep("a")])
    assert gate._quiescence_snapshot(api, "ns")["notReady"] == ["b"]


def test_quiescence_snapshot_tolerates_missing_pod_fields():
    api = _q_api(pods=[_q_pod(has_status=False), _q_pod(restarts=(None,))])
    assert gate._quiescence_snapshot(api, "ns")["restarts"] == 0


def test_quiescence_snapshot_keeps_newest_unhealthy_event():
    api = _q_api(
        events=[
            _q_event(last=_OLD),
            _q_event(last=_NOW),
            _q_event(last=None),  # timestamp-less: skipped
            _q_event(last=_OLD),  # older than the running max: kept out
        ]
    )
    assert gate._quiescence_snapshot(api, "ns")["lastUnhealthyAt"] == _NOW


# ── _event_time / _iso ────────────────────────────────────────────────


def test_event_time_prefers_last_timestamp():
    assert gate._event_time(_q_event(last=_NOW, event_time=_OLD, created=_OLD)) == _NOW


def test_event_time_falls_back_to_event_time_then_creation():
    assert gate._event_time(_q_event(event_time=_NOW)) == _NOW
    assert gate._event_time(_q_event(created=_NOW)) == _NOW


def test_event_time_normalises_naive_timestamps_to_utc():
    naive = datetime(2026, 6, 11, 12, 0, 0)
    assert gate._event_time(_q_event(last=naive)) == naive.replace(tzinfo=timezone.utc)


def test_event_time_none_when_no_timestamp_is_a_datetime():
    assert gate._event_time(_q_event()) is None


def test_iso_roundtrips_and_passes_none_through():
    assert gate._iso(None) is None
    assert gate._iso(_NOW) == _NOW.isoformat()


# ── collect_diagnostics ───────────────────────────────────────────────


def test_collect_diagnostics_snapshot_shape():
    deps = [_q_dep("fe", desired=3, ready=1), _q_dep("be", desired=None, observed=None)]
    pods = [
        _q_pod(name="fe-1", restarts=(2, 0), waiting="CrashLoopBackOff", node="w1"),
        _q_pod(name="be-1", restarts=(0,), phase="Pending", has_spec=False),
        _q_pod(name="gone", has_status=False),
    ]
    events = [
        _q_event(reason="FailedScheduling", last=_OLD, kind="Pod", name="be-1", count=None),
        _q_event(reason="Unhealthy", last=_NOW, message="Readiness probe failed"),
        _q_event(reason="Scheduled", last=_NOW),  # filtered out
        _q_event(reason="Unhealthy", last=None, involved=False, message=None),
    ]
    result = gate.collect_diagnostics(_q_api(deps, pods, events), "ns")
    assert result["deployments"] == {
        "fe": {"desired": 3, "ready": 1},
        "be": {"desired": 1, "ready": 0},
    }
    assert result["pods"][0] == {
        "pod": "fe-1",
        "phase": "Running",
        "node": "w1",
        "restarts": 2,
        "waitingReasons": ["CrashLoopBackOff"],
    }
    assert result["pods"][2] == {
        "pod": "gone",
        "phase": "",
        "node": "w1",
        "restarts": 0,
        "waitingReasons": [],
    }
    # newest first, non-matching reasons dropped, timestamp-less events last
    assert [e["reason"] for e in result["events"]] == [
        "Unhealthy",
        "FailedScheduling",
        "Unhealthy",
    ]
    assert result["events"][0]["message"] == "Readiness probe failed"
    assert result["events"][1] == {
        "reason": "FailedScheduling",
        "object": "Pod/be-1",
        "count": 1,  # missing count reads as 1
        "message": "probe failed",
        "lastSeenAt": _OLD.isoformat(),
    }
    assert result["events"][2] == {
        "reason": "Unhealthy",
        "object": "",
        "count": 3,
        "message": "",
        "lastSeenAt": None,
    }
    assert "capturedAt" in result


def test_collect_diagnostics_caps_events_at_ten():
    stamps = [_NOW - timedelta(minutes=i) for i in range(12)]
    events = [_q_event(last=stamp) for stamp in stamps]
    result = gate.collect_diagnostics(_q_api(events=events), "ns")
    assert len(result["events"]) == 10
    assert [e["lastSeenAt"] for e in result["events"]] == [
        s.isoformat() for s in stamps[:10]  # the 2 oldest dropped
    ]


# ── Phase C ───────────────────────────────────────────────────────────


def _phase_c_pod(node, namespace, cpu="100m", memory="128Mi", phase="Running", resources=True):
    pod = MagicMock()
    pod.spec.node_name = node
    pod.status.phase = phase
    pod.metadata.namespace = namespace
    container = MagicMock()
    if resources:
        container.resources.requests = {"cpu": cpu, "memory": memory}
    else:
        container.resources = None
    pod.spec.containers = [container]
    return pod


def _phase_c_node(name, cpu="4", memory="8Gi"):
    node = MagicMock()
    node.metadata.name = name
    node.status.allocatable = {"cpu": cpu, "memory": memory}
    return node


def _phase_c_api(pods, nodes):
    api = MagicMock()
    api.core.list_pod_for_all_namespaces.return_value = MagicMock(items=pods)
    api.core.list_node.return_value = MagicMock(items=nodes)
    return api


def test_run_phase_c_records_headroom_and_passes():
    api = _phase_c_api(
        pods=[
            _phase_c_pod("w1", "ns", cpu="500m", memory="1Gi"),
            _phase_c_pod("w2", "kube-system", cpu="500m", memory="1Gi"),
            _phase_c_pod("elsewhere", "ns"),  # not on a gate worker: excluded
            _phase_c_pod("w1", "ns", phase="Succeeded"),  # terminal: excluded
            _phase_c_pod("w2", "ns", resources=False),  # no requests: contributes 0
        ],
        nodes=[_phase_c_node("w1"), _phase_c_node("w2"), _phase_c_node("control-plane")],
    )
    result = gate.run_phase_c(api, "ns", ["w1", "w2"])
    assert result["passed"] is True
    assert result["appNamespaceRequests"] == {"cpuMillicores": 500, "memoryBytes": 2**30}
    assert result["allNamespaceRequestsOnWorkers"]["cpuMillicores"] == 1000
    assert result["allocatablePerWorker"] == {
        "w1": {"cpuMillicores": 4000, "memoryBytes": 8 * 2**30},
        "w2": {"cpuMillicores": 4000, "memoryBytes": 8 * 2**30},
    }
    assert result["headroom"]["cpu"] == 0.875
    assert result["missingWorkers"] == []


def test_run_phase_c_fails_below_headroom_floor():
    api = _phase_c_api(
        pods=[_phase_c_pod("w1", "ns", cpu="3500m", memory="1Gi")],
        nodes=[_phase_c_node("w1")],
    )
    result = gate.run_phase_c(api, "ns", ["w1"])
    assert result["passed"] is False
    assert result["headroom"]["cpu"] == 0.125


def test_run_phase_c_fails_when_a_worker_is_missing():
    api = _phase_c_api(pods=[], nodes=[_phase_c_node("w1")])
    result = gate.run_phase_c(api, "ns", ["w1", "w9"])
    assert result["passed"] is False
    assert result["missingWorkers"] == ["w9"]


def test_run_phase_c_zero_allocatable_reads_as_no_headroom():
    api = _phase_c_api(pods=[], nodes=[])
    result = gate.run_phase_c(api, "ns", ["w1"])
    assert result["headroom"] == {"cpu": 0.0, "memory": 0.0}
    assert result["passed"] is False


def test_pod_without_spec_or_status_is_skipped():
    pod = MagicMock()
    pod.spec = None
    pod.status = None
    api = _phase_c_api(pods=[pod], nodes=[_phase_c_node("w1")])
    result = gate.run_phase_c(api, "ns", ["w1"])
    assert result["allNamespaceRequestsOnWorkers"] == {"cpuMillicores": 0, "memoryBytes": 0}


# ── summary lines ─────────────────────────────────────────────────────


def _full_artifact(passed=True):
    return {
        "phaseA": {
            "levels": [
                {
                    "target": 0.25,
                    "passed": passed,
                    "bestConsecutive": 3 if passed else 1,
                    "requiredConsecutive": 3,
                    "totalAttempts": 3 if passed else 6,
                }
            ],
            "passed": passed,
        },
        "phaseB": {
            "antiAffine": {
                "passed": passed,
                "schedulingLatencySeconds": 7.0,
                "verification": {"services": [{"ok": passed}, {"ok": True}]},
            },
            "packed": {
                "passed": True,
                "schedulingLatencySeconds": 3.0,
                "verification": {"services": [{"ok": True}, {"ok": True}]},
            },
            "passed": passed,
        },
        "phaseC": {"passed": passed, "headroom": {"cpu": 0.62, "memory": 0.81}},
        "passed": passed,
    }


def test_summary_lines_pass_verdicts():
    lines = gate.summary_lines(_full_artifact(passed=True))
    assert lines[0] == "PASS  phase-A f=0.25  best streak 3/3 in 3 attempt(s)"
    assert "PASS  phase-B r=3 anti-affine  2/2 services verified (latency 7.0s)" in lines
    assert any(line.startswith("PASS  phase-C capacity  headroom cpu 62%") for line in lines)
    assert lines[-1] == "OVERALL: PASS"


def test_summary_lines_fail_verdicts():
    lines = gate.summary_lines(_full_artifact(passed=False))
    assert lines[0].startswith("FAIL  phase-A f=0.25")
    assert any(line.startswith("FAIL  phase-B r=3 anti-affine  1/2") for line in lines)
    assert lines[-1] == "OVERALL: FAIL"


def test_summary_lines_partial_artifact_only_overall():
    assert gate.summary_lines({"passed": False}) == ["OVERALL: FAIL"]


# ── main ──────────────────────────────────────────────────────────────


@pytest.fixture
def summary_file(tmp_path):
    summary = {
        "strategies": {
            "default": {
                "iterations": [
                    {"podPlacements": {"a-aa1-bb2": "w1", "b-aa1-bb2": "w2", "c-aa1-bb2": "w2"}}
                ],
                "aggregated": {"routeViewAggregate": [{"route": "a->b"}, {"route": "b->c"}]},
            }
        }
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary))
    return str(path)


def _wire_main(monkeypatch, phase_a=None, phase_b=None, phase_c=None):
    api = MagicMock()
    monkeypatch.setattr(gate.engine.K8sApi, "from_cluster", MagicMock(return_value=api))
    restore = MagicMock()
    monkeypatch.setattr(gate.engine, "restore", restore)
    artifact = _full_artifact()
    monkeypatch.setattr(gate, "run_phase_a", MagicMock(return_value=phase_a or artifact["phaseA"]))
    monkeypatch.setattr(gate, "run_phase_b", MagicMock(return_value=phase_b or artifact["phaseB"]))
    monkeypatch.setattr(gate, "run_phase_c", MagicMock(return_value=phase_c or artifact["phaseC"]))
    return api, restore


def _argv(summary_file, out, *extra):
    return [
        "--summary",
        summary_file,
        "--workers",
        ",".join(WORKERS),
        "-o",
        out,
        *extra,
    ]


def test_main_pass_writes_artifact_and_returns_zero(monkeypatch, tmp_path, capsys, summary_file):
    api, restore = _wire_main(monkeypatch)
    out = str(tmp_path / "artifact.json")
    assert gate.main(_argv(summary_file, out)) == 0
    artifact = json.loads(Path(out).read_text())
    assert artifact["schema"] == gate.SCHEMA == "chaosprobe/m1b-gate-artifact/v2"
    assert artifact["passed"] is True
    assert artifact["workers"] == WORKERS
    assert artifact["namespace"] == "online-boutique"
    assert artifact["config"]["levels"] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert artifact["phaseA"]["passed"] is True
    assert "startedAt" in artifact and "finishedAt" in artifact
    output = capsys.readouterr().out
    assert f"Gate artifact written to {out}" in output
    assert "OVERALL: PASS" in output
    restore.assert_not_called()  # no --restore-on-exit
    # phases received the parsed graph + workers
    args = gate.run_phase_a.call_args.args
    assert args[0] is api
    assert args[2] == [("a", "b", 1.0), ("b", "c", 1.0)]
    assert args[3] == ["a", "b", "c"]


def test_main_failure_returns_one(monkeypatch, tmp_path, capsys, summary_file):
    _wire_main(monkeypatch, phase_c={"passed": False, "headroom": {"cpu": 0.1, "memory": 0.2}})
    out = str(tmp_path / "artifact.json")
    assert gate.main(_argv(summary_file, out)) == 1
    assert json.loads(Path(out).read_text())["passed"] is False
    assert "OVERALL: FAIL" in capsys.readouterr().out


def test_main_restore_on_exit_restores_on_success(monkeypatch, tmp_path, summary_file):
    api, restore = _wire_main(monkeypatch)
    out = str(tmp_path / "artifact.json")
    assert gate.main(_argv(summary_file, out, "--restore-on-exit")) == 0
    restore.assert_called_once_with(api, "online-boutique", timeout=300.0)


def test_main_exception_still_restores_and_writes_artifact(monkeypatch, tmp_path, summary_file):
    api, restore = _wire_main(monkeypatch)
    monkeypatch.setattr(gate, "run_phase_b", MagicMock(side_effect=RuntimeError("node down")))
    out = str(tmp_path / "artifact.json")
    with pytest.raises(RuntimeError, match="node down"):
        gate.main(_argv(summary_file, out, "--restore-on-exit"))
    restore.assert_called_once()
    artifact = json.loads(Path(out).read_text())
    assert artifact["passed"] is False
    assert "node down" in artifact["aborted"]
    assert artifact["phaseA"]["passed"] is True  # partial record survives


def test_main_sigint_still_restores_and_writes_artifact(monkeypatch, tmp_path, summary_file):
    _, restore = _wire_main(monkeypatch)
    monkeypatch.setattr(gate, "run_phase_a", MagicMock(side_effect=KeyboardInterrupt()))
    out = str(tmp_path / "artifact.json")
    with pytest.raises(KeyboardInterrupt):
        gate.main(_argv(summary_file, out, "--restore-on-exit"))
    restore.assert_called_once()
    assert "KeyboardInterrupt" in json.loads(Path(out).read_text())["aborted"]


def test_main_restore_failure_never_masks_the_run(monkeypatch, tmp_path, capsys, summary_file):
    _, restore = _wire_main(monkeypatch)
    restore.side_effect = RuntimeError("api gone")
    out = str(tmp_path / "artifact.json")
    assert gate.main(_argv(summary_file, out, "--restore-on-exit")) == 0
    assert "restore-on-exit failed: api gone" in capsys.readouterr().err


def test_main_custom_gate_knobs(monkeypatch, tmp_path, summary_file):
    _wire_main(monkeypatch)
    out = str(tmp_path / "artifact.json")
    argv = _argv(
        summary_file,
        out,
        "-n",
        "other-ns",
        "--levels",
        "0,1",
        "--tolerance",
        "0.1",
        "--consecutive",
        "2",
        "--max-attempts",
        "4",
        "--seed",
        "7",
        "--timeout",
        "60",
        "--settle-seconds",
        "15",
        "--settle-timeout",
        "90",
    )
    assert gate.main(argv) == 0
    artifact = json.loads(Path(out).read_text())
    assert artifact["namespace"] == "other-ns"
    assert artifact["config"] == {
        "levels": [0.0, 1.0],
        "tolerance": 0.1,
        "consecutive": 2,
        "maxAttempts": 4,
        "seed": 7,
        "timeoutSeconds": 60.0,
        "settleSeconds": 15.0,
        "settleTimeoutSeconds": 90.0,
        "attemptProtocol": "restore-to-default,quiesce,solve,apply(r=1),schedule,verify-live",
    }
    cfg = gate.run_phase_a.call_args.args[5]
    assert cfg.consecutive == 2 and cfg.max_attempts == 4 and cfg.seed == 7
    assert cfg.settle_seconds == 15.0 and cfg.settle_timeout == 90.0


def test_main_rejects_bad_workers(monkeypatch, summary_file):
    with pytest.raises(SystemExit, match="duplicate"):
        gate.main(["--summary", summary_file, "--workers", "w1,w1"])


def test_main_rejects_bad_levels(monkeypatch, summary_file):
    with pytest.raises(SystemExit, match="in \\[0, 1\\]"):
        gate.main(["--summary", summary_file, "--workers", "w1,w2", "--levels", "2.0"])


def test_main_rejects_summary_without_edges(monkeypatch, tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"strategies": {}}))
    with pytest.raises(SystemExit, match="no inter-service edges"):
        gate.main(["--summary", str(path), "--workers", "w1,w2"])


def test_parser_requires_exactly_one_graph_source(tmp_path):
    with pytest.raises(SystemExit):
        gate.build_parser().parse_args(["--workers", "w1,w2"])
    with pytest.raises(SystemExit):
        gate.build_parser().parse_args(
            ["--summary", "s.json", "--topology", "t.json", "--workers", "w1,w2"]
        )


def test_main_rejects_topology_without_edges(tmp_path):
    """The strict loader rejects an empty edge list before the gate's own check."""
    path = tmp_path / "empty-topology.json"
    path.write_text(json.dumps({"services": ["a"], "edges": []}))
    with pytest.raises(ValueError, match="'edges' must be a non-empty list"):
        gate.main(["--topology", str(path), "--workers", "w1,w2"])


def test_main_loads_static_topology(monkeypatch, tmp_path):
    """--topology routes through load_static_topology and into the gate run."""
    path = tmp_path / "topology.json"
    path.write_text(json.dumps({"services": ["a", "b"], "edges": [["a", "b"]]}))
    seen = {}

    def fake_from_cluster():
        raise RuntimeError("stop-after-graph-load")

    monkeypatch.setattr(gate.engine.K8sApi, "from_cluster", staticmethod(fake_from_cluster))
    real_load = gate.fs.load_static_topology

    def spy_load(p):
        seen["path"] = p
        return real_load(p)

    monkeypatch.setattr(gate.fs, "load_static_topology", spy_load)
    with pytest.raises(RuntimeError, match="stop-after-graph-load"):
        gate.main(["--topology", str(path), "--workers", "w1,w2"])
    assert seen["path"] == str(path)
