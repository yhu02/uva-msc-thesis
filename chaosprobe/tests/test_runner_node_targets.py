"""Unit tests for node-fault TARGET_NODES resolution in chaos.runner.

node-cpu-hog / node-memory-hog are node-scoped: ChaosProbe resolves *which* node
to fault from ``appinfo.applabel`` (the service whose host node to hit) so the
fault follows the active placement strategy. These cover the resolution and
env-injection branches without a live cluster.
"""

from types import SimpleNamespace

import pytest
from kubernetes import client as k8s_client

from chaosprobe.chaos.runner import ChaosRunner

_CC = {"token": "t", "project_id": "p", "infra_id": "i", "gql_url": "http://x/query"}


def _runner():
    return ChaosRunner(namespace="online-boutique", chaoscenter=_CC)


def _engine(exp_name, env=None, applabel="app=productcatalogservice"):
    return {
        "spec": {
            "appinfo": {"applabel": applabel} if applabel is not None else {},
            "experiments": [
                {"name": exp_name, "spec": {"components": {"env": list(env or [])}}}
            ],
        }
    }


def _target_nodes(engine):
    env = engine["spec"]["experiments"][0]["spec"]["components"]["env"]
    return next((e["value"] for e in env if e["name"] == "TARGET_NODES"), None)


class TestResolveNodeTargets:
    def test_pod_fault_is_untouched(self, monkeypatch):
        runner = _runner()
        monkeypatch.setattr(runner, "_resolve_target_node", lambda al: "worker9")
        engine = _engine("pod-cpu-hog", env=[])
        runner._resolve_node_targets(engine)
        assert _target_nodes(engine) is None  # pod faults never get TARGET_NODES

    def test_auto_value_is_resolved(self, monkeypatch):
        runner = _runner()
        monkeypatch.setattr(runner, "_resolve_target_node", lambda al: "worker4")
        engine = _engine("node-cpu-hog", env=[{"name": "TARGET_NODES", "value": "auto"}])
        runner._resolve_node_targets(engine)
        assert _target_nodes(engine) == "worker4"

    def test_missing_env_entry_is_appended(self, monkeypatch):
        runner = _runner()
        monkeypatch.setattr(runner, "_resolve_target_node", lambda al: "worker2")
        engine = _engine("node-cpu-hog", env=[])
        runner._resolve_node_targets(engine)
        assert _target_nodes(engine) == "worker2"

    def test_explicit_value_is_respected(self, monkeypatch):
        runner = _runner()
        monkeypatch.setattr(
            runner, "_resolve_target_node",
            lambda al: pytest.fail("must not resolve when an explicit node is set"),
        )
        engine = _engine("node-cpu-hog", env=[{"name": "TARGET_NODES", "value": "worker1"}])
        runner._resolve_node_targets(engine)
        assert _target_nodes(engine) == "worker1"

    def test_missing_applabel_raises(self):
        engine = _engine(
            "node-cpu-hog", env=[{"name": "TARGET_NODES", "value": "auto"}], applabel=""
        )
        with pytest.raises(RuntimeError, match="appinfo.applabel"):
            _runner()._resolve_node_targets(engine)


def _pod(name, node, phase):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        spec=SimpleNamespace(node_name=node),
        status=SimpleNamespace(phase=phase),
    )


class _FakeCoreV1:
    def __init__(self, pods):
        self._pods = pods

    def list_namespaced_pod(self, ns, label_selector=None):
        return SimpleNamespace(items=self._pods)


def _patch_k8s(monkeypatch, pods):
    monkeypatch.setattr("chaosprobe.k8s.ensure_k8s_config", lambda: None)
    monkeypatch.setattr(k8s_client, "CoreV1Api", lambda: _FakeCoreV1(pods))


class TestResolveTargetNode:
    def test_prefers_running_pod(self, monkeypatch):
        _patch_k8s(monkeypatch, [_pod("a", "w1", "Pending"), _pod("b", "w2", "Running")])
        assert _runner()._resolve_target_node("app=x") == "w2"

    def test_falls_back_to_any_scheduled_pod(self, monkeypatch):
        _patch_k8s(monkeypatch, [_pod("a", "w3", "Pending")])
        assert _runner()._resolve_target_node("app=x") == "w3"

    def test_raises_when_no_pod_is_scheduled(self, monkeypatch):
        _patch_k8s(monkeypatch, [_pod("a", None, "Pending")])
        with pytest.raises(RuntimeError, match="target resolution failed"):
            _runner()._resolve_target_node("app=x")

    def test_raises_when_no_pods_match(self, monkeypatch):
        _patch_k8s(monkeypatch, [])
        with pytest.raises(RuntimeError, match="target resolution failed"):
            _runner()._resolve_target_node("app=x")
