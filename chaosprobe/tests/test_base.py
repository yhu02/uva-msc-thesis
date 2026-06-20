"""Tests for chaosprobe.metrics.base pod helpers."""

from unittest.mock import MagicMock, patch

from chaosprobe.metrics.base import (
    _is_chaos_infra_pod,
    _pod_has_python3,
    _pod_has_wget,
    exec_in_pod,
    find_all_probe_pods,
    find_all_probe_pods_with_node,
    find_probe_pod,
    find_probe_pods_per_node,
    find_ready_pod,
    pod_has_shell,
)


def _ready_pod(name, labels=None, node="worker1"):
    cond = MagicMock()
    cond.type = "Ready"
    cond.status = "True"
    pod = MagicMock()
    pod.status.conditions = [cond]
    pod.metadata.name = name
    pod.metadata.labels = {} if labels is None else labels
    pod.spec.node_name = node
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


class TestPodHelperApiErrors:
    """Pod-discovery and capability helpers degrade to a safe empty result
    (and log at debug) when the kubernetes API raises, rather than
    propagating the error or swallowing it silently."""

    def _raising_api(self):
        core_api = MagicMock()
        core_api.list_namespaced_pod.side_effect = RuntimeError("boom")
        return core_api

    def test_find_probe_pod_returns_none_on_api_error(self):
        assert find_probe_pod(self._raising_api(), "ns") is None

    def test_find_probe_pods_per_node_returns_empty_on_api_error(self):
        assert find_probe_pods_per_node(self._raising_api(), "ns") == []

    def test_find_all_probe_pods_with_node_returns_empty_on_api_error(self):
        assert find_all_probe_pods_with_node(self._raising_api(), "ns") == []

    def test_find_all_probe_pods_returns_empty_on_api_error(self):
        assert find_all_probe_pods(self._raising_api(), "ns") == []

    def test_pod_has_shell_returns_false_on_exec_error(self):
        with patch("kubernetes.stream.stream", side_effect=RuntimeError("boom")):
            assert pod_has_shell(MagicMock(), "ns", "pod-a") is False

    def test_pod_has_python3_returns_false_on_exec_error(self):
        with patch("kubernetes.stream.stream", side_effect=RuntimeError("boom")):
            assert _pod_has_python3(MagicMock(), "ns", "pod-a") is False

    def test_pod_has_wget_returns_false_on_exec_error(self):
        with patch("kubernetes.stream.stream", side_effect=RuntimeError("boom")):
            assert _pod_has_wget(MagicMock(), "ns", "pod-a") is False


class TestPodHasWget:
    """``_pod_has_wget`` reports whether a pod can run the wget-based probes."""

    def test_true_when_wget_present(self):
        with patch("kubernetes.stream.stream", return_value="ok"):
            assert _pod_has_wget(MagicMock(), "ns", "consul-x") is True

    def test_false_when_wget_absent(self):
        # `command -v wget` finds nothing → no "ok" echoed.
        with patch("kubernetes.stream.stream", return_value=""):
            assert _pod_has_wget(MagicMock(), "ns", "chaos-exporter-y") is False


