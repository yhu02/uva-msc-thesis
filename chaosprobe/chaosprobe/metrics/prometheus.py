"""Prometheus metrics collector for chaos experiments.

Queries an existing Prometheus instance in the cluster during chaos
experiments and collects application-level and infrastructure metrics
that complement ChaosProbe's active probing data.
"""

import json
import logging
import socket
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from chaosprobe.metrics.throughput import _ContinuousProberBase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default PromQL queries for common Kubernetes / Online Boutique metrics
# ---------------------------------------------------------------------------

DEFAULT_QUERIES: Dict[str, str] = {
    "container_restarts": (
        'sum(rate(kube_pod_container_status_restarts_total{{namespace="{namespace}"}}[5m])) by (pod)'
    ),
    "cpu_usage": (
        'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",container!=""}}[5m])) by (pod)'
    ),
    "cpu_throttling": (
        'sum(rate(container_cpu_cfs_throttled_seconds_total{{namespace="{namespace}"}}[5m])) by (pod)'
    ),
    "memory_usage": (
        'sum(container_memory_working_set_bytes{{namespace="{namespace}",container!=""}}) by (pod)'
    ),
    "network_receive_bytes": (
        'sum(rate(container_network_receive_bytes_total{{namespace="{namespace}"}}[5m])) by (pod)'
    ),
}

# Common service names / namespaces where Prometheus is typically deployed.
# Names must match exactly (not substring) to avoid false positives like
# prometheus-node-exporter or prometheus-pushgateway.
_PROMETHEUS_SERVICE_NAMES = ("prometheus-server", "prometheus", "prometheus-k8s")
_PROMETHEUS_NAMESPACES = ("monitoring", "prometheus", "kube-prometheus", "default")
_PROMETHEUS_PORT = 9090


def _query_prometheus(
    base_url: str, query: str, timeout: float = 10.0,
) -> Optional[List[Dict[str, Any]]]:
    """Execute an instant PromQL query and return the result vector.

    Returns a list of ``{"labels": {...}, "value": float}`` dicts, or
    *None* on error.
    """
    params = urlencode({"query": query})
    url = f"{base_url}/api/v1/query?{params}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("Prometheus query failed (%s): %s", url, exc)
        return None

    if body.get("status") != "success":
        logger.debug("Prometheus returned non-success: %s", body.get("error"))
        return None

    results: List[Dict[str, Any]] = []
    for item in body.get("data", {}).get("result", []):
        metric_labels = {k: v for k, v in item.get("metric", {}).items() if k != "__name__"}
        # Instant query returns [timestamp, "value"]
        raw_value = item.get("value", [None, None])
        try:
            value = float(raw_value[1])
        except (TypeError, IndexError, ValueError):
            continue
        results.append({"labels": metric_labels, "value": round(value, 6)})
    return results


def _find_prometheus_service() -> List[Tuple[str, str, int]]:
    """Find all Prometheus services in the cluster via the K8s API.

    Returns a list of ``(service_name, namespace, port)`` tuples.
    """
    try:
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        core = client.CoreV1Api()
    except Exception:
        return []

    found: List[Tuple[str, str, int]] = []
    for ns in _PROMETHEUS_NAMESPACES:
        try:
            services = core.list_namespaced_service(ns)
        except Exception:
            continue
        for svc in services.items:
            name = svc.metadata.name
            if name in _PROMETHEUS_SERVICE_NAMES:
                port = _PROMETHEUS_PORT
                for p in svc.spec.ports or []:
                    if p.port in (9090, 80, 443):
                        port = p.port
                        break
                found.append((name, ns, port))
    return found


