"""Tests for chaosprobe/placement/fraction_solver.py (v2 / M1a).

Anti-self-verification (WORKPLAN M1a exit criterion): `independent_fraction`
below is a SECOND, independently written implementation of the cross-node
fraction — complement-based (cross = placed − same-node) over per-node service
sets, a different data layout and code path from the module's edge-walking
`achieved_fraction`.  Property tests cross-check the solver's reported
`achieved_f` against it on dozens of seeded random graphs, and the enumerator
against brute force over ALL labelled assignments on small graphs.
"""

import itertools
import json
import random

import pytest

from chaosprobe.placement import fraction_solver as fs

# ──────────────────────────────────────────────────────────────────────
# Independent second implementation (different code path, by design)
# ──────────────────────────────────────────────────────────────────────


def independent_fraction(assignment, edges):
    """Cross-node fraction computed the *other* way around.

    Groups services into per-node sets, sums the weight of placed edges whose
    endpoints share a set (same-node weight), and returns the complement
    ``(placed − same) / placed`` — never comparing node labels edge-by-edge.
    """
    groups = {}
    for svc, node in assignment.items():
        groups.setdefault(node, set()).add(svc)
    placed_services = set(assignment)
    placed_weight = 0.0
    same_weight = 0.0
    for src, dst, weight in edges:
        if weight == 0 or src not in placed_services or dst not in placed_services:
            continue
        placed_weight += weight
        for members in groups.values():
            if src in members and dst in members:
                same_weight += weight
                break
    if placed_weight == 0:
        raise ValueError("undefined")
    return (placed_weight - same_weight) / placed_weight


def _random_graph(rng, n_services=None, allow_self_loops=True):
    n_services = n_services or rng.randint(2, 12)
    services = [f"svc{i}" for i in range(n_services)]
    edges = []
    for _ in range(rng.randint(1, 20)):
        src, dst = rng.choice(services), rng.choice(services)
        if not allow_self_loops and src == dst:
            continue
        edges.append((src, dst, round(rng.uniform(0.1, 50.0), 3)))
    if not edges:
        edges = [(services[0], services[1], 1.0)]
    return services, edges


# ──────────────────────────────────────────────────────────────────────
# Property tests: solver vs independent implementation (pre-registered)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", range(40))
def test_property_solver_achieved_f_matches_independent_impl(case):
    rng = random.Random(1000 + case)
    services, edges = _random_graph(rng)
    n_nodes = rng.randint(2, 5)
    target = rng.choice([0.0, 0.25, 0.5, 0.75, 1.0, round(rng.random(), 3)])
    solution = fs.solve(edges, services, n_nodes, target, seed=case)
    assert solution.achieved_f == pytest.approx(independent_fraction(solution.assignment, edges))
    assert solution.accepted == (abs(solution.achieved_f - target) <= fs.TARGET_TOLERANCE)
    assert set(solution.assignment) == set(services)
    assert all(0 <= node < n_nodes for node in solution.assignment.values())


@pytest.mark.parametrize("case", range(30))
def test_property_achieved_fraction_matches_independent_impl(case):
    rng = random.Random(2000 + case)
    services, edges = _random_graph(rng)
    # Random (possibly partial) assignment, exercising the skip-unassigned path.
    assigned = [svc for svc in services if rng.random() > 0.2] or services[:2]
    assignment = {svc: rng.randrange(4) for svc in assigned}
    try:
        expected = independent_fraction(assignment, edges)
    except ValueError:
        with pytest.raises(ValueError):
            fs.achieved_fraction(assignment, edges)
        return
    assert fs.achieved_fraction(assignment, edges) == pytest.approx(expected)


@pytest.mark.parametrize("case", range(15))
def test_property_enumerator_matches_brute_force_over_all_assignments(case):
    """Exhaustive enumeration (canonical partitions) == brute force over all
    n^s labelled assignments, fractions computed by the INDEPENDENT impl."""
    rng = random.Random(3000 + case)
    services, edges = _random_graph(rng, n_services=rng.randint(2, 5))
    n_nodes = rng.randint(2, 3)
    brute = set()
    for combo in itertools.product(range(n_nodes), repeat=len(services)):
        brute.add(round(independent_fraction(dict(zip(services, combo)), edges), 9))
    assert fs.enumerate_reachable(edges, services, n_nodes) == sorted(brute)


