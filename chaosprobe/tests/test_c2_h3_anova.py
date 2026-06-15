"""Tests for scripts/c2_h3_anova.py (V2-H3 replication-rescue analysis)."""

import importlib.util
import json
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
c2 = _load("c2_h3_anova")

_T0 = "2026-01-01T00:00:00+00:00"
_T15 = "2026-01-01T00:00:15+00:00"
_T30 = "2026-01-01T00:00:30+00:00"
_APP = ["frontend", "cartservice", "productcatalogservice", "checkoutservice"]


def _series(pre_ready, during_ready, services=_APP):
    """An EndpointSlice time series: every app service at pre_ready then during_ready."""
    return {
        "samples": [
            {
                "phase": "pre-chaos",
                "ts": _T0,
                "services": {s: {"ready": pre_ready} for s in services},
            },
            {
                "phase": "during-chaos",
                "ts": _T15,
                "services": {s: {"ready": during_ready} for s in services},
            },
            {
                "phase": "post-chaos",
                "ts": _T30,
                "services": {s: {"ready": pre_ready} for s in services},
            },
        ]
    }


def _latency(err_rate):
    # user route "/" with the given during-chaos error rate (errorCount/(err+samples)).
    e, s = int(round(err_rate * 100)), 100 - int(round(err_rate * 100))
    return {"phases": {"during-chaos": {"routes": {"/": {"errorCount": e, "sampleCount": s}}}}}


def _write_session(d, name, replicas, mode, pre, during, err_rate):
    run = Path(d) / name
    run.mkdir(parents=True)
    (run / "summary.json").write_text(
        json.dumps({"runId": name, "v2Session": {"replicas": replicas, "mode": mode}})
    )
    (run / "f-050.json").write_text(
        json.dumps(
            {
                "metrics": {
                    "endpointSliceTimeSeries": _series(pre, during),
                    "latency": _latency(err_rate),
                }
            }
        )
    )


# ── unit tests ─────────────────────────────────────────────────────────


def test_app_services_excludes_infra():
    ets = {
        "samples": [
            {
                "services": {
                    "frontend": {"ready": 1},
                    "frontend-external": {"ready": 1},
                    "chaos-exporter": {"ready": 1},
                    "workflow-controller-metrics": {"ready": 1},
                    "loadgenerator": {"ready": 0},
                    "cartservice": {"ready": 1},
                }
            }
        ]
    }
    assert c2.app_services_from_series(ets) == ["cartservice", "frontend"]


def test_trough_depth_fraction_math_and_edges():
    # baseline 4 services x 4 ready = 16; during 4 x 2 = 8 -> fraction 0.5.
    frac, base = c2.trough_depth_fraction(_series(4, 2), _APP)
    assert base == 16.0 and frac == 0.5
    # no pre-chaos sample -> (None, None)
    assert c2.trough_depth_fraction(
        {"samples": [{"phase": "during-chaos", "services": {}}]}, _APP
    ) == (
        None,
        None,
    )
    # baseline 0 -> (None, None)
    assert c2.trough_depth_fraction(_series(0, 0), _APP) == (None, None)


def test_two_sample_equiv_within_and_outside():
    within = c2._two_sample_equiv([0.5, 0.5, 0.5], [0.5, 0.5, 0.5], band=0.25)
    assert within["withinBand"] is True
    outside = c2._two_sample_equiv([0.9, 0.9, 0.9], [0.1, 0.1, 0.1], band=0.25)
    assert outside["withinBand"] is False
    assert c2._two_sample_equiv([], [1.0], band=0.25)["withinBand"] is False


def test_degenerate_warnings():
    S = c2.Session
    none_depth = [S("a", 1, "packed", None, None, None, 0.1)]
    assert any(
        "trough-depth" in w and "unavailable" in w for w in c2._degenerate_warnings(none_depth)
    )
    flat_err = [S(f"s{i}", 1, "packed", 0.1 * i, 4, 1.0, 1.0) for i in range(3)]
    assert any("error rate" in w and "no variance" in w for w in c2._degenerate_warnings(flat_err))


# ── integration: a clean rescue across the three cells ─────────────────


def _rescue_campaign(d):
    # integer ready counts; r1 baseline = 4 svc x 2 = 8 -> margin 1/8 = 0.125.
    for i in range(3):
        _write_session(d, f"r1-{i}", 1, "packed", 2, 1, 0.4)  # depth 0.5, err 0.4
        _write_session(d, f"pk-{i}", 3, "packed", 6, 3, 0.4)  # depth 0.5, err 0.4
        _write_session(d, f"an-{i}", 3, "anti-affine", 6, 5, 0.01)  # depth ~0.1667, err 0.01


def test_analyze_fractional_margin_and_rescue(tmp_path):
    _rescue_campaign(tmp_path)
    out = c2.analyze(str(tmp_path))
    assert out["nSessions"] == 9
    # depth margin = 1.0 pod / r1 app baseline (4 services x 2 = 8) = 0.125.
    assert out["depthMarginFraction"] == 0.125
    d = out["troughDepthFraction"]
    assert d["median"]["r1"] == 0.5 and d["median"]["r3_anti"] == round((24 - 20) / 24, 4)
    assert d["rescueMet"] is True  # 0.5 - 0.1667 = 0.333 >= 0.125
    assert d["tostPackedEqR1"]["withinBand"] is True  # packed 0.5 ~ r1 0.5
    e = out["userErrorRate"]
    assert e["median"]["r1"] == 0.4 and e["rescueMet"] is True  # 0.4 - 0.01 = 0.39 >= 0.302
    assert isinstance(out["conjunctionRescue"], bool)


def test_collect_skips_non_v2_and_missing_condition(tmp_path):
    (tmp_path / "nonv2").mkdir()
    (tmp_path / "nonv2" / "summary.json").write_text(json.dumps({"runId": "x"}))
    (tmp_path / "nocond").mkdir()
    (tmp_path / "nocond" / "summary.json").write_text(
        json.dumps({"v2Session": {"replicas": 1, "mode": "packed"}})
    )
    sessions, warnings = c2.collect_sessions(str(tmp_path))
    assert sessions == []
    assert any("not a v2" in w for w in warnings) and any(
        "no condition file" in w for w in warnings
    )


def test_main_smoke(tmp_path, capsys):
    _rescue_campaign(tmp_path)
    out_json = tmp_path / "h3.json"
    rc = c2.main(["--results-dir", str(tmp_path), "--json", str(out_json)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "V2-H3" in printed and "CONJUNCTION" in printed
    assert json.loads(out_json.read_text())["depthMarginFraction"] == 0.125


def test_art_rows_skips_non_numeric():
    # a session whose outcome is None is dropped from the ART rows.
    rows = c2._art_rows([c2.Session("a", 1, "packed", None, None, None, None)], "depth")
    assert rows == []


def test_main_on_empty_dir_prints_degenerate_warnings(tmp_path, capsys):
    rc = c2.main(["--results-dir", str(tmp_path)])
    assert rc == 0
    assert "DEGENERATE" in capsys.readouterr().out  # the warning-print path
