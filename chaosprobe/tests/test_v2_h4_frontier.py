"""Tests for scripts/v2_h4_frontier.py (V2-H4 descriptive placement frontier)."""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("m2_aa_analysis")
h4 = _load("v2_h4_frontier")

LAT, DEPTH, ERR = "ew_p95_pre_ms", "es_trough_depth_pods", "user_err_during"


def _pl(lat, depth, err, label="p", role="frontier", campaign="C1"):
    """A Placement with its three DV point estimates set directly."""
    p = h4.Placement(
        label=label, f=0.0, r=1, mode="packed", fault="pod-delete", campaign=campaign, role=role
    )
    p.stats = {
        LAT: {"point": lat, "ci_low": lat, "ci_high": lat, "n": 3},
        DEPTH: {"point": depth, "ci_low": depth, "ci_high": depth, "n": 3},
        ERR: {"point": err, "ci_low": err, "ci_high": err, "n": 3},
    }
    return p


# ── dominates(): margin on ALL three DVs (lower is better) ─────────────


def test_dominates_when_better_by_margin_on_all_three():
    # δ = 4.4 / 1.0 / 0.302. A beats B by ≥ each on every DV.
    a = _pl(30.0, 1.0, 0.0)
    b = _pl(40.0, 3.0, 0.5)
    assert h4.dominates(a, b) is True
    assert h4.dominates(b, a) is False


def test_no_dominance_when_only_latency_separates():
    # The real frontier case: depth + error equal, only latency differs by ≥δ.
    a = _pl(30.0, 1.0, 0.04)
    b = _pl(40.0, 1.0, 0.04)  # 10 ms better on latency, but depth/err tie
    assert h4.dominates(a, b) is False  # all-DV rule: a tie on any DV blocks dominance


def test_no_dominance_when_margin_not_met_on_one_dv():
    # Better on latency + error by margin, but depth only 0.5 pod better (< 1.0 δ).
    a = _pl(30.0, 1.0, 0.0)
    b = _pl(40.0, 1.5, 0.5)
    assert h4.dominates(a, b) is False


def test_no_dominance_on_exact_equal():
    a = _pl(35.0, 1.0, 0.04)
    b = _pl(35.0, 1.0, 0.04)
    assert h4.dominates(a, b) is False  # pb - pa = 0 < δ on every DV


def test_dominance_boundary_is_inclusive_at_exactly_delta():
    # Better by EXACTLY the margin on all three (4.4 / 1.0 / 0.302) → dominates (>=).
    # Use 0.0 baselines so pb - pa == the δ literal exactly (x - 0.0 == x), avoiding a
    # float knife-edge (e.g. 34.4 - 30.0 = 4.3999… would spuriously fail the >= rule).
    a = _pl(0.0, 0.0, 0.0)
    b = _pl(4.4, 1.0, 0.302)
    assert h4.dominates(a, b) is True


def test_dominates_false_on_missing_point():
    a = _pl(30.0, 1.0, 0.0)
    b = _pl(40.0, 3.0, 0.5)
    b.stats[LAT]["point"] = None
    assert h4.dominates(a, b) is False
    assert h4.dominates(b, a) is False


# ── non_dominated(): the frontier set ──────────────────────────────────


def test_non_dominated_all_when_only_latency_varies():
    # Mirrors the real C1 data: depth=1, low error everywhere; only latency moves.
    ps = [_pl(35.7, 1.0, 0.04, "f0"), _pl(38.6, 1.0, 0.04, "f25"), _pl(41.4, 1.0, 0.04, "f50")]
    nd = h4.non_dominated(ps)
    assert len(nd) == 3  # none dominates another → all on the frontier


def test_non_dominated_single_clear_winner():
    winner = _pl(20.0, 1.0, 0.0, "win")
    ps = [winner, _pl(40.0, 3.0, 0.5, "lose1"), _pl(41.0, 4.0, 0.6, "lose2")]
    nd = h4.non_dominated(ps)
    assert [p.label for p in nd] == ["win"]


def test_non_dominated_empty():
    assert h4.non_dominated([]) == []


# ── _placement_label ───────────────────────────────────────────────────


def test_label_r1_packed_omits_mode():
    assert h4._placement_label(0.5, 1, "packed") == "f=0.5, r=1"
    assert h4._placement_label(0.0, 1, "solver") == "f=0, r=1"


