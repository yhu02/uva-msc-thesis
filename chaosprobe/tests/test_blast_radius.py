"""Tests for scripts/blast_radius.py — node-failure blast-radius metric."""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "blast_radius.py"
_spec = importlib.util.spec_from_file_location("blast_radius", _SCRIPT)
assert _spec is not None and _spec.loader is not None
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)


def _es(pre: dict, post: dict) -> dict:
    """Build an endpointSlices block from {svc: ready} maps."""
    return {
        "preChaos": {"services": {s: {"ready": r} for s, r in pre.items()}},
        "postChaos": {"services": {s: {"ready": r} for s, r in post.items()}},
    }


def _strategy(assignments: dict, pre: dict, post: dict, recovery_ms=None) -> dict:
    metrics = {"endpointSlices": _es(pre, post)}
    if recovery_ms is not None:
        metrics["recovery"] = {"summary": {"meanRecovery_ms": recovery_ms}}
    return {"placement": {"assignments": assignments}, "metrics": metrics}


# ── _strategies (shape flattening) ────────────────────────────────────────────


def test_strategies_single_fault_shape():
    summary = {"strategies": {"colocate": {"a": 1}}}
    assert br._strategies(summary) == {"colocate": {"a": 1}}


def test_strategies_multi_fault_shape():
    summary = {"faults": {"node-drain": {"strategies": {"spread": {"b": 2}}}}}
    assert br._strategies(summary) == {"spread": {"b": 2}}


def test_strategies_merges_both_and_tolerates_none():
    summary = {
        "strategies": {"colocate": {"a": 1}},
        "faults": {"node-drain": {"strategies": {"spread": {"b": 2}}}, "other": None},
    }
    assert br._strategies(summary) == {"colocate": {"a": 1}, "spread": {"b": 2}}


def test_strategies_empty():
    assert br._strategies({}) == {}


# ── _assignments ──────────────────────────────────────────────────────────────


def test_assignments_present():
    assert br._assignments({"placement": {"assignments": {"x": "worker1"}}}) == {"x": "worker1"}


def test_assignments_absent():
    assert br._assignments({}) == {}
    assert br._assignments({"placement": {}}) == {}


# ── placement_concentration ───────────────────────────────────────────────────


def test_concentration_distributes():
    dist, max_on_node = br.placement_concentration({"a": "w1", "b": "w2", "c": "w1", "d": "w3"})
    assert max_on_node == 2
    assert dist == {"w1": ["a", "c"], "w2": ["b"], "w3": ["d"]}


def test_concentration_all_colocated():
    dist, max_on_node = br.placement_concentration({"a": "w1", "b": "w1", "c": "w1"})
    assert max_on_node == 3
    assert dist == {"w1": ["a", "b", "c"]}


def test_concentration_empty():
    dist, max_on_node = br.placement_concentration({})
    assert max_on_node == 0
    assert dist == {}


# ── _ready ────────────────────────────────────────────────────────────────────


def test_ready_value():
    phase = {"services": {"frontend": {"ready": 3}}}
    assert br._ready(phase, "frontend") == 3


def test_ready_missing_service():
    assert br._ready({"services": {}}, "frontend") is None


def test_ready_non_int():
    assert br._ready({"services": {"frontend": {"ready": None}}}, "frontend") is None


def test_ready_none_phase():
    assert br._ready(None, "frontend") is None


# ── ready_deltas ──────────────────────────────────────────────────────────────


def test_ready_deltas_pairs_known_only():
    es = _es({"a": 3, "b": 3}, {"a": 0, "b": 3})
    # "c" absent from snapshots → excluded
    deltas = br.ready_deltas(es, ["a", "b", "c"])
    assert deltas == {"a": (3, 0), "b": (3, 3)}


# ── blast_metrics ─────────────────────────────────────────────────────────────


def test_blast_metrics_colocate_wide():
    # 4 services all on worker1; drain takes all 4 to zero.
    strat = _strategy(
        {"a": "w1", "b": "w1", "c": "w1", "d": "w1"},
        {"a": 1, "b": 1, "c": 1, "d": 1},
        {"a": 0, "b": 0, "c": 0, "d": 0},
        recovery_ms=35000,
    )
    m = br.blast_metrics(strat)
    assert m["blastRadius"] == 4
    assert m["knockedToZero"] == ["a", "b", "c", "d"]
    assert m["podsLost"] == 4
    assert m["drainedNode"] == "w1"
    assert m["servicesOnDrainedNode"] == ["a", "b", "c", "d"]
    assert m["maxNodeConcentration"] == 4
    assert m["meanRecoveryMs"] == 35000
    assert m["measuredServices"] == 4


