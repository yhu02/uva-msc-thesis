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
    assert h4._placement_label(0.0, 1, "") == "f=0, r=1"  # empty mode also omits


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
    p = placements[(0.0, 1, "packed", "pod-delete")]
    assert p.session_values[LAT] == [40.0]  # tainted iter-1 excluded; median over {40} only


def test_collect_campaign_separates_placements_by_fault(tmp_path):
    # Same (f, r, mode) under two different faults must NOT merge into one
    # placement (the Placement carries a single fault label, so merging would
    # mislabel). They produce two distinct keyed placements.
    rdir = tmp_path / "mixed"
    _write_session(
        rdir,
        "s1",
        r=1,
        mode="packed",
        fault="pod-delete",
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 30.0)]},
    )
    _write_session(
        rdir,
        "s2",
        r=1,
        mode="packed",
        fault="node-drain",
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 80.0)]},
    )
    placements, _ = h4.collect_campaign(str(rdir), "C1", "frontier")
    assert (0.0, 1, "packed", "pod-delete") in placements
    assert (0.0, 1, "packed", "node-drain") in placements
    assert len(placements) == 2  # not merged
    assert placements[(0.0, 1, "packed", "pod-delete")].session_values[LAT] == [30.0]
    assert placements[(0.0, 1, "packed", "node-drain")].session_values[LAT] == [80.0]


def test_collect_campaign_dns_cache_filter(tmp_path):
    rdir = tmp_path / "c3"
    _write_session(
        rdir,
        "s1",
        r=1,
        mode="packed",
        fault="pod-delete",
        dns_cache="on",
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 36.0)]},
    )
    _write_session(
        rdir,
        "s2",
        r=1,
        mode="packed",
        fault="pod-delete",
        dns_cache="off",
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 88.0)]},
    )
    placements, warnings = h4.collect_campaign(str(rdir), "C3", "corroboration", dns_cache="on")
    assert len(placements) == 1
    p = placements[(0.0, 1, "packed", "pod-delete")]
    assert p.session_values[LAT] == [36.0]  # only the cache-on session
    # The excluded cache-off session is surfaced, not silently dropped.
    assert any("s2" in w and "dnsCache='off'" in w for w in warnings)


def test_collect_campaign_missing_dnscache_field_warns_not_silent(tmp_path):
    # A C3 session whose summary parses but omits v2Session.dnsCache → cache reads
    # None → excluded by the filter, but the exclusion must be SURFACED, not silent.
    # (A genuinely corrupt summary is dropped upstream by discover_sessions; this
    # drives collect_campaign's OWN cache-None branch with a readable session.)
    rdir = tmp_path / "c3"
    _write_session(
        rdir,
        "s1",
        r=1,
        mode="packed",
        fault="pod-delete",
        dns_cache="on",
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 36.0)]},
    )
    _write_session(
        rdir,
        "s2",
        r=1,
        mode="packed",
        fault="pod-delete",
        dns_cache=None,  # summary parses but has no dnsCache field
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 88.0)]},
    )
    placements, warnings = h4.collect_campaign(str(rdir), "C3", "corroboration", dns_cache="on")
    assert all(k[0] == 0.0 for k in placements)  # only s1 (cache-on) kept
    # The collect_campaign branch fires with the accurate "no dnsCache field" label.
    assert any("s2" in w and "excluded from C3" in w and "no dnsCache field" in w for w in warnings)
    assert h4._session_dns_cache(str(rdir), "s2") is None  # missing field → None


def test_session_dns_cache_missing_field_is_none(tmp_path):
    rdir = tmp_path / "c3"
    _write_session(
        rdir,
        "s1",
        r=1,
        mode="packed",
        fault="pod-delete",  # no dns_cache field
        levels=[("f-000", 0.0, True)],
        raws={"f-000": [_raw_iter(1, 36.0)]},
    )
    assert h4._session_dns_cache(str(rdir), "s1") is None
    assert h4._session_dns_cache(str(rdir), "does-not-exist") is None


def test_summarize_point_is_unrounded(tmp_path):
    # bootstrap_ci rounds its "point" to 2dp; the dominance point must stay exact
    # (δ_error=0.302 needs 3dp). 0.302 must survive, not collapse to 0.3.
    p = h4.Placement(
        label="p", f=0.0, r=1, mode="packed", fault="pod-delete", campaign="C1", role="frontier"
    )
    p.session_values = {ERR: [0.302, 0.302, 0.302]}
    p.summarize(seed=1)
    assert p.stats[ERR]["point"] == 0.302  # exact, not 0.3


