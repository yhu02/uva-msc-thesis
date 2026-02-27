"""General-purpose metrics collector for chaos experiments.

Collects multiple categories of metrics during a chaos experiment window:
- Recovery timing (from RecoveryWatcher real-time data)
- Pod restart counts
- Resource pressure on the target node
- Event timeline (all pod lifecycle events)
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
        self.apps_api = client.AppsV1Api()

    def collect(
        self,
        deployment_name: str,
        since_time: float,
        until_time: float,
        recovery_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Collect all available metrics for a deployment during an experiment.

        Args:
            deployment_name: Target deployment name.
            since_time: Unix timestamp for experiment start.
            until_time: Unix timestamp for experiment end.
            recovery_data: Pre-collected recovery data from RecoveryWatcher.
                           If None, the recovery section will be empty.

        Returns:
            Unified metrics dictionary with all collected data.
        """
        pod_status = self._collect_pod_status(deployment_name)
        node_info = self._collect_node_info(deployment_name)

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

        return {
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
                    "lastTransition": cond.last_transition_time.isoformat()
                    if cond.last_transition_time else None,
                }

            pod_list.append({
                "name": pod.metadata.name,
                "phase": pod.status.phase,
                "node": pod.spec.node_name,
                "restartCount": restarts,
                "conditions": conditions,
            })

        return {
            "pods": pod_list,
            "totalRestarts": total_restarts,
        }

    def _collect_node_info(self, deployment_name: str) -> Optional[Dict[str, Any]]:
        """Collect resource info for the node running the target deployment."""
        try:
            pods = self.core_api.list_namespaced_pod(
                self.namespace,
                label_selector=f"app={deployment_name}",
            )
        except ApiException:
            return None

        node_name = None
        for pod in pods.items:
            if pod.spec.node_name:
                node_name = pod.spec.node_name
                break

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