# ──────────────────────────────────────────────────────────────────────
# achieved_fraction — hand-computed ground truth
# ──────────────────────────────────────────────────────────────────────


def test_achieved_fraction_weighted_mid_value():
    edges = [("a", "b", 1.0), ("b", "c", 3.0)]
    assert fs.achieved_fraction({"a": 0, "b": 1, "c": 1}, edges) == pytest.approx(0.25)
    assert fs.achieved_fraction({"a": 0, "b": 0, "c": 1}, edges) == pytest.approx(0.75)


def test_achieved_fraction_accepts_node_names():
    assert fs.achieved_fraction({"a": "w1", "b": "w2"}, [("a", "b", 1.0)]) == 1.0


def test_achieved_fraction_skips_unassigned_and_zero_weight():
    edges = [("a", "b", 1.0), ("a", "ghost", 9.0), ("a", "c", 0.0)]
    assert fs.achieved_fraction({"a": 0, "b": 0, "c": 1}, edges) == 0.0


def test_achieved_fraction_self_loop_never_crosses():
    edges = [("a", "a", 1.0), ("a", "b", 1.0)]
    assert fs.achieved_fraction({"a": 0, "b": 1}, edges) == pytest.approx(0.5)


def test_achieved_fraction_negative_weight_raises():
    with pytest.raises(ValueError, match="negative"):
        fs.achieved_fraction({"a": 0, "b": 1}, [("a", "b", -1.0)])


def test_achieved_fraction_undefined_raises():
    with pytest.raises(ValueError, match="undefined"):
        fs.achieved_fraction({}, [("a", "b", 1.0)])


# ──────────────────────────────────────────────────────────────────────
# solve — known-optimal cuts and the rejection rule
# ──────────────────────────────────────────────────────────────────────

TRIANGLE = [("a", "b", 1.0), ("b", "c", 1.0), ("a", "c", 1.0)]
STAR = [("hub", "l1", 1.0), ("hub", "l2", 1.0), ("hub", "l3", 1.0)]


def test_solve_trivial_two_services_extremes():
    edges = [("a", "b", 1.0)]
    colocated = fs.solve(edges, ["a", "b"], 2, 0.0, seed=1)
    assert colocated.achieved_f == 0.0 and colocated.accepted
    split = fs.solve(edges, ["a", "b"], 2, 1.0, seed=1)
    assert split.achieved_f == 1.0 and split.accepted
    assert split.assignment["a"] != split.assignment["b"]


def test_solve_single_node_only_reaches_zero():
    solution = fs.solve([("a", "b", 1.0)], ["a", "b"], 1, 1.0, seed=0)
    assert solution.achieved_f == 0.0
    assert not solution.accepted  # gap 1.0 > 0.05 -> pre-registered rejection


def test_solve_triangle_optimal_cuts():
    assert fs.solve(TRIANGLE, ["a", "b", "c"], 2, 0.0, seed=0).achieved_f == 0.0
    # On 2 nodes a triangle can cut at most 2 of 3 edges.
    assert fs.solve(TRIANGLE, ["a", "b", "c"], 2, 1.0, seed=0).achieved_f == pytest.approx(2 / 3)
    assert fs.solve(TRIANGLE, ["a", "b", "c"], 3, 1.0, seed=0).achieved_f == 1.0


def test_solve_star_hits_known_mid_value():
    services = ["hub", "l1", "l2", "l3"]
    solution = fs.solve(STAR, services, 2, 1 / 3, seed=0)
    assert solution.achieved_f == pytest.approx(1 / 3)
    assert solution.accepted


def test_solve_weighted_mid_target():
    edges = [("a", "b", 1.0), ("b", "c", 3.0)]
    solution = fs.solve(edges, ["a", "b", "c"], 2, 0.25, seed=0)
    assert solution.achieved_f == pytest.approx(0.25)
    assert solution.assignment["b"] == solution.assignment["c"]


