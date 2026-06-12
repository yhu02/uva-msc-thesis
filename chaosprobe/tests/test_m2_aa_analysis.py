"""Tests for scripts/m2_aa_analysis.py (M2 A/A calibration analysis)."""

import importlib.util
import json
import math
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "m2_aa_analysis.py"
_spec = importlib.util.spec_from_file_location("m2_aa_analysis", _SCRIPT)
assert _spec is not None and _spec.loader is not None
aa = importlib.util.module_from_spec(_spec)
sys.modules["m2_aa_analysis"] = aa  # dataclasses resolve annotations via sys.modules
_spec.loader.exec_module(aa)


# ──────────────────────────────────────────────────────────────────────
# Fixture builders (synthetic v2 session summaries)
# ──────────────────────────────────────────────────────────────────────


def _ew_entry(route, p95):
    return {
        "route": route,
        "iterations": 3,
        "latencyProber": {"during-chaos": {"meanP95_ms": p95}},
    }


def _iter(score, verdict="PASS", healthy=True):
    return {"resilienceScore": score, "verdict": verdict, "preChaosHealthy": healthy}


def _strategy(ew=None, flush=None, udp=None, score=None, score_healthy=None, iterations=None):
    """A minimal per-condition strategy block carrying the requested metrics."""
    strat = {"aggregated": {}, "metrics": {}}
    rva = [{"route": "/", "latencyProber": {"during-chaos": {"meanP95_ms": 999.0}}}]
    if ew is not None:
        rva.append(_ew_entry("a->b", ew))
    strat["aggregated"]["routeViewAggregate"] = rva
    if flush is not None:  # (pre_mean, during_mean)
        strat["metrics"]["prometheus"] = {
            "phases": {
                "pre-chaos": {"metrics": {"conntrack_entries_per_node": {"mean": flush[0]}}},
                "during-chaos": {"metrics": {"conntrack_entries_per_node": {"mean": flush[1]}}},
            }
        }
    if udp is not None:  # (pre_count, during_count)
        strat["metrics"]["conntrackProtocolSamples"] = [
            {"node": "w1", "proto": "udp", "count": udp[0], "phase": "pre-chaos"},
            {"node": "w1", "proto": "udp", "count": udp[1], "phase": "during-chaos"},
            {"node": "w1", "proto": "tcp", "count": 5, "phase": "pre-chaos"},
            {"node": "w1", "proto": "udp", "count": 7, "phase": "post-chaos"},
        ]
    if score is not None:
        strat["aggregated"]["meanResilienceScore"] = score
    if score_healthy is not None:
        strat["aggregated"]["meanResilienceScore_healthyOnly"] = score_healthy
    if iterations is not None:
        strat["iterations"] = iterations
    return strat


def _summary(
    level_specs,
    *,
    solver_seed=0,
    replicas=1,
    mode="packed",
    workers=("w1", "w2"),
    run_id="run-x",
    timestamp="2026-01-01T00:00:00+00:00",
    top_level_strategies=False,
    extra_fault=False,
    rejected=(),
):
    """A synthetic summary.json: level_specs = [(condition, targetF, liveF, strategy)]."""
    per_level = [
        {
            "condition": cond,
            "targetF": f,
            "liveAchievedF": live,
            "accepted": cond not in rejected,
            "rejectionReasons": ["fraction_target_missed"] if cond in rejected else [],
        }
        for cond, f, live, _ in level_specs
    ]
    strategies = {cond: strat for cond, _, _, strat in level_specs}
    summary = {
        "runId": run_id,
        "timestamp": timestamp,
        "v2Session": {
            "solverSeed": solver_seed,
            "replicas": replicas,
            "mode": mode,
            "levels": [f for _, f, _, _ in level_specs],
            "workers": list(workers),
            "perLevel": per_level,
        },
    }
    if top_level_strategies:
        summary["strategies"] = strategies
    else:
        summary["faults"] = {"pod-delete": {"strategies": strategies}}
        if extra_fault:
            summary["faults"]["zz-extra"] = {"strategies": {}}
    return summary


