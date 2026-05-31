"""Unit tests for the fault-matrix builder extracted from ``run``.

The primary-reuse, filename-stem labeling, and deploy=False-for-additional rules
were inline in the ~440-line ``run`` command.
"""

from chaosprobe.commands import run_cmd
from chaosprobe.commands.run_cmd import _build_fault_scenarios


def test_single_experiment_reuses_primary_without_reloading(monkeypatch):
    monkeypatch.setattr(run_cmd, "extract_experiment_types", lambda scn: scn.get("types", []))
    loads = []
    monkeypatch.setattr(
        run_cmd,
        "_load_and_prepare_scenario",
        lambda *a, **k: loads.append(a) or ({}, "ns", "f", {}),
    )
    shared = {"types": ["pod-delete"]}

    fs = _build_fault_scenarios(
        ("/x/placement-experiment.yaml",), "/x/placement-experiment.yaml", shared, "demo"
    )

    assert fs == [("placement-experiment", shared, ["pod-delete"])]
    assert loads == []  # primary reused; no extra scenario load


def test_additional_experiments_loaded_without_redeploy(monkeypatch):
    monkeypatch.setattr(run_cmd, "extract_experiment_types", lambda scn: scn.get("types", []))
    add_scn = {"types": ["pod-cpu-hog"]}

    def fake_load(path, ns, deploy=True):
        assert deploy is False  # additional scenarios must not redeploy
        return add_scn, ns, "f2", {}

    monkeypatch.setattr(run_cmd, "_load_and_prepare_scenario", fake_load)
    shared = {"types": ["pod-delete"]}

    fs = _build_fault_scenarios(
        ("/x/placement-experiment.yaml", "/x/cpuhog.yaml"),
        "/x/placement-experiment.yaml",
        shared,
        "demo",
    )

    assert fs == [
        ("placement-experiment", shared, ["pod-delete"]),
        ("cpuhog", add_scn, ["pod-cpu-hog"]),
    ]
