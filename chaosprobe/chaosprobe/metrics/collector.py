"""General-purpose metrics collector for chaos experiments.

Collects multiple categories of metrics during a chaos experiment window:
- Recovery timing (from RecoveryWatcher real-time data)
- Pod restart counts
- Resource pressure on the target node
- Event timeline (all pod lifecycle events)
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException


class MetricsCollector:
    """Collects comprehensive metrics from a chaos experiment run.

    Orchestrates multiple metric sources and returns a unified result.
    The recovery data comes from a RecoveryWatcher that ran during the
    experiment (real-time), while pod status and node info are collected
    after the experiment ends.
    """

    def __init__(self, namespace: str):
        self.namespace = namespace

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.core_api = client.CoreV1Api()

    def collect(
        self,
        deployment_name: str,
        since_time: float,
        until_time: float,
        recovery_data: Optional[Dict[str, Any]] = None,
        latency_data: Optional[Dict[str, Any]] = None,
        redis_data: Optional[Dict[str, Any]] = None,
        disk_data: Optional[Dict[str, Any]] = None,
        resource_data: Optional[Dict[str, Any]] = None,
        collect_logs: bool = False,
    ) -> Dict[str, Any]:
        """Collect all available metrics for a deployment during an experiment.

        Args:
            deployment_name: Target deployment name.
            since_time: Unix timestamp for experiment start.
            until_time: Unix timestamp for experiment end.
            recovery_data: Pre-collected recovery data from RecoveryWatcher.
                           If None, the recovery section will be empty.
            latency_data: Pre-collected inter-service latency data from
                          ContinuousLatencyProber. If None, the latency
                          section will be omitted.
            redis_data: Pre-collected Redis throughput data from
                       ContinuousRedisProber. If None, omitted.
            disk_data: Pre-collected disk I/O throughput data from
                       ContinuousDiskProber. If None, omitted.
            resource_data: Pre-collected node/pod resource utilization
                           from ContinuousResourceProber. If None, omitted.
            collect_logs: If True, collect container logs from target pods.

        Returns:
            Unified metrics dictionary with all collected data.
        """
        pod_status = self._collect_pod_status(deployment_name)

        # Extract node name from already-fetched pod status to avoid a
        # duplicate list_namespaced_pod API call.
        node_name = None
        for pod in pod_status.get("pods", []):
            if pod.get("node"):
                node_name = pod["node"]
                break
        node_info = self._collect_node_info(node_name)

        # Use watcher data if provided, otherwise empty
        if recovery_data is None:
            recovery_data = {
                "deploymentName": deployment_name,
                "recoveryEvents": [],
                "summary": {
                    "count": 0,
                    "completedCycles": 0,
                    "meanRecovery_ms": None,
                    "medianRecovery_ms": None,
                    "minRecovery_ms": None,
                    "maxRecovery_ms": None,
                    "p95Recovery_ms": None,
                },
            }

        # Extract raw events from watcher for the timeline
        event_timeline = recovery_data.pop("rawEvents", [])

        result = {
            "deploymentName": deployment_name,
            "timeWindow": {
                "start": datetime.fromtimestamp(since_time, tz=timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(until_time, tz=timezone.utc).isoformat(),
                "duration_s": round(until_time - since_time, 1),
            },
            "recovery": recovery_data,
            "podStatus": pod_status,
            "eventTimeline": event_timeline,
            "nodeInfo": node_info,
        }

        if latency_data is not None:
            result["latency"] = latency_data

        if redis_data is not None:
            result["redis"] = redis_data

        if disk_data is not None:
            result["disk"] = disk_data

        if resource_data is not None:
            result["resources"] = resource_data

        if collect_logs:
            duration = until_time - since_time
            result["containerLogs"] = self._collect_container_logs(
                deployment_name,
                duration,
            )

        return result

    def _collect_pod_status(self, deployment_name: str) -> Dict[str, Any]:
        """Collect current pod status and restart counts."""
        try:
            pods = self.core_api.list_namespaced_pod(
                self.namespace,
                label_selector=f"app={deployment_name}",
            )
        except ApiException:
            return {"error": "Failed to query pods"}

        pod_list = []
        total_restarts = 0

        for pod in pods.items:
            restarts = 0
            container_statuses = pod.status.container_statuses or []
            for cs in container_statuses:
                restarts += cs.restart_count

            total_restarts += restarts

            conditions = {}
            for cond in pod.status.conditions or []:
                conditions[cond.type] = {
                    "status": cond.status,
                    "lastTransition": (
                        cond.last_transition_time.isoformat() if cond.last_transition_time else None
                    ),
                }

            pod_list.append(
                {
                    "name": pod.metadata.name,
                    "phase": pod.status.phase,
                    "node": pod.spec.node_name,
                    "restartCount": restarts,
                    "conditions": conditions,
                }
            )

        return {
            "pods": pod_list,
            "totalRestarts": total_restarts,
        }

    def _collect_node_info(self, node_name: Optional[str]) -> Optional[Dict[str, Any]]:
        """Collect resource info for a node.

        Args:
            node_name: Name of the node to query, or None to skip.
        """
        if not node_name:
            return None

        try:
            node = self.core_api.read_node(node_name)
        except ApiException:
            return None

        alloc = node.status.allocatable or {}
        capacity = node.status.capacity or {}

        return {
            "nodeName": node_name,
            "allocatable": {
                "cpu": alloc.get("cpu", "0"),
                "memory": alloc.get("memory", "0"),
            },
            "capacity": {
                "cpu": capacity.get("cpu", "0"),
                "memory": capacity.get("memory", "0"),
            },
        }

    def _collect_container_logs(
        self,
        deployment_name: str,
        duration_seconds: float,
        tail_lines: int = 500,
    ) -> Dict[str, Any]:
        """Collect container logs from the target deployment's pods.

        Captures both current and previous (crashed/restarted) container logs,
        which is important for diagnosing OOMKills and crash loops.

        Args:
            deployment_name: Target deployment name.
            duration_seconds: Experiment duration in seconds.
            tail_lines: Maximum number of log lines per container.

        Returns:
            Dict with logs per pod and collection config.
        """
        since_seconds = int(duration_seconds) + 30

        try:
            pods = self.core_api.list_namespaced_pod(
                self.namespace,
                label_selector=f"app={deployment_name}",
            )
        except ApiException as e:
            return {"error": f"Failed to list pods: {e.reason}"}

        logs_by_pod: Dict[str, Any] = {}

        for pod in pods.items:
            pod_name = pod.metadata.name
            pod_logs: Dict[str, Any] = {"containers": {}}

            for container_status in pod.status.container_statuses or []:
                container_name = container_status.name
                container_logs: Dict[str, Optional[str]] = {}

                # Current container logs
                try:
                    current = self.core_api.read_namespaced_pod_log(
                        name=pod_name,
                        namespace=self.namespace,
                        container=container_name,
                        since_seconds=since_seconds,
                        tail_lines=tail_lines,
                    )
                    container_logs["current"] = current
                except ApiException:
                    container_logs["current"] = None

                # Previous container logs (crashed/restarted — OOMKill evidence)
                try:
                    previous = self.core_api.read_namespaced_pod_log(
                        name=pod_name,
                        namespace=self.namespace,
                        container=container_name,
                        since_seconds=since_seconds,
                        tail_lines=tail_lines,
                        previous=True,
                    )
                    container_logs["previous"] = previous
                except ApiException:
                    container_logs["previous"] = None

                pod_logs["containers"][container_name] = container_logs

            pod_logs["restartCount"] = sum(
                cs.restart_count for cs in (pod.status.container_statuses or [])
            )
            logs_by_pod[pod_name] = pod_logs

        return {
            "pods": logs_by_pod,
            "config": {
                "sinceSeconds": since_seconds,
                "tailLines": tail_lines,
            },
        }
