"""Namespace quiescence barrier (shared by the M1b gate and the v2 session driver).

Moved verbatim from ``scripts/m1b_gate.py`` so in-package callers — the v2
complete-block session driver applies it between conditions — can import it
without executing a script module.  ``scripts/m1b_gate.py`` now imports from
here, keeping its CLI surface and test seams identical.

Quiescent = one uninterrupted window of ``settle_seconds`` in which every
deployment is fully ready, the namespace-wide pod restart-count total does not
change, and no new ``Unhealthy`` event is recorded.  The window opens once
readiness is reached and **resets** on any churn signal — the cascading 1 s
gRPC readiness/liveness probe timeouts observed after an apply are exactly
what this barrier lets drain before the next apply.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from chaosprobe.placement.affinity_engine import K8sApi

#: Quiescence barrier defaults: the namespace must stay clean for
#: ``DEFAULT_SETTLE_SECONDS``, waited for at most ``DEFAULT_SETTLE_TIMEOUT``.
DEFAULT_SETTLE_SECONDS = 60.0
DEFAULT_SETTLE_TIMEOUT = 300.0

#: Event reason that resets the quiescence window (probe-timeout churn).
UNHEALTHY_REASON = "Unhealthy"


def _iso(stamp: Optional[datetime]) -> Optional[str]:
    """ISO timestamp for the settle record, passing ``None`` through."""
    return stamp.isoformat() if stamp else None


def event_time(event: Any) -> Optional[datetime]:
    """Best-available timestamp of a Kubernetes event, UTC-normalised.

    Prefers ``lastTimestamp``, then ``eventTime``, then the event object's
    creation timestamp; returns ``None`` when none of them is a datetime.
    """
    metadata = getattr(event, "metadata", None)
    candidates = (
        getattr(event, "last_timestamp", None),
        getattr(event, "event_time", None),
        getattr(metadata, "creation_timestamp", None),
    )
    for candidate in candidates:
        if isinstance(candidate, datetime):
            return candidate if candidate.tzinfo else candidate.replace(tzinfo=timezone.utc)
    return None


def quiescence_snapshot(api: K8sApi, namespace: str) -> Dict[str, Any]:
    """One poll of the barrier's three signals.

    Returns ``notReady`` (deployments not fully rolled out), ``restarts``
    (the namespace-wide pod restart-count total — any *change* between
    polls, up or down, is churn), and ``lastUnhealthyAt`` (newest
    ``Unhealthy`` event timestamp, or ``None``).
    """
    not_ready: List[str] = []
    for dep in api.apps.list_namespaced_deployment(namespace).items:
        desired = dep.spec.replicas if dep.spec.replicas is not None else 1
        generation = dep.metadata.generation or 0
        status = dep.status
        observed = (status.observed_generation or 0) if status else 0
        ready = (status.ready_replicas or 0) if status else 0
        updated = (status.updated_replicas or 0) if status else 0
        available = (status.available_replicas or 0) if status else 0
        settled = (
            observed >= generation
            and updated >= desired
            and ready >= desired
            and available >= desired
        )
        if not settled:
            not_ready.append(dep.metadata.name)

    restarts = 0
    for pod in api.core.list_namespaced_pod(namespace).items:
        statuses = (pod.status.container_statuses or []) if pod.status else []
        restarts += sum(cs.restart_count or 0 for cs in statuses)

    last_unhealthy: Optional[datetime] = None
    for event in api.core.list_namespaced_event(namespace).items:
        if event.reason != UNHEALTHY_REASON:
            continue
        stamp = event_time(event)
        if stamp is not None and (last_unhealthy is None or stamp > last_unhealthy):
            last_unhealthy = stamp

    return {"notReady": sorted(not_ready), "restarts": restarts, "lastUnhealthyAt": last_unhealthy}


def wait_for_quiescence(
    api: K8sApi,
    namespace: str,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    timeout: float = DEFAULT_SETTLE_TIMEOUT,
    poll_seconds: float = 5.0,
) -> Dict[str, Any]:
    """Block until the namespace is quiescent, or ``timeout`` elapses.

    On timeout the barrier does **not** abort the caller: the verify step
    still judges the attempt, and the returned settle record (embedded in
    the gate artifact / ``v2Session`` metadata) makes an unsettled start
    attributable after the fact.
    """
    started = time.monotonic()
    deadline = started + timeout
    polls = 0
    resets = 0
    window_opened: Optional[float] = None
    window_opened_at: Optional[datetime] = None
    previous_restarts: Optional[int] = None
    quiescent = False
    while True:
        polls += 1
        snapshot = quiescence_snapshot(api, namespace)
        now = time.monotonic()
        churned = (
            bool(snapshot["notReady"])
            or (previous_restarts is not None and snapshot["restarts"] != previous_restarts)
            or (
                window_opened_at is not None
                and snapshot["lastUnhealthyAt"] is not None
                and snapshot["lastUnhealthyAt"] >= window_opened_at
            )
        )
        previous_restarts = snapshot["restarts"]
        if churned:
            if window_opened is not None:
                resets += 1
            window_opened = None
            window_opened_at = None
        elif window_opened is None:
            window_opened = now
            window_opened_at = datetime.now(timezone.utc)
        if window_opened is not None and now - window_opened >= settle_seconds:
            quiescent = True
            break
        if now >= deadline:
            break
        time.sleep(poll_seconds)
    return {
        "quiescent": quiescent,
        "waitedSeconds": round(time.monotonic() - started, 1),
        "polls": polls,
        "windowResets": resets,
        "settleSeconds": settle_seconds,
        "timeoutSeconds": timeout,
        "notReady": snapshot["notReady"],
        "restarts": snapshot["restarts"],
        "lastUnhealthyAt": _iso(snapshot["lastUnhealthyAt"]),
    }