def _write_run(results_dir, name, summary):
    run_dir = results_dir / name
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps(summary))


_LEVELS = [("f-000", 0.0), ("f-025", 0.25), ("f-050", 0.5), ("f-075", 0.75), ("f-100", 1.0)]


def _clean_pair_specs():
    """Two 5-level sessions with tiny mixed-direction deltas (a true null)."""
    specs_a, specs_b = [], []
    for i, (cond, f) in enumerate(_LEVELS):
        sign = 1 if i % 2 else -1
        live = None if cond == "f-050" else f  # exercise the None == None identity path
        specs_a.append(
            (
                cond,
                f,
                live,
                _strategy(
                    ew=40.0 + i,
                    flush=(10000, 8000 + 10 * i),
                    udp=(100, 60 + i),
                    score=50.0 + i,
                    iterations=[_iter(49.0 + i), _iter(51.0 + i)],
                ),
            )
        )
        specs_b.append(
            (
                cond,
                f,
                live,
                _strategy(
                    ew=40.0 + i + 0.5 * sign,
                    flush=(10000, 8000 + 10 * i + 5 * sign),
                    udp=None if i >= 3 else (100, 61 + i),
                    score=50.0 + i + 0.5 * sign,
                    iterations=[_iter(49.5 + i), _iter(50.5 + i + sign)],
                ),
            )
        )
    return specs_a, specs_b


def _write_clean_pair(results_dir):
    specs_a, specs_b = _clean_pair_specs()
    _write_run(
        results_dir,
        "20260101-000000",
        _summary(specs_a, run_id="run-a", timestamp="2026-01-01T00:00:00+00:00"),
    )
    _write_run(
        results_dir,
        "20260102-000000",
        _summary(specs_b, run_id="run-b", timestamp="2026-01-02T00:00:00+00:00"),
    )


# ──────────────────────────────────────────────────────────────────────
# Metric extraction
# ──────────────────────────────────────────────────────────────────────


def test_east_west_during_p95():
    assert aa.east_west_during_p95({}) is None
    ns_only = {"aggregated": {"routeViewAggregate": [_ew_entry("/", 10.0)]}}
    assert aa.east_west_during_p95(ns_only) is None
    no_value = {"aggregated": {"routeViewAggregate": [{"route": "a->b"}]}}
    assert aa.east_west_during_p95(no_value) is None
    two = {"aggregated": {"routeViewAggregate": [_ew_entry("a->b", 10.0), _ew_entry("b->c", 20.0)]}}
    assert aa.east_west_during_p95(two) == 15.0


def test_conntrack_flush_pct():
    assert aa.conntrack_flush_pct(_strategy(flush=(10000, 8000))) == 20.0
    assert aa.conntrack_flush_pct({}) is None
    assert aa.conntrack_flush_pct(_strategy(flush=(0, 5))) is None  # pre == 0 -> undefined
    pre_only = {
        "metrics": {
            "prometheus": {
                "phases": {
                    "pre-chaos": {"metrics": {"conntrack_entries_per_node": {"mean": 10.0}}},
                    "during-chaos": {"metrics": {"conntrack_entries_per_node": [1, 2]}},
                }
            }
        }
    }
    assert aa.conntrack_flush_pct(pre_only) is None  # non-dict entry -> _phase_mean None


def test_udp_conntrack_drop_pct():
    assert aa.udp_conntrack_drop_pct(_strategy(udp=(100, 60))) == 40.0
    assert aa.udp_conntrack_drop_pct({}) is None
    tcp_only = {
        "metrics": {
            "conntrackProtocolSamples": [
                {"node": "w1", "proto": "tcp", "count": 5, "phase": "pre-chaos"}
            ]
        }
    }
    assert aa.udp_conntrack_drop_pct(tcp_only) is None
    assert aa.udp_conntrack_drop_pct(_strategy(udp=(0, 0))) is None  # zero pre mean


