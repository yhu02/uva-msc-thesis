"""Tests for chaosprobe.metrics.base pod helpers."""

from unittest.mock import MagicMock, patch

from chaosprobe.metrics.base import exec_in_pod, find_ready_pod


def _ready_pod(name):
    cond = MagicMock()
    cond.type = "Ready"
    cond.status = "True"
    pod = MagicMock()
    pod.status.conditions = [cond]
    pod.metadata.name = name
    return pod


class TestFindReadyPod:
    def test_returns_name_of_ready_pod(self):
        core_api = MagicMock()
        core_api.list_namespaced_pod.return_value = MagicMock(items=[_ready_pod("frontend-abc")])
        assert find_ready_pod(core_api, "default", "frontend") == "frontend-abc"

    def test_returns_none_when_no_ready_pod(self):
        cond = MagicMock()
        cond.type = "Ready"
        cond.status = "False"
        not_ready = MagicMock()
        not_ready.status.conditions = [cond]
        core_api = MagicMock()
        core_api.list_namespaced_pod.return_value = MagicMock(items=[not_ready])
        assert find_ready_pod(core_api, "default", "frontend") is None

    def test_returns_none_on_api_error(self):
        core_api = MagicMock()
        core_api.list_namespaced_pod.side_effect = RuntimeError("boom")
        assert find_ready_pod(core_api, "default", "frontend") is None


class TestExecInPod:
    def test_returns_stdout(self):
        core_api = MagicMock()
        with patch("kubernetes.stream.stream", return_value="hello\n"):
            out = exec_in_pod(core_api, "default", "pod-1", ["echo", "hello"])
        assert out == "hello\n"

    def test_wraps_exception(self):
        core_api = MagicMock()
        with patch("kubernetes.stream.stream", side_effect=RuntimeError("boom")):
            out = exec_in_pod(core_api, "default", "pod-1", ["false"])
        assert out.startswith("ERROR:")
