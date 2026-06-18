"""Tests for scripts/v2_h4_frontier.py (V2-H4 descriptive placement frontier)."""

import importlib.util
import json
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


# ── collect_campaign(): end-to-end data extraction from on-disk fixtures ──
#
# Builds real session dirs (summary.json + raw f-XXX.json) so discover_sessions,
# the per-level targetF lookup, the (f,r,mode) grouping, the accepted/taint skips,
# the dnsCache filter, and the per-session-median aggregation all run for real.


def _raw_iter(n, ew_pre, pre_taints=()):
    """A raw iteration carrying an east-west pre-chaos p95 (the latency face)."""
    it = {
        "iteration": n,
        "verdict": "PASS",
        "metrics": {"latency": {"phases": {"pre-chaos": {"routes": {"a->b": {"p95_ms": ew_pre}}}}}},
    }
    if pre_taints:
        it["preChaosTaintReasons"] = list(pre_taints)
    return it


def _write_session(results_dir, name, *, r, mode, fault, levels, dns_cache=None, raws):
    """levels = [(condition, targetF, accepted)]; raws = {condition: [iterations]}."""
    run = results_dir / name
    run.mkdir(parents=True)
    per_level = [
        {"condition": c, "targetF": f, "liveAchievedF": f, "accepted": acc, "rejectionReasons": []}
        for c, f, acc in levels
    ]
    v2 = {
        "solverSeed": 0,
        "replicas": r,
        "mode": mode,
        "workers": ["w1", "w2"],
        "levels": [f for _, f, _ in levels],
        "perLevel": per_level,
    }
    if dns_cache is not None:
        v2["dnsCache"] = dns_cache
    summary = {
        "runId": name,
        "timestamp": f"2026-01-01T00:00:0{name[-1]}+00:00",
        "v2Session": v2,
        "faults": {fault: {"strategies": {}}},
    }
    (run / "summary.json").write_text(json.dumps(summary))
    for cond, iters in raws.items():
        (run / f"{cond}.json").write_text(
            json.dumps({"placement": {"assignments": {"svc-a": "w1"}}, "iterations": iters})
        )


def test_collect_campaign_groups_and_aggregates(tmp_path):
    rdir = tmp_path / "c1"
    # Two sessions of the SAME placement (f=0, r=1, packed) → one grouped placement.
    _write_session(
        rdir,
        "s1",
        r=1,
        mode="packed",
        fault="pod-delete",
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 30.0), _raw_iter(2, 40.0)]},
    )
    _write_session(
        rdir,
        "s2",
        r=1,
        mode="packed",
        fault="pod-delete",
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 50.0)]},
    )
    placements, _ = h4.collect_campaign(str(rdir), "C1", "frontier")
    assert len(placements) == 1
    p = next(iter(placements.values()))
    assert (p.f, p.r, p.mode, p.fault) == (0.0, 1, "packed", "pod-delete")
    # session_values = one per-session median each: s1 median(30,40)=35, s2 median(50)=50.
    assert sorted(p.session_values[LAT]) == [35.0, 50.0]


def test_collect_campaign_skips_unaccepted_and_tainted(tmp_path):
    rdir = tmp_path / "c1"
    # f-100 not accepted (skipped); f-000 has one tainted iteration (folded to None, excluded).
    _write_session(
        rdir,
        "s1",
        r=1,
        mode="packed",
        fault="pod-delete",
        levels=[("f-000", 0.0, True), ("f-100", 1.0, False)],
        raws={
            "f-000": [_raw_iter(1, 30.0, pre_taints=["x"]), _raw_iter(2, 40.0)],
            "f-100": [_raw_iter(1, 99.0)],
        },
    )
    placements, _ = h4.collect_campaign(str(rdir), "C1", "frontier")
    assert set(k[0] for k in placements) == {0.0}  # f-100 rejected → absent
    p = placements[(0.0, 1, "packed")]
    assert p.session_values[LAT] == [40.0]  # tainted iter-1 excluded; median over {40} only


def test_collect_campaign_dns_cache_filter(tmp_path):
    rdir = tmp_path / "c3"
    _write_session(
        rdir,
        "s1",
        r=1,
        mode="solver",
        fault="pod-delete",
        dns_cache="on",
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 36.0)]},
    )
    _write_session(
        rdir,
        "s2",
        r=1,
        mode="solver",
        fault="pod-delete",
        dns_cache="off",
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 88.0)]},
    )
    placements, warnings = h4.collect_campaign(str(rdir), "C3", "corroboration", dns_cache="on")
    assert len(placements) == 1
    p = placements[(0.0, 1, "solver")]
    assert p.session_values[LAT] == [36.0]  # only the cache-on session
    # The excluded cache-off session is surfaced, not silently dropped.
    assert any("s2" in w and "dnsCache='off'" in w for w in warnings)


