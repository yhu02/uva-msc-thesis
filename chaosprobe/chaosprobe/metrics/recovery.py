"""Pod recovery time metrics via real-time Kubernetes watch.

Runs a background thread that watches pod phase transitions for the target
deployment.  The watcher records deletion and ready timestamps as they happen,
guaranteeing capture regardless of Kubernetes event-store retention.
"""

import statistics
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes import client, config, watch


class RecoveryWatcher:
    """Watches pods in real-time and records recovery cycles.

    Usage::

        watcher = RecoveryWatcher("online-boutique", "checkoutservice")
        watcher.start()          # begins background watch
        # ... run chaos experiment ...
        watcher.stop()           # stops the watch thread
        result = watcher.result()  # structured recovery data
    """

    def __init__(self, namespace: str, deployment_name: str):
        self.namespace = namespace
        self.deployment_name = deployment_name
        self._label_selector = f"app={deployment_name}"

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.core_api = client.CoreV1Api()

        # Internal state
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Pod tracking: pod_name -> was_ready (bool)
        self._pod_ready: Dict[str, bool] = {}
        # Open cycle: pod was deleted, waiting for replacement
        self._pending_deletion: Optional[datetime] = None
        # Completed recovery cycles
        self._cycles: List[Dict[str, Any]] = []
        # Raw events for the timeline
        self._events: List[Dict[str, Any]] = []

    # ── Lifecycle ────────────────────────────────────────────

    def start(self) -> None:
        """Start watching pods in a background thread."""
        # Snapshot current pods before experiment starts
        self._snapshot_pods()

        self._thread = threading.Thread(
            target=self._watch_loop, daemon=True, name="recovery-watcher"
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the watch and wait for the thread to finish."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

        # Close any pending cycle
        with self._lock:
            if self._pending_deletion is not None:
                self._cycles.append(self._finalize_cycle({
                    "deletionTime": self._pending_deletion,
                    "scheduledTime": None,
                    "readyTime": None,
                }))
                self._pending_deletion = None

    def result(self) -> Dict[str, Any]:
        """Return structured recovery data."""
        with self._lock:
            cycles = list(self._cycles)
            events = list(self._events)

        summary = self._compute_summary(cycles)
        return {
            "deploymentName": self.deployment_name,
            "recoveryEvents": cycles,
            "summary": summary,
            "rawEvents": events,
        }

    # ── Watch loop ───────────────────────────────────────────

    def _snapshot_pods(self) -> None:
        """Record current pod ready state before chaos starts."""
        try:
            pods = self.core_api.list_namespaced_pod(
                self.namespace, label_selector=self._label_selector
            )
            for pod in pods.items:
                self._pod_ready[pod.metadata.name] = self._is_pod_ready(pod)
        except Exception:
            pass

    def _watch_loop(self) -> None:
        """Main watch loop running in background thread."""
        w = watch.Watch()
        try:
            for event in w.stream(
                self.core_api.list_namespaced_pod,
                namespace=self.namespace,
                label_selector=self._label_selector,
                timeout_seconds=0,  # server-side: no timeout
            ):
                if self._stop_event.is_set():
                    break

                event_type = event["type"]  # ADDED, MODIFIED, DELETED
                pod = event["object"]
                pod_name = pod.metadata.name
                pod_phase = pod.status.phase
                now = datetime.now(timezone.utc)

                with self._lock:
                    self._events.append({
                        "time": now.isoformat(),
                        "type": event_type,
                        "pod": pod_name,
                        "phase": pod_phase,
                    })

                    if event_type == "DELETED":
                        self._pod_ready.pop(pod_name, None)
                        # Pod deleted — start or extend a recovery cycle
                        if self._pending_deletion is None:
                            self._pending_deletion = now

                    elif event_type in ("ADDED", "MODIFIED"):
                        was_ready = self._pod_ready.get(pod_name, False)
                        is_ready = (
                            pod_phase == "Running" and self._is_pod_ready(pod)
                        )
                        self._pod_ready[pod_name] = is_ready

                        # Trigger on not-ready → ready transition
                        if is_ready and not was_ready and self._pending_deletion is not None:
                            scheduled_time = self._get_scheduled_time(pod)
                            self._cycles.append(self._finalize_cycle({
                                "deletionTime": self._pending_deletion,
                                "scheduledTime": scheduled_time,
                                "readyTime": now,
                            }))
                            self._pending_deletion = None
        except Exception:
            pass
        finally:
            w.stop()

    # ── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _is_pod_ready(pod) -> bool:
        """Check if all containers in the pod are ready."""
        if not pod.status.conditions:
            return False
        for cond in pod.status.conditions:
            if cond.type == "Ready" and cond.status == "True":
                return True
        return False

    @staticmethod
    def _get_scheduled_time(pod) -> Optional[datetime]:
        """Extract PodScheduled condition transition time."""
        if not pod.status.conditions:
            return None
        for cond in pod.status.conditions:
            if cond.type == "PodScheduled" and cond.last_transition_time:
                ts = cond.last_transition_time
                if hasattr(ts, "tzinfo") and ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return ts
        return None

    @staticmethod
    def _finalize_cycle(cycle: Dict[str, Any]) -> Dict[str, Any]:
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

    @staticmethod
    def _compute_summary(cycles: List[Dict[str, Any]]) -> Dict[str, Any]:
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