def _find_free_port() -> int:
    """Return an available local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _check_prometheus_url(url: str, timeout: float = 5.0) -> bool:
    """Return True if *url* responds to a Prometheus API health check."""
    try:
        req = urllib.request.Request(
            f"{url}/api/v1/status/config", method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def discover_prometheus_urls(namespace: str = "monitoring") -> List[str]:
    """Try to find all reachable Prometheus URLs.

    1. Locate Prometheus services via the K8s API.
    2. Try a direct in-cluster URL for each (works inside the cluster).
    3. Return only those that respond to a health check.  The caller can
       set up port-forwards for unreachable services separately.
    """
    services = _find_prometheus_service()
    if not services:
        return []

    reachable: List[str] = []
    for name, ns, port in services:
        url = f"http://{name}.{ns}:{port}"
        if _check_prometheus_url(url):
            reachable.append(url)
    return reachable


# Keep backward-compatible alias
def discover_prometheus_url(namespace: str = "monitoring") -> Optional[str]:
    """Return the first reachable Prometheus URL, or *None*."""
    urls = discover_prometheus_urls(namespace)
    return urls[0] if urls else None


# ---------------------------------------------------------------------------
# Continuous Prometheus prober
# ---------------------------------------------------------------------------


class ContinuousPrometheusProber(_ContinuousProberBase):
    """Queries one or more Prometheus instances at intervals during a chaos
    experiment.

    Behaves identically to ContinuousResourceProber: start/stop lifecycle,
    phase tracking, and structured result output with per-phase aggregation.

    If no Prometheus URLs are given, the prober attempts auto-discovery.
    If Prometheus is unreachable the prober disables itself gracefully.

    Multiple Prometheus instances are supported — each query is sent to
    every reachable server and results are merged (deduplicated by label
    set).

    Usage::

        prober = ContinuousPrometheusProber(
            namespace="online-boutique",
            prometheus_urls=["http://prometheus-server.monitoring:9090"],
        )
        prober.start()
        prober.mark_chaos_start()
        # ... chaos ...
        prober.mark_chaos_end()
        prober.stop()
        data = prober.result()
    """

    def __init__(
        self,
        namespace: str,
        prometheus_url: Optional[str] = None,
        prometheus_urls: Optional[List[str]] = None,
        interval: float = 10.0,
        queries: Optional[Dict[str, str]] = None,
    ):
        super().__init__(namespace, interval, name="prometheus-prober")
        # Accept both singular and plural for convenience
        if prometheus_urls:
            self._prometheus_urls: List[str] = list(prometheus_urls)
        elif prometheus_url:
            self._prometheus_urls = [prometheus_url]
        else:
            self._prometheus_urls = []
        self._available: bool = True
        self._port_forward_procs: List[subprocess.Popen] = []
        # Resolve query templates with the target namespace
        raw = queries if queries is not None else dict(DEFAULT_QUERIES)
        self._queries: Dict[str, str] = {
            label: tpl.format(namespace=namespace) for label, tpl in raw.items()
        }

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start Prometheus probing.  Auto-discovers if no URLs given."""
        if not self._prometheus_urls:
            # Try direct in-cluster URLs first
            self._prometheus_urls = discover_prometheus_urls(self.namespace)
            if self._prometheus_urls:
                logger.info(
                    "Auto-discovered %d Prometheus instance(s): %s",
                    len(self._prometheus_urls),
                    ", ".join(self._prometheus_urls),
                )
            else:
                # Direct URLs not reachable — try port-forwarding each service
                services = _find_prometheus_service()
                if services:
                    for svc_name, ns, port in services:
                        url = self._start_port_forward(svc_name, ns, port)
                        if url:
                            self._prometheus_urls.append(url)
                if self._prometheus_urls:
                    logger.info(
                        "Prometheus reachable via port-forward: %s",
                        ", ".join(self._prometheus_urls),
                    )
                else:
                    logger.warning(
                        "No Prometheus instance found — prometheus probing disabled"
                    )
                    self._available = False
                    return

        # Connectivity check — keep only reachable URLs
        reachable = [u for u in self._prometheus_urls if _check_prometheus_url(u)]
        if not reachable:
            logger.warning(
                "Prometheus at %s is unreachable — probing disabled",
                ", ".join(self._prometheus_urls),
            )
            self._available = False
            return
        self._prometheus_urls = reachable

        super().start()

    def stop(self) -> None:
        """Stop probing and clean up any port-forward processes."""
        super().stop()
        self._cleanup_port_forwards()

    # -- probe loop ---------------------------------------------------------

    def _probe_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._available:
                break

            try:
                now = time.time()
                phase = self._current_phase(now)
                entry = self._make_entry(now, phase)

                metrics: Dict[str, Any] = {}
                for label, query in self._queries.items():
                    merged: List[Dict[str, Any]] = []
                    seen_keys: set = set()
                    for url in self._prometheus_urls:
                        result = _query_prometheus(url, query)
                        if result is not None:
                            for item in result:
                                # Deduplicate by (sorted label key-value pairs)
                                key = tuple(sorted(item["labels"].items()))
                                if key not in seen_keys:
                                    seen_keys.add(key)
                                    merged.append(item)
                    if merged:
                        metrics[label] = merged

                if not metrics:
                    # All queries failed — Prometheus may be down
                    with self._lock:
                        self._probe_errors += 1
                    if self._probe_errors >= 3:
                        logger.warning(
                            "Prometheus unreachable after %d attempts — stopping",
                            self._probe_errors,
                        )
                        self._available = False
                        break
                else:
                    entry["metrics"] = metrics
                    with self._lock:
                        self._time_series.append(entry)

            except Exception as exc:
                logger.warning("Prometheus probe failed: %s", exc)
                with self._lock:
                    self._probe_errors += 1

            self._stop_event.wait(timeout=self.interval)

    # -- result -------------------------------------------------------------

    def result(self) -> Dict[str, Any]:
        """Return structured Prometheus metrics data."""
        with self._lock:
            series = list(self._time_series)

        if not series:
            return {
                "available": False,
                "reason": (
                    "prometheus not found"
                    if not self._available
                    else "no data collected"
                ),
            }

        phases = self._split_phases(series)

        data: Dict[str, Any] = {
            "available": True,
            "serverUrls": list(self._prometheus_urls),
            "queries": dict(self._queries),
            "timeSeries": series,
            "phases": phases,
            "config": {
                "interval_s": self.interval,
                "namespace": self.namespace,
            },
        }
        if self._probe_errors > 0:
            data["probeErrors"] = self._probe_errors
        return data

    # -- phase aggregation --------------------------------------------------

    def _split_phases(
        self, series: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Aggregate metrics per phase (pre/during/post-chaos)."""
        buckets: Dict[str, List[Dict[str, Any]]] = {
            "pre-chaos": [],
            "during-chaos": [],
            "post-chaos": [],
        }
        for entry in series:
            buckets.setdefault(entry.get("phase", "pre-chaos"), []).append(entry)

        result: Dict[str, Any] = {}
        for phase_name, entries in buckets.items():
            if not entries:
                result[phase_name] = {"sampleCount": 0}
                continue

            phase_summary: Dict[str, Any] = {"sampleCount": len(entries)}

            # Collect all metric labels seen in this phase
            metric_labels: set = set()
            for e in entries:
                metric_labels.update(e.get("metrics", {}).keys())

            agg: Dict[str, Any] = {}
            for label in sorted(metric_labels):
                # Gather scalar values per label across all samples.
                # Each sample may have multiple series (e.g. per-pod).
                # We aggregate the *sum* of all series per sample.
                sample_sums: List[float] = []
                for e in entries:
                    metric_items = e.get("metrics", {}).get(label, [])
                    total = sum(item.get("value", 0) for item in metric_items)
                    sample_sums.append(total)

                if sample_sums:
                    agg[label] = {
                        "mean": round(statistics.mean(sample_sums), 6),
                        "max": round(max(sample_sums), 6),
                        "min": round(min(sample_sums), 6),
                    }
                    if len(sample_sums) >= 2:
                        agg[label]["stdev"] = round(
                            statistics.stdev(sample_sums), 6,
                        )

            phase_summary["metrics"] = agg
            result[phase_name] = phase_summary

        return result

    # -- helpers ------------------------------------------------------------

    def _start_port_forward(
        self, svc_name: str, namespace: str, remote_port: int,
    ) -> Optional[str]:
        """Start ``kubectl port-forward`` and return a localhost URL.

        Returns *None* if the tunnel cannot be established within a few
        seconds.
        """
        local_port = _find_free_port()
        try:
            proc = subprocess.Popen(
                [
                    "kubectl", "port-forward",
                    f"svc/{svc_name}",
                    f"{local_port}:{remote_port}",
                    "-n", namespace,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.warning("kubectl not found — cannot port-forward to Prometheus")
            return None

        # Wait for the tunnel to become usable
        url = f"http://localhost:{local_port}"
        for _ in range(10):
            time.sleep(1)
            if proc.poll() is not None:
                # Process exited prematurely
                logger.warning("kubectl port-forward exited unexpectedly")
                return None
            if _check_prometheus_url(url, timeout=2):
                self._port_forward_procs.append(proc)
                return url

        # Timeout — clean up
        logger.warning("Port-forward to Prometheus did not become ready")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
        return None

    def _cleanup_port_forwards(self) -> None:
        """Terminate all port-forward subprocesses."""
        for proc in self._port_forward_procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                pass
        self._port_forward_procs.clear()
