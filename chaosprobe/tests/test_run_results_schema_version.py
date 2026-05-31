"""Regression test: ``write_run_results`` stamps ``schemaVersion`` into summary.json.

Every analysis command (``doctor``, ``stats``, ``compare``, ``report``) reads
``summary.json`` and keys off ``schemaVersion`` to detect schema drift.  The run
command built its aggregated summary without the field, so ``doctor`` warned
"schemaVersion missing" on every real run even though chaosprobe claims to write
"2.0.0".  This locks the field into the summary writer, consistent with the
single-run / comparison writers in ``chaosprobe.output``.
"""

import json

from chaosprobe.orchestrator import run_phases
from chaosprobe.output import SCHEMA_VERSION


class _FakeLitmus:
    """Stub so the ChaosCenter dashboard lookup never touches a cluster."""

    def is_chaoscenter_installed(self) -> bool:
        return False


def test_write_run_results_stamps_schema_version(tmp_path, monkeypatch):
    # Isolate side effects that would otherwise need the repo script / a cluster.
    monkeypatch.setattr(run_phases, "_regenerate_presentation", lambda: None)
    monkeypatch.setattr(run_phases, "LitmusSetup", _FakeLitmus)

    overall_results = {
        "runId": "run-test",
        "namespace": "demo",
        "iterations": 1,
        "strategies": {},
    }

    run_phases.write_run_results(
        overall_results,
        tmp_path,
        passed=0,
        failed=0,
        total=0,
        ts="20260531-000000",
        do_visualize=False,
        graph_store=None,
    )

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["schemaVersion"] == SCHEMA_VERSION
