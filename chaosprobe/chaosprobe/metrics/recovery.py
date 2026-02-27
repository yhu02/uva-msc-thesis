"""Pod recovery time metrics collector.

Measures how long it takes for pods to recover after chaos-induced deletions
by correlating Kubernetes events (Killing, Scheduled, Started) into recovery
cycles with precise timing data.
"""

import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException


class RecoveryMetricsCollector:
    """Collects pod recovery timing metrics from Kubernetes events."""

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
    ) -> Dict[str, Any]:
        """Collect recovery metrics for a deployment during a time window.

        Args:
            deployment_name: Name of the target deployment (used as pod label).
            since_time: Unix timestamp for window start.
            until_time: Unix timestamp for window end.

        Returns:
            Dictionary with recoveryEvents list and summary statistics.
        """
        events = self._get_pod_events(deployment_name, since_time, until_time)
        cycles = self._build_recovery_cycles(events)

        summary = self._compute_summary(cycles)

        return {
            "deploymentName": deployment_name,
            "recoveryEvents": cycles,
            "summary": summary,
        }

    def _get_pod_events(
        self,
        deployment_name: str,
        since_time: float,
        until_time: float,
    ) -> List[Dict[str, Any]]:
        """Fetch relevant pod events from the Kubernetes API."""
        relevant_reasons = {
            "Killing", "Scheduled", "Pulled", "Created", "Started",
            "SuccessfulCreate",
        }

        try:
            events_resp = self.core_api.list_namespaced_event(
                namespace=self.namespace,
                field_selector="involvedObject.kind=Pod",
            )
        except ApiException:
            return []

        filtered = []
        since_dt = datetime.fromtimestamp(since_time, tz=timezone.utc)
        until_dt = datetime.fromtimestamp(until_time, tz=timezone.utc)

        for event in events_resp.items:
            if event.reason not in relevant_reasons:
                continue

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

            filtered.append({
                "reason": event.reason,
                "timestamp": event_time,
                "pod": obj_name,
                "message": event.message or "",
            })

        filtered.sort(key=lambda e: e["timestamp"])
        return filtered

    def _build_recovery_cycles(
        self,
        events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Group events into recovery cycles.

        A recovery cycle starts with a Killing event and completes when
        a new pod reaches the Started state.
        """
        cycles: List[Dict[str, Any]] = []
        current_cycle: Optional[Dict[str, Any]] = None

        for event in events:
            reason = event["reason"]
            ts = event["timestamp"]

            if reason == "Killing":
                if current_cycle is not None:
                    cycles.append(self._finalize_cycle(current_cycle))
                current_cycle = {
                    "deletionTime": ts,
                    "scheduledTime": None,
                    "readyTime": None,
                }

            elif current_cycle is not None:
                if reason == "Scheduled" and current_cycle["scheduledTime"] is None:
                    current_cycle["scheduledTime"] = ts
                elif reason == "Started" and current_cycle["readyTime"] is None:
                    current_cycle["readyTime"] = ts
                    cycles.append(self._finalize_cycle(current_cycle))
                    current_cycle = None

        if current_cycle is not None:
            cycles.append(self._finalize_cycle(current_cycle))

        return cycles

    def _finalize_cycle(self, cycle: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a raw cycle into the output format with durations."""
        deletion = cycle["deletionTime"]
        scheduled = cycle.get("scheduledTime")
        ready = cycle.get("readyTime")

        deletion_to_scheduled = None
        scheduled_to_ready = None
        total_recovery = None

        if deletion and scheduled:
            deletion_to_scheduled = int(
                (scheduled - deletion).total_seconds() * 1000
            )

        if scheduled and ready:
            scheduled_to_ready = int(
                (ready - scheduled).total_seconds() * 1000
            )

        if deletion and ready:
            total_recovery = int(
                (ready - deletion).total_seconds() * 1000
            )

        return {
            "deletionTime": deletion.isoformat() if deletion else None,
            "scheduledTime": scheduled.isoformat() if scheduled else None,
            "readyTime": ready.isoformat() if ready else None,
            "deletionToScheduled_ms": deletion_to_scheduled,
            "scheduledToReady_ms": scheduled_to_ready,
            "totalRecovery_ms": total_recovery,
        }

    def _compute_summary(
        self, cycles: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Compute summary statistics from recovery cycles."""
        recovery_times = [
            c["totalRecovery_ms"]
            for c in cycles
            if c["totalRecovery_ms"] is not None
        ]

        if not recovery_times:
            return {
                "count": len(cycles),
                "completedCycles": 0,
                "meanRecovery_ms": None,
                "medianRecovery_ms": None,
                "minRecovery_ms": None,
                "maxRecovery_ms": None,
                "p95Recovery_ms": None,
            }

        sorted_times = sorted(recovery_times)
        p95_idx = int(len(sorted_times) * 0.95)
        p95_idx = min(p95_idx, len(sorted_times) - 1)

        return {
            "count": len(cycles),
            "completedCycles": len(recovery_times),
            "meanRecovery_ms": round(statistics.mean(recovery_times), 1),
            "medianRecovery_ms": round(statistics.median(recovery_times), 1),
            "minRecovery_ms": min(recovery_times),
            "maxRecovery_ms": max(recovery_times),
            "p95Recovery_ms": round(sorted_times[p95_idx], 1),
        }
