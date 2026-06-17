"""Tests for scripts/c3_h2_dns.py (V2-H2 placement-dependence + DNS intervention)."""

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("m2_aa_analysis")
c3 = _load("c3_h2_dns")


def _sess(run, dns, packed, spread, ts=None):
    return c3.C3Session(run=run, timestamp=ts or run, dns_cache=dns, packed=packed, spread=spread)


# ── _one_sided_greater ─────────────────────────────────────────────────


def test_one_sided_in_direction_halves_two_sided():
    res = {"n_nonzero": 4, "p_two_sided": 0.04, "w_plus": 10.0, "w_minus": 0.0}
    assert c3._one_sided_greater(res) == 0.02


def test_one_sided_against_direction_is_complement():
    res = {"n_nonzero": 4, "p_two_sided": 0.04, "w_plus": 0.0, "w_minus": 10.0}
    assert abs(c3._one_sided_greater(res) - 0.98) < 1e-9


def test_one_sided_no_nonzero_pairs_is_none():
    assert (
        c3._one_sided_greater({"n_nonzero": 0, "p_two_sided": 1.0, "w_plus": 0, "w_minus": 0})
        is None
    )


# ── analyze: the conjunction over synthetic per-session drops ──────────


def test_clean_conjunction_pass(monkeypatch):
    # cache-off: spread (≈100) ≫ packed (≈10) → (a) direction holds.
    # cache-on spread ≈20 → shrinkage (100−20)/100 = 0.8 ≥ 0.5 → (b) met.
    # packed unchanged on/off → secondary ~no effect.
    off = [_sess(f"off{i}", "off", 10 + i, 100 + i) for i in range(3)]
    on = [_sess(f"on{i}", "on", 10 + i, 20 + i) for i in range(3)]
    monkeypatch.setattr(c3, "collect_sessions", lambda _d: (off + on, []))
    out = c3.analyze("x")
    assert out["nCacheOff"] == 3 and out["nCacheOn"] == 3
    assert out["placementDependence"]["rescueMet"] is True
    assert out["mechanismShrinkage"]["barMet"] is True
    assert out["mechanismShrinkage"]["shrinkageMedian"] >= 0.5
    assert out["conjunction"] is True
    assert out["familyInputMaxP"] is not None


def test_placement_dependence_direction_fails(monkeypatch):
    # spread ≈ packed cache-off → no placement dependence → (a) direction False.
    off = [_sess(f"off{i}", "off", 50 + i, 50 + i) for i in range(3)]
    on = [_sess(f"on{i}", "on", 10 + i, 10 + i) for i in range(3)]
    monkeypatch.setattr(c3, "collect_sessions", lambda _d: (off + on, []))
    out = c3.analyze("x")
    assert out["placementDependence"]["rescueMet"] is False
    assert out["conjunction"] is False


def test_mechanism_below_bar_fails(monkeypatch):
    # cache-on barely shrinks spread (100→95 → 5% < 50%) → (b) bar not met.
    off = [_sess(f"off{i}", "off", 10, 100) for i in range(3)]
    on = [_sess(f"on{i}", "on", 10, 95) for i in range(3)]
    monkeypatch.setattr(c3, "collect_sessions", lambda _d: (off + on, []))
    out = c3.analyze("x")
    assert out["placementDependence"]["rescueMet"] is True
    assert out["mechanismShrinkage"]["barMet"] is False
    assert out["conjunction"] is False


def test_non_positive_cache_off_drop_dropped_from_shrinkage(monkeypatch):
    # a cache-off spread drop ≤ 0 has an undefined shrinkage denominator → dropped.
    off = [_sess("off0", "off", 10, 0.0), _sess("off1", "off", 10, 100.0)]
    on = [_sess("on0", "on", 10, 5.0), _sess("on1", "on", 10, 20.0)]
    monkeypatch.setattr(c3, "collect_sessions", lambda _d: (off + on, []))
    out = c3.analyze("x")
    assert any("non-positive cache-off drop" in w for w in out["warnings"])
    # only the second pair (100→20) contributes a shrinkage.
    assert out["mechanismShrinkage"]["n_pairs"] == 1


