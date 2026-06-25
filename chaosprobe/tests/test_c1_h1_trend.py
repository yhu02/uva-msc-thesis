"""Tests for scripts/c1_h1_trend.py (H1 Page's L dose-response analysis)."""

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
h1 = _load("c1_h1_trend")

_T0 = "2026-01-01T00:00:00+00:00"
_T60 = "2026-01-01T00:01:00+00:00"
_ALL = ("f-000", "f-025", "f-050", "f-075", "f-100")


def _iter(n, ew_pre, slope_epm=None):
    """Raw iteration with an east-west pre-chaos p95 and optional UDP slope."""
    metrics = {
        "latency": {
            "phases": {"pre-chaos": {"routes": {"a->b": {"p95_ms": ew_pre}, "/": {"p95_ms": 9.0}}}}
        }
    }
    if slope_epm is not None:
        metrics["conntrackProtocolSamples"] = [
            {"node": "w1", "proto": "udp", "count": 1000.0, "phase": "pre-chaos", "ts": _T0},
            {
                "node": "w1",
                "proto": "udp",
                "count": 1000.0 + slope_epm,
                "phase": "pre-chaos",
                "ts": _T60,
            },
        ]
    return {"iteration": n, "verdict": "PASS", "metrics": metrics}


def _write_session(results_dir, name, level_iters, *, rejected=()):
    """Write a placement session: summary.json (perLevel) + raw <cond>.json per level.

    ``level_iters`` maps condition -> list of raw iteration dicts (from _iter).
    """
    run = Path(results_dir) / name
    run.mkdir(parents=True)
    per_level = [
        {
            "condition": cond,
            "targetF": f,
            "liveAchievedF": f,
            "accepted": cond not in rejected,
            "rejectionReasons": ["fraction_target_missed"] if cond in rejected else [],
        }
        for cond, f in zip(_ALL, (0.0, 0.25, 0.5, 0.75, 1.0))
        if cond in level_iters
    ]
    summary = {
        "runId": name,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "session": {
            "solverSeed": 0,
            "replicas": 1,
            "mode": "packed",
            "levels": [0.0, 0.25, 0.5, 0.75, 1.0],
            "workers": ["w1"],
            "perLevel": per_level,
        },
        "faults": {"pod-delete": {"strategies": {}}},
    }
    (run / "summary.json").write_text(json.dumps(summary))
    for cond, iterations in level_iters.items():
        (run / f"{cond}.json").write_text(
            json.dumps({"placement": {"assignments": {"svc-a": "w1"}}, "iterations": iterations})
        )


def _increasing_session(results_dir, name, base):
    """A complete block whose east-west p95 rises across the five f-levels."""
    _write_session(
        results_dir,
        name,
        {cond: [_iter(1, base + 2 * i)] for i, cond in enumerate(_ALL)},
    )


def test_complete_increasing_blocks_significant_trend(tmp_path):
    for k, name in enumerate(("s1", "s2", "s3")):
        _increasing_session(tmp_path, name, base=40 + k)
    out = h1.analyze(str(tmp_path))
    assert out["nCompleteBlocks"] == 3
    page = out["pageTrendTest"]
    assert page["k"] == 5 and page["n_blocks"] == 3
    assert page["p_one_sided"] < 0.01  # monotone increase -> significant
    # f0 grand median 41 (40,41,42), f1 = 49 -> +19.5% >= 15% SESOI.
    assert out["sesoi"]["meetsSesoi"] is True


def test_incomplete_block_excluded_with_warning(tmp_path):
    _increasing_session(tmp_path, "s1", base=40)
    _increasing_session(tmp_path, "s2", base=41)
    # s3 has f-100 rejected -> incomplete -> excluded from Page's L.
    _write_session(
        tmp_path,
        "s3",
        {cond: [_iter(1, 40 + 2 * i)] for i, cond in enumerate(_ALL)},
        rejected=["f-100"],
    )
    out = h1.analyze(str(tmp_path))
    assert out["nCompleteBlocks"] == 2
    assert any("incomplete H1 block" in w and "f-100" in w for w in out["warnings"])


def test_d3_slope_band_taint_optional_default_off(tmp_path):
    # f-025 band is [-358, 1022]: it1 slope 200 (in-band, ew 42), it2 slope 5000
    # (out of band, ew 9999). f-025 is index 1.
    level_iters = {cond: [_iter(1, 40 + 2 * i)] for i, cond in enumerate(_ALL)}
    level_iters["f-025"] = [_iter(1, 42, slope_epm=200), _iter(2, 9999, slope_epm=5000)]
    _write_session(tmp_path, "s1", level_iters)
    # Default (D-2026-06-14-02): slope-taint OFF -> both iters kept -> f-025
    # value is median(42, 9999) = 5020.5.
    blocks_off, _ = h1.collect_blocks(str(tmp_path))
    assert blocks_off == [[40.0, 5020.5, 44.0, 46.0, 48.0]]
    # Opt-in (--slope-band-taint): the out-of-band 9999 iteration is dropped ->
    # f-025 value is 42.
    blocks_on, _ = h1.collect_blocks(str(tmp_path), slope_band_taint=True)
    assert blocks_on == [[40.0, 42.0, 44.0, 46.0, 48.0]]


def test_sesoi_effect_below_and_undefined():
    # blocks are complete 5-value rows in f-ascending order.
    below = h1.sesoi_effect([[40.0, 41.0, 42.0, 43.0, 44.0]])
    assert below["pctChange"] == 10.0 and below["meetsSesoi"] is False
    # f0 <= 0 -> percent change undefined.
    undefined = h1.sesoi_effect([[0.0, 1.0, 2.0, 3.0, 4.0]])
    assert undefined["pctChange"] is None and undefined["meetsSesoi"] is False
    # no blocks -> grand medians None, still no crash.
    empty = h1.sesoi_effect([])
    assert empty["f0"] is None and empty["pctChange"] is None


def test_main_smoke_and_json_output(tmp_path, capsys):
    for k, name in enumerate(("s1", "s2")):
        _increasing_session(tmp_path, name, base=40 + k)
    # An incomplete session so main's report prints the warning line too.
    _write_session(
        tmp_path,
        "s3",
        {cond: [_iter(1, 40 + 2 * i)] for i, cond in enumerate(_ALL)},
        rejected=["f-100"],
    )
    out_json = tmp_path / "h1.json"
    rc = h1.main(["--results-dir", str(tmp_path), "--json", str(out_json)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "Page's L" in printed and "SESOI effect" in printed
    assert "incomplete H1 block" in printed  # the warning-print path
    written = json.loads(out_json.read_text())
    assert written["outcome"] == "ew_p95_pre_ms" and written["nCompleteBlocks"] == 2
    assert written["slopeBandTaint"] is False  # default off (D-2026-06-14-02)
    assert "OFF (D-2026-06-14-02)" in printed


def test_main_slope_band_taint_sensitivity_mode(tmp_path, capsys):
    for k, name in enumerate(("s1", "s2")):
        _increasing_session(tmp_path, name, base=40 + k)
    out_json = tmp_path / "h1_sens.json"
    rc = h1.main(["--results-dir", str(tmp_path), "--slope-band-taint", "--json", str(out_json)])
    assert rc == 0
    assert "ON (D3 sensitivity)" in capsys.readouterr().out
    assert json.loads(out_json.read_text())["slopeBandTaint"] is True
