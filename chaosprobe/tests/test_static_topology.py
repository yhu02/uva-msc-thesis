"""Tests for fraction_solver.load_static_topology + the hotel-reservation topology.

The static topology is the M2 solver-gate stand-in graph for workloads with no
measured ``summary.json`` yet (DESIGN §7 — hotelReservation). These tests cover
the loader at 100 % (happy path + every rejection branch) and pin the committed
``scenarios/hotel-reservation/topology.json`` to the upstream-verified service
and edge counts so silent drift fails CI.
"""

import json
from pathlib import Path

import pytest

from chaosprobe.placement import fraction_solver as fs

REPO_TOPOLOGY = (
    Path(__file__).resolve().parents[1] / "scenarios" / "hotel-reservation" / "topology.json"
)


def write_topology(tmp_path, payload):
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(payload))
    return str(path)


def valid_payload():
    return {
        "workload": "demo",
        "comment": "metadata keys are ignored",
        "services": ["a", "b", "c", "lonely"],
        "edges": [["b", "a"], ["a", "c"]],
    }


# ──────────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────────


def test_load_static_topology_happy_path(tmp_path):
    edges, services = fs.load_static_topology(write_topology(tmp_path, valid_payload()))
    assert edges == [("a", "c", 1.0), ("b", "a", 1.0)]  # sorted, uniform weight
    assert services == ["a", "b", "c", "lonely"]  # sorted; edge-less services kept


def test_load_static_topology_same_shape_as_measured_loader(tmp_path):
    """The static loader's output feeds the same downstream API as the measured one."""
    edges, services = fs.load_static_topology(write_topology(tmp_path, valid_payload()))
    packed = {svc: "node-0" for svc in services}
    spread = {svc: f"node-{i}" for i, svc in enumerate(services)}
    assert fs.achieved_fraction(packed, edges) == 0.0
    assert fs.achieved_fraction(spread, edges) == 1.0
    assert fs.active_services(edges) == ["a", "b", "c"]


# ──────────────────────────────────────────────────────────────────────
# Rejection branches
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda p: p.pop("services"), "'services' must be a non-empty list"),
        (lambda p: p.update(services=[]), "'services' must be a non-empty list"),
        (lambda p: p.update(services="a,b"), "'services' must be a non-empty list"),
        (lambda p: p.update(services=["a", 2]), "must be non-empty strings"),
        (lambda p: p.update(services=["a", ""]), "must be non-empty strings"),
        (lambda p: p.update(services=["a", "b", "c", "a"]), "duplicate names"),
        (lambda p: p.pop("edges"), "'edges' must be a non-empty list"),
        (lambda p: p.update(edges=[]), "'edges' must be a non-empty list"),
        (lambda p: p.update(edges="a->b"), "'edges' must be a non-empty list"),
        (lambda p: p.update(edges=["a->b"]), r"\[src, dst\] pair"),
        (lambda p: p.update(edges=[["a", "b", "c"]]), r"\[src, dst\] pair"),
        (lambda p: p.update(edges=[["a", 2]]), r"\[src, dst\] pair"),
        (lambda p: p.update(edges=[["a", ""]]), r"\[src, dst\] pair"),
        (lambda p: p.update(edges=[["a", "a"]]), "self-loop"),
        (lambda p: p.update(edges=[["a", "b"], ["a", "b"]]), "duplicate edge"),
        (lambda p: p.update(edges=[["a", "zz"]]), "undeclared service"),
        (lambda p: p.update(edges=[["zz", "a"]]), "undeclared service"),
    ],
)
def test_load_static_topology_rejects_malformed_files(tmp_path, mutate, match):
    payload = valid_payload()
    mutate(payload)
    with pytest.raises(ValueError, match=match):
        fs.load_static_topology(write_topology(tmp_path, payload))


# ──────────────────────────────────────────────────────────────────────
# The committed hotel-reservation topology
# ──────────────────────────────────────────────────────────────────────


def test_hotel_reservation_topology_counts_and_shape():
    edges, services = fs.load_static_topology(str(REPO_TOPOLOGY))
    assert len(services) == 19  # 8 app + consul + jaeger + 6 mongodb + 3 memcached
    assert len(edges) == 16
    assert all(weight == 1.0 for _, _, weight in edges)
    # Spot-check the architecture spine (DSB docs: frontend fan-out, search -> geo/rate).
    pairs = {(src, dst) for src, dst, _ in edges}
    assert ("frontend", "search") in pairs
    assert ("search", "geo") in pairs
    assert ("search", "rate") in pairs
    assert ("reservation", "memcached-reserve") in pairs
    # consul/jaeger are placeable services but carry no request-path edges.
    edge_endpoints = {endpoint for pair in pairs for endpoint in pair}
    assert "consul" not in edge_endpoints
    assert "jaeger" not in edge_endpoints
    assert {"consul", "jaeger"} <= set(services)


def test_hotel_reservation_topology_drives_the_solver():
    """M2 gate plumbing: the static graph feeds solve() end to end at the pinned N=8.

    Every f target is hit exactly (the 16 uniform edges make
    {0, .25, .5, .75, 1} multiples of the 1/16 quantum) within a small seed
    sweep — a single seed may stall in a local optimum on this tree-shaped
    graph (e.g. seed 42 alone yields 1/16 for f=0), which is the solver's
    documented restart territory, not a topology defect.
    """
    edges, services = fs.load_static_topology(str(REPO_TOPOLOGY))
    for target in (0.0, 0.25, 0.5, 0.75, 1.0):
        best_gap = None
        for seed in range(5):
            solution = fs.solve(edges, services, n_nodes=8, target_f=target, seed=seed)
            assert set(solution.assignment) == set(services)
            # Reported fraction must agree with the shared scorer.
            recomputed = fs.achieved_fraction(solution.assignment, edges)
            assert solution.achieved_f == pytest.approx(recomputed)
            gap = abs(solution.achieved_f - target)
            best_gap = gap if best_gap is None else min(best_gap, gap)
        assert best_gap == pytest.approx(0.0), f"target {target} unreachable in seed sweep"