class TestFindProbePodRequireWget:
    """``require_wget`` skips wget-less pods (the real cause of the hotel gate
    timing out at 0/5: ``chaos-exporter`` sorts first, has a shell, but no
    wget, so every probe failed with 'wget: not found')."""

    @staticmethod
    def _core(*names):
        core = MagicMock()
        core.list_namespaced_pod.return_value = MagicMock(items=[_ready_pod(n) for n in names])
        return core

    def test_require_wget_skips_wgetless_first_pod(self):
        # chaos-exporter sorts first but has no wget; consul does.
        core = self._core("consul-x", "chaos-exporter-y")
        with (
            patch("chaosprobe.metrics.base.pod_has_shell", return_value=True),
            patch(
                "chaosprobe.metrics.base._pod_has_wget",
                side_effect=lambda c, ns, n: n.startswith("consul"),
            ),
        ):
            assert find_probe_pod(core, "ns", require_wget=True) == "consul-x"

    def test_without_require_wget_keeps_alphabetical_first(self):
        # Default behaviour unchanged: first shell pod regardless of wget.
        core = self._core("consul-x", "chaos-exporter-y")
        with (
            patch("chaosprobe.metrics.base.pod_has_shell", return_value=True),
            patch(
                "chaosprobe.metrics.base._pod_has_wget",
                side_effect=lambda c, ns, n: n.startswith("consul"),
            ),
        ):
            assert find_probe_pod(core, "ns") == "chaos-exporter-y"

    def test_require_wget_returns_none_when_no_wget_pod(self):
        # HARD requirement: no fallback to a wget-less shell pod.
        core = self._core("chaos-exporter-y", "mongodb-z")
        with (
            patch("chaosprobe.metrics.base.pod_has_shell", return_value=True),
            patch("chaosprobe.metrics.base._pod_has_wget", return_value=False),
        ):
            assert find_probe_pod(core, "ns", require_wget=True) is None

    def test_require_wget_and_python3_picks_pod_with_both(self):
        # Both have wget; only the second has python3 → require both picks it.
        core = self._core("consul-x", "geo-y")
        with (
            patch("chaosprobe.metrics.base.pod_has_shell", return_value=True),
            patch("chaosprobe.metrics.base._pod_has_wget", return_value=True),
            patch(
                "chaosprobe.metrics.base._pod_has_python3",
                side_effect=lambda c, ns, n: n.startswith("geo"),
            ),
        ):
            got = find_probe_pod(core, "ns", require_python3=True, require_wget=True)
            assert got == "geo-y"

    def test_require_python3_only_does_not_probe_wget(self):
        # The latency-prober shape (require_python3=True, require_wget=False): the
        # restructured loop must behave exactly as before — pick the first shell
        # pod with python3 and NEVER probe wget.
        core = self._core("consul-x", "geo-y")
        wget_mock = MagicMock()
        with (
            patch("chaosprobe.metrics.base.pod_has_shell", return_value=True),
            patch("chaosprobe.metrics.base._pod_has_wget", wget_mock),
            patch(
                "chaosprobe.metrics.base._pod_has_python3",
                side_effect=lambda c, ns, n: n.startswith("geo"),
            ),
        ):
            assert find_probe_pod(core, "ns", require_python3=True) == "geo-y"
        wget_mock.assert_not_called()

    def test_python3_fallback_only_among_wget_pods(self):
        # No python3 anywhere, but both have wget → soft-fallback to the first
        # wget-capable pod (never a wget-less one).
        core = self._core("consul-x", "geo-y")
        with (
            patch("chaosprobe.metrics.base.pod_has_shell", return_value=True),
            patch("chaosprobe.metrics.base._pod_has_wget", return_value=True),
            patch("chaosprobe.metrics.base._pod_has_python3", return_value=False),
        ):
            got = find_probe_pod(core, "ns", require_python3=True, require_wget=True)
            assert got == "consul-x"


class TestChaosInfraPodExclusion:
    """LitmusChaos / Argo-workflow pods join the namespace mid-experiment and
    go Ready, but must never be chosen as probe *sources* — they vanish and
    yield Handshake-404 exec errors that pollute the error rate."""

    def test_is_chaos_infra_pod_litmus_label(self):
        pod = _ready_pod("pod-cpu-hog-helper-1", {"app.kubernetes.io/part-of": "litmus"})
        assert _is_chaos_infra_pod(pod) is True

    def test_is_chaos_infra_pod_argo_workflow_label(self):
        pod = _ready_pod("chaos-wf-xyz", {"workflows.argoproj.io/workflow": "chaos-wf"})
        assert _is_chaos_infra_pod(pod) is True

    def test_is_chaos_infra_pod_app_pod_not_excluded(self):
        assert _is_chaos_infra_pod(_ready_pod("frontend-abc", {"app": "frontend"})) is False

    def test_is_chaos_infra_pod_handles_missing_labels(self):
        pod = _ready_pod("frontend-abc")
        pod.metadata.labels = None
        assert _is_chaos_infra_pod(pod) is False

    @staticmethod
    def _api_with_mixed_pods():
        app = _ready_pod("frontend-abc", {"app": "frontend"})
        # Sorts before the app pod, so without exclusion it would be picked first.
        litmus = _ready_pod("a-pod-cpu-hog-helper", {"app.kubernetes.io/part-of": "litmus"})
        argo = _ready_pod("b-chaos-wf-xyz", {"workflows.argoproj.io/workflow": "chaos-wf"})
        core_api = MagicMock()
        core_api.list_namespaced_pod.return_value = MagicMock(items=[app, litmus, argo])
        return core_api

    def test_find_all_probe_pods_excludes_chaos_pods(self):
        with patch("chaosprobe.metrics.base.pod_has_shell", return_value=True):
            assert find_all_probe_pods(self._api_with_mixed_pods(), "default") == ["frontend-abc"]

    def test_find_all_probe_pods_with_node_excludes_chaos_pods(self):
        with patch("chaosprobe.metrics.base.pod_has_shell", return_value=True):
            assert find_all_probe_pods_with_node(self._api_with_mixed_pods(), "default") == [
                ("frontend-abc", "worker1")
            ]

    def test_find_probe_pod_skips_chaos_pods(self):
        with patch("chaosprobe.metrics.base.pod_has_shell", return_value=True):
            assert find_probe_pod(self._api_with_mixed_pods(), "default") == "frontend-abc"

    def test_find_probe_pods_per_node_excludes_chaos_pods(self):
        with patch("chaosprobe.metrics.base.pod_has_shell", return_value=True):
            assert find_probe_pods_per_node(self._api_with_mixed_pods(), "default") == [
                ("frontend-abc", "worker1")
            ]
