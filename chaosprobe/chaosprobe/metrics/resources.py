"""Node and pod resource utilization measurement via Kubernetes Metrics API.

Polls metrics.k8s.io/v1beta1 to capture CPU and memory utilization for the
node hosting the target deployment and for the deployment's pods.  Measurements
are split across pre-chaos, during-chaos, and post-chaos phases.
"""

import logging
import statistics
import time
from typing import Any, Dict, List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from chaosprobe.metrics.throughput import _ContinuousProberBase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Kubernetes resource quantity parsers
# ---------------------------------------------------------------------------


def parse_cpu_quantity(value: str) -> float:
    """Parse a Kubernetes CPU quantity to millicores.

    Examples::

        parse_cpu_quantity("2")       -> 2000.0
        parse_cpu_quantity("412m")    -> 412.0
        parse_cpu_quantity("500000n") -> 0.5
        parse_cpu_quantity("196250u") -> 196.25
    """
    value = value.strip()
    if value.endswith("n"):
        return float(value[:-1]) / 1_000_000
    if value.endswith("u"):
        return float(value[:-1]) / 1_000
    if value.endswith("m"):
        return float(value[:-1])
    return float(value) * 1000


def parse_memory_quantity(value: str) -> int:
    """Parse a Kubernetes memory quantity to bytes.

    Examples::

        parse_memory_quantity("1823456Ki") -> 1867218944
        parse_memory_quantity("2Gi")       -> 2147483648
        parse_memory_quantity("1024Mi")    -> 1073741824
        parse_memory_quantity("1073741824") -> 1073741824
    """
    value = value.strip()
    suffixes = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "k": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
    }
    for suffix, multiplier in suffixes.items():
        if value.endswith(suffix):
            return int(float(value[: -len(suffix)]) * multiplier)
    return int(value)


# ---------------------------------------------------------------------------
# Continuous resource prober
# ---------------------------------------------------------------------------


