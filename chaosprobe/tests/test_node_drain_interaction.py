"""Tests for scripts/node_drain_interaction.py — E1 placement x replicas ART."""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "node_drain_interaction.py"
_spec = importlib.util.spec_from_file_location("node_drain_interaction", _SCRIPT)
assert _spec is not None and _spec.loader is not None
ndi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ndi)


def _phase(counts: dict) -> dict:
    return {"services": {s: {"ready": r} for s, r in counts.items()}}


def _strategy(pre: dict, trough: dict, during: bool = False) -> dict:
    es = {"preChaos": _phase(pre)}
    if during:
        es["duringChaos"] = _phase(trough)
    else:
        es["postChaos"] = _phase(trough)
    return {"metrics": {"endpointSlices": es}}


# ── _strategies ───────────────────────────────────────────────────────────────


def test_strategies_single_fault():
    assert ndi._strategies({"strategies": {"colocate": {"a": 1}}}) == {"colocate": {"a": 1}}


def test_strategies_node_drain_fault_only():
    summary = {
        "faults": {
            "node-drain": {"strategies": {"spread": {"b": 2}}},
            "pod-delete": {"strategies": {"colocate": {"c": 3}}},
        }
    }
    assert ndi._strategies(summary) == {"spread": {"b": 2}}


def test_strategies_empty():
    assert ndi._strategies({}) == {}


# ── availability ────────────────────────────────────────────────────────────────


def test_availability_full_outage_single_replica():
    av = ndi.availability(_strategy({"a": 1, "b": 1}, {"a": 0, "b": 0}))
    assert av == (0.0, 1)


def test_availability_partial_multireplica():
    avail, replicas = ndi.availability(_strategy({"a": 3}, {"a": 2}))
    assert avail == 2 / 3
    assert replicas == 3


def test_availability_prefers_during_trough():
    avail, _ = ndi.availability(_strategy({"a": 1}, {"a": 0}, during=True))
    assert avail == 0.0


def test_availability_no_prechaos_returns_none():
    assert (
        ndi.availability({"metrics": {"endpointSlices": {"postChaos": _phase({"a": 0})}}}) is None
    )


def test_availability_no_measurable_services_returns_none():
    # pre ready is 0 -> no usable ratio.
    assert ndi.availability(_strategy({"a": 0}, {"a": 0})) is None


def test_availability_missing_trough_phase_returns_none():
    # preChaos present but no trough snapshot at all -> _ready(None, svc) is None.
    strat = {"metrics": {"endpointSlices": {"preChaos": _phase({"a": 1})}}}
    assert ndi.availability(strat) is None


def test_availability_skips_service_absent_or_non_numeric_at_trough():
    # "a" recovers cleanly; "b" is absent from the trough snapshot (ready None).
    pre = _phase({"a": 1, "b": 1})
    trough = {"services": {"a": {"ready": 0}, "b": {"ready": None}}}
    strat = {"metrics": {"endpointSlices": {"preChaos": pre, "postChaos": trough}}}
    avail, replicas = ndi.availability(strat)
    assert avail == 0.0  # only "a" counted (0/1); "b" skipped
    assert replicas == 1


# ── extract ─────────────────────────────────────────────────────────────────────


def test_extract_skips_baseline_and_unmeasurable():
    summary = {
        "faults": {
            "node-drain": {
                "strategies": {
                    "colocate": _strategy({"a": 1, "b": 1}, {"a": 0, "b": 0}),
                    "baseline": _strategy({"a": 1}, {"a": 1}),
                    "broken": {"metrics": {}},
                }
            }
        }
    }
    rows = ndi.extract(summary)
    assert rows == [("colocate", 1, 0.0)]


# ── report ──────────────────────────────────────────────────────────────────────


def test_report_no_data(capsys):
    assert ndi.report([]) == {}
    assert "No measurable node-drain" in capsys.readouterr().out


def test_report_interaction_detected(capsys):
    rows = [
        ("spread", 1, 0.00),
        ("spread", 1, 0.01),
        ("spread", 1, 0.02),
        ("colocate", 1, 0.00),
        ("colocate", 1, 0.01),
        ("colocate", 1, 0.02),
        ("spread", 3, 0.95),
        ("spread", 3, 0.97),
        ("spread", 3, 0.99),
        ("colocate", 3, 0.00),
        ("colocate", 3, 0.02),
        ("colocate", 3, 0.04),
    ]
    art = ndi.report(rows)
    out = capsys.readouterr().out
    assert art["n"] == 12
    assert art["levels_a"] == ["colocate", "spread"]
    assert art["levels_b"] == [1, 3]
    assert art["interaction"]["f"] is not None
    assert art["interaction"]["p"] < 0.05
    assert "E1 supported" in out


def test_report_insufficient_design(capsys):
    rows = [
        ("spread", 1, 0.0),
        ("spread", 1, 0.05),
        ("colocate", 1, 0.0),
        ("colocate", 1, 0.02),
    ]
    art = ndi.report(rows)
    out = capsys.readouterr().out
    assert art["interaction"]["f"] is None
    assert art["factor_b"]["f"] is None
    assert "n/a" in out
