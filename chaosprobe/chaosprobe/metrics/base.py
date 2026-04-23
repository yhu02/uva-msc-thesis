"""Base class for continuous probers with phase tracking.

Provides a reusable ``ContinuousProberBase`` that handles thread
lifecycle (start/stop), chaos phase boundaries, and time-series
collection.  Subclasses only need to implement ``_probe_loop``.

Also provides shared Kubernetes pod helper functions used by multiple
metric probers (latency, throughput, resources).
"""

import logging
import statistics
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared pod helper functions
# ---------------------------------------------------------------------------

def find_ready_pod(core_api: Any, namespace: str, service_name: str) -> Optional[str]:
    """Find a ready pod for a given service by label ``app=<service_name>``."""
    try:
        pods = core_api.list_namespaced_pod(
            namespace,
            label_selector=f"app={service_name}",
            field_selector="status.phase=Running",
        )
    except Exception:
        return None

    for pod in pods.items:
        if pod.status.conditions:
            for cond in pod.status.conditions:
                if cond.type == "Ready" and cond.status == "True":
                    return pod.metadata.name
    return None


def pod_has_shell(core_api: Any, namespace: str, pod_name: str) -> bool:
    """Quick check whether *pod_name* has a usable shell."""
    try:
        from kubernetes.stream import stream

        out = stream(
            core_api.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=["sh", "-c", "echo ok"],
            stderr=True,
            stdout=True,
            stdin=False,
            tty=False,
        )
        return "ok" in out
    except Exception:
        return False


def _pod_has_python3(core_api: Any, namespace: str, pod_name: str) -> bool:
    """Check whether *pod_name* has a working ``python3`` interpreter."""
    try:
        from kubernetes.stream import stream

        out = stream(
            core_api.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=["python3", "-c", "print('ok')"],
            stderr=True,
            stdout=True,
            stdin=False,
            tty=False,
        )
        return "ok" in out
    except Exception:
        return False


def find_probe_pod(
    core_api: Any,
    namespace: str,
    require_python3: bool = False,
    exclude_prefixes: Optional[List[str]] = None,
) -> Optional[str]:
    """Find a pod suitable for running probe commands.

    Discovers running pods in the namespace and tests whether they have
    a usable shell.  When *require_python3* is True the selected pod
    must also have a working ``python3`` interpreter.  If no pod satisfies
    that constraint, falls back to any pod with a shell.

    Pods matching *exclude_prefixes* (e.g. the chaos target deployment)
    are skipped — they may be killed during experiments.

    Pods are checked in alphabetical order; the first pod with a shell
    (and python3 if required) is returned.
    """
    try:
        pods = core_api.list_namespaced_pod(
            namespace,
            field_selector="status.phase=Running",
        )
    except Exception:
        return None

    _exclude = tuple(exclude_prefixes) if exclude_prefixes else ()

    ready_pods: List[str] = []

    for pod in pods.items:
        if not pod.status.conditions:
            continue
        is_ready = any(
            c.type == "Ready" and c.status == "True"
            for c in pod.status.conditions
        )
        if not is_ready:
            continue
        name = pod.metadata.name
        if _exclude and name.startswith(_exclude):
            continue
        ready_pods.append(name)

    # Deterministic order for reproducibility
    ready_pods.sort()

    shell_pods: List[str] = []
    for name in ready_pods:
        if pod_has_shell(core_api, namespace, name):
            if require_python3:
                if _pod_has_python3(core_api, namespace, name):
                    return name
                shell_pods.append(name)  # remember for fallback
            else:
                return name

    # Fallback: any pod with a shell (even without python3)
    if require_python3 and shell_pods:
        logger.warning(
            "No pod with python3 found; falling back to %s",
            shell_pods[0],
        )
        return shell_pods[0]
    return None