def test_blast_metrics_spread_narrow():
    # productcatalog (pc) on w4 with one neighbour; the rest elsewhere.
    strat = _strategy(
        {"pc": "w4", "x": "w4", "f": "w2", "g": "w3"},
        {"pc": 1, "x": 1, "f": 1, "g": 1},
        {"pc": 0, "x": 0, "f": 1, "g": 1},
    )
    m = br.blast_metrics(strat)
    assert m["blastRadius"] == 2
    assert m["knockedToZero"] == ["pc", "x"]
    assert m["drainedNode"] == "w4"
    assert m["servicesOnDrainedNode"] == ["pc", "x"]
    assert m["maxNodeConcentration"] == 2
    assert m["meanRecoveryMs"] is None  # no recovery block


def test_blast_metrics_multireplica_partial_loss():
    # 3 replicas, one on the drained node: ready 3 -> 2, not knocked to zero.
    strat = _strategy({"a": "w1"}, {"a": 3}, {"a": 2})
    m = br.blast_metrics(strat)
    assert m["blastRadius"] == 0
    assert m["knockedToZero"] == []
    assert m["podsLost"] == 1
    assert m["drainedNode"] is None  # nothing fully down → node not inferred
    assert m["servicesOnDrainedNode"] == []


def test_blast_metrics_no_assignments_returns_none():
    strat = {"metrics": {"endpointSlices": _es({"a": 1}, {"a": 0})}}
    assert br.blast_metrics(strat) is None


def test_blast_metrics_no_endpointslices_returns_none():
    strat = {"placement": {"assignments": {"a": "w1"}}, "metrics": {}}
    assert br.blast_metrics(strat) is None


def test_blast_metrics_no_measurable_services_returns_none():
    # assignment for "a" but the snapshot only knows "z" → no overlap.
    strat = _strategy({"a": "w1"}, {"z": 1}, {"z": 1})
    assert br.blast_metrics(strat) is None


def test_blast_metrics_already_unready_excluded_from_drain_node():
    # "b" was already 0 pre-chaos (not a drain victim); only "a" fell.
    strat = _strategy({"a": "w1", "b": "w2"}, {"a": 1, "b": 0}, {"a": 0, "b": 0})
    m = br.blast_metrics(strat)
    assert m["knockedToZero"] == ["a"]
    assert m["drainedNode"] == "w1"


# ── collect ───────────────────────────────────────────────────────────────────


def test_collect_skips_unmeasurable():
    summary = {
        "faults": {
            "node-drain": {
                "strategies": {
                    "colocate": _strategy(
                        {"a": "w1", "b": "w1"}, {"a": 1, "b": 1}, {"a": 0, "b": 0}
                    ),
                    "broken": {"metrics": {}},  # no placement → skipped
                }
            }
        }
    }
    out = br.collect(summary)
    assert set(out) == {"colocate"}
    assert out["colocate"]["blastRadius"] == 2


# ── report ────────────────────────────────────────────────────────────────────


def test_report_separation(capsys):
    summary = {
        "faults": {
            "node-drain": {
                "strategies": {
                    "colocate": _strategy(
                        {"a": "w1", "b": "w1", "c": "w1"},
                        {"a": 1, "b": 1, "c": 1},
                        {"a": 0, "b": 0, "c": 0},
                        recovery_ms=40000,
                    ),
                    "spread": _strategy(
                        {"a": "w1", "b": "w2", "c": "w3"},
                        {"a": 1, "b": 1, "c": 1},
                        {"a": 0, "b": 1, "c": 1},
                    ),
                }
            }
        }
    }
    metrics = br.report(summary)
    out = capsys.readouterr().out
    assert "Widest blast: colocate" in out
    assert "narrowest: spread" in out
    assert metrics["colocate"]["blastRadius"] == 3
    assert metrics["spread"]["blastRadius"] == 1
    # widest blast printed first
    assert out.index("colocate") < out.index("spread")


def test_report_no_data(capsys):
    metrics = br.report({"faults": {}})
    out = capsys.readouterr().out
    assert "No measurable node-drain data" in out
    assert metrics == {}


def test_report_no_separation(capsys):
    # Two strategies, identical blast radius of 1.
    summary = {
        "strategies": {
            "colocate": _strategy({"a": "w1"}, {"a": 1}, {"a": 0}),
            "spread": _strategy({"a": "w2"}, {"a": 1}, {"a": 0}),
        }
    }
    br.report(summary)
    out = capsys.readouterr().out
    assert "no separation" in out
