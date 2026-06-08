"""Tests for the bootstrap-ICC wiring added to scripts/score_variance.py."""

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))  # score_variance imports the sibling fault_taxonomy
_SCRIPT = _SCRIPTS / "score_variance.py"
_spec = importlib.util.spec_from_file_location("score_variance", _SCRIPT)
assert _spec is not None and _spec.loader is not None
sv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sv)


def test_format_icc_ci_present():
    line = sv._format_icc_ci({"ci_low": 0.1, "ci_high": 0.4, "n_resamples": 2000})
    assert "ICC 95% CI = [0.1, 0.4]" in line
    assert "2000 resamples" in line


def test_format_icc_ci_absent():
    line = sv._format_icc_ci({"ci_low": None, "ci_high": None, "n_resamples": 0})
    assert line == "  ICC 95% CI = n/a (insufficient runs to bootstrap)"


def test_report_prints_ci_line(capsys):
    cells = {
        ("colocate", "r1"): [60, 70],
        ("colocate", "r2"): [62, 68],
        ("spread", "r1"): [71, 69],
        ("spread", "r2"): [73, 67],
    }
    sv.report(cells, 3)
    out = capsys.readouterr().out
    assert "ICC_strategy" in out
    assert "ICC 95% CI = [" in out