def test_solve_rejection_rule_fires_on_unreachable_target():
    # Triangle on 2 nodes reaches only {0, 2/3}; target 0.4 is >0.05 from both.
    solution = fs.solve(TRIANGLE, ["a", "b", "c"], 2, 0.4, seed=0)
    assert solution.achieved_f == pytest.approx(2 / 3)  # nearest reachable
    assert not solution.accepted
    assert solution.target_f == 0.4


def test_solve_deterministic_under_fixed_seed():
    rng = random.Random(99)
    services, edges = _random_graph(rng, n_services=9)
    first = fs.solve(edges, services, 4, 0.5, seed=42)
    second = fs.solve(edges, services, 4, 0.5, seed=42)
    assert first.assignment == second.assignment
    assert first.achieved_f == second.achieved_f
    assert first.trace == second.trace


def test_solve_isolated_service_is_assigned():
    solution = fs.solve([("a", "b", 1.0)], ["a", "b", "lonely"], 2, 0.0, seed=0)
    assert "lonely" in solution.assignment


def test_solve_trace_records_search_metadata():
    solution = fs.solve(TRIANGLE, ["a", "b", "c"], 2, 0.0, seed=7, restarts=3)
    trace = solution.trace
    assert trace["method"] == "greedy-edge-cut + local-search"
    assert trace["seed"] == 7
    assert 1 <= trace["restartsRun"] <= 3  # may break early on an exact hit
    assert len(trace["restartGaps"]) == trace["restartsRun"]
    assert "nodeLoads" not in trace  # no capacity given


def test_solve_local_search_pass_cap_zero_skips_refinement():
    solution = fs.solve(TRIANGLE, ["a", "b", "c"], 2, 0.0, seed=0, max_local_search_passes=0)
    assert solution.trace["localSearchPasses"] == 0
    assert solution.trace["localSearchMoves"] == 0


# ── capacity ──────────────────────────────────────────────────────────


def test_solve_capacity_forces_split_and_rejection():
    capacity = {"a": 1.0, "b": 1.0}
    solution = fs.solve(
        [("a", "b", 1.0)], ["a", "b"], 2, 0.0, capacity=capacity, seed=0, node_capacity=1.0
    )
    assert solution.achieved_f == 1.0  # colocation impossible under capacity
    assert not solution.accepted
    assert solution.trace["capacityViolations"] == 0
    assert solution.trace["nodeLoads"] == [1.0, 1.0]


def test_solve_capacity_respected_on_cycle():
    # 4-cycle, requests 1 each, node budget 2 on 2 nodes: best f=0 packing is
    # two adjacent pairs -> exactly 2 of 4 edges cross.
    cycle = [("a", "b", 1.0), ("b", "c", 1.0), ("c", "d", 1.0), ("d", "a", 1.0)]
    capacity = {svc: 1.0 for svc in "abcd"}
    solution = fs.solve(cycle, list("abcd"), 2, 0.0, capacity=capacity, seed=3, node_capacity=2.0)
    assert solution.achieved_f == pytest.approx(0.5)
    assert solution.trace["capacityViolations"] == 0
    assert all(load <= 2.0 for load in solution.trace["nodeLoads"])


def test_solve_capacity_missing_service_requests_zero():
    capacity = {"a": 1.0}  # b absent -> requests 0 (kube convention)
    solution = fs.solve(
        [("a", "b", 1.0)], ["a", "b"], 2, 0.0, capacity=capacity, seed=0, node_capacity=1.0
    )
    assert solution.achieved_f == 0.0 and solution.accepted


def test_solve_capacity_overflow_falls_back_with_violation_recorded():
    capacity = {"a": 2.0, "b": 2.0}
    solution = fs.solve(
        [("a", "b", 1.0)], ["a", "b"], 2, 0.0, capacity=capacity, seed=0, node_capacity=1.0
    )
    assert set(solution.assignment) == {"a", "b"}
    assert solution.trace["capacityViolations"] >= 2  # nothing ever fits


