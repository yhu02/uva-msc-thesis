"""Tests for the paired Wilcoxon/sign-test wiring in scripts/mechanism_metrics.py."""

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))  # mechanism_metrics imports the sibling fault_taxonomy
_SCRIPT = _SCRIPTS / "mechanism_metrics.py"
_spec = importlib.util.spec_from_file_location("mechanism_metrics", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mm)


def _data(m1_runs):
    return {
        "flush": {"spread": [30.0, 32.0], "colocate": [2.0, 3.0]},
        "throttle": {"colocate": [1.5], "default": [1.9], "spread": [1.94]},
        "m1_runs": m1_runs,
        "m2_runs": [("r1", {"colocate": 1.5, "default": 1.9, "spread": 1.94})],
    }


def test_report_prints_paired_test(capsys):
    data = _data([("r1", 30.0, 2.0), ("r2", 32.0, 3.0), ("r3", 28.0, 1.0)])
    mm.report(data)
    out = capsys.readouterr().out
    assert "M1 paired test" in out
    assert "sign test 3/3" in out


def test_report_no_paired_line_without_runs(capsys):
    mm.report(_data([]))
    out = capsys.readouterr().out
    assert "M1 paired test" not in out
