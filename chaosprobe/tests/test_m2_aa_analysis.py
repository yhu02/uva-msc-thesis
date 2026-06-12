"""Tests for scripts/m2_aa_analysis.py (M2 A/A calibration analysis, D4 forms)."""

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
# Fixture builders (synthetic v2 session summaries + raw per-condition files)
# ──────────────────────────────────────────────────────────────────────


def _raw_iter(
    n,
    *,
    ew_pre=None,
    ew_during=None,
    udp=None,
    flush=None,
    score=None,
    verdict="PASS",
    pre_taints=(),
):
    """One raw-file iteration record carrying the requested per-iteration metrics."""
    metrics = {}
    phases = {}
    if ew_pre is not None:
        phases["pre-chaos"] = {"routes": {"a->b": {"p95_ms": ew_pre}, "/": {"p95_ms": 999.0}}}
    if ew_during is not None:
        phases["during-chaos"] = {"routes": {"a->b": {"p95_ms": ew_during}}}
    if phases:
        metrics["latency"] = {"phases": phases}
    if udp is not None:  # (pre_count, during_count) on one node
        metrics["conntrackProtocolSamples"] = [
            {"node": "w1", "proto": "udp", "count": udp[0], "phase": "pre-chaos"},
            {"node": "w1", "proto": "udp", "count": udp[1], "phase": "during-chaos"},
            {"node": "w1", "proto": "tcp", "count": 5, "phase": "pre-chaos"},
            {"node": "w1", "proto": "udp", "count": 7, "phase": "post-chaos"},
        ]
    if flush is not None:  # (pre_mean, during_mean)
        metrics["prometheus"] = {
            "phases": {
                "pre-chaos": {"metrics": {"conntrack_entries_per_node": {"mean": flush[0]}}},
                "during-chaos": {"metrics": {"conntrack_entries_per_node": {"mean": flush[1]}}},
            }
        }
    it = {"iteration": n, "verdict": verdict, "metrics": metrics}
    if score is not None:
        it["resilienceScore"] = score
    if pre_taints:
        it["preChaosTaintReasons"] = list(pre_taints)
    return it


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
    taints=None,
):
    """A synthetic summary.json: level_specs = [(condition, targetF, liveF)].

    ``taints`` maps condition -> {iteration: [taintReasons]} into the
    ``perLevel[].perIteration`` records (the engine-side taint channel).
    """
    taints = taints or {}
    per_level = []
    for cond, f, live in level_specs:
        record = {
            "condition": cond,
            "targetF": f,
            "liveAchievedF": live,
            "accepted": cond not in rejected,
            "rejectionReasons": ["fraction_target_missed"] if cond in rejected else [],
        }
        if cond in taints:
            record["perIteration"] = [
                {"iteration": it, "taintReasons": list(reasons)}
                for it, reasons in sorted(taints[cond].items())
            ]
        per_level.append(record)
    summary = {
        "runId": run_id,
        "timestamp": timestamp,
        "v2Session": {
            "solverSeed": solver_seed,
            "replicas": replicas,
            "mode": mode,
            "levels": [f for _, f, _ in level_specs],
            "workers": list(workers),
            "perLevel": per_level,
        },
    }
    if top_level_strategies:
        summary["strategies"] = {}
    else:
        summary["faults"] = {"pod-delete": {"strategies": {}}}
        if extra_fault:
            summary["faults"]["zz-extra"] = {"strategies": {}}
    return summary


def _write_run(results_dir, name, summary, raws=None):
    """Write one session dir: summary.json + raw per-condition f-XXX.json files."""
    run_dir = results_dir / name
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps(summary))
    for cond, iterations in (raws or {}).items():
        raw = {
            "placement": {"assignments": {"svc-a": "w1", "svc-b": "w2"}},
            "iterations": iterations,
        }
        (run_dir / f"{cond}.json").write_text(json.dumps(raw))


_LEVELS = [("f-000", 0.0), ("f-025", 0.25), ("f-050", 0.5), ("f-075", 0.75), ("f-100", 1.0)]