def test_incomplete_placement_is_unranked_not_non_dominated(monkeypatch, tmp_path):
    # A frontier placement missing a DV must NOT appear non-dominated; it is
    # unranked (nonDominated=None) and warned about, and excluded from the count.
    def fake_collect(results_dir, campaign, role, dns_cache=None):
        full = _pl(40.0, 3.0, 0.6, "full", "frontier", "C1")
        incomplete = _pl(20.0, 1.0, 0.0, "inc", "frontier", "C1")
        incomplete.stats[DEPTH]["point"] = None  # missing a DV
        return {("a",): full, ("b",): incomplete}, []

    monkeypatch.setattr(h4, "collect_campaign", fake_collect)
    monkeypatch.setattr(h4.Placement, "summarize", lambda self, seed=42: None)
    (tmp_path / "c1-online-boutique").mkdir()
    res = h4.build_frontier(str(tmp_path))
    by = {p["label"]: p for p in res["placements"]}
    assert by["inc"]["nonDominated"] is None  # unranked, NOT True
    assert "incomplete" in " ".join(res["warnings"])
    assert res["frontierSize"] == 2  # both are frontier-role
    # only the complete one is ranked; it is non-dominated (nothing complete dominates it)
    assert res["nonDominated"] == ["C1:full"]
    assert res["nonDominatedCount"] == 1


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
    # CLI plumbing smoke: argv → build → render → JSON + fig. The fixture carries
    # only the latency face (no depth/error), so its single placement is INCOMPLETE
    # → unranked (empty frontier) with a surfaced warning — exercising main() plus
    # the incomplete-placement path end-to-end. (The ranked happy path is covered by
    # test_build_frontier_marks_roles_and_frontier.)
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
    assert written["frontierSize"] == 1  # the cell is frontier-role
    assert written["nonDominated"] == []  # but incomplete (latency-only) → unranked
    assert any("incomplete" in w for w in written["warnings"])
    by = {p["label"]: p for p in written["placements"]}
    assert by["f=0, r=1"]["nonDominated"] is None
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


# ── regression guards for fix 4 (identity membership) + fix 5 (plot None/0.0) ──


def test_coord_or_treats_zero_as_present_not_missing():
    # The core of fix 5: a valid 0.0 coordinate/CI bound must NOT fall back.
    assert h4._coord_or(0.0, 9.9) == 0.0  # `0.0 or 9.9` would wrongly give 9.9
    assert h4._coord_or(None, 9.9) == 9.9
    assert h4._coord_or(3.2, 9.9) == 3.2


def test_nd_membership_keyed_by_identity_not_label(monkeypatch, tmp_path):
    # Fix 4 regression guard: two frontier placements that COLLIDE on (campaign,label).
    # The label omits the fault class, so the same (f, r, mode) under two different
    # faults renders the identical "f=0, r=1" label on two distinct placements (the
    # grouping key includes fault, so they ARE distinct). The winner dominates the
    # loser; a label-keyed membership set would mark BOTH non-dominated — identity
    # keying must flag only the winner.
    def fake_collect(results_dir, campaign, role, dns_cache=None):
        win = _pl(20.0, 1.0, 0.0, "f=0, r=1", "frontier", "C1")  # dominates
        win.fault = "pod-delete"
        lose = _pl(40.0, 3.0, 0.6, "f=0, r=1", "frontier", "C1")  # dominated; SAME label
        lose.fault = "node-drain"  # differs only in fault (omitted from the label)
        return {("pod-delete",): win, ("node-drain",): lose}, []

    monkeypatch.setattr(h4, "collect_campaign", fake_collect)
    monkeypatch.setattr(h4.Placement, "summarize", lambda self, seed=42: None)
    (tmp_path / "c1-online-boutique").mkdir()
    res = h4.build_frontier(str(tmp_path))

    assert res["nonDominatedCount"] == 1  # only the winner, despite the shared label
    flags = [p["nonDominated"] for p in res["placements"]]
    assert sorted(flags, key=lambda b: b is True) == [False, True]  # exactly one each
    # The winner (lower latency) is the non-dominated one.
    by_lat = sorted(res["placements"], key=lambda p: p["stats"][LAT]["point"])
    assert by_lat[0]["nonDominated"] is True and by_lat[1]["nonDominated"] is False


def test_scatter_colour_kw_grey_for_missing_value_mapped_otherwise():
    # Fix 5 regression guard (grey decision): a missing error rate → solid grey
    # with NO cmap args (reverting to `c=[err]` / cmap-0.0 fails the first assert);
    # a present value → colour-mapped on viridis over [0,1] (cmap args present, so
    # the cmap-args-only-when-mapping refactor is pinned — a revert that always
    # passed cmap args would not satisfy the grey branch's exact dict).
    assert h4._scatter_colour_kw(None) == {"c": "lightgray"}
    assert h4._scatter_colour_kw(0.0) == {"c": [0.0], "cmap": "viridis", "vmin": 0, "vmax": 1}
    assert h4._scatter_colour_kw(0.5) == {"c": [0.5], "cmap": "viridis", "vmin": 0, "vmax": 1}


def test_plot_no_colormap_warning_and_has_colorbar(tmp_path):
    # Fix 5 regression guard (no unused-cmap warning + dedicated colorbar): a 0.0
    # latency point (exercises _coord_or in the sort key) and an err=None point.
    # Reverting the colour_kw refactor to always pass cmap/vmin/vmax on the grey
    # string colour re-raises matplotlib's "No data for colormapping" UserWarning,
    # which this `simplefilter("error")` turns into a failure.
    import warnings as _warnings

    res = {
        "deltas": {LAT: 4.4, DEPTH: 1.0, ERR: 0.302},
        "placements": [
            _p_dict("C1", "f0", 0.0, 1.0, 0.04, True),  # valid 0.0 latency (sorts first)
            _p_dict("C1", "f1", 40.0, 1.0, None, False),  # err=None → grey (sorts last)
        ],
        "nonDominated": ["C1:f0"],
        "frontierSize": 2,
        "nonDominatedCount": 1,
        "warnings": [],
    }
    fig_path = tmp_path / "f.png"
    with _warnings.catch_warnings():
        _warnings.simplefilter("error", UserWarning)
        fig, ax = h4.plot(res, str(fig_path))
    assert fig_path.exists() and fig_path.stat().st_size > 0
    # A dedicated colorbar axes exists (separate from the main axes), independent of
    # the last-plotted (grey) point.
    assert len(fig.axes) == 2 and fig.axes[-1] is not ax
