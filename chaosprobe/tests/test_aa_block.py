"""Tests for scripts/aa_block.py (supplementary A/A variance outcomes).

The per-iteration extraction itself is the canonical one
(scripts/m2_aa_analysis.py, tested in test_m2_aa_analysis.py); these tests
cover the supplementary statistics (variance components, noise bands,
null tests) and the report/CLI surface around it.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.test_m2_aa_analysis import _raw_iter, _summary, _write_run, aa  # noqa: F401

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "aa_block.py"
_spec = importlib.util.spec_from_file_location("aa_block", _SCRIPT)
assert _spec is not None and _spec.loader is not None
ab = importlib.util.module_from_spec(_spec)
sys.modules["aa_block"] = ab
_spec.loader.exec_module(ab)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


def _aa_summary(level_specs, *, solver_seed=0, order_seed=11, taints=None, live_f=None, **kw):
    """A v2 summary with the aa_block-specific fields (orderSeed, assignment)."""
    summary = _summary(level_specs, solver_seed=solver_seed, taints=taints, **kw)
    summary["v2Session"]["orderSeed"] = order_seed
    for record in summary["v2Session"]["perLevel"]:
        record["assignment"] = {"svc-a": "w1", "svc-b": "w2"}
        record.setdefault(
            "perIteration",
            [
                {
                    "iteration": 1,
                    "liveAchievedF": (live_f or {}).get(record["condition"], record["targetF"]),
                    "taintReasons": [],
                }
            ],
        )
    return summary


_TWO_LEVELS = [("f-000", 0.0, 0.0), ("f-100", 1.0, 1.0)]


def _raws(base, jitter=0.0):
    """Two-condition raw files with ew/udp/flush/score payloads around ``base``."""
    return {
        cond: [
            _raw_iter(
                n,
                ew_pre=base + 10 * i + 0.1 * n + jitter,
                ew_during=base + 12 * i + jitter,
                udp=(1000, 400 + 10 * i + n + jitter),
                flush=(10000, 8000 + 10 * i + jitter),
                score=base + i + 0.2 * n + jitter,
            )
            for n in (1, 2)
        ]
        for i, (cond, _, _) in enumerate(_TWO_LEVELS)
    }


def _write_pair(tmp_path, jitter_b=0.3):
    _write_run(
        tmp_path,
        "s1",
        _aa_summary(_TWO_LEVELS, solver_seed=0, order_seed=11),
        _raws(40.0),
    )
    _write_run(
        tmp_path,
        "s2",
        _aa_summary(_TWO_LEVELS, solver_seed=0, order_seed=12),
        _raws(40.0, jitter=jitter_b),
    )


# ──────────────────────────────────────────────────────────────────────
# Session loading (delegates extraction to the canonical module)
# ──────────────────────────────────────────────────────────────────────


def test_load_session_skips_and_warns(tmp_path, capsys):
    (tmp_path / "inflight").mkdir()
    assert ab.load_session(str(tmp_path / "inflight")) is None
    not_v2 = tmp_path / "not-v2"
    not_v2.mkdir()
    (not_v2 / "summary.json").write_text(json.dumps({"runId": "x"}))
    assert ab.load_session(str(not_v2)) is None
    out = capsys.readouterr().out
    assert "[skip] inflight: no summary.json" in out
    assert "[skip] not-v2: no v2Session block" in out


def test_load_session_values_taints_and_missing_raw(tmp_path, capsys):
    raws = {
        "f-000": [
            _raw_iter(1, ew_pre=40.0, udp=(100, 60), score=50.0),
            _raw_iter(2, ew_pre=42.0, udp=(100, 62), score=52.0),
            _raw_iter(3, ew_pre=999.0, score=0.0, pre_taints=["pre_chaos_errors_high"]),
        ]
        # f-100 raw file deliberately missing.
    }
    summary = _aa_summary(_TWO_LEVELS, taints={"f-000": {2: ["app_ready_timeout"]}, "f-100": {}})
    _write_run(tmp_path, "s1", summary, raws)
    session = ab.load_session(str(tmp_path / "s1"))
    out = capsys.readouterr().out
    assert session is not None
    assert "[warn] s1/f-100: raw file missing; condition skipped" in out
    assert "f-100" not in session["values"]
    # Shared canonical extraction: taint-excluded with None rows aligned.
    assert session["values"]["f-000"]["ew_p95_pre_ms"] == [40.0, None, None]
    assert session["values"]["f-000"]["udp_conntrack_drop_entries"] == [40.0, None, None]
    assert "f-000 it2: app_ready_timeout" in session["taints"]
    assert "f-000 it3: pre-chaos pre_chaos_errors_high" in session["taints"]
    # The session-condition unit value is the median of the untainted rows.
    assert ab.cond_value(session, "f-000", "score") == 50.0
    assert ab.cond_value(session, "f-100", "score") is None


def test_group_pairs_orders_by_seed():
    def _s(name, seed, fault="pod-delete", mode="packed", levels=(0.0, 1.0), workers=("w1",)):
        return {
            "name": name,
            "solverSeed": seed,
            "fault": fault,
            "replicas": 1,
            "mode": mode,
            "levels": list(levels),
            "workers": list(workers),
        }

    groups = ab.group_pairs([_s("a", 1), _s("b", 0), _s("c", 1)])
    assert list(groups) == ["pair-seed0", "pair-seed1"]
    assert len(groups["pair-seed1"]) == 2

    # A solverSeed reused by a DIFFERENT cell must never pair (mirrors the
    # canonical PairKey): cross-fault sessions split into labeled groups.
    cross = ab.group_pairs(
        [
            _s("a", 0),
            _s("b", 0, fault="cpu-hog"),
            _s("c", 0, fault="cpu-hog", mode="spread"),
            _s("d", 0, fault="cpu-hog", mode="spread", levels=(0.0, 0.5)),
            _s("e", 0, workers=("w1", "w2")),  # rescaled cluster: not identical-placement
        ]
    )
    assert len(cross) == 5  # nothing pairs
    assert "pair-seed0-pod-delete-r1-packed" in cross
    assert "pair-seed0-pod-delete-r1-packed+" in cross  # workers-only collision
    assert "pair-seed0-cpu-hog-r1-spread" in cross
    assert "pair-seed0-cpu-hog-r1-spread+" in cross  # level-grid-only collision


# ──────────────────────────────────────────────────────────────────────
# Statistics helpers
# ──────────────────────────────────────────────────────────────────────


def test_quantile_and_fmt():
    assert ab._quantile([5.0], 0.95) == 5.0
    assert ab._quantile([0.0, 10.0], 0.5) == 5.0
    assert ab._quantile([0.0, 1.0, 2.0, 3.0], 1.0) == 3.0
    assert ab._fmt(None) == "-"
    assert ab._fmt(1.23456) == "1.23"
    assert ab._fmt(2.5e7) == "2.500e+07"  # large magnitudes go scientific


def _hand_sessions():
    s1 = {
        "name": "s1",
        "conditions": ["f-000"],
        "values": {"f-000": {"score": [1.0, 3.0]}},
    }
    s2 = {
        "name": "s2",
        "conditions": ["f-000"],
        "values": {"f-000": {"score": [5.0, 7.0]}},
    }
    return s1, s2


def test_variance_components_hand_computed():
    s1, s2 = _hand_sessions()
    vc = ab.variance_components([s1, s2], {0: [s1, s2]}, "score")
    # within: mean(pvar([1,3]), pvar([5,7])) = 1; between: pvar([2, 6]) = 4.
    assert vc["perCondition"]["f-000"]["sig2_within"] == 1.0
    assert vc["perCondition"]["f-000"]["sig2_between_pair"] == 4.0
    assert vc["pooled"]["sd_within"] == 1.0
    assert vc["pooled"]["sd_between_pair"] == 2.0
    assert vc["pooled"]["between_share"] == 0.8
    # An outcome with no data anywhere: all None, no crash.
    empty = ab.variance_components([s1, s2], {0: [s1, s2]}, "loadgen_err")
    assert empty["pooled"]["sd_within"] is None
    assert empty["pooled"]["between_share"] is None


def test_noise_band_hand_computed_and_empty():
    s1, s2 = _hand_sessions()
    band = ab.noise_band({0: [s1, s2]}, "score")
    # Medians 2 vs 6 -> one |A-B| = 4; mean level = 4.
    assert band["perCondition"] == {"f-000": [4.0]}
    assert band["n"] == 1
    assert band["p95_abs_diff"] == 4.0
    assert band["p95_pct_of_level"] == 100.0
    assert ab.noise_band({0: [s1]}, "score") == {"perCondition": {}, "n": 0}
    assert ab.noise_band({0: [s1, s2]}, "loadgen_err") == {"perCondition": {}, "n": 0}


def test_null_tests_levels_and_pooling():
    s1, s2 = _hand_sessions()
    tests = ab.null_tests({"pair-seed0": [s1, s2]}, "score")
    entry = tests["perPair"]["pair-seed0"]
    # Only 1 shared condition -> condition-level test is n/a; the 2
    # iteration pairs are enough for the sensitivity pairing.
    assert entry["condition_level"] is None
    assert entry["iteration_level"]["n_pairs"] == 2
    assert tests["pooled_condition_level"] is None  # < 2 pooled condition pairs


def test_null_tests_iteration_pairing_drops_none_and_nan():
    s1, s2 = _hand_sessions()
    s1 = dict(s1, values={"f-000": {"score": [1.0, None, float("nan"), 4.0]}})
    s2 = dict(s2, values={"f-000": {"score": [5.0, 6.0, 7.0, 8.0]}})
    tests = ab.null_tests({"pair-seed0": [s1, s2]}, "score")
    # None (tainted) and NaN rows must not pair: NaN joins neither Wilcoxon
    # rank sum yet inflates n, silently biasing the p-value.
    assert tests["perPair"]["pair-seed0"]["iteration_level"]["n_pairs"] == 2


def test_significant_findings_flags_all_test_levels():
    w_hit = {"p_two_sided": 0.01, "w_statistic": 0.0, "n_pairs": 5}
    w_ok = {"p_two_sided": 0.5, "w_statistic": 5.0, "n_pairs": 5}
    tests = {
        "score": {
            "perPair": {"pair-seed0": {"condition_level": w_hit, "iteration_level": w_ok}},
            "pooled_condition_level": w_hit,
        },
        "ew_p95_pre_ms": {
            "perPair": {"pair-seed0": {"condition_level": None, "iteration_level": None}},
            "pooled_condition_level": None,
        },
    }
    hits = ab.significant_findings(tests, 0.05)
    assert hits == [
        "score / pair-seed0 / condition_level: p=0.01 (W=0.0, n=5)",
        "score / pooled-conditions: p=0.01 (W=0.0, n=5)",
    ]


def test_design_checks():
    base = {
        "orderSeed": 11,
        "conditions": ["f-000"],
        "assignments": {"f-000": {"svc-a": "w1"}},
        "achievedF": {"f-000": [0.5]},
    }
    a = dict(base, name="a")
    b = dict(base, name="b", orderSeed=11)  # identical order seed
    notes = ab.design_checks({"pair-seed0": [a, b]})
    assert any("orderSeeds identical" in n for n in notes)
    c = dict(
        base,
        name="c",
        orderSeed=12,
        assignments={"f-000": {"svc-a": "w2"}},
        achievedF={"f-000": [0.75]},
    )
    notes = ab.design_checks({"pair-seed0": [a, c]})
    assert any("solver assignments differ" in n for n in notes)
    assert any("achieved f differs" in n for n in notes)
    assert ab.design_checks({"pair-seed0": [a]}) == []  # incomplete pair: nothing to check


# ──────────────────────────────────────────────────────────────────────
# End-to-end report + CLI
# ──────────────────────────────────────────────────────────────────────


def test_main_e2e_clean_pair(tmp_path, capsys):
    _write_pair(tmp_path)
    out_json = tmp_path / "aa.json"
    ab.main(["--results-dir", str(tmp_path), "--json", str(out_json)])
    out = capsys.readouterr().out
    assert "pair-seed0: complete" in out
    assert "No tainted iterations in any banked session." in out
    assert "No statistically significant A/A finding at alpha=0.05" in out
    assert "V2-H1 SESOI check" in out
    assert f"JSON written to {out_json}" in out
    result = json.loads(out_json.read_text())
    assert set(result["outcomes"]) == {key for key, _, _ in ab.OUTCOMES}
    band = result["outcomes"]["ew_p95_pre_ms"]["noiseBand"]
    assert band["n"] == 2  # two conditions, one complete pair
    assert result["significantFindings"] == []
    assert result["anomalies"] == []


def test_main_pending_extra_anomalies_and_excludes(tmp_path, capsys):
    # Pair seed 0: two sessions with the SAME order seed (anomaly), different
    # assignments on one level (anomaly), plus a third seed-0 session
    # (ignored) and a lone seed-1 session (PENDING).  One summary taint so
    # the taint listing prints.
    s1 = _aa_summary(_TWO_LEVELS, solver_seed=0, order_seed=11)
    s2 = _aa_summary(
        _TWO_LEVELS,
        solver_seed=0,
        order_seed=11,
        taints={"f-000": {1: ["app_ready_timeout"]}},
        live_f={"f-100": 0.25},  # achieved f differs from s1's 1.0
    )
    s2["v2Session"]["perLevel"][1]["assignment"] = {"svc-a": "w9"}
    s3 = _aa_summary(_TWO_LEVELS, solver_seed=0, order_seed=13)
    s4 = _aa_summary(_TWO_LEVELS, solver_seed=1, order_seed=21)
    for name, summary in [("s1", s1), ("s2", s2), ("s3", s3), ("s4", s4)]:
        _write_run(tmp_path, name, summary, _raws(40.0))
    (tmp_path / "stray-file.txt").write_text("not a session dir")
    (tmp_path / "skipme").mkdir()
    ab.main(["--results-dir", str(tmp_path), "--exclude", "skipme"])
    out = capsys.readouterr().out
    assert "[skip] skipme: excluded on the command line" in out
    assert "complete (extra sessions ignored: ['s3'])" in out
    assert "pair-seed1: PENDING — 1/2 sessions banked (s4)" in out
    assert "Tainted iterations (registered: never quoted in results):" in out
    assert "s2: f-000 it1: app_ready_timeout" in out
    assert "!! pair-seed0: orderSeeds identical (11)" in out
    assert "solver assignments differ" in out
    assert "achieved f differs" in out


def test_main_no_complete_pair(tmp_path, capsys):
    _write_run(tmp_path, "s1", _aa_summary(_TWO_LEVELS, solver_seed=0, order_seed=11), _raws(40.0))
    ab.main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "No complete pair yet — variance bands and null tests need >=1 pair." in out


def test_main_no_sessions_exits(tmp_path):
    with pytest.raises(SystemExit, match="No complete A/A sessions found."):
        ab.main(["--results-dir", str(tmp_path)])


def test_main_iteration_level_finding_prints_note(tmp_path, capsys):
    # A constant session-level offset with distinct condition-level |deltas|:
    # the condition-level Wilcoxon stays at its 5-level floor (p = 0.0591,
    # n.s.) while the 15 same-signed iteration-level pairs reject — the
    # exact F1 signature from the M2 report, which must print the
    # "iteration-level = between-session variance" explainer.
    levels = [(f"f-{i:03d}", i / 4.0, i / 4.0) for i in range(5)]
    raws_a = {
        cond: [_raw_iter(n, score=50.0 + i) for n in (1, 2, 3)]
        for i, (cond, _, _) in enumerate(levels)
    }
    raws_b = {
        cond: [_raw_iter(n, score=48.0 + 0.9 * i) for n in (1, 2, 3)]
        for i, (cond, _, _) in enumerate(levels)
    }
    _write_run(tmp_path, "s1", _aa_summary(levels, solver_seed=0, order_seed=11), raws_a)
    _write_run(tmp_path, "s2", _aa_summary(levels, solver_seed=0, order_seed=12), raws_b)
    ab.main(["--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "STATISTICALLY SIGNIFICANT A/A FINDING(S) at alpha=0.05" in out
    assert "score / pair-seed0 / iteration_level" in out
    assert "Note: all hits are iteration-level (sensitivity pairing)." in out