def test_unequal_spread_counts_warns_and_pairs_min(monkeypatch):
    off = [_sess(f"off{i}", "off", 10, 100) for i in range(3)]
    on = [_sess("on0", "on", 10, 20)]  # only 1 cache-on
    monkeypatch.setattr(c3, "collect_sessions", lambda _d: (off + on, []))
    out = c3.analyze("x")
    assert any("unequal valid spread counts" in w for w in out["warnings"])
    assert out["mechanismShrinkage"]["n_pairs"] == 1


# ── collect_sessions: grouping + exclusion ─────────────────────────────


class _Obs:
    def __init__(self, accepted=True):
        self.accepted = accepted


class _Sess:
    def __init__(self, run, conds, ts="t"):
        self.run = run
        self.timestamp = ts
        self.levels = {c: _Obs(acc) for c, acc in conds.items()}
        self.tainted = set()
        self.taints = []


def test_collect_groups_by_cache_and_skips_non_c3(monkeypatch):
    sessions = [
        _Sess("a", {"f-000": True, "f-100": True}),
        _Sess("b", {"f-000": True, "f-100": True}),
        _Sess("c", {"f-000": True, "f-100": True}),  # non-C3 (dnsCache None)
    ]
    monkeypatch.setattr(c3, "discover_sessions", lambda _d: (sessions, []))
    monkeypatch.setattr(
        c3, "_session_dns_cache", lambda _d, run: {"a": "off", "b": "on", "c": None}[run]
    )
    monkeypatch.setattr(
        c3,
        "load_condition_outcomes",
        lambda _d, cond, *_a, **_k: {c3.OUTCOME: [100.0 if cond == "f-100" else 10.0]},
    )
    out, warnings = c3.collect_sessions("x")
    assert {s.run for s in out} == {"a", "b"}
    assert any("not a C3 session" in w and "c" in w for w in warnings)
    a = next(s for s in out if s.run == "a")
    assert a.dns_cache == "off" and a.spread == 100.0 and a.packed == 10.0


def test_collect_excludes_unaccepted_condition(monkeypatch):
    sessions = [_Sess("a", {"f-000": True, "f-100": False})]  # spread condition rejected
    monkeypatch.setattr(c3, "discover_sessions", lambda _d: (sessions, []))
    monkeypatch.setattr(c3, "_session_dns_cache", lambda _d, run: "off")
    monkeypatch.setattr(
        c3, "load_condition_outcomes", lambda _d, cond, *_a, **_k: {c3.OUTCOME: [42.0]}
    )
    out, _ = c3.collect_sessions("x")
    assert out[0].packed == 42.0 and out[0].spread is None  # rejected spread → None


def test_main_smoke(tmp_path, monkeypatch, capsys):
    off = [_sess(f"off{i}", "off", 10, 100) for i in range(3)]
    on = [_sess(f"on{i}", "on", 10, 20) for i in range(3)]
    monkeypatch.setattr(c3, "collect_sessions", lambda _d: (off + on, []))
    out_json = tmp_path / "h2.json"
    rc = c3.main(["--results-dir", str(tmp_path), "--json", str(out_json)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "V2-H2" in printed and "CONJUNCTION" in printed
    import json

    assert json.loads(out_json.read_text())["conjunction"] is True


def test_session_dns_cache_reads_and_handles_missing(tmp_path):
    run = tmp_path / "r1"
    run.mkdir()
    (run / "summary.json").write_text('{"v2Session": {"dnsCache": "off"}}')
    assert c3._session_dns_cache(str(tmp_path), "r1") == "off"
    # missing summary.json → OSError → None (the except path)
    assert c3._session_dns_cache(str(tmp_path), "nope") is None


def test_condition_udp_drop_none_when_no_outcomes(monkeypatch):
    sess = _Sess("a", {"f-000": True})
    monkeypatch.setattr(c3, "load_condition_outcomes", lambda *_a, **_k: None)
    assert c3._condition_udp_drop("x", sess, "f-000") is None


def test_main_prints_warnings(tmp_path, monkeypatch, capsys):
    off = [_sess(f"off{i}", "off", 10, 100) for i in range(2)]
    on = [_sess("on0", "on", 10, 20)]  # unequal counts → emits a warning
    monkeypatch.setattr(c3, "collect_sessions", lambda _d: (off + on, []))
    rc = c3.main(["--results-dir", str(tmp_path)])  # no --json
    assert rc == 0
    out = capsys.readouterr().out
    assert "! " in out and "unequal valid spread counts" in out
