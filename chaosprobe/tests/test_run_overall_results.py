"""Unit test for the overall-results initializer extracted from ``run``.

The multi-fault matrix keying (`faults[label]`) and the parallel flat
`strategies`/`faultExperiments` views were built inline in the ~440-line ``run``
command.
"""

from unittest.mock import MagicMock

from chaosprobe.commands import run_cmd
from chaosprobe.commands.run_cmd import _collect_scenario_hashes, _init_overall_results


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
    assert r["scenarioHashes"] == []  # empty scenario dicts → nothing to hash
    assert r["runId"].startswith("run-")
    assert isinstance(r["timestamp"], str)


def test_collect_scenario_hashes_dedupes_shared_files(tmp_path):
    # Two faults in one scenario dir share the deploy manifest but each has
    # its own experiment file; the shared manifest is hashed once.
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    manifest = deploy / "app.yaml"
    manifest.write_bytes(b"kind: Deployment\n")
    pod_delete = tmp_path / "pod-delete.yaml"
    pod_delete.write_bytes(b"kind: ChaosEngine\n# delete\n")
    cpu_hog = tmp_path / "cpu-hog.yaml"
    cpu_hog.write_bytes(b"kind: ChaosEngine\n# hog\n")

    def _scn(experiment):
        return {
            "path": str(tmp_path),
            "manifests": [{"file": str(manifest)}],
            "experiments": [{"file": str(experiment)}],
        }

    fault_scenarios = [
        ("pod-delete", _scn(pod_delete), ["pod-delete"]),
        ("cpu-hog", _scn(cpu_hog), ["pod-cpu-hog"]),
    ]
    result = _collect_scenario_hashes(fault_scenarios)
    files = [e["file"] for e in result]
    assert files == ["cpu-hog.yaml", "deploy/app.yaml", "pod-delete.yaml"]
