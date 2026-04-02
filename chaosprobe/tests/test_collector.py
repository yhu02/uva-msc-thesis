"""Tests for the MetricsCollector container log collection."""

from unittest.mock import MagicMock, patch

from kubernetes.client.rest import ApiException

from chaosprobe.metrics.collector import MetricsCollector


def _make_collector():
    """Create a MetricsCollector without hitting the real K8s API."""
    with patch("chaosprobe.metrics.collector.config"), \
         patch("chaosprobe.metrics.collector.client") as mock_client:
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
