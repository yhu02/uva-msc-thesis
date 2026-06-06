"""EndpointSlice snapshots for the churn-mechanism story.

Pod deletion is an asynchronous, multi-layer event in Kubernetes: the
control plane updates the target Service's EndpointSlices, kube-proxy
reprograms forwarding state on every node, and connection-tracking state
reconverges.  The thesis attributes ``pod-delete`` behaviour to that
churn rather than node-level contention, so capturing the *direct*
API-level signature — how a service's ready / terminating endpoint
counts move around the kill cycle — corroborates the conntrack-flush and
kube-proxy-sync metrics with a first-party source.

This module is pure: :func:`summarize_endpoint_slices` takes already-fetched
EndpointSlice objects (or anything duck-typing them) so it can be unit
tested without a cluster.  The K8s API call lives in ``MetricsCollector``.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Standard label kube-proxy / the EndpointSlice controller stamp on every
# slice naming the Service it backs.
_SERVICE_NAME_LABEL = "kubernetes.io/service-name"


def _service_name(endpoint_slice: Any) -> str:
    labels = getattr(getattr(endpoint_slice, "metadata", None), "labels", None) or {}
    name = labels.get(_SERVICE_NAME_LABEL)
    return name if isinstance(name, str) and name else ""


def summarize_endpoint_slices(slices: List[Any]) -> Dict[str, Any]:
    """Summarize EndpointSlices into per-service endpoint counts.

    Endpoints are grouped by their backing Service (the
    ``kubernetes.io/service-name`` label) and each endpoint entry is
    classified by its conditions:

    - ``terminating`` — ``conditions.terminating`` is true (pod is shutting
      down; the kill cycle's transient state),
    - ``ready`` — not terminating and ``conditions.ready`` is true,
    - ``notReady`` — neither (scheduled but not yet serving, or failing).

    ``total`` is the number of endpoint entries for the service. Slices
    without the service-name label are ignored. Returns
    ``{"services": {name: {ready, terminating, notReady, total}}}`` with
    services sorted by name for stable output.
    """
    services: Dict[str, Dict[str, int]] = {}
    for endpoint_slice in slices:
        svc = _service_name(endpoint_slice)
        if not svc:
            continue
        bucket = services.setdefault(svc, {"ready": 0, "terminating": 0, "notReady": 0, "total": 0})
        for endpoint in getattr(endpoint_slice, "endpoints", None) or []:
            bucket["total"] += 1
            cond = getattr(endpoint, "conditions", None)
            ready = getattr(cond, "ready", None) if cond is not None else None
            terminating = getattr(cond, "terminating", None) if cond is not None else None
            if terminating:
                bucket["terminating"] += 1
            elif ready:
                bucket["ready"] += 1
            else:
                bucket["notReady"] += 1
    return {"services": {name: services[name] for name in sorted(services)}}
