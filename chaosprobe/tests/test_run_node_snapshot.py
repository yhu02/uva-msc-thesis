"""Unit test for the best-fit node-usage snapshot helper extracted from ``run``.

The snapshot was inline in the ~440-line ``run`` command, so its
exclude-app-pods logic (critical to best-fit reproducibility) had no unit
coverage. Extracting it lets a mocked mutator exercise it directly.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from chaosprobe.commands.run_cmd import _snapshot_node_usage_for_bestfit


def test_snapshot_excludes_app_pods_and_records_usage():
    mutator = MagicMock()
    mutator.get_deployments.return_value = [
        SimpleNamespace(name="frontend", replicas=1),
        SimpleNamespace(name="scaled-to-zero", replicas=0),  # excluded by replicas > 0
    ]
    mutator.observe_pod_placements.return_value = {"frontend-abc": "node1", "frontend-def": "node2"}
    snapshot = {"node1": (500, 104857600), "node2": (300, 52428800)}
    mutator.get_node_pod_usage.return_value = snapshot

    result = _snapshot_node_usage_for_bestfit(mutator, "demo")

    # Returns the snapshot and stashes it on the mutator for best-fit reuse.
    assert result == snapshot
    assert mutator.usage_snapshot == snapshot
    # Only replicas>0 deployments are observed.
    mutator.observe_pod_placements.assert_called_once_with(["frontend"])
    # The app's own pods are excluded from the baseline-usage view.
    mutator.get_node_pod_usage.assert_called_once_with(
        exclude_pods={("demo", "frontend-abc"), ("demo", "frontend-def")}
    )
