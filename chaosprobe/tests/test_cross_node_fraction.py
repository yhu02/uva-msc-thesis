"""Tests for scripts/cross_node_fraction.py — graph-derived placement metric."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cross_node_fraction.py"
_spec = importlib.util.spec_from_file_location("cross_node_fraction", _SCRIPT)
assert _spec is not None and _spec.loader is not None
xnf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(xnf)


@pytest.mark.parametrize(
    "pod, dep",
    [
        ("adservice-79c4dfd9c5-5hlpz", "adservice"),
        ("redis-cart-6dc9b8f5d-abcde", "redis-cart"),
        ("productcatalogservice-59c5c86554-mgpq2", "productcatalogservice"),
    ],
)
def test_deployment_of(pod, dep):
    assert xnf.deployment_of(pod) == dep


def test_edges_from_route_view_splits_comma_joined():
    rva = [
        {"route": "frontend->checkoutservice"},
        {"route": "checkoutservice->cartservice,frontend->cartservice"},
        {"route": "/"},  # north-south, ignored
    ]
    assert xnf.edges_from_route_view(rva) == {
        ("frontend", "checkoutservice"),
        ("checkoutservice", "cartservice"),
        ("frontend", "cartservice"),
    }


def test_cross_node_fraction_all_same_node_is_zero():
    placements = {"frontend-x-y": "n1", "cartservice-x-y": "n1", "redis-cart-x-y": "n1"}
    edges = {("frontend", "cartservice"), ("cartservice", "redis-cart")}
    assert xnf.cross_node_fraction(placements, edges) == 0.0


def test_cross_node_fraction_mixed():
    placements = {"frontend-x-y": "n1", "cartservice-x-y": "n2", "redis-cart-x-y": "n2"}
    edges = {("frontend", "cartservice"), ("cartservice", "redis-cart")}
    # frontend->cartservice crosses (n1!=n2); cartservice->redis-cart does not.
    assert xnf.cross_node_fraction(placements, edges) == 0.5


def test_cross_node_fraction_skips_unplaced_endpoint():
    # loadgenerator not placed -> that edge is skipped, leaving 1 same-node edge.
    placements = {"frontend-x-y": "n1", "cartservice-x-y": "n1"}
    edges = {("loadgenerator", "frontend"), ("frontend", "cartservice")}
    assert xnf.cross_node_fraction(placements, edges) == 0.0


def test_cross_node_fraction_none_when_no_edge_placed():
    assert xnf.cross_node_fraction({}, {("a", "b")}) is None


def test_target_scoped_only_counts_incident_edges():
    # target=pcs. Its one edge (frontend->pcs) crosses nodes -> 1.0, even though a
    # non-incident edge (cart->redis) sits on one node and would dilute the global.
    placements = {
        "frontend-x-y": "n1",
        "productcatalogservice-x-y": "n2",
        "cartservice-x-y": "n3",
        "redis-cart-x-y": "n3",
    }
    edges = {
        ("frontend", "productcatalogservice"),
        ("cartservice", "redis-cart"),
    }
    assert xnf.cross_node_fraction(placements, edges) == 0.5  # global: 1 of 2 crosses
    assert xnf.target_scoped_cross_node_fraction(placements, edges, "productcatalogservice") == 1.0


def test_target_scoped_none_when_target_absent():
    placements = {"a-x-y": "n1", "b-x-y": "n1"}
    edges = {("a", "b")}
    assert xnf.target_scoped_cross_node_fraction(placements, edges, "productcatalogservice") is None


def test_east_west_median_p95_ignores_north_south_and_none():
    rva = [
        {"route": "/", "latencyProber": {"during-chaos": {"meanP95_ms": 800.0}}},
        {"route": "a->b", "latencyProber": {"during-chaos": {"meanP95_ms": 30.0}}},
        {"route": "c->d", "latencyProber": {"during-chaos": {"meanP95_ms": 50.0}}},
        {"route": "e->f", "latencyProber": {"during-chaos": {}}},  # no p95
    ]
    assert xnf.east_west_median_p95(rva) == 40.0


def test_spearman_none_below_three_points():
    assert xnf._spearman([(0.0, 1.0), (1.0, 2.0)]) is None


def test_spearman_perfect_monotonic():
    rho = xnf._spearman([(0.0, 10.0), (0.5, 20.0), (1.0, 30.0)])
    assert rho is not None and rho > 0.99


def test_spearman_handles_tied_ranks():
    # Two tied x-values exercise the average-rank branch; here all y also tie on x,
    # so the correlation is well-defined and finite.
    rho = xnf._spearman([(0.0, 10.0), (0.0, 20.0), (1.0, 30.0)])
    assert rho is not None


def _summary(single_fault=True):
    def _strat(frac_node_map, ew_p95):
        # one east-west edge a->b; placements decide cross-node-ness
        return {
            "iterations": [{"podPlacements": frac_node_map}],
            "aggregated": {
                "routeViewAggregate": [
                    {"route": "a->b", "latencyProber": {"during-chaos": {"meanP95_ms": ew_p95}}}
                ]
            },
        }

    strats = {
        "colocate": _strat({"a-x-y": "n1", "b-x-y": "n1"}, 30.0),  # frac 0.0
        "spread": _strat({"a-x-y": "n1", "b-x-y": "n2"}, 45.0),  # frac 1.0
    }
    return {"strategies": strats} if single_fault else {"faults": {"load": {"strategies": strats}}}


def test_report_runs_and_flags_no_gradient(capsys):
    xnf.report(_summary())
    out = capsys.readouterr().out
    assert "colocate" in out and "0.000" in out
    assert "NOTE:" in out  # 2 distinct fractions (0.0, 1.0) -> still <3 -> gradient note


def test_report_multi_fault_shape(capsys):
    xnf.report(_summary(single_fault=False))
    assert "spread" in capsys.readouterr().out


def test_main_reads_file(tmp_path, monkeypatch, capsys):
    import json

    f = tmp_path / "summary.json"
    f.write_text(json.dumps(_summary()))
    monkeypatch.setattr("sys.argv", ["cross_node_fraction.py", "-s", str(f)])
    xnf.main()
    assert "cross-node call fraction" in capsys.readouterr().out.lower()
