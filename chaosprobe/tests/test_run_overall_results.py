"""Unit test for the overall-results initializer extracted from ``run``.

The multi-fault matrix keying (`faults[label]`) and the parallel flat
`strategies`/`faultExperiments` views were built inline in the ~440-line ``run``
command.
"""

from unittest.mock import MagicMock

from chaosprobe.commands import run_cmd
from chaosprobe.commands.run_cmd import _init_overall_results


def test_builds_matrix_and_flat_views(monkeypatch):
    monkeypatch.setattr(run_cmd, "gather_run_metadata", lambda core_api=None: {"git": "abc"})
    fault_scenarios = [
        ("placement-experiment", {}, ["pod-delete"]),
        ("cpuhog", {}, ["pod-cpu-hog"]),
    ]

    r = _init_overall_results(fault_scenarios, "demo", 3, MagicMock())

    assert r["namespace"] == "demo"
    assert r["iterations"] == 3
    assert r["runMetadata"] == {"git": "abc"}
    assert r["faults"] == {
        "placement-experiment": {"strategies": {}},
        "cpuhog": {"strategies": {}},
    }
    assert r["faultExperiments"] == ["placement-experiment", "cpuhog"]
    assert r["strategies"] == {}
    assert r["runId"].startswith("run-")
    assert isinstance(r["timestamp"], str)