def find_probe_pods_per_node(
    core_api: Any,
    namespace: str,
    require_python3: bool = False,
    exclude_prefixes: Optional[List[str]] = None,
) -> List[tuple]:
    """Return ``[(pod_name, node_name), ...]`` with at most one pod per node.

    Placement strategies distribute pods across nodes differently; a
    per-node sample set reveals whether load or impact concentrates on
    specific nodes.  Single-pod probing cannot distinguish ``colocate``
    from ``spread`` because it only ever observes one node.

    Ready pods are grouped by ``spec.nodeName``.  Within each node the
    first pod (alphabetically) with a working shell (and ``python3`` if
    *require_python3* is set) is selected.  Nodes where no suitable pod
    is found are silently omitted.  Pods whose names start with any
    entry in *exclude_prefixes* are skipped.
    """
    try:
        pods = core_api.list_namespaced_pod(
            namespace,
            field_selector="status.phase=Running",
        )
    except Exception:
        return []

    _exclude = tuple(exclude_prefixes) if exclude_prefixes else ()

    by_node: Dict[str, List[str]] = {}
    for pod in pods.items:
        if not pod.status.conditions:
            continue
        is_ready = any(
            c.type == "Ready" and c.status == "True"
            for c in pod.status.conditions
        )
        if not is_ready:
            continue
        name = pod.metadata.name
        if _exclude and name.startswith(_exclude):
            continue
        node = pod.spec.node_name if pod.spec else None
        if not node:
            continue
        by_node.setdefault(node, []).append(name)

    result: List[tuple] = []
    for node in sorted(by_node.keys()):
        for pod_name in sorted(by_node[node]):
            if not pod_has_shell(core_api, namespace, pod_name):
                continue
            if require_python3 and not _pod_has_python3(core_api, namespace, pod_name):
                continue
            result.append((pod_name, node))
            break
    return result


def find_all_probe_pods_with_node(
    core_api: Any,
    namespace: str,
    require_python3: bool = False,
    exclude_prefixes: Optional[List[str]] = None,
) -> List[tuple]:
    """Return ``[(pod_name, node_name), ...]`` for every ready probe-capable pod.

    Same filtering as :func:`find_all_probe_pods` (ready, has shell,
    optional python3) but also carries the scheduling node so callers
    can group samples by node for placement analysis.  Pods without an
    assigned node are skipped.
    """
    try:
        pods = core_api.list_namespaced_pod(
            namespace,
            field_selector="status.phase=Running",
        )
    except Exception:
        return []

    _exclude = tuple(exclude_prefixes) if exclude_prefixes else ()

    ready: List[tuple] = []
    for pod in pods.items:
        if not pod.status.conditions:
            continue
        is_ready = any(
            c.type == "Ready" and c.status == "True"
            for c in pod.status.conditions
        )
        if not is_ready:
            continue
        name = pod.metadata.name
        if _exclude and name.startswith(_exclude):
            continue
        node = pod.spec.node_name if pod.spec else None
        if not node:
            continue
        ready.append((name, node))

    ready.sort()

    result: List[tuple] = []
    for name, node in ready:
        if not pod_has_shell(core_api, namespace, name):
            continue
        if require_python3 and not _pod_has_python3(core_api, namespace, name):
            continue
        result.append((name, node))
    return result


def find_all_probe_pods(
    core_api: Any,
    namespace: str,
    require_python3: bool = False,
    exclude_prefixes: Optional[List[str]] = None,
) -> List[str]:
    """Return all pods suitable for probing, in alphabetical order.

    Unlike ``find_probe_pod`` which returns the first match, this
    returns *all* candidates so callers can iterate through them
    when the first pod fails a connectivity pre-flight check.
    """
    try:
        pods = core_api.list_namespaced_pod(
            namespace,
            field_selector="status.phase=Running",
        )
    except Exception:
        return []

    _exclude = tuple(exclude_prefixes) if exclude_prefixes else ()

    ready_pods: List[str] = []
    for pod in pods.items:
        if not pod.status.conditions:
            continue
        is_ready = any(
            c.type == "Ready" and c.status == "True"
            for c in pod.status.conditions
        )
        if not is_ready:
            continue
        name = pod.metadata.name
        if _exclude and name.startswith(_exclude):
            continue
        ready_pods.append(name)

    ready_pods.sort()

    result: List[str] = []
    for name in ready_pods:
        if pod_has_shell(core_api, namespace, name):
            if require_python3:
                if _pod_has_python3(core_api, namespace, name):
                    result.append(name)
            else:
                result.append(name)
    return result