def _clean_pair_specs():
    """Two 5-level sessions with tiny mixed-direction deltas (a true null).

    Returns (specs_a, raws_a, specs_b, raws_b); the udp prober is absent in
    B's three highest levels so the udp metric exercises the sign test.
    """
    specs_a, raws_a, specs_b, raws_b = [], {}, [], {}
    for i, (cond, f) in enumerate(_LEVELS):
        sign = 1 if i % 2 else -1
        live = None if cond == "f-050" else f  # exercise the None == None identity path
        specs_a.append((cond, f, live))
        raws_a[cond] = [
            _raw_iter(
                1, ew_pre=40.0 + i, udp=(100, 60 + i), flush=(10000, 8000 + 10 * i), score=49.0 + i
            ),
            _raw_iter(
                2, ew_pre=40.0 + i, udp=(100, 60 + i), flush=(10000, 8000 + 10 * i), score=51.0 + i
            ),
        ]
        specs_b.append((cond, f, live))
        udp_b = None if i >= 3 else (100, 61 + i)
        raws_b[cond] = [
            _raw_iter(
                1,
                ew_pre=40.0 + i + 0.5 * sign,
                udp=udp_b,
                flush=(10000, 8000 + 10 * i + 5 * sign),
                score=49.5 + i,
            ),
            _raw_iter(
                2,
                ew_pre=40.0 + i + 0.5 * sign,
                udp=udp_b,
                flush=(10000, 8000 + 10 * i + 5 * sign),
                score=50.5 + i + sign,
            ),
        ]
    return specs_a, raws_a, specs_b, raws_b


def _write_clean_pair(results_dir):
    specs_a, raws_a, specs_b, raws_b = _clean_pair_specs()
    _write_run(
        results_dir,
        "20260101-000000",
        _summary(specs_a, run_id="run-a", timestamp="2026-01-01T00:00:00+00:00"),
        raws_a,
    )
    _write_run(
        results_dir,
        "20260102-000000",
        _summary(specs_b, run_id="run-b", timestamp="2026-01-02T00:00:00+00:00"),
        raws_b,
    )


# ──────────────────────────────────────────────────────────────────────
# Per-iteration metric extraction (the D4 canonical extraction)
# ──────────────────────────────────────────────────────────────────────


def test_route_classifiers():
    assert aa.is_east_west("a->b")
    assert aa.is_east_west("a->b,c->d")  # contention edge group
    assert not aa.is_east_west("loadgenerator->frontend")  # DESIGN §4 exclusion
    assert not aa.is_east_west("a->b, loadgenerator->b")
    assert not aa.is_east_west("/cart")
    assert aa.is_user_route("/cart")
    assert not aa.is_user_route("/_healthz")
    assert not aa.is_user_route("a->b")


def test_east_west_p95_median_over_routes():
    latency = {
        "phases": {
            "pre-chaos": {
                "routes": {
                    "a->b": {"p95_ms": 10.0},
                    "b->c": {"p95_ms": 20.0},
                    "c->d": {"p95_ms": 31.0},
                    "/": {"p95_ms": 999.0},  # user route — excluded
                    "loadgenerator->a": {"p95_ms": 999.0},  # loadgen — excluded
                    "x->y": {"p95_ms": "bad"},  # non-numeric — excluded
                    "y->z": "not-a-dict",  # malformed — excluded
                }
            }
        }
    }
    assert aa.east_west_p95(latency, "pre-chaos") == 20.0  # MEDIAN, not mean (D4)
    assert aa.east_west_p95(latency, "during-chaos") is None
    assert aa.east_west_p95({}, "pre-chaos") is None


def test_user_error_rate():
    latency = {
        "phases": {
            "during-chaos": {
                "routes": {
                    "/": {"errorCount": 2, "sampleCount": 8},
                    "/cart": {"errorCount": 0, "sampleCount": 10},
                    "/_healthz": {"errorCount": 50, "sampleCount": 0},  # excluded
                    "a->b": {"errorCount": 50, "sampleCount": 0},  # excluded
                    "/bad": "not-a-dict",  # excluded
                }
            }
        }
    }
    assert aa.user_error_rate(latency, "during-chaos") == 2 / 20
    assert aa.user_error_rate(latency, "pre-chaos") is None