# ── input validation ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(n_nodes=0), "n_nodes"),
        (dict(target_f=1.5), "target_f"),
        (dict(target_f=-0.1), "target_f"),
        (dict(restarts=0), "restarts"),
        (dict(capacity={"a": 1.0}), "node_capacity"),
        (dict(capacity={"a": 1.0}, node_capacity=0.0), "node_capacity"),
    ],
)
def test_solve_validates_arguments(kwargs, match):
    base = dict(edges=[("a", "b", 1.0)], services=["a", "b"], n_nodes=2, target_f=0.5, seed=0)
    base.update(kwargs)
    with pytest.raises(ValueError, match=match):
        fs.solve(**base)


def test_solve_rejects_empty_services():
    with pytest.raises(ValueError, match="services"):
        fs.solve([("a", "b", 1.0)], [], 2, 0.5, seed=0)


def test_solve_rejects_unknown_edge_endpoint():
    with pytest.raises(ValueError, match="not in `services`"):
        fs.solve([("a", "ghost", 1.0)], ["a", "b"], 2, 0.5, seed=0)


def test_solve_rejects_negative_weight():
    with pytest.raises(ValueError, match="negative"):
        fs.solve([("a", "b", -2.0)], ["a", "b"], 2, 0.5, seed=0)


def test_solve_rejects_all_zero_weights():
    with pytest.raises(ValueError, match="no positive-weight"):
        fs.solve([("a", "b", 0.0)], ["a", "b"], 2, 0.5, seed=0)


# ── local search internals (deterministic branch coverage) ───────────


def test_local_search_swap_escapes_capacity_locked_state():
    # a-b and c-d both cross; every single move is capacity-blocked (each node
    # is full), but swapping b and c reaches f=0 without touching loads.
    edges = [("a", "b", 1.0), ("c", "d", 1.0)]
    adj = fs._adjacency(edges)
    assignment = {"a": 0, "b": 1, "c": 0, "d": 1}
    loads = [2.0, 2.0]
    capacity = {svc: 1.0 for svc in "abcd"}
    cross, passes, moves = fs._local_search(
        assignment, loads, adj, 2.0, 2.0, 2, 0.0, capacity, 2.0, 50
    )
    assert cross == 0.0
    assert moves >= 1
    assert assignment["a"] == assignment["b"]
    assert assignment["c"] == assignment["d"]
    assert loads == [2.0, 2.0]


def test_local_search_single_move_improves_without_capacity():
    edges = [("a", "b", 1.0)]
    adj = fs._adjacency(edges)
    assignment = {"a": 0, "b": 0}
    cross, _, moves = fs._local_search(
        assignment, [0.0, 0.0], adj, 1.0, 0.0, 2, 1.0, None, None, 50
    )
    assert cross == 1.0 and moves == 1
    assert assignment["a"] != assignment["b"]


# ──────────────────────────────────────────────────────────────────────
# enumerate_reachable — hand-computable reachable sets
# ──────────────────────────────────────────────────────────────────────


def test_enumerate_path_graph_three_services_two_nodes():
    edges = [("a", "b", 1.0), ("b", "c", 1.0)]
    assert fs.enumerate_reachable(edges, ["a", "b", "c"], 2) == [0.0, 0.5, 1.0]


def test_enumerate_triangle_two_vs_three_nodes():
    services = ["a", "b", "c"]
    assert fs.enumerate_reachable(TRIANGLE, services, 2) == pytest.approx([0.0, 2 / 3])
    assert fs.enumerate_reachable(TRIANGLE, services, 3) == pytest.approx([0.0, 2 / 3, 1.0])


def test_enumerate_star_two_nodes():
    services = ["hub", "l1", "l2", "l3"]
    expected = [0.0, 1 / 3, 2 / 3, 1.0]
    assert fs.enumerate_reachable(STAR, services, 2) == pytest.approx(expected)


def test_enumerate_weighted_path():
    edges = [("a", "b", 1.0), ("b", "c", 3.0)]
    assert fs.enumerate_reachable(edges, ["a", "b", "c"], 2) == pytest.approx(
        [0.0, 0.25, 0.75, 1.0]
    )


def test_enumerate_caps_blocks_at_active_service_count():
    # 2 active services on 5 nodes still yields only {0, 1}.
    assert fs.enumerate_reachable([("a", "b", 1.0)], ["a", "b"], 5) == [0.0, 1.0]


