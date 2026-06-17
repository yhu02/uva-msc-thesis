"""Tests for the C3 / V2-H2 pod-level DNS-cache toggle (placement/dns_cache.py).

Pure-Python per CONTRIBUTING: the Kubernetes API and the rollout barrier are
MagicMocked — no cluster is touched.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import chaosprobe.placement.dns_cache as dc
from chaosprobe.placement.affinity_engine import ApplyResult

COREDNS_IP = "10.233.0.3"


def _svc(cluster_ip):
    return SimpleNamespace(spec=SimpleNamespace(cluster_ip=cluster_ip))


# ── build_dns_patch ────────────────────────────────────────────────────


def test_cache_off_patch_pins_resolver_to_coredns_over_udp():
    p = dc.build_dns_patch(dc.CACHE_OFF, "online-boutique", COREDNS_IP)
    spec = p["spec"]["template"]["spec"]
    assert spec["dnsPolicy"] == "None"
    assert spec["dnsConfig"]["nameservers"] == [COREDNS_IP]
    assert spec["dnsConfig"]["searches"] == [
        "online-boutique.svc.cluster.local",
        "svc.cluster.local",
        "cluster.local",
    ]
    assert spec["dnsConfig"]["options"] == [{"name": "ndots", "value": "5"}]
    assert p["metadata"]["annotations"][dc.DNS_CACHE_ANNOTATION] == "off"
    # Recreate so pods restart with the new resolv.conf (never a mixed generation).
    assert p["spec"]["strategy"] == {"type": "Recreate", "rollingUpdate": None}


def test_cache_on_patch_clears_override_to_kubelet_default():
    p = dc.build_dns_patch(dc.CACHE_ON, "online-boutique", COREDNS_IP)
    spec = p["spec"]["template"]["spec"]
    assert spec["dnsPolicy"] == "ClusterFirst"
    assert spec["dnsConfig"] is None  # revert to the node-local cache default
    assert p["metadata"]["annotations"][dc.DNS_CACHE_ANNOTATION] == "on"


def test_cache_on_ignores_coredns_ip():
    # cache-on needs no upstream IP; an empty one is fine.
    p = dc.build_dns_patch(dc.CACHE_ON, "ns", "")
    assert p["spec"]["template"]["spec"]["dnsConfig"] is None


def test_invalid_mode_raises():
    with pytest.raises(ValueError, match="dns-cache mode must be one of"):
        dc.build_dns_patch("warm", "ns", COREDNS_IP)


def test_cache_off_requires_coredns_ip():
    with pytest.raises(ValueError, match="cache-off requires a non-empty CoreDNS clusterIP"):
        dc.build_dns_patch(dc.CACHE_OFF, "ns", "")


# ── discover_coredns_clusterip ─────────────────────────────────────────


def test_discover_returns_coredns_clusterip():
    api = MagicMock()
    api.core.read_namespaced_service.return_value = _svc(COREDNS_IP)
    assert dc.discover_coredns_clusterip(api) == COREDNS_IP
    api.core.read_namespaced_service.assert_called_with("coredns", "kube-system")


def test_discover_falls_back_to_kube_dns():
    api = MagicMock()

    def _read(name, ns):
        if name == "coredns":
            raise RuntimeError("not found")
        return _svc("10.96.0.10")

    api.core.read_namespaced_service.side_effect = _read
    assert dc.discover_coredns_clusterip(api) == "10.96.0.10"


def test_discover_skips_headless_and_raises_when_none():
    api = MagicMock()
    api.core.read_namespaced_service.return_value = _svc("None")  # headless → skip
    with pytest.raises(RuntimeError, match="could not discover"):
        dc.discover_coredns_clusterip(api)


# ── apply_dns_cache ────────────────────────────────────────────────────


def _wire(monkeypatch, pending=None):
    api = MagicMock()
    api.core.read_namespaced_service.return_value = _svc(COREDNS_IP)
    rollouts = MagicMock(return_value=pending or [])
    monkeypatch.setattr(dc, "wait_for_rollouts", rollouts)
    return api, rollouts


def test_apply_patches_each_service_and_waits(monkeypatch):
    api, rollouts = _wire(monkeypatch)
    res = dc.apply_dns_cache(api, "ns", ["b", "a"], dc.CACHE_OFF, coredns_ip=COREDNS_IP)
    assert isinstance(res, ApplyResult)
    assert res.applied == ["a", "b"]  # sorted
    assert res.pending == []
    # one patch per service, with the cache-off patch
    assert api.apps.patch_namespaced_deployment.call_count == 2
    svc, ns, patch = api.apps.patch_namespaced_deployment.call_args_list[0].args
    assert ns == "ns" and patch["metadata"]["annotations"][dc.DNS_CACHE_ANNOTATION] == "off"
    rollouts.assert_called_once()


def test_apply_cache_off_discovers_coredns_ip_when_omitted(monkeypatch):
    api, _ = _wire(monkeypatch)
    dc.apply_dns_cache(api, "ns", ["a"], dc.CACHE_OFF)  # no coredns_ip
    _, _, patch = api.apps.patch_namespaced_deployment.call_args.args
    assert patch["spec"]["template"]["spec"]["dnsConfig"]["nameservers"] == [COREDNS_IP]
    api.core.read_namespaced_service.assert_called()


def test_apply_cache_on_does_not_discover(monkeypatch):
    api, _ = _wire(monkeypatch)
    dc.apply_dns_cache(api, "ns", ["a"], dc.CACHE_ON)
    api.core.read_namespaced_service.assert_not_called()


def test_apply_reports_pending(monkeypatch):
    api, _ = _wire(monkeypatch, pending=["a"])
    res = dc.apply_dns_cache(api, "ns", ["a"], dc.CACHE_ON)
    assert res.pending == ["a"]


def test_apply_no_wait_skips_rollout(monkeypatch):
    api, rollouts = _wire(monkeypatch)
    dc.apply_dns_cache(api, "ns", ["a"], dc.CACHE_ON, wait=False)
    rollouts.assert_not_called()


def test_apply_empty_services_raises(monkeypatch):
    api, _ = _wire(monkeypatch)
    with pytest.raises(ValueError, match="non-empty service set"):
        dc.apply_dns_cache(api, "ns", [], dc.CACHE_ON)


def test_apply_invalid_mode_raises(monkeypatch):
    api, _ = _wire(monkeypatch)
    with pytest.raises(ValueError, match="dns-cache mode must be one of"):
        dc.apply_dns_cache(api, "ns", ["a"], "warm")
