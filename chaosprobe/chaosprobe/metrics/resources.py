"""Cluster-wide resource utilization measurement via Kubernetes Metrics API.

Polls ``metrics.k8s.io/v1beta1`` every *interval* seconds and captures CPU
(millicores) and memory (bytes) usage for **every node** in the cluster
plus every pod in the target namespace.  Per-tick output contains:

* ``nodes``        -- per-node breakdown (one entry per cluster node)
* ``node``         -- cluster-wide aggregate (sum for absolute units,
                      mean for percentages)
* ``nodeStats``    -- additional stddev / max across nodes
* ``pods``         -- per-pod usage for every pod in the namespace
* ``podAggregate`` -- total CPU / memory / count across the namespace
* ``targetPods``   -- subset of ``pods`` whose label ``app`` matches the
                      configured target deployment

Measurements are split across pre-chaos, during-chaos, and post-chaos
phases by :class:`chaosprobe.metrics.base.ContinuousProberBase`.
"""

import logging
import statistics
import time
from typing import Any, Dict, List, Optional

from kubernetes import client
from kubernetes.client.rest import ApiException

from chaosprobe.k8s import ensure_k8s_config
from chaosprobe.metrics.base import ContinuousProberBase

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


class ContinuousResourceProber(ContinuousProberBase):
    """Poll the Kubernetes Metrics API for cluster-wide resource usage.

    On every tick the prober queries *all* nodes and *all* pods in the
    configured namespace.  Per-node capacity (allocatable CPU and memory)
    is re-read on each cycle so pods / nodes that appear or disappear
    during chaos are tracked correctly.  If metrics-server is missing the
    prober disables itself gracefully.

    The ``deployment_name`` argument is retained so callers can tag the
    subset of pods belonging to the chaos target for downstream analysis
    (see ``targetPods`` in each tick's time-series entry).

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
        # node_name -> {"cpu_millicores": float, "memory_bytes": int}
        self._node_capacity: Dict[str, Dict[str, float]] = {}
        self._metrics_available: bool = True

        ensure_k8s_config()

        self._custom_api = client.CustomObjectsApi()
        self._core_api = client.CoreV1Api()

    # -- lifecycle overrides ------------------------------------------------

    def start(self) -> None:
        """Start cluster-wide resource probing."""
        self._refresh_node_capacity()
        if not self._node_capacity:
            logger.warning("No nodes discovered in cluster — resource probing disabled")
            self._metrics_available = False
            return

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

                # Re-discover node capacity so node churn during chaos is
                # reflected correctly (nodes added / drained / deleted).
                self._refresh_node_capacity()

                node_metrics = self._fetch_all_node_metrics()
                if node_metrics is None:
                    self._metrics_available = False
                    logger.warning("metrics-server became unavailable — stopping resource probing")
                    break

                entry["nodes"] = node_metrics

                pod_metrics = self._fetch_pod_metrics()
                entry["pods"] = pod_metrics
                entry["targetPods"] = [
                    p for p in pod_metrics if p.get("deployment") == self._deployment_name
                ]

                # Only aggregate nodes that host namespace pods.  This
                # avoids idle nodes diluting the mean — critical for
                # distinguishing colocate from spread.
                used_node_names = self._fetch_used_node_names()
                entry["usedNodeNames"] = sorted(used_node_names)
                used_node_metrics = [
                    n for n in node_metrics if n["name"] in used_node_names
                ]
                entry["usedNode"] = self._aggregate_node_metrics(used_node_metrics)
                entry["usedNodeStats"] = self._node_stats(used_node_metrics)

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
            errors = self._probe_errors

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

        # Build a sorted list of observed nodes and their last-seen capacity
        # so downstream analysis has a stable reference.
        node_capacity_snapshot = {
            name: {
                "cpu_millicores": cap["cpu_millicores"],
                "memory_bytes": cap["memory_bytes"],
            }
            for name, cap in sorted(self._node_capacity.items())
        }

        data: Dict[str, Any] = {
            "available": True,
            "nodeNames": sorted(self._node_capacity.keys()),
            "nodeCapacity": node_capacity_snapshot,
            "usedNodeNames": sorted(self._fetch_used_node_names()),
            "timeSeries": series,
            "phases": phases,
            "config": {
                "interval_s": self.interval,
                "namespace": self.namespace,
                "deploymentName": self._deployment_name,
            },
        }
        if errors > 0:
            data["probeErrors"] = errors
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

            phase_summary: Dict[str, Any] = {"sampleCount": len(entries)}
            used_node_agg = self._summarise_node_aggregate(entries)
            if used_node_agg:
                phase_summary["usedNode"] = used_node_agg
            per_node = self._summarise_per_node(entries)
            if per_node:
                phase_summary["perNode"] = per_node
            result[phase_name] = phase_summary

        return result

    @staticmethod
    def _summarise_node_aggregate(
        entries: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Reduce the per-tick used-node aggregate across a phase.

        Only nodes hosting namespace pods are included (``usedNode`` key),
        so idle nodes don't dilute the mean.
        """
        cpu_values = [e["usedNode"]["cpu_millicores"] for e in entries if "usedNode" in e]
        mem_values = [e["usedNode"]["memory_bytes"] for e in entries if "usedNode" in e]
        cpu_pct = [
            e["usedNode"]["cpu_percent"]
            for e in entries
            if "usedNode" in e and "cpu_percent" in e["usedNode"]
        ]
        mem_pct = [
            e["usedNode"]["memory_percent"]
            for e in entries
            if "usedNode" in e and "memory_percent" in e["usedNode"]
        ]
        max_cpu_pct_ticks = [
            e.get("usedNodeStats", {}).get("maxCpu_percent")
            for e in entries
            if e.get("usedNodeStats", {}).get("maxCpu_percent") is not None
        ]
        max_mem_pct_ticks = [
            e.get("usedNodeStats", {}).get("maxMemory_percent")
            for e in entries
            if e.get("usedNodeStats", {}).get("maxMemory_percent") is not None
        ]

        if not cpu_values:
            return {}

        summary: Dict[str, Any] = {
            "meanCpu_millicores": round(statistics.mean(cpu_values), 1),
            "maxCpu_millicores": round(max(cpu_values), 1),
            "meanMemory_bytes": round(statistics.mean(mem_values)),
            "maxMemory_bytes": max(mem_values),
        }
        if cpu_pct:
            summary["meanCpu_percent"] = round(statistics.mean(cpu_pct), 1)
            summary["maxCpu_percent"] = round(max(cpu_pct), 1)
            if len(cpu_pct) > 1:
                summary["stddevCpu_percent"] = round(statistics.stdev(cpu_pct), 2)
        if mem_pct:
            summary["meanMemory_percent"] = round(statistics.mean(mem_pct), 1)
            summary["maxMemory_percent"] = round(max(mem_pct), 1)
            if len(mem_pct) > 1:
                summary["stddevMemory_percent"] = round(statistics.stdev(mem_pct), 2)
        # Hottest node ever seen during the phase
        if max_cpu_pct_ticks:
            summary["peakNodeCpu_percent"] = round(max(max_cpu_pct_ticks), 1)
        if max_mem_pct_ticks:
            summary["peakNodeMemory_percent"] = round(max(max_mem_pct_ticks), 1)
        return summary

    @staticmethod
    def _summarise_per_node(
        entries: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Mean / max per node across the phase."""
        by_node: Dict[str, Dict[str, List[float]]] = {}
        for entry in entries:
            for nd in entry.get("nodes", []):
                name = nd.get("name")
                if not name:
                    continue
                bucket = by_node.setdefault(
                    name,
                    {"cpu_mc": [], "mem_b": [], "cpu_pct": [], "mem_pct": []},
                )
                bucket["cpu_mc"].append(nd["cpu_millicores"])
                bucket["mem_b"].append(nd["memory_bytes"])
                if "cpu_percent" in nd:
                    bucket["cpu_pct"].append(nd["cpu_percent"])
                if "memory_percent" in nd:
                    bucket["mem_pct"].append(nd["memory_percent"])

        result: Dict[str, Dict[str, Any]] = {}
        for name, series in by_node.items():
            node_summary: Dict[str, Any] = {
                "meanCpu_millicores": round(statistics.mean(series["cpu_mc"]), 1),
                "maxCpu_millicores": round(max(series["cpu_mc"]), 1),
                "meanMemory_bytes": round(statistics.mean(series["mem_b"])),
                "maxMemory_bytes": max(series["mem_b"]),
                "sampleCount": len(series["cpu_mc"]),
            }
            if series["cpu_pct"]:
                node_summary["meanCpu_percent"] = round(statistics.mean(series["cpu_pct"]), 1)
                node_summary["maxCpu_percent"] = round(max(series["cpu_pct"]), 1)
            if series["mem_pct"]:
                node_summary["meanMemory_percent"] = round(statistics.mean(series["mem_pct"]), 1)
                node_summary["maxMemory_percent"] = round(max(series["mem_pct"]), 1)
            result[name] = node_summary
        return result

    # -- helpers ------------------------------------------------------------

    def _refresh_node_capacity(self) -> None:
        """Re-list cluster nodes and refresh allocatable-capacity table.

        Nodes discovered during chaos are added; nodes removed from the
        cluster are dropped.  Called both at ``start()`` and on every
        probe tick so placement changes under chaos are reflected.
        """
        try:
            nodes = self._core_api.list_node()
        except ApiException as exc:
            logger.warning("list_node() failed: %s", exc)
            return

        fresh: Dict[str, Dict[str, float]] = {}
        for node in nodes.items:
            name = node.metadata.name
            alloc = node.status.allocatable or {}
            cpu = parse_cpu_quantity(alloc.get("cpu", "0"))
            mem = parse_memory_quantity(alloc.get("memory", "0"))
            fresh[name] = {"cpu_millicores": cpu, "memory_bytes": mem}
        self._node_capacity = fresh

    def _check_metrics_server(self) -> bool:
        """Test that metrics-server is reachable."""
        try:
            self._custom_api.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
            )
            return True
        except ApiException:
            return False

    def _fetch_used_node_names(self) -> set:
        """Return the set of node names that host at least one pod in the namespace."""
        if not hasattr(self, "_core_api"):
            return set()
        try:
            pods = self._core_api.list_namespaced_pod(
                self.namespace,
                field_selector="status.phase=Running",
            )
        except ApiException:
            return set()

        nodes = set()
        for pod in pods.items:
            node = pod.spec.node_name if pod.spec else None
            if node:
                nodes.add(node)
        return nodes

    def _fetch_all_node_metrics(self) -> Optional[List[Dict[str, Any]]]:
        """Fetch per-node CPU/memory usage for *every* cluster node.

        Returns ``None`` only when the Metrics API becomes unreachable
        (signals the probe loop to stop).  An empty list simply means
        no nodes were reported, which shouldn't happen in a healthy
        cluster but is not a fatal error.
        """
        try:
            listing = self._custom_api.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
            )
        except ApiException:
            return None

        result: List[Dict[str, Any]] = []
        for item in listing.get("items", []):
            name = item.get("metadata", {}).get("name", "")
            if not name:
                continue
            usage = item.get("usage", {})
            cpu = parse_cpu_quantity(usage.get("cpu", "0"))
            mem = parse_memory_quantity(usage.get("memory", "0"))

            node_entry: Dict[str, Any] = {
                "name": name,
                "cpu_millicores": round(cpu, 1),
                "memory_bytes": mem,
            }
            cap = self._node_capacity.get(name)
            if cap:
                if cap["cpu_millicores"] > 0:
                    node_entry["cpu_percent"] = round(cpu / cap["cpu_millicores"] * 100, 1)
                if cap["memory_bytes"] > 0:
                    node_entry["memory_percent"] = round(mem / cap["memory_bytes"] * 100, 1)
            result.append(node_entry)

        # Deterministic ordering so downstream diffs / snapshots are stable.
        result.sort(key=lambda n: n["name"])
        return result

    @staticmethod
    def _aggregate_node_metrics(
        node_metrics: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Reduce per-node metrics into a single cluster-wide entry.

        Absolute units (millicores, bytes) are summed — they represent
        total cluster usage.  Percentages are averaged — they represent
        mean node utilization.  Mixing those definitions in one dict is
        intentional; each key's unit makes its reduction unambiguous.
        """
        if not node_metrics:
            return {}
        total_cpu = sum(n["cpu_millicores"] for n in node_metrics)
        total_mem = sum(n["memory_bytes"] for n in node_metrics)
        entry: Dict[str, Any] = {
            "cpu_millicores": round(total_cpu, 1),
            "memory_bytes": total_mem,
            "nodeCount": len(node_metrics),
        }
        cpu_pcts = [n["cpu_percent"] for n in node_metrics if "cpu_percent" in n]
        mem_pcts = [n["memory_percent"] for n in node_metrics if "memory_percent" in n]
        if cpu_pcts:
            entry["cpu_percent"] = round(statistics.mean(cpu_pcts), 1)
        if mem_pcts:
            entry["memory_percent"] = round(statistics.mean(mem_pcts), 1)
        return entry

    @staticmethod
    def _node_stats(
        node_metrics: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Spread statistics across nodes within a single tick."""
        if not node_metrics:
            return {}
        cpu_pcts = [n["cpu_percent"] for n in node_metrics if "cpu_percent" in n]
        mem_pcts = [n["memory_percent"] for n in node_metrics if "memory_percent" in n]
        stats: Dict[str, Any] = {}
        if cpu_pcts:
            stats["maxCpu_percent"] = round(max(cpu_pcts), 1)
            stats["minCpu_percent"] = round(min(cpu_pcts), 1)
            if len(cpu_pcts) > 1:
                stats["stddevCpu_percent"] = round(statistics.stdev(cpu_pcts), 2)
        if mem_pcts:
            stats["maxMemory_percent"] = round(max(mem_pcts), 1)
            stats["minMemory_percent"] = round(min(mem_pcts), 1)
            if len(mem_pcts) > 1:
                stats["stddevMemory_percent"] = round(statistics.stdev(mem_pcts), 2)
        return stats

    def _fetch_pod_metrics(self) -> List[Dict[str, Any]]:
        """Fetch CPU/memory for every pod in the namespace.

        Each pod entry carries a ``deployment`` field (the ``app`` label)
        so callers can filter down to a specific deployment's pods.
        """
        try:
            pod_metrics_list = self._custom_api.list_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=self.namespace,
                plural="pods",
            )
        except ApiException:
            return []

        result = []
        for item in pod_metrics_list.get("items", []):
            metadata = item.get("metadata", {}) or {}
            pod_name = metadata.get("name", "unknown")
            labels = metadata.get("labels", {}) or {}
            app_label = labels.get("app")
            total_cpu = 0.0
            total_mem = 0
            for container in item.get("containers", []):
                usage = container.get("usage", {})
                total_cpu += parse_cpu_quantity(usage.get("cpu", "0"))
                total_mem += parse_memory_quantity(usage.get("memory", "0"))
            result.append(
                {
                    "pod": pod_name,
                    "deployment": app_label,
                    "cpu_millicores": round(total_cpu, 1),
                    "memory_bytes": total_mem,
                }
            )
        result.sort(key=lambda p: p["pod"])
        return result
