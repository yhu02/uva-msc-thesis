"""Tests for the MetricsCollector container log collection."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from kubernetes.client.rest import ApiException

from chaosprobe.metrics.collector import MetricsCollector


def _make_node_condition(
    type_: str,
    status: str = "False",
    reason: str = "",
    message: str = "",
    last_transition: object = None,
):
    """Build a fake kubernetes.client.V1NodeCondition-like object."""
    cond = MagicMock()
    cond.type = type_
    cond.status = status
    cond.reason = reason
    cond.message = message
    cond.last_transition_time = last_transition
    return cond


def _make_node(conditions=None, allocatable=None, capacity=None):
    """Build a fake kubernetes.client.V1Node-like object."""
    node = MagicMock()
    node.status = MagicMock()
    node.status.conditions = conditions
    node.status.allocatable = allocatable or {"cpu": "2", "memory": "4Gi"}
    node.status.capacity = capacity or {"cpu": "2", "memory": "4Gi"}
    return node


def _make_collector():
    """Create a MetricsCollector without hitting the real K8s API."""
    with (
        patch("chaosprobe.metrics.collector.ensure_k8s_config"),
        patch("chaosprobe.metrics.collector.client") as mock_client,
    ):
        mock_core = MagicMock()
        mock_apps = MagicMock()
        mock_client.CoreV1Api.return_value = mock_core
        mock_client.AppsV1Api.return_value = mock_apps
        collector = MetricsCollector("test-ns")
    return collector, mock_core


class TestCollectContainerLogs:
    def test_collects_current_and_previous_logs(self):
        collector, mock_core = _make_collector()
        collector.core_api = mock_core

        # Simulate one pod with one container
        mock_container_status = MagicMock()
        mock_container_status.name = "server"
        mock_container_status.restart_count = 2

        mock_pod = MagicMock()
        mock_pod.metadata.name = "checkout-abc"
        mock_pod.status.container_statuses = [mock_container_status]

        mock_core.list_namespaced_pod.return_value = MagicMock(items=[mock_pod])

        # Return different strings for current vs previous
        def log_side_effect(*args, **kwargs):
            if kwargs.get("previous"):
                return "OOM killed at 12:00:01"
            return "Starting server on :5050\nReady"

        mock_core.read_namespaced_pod_log.side_effect = log_side_effect

        result = collector._collect_container_logs("checkoutservice", 60.0)

        assert "checkout-abc" in result["pods"]
        pod_logs = result["pods"]["checkout-abc"]
        assert pod_logs["restartCount"] == 2
        assert "server" in pod_logs["containers"]
        assert pod_logs["containers"]["server"]["current"] == "Starting server on :5050\nReady"
        assert pod_logs["containers"]["server"]["previous"] == "OOM killed at 12:00:01"
        assert result["config"]["tailLines"] == 500

    def test_previous_logs_unavailable(self):
        collector, mock_core = _make_collector()
        collector.core_api = mock_core

        mock_cs = MagicMock()
        mock_cs.name = "app"
        mock_cs.restart_count = 0

        mock_pod = MagicMock()
        mock_pod.metadata.name = "svc-xyz"
        mock_pod.status.container_statuses = [mock_cs]

        mock_core.list_namespaced_pod.return_value = MagicMock(items=[mock_pod])

        def log_side_effect(*args, **kwargs):
            if kwargs.get("previous"):
                raise ApiException(status=400, reason="previous terminated container not found")
            return "normal log output"

        mock_core.read_namespaced_pod_log.side_effect = log_side_effect

        result = collector._collect_container_logs("svc", 30.0)

        pod_logs = result["pods"]["svc-xyz"]
        assert pod_logs["containers"]["app"]["current"] == "normal log output"
        assert pod_logs["containers"]["app"]["previous"] is None

    def test_pod_gone_404(self):
        collector, mock_core = _make_collector()
        collector.core_api = mock_core

        mock_cs = MagicMock()
        mock_cs.name = "server"
        mock_cs.restart_count = 0

        mock_pod = MagicMock()
        mock_pod.metadata.name = "gone-pod"
        mock_pod.status.container_statuses = [mock_cs]

        mock_core.list_namespaced_pod.return_value = MagicMock(items=[mock_pod])
        mock_core.read_namespaced_pod_log.side_effect = ApiException(status=404)

        result = collector._collect_container_logs("svc", 60.0)

        # Should not crash; both logs should be None
        pod_logs = result["pods"]["gone-pod"]
        assert pod_logs["containers"]["server"]["current"] is None
        assert pod_logs["containers"]["server"]["previous"] is None

    def test_list_pods_failure(self):
        collector, mock_core = _make_collector()
        collector.core_api = mock_core

        mock_core.list_namespaced_pod.side_effect = ApiException(status=500, reason="Internal")

        result = collector._collect_container_logs("svc", 60.0)
        assert "error" in result

    def test_multiple_containers(self):
        collector, mock_core = _make_collector()
        collector.core_api = mock_core

        mock_cs1 = MagicMock()
        mock_cs1.name = "main"
        mock_cs1.restart_count = 1
        mock_cs2 = MagicMock()
        mock_cs2.name = "sidecar"
        mock_cs2.restart_count = 0

        mock_pod = MagicMock()
        mock_pod.metadata.name = "multi-pod"
        mock_pod.status.container_statuses = [mock_cs1, mock_cs2]

        mock_core.list_namespaced_pod.return_value = MagicMock(items=[mock_pod])
        mock_core.read_namespaced_pod_log.return_value = "log line"

        result = collector._collect_container_logs("svc", 60.0)

        containers = result["pods"]["multi-pod"]["containers"]
        assert "main" in containers
        assert "sidecar" in containers
        assert result["pods"]["multi-pod"]["restartCount"] == 1


class TestCollectIntegration:
    def test_collect_passes_resource_data_and_logs(self):
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        collector.apps_api = MagicMock()

        # Mock pod status
        mock_core.list_namespaced_pod.return_value = MagicMock(items=[])

        resource_data = {"available": True, "timeSeries": [], "phases": {}}

        result = collector.collect(
            deployment_name="svc",
            since_time=1000.0,
            until_time=1060.0,
            resource_data=resource_data,
            collect_logs=False,
        )

        assert result["resources"] is resource_data
        assert "containerLogs" not in result

    def test_collect_with_logs_enabled(self):
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        collector.apps_api = MagicMock()

        mock_core.list_namespaced_pod.return_value = MagicMock(items=[])

        result = collector.collect(
            deployment_name="svc",
            since_time=1000.0,
            until_time=1060.0,
            collect_logs=True,
        )

        assert "containerLogs" in result
        assert "config" in result["containerLogs"]


class TestCollectNodeInfoConditions:
    """`_collect_node_info` surfaces node pressure conditions for the
    placement-vs-pressure analysis path of the thesis."""

    def test_returns_none_when_node_name_missing(self):
        collector, _ = _make_collector()
        assert collector._collect_node_info(None) is None
        assert collector._collect_node_info("") is None

    def test_returns_none_on_api_error(self):
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        mock_core.read_node.side_effect = ApiException(status=404)
        assert collector._collect_node_info("worker-1") is None

    def test_healthy_node_emits_all_false_pressure_flags(self):
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        ts = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
        node = _make_node(
            conditions=[
                _make_node_condition("Ready", "True", "KubeletReady", "ready", ts),
                _make_node_condition("MemoryPressure", "False", "KubeletHasSufficientMemory"),
                _make_node_condition("DiskPressure", "False", "KubeletHasNoDiskPressure"),
                _make_node_condition("PIDPressure", "False", "KubeletHasSufficientPID"),
                _make_node_condition("NetworkUnavailable", "False"),
            ],
        )
        mock_core.read_node.return_value = node

        info = collector._collect_node_info("worker-1")

        assert info is not None
        assert info["nodeName"] == "worker-1"
        assert info["conditions"]["Ready"]["status"] == "True"
        assert info["conditions"]["Ready"]["reason"] == "KubeletReady"
        assert info["conditions"]["Ready"]["message"] == "ready"
        assert info["conditions"]["Ready"]["lastTransition"] == ts.isoformat()
        for pressure in ("MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable"):
            assert info["conditions"][pressure]["status"] == "False"

    def test_single_pressure_condition_surfaces(self):
        """A node under MemoryPressure has its condition flag flipped to True
        with the kubelet's reason captured, so placement-driven contention
        can be distinguished from healthy nodes."""
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        node = _make_node(
            conditions=[
                _make_node_condition("Ready", "True"),
                _make_node_condition(
                    "MemoryPressure",
                    "True",
                    reason="KubeletHasInsufficientMemory",
                    message="node has insufficient memory available",
                ),
            ],
        )
        mock_core.read_node.return_value = node

        info = collector._collect_node_info("worker-2")

        assert info["conditions"]["MemoryPressure"]["status"] == "True"
        assert info["conditions"]["MemoryPressure"]["reason"] == "KubeletHasInsufficientMemory"
        assert "insufficient memory" in info["conditions"]["MemoryPressure"]["message"]
        # Conditions not reported by the node are absent from the dict so
        # consumers can distinguish "False" (kubelet says fine) from absent
        # (kubelet didn't report it).
        assert "DiskPressure" not in info["conditions"]

    def test_node_with_no_conditions_yields_empty_dict(self):
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        node = _make_node(conditions=None)
        mock_core.read_node.return_value = node

        info = collector._collect_node_info("worker-3")

        # Explicit empty dict (not absent / None) so callers can tell
        # "node returned no condition data" from "field doesn't exist"
        assert info["conditions"] == {}

    def test_node_with_no_status_yields_empty_dict(self):
        """When the K8s client returns a node object whose status field is
        entirely absent (rare, but real on freshly-registered nodes), the
        condition extraction returns `{}` without raising."""
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        node = MagicMock()
        node.status = None  # the edge case under test
        mock_core.read_node.return_value = node

        info = collector._collect_node_info("worker-4")

        assert info is not None
        assert info["conditions"] == {}

    def test_custom_condition_type_passes_through(self):
        """node-problem-detector and similar agents add custom condition
        types; surface them unchanged so any post-hoc analysis can pick
        them up."""
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        node = _make_node(
            conditions=[
                _make_node_condition("Ready", "True"),
                _make_node_condition(
                    "KernelDeadlock",
                    status="True",
                    reason="DockerHung",
                    message="task X blocked for more than 120 seconds",
                ),
            ],
        )
        mock_core.read_node.return_value = node

        info = collector._collect_node_info("worker-5")

        assert info["conditions"]["KernelDeadlock"]["status"] == "True"
        assert info["conditions"]["KernelDeadlock"]["reason"] == "DockerHung"

    def test_condition_without_type_is_skipped(self):
        """Malformed condition entries (defensive: type is required by the
        K8s API but we shouldn't crash if a mock returns one without it)."""
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        bad = MagicMock()
        bad.type = None  # malformed
        bad.status = "True"
        bad.reason = ""
        bad.message = ""
        bad.last_transition_time = None
        node = _make_node(
            conditions=[
                bad,
                _make_node_condition("Ready", "True"),
            ],
        )
        mock_core.read_node.return_value = node

        info = collector._collect_node_info("worker-6")

        assert "Ready" in info["conditions"]
        assert len(info["conditions"]) == 1  # the malformed one was dropped

    def test_lasttransition_str_passes_through_unchanged(self):
        """If a mock returns lastTransition as an already-formatted string
        (instead of a datetime), the extractor passes it through with
        str() coercion rather than calling .isoformat()."""
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        node = _make_node(
            conditions=[
                _make_node_condition("Ready", "True", last_transition="2026-05-28T12:00:00Z"),
            ],
        )
        mock_core.read_node.return_value = node

        info = collector._collect_node_info("worker-7")

        assert info["conditions"]["Ready"]["lastTransition"] == "2026-05-28T12:00:00Z"


def _make_container_status(
    name: str,
    *,
    restart_count: int = 0,
    last_terminated_reason: object = None,
    current_terminated_reason: object = None,
    ready: bool = True,
):
    """Build a fake V1ContainerStatus-like object for OOM-aggregation tests."""
    cs = MagicMock()
    cs.name = name
    cs.restart_count = restart_count
    cs.ready = ready

    cs.state = MagicMock()
    if current_terminated_reason is not None:
        cs.state.running = None
        cs.state.waiting = None
        cs.state.terminated = MagicMock()
        cs.state.terminated.reason = current_terminated_reason
        cs.state.terminated.exit_code = 137 if current_terminated_reason == "OOMKilled" else 1
    else:
        cs.state.running = MagicMock()
        cs.state.running.started_at = None
        cs.state.waiting = None
        cs.state.terminated = None

    cs.last_state = MagicMock()
    if last_terminated_reason is not None:
        cs.last_state.terminated = MagicMock()
        cs.last_state.terminated.reason = last_terminated_reason
        cs.last_state.terminated.exit_code = 137 if last_terminated_reason == "OOMKilled" else 1
        cs.last_state.terminated.started_at = None
        cs.last_state.terminated.finished_at = None
        cs.last_state.terminated.message = ""
    else:
        cs.last_state.terminated = None
    return cs


def _make_pod_for_status(name: str, container_statuses, node: str = "worker-1"):
    """Build a fake V1Pod-like object whose container_statuses we control."""
    pod = MagicMock()
    pod.metadata = MagicMock()
    pod.metadata.name = name
    pod.spec = MagicMock()
    pod.spec.node_name = node
    pod.spec.containers = []  # no resource specs needed for OOM tests
    pod.status = MagicMock()
    pod.status.phase = "Running"
    pod.status.container_statuses = container_statuses
    pod.status.conditions = []
    return pod


class TestCollectPodStatusOOMKills:
    """`_collect_pod_status` aggregates OOMKill counts at the pod and
    podStatus level so iterations confounded by the chaos target being
    OOMed (e.g. colocate packing too many pods onto a 2 GiB worker) can
    be flagged post-hoc."""

    def test_no_oom_kills_emits_zero(self):
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        pod = _make_pod_for_status("frontend-abc", [_make_container_status("app")])
        mock_core.list_namespaced_pod.return_value = MagicMock(items=[pod])

        result = collector._collect_pod_status("frontend")

        assert result["totalOOMKills"] == 0
        assert result["pods"][0]["containers"][0]["oomKillCount"] == 0

    def test_oom_in_last_termination_counted(self):
        """An OOMKill in `lastTermination.reason` (the canonical signal —
        the prior container generation was OOMed and kubelet restarted)
        increments the count."""
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        pod = _make_pod_for_status(
            "frontend-abc",
            [_make_container_status("app", restart_count=1, last_terminated_reason="OOMKilled")],
        )
        mock_core.list_namespaced_pod.return_value = MagicMock(items=[pod])

        result = collector._collect_pod_status("frontend")

        assert result["totalOOMKills"] == 1
        assert result["pods"][0]["containers"][0]["oomKillCount"] == 1

    def test_oom_in_current_terminated_state_also_counted(self):
        """A container still in `state.terminated` with reason=OOMKilled
        (caught the iteration mid-OOM) is counted too."""
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        pod = _make_pod_for_status(
            "frontend-abc",
            [_make_container_status("app", current_terminated_reason="OOMKilled")],
        )
        mock_core.list_namespaced_pod.return_value = MagicMock(items=[pod])

        result = collector._collect_pod_status("frontend")
        assert result["totalOOMKills"] == 1

    def test_non_oom_termination_not_counted(self):
        """A `lastTermination.reason` other than `OOMKilled` (e.g.
        `Completed`, `Error`) does NOT increment the OOM count."""
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        pod = _make_pod_for_status(
            "frontend-abc",
            [_make_container_status("app", restart_count=1, last_terminated_reason="Error")],
        )
        mock_core.list_namespaced_pod.return_value = MagicMock(items=[pod])

        result = collector._collect_pod_status("frontend")
        assert result["totalOOMKills"] == 0
        assert result["pods"][0]["containers"][0]["oomKillCount"] == 0

    def test_multi_container_aggregation(self):
        """Each OOMed container increments the pod-level and the top-level
        aggregate; non-OOM containers don't."""
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        pod = _make_pod_for_status(
            "frontend-abc",
            [
                _make_container_status("app", restart_count=2, last_terminated_reason="OOMKilled"),
                _make_container_status("sidecar"),  # no OOM
                _make_container_status(
                    "init",
                    current_terminated_reason="OOMKilled",
                ),
            ],
        )
        mock_core.list_namespaced_pod.return_value = MagicMock(items=[pod])

        result = collector._collect_pod_status("frontend")

        assert result["totalOOMKills"] == 2
        # Per-container breakdown
        container_oom = {c["name"]: c["oomKillCount"] for c in result["pods"][0]["containers"]}
        assert container_oom == {"app": 1, "sidecar": 0, "init": 1}

    def test_multi_pod_oom_aggregates_across_pods(self):
        """A pod-delete burst can OOM the replacement on multiple pods;
        the top-level totalOOMKills sums across the namespace."""
        collector, mock_core = _make_collector()
        collector.core_api = mock_core
        pods = [
            _make_pod_for_status(
                "frontend-1",
                [
                    _make_container_status(
                        "app", restart_count=1, last_terminated_reason="OOMKilled"
                    )
                ],
            ),
            _make_pod_for_status(
                "frontend-2",
                [
                    _make_container_status(
                        "app", restart_count=1, last_terminated_reason="OOMKilled"
                    )
                ],
            ),
        ]
        mock_core.list_namespaced_pod.return_value = MagicMock(items=pods)

        result = collector._collect_pod_status("frontend")

        assert result["totalOOMKills"] == 2