def test_aggregate_score_prefers_healthy_only():
    assert aa.aggregate_score(_strategy(score=40.0, score_healthy=45.0)) == 45.0
    assert aa.aggregate_score(_strategy(score=40.0)) == 40.0  # legacy fallback
    assert aa.aggregate_score({}) is None


def test_healthy_score_iterations_filters_like_aggregation():
    iters = [
        _iter(50.0),
        _iter(0.0, verdict="ERROR"),  # fabricated 0.0 — must never enter
        _iter(30.0, healthy=False),  # tainted — excluded while healthy ones exist
        {"verdict": "PASS"},  # no score recorded — excluded
    ]
    assert aa.healthy_score_iterations({"iterations": iters}) == [50.0]
    # All valid iterations tainted -> the aggregation's healthy-or-valid fallback.
    all_tainted = [_iter(30.0, healthy=False), _iter(32.0, healthy=False)]
    assert aa.healthy_score_iterations({"iterations": all_tainted}) == [30.0, 32.0]
    assert aa.healthy_score_iterations({}) == []


# ──────────────────────────────────────────────────────────────────────
# Discovery + parsing
# ──────────────────────────────────────────────────────────────────────


def test_discover_skips_and_warns(tmp_path):
    _write_run(tmp_path, "not-v2", {"runId": "x"})
    _write_run(tmp_path, "malformed", {"v2Session": {"replicas": 1}})
    garbage = tmp_path / "garbage"
    garbage.mkdir()
    (garbage / "summary.json").write_text("{not json")
    sessions, warnings = aa.discover_sessions(str(tmp_path))
    assert sessions == []
    assert any("no v2Session block" in w for w in warnings)
    assert any("malformed v2Session cell fields" in w for w in warnings)
    assert any("unreadable summary.json" in w for w in warnings)


def test_parse_session_fault_variants():
    specs = [("f-000", 0.0, 0.0, _strategy(score=10.0))]
    warnings = []
    multi = _summary(specs, extra_fault=True)
    session = aa.parse_session("r1", multi, warnings)
    assert session is not None
    assert session.key.fault == "pod-delete"
    assert session.levels["f-000"].values["score"] == 10.0
    assert any("2 fault blocks" in w and "pod-delete" in w for w in warnings)

    top = _summary(specs, top_level_strategies=True)
    session = aa.parse_session("r2", top, [])
    assert session is not None
    assert session.key.fault == ""
    assert session.levels["f-000"].values["score"] == 10.0


def test_parse_session_per_level_edge_cases():
    summary = _summary([("f-000", 0.0, 0.0, _strategy())])
    summary["v2Session"]["perLevel"].append({"liveAchievedF": 1.0})  # no condition: skipped
    summary["v2Session"]["perLevel"].append({"condition": "f-100"})  # no targetF: 0.0
    session = aa.parse_session("r", summary, [])
    assert session is not None
    assert set(session.levels) == {"f-000", "f-100"}
    assert session.levels["f-100"].target_f == 0.0
    assert session.levels["f-100"].accepted is True  # absent flag defaults accepted

    bare = {"v2Session": _summary([])["v2Session"]}
    bare["v2Session"].pop("perLevel")
    session = aa.parse_session("r", bare, [])
    assert session is not None and session.levels == {}


def test_cell_key_normalizes_levels_and_workers_order():
    specs = [("f-000", 0.0, 0.0, _strategy()), ("f-100", 1.0, 1.0, _strategy())]
    reversed_order = _summary(specs, workers=("w2", "w1"))
    reversed_order["v2Session"]["levels"] = [1.0, 0.0]
    natural = _summary(specs, workers=("w1", "w2"))
    key_a = aa.parse_session("a", reversed_order, []).key
    key_b = aa.parse_session("b", natural, []).key
    assert key_a == key_b  # ordering differences must not split an A/A cell


# ──────────────────────────────────────────────────────────────────────
# Pairing
# ──────────────────────────────────────────────────────────────────────


