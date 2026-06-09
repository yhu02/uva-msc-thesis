"""Tests for scripts/campaign_status.py — multi-session interim aggregator."""

import importlib.util
import math
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))  # imports sibling cross_node_fraction + fault_taxonomy
_SCRIPT = _SCRIPTS / "campaign_status.py"
_spec = importlib.util.spec_from_file_location("campaign_status", _SCRIPT)
assert _spec is not None and _spec.loader is not None
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

_ROUTES = [{"route": "frontend->productcatalogservice"}]


def _strat(scores, pre, during, placements):
    return {
        "experiment": {"perIterationScores": scores},
        "metrics": {
            "prometheus": {
                "phases": {
                    "pre-chaos": {"metrics": {"conntrack_entries_per_node": {"mean": pre}}},
                    "during-chaos": {"metrics": {"conntrack_entries_per_node": {"mean": during}}},
                }
            }
        },
        "aggregated": {"routeViewAggregate": _ROUTES},
        "iterations": [{"podPlacements": placements}],
    }


# flush 40%, frac 1.0 (frontend n1, pcs n2 -> cross)
def _spread():
    return _strat([80, 85], 1000, 600, {"frontend-a-b": "n1", "productcatalogservice-c-d": "n2"})


# flush 0%, frac 0.0 (both on n1)
def _colocate():
    return _strat([66, 66], 1000, 1000, {"frontend-a-b": "n1", "productcatalogservice-c-d": "n1"})


def _summary(strats):
    return {"faults": {"pod-delete": {"strategies": strats}}}


def _session(run):
    return (run, _summary({"spread": _spread(), "colocate": _colocate()}))


# ── collect ─────────────────────────────────────────────────────────────────────


def test_collect_cells_pairs_and_frac():
    # default has flush but no usable placement -> excluded from frac_flush only.
    default = _strat([66, 70], 1000, 700, {})  # flush 30, no placements
    baseline = _strat([100], 1000, 500, {})  # excluded entirely
    summary = _summary(
        {"spread": _spread(), "colocate": _colocate(), "default": default, "baseline": baseline}
    )
    data = cs.collect([("s01", summary)], "productcatalogservice")
    assert data["cells"][("spread", "s01")] == [80, 85]
    assert ("baseline", "s01") not in data["cells"]
    assert len(data["flush_pairs"]) == 1
    run, sp, co = data["flush_pairs"][0]
    assert run == "s01"
    assert sp == 40.0 and co == 0.0
    # spread (1.0,1.0,40) + colocate (0,0,0) have frac; default has none.
    assert len(data["frac_flush"]) == 2


def test_collect_skips_non_churn_fault():
    summary = {"faults": {"node-cpu-hog": {"strategies": {"spread": _spread()}}}}
    data = cs.collect([("s01", summary)], "productcatalogservice")
    assert data["cells"] == {}


# ── report ──────────────────────────────────────────────────────────────────────


def test_report_empty(capsys):
    cs.report([], "productcatalogservice")
    out = capsys.readouterr().out
    assert "0 session" in out
    assert "n/a" in out  # ICC + H7 both n/a
    assert "spread > colocate in 0/0" in out


def test_report_two_sessions(capsys):
    data = cs.report([_session("s01"), _session("s02")], "productcatalogservice")
    out = capsys.readouterr().out
    assert "2 session(s)" in out
    assert "ICC =" in out
    assert "run-to-run visible" in out
    assert "spread > colocate in 2/2" in out
    assert "more clean session" in out  # n<6
    assert data["flush_pairs"][0][1] > data["flush_pairs"][0][2]


def test_report_single_session_note(capsys):
    cs.report([_session("s01")], "productcatalogservice")
    out = capsys.readouterr().out
    assert "run-to-run variance still 0" in out  # n<2 note


def test_report_h7_spearman_with_three_cells(capsys):
    # 3 strategies in one session -> 3 frac/flush cells -> Spearman printed.
    third = _strat([70, 72], 1000, 800, {"frontend-a-b": "n1", "productcatalogservice-c-d": "n2"})
    summary = _summary({"spread": _spread(), "colocate": _colocate(), "best-fit": third})
    cs.report([("s01", summary)], "productcatalogservice")
    out = capsys.readouterr().out
    assert "Spearman(global frac, flush)" in out
    assert "Spearman(target-scoped frac, flush)" in out


def test_report_sign_test_significant_at_six(capsys):
    sessions = [_session(f"s{i:02d}") for i in range(1, 7)]  # 6 sessions, spread wins all
    cs.report(sessions, "productcatalogservice")
    out = capsys.readouterr().out
    assert "spread > colocate in 6/6" in out
    assert "significant" in out
    assert "more clean session" not in out  # n>=6


# ── _fmt ────────────────────────────────────────────────────────────────────────


def test_fmt_handles_none_and_nan():
    assert cs._fmt(None) == "n/a"
    assert cs._fmt(math.nan) == "n/a"
    assert cs._fmt(0.789) == "0.79"


def test_flush_pct_none_without_conntrack():
    assert cs._flush_pct({"metrics": {}}) is None
