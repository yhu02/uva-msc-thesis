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


def summarize_endpoint_slices_json(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Like :func:`summarize_endpoint_slices`, but from raw EndpointSlice JSON.

    The typed ``discovery.k8s.io/v1`` ``V1EndpointSlice`` model marks ``endpoints``
    as a *required* field, yet the API returns ``endpoints: null`` for an empty
    slice — which happens when *all* of a service's pods are evicted at once (e.g.
    mid ``node-drain``). Deserializing that raises ``ValueError`` and would crash
    the snapshot, so the collector reads slices as raw JSON (``_preload_content=
    False``) and summarizes from dicts here, where a null ``endpoints`` is simply
    an empty list (``total = 0``) — itself the signal that the service lost all
    its endpoints.
    """
    services: Dict[str, Dict[str, int]] = {}
    for item in items or []:
        labels = ((item or {}).get("metadata") or {}).get("labels") or {}
        svc = labels.get(_SERVICE_NAME_LABEL)
        if not (isinstance(svc, str) and svc):
            continue
        bucket = services.setdefault(svc, {"ready": 0, "terminating": 0, "notReady": 0, "total": 0})
        for endpoint in item.get("endpoints") or []:
            bucket["total"] += 1
            cond = endpoint.get("conditions") or {}
            if cond.get("terminating"):
                bucket["terminating"] += 1
            elif cond.get("ready"):
                bucket["ready"] += 1
            else:
                bucket["notReady"] += 1
    return {"services": {name: services[name] for name in sorted(services)}}
