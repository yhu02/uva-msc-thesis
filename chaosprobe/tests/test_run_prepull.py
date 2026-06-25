"""Unit tests for the probe-image pre-pull helper extracted from ``run``.

The worker-node selection (schedulable, non-control-plane) and the
no-images / no-workers no-ops were inline in the ~440-line ``run`` command.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from chaosprobe.commands import run_cmd


def _nodes():
    return [
        SimpleNamespace(name="worker1", is_schedulable=True, is_control_plane=False),
        SimpleNamespace(name="worker2", is_schedulable=True, is_control_plane=False),
        SimpleNamespace(name="cp1", is_schedulable=True, is_control_plane=True),
        SimpleNamespace(name="cordoned", is_schedulable=False, is_control_plane=False),
    ]


def test_pulls_union_onto_schedulable_workers_only(monkeypatch):
    monkeypatch.setattr(run_cmd, "_unique_probe_images", lambda fs: ["img1", "img2"])
    calls = {}

    def fake_prepull(ns, images, workers):
        calls.update(ns=ns, images=images, workers=workers)
        return len(images) * len(workers)

    monkeypatch.setattr(run_cmd, "prepull_probe_images", fake_prepull)
    mutator = MagicMock()
    mutator.get_nodes.return_value = _nodes()

    run_cmd._prepull_probe_images_onto_workers(mutator, "demo", [])

    assert calls["ns"] == "demo"
    assert calls["images"] == ["img1", "img2"]
    # Control-plane and unschedulable nodes are excluded.
    assert calls["workers"] == ["worker1", "worker2"]


def test_noop_when_no_images(monkeypatch):
    monkeypatch.setattr(run_cmd, "_unique_probe_images", lambda fs: [])
    called = []
    monkeypatch.setattr(run_cmd, "prepull_probe_images", lambda *a: called.append(1))
    mutator = MagicMock()
    mutator.get_nodes.return_value = _nodes()

    run_cmd._prepull_probe_images_onto_workers(mutator, "demo", [])
    assert called == []


def test_noop_when_no_workers(monkeypatch):
    monkeypatch.setattr(run_cmd, "_unique_probe_images", lambda fs: ["img1"])
    called = []
    monkeypatch.setattr(run_cmd, "prepull_probe_images", lambda *a: called.append(1))
    mutator = MagicMock()
    mutator.get_nodes.return_value = [
        SimpleNamespace(name="cp1", is_schedulable=True, is_control_plane=True),
    ]

    run_cmd._prepull_probe_images_onto_workers(mutator, "demo", [])
    assert called == []