def test_enumerate_isolated_services_do_not_change_the_set():
    services = ["a", "b", "c", "idle1", "idle2"]
    edges = [("a", "b", 1.0), ("b", "c", 1.0)]
    assert fs.enumerate_reachable(edges, services, 2) == [0.0, 0.5, 1.0]


def test_enumerate_self_loops_only_yields_constant_zero():
    assert fs.enumerate_reachable([("a", "a", 2.0)], ["a", "b"], 4) == [0.0]


def test_enumerate_sampled_path_is_seeded_subset():
    edges = [("a", "b", 1.0), ("b", "c", 1.0)]
    services = ["a", "b", "c"]
    exact = fs.enumerate_reachable(edges, services, 2)
    sampled = fs.enumerate_reachable(edges, services, 2, samples=200, seed=5, exhaustive_budget=0)
    again = fs.enumerate_reachable(edges, services, 2, samples=200, seed=5, exhaustive_budget=0)
    assert sampled == again  # deterministic under a fixed seed
    assert set(sampled) <= set(exact)
    assert {0.0, 1.0} <= set(sampled)  # solver refinement finds the extremes


def test_enumerate_validates_arguments():
    edges = [("a", "b", 1.0)]
    with pytest.raises(ValueError, match="n_nodes"):
        fs.enumerate_reachable(edges, ["a", "b"], 0)
    with pytest.raises(ValueError, match="samples"):
        fs.enumerate_reachable(edges, ["a", "b"], 2, samples=-1)
    with pytest.raises(ValueError, match="no positive-weight"):
        fs.enumerate_reachable([], ["a"], 2)


# ── enumeration scaffolding ──────────────────────────────────────────


def test_count_canonical_assignments_known_values():
    assert fs.count_canonical_assignments(3, 2) == 4  # S(3,1)+S(3,2) = 1+3
    assert fs.count_canonical_assignments(3, 3) == 5  # Bell(3)
    assert fs.count_canonical_assignments(11, 4) == 175_275  # the M1a N=4 case
    assert fs.count_canonical_assignments(11, 11) == 678_570  # Bell(11)
    assert fs.count_canonical_assignments(4, 9) == fs.count_canonical_assignments(4, 4)


def test_count_canonical_assignments_validates():
    with pytest.raises(ValueError):
        fs.count_canonical_assignments(0, 2)
    with pytest.raises(ValueError):
        fs.count_canonical_assignments(2, 0)


def test_enumeration_method_thresholds():
    assert fs.enumeration_method(3, 2) == "exhaustive"
    assert fs.enumeration_method(3, 2, exhaustive_budget=3) == "sampled"  # count 4 > 3
    assert fs.enumeration_method(0, 4) == "exhaustive"


def test_active_services_excludes_zero_weight_and_self_loops():
    edges = [("a", "b", 1.0), ("c", "c", 5.0), ("d", "e", 0.0)]
    assert fs.active_services(edges) == ["a", "b"]


def test_restricted_growth_strings_enumerate_partitions_exactly():
    seen = [tuple(blocks) for blocks in fs._iter_restricted_growth_strings(3, 2)]
    assert seen == [(0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1)]
    assert [tuple(b) for b in fs._iter_restricted_growth_strings(1, 1)] == [(0,)]
    count = sum(1 for _ in fs._iter_restricted_growth_strings(7, 4))
    assert count == fs.count_canonical_assignments(7, 4)


# ──────────────────────────────────────────────────────────────────────
# load_dependency_graph
# ──────────────────────────────────────────────────────────────────────


def _write_summary(tmp_path, summary):
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary))
    return str(path)


def _strategy(route_entries, pod_placements=None):
    iterations = []
    if pod_placements is not None:
        iterations = [{"podPlacements": pod_placements}]
    return {
        "iterations": iterations,
        "aggregated": {"routeViewAggregate": route_entries},
    }


