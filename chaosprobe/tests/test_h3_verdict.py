"""Tests for the TOST-backed verdict wiring in scripts/h3_mechanism_outcome.py."""

import importlib.util
import math
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))  # h3 imports the sibling fault_taxonomy
_SCRIPT = _SCRIPTS / "h3_mechanism_outcome.py"
_spec = importlib.util.spec_from_file_location("h3_mechanism_outcome", _SCRIPT)
assert _spec is not None and _spec.loader is not None
h3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h3)


def test_verdict_supported():
    # Strong on dependent, weak on control -> H3 supported.
    assert h3._verdict(0.8, 0.001, 0.1, 0.5, 20) == "H3 supported"


def test_verdict_decoupled_via_tost():
    # Near-zero dependent correlation at large n is statistically inside +/-0.3.
    assert h3._verdict(0.0, 0.9, 0.0, 0.9, 100) == "decoupled (TOST)"


def test_verdict_confound_both():
    # Not significant on dependent, but control lights up -> run-level confound.
    assert h3._verdict(0.4, 0.20, 0.5, 0.01, 8) == "confound? (both)"


def test_verdict_no_link():
    assert h3._verdict(0.1, 0.5, 0.1, 0.5, 8) == "no link"


def test_verdict_nan_rho():
    assert h3._verdict(math.nan, math.nan, math.nan, math.nan, 3) == "no link"


def test_report_runs_over_rows(capsys):
    rows = [
        {
            "conntrack_flush_pct": float(i),
            "coredns_p99_during": float(i),
            "coredns_p99_delta": float(i),
            "tcp_retx_during": float(i),
            "tcp_retx_delta": float(i),
            "dep_p95": float(i % 3),
            "dep_max": float(i % 4),
            "ctrl_p95": float((i + 1) % 3),
            "ctrl_max": float((i + 2) % 4),
        }
        for i in range(8)
    ]
    h3.report(rows)
    out = capsys.readouterr().out
    assert "H3: mechanism" in out