def exec_in_pod(core_api: Any, namespace: str, pod_name: str, command: List[str]) -> str:
    """Execute a command inside a pod and return stdout."""
    try:
        from kubernetes.stream import stream

        return stream(
            core_api.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=True,
        )
    except Exception as e:
        return f"ERROR:{e}"


class ContinuousProberBase:
    """Thread-safe base for continuous measurement probers.

    Handles start/stop, chaos-phase markers (pre-chaos, during-chaos,
    post-chaos), and basic time-series bookkeeping.

    Subclasses must implement ``_probe_loop()`` which runs in a
    background thread.  Call ``self._stop_event.wait(timeout=…)``
    between probe cycles to respect the stop signal.
    """

    def __init__(self, namespace: str, interval: float, name: str):
        self.namespace = namespace
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._time_series: List[Dict[str, Any]] = []
        self._start_time: Optional[float] = None
        self._chaos_start_time: Optional[float] = None
        self._chaos_end_time: Optional[float] = None
        self._expected_chaos_duration: Optional[float] = None
        self._post_chaos_buffer: float = 15.0  # seconds; default, can be overridden
        self._probe_errors: int = 0
        self._thread_name = name

    def start(self) -> None:
        self._start_time = time.time()
        self._thread = threading.Thread(
            target=self._probe_loop,
            daemon=True,
            name=self._thread_name,
        )
        self._thread.start()

    def mark_chaos_start(self) -> None:
        with self._lock:
            self._chaos_start_time = time.time()

    def mark_chaos_end(self) -> None:
        with self._lock:
            self._chaos_end_time = time.time()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=30)

    def _current_phase(self, now: float) -> str:
        with self._lock:
            chaos_start = self._chaos_start_time
            chaos_end = self._chaos_end_time
        if chaos_start is None or now < chaos_start:
            return "pre-chaos"
        if chaos_end is not None and now >= chaos_end:
            return "post-chaos"
        # Cap: if expected duration is set and exceeded, treat as post-chaos.
        # Use a dynamic buffer that scales with the expected chaos duration
        # (at least 15s, at most 30s) to account for variable recovery times.
        if self._expected_chaos_duration is not None:
            buffer = max(self._post_chaos_buffer, self._expected_chaos_duration * 0.15)
            buffer = min(buffer, 30.0)
            if now >= chaos_start + self._expected_chaos_duration + buffer:
                return "post-chaos"
        return "during-chaos"

    def _make_entry(self, now: float, phase: str) -> Dict[str, Any]:
        return {
            "timestamp": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "elapsed_s": round(now - (self._start_time or now), 1),
            "phase": phase,
        }

    def _probe_loop(self) -> None:
        raise NotImplementedError

    def _split_phases(self, series: List[Dict[str, Any]], metric_key: str) -> Dict[str, Any]:
        phases: Dict[str, List[Dict[str, Any]]] = {
            "pre-chaos": [],
            "during-chaos": [],
            "post-chaos": [],
        }
        for entry in series:
            phases.setdefault(entry.get("phase", "pre-chaos"), []).append(entry)
        result = {}
        for name, entries in phases.items():
            if not entries:
                result[name] = {"sampleCount": 0, metric_key: {}}
            else:
                result[name] = {
                    "sampleCount": len(entries),
                    metric_key: self._aggregate_operations(entries, metric_key),
                }
        return result

    @staticmethod
    def _aggregate_operations(
        entries: List[Dict[str, Any]],
        key: str,
    ) -> Dict[str, Any]:
        """Aggregate throughput metrics for a given key across entries.

        Tick-level cross-node variance (``stddevOpsPerSecond``,
        ``minOpsPerSecond``, ``maxOpsPerSecond``) is propagated into the
        phase summary as ``meanCrossNodeStddevOpsPerSecond`` (average
        cross-node spread over the phase) and ``peakMaxOpsPerSecond`` /
        ``worstMinOpsPerSecond`` (best/worst single-node reading ever
        observed in the phase).  Without this, the placement-strategy
        signal collected at tick-level is lost before reaching consumers.
        """
        ops_by_op: Dict[str, List[float]] = {}
        lat_by_op: Dict[str, List[float]] = {}
        bps_by_op: Dict[str, List[float]] = {}
        err_by_op: Dict[str, int] = {}
        stddev_by_op: Dict[str, List[float]] = {}
        tick_min_by_op: Dict[str, List[float]] = {}
        tick_max_by_op: Dict[str, List[float]] = {}

        for entry in entries:
            for op, data in entry.get(key, {}).items():
                ops_by_op.setdefault(op, [])
                lat_by_op.setdefault(op, [])
                bps_by_op.setdefault(op, [])
                err_by_op.setdefault(op, 0)
                stddev_by_op.setdefault(op, [])
                tick_min_by_op.setdefault(op, [])
                tick_max_by_op.setdefault(op, [])
                if data.get("ops_per_second") is not None:
                    ops_by_op[op].append(data["ops_per_second"])
                if data.get("latency_ms") is not None:
                    lat_by_op[op].append(data["latency_ms"])
                if data.get("bytes_per_second") is not None:
                    bps_by_op[op].append(data["bytes_per_second"])
                if data.get("status") != "ok":
                    err_by_op[op] += 1
                if data.get("stddevOpsPerSecond") is not None:
                    stddev_by_op[op].append(data["stddevOpsPerSecond"])
                if data.get("minOpsPerSecond") is not None:
                    tick_min_by_op[op].append(data["minOpsPerSecond"])
                if data.get("maxOpsPerSecond") is not None:
                    tick_max_by_op[op].append(data["maxOpsPerSecond"])

        summary = {}
        for op in ops_by_op:
            ops = ops_by_op[op]
            lats = lat_by_op.get(op, [])
            bps = bps_by_op.get(op, [])
            errs = err_by_op.get(op, 0)
            if ops:
                row = {
                    "meanOpsPerSecond": round(statistics.mean(ops), 2),
                    "medianOpsPerSecond": round(statistics.median(ops), 2),
                    "minOpsPerSecond": round(min(ops), 2),
                    "maxOpsPerSecond": round(max(ops), 2),
                    "meanLatency_ms": round(statistics.mean(lats), 2) if lats else None,
                    "meanBytesPerSecond": round(statistics.mean(bps), 2) if bps else None,
                    "sampleCount": len(ops),
                    "errorCount": errs,
                }
                tick_stddevs = stddev_by_op.get(op, [])
                if tick_stddevs:
                    row["meanCrossNodeStddevOpsPerSecond"] = round(
                        statistics.mean(tick_stddevs), 2,
                    )
                tick_maxes = tick_max_by_op.get(op, [])
                if tick_maxes:
                    row["peakMaxOpsPerSecond"] = round(max(tick_maxes), 2)
                tick_mins = tick_min_by_op.get(op, [])
                if tick_mins:
                    row["worstMinOpsPerSecond"] = round(min(tick_mins), 2)
                summary[op] = row
            else:
                summary[op] = {"meanOpsPerSecond": None, "sampleCount": 0, "errorCount": errs}
        return summary
