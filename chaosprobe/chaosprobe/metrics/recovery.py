"""Pod recovery time metrics via real-time Kubernetes watch.

Runs a background thread that watches pod phase transitions for the target
deployment.  The watcher records deletion and ready timestamps as they happen,
guaranteeing capture regardless of Kubernetes event-store retention.
"""

import logging
import statistics
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes import client, watch

from chaosprobe.k8s import ensure_k8s_config

logger = logging.getLogger(__name__)


class RecoveryWatcher:
    """Watches pods in real-time and records recovery cycles.

    Usage::

        watcher = RecoveryWatcher("online-boutique", "checkoutservice")
        watcher.start()          # begins background watch
        # ... run chaos experiment ...
        watcher.stop()           # stops the watch thread
        result = watcher.result()  # structured recovery data
    """

    # Event reasons we surface as `schedulerEvents`.  These are the K8s
    # events that explain *why* the scheduler made a decision, *why* a
    # scheduling attempt failed, or *why* a pod can't start — exactly the
    # signal the H9 "scheduling latency dominates recovery" hypothesis needs.
    _SCHEDULER_EVENT_REASONS = frozenset(
        {
            "Scheduled",
            "FailedScheduling",
            "BackOff",
            "FailedCreate",
            "FailedMount",
        }
    )

    def __init__(self, namespace: str, deployment_name: str):
        self.namespace = namespace
        self.deployment_name = deployment_name
        self._label_selector = f"app={deployment_name}"
        self._pod_name_prefix = f"{deployment_name}-"

        ensure_k8s_config()

        self.core_api = client.CoreV1Api()

        # Internal state
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._event_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Pod tracking: pod_name -> was_ready (bool)
        self._pod_ready: Dict[str, bool] = {}
        # Open cycle: pod was deleted, waiting for replacement
        self._pending_deletion: Optional[datetime] = None
        # Completed recovery cycles
        self._cycles: List[Dict[str, Any]] = []
        # Raw events for the timeline
        self._events: List[Dict[str, Any]] = []
        # Watch errors surfaced through result()
        self._watch_errors: List[str] = []
        # Scheduler-specific K8s events for the deployment's pods.  Captured
        # in parallel with the pod-state watch so a failed scheduling attempt
        # is visible even when the pod never reaches Ready.
        self._scheduler_events: List[Dict[str, Any]] = []

    # ── Lifecycle ────────────────────────────────────────────

    def start(self) -> None:
        """Start watching pods and scheduler events in background threads."""
        # Snapshot current pods before experiment starts
        self._snapshot_pods()

        self._thread = threading.Thread(
            target=self._watch_loop, daemon=True, name="recovery-watcher"
        )
        self._thread.start()

        self._event_thread = threading.Thread(
            target=self._event_watch_loop, daemon=True, name="recovery-event-watcher"
        )
        self._event_thread.start()

    def stop(self) -> None:
        """Stop the watches and wait for both threads to finish."""
        self._stop_event.set()
        for thread in (self._thread, self._event_thread):
            if thread and thread.is_alive():
                thread.join(timeout=5)

        # Close any pending cycle
        with self._lock:
            if self._pending_deletion is not None:
                self._cycles.append(
                    self._finalize_cycle(
                        {
                            "deletionTime": self._pending_deletion,
                            "scheduledTime": None,
                            "readyTime": None,
                            "failure_reason": "experiment_ended_before_recovery",
                        }
                    )
                )
                self._pending_deletion = None

    def result(self) -> Dict[str, Any]:
        """Return structured recovery data."""
        with self._lock:
            cycles = list(self._cycles)
            events = list(self._events)
            scheduler_events = list(self._scheduler_events)
            errors = list(self._watch_errors)

        summary = self._compute_summary(cycles)
        data: Dict[str, Any] = {
            "deploymentName": self.deployment_name,
            "recoveryEvents": cycles,
            "summary": summary,
            "rawEvents": events,
            "schedulerEvents": scheduler_events,
        }
        if errors:
            data["watchErrors"] = errors
        return data

    # ── Watch loop ───────────────────────────────────────────

    def _snapshot_pods(self) -> None:
        """Record current pod ready state before chaos starts."""
        try:
            pods = self.core_api.list_namespaced_pod(
                self.namespace, label_selector=self._label_selector
            )
            for pod in pods.items:
                self._pod_ready[pod.metadata.name] = self._is_pod_ready(pod)
        except Exception as exc:
            logger.warning("Failed to snapshot pods: %s", exc)

    def _watch_loop(self) -> None:
        """Main watch loop running in background thread.

        Retries on transient errors (API disconnects, network blips) which
        are common during chaos experiments.
        """
        max_retries = 5
        retry_delay = 1.0

        for attempt in range(max_retries):
            if self._stop_event.is_set():
                return

            w = watch.Watch()
            try:
                for event in w.stream(
                    self.core_api.list_namespaced_pod,
                    namespace=self.namespace,
                    label_selector=self._label_selector,
                    timeout_seconds=0,  # server-side: no timeout
                ):
                    if self._stop_event.is_set():
                        return

                    event_type = event["type"]  # ADDED, MODIFIED, DELETED
                    pod = event["object"]
                    pod_name = pod.metadata.name
                    pod_phase = pod.status.phase
                    now = datetime.now(timezone.utc)

                    with self._lock:
                        self._events.append(
                            {
                                "time": now.isoformat(),
                                "type": event_type,
                                "pod": pod_name,
                                "phase": pod_phase,
                            }
                        )

                        if event_type == "DELETED":
                            self._pod_ready.pop(pod_name, None)
                            # Pod deleted — start or extend a recovery cycle
                            if self._pending_deletion is None:
                                # Use local clock for sub-ms precision.
                                # Both deletion and ready use the same
                                # clock (watch-event reception time) to
                                # avoid skew between local and K8s clocks.
                                self._pending_deletion = now

                        elif event_type in ("ADDED", "MODIFIED"):
                            was_ready = self._pod_ready.get(pod_name, False)
                            is_ready = pod_phase == "Running" and self._is_pod_ready(pod)
                            self._pod_ready[pod_name] = is_ready

                            # Trigger on not-ready → ready transition
                            if is_ready and not was_ready and self._pending_deletion is not None:
                                scheduled_time = self._get_scheduled_time(pod)
                                # Use local clock (now) for sub-ms precision,
                                # consistent with deletionTime's clock source.
                                self._cycles.append(
                                    self._finalize_cycle(
                                        {
                                            "deletionTime": self._pending_deletion,
                                            "scheduledTime": scheduled_time,
                                            "readyTime": now,
                                        }
                                    )
                                )
                                self._pending_deletion = None
            except Exception as exc:
                logger.warning(
                    "Watch stream interrupted (attempt %d/%d): %s",
                    attempt + 1,
                    max_retries,
                    exc,
                )
                with self._lock:
                    self._watch_errors.append(f"attempt {attempt + 1}: {exc}")
                if attempt < max_retries - 1 and not self._stop_event.is_set():
                    self._stop_event.wait(timeout=retry_delay)
                    retry_delay = min(retry_delay * 2, 10.0)
            finally:
                w.stop()

    def _event_watch_loop(self) -> None:
        """Background loop that watches K8s events for the deployment's pods.

        Uses the same retry-with-backoff shape as the pod watch.  Events
        are filtered to:

        * involvedObject kind == Pod, namespace == self.namespace
        * involvedObject name starts with ``"<deployment>-"`` (the standard
          K8s naming convention for deployment-managed pods)
        * reason ∈ ``_SCHEDULER_EVENT_REASONS``

        Each captured event is recorded as a flat dict; downstream consumers
        (visualisation, ML export) can join on `podName` to correlate with
        recovery cycles.
        """
        max_retries = 5
        retry_delay = 1.0

        for attempt in range(max_retries):
            if self._stop_event.is_set():
                return

            w = watch.Watch()
            try:
                for raw_event in w.stream(
                    self.core_api.list_namespaced_event,
                    namespace=self.namespace,
                    field_selector="involvedObject.kind=Pod",
                    timeout_seconds=0,
                ):
                    if self._stop_event.is_set():
                        return

                    parsed = self._parse_scheduler_event(raw_event.get("object"))
                    if parsed is None:
                        continue
                    with self._lock:
                        self._scheduler_events.append(parsed)
            except Exception as exc:
                logger.warning(
                    "Scheduler-event watch interrupted (attempt %d/%d): %s",
                    attempt + 1,
                    max_retries,
                    exc,
                )
                with self._lock:
                    self._watch_errors.append(f"event-attempt {attempt + 1}: {exc}")
                if attempt < max_retries - 1 and not self._stop_event.is_set():
                    self._stop_event.wait(timeout=retry_delay)
                    retry_delay = min(retry_delay * 2, 10.0)
            finally:
                w.stop()

    def _parse_scheduler_event(self, event_obj: Any) -> Optional[Dict[str, Any]]:
        """Convert a K8s V1Event into a flat dict, or None if the event is
        not one of the scheduler reasons we surface (or is malformed)."""
        if event_obj is None:
            return None
        reason = getattr(event_obj, "reason", None)
        if reason not in self._SCHEDULER_EVENT_REASONS:
            return None
        involved = getattr(event_obj, "involved_object", None)
        if involved is None:
            return None
        pod_name = getattr(involved, "name", None)
        if not pod_name or not pod_name.startswith(self._pod_name_prefix):
            return None

        # Node: prefer event.source.host (set by the scheduler / kubelet),
        # fall back to None if absent.  Don't parse the message text — that's
        # K8s-version-fragile.
        source = getattr(event_obj, "source", None)
        node = getattr(source, "host", None) if source is not None else None

        event_time = (
            getattr(event_obj, "event_time", None)
            or getattr(event_obj, "last_timestamp", None)
            or getattr(event_obj, "first_timestamp", None)
        )
        if event_time is not None and hasattr(event_time, "isoformat"):
            timestamp = event_time.isoformat()
        elif event_time is not None:
            timestamp = str(event_time)
        else:
            timestamp = datetime.now(timezone.utc).isoformat()

        return {
            "timestamp": timestamp,
            "type": getattr(event_obj, "type", None),
            "reason": reason,
            "message": getattr(event_obj, "message", None),
            "podName": pod_name,
            "node": node,
        }

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
        """Convert a raw cycle into the output format with durations.

        Note: scheduledTime comes from the K8s PodScheduled condition which
        has only second-level precision (truncated, not rounded). deletionTime
        and readyTime use the local clock with ms precision. This mismatch
        can produce negative deletionToScheduled_ms values (up to -999ms)
        when the scheduling happens within the same second as deletion.
        We clamp deletionToScheduled_ms to 0 minimum since negative scheduling
        time is physically impossible — it's a clock-precision artifact.
        """
        deletion = cycle["deletionTime"]
        scheduled = cycle.get("scheduledTime")
        ready = cycle.get("readyTime")

        deletion_to_scheduled = None
        scheduled_to_ready = None
        total_recovery = None

        if deletion and scheduled:
            raw_d2s = int((scheduled - deletion).total_seconds() * 1000)
            # Clamp to 0: K8s scheduledTime has second-level precision
            # (always truncated to :00.000), so it can appear to be
            # "before" the ms-precision deletionTime within the same second.
            deletion_to_scheduled = max(0, raw_d2s)

        if scheduled and ready:
            raw_s2r = int((ready - scheduled).total_seconds() * 1000)
            # Same clock-precision issue: clamp to 0.
            scheduled_to_ready = max(0, raw_s2r)

        if deletion and ready:
            total_recovery = int((ready - deletion).total_seconds() * 1000)

        result = {
            "deletionTime": deletion.isoformat() if deletion else None,
            "scheduledTime": scheduled.isoformat() if scheduled else None,
            "readyTime": ready.isoformat() if ready else None,
            "deletionToScheduled_ms": deletion_to_scheduled,
            "scheduledToReady_ms": scheduled_to_ready,
            "totalRecovery_ms": total_recovery,
        }
        if cycle.get("failure_reason"):
            result["failure_reason"] = cycle["failure_reason"]
        return result

    @staticmethod
    def _compute_summary(cycles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute summary statistics from recovery cycles."""
        recovery_times = [
            c["totalRecovery_ms"] for c in cycles if c["totalRecovery_ms"] is not None
        ]

        incomplete = len(cycles) - len(recovery_times)

        if not recovery_times:
            return {
                "count": len(cycles),
                "completedCycles": 0,
                "incompleteCycles": incomplete,
                "meanRecovery_ms": None,
                "medianRecovery_ms": None,
                "minRecovery_ms": None,
                "maxRecovery_ms": None,
                "p95Recovery_ms": None,
            }

        sorted_times = sorted(recovery_times)
        p95_idx = int(len(sorted_times) * 0.95)
        p95_idx = min(p95_idx, len(sorted_times) - 1)

        # Split into the two phases.  Schedules under heavy contention
        # (colocate / best-fit / random-with-affinity-collision) can stall
        # in the deletion-to-scheduled phase while the kubelet retries.
        # Separating the components makes those scheduler-pathological
        # cases distinguishable from true container start-up latency.
        d2s = [
            c["deletionToScheduled_ms"]
            for c in cycles
            if c.get("deletionToScheduled_ms") is not None
        ]
        s2r = [c["scheduledToReady_ms"] for c in cycles if c.get("scheduledToReady_ms") is not None]

        summary = {
            "count": len(cycles),
            "completedCycles": len(recovery_times),
            "incompleteCycles": incomplete,
            "meanRecovery_ms": round(statistics.mean(recovery_times), 1),
            "medianRecovery_ms": round(statistics.median(recovery_times), 1),
            "minRecovery_ms": min(recovery_times),
            "maxRecovery_ms": max(recovery_times),
            "p95Recovery_ms": round(sorted_times[p95_idx], 1),
        }

        if d2s:
            summary["meanDeletionToScheduled_ms"] = round(statistics.mean(d2s), 1)
            summary["maxDeletionToScheduled_ms"] = max(d2s)
        if s2r:
            summary["meanScheduledToReady_ms"] = round(statistics.mean(s2r), 1)
            summary["maxScheduledToReady_ms"] = max(s2r)

        return summary