def test_load_dependency_graph_extracts_edges_weights_and_services(tmp_path):
    summary = {
        "strategies": {
            "default": _strategy(
                [
                    {"route": "frontend->cartservice", "locust": {"totalRequests": 120}},
                    {"route": "checkoutservice->cartservice,frontend->cartservice"},
                    {"route": "/"},  # north-south: ignored
                ],
                pod_placements={
                    "frontend-abc12-x1y2z": "w1",
                    "cartservice-abc12-x1y2z": "w2",
                    "checkoutservice-abc12-x1y2z": "w1",
                },
            )
        }
    }
    edges, services = fs.load_dependency_graph(_write_summary(tmp_path, summary))
    assert services == ["cartservice", "checkoutservice", "frontend"]
    # frontend->cartservice appears in two entries: 120 (locust) + 1.0 (unweighted).
    assert edges == [
        ("checkoutservice", "cartservice", 1.0),
        ("frontend", "cartservice", 121.0),
    ]


def test_load_dependency_graph_drops_edges_to_unplaced_endpoints(tmp_path):
    summary = {
        "strategies": {
            "default": _strategy(
                [{"route": "loadgenerator->frontend"}, {"route": "frontend->cartservice"}],
                pod_placements={"frontend-aa1-bb2": "w1", "cartservice-aa1-bb2": "w2"},
            )
        }
    }
    edges, services = fs.load_dependency_graph(_write_summary(tmp_path, summary))
    assert services == ["cartservice", "frontend"]
    assert edges == [("frontend", "cartservice", 1.0)]


def test_load_dependency_graph_keeps_max_weight_across_strategies(tmp_path):
    placements = {"a-aa1-bb2": "w1", "b-aa1-bb2": "w2"}
    summary = {
        "strategies": {
            "s1": _strategy([{"route": "a->b", "locust": {"totalRequests": 10}}], placements),
            "s2": _strategy([{"route": "a->b", "locust": {"totalRequests": 90}}], placements),
            "s3": _strategy([{"route": "a->b"}], placements),  # unweighted -> 1.0, ignored by max
        }
    }
    edges, _ = fs.load_dependency_graph(_write_summary(tmp_path, summary))
    assert edges == [("a", "b", 90.0)]


def test_load_dependency_graph_multi_fault_shape_and_placement_union(tmp_path):
    summary = {
        "faults": {
            "pod-delete": {
                "strategies": {
                    "spread": _strategy([{"route": "a->b"}], {"a-aa1-bb2": "w1"}),
                }
            },
            "cpu-hog": {
                "strategies": {
                    "spread": _strategy([{"route": "a->b"}], {"b-aa1-bb2": "w1"}),
                }
            },
        }
    }
    edges, services = fs.load_dependency_graph(_write_summary(tmp_path, summary))
    assert services == ["a", "b"]  # placement union across faults
    assert edges == [("a", "b", 1.0)]


def test_load_dependency_graph_falls_back_to_endpoints_without_placements(tmp_path):
    summary = {"strategies": {"default": _strategy([{"route": "x->y"}])}}
    edges, services = fs.load_dependency_graph(_write_summary(tmp_path, summary))
    assert services == ["x", "y"]
    assert edges == [("x", "y", 1.0)]


def test_load_dependency_graph_tolerates_malformed_blocks(tmp_path):
    summary = {
        "strategies": {
            "broken": None,
            "sparse": {
                "iterations": [None, {"podPlacements": {"a-aa1-bb2": "w1", "b-aa1-bb2": "w2"}}],
                "aggregated": {
                    "routeViewAggregate": [
                        {},  # no route key
                        {"route": "a->b", "locust": {"totalRequests": -5}},  # non-positive -> 1.0
                    ]
                },
            },
        }
    }
    edges, services = fs.load_dependency_graph(_write_summary(tmp_path, summary))
    assert edges == [("a", "b", 1.0)]
    assert services == ["a", "b"]


def test_load_dependency_graph_empty_summary(tmp_path):
    edges, services = fs.load_dependency_graph(_write_summary(tmp_path, {}))
    assert edges == [] and services == []


# ── shared helpers (moved here from scripts/cross_node_fraction.py) ──