def test_udp_cluster_phase_mean_sums_per_node_means():
    samples = [
        {"node": "w1", "proto": "udp", "count": 10, "phase": "pre-chaos"},
        {"node": "w1", "proto": "udp", "count": 20, "phase": "pre-chaos"},
        {"node": "w2", "proto": "udp", "count": 5, "phase": "pre-chaos"},
        {"node": "w2", "proto": "tcp", "count": 999, "phase": "pre-chaos"},
        {"node": "w2", "proto": "udp", "count": 999, "phase": "during-chaos"},
    ]
    assert aa.udp_cluster_phase_mean(samples, "pre-chaos") == 20.0  # mean(10,20) + 5
    assert aa.udp_cluster_phase_mean(samples, "post-chaos") is None
    assert aa.udp_cluster_phase_mean([], "pre-chaos") is None


def test_udp_pre_slope():
    samples = [
        # w1: +60 entries over 60 s -> +1/s -> +60/min
        {
            "node": "w1",
            "proto": "udp",
            "count": 100,
            "phase": "pre-chaos",
            "ts": "2026-01-01T00:00:00+00:00",
        },
        {
            "node": "w1",
            "proto": "udp",
            "count": 160,
            "phase": "pre-chaos",
            "ts": "2026-01-01T00:01:00+00:00",
        },
        # w2: single point — no slope
        {
            "node": "w2",
            "proto": "udp",
            "count": 5,
            "phase": "pre-chaos",
            "ts": "2026-01-01T00:00:00+00:00",
        },
        # w3: two points at the same timestamp — sxx == 0, skipped
        {
            "node": "w3",
            "proto": "udp",
            "count": 5,
            "phase": "pre-chaos",
            "ts": "2026-01-01T00:00:00+00:00",
        },
        {
            "node": "w3",
            "proto": "udp",
            "count": 9,
            "phase": "pre-chaos",
            "ts": "2026-01-01T00:00:00+00:00",
        },
        # wrong proto / phase: ignored
        {
            "node": "w1",
            "proto": "tcp",
            "count": 1,
            "phase": "pre-chaos",
            "ts": "2026-01-01T00:02:00+00:00",
        },
        {
            "node": "w1",
            "proto": "udp",
            "count": 1,
            "phase": "during-chaos",
            "ts": "2026-01-01T00:02:00+00:00",
        },
    ]
    # A sample without a timestamp is skipped, never a crash.
    samples.append({"node": "w1", "proto": "udp", "count": 1, "phase": "pre-chaos"})
    assert aa.udp_pre_slope(samples) == 60.0
    assert aa.udp_pre_slope([]) is None


def test_es_trough():
    slices = {
        "preChaos": {"services": {"a": {"ready": 2}, "b": {"ready": 1}, "c": {"ready": 0}}},
        "duringChaos": {"services": {"a": {"ready": 1}, "b": {"ready": 0}, "c": {"ready": 0}}},
    }
    depth, zeroed = aa.es_trough(slices, ["a", "b", "c", "missing"])
    assert depth == 2.0  # (2-1) + (1-0) + 0
    assert zeroed == 1.0  # b driven 1 -> 0; c was already 0 pre-chaos
    assert aa.es_trough({}, ["a"]) == (None, None)
    assert aa.es_trough(slices, ["missing"]) == (None, None)  # nothing measured


def test_iteration_conntrack_flush_pct():
    it = _raw_iter(1, flush=(10000, 8000))
    assert aa.iteration_conntrack_flush_pct(it["metrics"]) == 20.0
    assert aa.iteration_conntrack_flush_pct({}) is None
    assert aa.iteration_conntrack_flush_pct(_raw_iter(1, flush=(0, 5))["metrics"]) is None
    non_dict = {
        "prometheus": {
            "phases": {
                "pre-chaos": {"metrics": {"conntrack_entries_per_node": {"mean": 10.0}}},
                "during-chaos": {"metrics": {"conntrack_entries_per_node": [1, 2]}},
            }
        }
    }
    assert aa.iteration_conntrack_flush_pct(non_dict) is None