def test_label_includes_mode_for_replicated_or_antiaffine():
    assert h4._placement_label(0.5, 3, "packed") == "f=0.5, r=3, packed"
    assert h4._placement_label(0.5, 3, "anti-affine") == "f=0.5, r=3, anti-affine"


# ── summarize(): bootstrap CI fills stats deterministically ────────────


def test_summarize_fills_point_and_ci():
    p = h4.Placement(
        label="p", f=0.0, r=1, mode="packed", fault="pod-delete", campaign="C1", role="frontier"
    )
    p.session_values = {LAT: [35.0, 36.0, 37.0], DEPTH: [1.0, 1.0, 1.0], ERR: [0.04, 0.05, 0.03]}
    p.summarize(seed=42)
    assert p.stats[LAT]["point"] == pytest.approx(36.0)  # median
    assert p.stats[DEPTH]["point"] == pytest.approx(1.0)
    assert p.stats[LAT]["n"] == 3
    assert p.stats[LAT]["ci_low"] <= p.stats[LAT]["point"] <= p.stats[LAT]["ci_high"]


def test_summarize_empty_dv_is_none():
    p = h4.Placement(
        label="p", f=0.0, r=1, mode="packed", fault="pod-delete", campaign="C1", role="frontier"
    )
    p.session_values = {}  # no values for any DV
    p.summarize()
    assert p.stats[LAT]["point"] is None
    assert p.stats[LAT]["n"] == 0


# ── build_frontier(): role + nonDominated wiring (collection monkeypatched) ──


def test_build_frontier_marks_roles_and_frontier(monkeypatch, tmp_path):
    # Two C1 frontier cells + one C3 corroboration cell; C1 winner dominates the other.
    def fake_collect(results_dir, campaign, role, dns_cache=None):
        if campaign == "C1":
            return {
                ("a",): _pl(20.0, 1.0, 0.0, "f0", "frontier", "C1"),
                ("b",): _pl(40.0, 3.0, 0.6, "f50", "frontier", "C1"),
            }, []
        return {("c",): _pl(21.0, 1.0, 0.0, "f0", "corroboration", "C3")}, []

    monkeypatch.setattr(h4, "collect_campaign", fake_collect)
    # point/ci already set by _pl; summarize would overwrite with empty → stub it out.
    monkeypatch.setattr(h4.Placement, "summarize", lambda self, seed=42: None)
    # Make the campaign dirs "exist".
    for sub in ("c1-online-boutique", "c3-dns"):
        (tmp_path / sub).mkdir()
    res = h4.build_frontier(str(tmp_path))

    assert res["frontierSize"] == 2  # only C1 cells are frontier members
    assert res["nonDominatedCount"] == 1
    assert res["nonDominated"] == ["C1:f0"]  # the winner
    roles = {(p["campaign"], p["label"]): p for p in res["placements"]}
    assert roles[("C3", "f0")]["role"] == "corroboration"
    assert roles[("C3", "f0")]["nonDominated"] is None  # corroboration is never ranked
    assert roles[("C1", "f50")]["nonDominated"] is False


def test_render_and_plot_smoke(tmp_path):
    res = {
        "deltas": {LAT: 4.4, DEPTH: 1.0, ERR: 0.302},
        "placements": [
            _p_dict("C1", "f0", 35.7, 1.0, 0.04, True),
            _p_dict("C3", "f1", 39.9, 1.0, 0.15, None, role="corroboration"),
        ],
        "nonDominated": ["C1:f0"],
        "frontierSize": 1,
        "nonDominatedCount": 1,
        "warnings": [],
    }
    out = h4.render(res)
    assert "non-dominated" in out.lower() and "C1:f0" in out
    fig_path = tmp_path / "f.png"
    h4.plot(res, str(fig_path))
    assert fig_path.exists() and fig_path.stat().st_size > 0


def _p_dict(camp, label, lat, depth, err, nd, role="frontier"):
    def s(v):
        return {"point": v, "ci_low": v, "ci_high": v, "n": 3}

    return {
        "campaign": camp,
        "label": label,
        "f": 0.0,
        "r": 1,
        "mode": "packed",
        "fault": "pod-delete",
        "role": role,
        "nonDominated": nd,
        "stats": {LAT: s(lat), DEPTH: s(depth), ERR: s(err)},
    }
