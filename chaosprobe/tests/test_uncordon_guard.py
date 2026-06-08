"""Tests for the node-drain uncordon guard in run_phases."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from chaosprobe.orchestrator.run_phases import (
    _orphaned_cordoned_workers,
    _uncordon_orphaned_nodes,
)


def _node(name, unschedulable=False, labels=None):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, labels=labels or {}),
        spec=SimpleNamespace(unschedulable=unschedulable),
    )


# ── _orphaned_cordoned_workers (pure selection) ───────────────────────────────


def test_cordoned_worker_selected():
    nodes = [_node("worker1", unschedulable=True)]
    assert _orphaned_cordoned_workers(nodes) == ["worker1"]


def test_schedulable_worker_ignored():
    nodes = [_node("worker1", unschedulable=False), _node("worker2", unschedulable=None)]
    assert _orphaned_cordoned_workers(nodes) == []


def test_cordoned_control_plane_excluded():
    nodes = [
        _node("cp1", unschedulable=True, labels={"node-role.kubernetes.io/control-plane": ""}),
        _node("cp-old", unschedulable=True, labels={"node-role.kubernetes.io/master": ""}),
    ]
    assert _orphaned_cordoned_workers(nodes) == []


def test_mixed_returns_only_cordoned_workers():
    nodes = [
        _node("worker1", unschedulable=True),
        _node("worker2", unschedulable=False),
        _node("cp1", unschedulable=True, labels={"node-role.kubernetes.io/control-plane": ""}),
        _node("worker3", unschedulable=True),
    ]
    assert _orphaned_cordoned_workers(nodes) == ["worker1", "worker3"]


def test_node_without_name_skipped():
    nodes = [_node(None, unschedulable=True)]
    assert _orphaned_cordoned_workers(nodes) == []


def test_node_without_spec_skipped():
    nodes = [SimpleNamespace(metadata=SimpleNamespace(name="x", labels={}), spec=None)]
    assert _orphaned_cordoned_workers(nodes) == []


def test_null_labels_treated_as_worker():
    nodes = [_node("worker1", unschedulable=True, labels=None)]
    assert _orphaned_cordoned_workers(nodes) == ["worker1"]


# ── _uncordon_orphaned_nodes (action, mocked API) ─────────────────────────────


def test_uncordon_patches_only_cordoned_workers():
    core = MagicMock()
    core.list_node.return_value = SimpleNamespace(
        items=[
            _node("worker1", unschedulable=True),
            _node("worker2", unschedulable=False),
            _node("cp1", unschedulable=True, labels={"node-role.kubernetes.io/control-plane": ""}),
        ]
    )
    with patch("kubernetes.client.CoreV1Api", return_value=core):
        _uncordon_orphaned_nodes()
    core.patch_node.assert_called_once_with("worker1", {"spec": {"unschedulable": False}})


def test_uncordon_handles_list_node_failure():
    core = MagicMock()
    core.list_node.side_effect = RuntimeError("api down")
    with patch("kubernetes.client.CoreV1Api", return_value=core):
        _uncordon_orphaned_nodes()  # must not raise
    core.patch_node.assert_not_called()


def test_uncordon_swallows_patch_failure():
    core = MagicMock()
    core.list_node.return_value = SimpleNamespace(items=[_node("worker1", unschedulable=True)])
    core.patch_node.side_effect = RuntimeError("conflict")
    with patch("kubernetes.client.CoreV1Api", return_value=core):
        _uncordon_orphaned_nodes()  # best-effort: must not raise
    core.patch_node.assert_called_once()
