"""Tests for scripts/d3_slope_bands.py (D3 UDP-slope band re-derivation)."""

import glob
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


aa = _load("m2_aa_analysis")
d3 = _load("d3_slope_bands")


# ──────────────────────────────────────────────────────────────────────
# Synthetic A/A fixtures (a session per chosen slope value)
# ──────────────────────────────────────────────────────────────────────


def _slope_samples(slope_epm, base=1000.0):
    """Two pre-chaos UDP samples 60 s apart -> udp_pre_slope == slope_epm."""
    return [
        {"node": "w1", "proto": "udp", "count": base, "phase": "pre-chaos", "ts": _T0},
        {"node": "w1", "proto": "udp", "count": base + slope_epm, "phase": "pre-chaos", "ts": _T60},
    ]


_T0 = "2026-01-01T00:00:00+00:00"
_T60 = "2026-01-01T00:01:00+00:00"


def _write_session(
    results_dir, name, level_slopes, *, with_session=True, rejected=(), missing_raw=()
):
    """One session dir: summary.json (perLevel) + raw <level>.json per level.

    ``level_slopes`` maps condition -> list of per-iteration slopes (None = an
    iteration with a single pre-chaos sample, so its slope is None).
    Conditions in ``rejected`` get ``accepted: False`` in the summary;
    conditions in ``missing_raw`` are declared (accepted) but get no raw file.
    """
    run = Path(results_dir) / name
    run.mkdir(parents=True)
    per_level = [{"condition": cond, "accepted": cond not in rejected} for cond in level_slopes]
    summary = {"runId": name}
    if with_session:
        summary["session"] = {"perLevel": per_level}
    (run / "summary.json").write_text(json.dumps(summary))
    for cond, slopes in level_slopes.items():
        if cond in missing_raw:  # declared/accepted but no raw file on disk
            continue
        iterations = []
        for i, s in enumerate(slopes, start=1):
            if s is None:  # single sample -> no slope
                samples = [
                    {"node": "w1", "proto": "udp", "count": 1.0, "phase": "pre-chaos", "ts": _T0}
                ]
            else:
                samples = _slope_samples(s)
            iterations.append(
                {
                    "iteration": i,
                    "verdict": "PASS",
                    "metrics": {"conntrackProtocolSamples": samples},
                }
            )
        (run / f"{cond}.json").write_text(
            json.dumps({"placement": {"assignments": {"svc-a": "w1"}}, "iterations": iterations})
        )


# ──────────────────────────────────────────────────────────────────────
# Unit tests
# ──────────────────────────────────────────────────────────────────────


def test_band_from_slopes_hand_computed():
    # mean 700, pop-SD sqrt(20000/3)=81.65, 3·SD=244.95 -> round(455.05, 944.95)
    assert d3.band_from_slopes([600.0, 700.0, 800.0]) == (455, 945)


def test_collect_slopes_pools_per_level_and_skips(tmp_path):
    # s1 also declares f-000 as accepted but writes no raw file for it ->
    # load_condition_outcomes returns None and the level is skipped.
    _write_session(tmp_path, "s1", {"f-050": [600.0], "f-000": [0.0]}, missing_raw=["f-000"])
    _write_session(tmp_path, "s2", {"f-050": [700.0, None]})  # None iter skipped
    _write_session(tmp_path, "s3", {"f-050": [800.0]})
    _write_session(tmp_path, "skip-me", {"f-050": [9999.0]})  # excluded by name
    _write_session(tmp_path, "non-session", {"f-050": [1234.0]}, with_session=False)  # no session
    by_level = d3.collect_slopes(str(tmp_path), exclude=["skip-me"])
    assert by_level["f-050"] == [600.0, 700.0, 800.0]
    assert by_level["f-000"] == []  # declared/accepted but raw file missing -> empty


def test_collect_slopes_drops_not_accepted_conditions(tmp_path):
    # A not-accepted condition (e.g. fraction_target_missed) is excluded from
    # the band, mirroring the canonical M2 path — even though its raw file
    # exists and carries an untainted slope.
    _write_session(tmp_path, "s1", {"f-050": [600.0]})
    _write_session(tmp_path, "s2", {"f-050": [9999.0]}, rejected=["f-050"])
    assert d3.collect_slopes(str(tmp_path))["f-050"] == [600.0]


def test_derive_bands_omits_levels_below_min_samples(tmp_path):
    _write_session(tmp_path, "s1", {"f-050": [600.0], "f-075": [-7000.0]})
    _write_session(tmp_path, "s2", {"f-050": [700.0]})
    _write_session(tmp_path, "s3", {"f-050": [800.0]})
    bands = d3.derive_bands(str(tmp_path))
    assert bands == {"f-050": (455, 945)}  # f-075 has 1 sample -> omitted


def test_main_check_pass(tmp_path, capsys, monkeypatch):
    _write_session(tmp_path, "s1", {"f-050": [600.0]})
    _write_session(tmp_path, "s2", {"f-050": [700.0]})
    _write_session(tmp_path, "s3", {"f-050": [800.0]})
    monkeypatch.setattr(d3, "LEVELS", ("f-050",))
    monkeypatch.setattr(d3, "D3_UDP_SLOPE_BANDS_EPM", {"f-050": (455, 945)})
    rc = d3.main(["--results-dir", str(tmp_path), "--check"])
    assert rc == 0
    assert "OK" in capsys.readouterr().out


def test_main_check_mismatch_and_insufficient_branch(tmp_path, capsys):
    # f-050 derives a band that won't match the real f-050; f-075 has
    # 1 sample -> the "insufficient samples" print branch; other levels empty.
    _write_session(tmp_path, "s1", {"f-050": [600.0], "f-075": [-7000.0]})
    _write_session(tmp_path, "s2", {"f-050": [700.0]})
    _write_session(tmp_path, "s3", {"f-050": [800.0]})
    rc = d3.main(["--results-dir", str(tmp_path)])  # no --check -> always 0
    assert rc == 0
    rc = d3.main(["--results-dir", str(tmp_path), "--check"])
    assert rc == 1
    out = capsys.readouterr()
    assert "insufficient samples" in out.out
    assert "MISMATCH" in out.err


# ──────────────────────────────────────────────────────────────────────
# Parity against the real M2 A/A block (skipped when the data isn't present)
# ──────────────────────────────────────────────────────────────────────

_REAL_AA = Path(__file__).resolve().parent.parent / "results" / "aa"


@pytest.mark.skipif(
    not (_REAL_AA.is_dir() and glob.glob(str(_REAL_AA / "*" / "summary.json"))),
    reason="raw M2 A/A block (results/aa) not present — DOI-deposited, gitignored",
)
def test_frozen_bands_match_real_aa_block():
    assert d3.derive_bands(str(_REAL_AA)) == dict(aa.D3_UDP_SLOPE_BANDS_EPM)