def test_extract_iteration_full_row():
    it = _raw_iter(1, ew_pre=40.0, ew_during=44.0, udp=(100, 60), flush=(10000, 8000), score=50.0)
    it["metrics"]["endpointSlices"] = {
        "preChaos": {"services": {"svc-a": {"ready": 2}}},
        "duringChaos": {"services": {"svc-a": {"ready": 0}}},
    }
    it["metrics"]["recovery"] = {"summary": {"meanRecovery_ms": 1500}}
    it["loadGeneration"] = {"stats": {"errorRate": 0.25}}
    row = aa.extract_iteration(it, ["svc-a", "svc-b"])
    assert set(row) == set(aa.ITERATION_OUTCOMES)
    assert row["ew_p95_pre_ms"] == 40.0
    assert row["ew_p95_during_ms"] == 44.0
    assert row["udp_conntrack_drop_entries"] == 40.0  # ABSOLUTE entries (D4)
    assert row["udp_conntrack_drop_pct"] == 40.0  # context only
    assert row["conntrack_flush_pct"] == 20.0
    assert row["es_trough_depth_pods"] == 2.0
    assert row["es_zero_services"] == 1.0
    assert row["trough_duration_s"] == 1.5
    assert row["loadgen_err"] == 0.25
    assert row["score"] == 50.0


def test_extract_iteration_absent_and_error_verdict():
    empty = aa.extract_iteration({}, [])
    assert all(value is None for value in empty.values())
    # An ERROR verdict fabricates a 0.0 score — not a valid measurement.
    errored = aa.extract_iteration(_raw_iter(1, score=0.0, verdict="ERROR"), [])
    assert errored["score"] is None
    # loadGeneration fallback path (metrics.loadGeneration, no stats block).
    legacy = {"metrics": {"loadGeneration": {"errorRate": 0.5}}}
    assert aa.extract_iteration(legacy, [])["loadgen_err"] == 0.5


def test_median_or_none_filters_none_and_nan():
    assert aa._median_or_none([1.0, None, 3.0, float("nan")]) == 2.0
    assert aa._median_or_none([None, None]) is None
    assert aa._median_or_none([]) is None


# ──────────────────────────────────────────────────────────────────────
# Taint plumbing (taintReasons + preChaosTaintReasons — D4: every metric)
# ──────────────────────────────────────────────────────────────────────


def test_summary_tainted_iterations():
    per_level = [
        {
            "condition": "f-100",
            "perIteration": [
                {"iteration": 1, "taintReasons": []},
                {"iteration": 3, "taintReasons": ["app_ready_timeout", "pre_chaos_errors_high"]},
            ],
        },
        {"perIteration": [{"iteration": 1, "taintReasons": ["x"]}]},  # no condition: skipped
        {"condition": "f-000"},  # no perIteration: fine
    ]
    tainted, taints = aa.summary_tainted_iterations(per_level)
    assert tainted == {("f-100", 3)}
    assert taints == [
        "f-100 it3: app_ready_timeout",
        "f-100 it3: pre_chaos_errors_high",
    ]


