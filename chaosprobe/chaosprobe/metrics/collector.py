"""General-purpose metrics collector for chaos experiments.

Collects multiple categories of metrics during a chaos experiment window:
- Recovery timing (pod deletion → scheduling → ready)
- Pod restart counts
- Resource pressure on the target node
- Event timeline (all pod lifecycle events)
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from chaosprobe.metrics.recovery import RecoveryMetricsCollector


class MetricsCollector:
    """Collects comprehensive metrics from a chaos experiment run.

    Orchestrates multiple metric sources and returns a unified result.
    """

    def __init__(self, namespace: str):
        self.namespace = namespace

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.core_api = client.CoreV1Api()
        self.apps_api = client.AppsV1Api()
        self._recovery = RecoveryMetricsCollector(namespace)

    def collect(
        self,
        deployment_name: str,
        since_time: float,
        until_time: float,
    ) -> Dict[str, Any]:
        """Collect all available metrics for a deployment during an experiment.

        Args:
            deployment_name: Target deployment name.
            since_time: Unix timestamp for experiment start.
            until_time: Unix timestamp for experiment end.

        Returns:
            Unified metrics dictionary with all collected data.
        """
        recovery = self._recovery.collect(deployment_name, since_time, until_time)
        pod_status = self._collect_pod_status(deployment_name)
        event_timeline = self._collect_event_timeline(deployment_name, since_time, until_time)
        node_info = self._collect_node_info(deployment_name)

        return {
            "deploymentName": deployment_name,
            "timeWindow": {
                "start": datetime.fromtimestamp(since_time, tz=timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(until_time, tz=timezone.utc).isoformat(),
                "duration_s": round(until_time - since_time, 1),
            },
            "recovery": recovery,
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

    def _collect_event_timeline(
        self,
        deployment_name: str,
        since_time: float,
        until_time: float,
    ) -> List[Dict[str, Any]]:
        """Collect all pod events during the experiment window."""
        try:
            events_resp = self.core_api.list_namespaced_event(
                namespace=self.namespace,
                field_selector="involvedObject.kind=Pod",
            )
        except ApiException:
            return []

        since_dt = datetime.fromtimestamp(since_time, tz=timezone.utc)
        until_dt = datetime.fromtimestamp(until_time, tz=timezone.utc)

        timeline = []
        for event in events_resp.items:
            obj_name = event.involved_object.name or ""
            if deployment_name not in obj_name:
                continue

            event_time = event.last_timestamp or event.event_time
            if event_time is None:
                continue

            if hasattr(event_time, "tzinfo") and event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            if event_time < since_dt or event_time > until_dt:
                continue

            timeline.append({
                "time": event_time.isoformat(),
                "reason": event.reason,
                "pod": obj_name,
                "message": event.message or "",
                "type": event.type,
            })

        timeline.sort(key=lambda e: e["time"])
        return timeline

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