@pytest.mark.parametrize(
    "pod, dep",
    [
        ("adservice-79c4dfd9c5-5hlpz", "adservice"),
        ("redis-cart-6dc9b8f5d-abcde", "redis-cart"),
    ],
)
def test_deployment_of(pod, dep):
    assert fs.deployment_of(pod) == dep


def test_edges_from_route_view_splits_comma_joined():
    rva = [{"route": "a->b,c->b"}, {"route": "/"}, {"route": None}]
    assert fs.edges_from_route_view(rva) == {("a", "b"), ("c", "b")}


def test_strategies_from_summary_flattens_both_shapes():
    flat = {"strategies": {"s1": {"x": 1}}}
    nested = {"faults": {"f1": {"strategies": {"s2": {"y": 2}}}, "f2": None}}
    assert fs.strategies_from_summary(flat) == {"s1": {"x": 1}}
    assert fs.strategies_from_summary(nested) == {"s2": {"y": 2}}
    assert fs.strategies_from_summary({}) == {}


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def _ob_like_summary(tmp_path):
    """A small Online-Boutique-like summary: 4 services, 4 edges."""
    summary = {
        "strategies": {
            "default": _strategy(
                [
                    {"route": "frontend->cartservice"},
                    {"route": "frontend->productcatalogservice"},
                    {"route": "cartservice->redis-cart"},
                    {"route": "frontend->redis-cart"},
                ],
                pod_placements={
                    "frontend-aa1-bb2": "w1",
                    "cartservice-aa1-bb2": "w2",
                    "productcatalogservice-aa1-bb2": "w1",
                    "redis-cart-aa1-bb2": "w2",
                },
            )
        }
    }
    return _write_summary(tmp_path, summary)


def test_cli_target_mode_prints_solution_table(tmp_path, capsys):
    fs.main(["--summary", _ob_like_summary(tmp_path), "--n-nodes", "2", "--target", "0.5"])
    out = capsys.readouterr().out
    assert "Dependency graph: 4 services, 4 edges" in out
    assert "achieved f = 0.5000" in out
    assert "ACCEPTED" in out
    assert "frontend" in out


def test_cli_target_mode_reports_rejection(tmp_path, capsys):
    fs.main(["--summary", _ob_like_summary(tmp_path), "--n-nodes", "1", "--target", "1.0"])
    assert "REJECTED" in capsys.readouterr().out


def test_cli_target_mode_with_capacity_file(tmp_path, capsys):
    capacity_file = tmp_path / "requests.json"
    capacity_file.write_text(
        json.dumps(
            {"frontend": 1.0, "cartservice": 1.0, "productcatalogservice": 1.0, "redis-cart": 1.0}
        )
    )
    fs.main(
        [
            "--summary",
            _ob_like_summary(tmp_path),
            "--n-nodes",
            "2",
            "--target",
            "0.5",
            "--capacity-file",
            str(capacity_file),
            "--node-capacity",
            "2.0",
        ]
    )
    assert "ACCEPTED" in capsys.readouterr().out


def test_cli_enumerate_mode_exhaustive(tmp_path, capsys):
    fs.main(["--summary", _ob_like_summary(tmp_path), "--n-nodes", "4", "--enumerate"])
    out = capsys.readouterr().out
    assert "method: exhaustive" in out
    assert "canonical assignments" in out
    assert "within ±0.05" in out
    assert "0.000" in out and "1.000" in out


def test_cli_enumerate_mode_sampled_via_budget(tmp_path, capsys):
    fs.main(
        [
            "--summary",
            _ob_like_summary(tmp_path),
            "--n-nodes",
            "4",
            "--enumerate",
            "--samples",
            "100",
            "--seed",
            "3",
            "--exhaustive-budget",
            "0",
        ]
    )
    out = capsys.readouterr().out
    assert "method: sampled" in out
    assert "NOT a census" in out


def test_cli_enumerate_elides_large_fraction_lists(tmp_path, capsys):
    # A 10-leaf star with power-of-two weights reaches 2^10 distinct sums.
    entries = [{"route": f"hub->leaf{i}", "locust": {"totalRequests": 2**i}} for i in range(10)]
    placements = {f"leaf{i}-aa1-bb2": "w1" for i in range(10)}
    placements["hub-aa1-bb2"] = "w1"
    summary = {"strategies": {"default": _strategy(entries, placements)}}
    fs.main(["--summary", _write_summary(tmp_path, summary), "--n-nodes", "2", "--enumerate"])
    out = capsys.readouterr().out
    assert "list elided" in out