def test_collect_campaign_unreadable_summary_warns_not_silent(tmp_path):
    # A C3 session whose summary.json is corrupt → dnsCache reads None → excluded,
    # but the exclusion must be reported (provenance: no silent drops).
    rdir = tmp_path / "c3"
    _write_session(
        rdir,
        "s1",
        r=1,
        mode="solver",
        fault="pod-delete",
        dns_cache="on",
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 36.0)]},
    )
    (rdir / "s2").mkdir()
    (rdir / "s2" / "summary.json").write_text("{ this is not valid json")
    placements, warnings = h4.collect_campaign(str(rdir), "C3", "corroboration", dns_cache="on")
    # s2 is dropped (discover_sessions can't parse it) — and if it surfaces at all,
    # _session_dns_cache returns None so the filter excludes it with a warning.
    assert all(k[0] == 0.0 for k in placements)
    assert h4._session_dns_cache(str(rdir), "s2") is None  # unreadable → None


def test_session_dns_cache_missing_field_is_none(tmp_path):
    rdir = tmp_path / "c3"
    _write_session(
        rdir,
        "s1",
        r=1,
        mode="solver",
        fault="pod-delete",  # no dns_cache field
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 36.0)]},
    )
    assert h4._session_dns_cache(str(rdir), "s1") is None
    assert h4._session_dns_cache(str(rdir), "does-not-exist") is None


def test_build_frontier_warns_on_missing_campaign_dir(tmp_path):
    # No campaign subdirs exist → each is skipped with a warning, empty frontier.
    res = h4.build_frontier(str(tmp_path))
    assert res["frontierSize"] == 0
    assert res["nonDominatedCount"] == 0
    assert any("missing — skipped" in w for w in res["warnings"])


def test_render_shows_warning_count(tmp_path):
    res = {
        "deltas": {LAT: 4.4, DEPTH: 1.0, ERR: 0.302},
        "placements": [_p_dict("C1", "f0", 35.7, 1.0, 0.04, True)],
        "nonDominated": ["C1:f0"],
        "frontierSize": 1,
        "nonDominatedCount": 1,
        "warnings": ["w1", "w2"],
    }
    out = h4.render(res)
    assert "2 warning(s)" in out


def test_plot_skips_points_with_missing_coordinate(tmp_path):
    # A placement with a None latency point must be skipped, not crash plot().
    res = {
        "deltas": {LAT: 4.4, DEPTH: 1.0, ERR: 0.302},
        "placements": [
            _p_dict("C1", "f0", 35.7, 1.0, 0.04, True),
            _p_dict("C1", "fX", None, 1.0, 0.04, False),  # missing x → skipped
        ],
        "nonDominated": ["C1:f0"],
        "frontierSize": 2,
        "nonDominatedCount": 1,
        "warnings": [],
    }
    fig_path = tmp_path / "f.png"
    h4.plot(res, str(fig_path))
    assert fig_path.exists() and fig_path.stat().st_size > 0


def test_main_end_to_end_writes_json_and_fig(tmp_path, monkeypatch, capsys):
    # One C1 frontier session over real fixtures; main() drives build → render → JSON → fig.
    rdir = tmp_path / "results" / "c1-online-boutique"
    _write_session(
        rdir,
        "s1",
        r=1,
        mode="packed",
        fault="pod-delete",
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 35.0)]},
    )
    out_json, out_fig = tmp_path / "f.json", tmp_path / "f.png"
    monkeypatch.setattr(
        "sys.argv",
        [
            "v2_h4_frontier.py",
            "--results-root",
            str(tmp_path / "results"),
            "--json",
            str(out_json),
            "--fig",
            str(out_fig),
            "--seed",
            "1",
        ],
    )
    h4.main()
    printed = capsys.readouterr().out
    assert "placement frontier" in printed.lower()
    written = json.loads(out_json.read_text())
    assert written["nonDominated"] == ["C1:f=0, r=1"]  # the single frontier cell
    assert out_fig.exists() and out_fig.stat().st_size > 0


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