def test_load_condition_outcomes_taint_exclusion_preserves_alignment(tmp_path):
    iterations = [
        _raw_iter(1, ew_pre=40.0, score=50.0),
        _raw_iter(2, ew_pre=4000.0, score=999.0),  # summary-tainted below
        _raw_iter(3, ew_pre=4000.0, score=888.0, pre_taints=["pre_chaos_errors_high"]),
    ]
    _write_run(tmp_path, "run", _summary([("f-000", 0.0, 0.0)]), {"f-000": iterations})
    tainted = {("f-000", 2)}
    taints = []
    out = aa.load_condition_outcomes(str(tmp_path / "run"), "f-000", tainted, taints)
    assert out is not None
    # None rows preserve index alignment for the paired iteration-level tests.
    assert out["ew_p95_pre_ms"] == [40.0, None, None]
    assert out["score"] == [50.0, None, None]
    assert tainted == {("f-000", 2), ("f-000", 3)}
    assert taints == ["f-000 it3: pre-chaos pre_chaos_errors_high"]
    # Missing raw file -> None (caller decides how loudly to complain).
    assert aa.load_condition_outcomes(str(tmp_path / "run"), "f-999", set(), []) is None


def test_main_taint_excluded_from_every_metric(tmp_path, capsys):
    """The D4 fix: tainted iterations leave EVERY metric, not just score."""
    specs = [("f-000", 0.0, 0.0)]
    raws_a = {
        "f-000": [
            _raw_iter(1, ew_pre=40.0, udp=(100, 60), flush=(10000, 8000), score=50.0),
            _raw_iter(2, ew_pre=4000.0, udp=(9000, 0), flush=(10000, 0), score=1.0),
            _raw_iter(
                3,
                ew_pre=4000.0,
                udp=(9000, 0),
                flush=(10000, 0),
                score=1.0,
                pre_taints=["pre_chaos_errors_high"],
            ),
        ]
    }
    raws_b = {
        "f-000": [
            _raw_iter(n, ew_pre=40.5, udp=(100, 61), flush=(10000, 8005), score=50.5)
            for n in (1, 2, 3)
        ]
    }
    summary_a = _summary(
        specs,
        timestamp="2026-01-01T00:00:00+00:00",
        taints={"f-000": {1: [], 2: ["app_ready_timeout"]}},
    )
    _write_run(tmp_path, "a", summary_a, raws_a)
    _write_run(tmp_path, "b", _summary(specs, timestamp="2026-01-02T00:00:00+00:00"), raws_b)
    out_json = tmp_path / "out.json"
    rc = aa.main(["--results-dir", str(tmp_path), "--json", str(out_json)])
    out = capsys.readouterr().out
    assert rc == 2  # clean, 1 pair < 3
    assert "TAINTED (excluded): a: f-000 it2: app_ready_timeout" in out
    assert "TAINTED (excluded): a: f-000 it3: pre-chaos pre_chaos_errors_high" in out
    result = json.loads(out_json.read_text())
    assert result["taintedIterations"] == [
        "a: f-000 it2: app_ready_timeout",
        "a: f-000 it3: pre-chaos pre_chaos_errors_high",
    ]
    # Every session-condition value comes from the single untainted iteration.
    per_level = result["pairs"][0]["metrics"]
    assert per_level["ew_p95_pre_ms"]["perLevel"]["f-000"]["a"] == 40.0
    assert per_level["udp_conntrack_drop_entries"]["perLevel"]["f-000"]["a"] == 40.0
    assert per_level["conntrack_flush_pct"]["perLevel"]["f-000"]["a"] == 20.0
    assert per_level["score"]["perLevel"]["f-000"]["a"] == 50.0
    # The tainted iterations also stay out of the variance decomposition.
    assert result["varianceComponents"]["score"]["nObservations"] == 4  # 1 (a) + 3 (b)


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


def test_discover_warns_on_missing_raw_file(tmp_path):
    _write_run(tmp_path, "r1", _summary([("f-000", 0.0, 0.0)]))  # no raw f-000.json
    sessions, warnings = aa.discover_sessions(str(tmp_path))
    assert len(sessions) == 1
    assert any("r1: raw f-000.json missing" in w for w in warnings)
    obs = sessions[0].levels["f-000"]
    assert obs.iteration_values == {}
    assert all(value is None for value in obs.values.values())