def _session(run, timestamp, key, levels=None):
    return aa.Session(run=run, run_id=run, timestamp=timestamp, key=key, levels=levels or {})


def test_pairing_chronological_odd_and_singleton():
    key = aa.PairKey("pod-delete", 0, 1, "packed", (0.0,), ("w1",))
    other = aa.PairKey("pod-delete", 7, 1, "packed", (0.0,), ("w1",))
    s_none = _session("r-none", None, key)  # no timestamp sorts first
    s1 = _session("r1", "2026-01-02T00:00:00+00:00", key)
    s2 = _session("r2", "2026-01-03T00:00:00+00:00", key)
    lone = _session("r-lone", "2026-01-01T00:00:00+00:00", other)
    warnings = []
    pairs = aa.pair_sessions([s2, s1, s_none, lone], warnings)
    assert len(pairs) == 1
    assert (pairs[0].a.run, pairs[0].b.run) == ("r-none", "r1")
    assert any("r2: unpaired session" in w and "3 session(s)" in w for w in warnings)
    assert any("r-lone: unpaired session" in w for w in warnings)


def test_different_faults_never_pair(tmp_path, capsys):
    specs = [("f-000", 0.0, 0.0, _strategy(score=50.0))]
    summary_a = _summary(specs, timestamp="2026-01-01T00:00:00+00:00")
    summary_b = _summary(specs, timestamp="2026-01-02T00:00:00+00:00")
    summary_b["faults"] = {"cpu-hog": summary_b["faults"].pop("pod-delete")}
    _write_run(tmp_path, "a", summary_a)
    _write_run(tmp_path, "b", summary_b)
    rc = aa.main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 2  # nothing paired -> insufficient, never a cross-fault "A/A" pair
    assert "(no pairs)" in out
    assert "a: unpaired session" in out and "b: unpaired session" in out


# ──────────────────────────────────────────────────────────────────────
# End-to-end: clean A/A pair (true null)
# ──────────────────────────────────────────────────────────────────────


def test_main_clean_pair(tmp_path, capsys):
    _write_clean_pair(tmp_path)
    out_json = tmp_path / "out.json"
    rc = aa.main(["--results-dir", str(tmp_path), "--json", str(out_json)])
    out = capsys.readouterr().out
    assert rc == 2  # clean but only 1 pair < the registered 3
    assert "No A/A findings" in out
    assert "liveAchievedF identity: OK" in out
    assert "1 pair(s) found vs pre-registered >= 3 — INSUFFICIENT" in out

    result = json.loads(out_json.read_text())
    assert result["schema"] == aa.SCHEMA
    assert result["findings"] == [] and result["pipelineBugs"] == []
    pair = result["pairs"][0]
    assert pair["sessions"] == ["20260101-000000", "20260102-000000"]
    assert pair["cell"]["fault"] == "pod-delete"
    # 5 shared levels -> Wilcoxon for fully-populated metrics.
    assert pair["metrics"]["score"]["method"] == "wilcoxon_signed_rank"
    assert pair["metrics"]["score"]["nLevelsTested"] == 5
    # udp present in only 3 of B's levels -> sign test on the 3 complete pairs.
    assert pair["metrics"]["udp_conntrack_drop_pct"]["method"] == "sign_test"
    assert pair["metrics"]["udp_conntrack_drop_pct"]["nLevelsTested"] == 3
    # The f-050 level had liveAchievedF None in both sessions: still equal.
    assert pair["liveAchievedF"]["perLevel"]["f-050"]["equal"] is True
    # Cross-pair drift test: one mean level-delta for the single pair vs 0.
    assert result["crossPairTests"]["score"]["nPairs"] == 1
    assert result["crossPairTests"]["score"]["method"] == "sign_test"
    assert result["crossPairTests"]["score"]["meanDeltaPerPair"] == [
        pair["metrics"]["score"]["meanDelta"]
    ]
    assert result["crossPairTests"]["score"]["finding"] is False
    # Noise band rows exist for every populated (metric, level).
    bands = {(r["metric"], r["targetF"]): r for r in result["noiseBand"]}
    assert bands[("score", 0.0)]["nPairs"] == 1
    assert ("udp_conntrack_drop_pct", 1.0) not in bands  # missing in B there
    assert result["sufficiency"] == {
        "pairsFound": 1,
        "registeredMinimum": 3,
        "sufficient": False,
    }