class ContinuousResourceProber(_ContinuousProberBase):
    """Polls Kubernetes Metrics API for node and pod resource utilization.

    Captures CPU (millicores) and memory (bytes) for the node hosting the
    target deployment and for each pod of that deployment.  If the
    metrics-server is not deployed the prober disables itself gracefully.

    Usage::

        prober = ContinuousResourceProber("online-boutique", "checkoutservice")
        prober.start()
        # ... run chaos experiment ...
        prober.mark_chaos_start()
        # ... chaos runs ...
        prober.mark_chaos_end()
        prober.stop()
        data = prober.result()
    """

    def __init__(
        self,
        namespace: str,
        deployment_name: str,
        interval: float = 5.0,
    ):
        super().__init__(namespace, interval, name="resource-prober")
        self._deployment_name = deployment_name
        self._node_name: Optional[str] = None
        self._node_capacity_cpu: Optional[float] = None  # millicores
        self._node_capacity_mem: Optional[int] = None  # bytes
        self._metrics_available: bool = True

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self._custom_api = client.CustomObjectsApi()
        self._core_api = client.CoreV1Api()

    # -- lifecycle overrides ------------------------------------------------

    def start(self) -> None:
        """Start resource probing.  Discovers node and checks metrics-server."""
        self._node_name = self._discover_node_name()
        if not self._node_name:
            logger.warning(
                "Could not discover node for %s — resource probing disabled",
                self._deployment_name,
            )
            self._metrics_available = False
            return

        self._read_node_capacity()

        if not self._check_metrics_server():
            logger.warning("metrics-server not available — resource probing disabled")
            self._metrics_available = False
            return

        super().start()

    # -- probe loop ---------------------------------------------------------

    def _probe_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._metrics_available:
                break

            try:
                now = time.time()
                phase = self._current_phase(now)
                entry = self._make_entry(now, phase)

                node_metrics = self._fetch_node_metrics()
                if node_metrics is None:
                    self._metrics_available = False
                    logger.warning("metrics-server became unavailable — stopping resource probing")
                    break
                entry["node"] = node_metrics

                pod_metrics = self._fetch_pod_metrics()
                entry["pods"] = pod_metrics
                if pod_metrics:
                    entry["podAggregate"] = {
                        "totalCpu_millicores": round(
                            sum(p["cpu_millicores"] for p in pod_metrics), 1
                        ),
                        "totalMemory_bytes": sum(p["memory_bytes"] for p in pod_metrics),
                        "podCount": len(pod_metrics),
                    }

                with self._lock:
                    self._time_series.append(entry)

            except Exception as exc:
                logger.warning("Resource probe failed: %s", exc)
                with self._lock:
                    self._probe_errors += 1

            self._stop_event.wait(timeout=self.interval)

    # -- result -------------------------------------------------------------

    def result(self) -> Dict[str, Any]:
        """Return structured resource utilization data."""
        with self._lock:
            series = list(self._time_series)

        if not series:
            return {
                "available": False,
                "reason": (
                    "metrics-server not available"
                    if not self._metrics_available
                    else "no data collected"
                ),
            }

        phases = self._split_phases(series)

        data: Dict[str, Any] = {
            "available": True,
            "nodeName": self._node_name,
            "nodeCapacity": {
                "cpu_millicores": self._node_capacity_cpu,
                "memory_bytes": self._node_capacity_mem,
            },
            "timeSeries": series,
            "phases": phases,
            "config": {
                "interval_s": self.interval,
                "namespace": self.namespace,
                "deploymentName": self._deployment_name,
            },
        }
        if self._probe_errors > 0:
            data["probeErrors"] = self._probe_errors
        return data

    # -- phase aggregation --------------------------------------------------

    def _split_phases(self, series: List[Dict[str, Any]]) -> Dict[str, Any]:
        phases: Dict[str, List[Dict[str, Any]]] = {
            "pre-chaos": [],
            "during-chaos": [],
            "post-chaos": [],
        }
        for entry in series:
            phases.setdefault(entry.get("phase", "pre-chaos"), []).append(entry)

        result: Dict[str, Any] = {}
        for phase_name, entries in phases.items():
            if not entries:
                result[phase_name] = {"sampleCount": 0}
                continue

            cpu_values = [e["node"]["cpu_millicores"] for e in entries if "node" in e]
            mem_values = [e["node"]["memory_bytes"] for e in entries if "node" in e]
            cpu_pct = [
                e["node"]["cpu_percent"]
                for e in entries
                if "node" in e and "cpu_percent" in e["node"]
            ]
            mem_pct = [
                e["node"]["memory_percent"]
                for e in entries
                if "node" in e and "memory_percent" in e["node"]
            ]

            phase_summary: Dict[str, Any] = {"sampleCount": len(entries)}

            if cpu_values:
                node_summary: Dict[str, Any] = {
                    "meanCpu_millicores": round(statistics.mean(cpu_values), 1),
                    "maxCpu_millicores": round(max(cpu_values), 1),
                    "meanMemory_bytes": round(statistics.mean(mem_values)),
                    "maxMemory_bytes": max(mem_values),
                }
                if cpu_pct:
                    node_summary["meanCpu_percent"] = round(statistics.mean(cpu_pct), 1)
                    node_summary["maxCpu_percent"] = round(max(cpu_pct), 1)
                if mem_pct:
                    node_summary["meanMemory_percent"] = round(statistics.mean(mem_pct), 1)
                    node_summary["maxMemory_percent"] = round(max(mem_pct), 1)
                phase_summary["node"] = node_summary

            result[phase_name] = phase_summary

        return result

    # -- helpers ------------------------------------------------------------

    def _discover_node_name(self) -> Optional[str]:
        """Find the node hosting the target deployment's pods."""
        try:
            pods = self._core_api.list_namespaced_pod(
                self.namespace,
                label_selector=f"app={self._deployment_name}",
            )
        except ApiException:
            return None

        for pod in pods.items:
            if pod.spec.node_name:
                return pod.spec.node_name
        return None

    def _read_node_capacity(self) -> None:
        """Read node allocatable CPU/memory for percentage calculations."""
        if not self._node_name:
            return
        try:
            node = self._core_api.read_node(self._node_name)
            alloc = node.status.allocatable or {}
            self._node_capacity_cpu = parse_cpu_quantity(alloc.get("cpu", "0"))
            self._node_capacity_mem = parse_memory_quantity(alloc.get("memory", "0"))
        except ApiException:
            pass

    def _check_metrics_server(self) -> bool:
        """Test that metrics-server is reachable."""
        try:
            self._custom_api.get_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
                name=self._node_name,
            )
            return True
        except ApiException:
            return False

    def _fetch_node_metrics(self) -> Optional[Dict[str, Any]]:
        """Fetch current node CPU/memory usage from the Metrics API."""
        try:
            metrics = self._custom_api.get_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
                name=self._node_name,
            )
        except ApiException:
            return None

        usage = metrics.get("usage", {})
        cpu = parse_cpu_quantity(usage.get("cpu", "0"))
        mem = parse_memory_quantity(usage.get("memory", "0"))

        result: Dict[str, Any] = {
            "cpu_millicores": round(cpu, 1),
            "memory_bytes": mem,
        }
        if self._node_capacity_cpu and self._node_capacity_cpu > 0:
            result["cpu_percent"] = round(cpu / self._node_capacity_cpu * 100, 1)
        if self._node_capacity_mem and self._node_capacity_mem > 0:
            result["memory_percent"] = round(mem / self._node_capacity_mem * 100, 1)
        return result

    def _fetch_pod_metrics(self) -> List[Dict[str, Any]]:
        """Fetch CPU/memory for all pods of the target deployment."""
        try:
            pod_metrics_list = self._custom_api.list_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=self.namespace,
                plural="pods",
                label_selector=f"app={self._deployment_name}",
            )
        except ApiException:
            return []

        result = []
        for item in pod_metrics_list.get("items", []):
            pod_name = item.get("metadata", {}).get("name", "unknown")
            total_cpu = 0.0
            total_mem = 0
            for container in item.get("containers", []):
                usage = container.get("usage", {})
                total_cpu += parse_cpu_quantity(usage.get("cpu", "0"))
                total_mem += parse_memory_quantity(usage.get("memory", "0"))
            result.append(
                {
                    "pod": pod_name,
                    "cpu_millicores": round(total_cpu, 1),
                    "memory_bytes": total_mem,
                }
            )
        return result
