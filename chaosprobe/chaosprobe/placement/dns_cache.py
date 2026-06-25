"""Pod-level DNS-cache intervention for the C3 / H2 campaign.

The thesis cluster runs **NodeLocal DNSCache** (kubelet ``clusterDNS`` is the
link-local cache IP ``169.254.25.10``), so pods resolve through the node-local
cache **by default** — i.e. *cache-on* is the cluster's standing state, and the
node-local cache forwards upstream to CoreDNS over TCP, which is precisely why
it removes the cross-node **UDP** DNS conntrack that H2 is about.

This module toggles, per session and per app deployment, whether pods *bypass*
that cache:

- **cache-off** (the cross-node-UDP baseline): patch ``dnsPolicy: None`` +
  ``dnsConfig`` pointing at the CoreDNS clusterIP, so resolution goes
  pod → CoreDNS over UDP (the cross-node conntrack flows).
- **cache-on** (cluster default / the intervention): clear the override,
  reverting to the kubelet-default node-local cache.

It realizes "NodeLocal DNSCache on/off" via pod ``dnsConfig`` rather than the
kubelet ``--cluster-dns`` default, chosen for **per-session reversibility** under
the randomized-order paired C3 design (a logged deviation). Pods
still resolve through the same node-local cache; only the per-deployment
resolver selection changes.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence

from chaosprobe.placement.affinity_engine import ApplyResult, K8sApi, wait_for_rollouts

logger = logging.getLogger(__name__)

#: DNS-cache modes (the C3 cache axis).
CACHE_ON = "on"
CACHE_OFF = "off"
CACHE_MODES = (CACHE_ON, CACHE_OFF)

#: Managed annotation recording the DNS-cache mode chaosprobe applied.
DNS_CACHE_ANNOTATION = "chaosprobe.io/dns-cache"

#: Where to discover the CoreDNS clusterIP (the cache-off upstream).
_DNS_NAMESPACE = "kube-system"
_DNS_SERVICE_NAMES = ("coredns", "kube-dns")


def _validate_mode(mode: str) -> None:
    if mode not in CACHE_MODES:
        raise ValueError(f"dns-cache mode must be one of {CACHE_MODES}, got '{mode}'")


def _cluster_searches(namespace: str) -> List[str]:
    """The ClusterFirst search domains a cache-off pod must replicate.

    ``dnsPolicy: None`` replaces the resolver wholesale, so the search list and
    ``ndots`` that ClusterFirst would inject must be restated or in-cluster
    short names (``svc``, ``svc.ns``) stop resolving.
    """
    return [f"{namespace}.svc.cluster.local", "svc.cluster.local", "cluster.local"]


def discover_coredns_clusterip(api: K8sApi) -> str:
    """The in-cluster DNS service clusterIP — the cache-off resolver target."""
    for name in _DNS_SERVICE_NAMES:
        try:
            svc = api.core.read_namespaced_service(name, _DNS_NAMESPACE)
        except Exception:  # service absent under this name — try the next
            continue
        ip = getattr(svc.spec, "cluster_ip", None) if svc and svc.spec else None
        if ip and ip != "None":
            return str(ip)
    raise RuntimeError(
        f"could not discover a CoreDNS/kube-dns clusterIP in namespace '{_DNS_NAMESPACE}'"
    )


def build_dns_patch(mode: str, namespace: str, coredns_ip: str) -> Dict[str, Any]:
    """Strategic-merge patch toggling one deployment's DNS resolver.

    cache-off pins the pod resolver to ``coredns_ip`` over UDP (bypassing the
    node-local cache); cache-on clears the override, restoring the kubelet
    default. A ``Recreate`` rollout is forced either way because a pod's
    ``resolv.conf`` is written at start, so the resolver only changes on
    restart — never a mixed generation.
    """
    _validate_mode(mode)
    if mode == CACHE_OFF:
        if not coredns_ip:
            raise ValueError("cache-off requires a non-empty CoreDNS clusterIP")
        dns_spec: Dict[str, Any] = {
            "dnsPolicy": "None",
            "dnsConfig": {
                "nameservers": [coredns_ip],
                "searches": _cluster_searches(namespace),
                "options": [{"name": "ndots", "value": "5"}],
            },
        }
    else:  # CACHE_ON — revert to the kubelet-default node-local cache
        dns_spec = {"dnsPolicy": "ClusterFirst", "dnsConfig": None}
    return {
        "metadata": {"annotations": {DNS_CACHE_ANNOTATION: mode}},
        "spec": {
            "strategy": {"type": "Recreate", "rollingUpdate": None},
            "template": {"spec": dns_spec},
        },
    }


def apply_dns_cache(
    api: K8sApi,
    namespace: str,
    services: Sequence[str],
    mode: str,
    coredns_ip: Optional[str] = None,
    wait: bool = True,
    timeout: float = 300,
    poll_seconds: float = 3.0,
) -> ApplyResult:
    """Patch each app deployment's DNS resolver to ``mode`` and settle.

    ``services`` is the explicit app set (the session passes its own discovered
    set, never re-discovering). ``coredns_ip`` is discovered when omitted and
    only consulted for cache-off. Returns the patched names, any still pending
    at the deadline, and the wall-clock latency.
    """
    _validate_mode(mode)
    names = sorted(s for s in services if s)
    if not names:
        raise ValueError("apply_dns_cache requires a non-empty service set")
    if mode == CACHE_OFF and not coredns_ip:
        coredns_ip = discover_coredns_clusterip(api)
    started = time.monotonic()
    for svc in names:
        api.apps.patch_namespaced_deployment(
            svc, namespace, build_dns_patch(mode, namespace, coredns_ip or "")
        )
    pending: List[str] = []
    if wait:
        pending = wait_for_rollouts(api, namespace, names, timeout, poll_seconds)
        if pending:
            logger.warning(
                "apply_dns_cache(mode=%s): %d deployment(s) not ready after %.0fs: %s",
                mode,
                len(pending),
                timeout,
                ", ".join(pending),
            )
    return ApplyResult(applied=names, pending=pending, duration_seconds=time.monotonic() - started)