def test_per_pair_tie_artifact_finding_at_default_alpha(tmp_path, capsys):
    # A constant +2 score offset (plausible for the 1-dp-rounded score) makes
    # all five |deltas| tied: the exact two-sided p is 0.0625, but the
    # helper's tie-corrected normal approximation gives 0.0369 < 0.05 — the
    # documented per-pair artifact.  It must still be reported as a finding
    # (any p < alpha triggers investigation per the amended protocol).
    specs_a = [(c, f, f, _strategy(score=50.0 + i)) for i, (c, f) in enumerate(_LEVELS)]
    specs_b = [(c, f, f, _strategy(score=48.0 + i)) for i, (c, f) in enumerate(_LEVELS)]
    _write_run(tmp_path, "a", _summary(specs_a, timestamp="2026-01-01T00:00:00+00:00"))
    _write_run(tmp_path, "b", _summary(specs_b, timestamp="2026-01-02T00:00:00+00:00"))
    rc = aa.main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "A/A FINDING — investigate: pair-01 metric=score wilcoxon_signed_rank p=0.0369" in out
    assert "amended protocol: investigate, fix, rerun" in out


def test_cross_pair_drift_test(tmp_path, capsys):
    # A consistent ~+2 score offset between the sessions of all 3 pairs, with
    # all |deltas| distinct (tied deltas would trip the per-pair tie
    # artifact): per-pair Wilcoxon stays sub-threshold at alpha=0.05 (most
    # extreme distinct-magnitude p = 0.0591) and the cross-pair sign test
    # bottoms out at its documented 3-pair minimum p = 0.25 — no finding at
    # the default alpha, a finding at alpha=0.3.
    for p in range(3):
        specs_a = [(c, f, f, _strategy(score=50.0 + i)) for i, (c, f) in enumerate(_LEVELS)]
        specs_b = [
            (c, f, f, _strategy(score=48.0 + 0.9 * i - 0.01 * p))
            for i, (c, f) in enumerate(_LEVELS)
        ]
        _write_run(
            tmp_path, f"a{p}", _summary(specs_a, timestamp=f"2026-01-0{2 * p + 1}T00:00:00+00:00")
        )
        _write_run(
            tmp_path, f"b{p}", _summary(specs_b, timestamp=f"2026-01-0{2 * p + 2}T00:00:00+00:00")
        )
    rc = aa.main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0  # sufficient pairs, nothing crosses the default alpha
    assert "No A/A findings" in out
    assert "3 pair(s) found vs pre-registered >= 3 — SUFFICIENT" in out
    assert "Cross-pair drift test" in out

    rc = aa.main(["--results-dir", str(tmp_path), "--alpha", "0.3"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "A/A FINDING — investigate: cross-pair metric=score sign_test p=0.25 < alpha=0.3" in out


def test_main_live_fraction_mismatch_is_pipeline_bug(tmp_path, capsys):
    specs_a = [("f-000", 0.0, 0.25, _strategy(score=50.0))]
    specs_b = [("f-000", 0.0, 0.5, _strategy(score=50.0))]
    _write_run(tmp_path, "a", _summary(specs_a, timestamp="2026-01-01T00:00:00+00:00"))
    _write_run(tmp_path, "b", _summary(specs_b, timestamp="2026-01-02T00:00:00+00:00"))
    rc = aa.main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "PIPELINE BUG — liveAchievedF mismatch in pair-01 at f-000: 0.25 != 0.5" in out
    assert "liveAchievedF identity: MISMATCH" in out


def test_main_empty_results_dir(tmp_path, capsys):
    rc = aa.main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "(no pairs)" in out
    assert "0 pair(s) found vs pre-registered >= 3 — INSUFFICIENT" in out


def test_main_sufficient_pairs_and_warnings_printed(tmp_path, capsys):
    spec = [("f-000", 0.0, 0.0, _strategy(score=50.0))]
    for i in range(6):
        _write_run(
            tmp_path, f"run-{i}", _summary(spec, timestamp=f"2026-01-0{i + 1}T00:00:00+00:00")
        )
    _write_run(tmp_path, "zz-not-v2", {"runId": "x"})  # warned + printed
    rc = aa.main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "3 pair(s) found vs pre-registered >= 3 — SUFFICIENT" in out
    assert "WARNING: zz-not-v2: no v2Session block" in out
    # Identical scores: sign test drops all ties -> p = 1.0, no finding.
    assert "A/A FINDING" not in out


def test_levels_not_shared_warned_and_metricless_pair(tmp_path, capsys):
    specs_a = [("f-000", 0.0, 0.0, _strategy()), ("f-100", 1.0, 1.0, _strategy())]
    specs_b = [("f-000", 0.0, 0.0, _strategy())]
    summary_b = _summary(specs_b, timestamp="2026-01-02T00:00:00+00:00")
    # Same registered cell (same levels grid) but f-100 never produced a
    # perLevel record in B — the pair must drop it with a warning.
    summary_b["v2Session"]["levels"] = [0.0, 1.0]
    _write_run(tmp_path, "a", _summary(specs_a, timestamp="2026-01-01T00:00:00+00:00"))
    _write_run(tmp_path, "b", summary_b)
    rc = aa.main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "WARNING: pair-01: level(s) f-100 present in only one session" in out
    # No metric values at all -> n=0, no test, p rendered as '-'.
    assert "method=- p=-" in out


def test_rejected_level_excluded_with_warning(tmp_path, capsys):
    specs = [
        ("f-000", 0.0, 0.0, _strategy(score=50.0)),
        ("f-100", 1.0, 1.0, _strategy(score=60.0)),
    ]
    _write_run(tmp_path, "a", _summary(specs, timestamp="2026-01-01T00:00:00+00:00"))
    _write_run(
        tmp_path,
        "b",
        _summary(specs, timestamp="2026-01-02T00:00:00+00:00", rejected=("f-100",)),
    )
    out_json = tmp_path / "out.json"
    rc = aa.main(["--results-dir", str(tmp_path), "--json", str(out_json)])
    out = capsys.readouterr().out
    assert rc == 2
    assert (
        "WARNING: pair-01: level f-100 excluded — condition not accepted "
        "(b: fraction_target_missed)" in out
    )
    result = json.loads(out_json.read_text())
    pair = result["pairs"][0]
    assert pair["levelsUsed"] == ["f-000"]
    assert pair["levelsNotAccepted"] == {"f-100": ["b: fraction_target_missed"]}
    # The rejected level enters neither the identity check nor the noise band.
    assert list(pair["liveAchievedF"]["perLevel"]) == ["f-000"]
    assert {r["targetF"] for r in result["noiseBand"]} == {0.0}
    assert result["varianceComponents"]["score"]["nObservations"] == 3  # b's f-100 dropped


# ──────────────────────────────────────────────────────────────────────
# Variance components + noise band (hand-computed)
# ──────────────────────────────────────────────────────────────────────


def _obs(cond, target_f, *, flush=None, score=None, iters=(), live=None, accepted=True):
    values = {metric: None for metric in aa.METRICS}
    if flush is not None:
        values["conntrack_flush_pct"] = flush
    if score is not None:
        values["score"] = score
    return aa.LevelObs(
        condition=cond,
        target_f=target_f,
        live_achieved_f=live,
        accepted=accepted,
        rejection_reasons=[],
        values=values,
        score_iterations=list(iters),
    )


def _hand_pair():
    key = aa.PairKey("pod-delete", 0, 1, "packed", (0.0, 1.0), ("w1",))
    s1 = _session(
        "s1",
        "t1",
        key,
        {
            "f-000": _obs("f-000", 0.0, flush=10.0, score=2.0, iters=[1.0, 3.0]),
            "f-100": _obs("f-100", 1.0, flush=30.0),
            # Rejected on both sides: must enter neither decomposition nor band.
            "f-050": _obs("f-050", 0.5, flush=999.0, accepted=False),
        },
    )
    s2 = _session(
        "s2",
        "t2",
        key,
        {
            "f-000": _obs("f-000", 0.0, flush=12.0, score=6.0, iters=[5.0, 7.0]),
            "f-100": _obs("f-100", 1.0, flush=34.0),
            "f-050": _obs("f-050", 0.5, flush=999.0, accepted=False),
        },
    )
    return aa.Pair(label="pair-01", key=key, a=s1, b=s2)


def test_variance_components_hand_computed():
    components = aa.variance_components([_hand_pair()])

    flush = components["conntrack_flush_pct"]
    # Cells: (p1,f-000)x{s1:[10], s2:[12]}, (p1,f-100)x{s1:[30], s2:[34]}.
    # sig2_iter: no cell has >= 2 values -> 0.  sig2_run: mean(pvar([10,12]),
    # pvar([30,34])) = mean(1, 4) = 2.5.  sig2_strat: pvar([11, 32]) = 110.25.
    assert flush["betweenIteration"] == 0.0
    assert flush["betweenSessionWithinPair"] == 2.5
    assert flush["betweenPairLevel"] == 110.25
    assert flush["icc"] == round(110.25 / 112.75, 4)
    assert flush["nPairLevelCells"] == 2 and flush["nObservations"] == 4

    score = components["score"]
    # One cell group: iterations [1,3] vs [5,7] -> sig2_iter mean(1,1)=1,
    # sig2_run pvar([2,6])=4, sig2_strat pvar([4])=0.
    assert score["betweenIteration"] == 1.0
    assert score["betweenSessionWithinPair"] == 4.0
    assert score["betweenPairLevel"] == 0.0
    assert score["icc"] == 0.0

    # A metric absent everywhere yields an empty decomposition, not a crash.
    assert components["udp_conntrack_drop_pct"]["icc"] is None
    assert components["udp_conntrack_drop_pct"]["nObservations"] == 0


def test_cell_values():
    with_iters = _obs("f-000", 0.0, score=2.0, iters=[1.0, 3.0])
    assert aa._cell_values("score", with_iters) == [1.0, 3.0]
    without_iters = _obs("f-000", 0.0, score=2.0)
    assert aa._cell_values("score", without_iters) == [2.0]
    assert aa._cell_values("conntrack_flush_pct", without_iters) == []


def test_noise_band_hand_computed():
    rows = aa.noise_band([_hand_pair()])
    # ddof=1 variance of a 2-session pair is (a-b)^2/2; RMS-pooled over pairs.
    # The rejected f-050 level (flush=999 both sides) must not appear.
    assert [(r["metric"], r["targetF"]) for r in rows] == [
        ("conntrack_flush_pct", 0.0),
        ("conntrack_flush_pct", 1.0),
        ("score", 0.0),
    ]
    assert rows[0]["withinPairSessionSD"] == round(math.sqrt(2.0), 6)
    assert rows[0]["meanAbsDelta"] == 2.0
    assert rows[1]["withinPairSessionSD"] == round(math.sqrt(8.0), 6)
    assert rows[1]["meanAbsDelta"] == 4.0
    assert rows[2]["nPairs"] == 1


def test_pair_key_label_and_dict():
    key = aa.PairKey("pod-delete", 3, 1, "packed", (0.0, 0.5), ("w1", "w2"))
    assert key.label() == ("fault=pod-delete seed=3 r=1 mode=packed levels=0,0.5 workers=w1,w2")
    assert key.to_dict() == {
        "fault": "pod-delete",
        "solverSeed": 3,
        "replicas": 1,
        "mode": "packed",
        "levels": [0.0, 0.5],
        "workers": ["w1", "w2"],
    }