def test_cli_errors_when_summary_has_no_edges(tmp_path):
    with pytest.raises(SystemExit, match="no inter-service edges"):
        fs.main(["--summary", _write_summary(tmp_path, {}), "--n-nodes", "2", "--target", "0.5"])


def test_cli_requires_exactly_one_mode(tmp_path):
    with pytest.raises(SystemExit):
        fs.main(["--summary", "x.json", "--n-nodes", "2"])
    with pytest.raises(SystemExit):
        fs.main(["--summary", "x.json", "--n-nodes", "2", "--target", "0.5", "--enumerate"])


def test_cli_converts_solver_value_errors_to_clean_exit(tmp_path):
    with pytest.raises(SystemExit, match="target_f"):
        fs.main(["--summary", _ob_like_summary(tmp_path), "--n-nodes", "2", "--target", "1.5"])
    with pytest.raises(SystemExit, match="n_nodes"):
        fs.main(["--summary", _ob_like_summary(tmp_path), "--n-nodes", "0", "--enumerate"])


def test_enumeration_method_validates_n_nodes():
    with pytest.raises(ValueError, match="n_nodes"):
        fs.enumeration_method(3, 0)


def test_independent_impl_anchored_on_hand_computed_values():
    """Anchor the SECOND implementation itself, so the property tests cannot
    pass with both implementations wrong in the same way."""
    edges = [("a", "b", 1.0), ("b", "c", 3.0)]
    assert independent_fraction({"a": 0, "b": 1, "c": 1}, edges) == pytest.approx(0.25)
    assert independent_fraction({"a": 0, "b": 0, "c": 0}, edges) == 0.0
    assert independent_fraction({"a": 0, "b": 1, "c": 2}, edges) == 1.0


# ── collapsed warm-start (f=0 reliability on tree-shaped graphs) ───────

# A 6-service star: one hub, five leaves -> 5 edges, all incident on the hub.
# Reaching f=0 (everything on one node) from a spread start needs every leaf
# to migrate through a cut-increasing single move, the local-minimum trap the
# warm-start exists to bypass.
_STAR = [("hub", f"leaf{i}", 1.0) for i in range(5)]
_STAR_SERVICES = ["hub", *(f"leaf{i}" for i in range(5))]


def test_warm_start_makes_f0_reliable_across_seeds():
    # Without the collapsed warm-start this star hit f=0 from only some seeds;
    # the closed-form optimum must now be found from every seed.
    for seed in range(20):
        sol = fs.solve(_STAR, _STAR_SERVICES, 8, 0.0, seed=seed)
        assert sol.accepted, f"seed {seed} missed f=0"
        assert sol.achieved_f == 0.0


def test_warm_start_gap_recorded_in_trace_no_capacity():
    sol = fs.solve(_STAR, _STAR_SERVICES, 8, 0.0, seed=0)
    assert sol.trace["warmStartGap"] == 0.0
    assert sol.trace["bestRestart"] == -1  # the warm-start won, not a restart


def test_warm_start_skipped_under_capacity():
    capacity = {svc: 1.0 for svc in _STAR_SERVICES}
    sol = fs.solve(_STAR, _STAR_SERVICES, 8, 0.0, capacity=capacity, seed=0, node_capacity=2.0)
    assert sol.trace["warmStartGap"] is None  # warm-start does not run with capacity
    assert sol.trace["bestRestart"] >= 0


def test_warm_start_local_search_still_reaches_high_target():
    # The warm candidate is collapsed-then-local-searched, so it refines all
    # the way to f=1 (splitting every edge out) — accepted, gap recorded.
    sol = fs.solve(_STAR, _STAR_SERVICES, 8, 1.0, seed=0)
    assert sol.accepted and sol.achieved_f == 1.0
    assert sol.trace["warmStartGap"] is not None
