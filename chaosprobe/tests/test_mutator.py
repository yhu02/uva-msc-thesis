"""Tests for PlacementMutator kubernetes helpers."""

from unittest.mock import MagicMock

from chaosprobe.placement.mutator import PlacementMutator


def _mutator():
    m = PlacementMutator.__new__(PlacementMutator)
    m.namespace = "test-ns"
    m.core_api = MagicMock()
    return m


class TestGetPodNode:
    def test_returns_node_of_scheduled_pod(self):
        m = _mutator()
        pod = MagicMock()
        pod.spec.node_name = "node-a"
        m.core_api.list_namespaced_pod.return_value = MagicMock(items=[pod])
        assert m._get_pod_node("frontend") == "node-a"

    def test_returns_none_when_unscheduled(self):
        m = _mutator()
        pod = MagicMock()
        pod.spec.node_name = None
        m.core_api.list_namespaced_pod.return_value = MagicMock(items=[pod])
        assert m._get_pod_node("frontend") is None