def test_parse_session_fault_variants():
    specs = [("f-000", 0.0, 0.0)]
    warnings = []
    multi = _summary(specs, extra_fault=True)
    session = aa.parse_session("r1", multi, warnings)
    assert session is not None
    assert session.key.fault == "pod-delete"
    assert set(session.levels) == {"f-000"}
    assert any("2 fault blocks" in w and "pod-delete" in w for w in warnings)

    top = _summary(specs, top_level_strategies=True)
    session = aa.parse_session("r2", top, [])
    assert session is not None
    assert session.key.fault == ""
    # parse_session is metadata-only: values arrive via load_session_outcomes.
    assert all(value is None for value in session.levels["f-000"].values.values())


def test_parse_session_per_level_edge_cases():
    summary = _summary([("f-000", 0.0, 0.0)])
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
    specs = [("f-000", 0.0, 0.0), ("f-100", 1.0, 1.0)]
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
    specs = [("f-000", 0.0, 0.0)]
    raws = {"f-000": [_raw_iter(1, score=50.0)]}
    summary_a = _summary(specs, timestamp="2026-01-01T00:00:00+00:00")
    summary_b = _summary(specs, timestamp="2026-01-02T00:00:00+00:00")
    summary_b["faults"] = {"cpu-hog": summary_b["faults"].pop("pod-delete")}
    _write_run(tmp_path, "a", summary_a, raws)
    _write_run(tmp_path, "b", summary_b, raws)
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
    assert "(none)" in out  # no tainted iterations
    assert "1 pair(s) found vs pre-registered >= 3 — INSUFFICIENT" in out

    result = json.loads(out_json.read_text())
    assert result["schema"] == aa.SCHEMA
    assert result["findings"] == [] and result["pipelineBugs"] == []
    assert result["taintedIterations"] == []
    pair = result["pairs"][0]
    assert pair["sessions"] == ["20260101-000000", "20260102-000000"]
    assert pair["cell"]["fault"] == "pod-delete"
    # 5 shared levels -> Wilcoxon for fully-populated metrics.
    assert pair["metrics"]["score"]["method"] == "wilcoxon_signed_rank"
    assert pair["metrics"]["score"]["nLevelsTested"] == 5
    # The registered unit: session-condition MEDIAN over iterations.
    assert pair["metrics"]["score"]["perLevel"]["f-000"]["a"] == 50.0  # median(49, 51)
    assert pair["metrics"]["ew_p95_pre_ms"]["perLevel"]["f-000"]["a"] == 40.0
    # udp absent in 2 of B's levels -> sign test on the 3 complete pairs;
    # the delta is in ABSOLUTE entries (D4: no ratio denominator).
    assert pair["metrics"]["udp_conntrack_drop_entries"]["method"] == "sign_test"
    assert pair["metrics"]["udp_conntrack_drop_entries"]["nLevelsTested"] == 3
    assert pair["metrics"]["udp_conntrack_drop_entries"]["perLevel"]["f-000"]["delta"] == 1.0
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
    assert ("udp_conntrack_drop_entries", 1.0) not in bands  # missing in B there
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
    specs = [(c, f, f) for c, f in _LEVELS]
    raws_a = {c: [_raw_iter(1, score=50.0 + i)] for i, (c, _) in enumerate(_LEVELS)}
    raws_b = {c: [_raw_iter(1, score=48.0 + i)] for i, (c, _) in enumerate(_LEVELS)}
    _write_run(tmp_path, "a", _summary(specs, timestamp="2026-01-01T00:00:00+00:00"), raws_a)
    _write_run(tmp_path, "b", _summary(specs, timestamp="2026-01-02T00:00:00+00:00"), raws_b)
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
    specs = [(c, f, f) for c, f in _LEVELS]
    for p in range(3):
        raws_a = {c: [_raw_iter(1, score=50.0 + i)] for i, (c, _) in enumerate(_LEVELS)}
        raws_b = {
            c: [_raw_iter(1, score=48.0 + 0.9 * i - 0.01 * p)] for i, (c, _) in enumerate(_LEVELS)
        }
        _write_run(
            tmp_path,
            f"a{p}",
            _summary(specs, timestamp=f"2026-01-0{2 * p + 1}T00:00:00+00:00"),
            raws_a,
        )
        _write_run(
            tmp_path,
            f"b{p}",
            _summary(specs, timestamp=f"2026-01-0{2 * p + 2}T00:00:00+00:00"),
            raws_b,
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
    specs_a = [("f-000", 0.0, 0.25)]
    specs_b = [("f-000", 0.0, 0.5)]
    _write_run(tmp_path, "a", _summary(specs_a, timestamp="2026-01-01T00:00:00+00:00"))
    _write_run(tmp_path, "b", _summary(specs_b, timestamp="2026-01-02T00:00:00+00:00"))
    rc = aa.main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "PIPELINE BUG — liveAchievedF mismatch in pair-01 at f-000: 0.25 != 0.5" in out
    assert "liveAchievedF identity: MISMATCH" in out
    assert "WARNING: a: raw f-000.json missing" in out  # raws absent, warned not crashed


def test_main_empty_results_dir(tmp_path, capsys):
    rc = aa.main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "(no pairs)" in out
    assert "0 pair(s) found vs pre-registered >= 3 — INSUFFICIENT" in out


def test_main_sufficient_pairs_and_warnings_printed(tmp_path, capsys):
    spec = [("f-000", 0.0, 0.0)]
    raws = {"f-000": [_raw_iter(1, score=50.0)]}
    for i in range(6):
        _write_run(
            tmp_path, f"run-{i}", _summary(spec, timestamp=f"2026-01-0{i + 1}T00:00:00+00:00"), raws
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
    specs_a = [("f-000", 0.0, 0.0), ("f-100", 1.0, 1.0)]
    specs_b = [("f-000", 0.0, 0.0)]
    metricless = {"f-000": [_raw_iter(1)], "f-100": [_raw_iter(1)]}  # no metric payloads
    summary_b = _summary(specs_b, timestamp="2026-01-02T00:00:00+00:00")
    # Same registered cell (same levels grid) but f-100 never produced a
    # perLevel record in B — the pair must drop it with a warning.
    summary_b["v2Session"]["levels"] = [0.0, 1.0]
    _write_run(tmp_path, "a", _summary(specs_a, timestamp="2026-01-01T00:00:00+00:00"), metricless)
    _write_run(tmp_path, "b", summary_b, {"f-000": [_raw_iter(1)]})
    rc = aa.main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "WARNING: pair-01: level(s) f-100 present in only one session" in out
    # No metric values at all -> n=0, no test, p rendered as '-'.
    assert "method=- p=-" in out


def test_rejected_level_excluded_with_warning(tmp_path, capsys):
    specs = [("f-000", 0.0, 0.0), ("f-100", 1.0, 1.0)]
    raws = {
        "f-000": [_raw_iter(1, score=50.0)],
        "f-100": [_raw_iter(1, score=60.0)],
    }
    _write_run(tmp_path, "a", _summary(specs, timestamp="2026-01-01T00:00:00+00:00"), raws)
    _write_run(
        tmp_path,
        "b",
        _summary(specs, timestamp="2026-01-02T00:00:00+00:00", rejected=("f-100",)),
        raws,
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
    iteration_values = {}
    if flush is not None:
        values["conntrack_flush_pct"] = flush
    if score is not None:
        values["score"] = score
    if iters:
        iteration_values["score"] = list(iters)
    return aa.LevelObs(
        condition=cond,
        target_f=target_f,
        live_achieved_f=live,
        accepted=accepted,
        rejection_reasons=[],
        values=values,
        iteration_values=iteration_values,
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
    assert components["udp_conntrack_drop_entries"]["icc"] is None
    assert components["udp_conntrack_drop_entries"]["nObservations"] == 0


def test_cell_values():
    with_iters = _obs("f-000", 0.0, score=2.0, iters=[1.0, None, 3.0])
    assert aa._cell_values("score", with_iters) == [1.0, 3.0]  # None rows excluded
    without_iters = _obs("f-000", 0.0, score=2.0)
    assert aa._cell_values("score", without_iters) == [2.0]  # session-level fallback
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
