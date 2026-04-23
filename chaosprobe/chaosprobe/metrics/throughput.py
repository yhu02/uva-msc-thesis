"""Throughput measurement for database and disk I/O operations.

Measures read/write throughput for:
- Redis (database) operations via the cartservice/redis-cart
- Disk I/O operations within pods

Runs benchmark commands inside pods via kubectl exec and collects
operations-per-second and latency statistics.
"""

import logging
import re
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes import client

from chaosprobe.k8s import ensure_k8s_config
from chaosprobe.metrics.base import (
    ContinuousProberBase,
    find_probe_pods_per_node,
    find_ready_pod,
)
from chaosprobe.metrics.base import (
    exec_in_pod as _base_exec_in_pod,
)

logger = logging.getLogger(__name__)


# Matches the elapsed-time field in dd's stderr summary line.
# GNU dd:     "262144 bytes (262 kB, 256 KiB) copied, 0.00213 s, 123 MB/s"
# busybox dd: "262144 bytes (256.0KB) copied, 0.000876 seconds, 285.0MB/s"
# The `s` after the float matches both "s," and the start of "seconds,".
_DD_ELAPSED_RE = re.compile(r"copied,\s*([0-9.eE+-]+)\s*s")


def _parse_dd_elapsed_seconds(output: str) -> Optional[float]:
    """Extract the elapsed-seconds value from a dd stderr summary line.

    Returns ``None`` if no recognisable summary line is present (e.g.
    dd failed before printing its report).
    """
    m = _DD_ELAPSED_RE.search(output)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _aggregate_disk_samples(
    per_pod_samples: List[tuple],
) -> Dict[str, Any]:
    """Aggregate per-pod disk samples into a single tick-level record.

    Parameters
    ----------
    per_pod_samples:
        ``[(pod_name, node_name, ThroughputSample), ...]`` — one entry
        per probed node.

    Returns a dict shaped for placement analysis with flat aggregate
    fields (``ops_per_second`` = mean across nodes) and cross-node
    distribution (``stddev``/``min``/``max`` + ``perNode`` breakdown).
    When every sample failed, the flat fields are ``None`` and
    ``status='error'``.
    """
    ok = [(p, n, s) for p, n, s in per_pod_samples if s.status == "ok"]
    err = [(p, n, s) for p, n, s in per_pod_samples if s.status != "ok"]

    per_node: Dict[str, Dict[str, Any]] = {}
    for pod, node, s in per_pod_samples:
        per_node[node] = {
            "pod": pod,
            "ops_per_second": s.ops_per_second if s.status == "ok" else None,
            "latency_ms": s.latency_ms if s.status == "ok" else None,
            "bytes_per_second": s.bytes_per_second if s.status == "ok" else None,
            "status": s.status,
        }
        if s.status != "ok" and s.error:
            per_node[node]["error"] = s.error[:200]

    if not ok:
        entry: Dict[str, Any] = {
            "ops_per_second": None,
            "latency_ms": None,
            "bytes_per_second": None,
            "status": "error",
            "probeCount": 0,
            "errorCount": len(err),
            "perNode": per_node,
        }
        # Surface one representative error for quick diagnosis.
        if err and err[0][2].error:
            entry["error"] = err[0][2].error[:200]
        return entry

    ops = [s.ops_per_second for _, _, s in ok]
    lats = [s.latency_ms for _, _, s in ok]
    bps_list = [s.bytes_per_second for _, _, s in ok if s.bytes_per_second is not None]

    return {
        "ops_per_second": round(statistics.mean(ops), 2),
        "latency_ms": round(statistics.mean(lats), 4),
        "bytes_per_second": (
            round(statistics.mean(bps_list), 2) if bps_list else None
        ),
        "status": "ok",
        "probeCount": len(ok),
        "errorCount": len(err),
        "minOpsPerSecond": round(min(ops), 2),
        "maxOpsPerSecond": round(max(ops), 2),
        "stddevOpsPerSecond": (
            round(statistics.stdev(ops), 2) if len(ops) > 1 else 0.0
        ),
        "perNode": per_node,
    }


@dataclass
class ThroughputSample:
    """A single throughput measurement."""

    operation: str  # "read", "write", "mixed"
    target: str  # "redis", "disk"
    ops_per_second: float
    latency_ms: float  # per-operation latency
    bytes_per_second: Optional[float] = None
    status: str = "ok"  # "ok", "error"
    timestamp: str = ""
    error: Optional[str] = None


@dataclass
class ThroughputResult:
    """Aggregated throughput results for a measurement series."""

    target: str
    operation: str
    description: str
    samples: List[ThroughputSample] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        ok_samples = [s for s in self.samples if s.status == "ok"]
        error_count = len(self.samples) - len(ok_samples)

        if not ok_samples:
            return {
                "target": self.target,
                "operation": self.operation,
                "description": self.description,
                "sampleCount": len(self.samples),
                "errorCount": error_count,
                "meanOpsPerSecond": None,
                "meanLatency_ms": None,
                "meanBytesPerSecond": None,
                "minOpsPerSecond": None,
                "maxOpsPerSecond": None,
            }

        ops_list = [s.ops_per_second for s in ok_samples]
        lat_list = [s.latency_ms for s in ok_samples]
        bps_list = [s.bytes_per_second for s in ok_samples if s.bytes_per_second is not None]

        return {
            "target": self.target,
            "operation": self.operation,
            "description": self.description,
            "sampleCount": len(self.samples),
            "errorCount": error_count,
            "meanOpsPerSecond": round(statistics.mean(ops_list), 2),
            "medianOpsPerSecond": round(statistics.median(ops_list), 2),
            "minOpsPerSecond": round(min(ops_list), 2),
            "maxOpsPerSecond": round(max(ops_list), 2),
            "meanLatency_ms": round(statistics.mean(lat_list), 2),
            "p95Latency_ms": round(
                sorted(lat_list)[min(int(len(lat_list) * 0.95), len(lat_list) - 1)], 2
            ),
            "meanBytesPerSecond": round(statistics.mean(bps_list), 2) if bps_list else None,
        }


class ThroughputProber:
    """Measures database and disk throughput within Kubernetes pods.

    For Redis: Uses redis-cli PING, SET/GET benchmarks executed from
    the cartservice pod (or any pod with network access to redis).

    For Disk: Uses dd commands inside target pods to measure sequential
    read/write speed.

    Usage::

        prober = ThroughputProber("online-boutique")
        results = prober.measure_all(samples=5)
        for r in results:
            print(r.summary())
    """

    def __init__(self, namespace: str, timeout_seconds: int = 30):
        self.namespace = namespace
        self.timeout_seconds = timeout_seconds

        ensure_k8s_config()

        self.core_api = client.CoreV1Api()
        self._exec_pod_cache: Dict[str, Optional[str]] = {}
        self._cache_lock = threading.Lock()

    def measure_redis_throughput(
        self,
        redis_host: str = "redis-cart",
        redis_port: int = 6379,
        samples: int = 5,
        ops_per_sample: int = 1000,
    ) -> List[ThroughputResult]:
        """Measure Redis read/write throughput from a pod.

        Executes redis-benchmark style operations by running SET/GET
        commands in a loop from inside the redis pod.

        Args:
            redis_host: Redis service hostname.
            redis_port: Redis service port.
            samples: Number of benchmark rounds.
            ops_per_sample: Operations per round.

        Returns:
            List of ThroughputResult for write and read operations.
        """
        redis_pod = self._find_ready_pod(redis_host)
        if not redis_pod:
            return []

        write_result = ThroughputResult(
            target="redis",
            operation="write",
            description=f"Redis SET ({ops_per_sample} keys per sample)",
        )
        read_result = ThroughputResult(
            target="redis",
            operation="read",
            description=f"Redis GET ({ops_per_sample} keys per sample)",
        )

        for _i in range(samples):
            # Write benchmark
            w_sample = self._redis_benchmark(
                redis_pod, "write", ops_per_sample, redis_host, redis_port
            )
            write_result.samples.append(w_sample)

            # Read benchmark
            r_sample = self._redis_benchmark(
                redis_pod, "read", ops_per_sample, redis_host, redis_port
            )
            read_result.samples.append(r_sample)

        return [write_result, read_result]

    def measure_disk_throughput(
        self,
        target_service: str = "redis-cart",
        samples: int = 5,
        block_size_kb: int = 1024,
        count: int = 10,
        disk_path: str = "/tmp/chaosprobe_disktest",
        exclude_services: Optional[List[str]] = None,
    ) -> List[ThroughputResult]:
        """Measure disk write/read throughput inside a pod.

        Uses dd to write and read blocks, measuring sequential I/O speed.

        Args:
            target_service: Service whose pod to benchmark. Defaults to
                redis-cart since many microservice pods are distroless.
            samples: Number of benchmark rounds.
            block_size_kb: Block size in KB for dd.
            count: Number of blocks per dd operation.
            disk_path: Full path for the test file inside the pod.
            exclude_services: Service names to skip during pod discovery.

        Returns:
            List of ThroughputResult for disk write and read.
        """
        pod = self._find_exec_pod(target_service, exclude_services=exclude_services)
        if not pod:
            return []

        write_result = ThroughputResult(
            target="disk",
            operation="write",
            description=f"Sequential disk write ({block_size_kb}KB x {count} blocks)",
        )
        read_result = ThroughputResult(
            target="disk",
            operation="read",
            description=f"Sequential disk read ({block_size_kb}KB x {count} blocks)",
        )

        for _i in range(samples):
            w_sample = self._disk_benchmark(pod, "write", block_size_kb, count, disk_path)
            write_result.samples.append(w_sample)

            r_sample = self._disk_benchmark(pod, "read", block_size_kb, count, disk_path)
            read_result.samples.append(r_sample)

        # Clean up test file
        self._exec_in_pod(
            pod, ["sh", "-c", f"rm -f {disk_path} 2>/dev/null; echo done"]
        )

        return [write_result, read_result]

    def measure_all(
        self,
        samples: int = 5,
        ops_per_sample: int = 1000,
        disk_target: str = "redis-cart",
        disk_block_kb: int = 1024,
        disk_count: int = 10,
    ) -> Dict[str, Any]:
        """Run all throughput benchmarks.

        Args:
            samples: Number of samples per measurement.
            ops_per_sample: Redis operations per sample.
            disk_target: Service to benchmark disk on.
            disk_block_kb: Block size in KB for disk test.
            disk_count: Number of blocks for disk test.

        Returns:
            Dictionary with redis and disk throughput results.
        """
        with ThreadPoolExecutor(max_workers=2) as pool:
            redis_future = pool.submit(
                self.measure_redis_throughput,
                samples=samples,
                ops_per_sample=ops_per_sample,
            )
            disk_future = pool.submit(
                self.measure_disk_throughput,
                target_service=disk_target,
                samples=samples,
                block_size_kb=disk_block_kb,
                count=disk_count,
            )
            redis_results = redis_future.result()
            disk_results = disk_future.result()

        return {
            "redis": [r.summary() for r in redis_results],
            "disk": [r.summary() for r in disk_results],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": {
                "samples": samples,
                "redisOpsPerSample": ops_per_sample,
                "diskBlockSize_kb": disk_block_kb,
                "diskBlockCount": disk_count,
            },
        }

    def _find_ready_pod(self, service_name: str) -> Optional[str]:
        """Find a ready pod for a service."""
        return find_ready_pod(self.core_api, self.namespace, service_name)

    def _find_exec_pod(
        self, target_service: str, *, exclude_services: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Find a pod that supports shell exec for benchmarks.

        Many application pods are distroless (no shell).  This tries
        the target first, then auto-discovers all running pods in the
        namespace and checks each for shell access.  Results are cached
        to avoid repeated exec checks.

        Parameters
        ----------
        target_service:
            Preferred service to benchmark on.
        exclude_services:
            Service names to skip (e.g. the chaos target being killed).
        """
        with self._cache_lock:
            if target_service in self._exec_pod_cache:
                return self._exec_pod_cache[target_service]

        exclude = set(exclude_services or [])

        # Try the preferred target first (if not excluded)
        if target_service not in exclude:
            pod = self._find_ready_pod(target_service)
            if pod:
                resp = self._exec_in_pod(pod, ["sh", "-c", "echo ok"])
                if not resp.startswith("ERROR:") and "ok" in resp:
                    with self._cache_lock:
                        # Re-check under lock to avoid overwriting a
                        # concurrent discovery with a stale result.
                        if target_service not in self._exec_pod_cache:
                            self._exec_pod_cache[target_service] = pod
                        return self._exec_pod_cache[target_service]

        # Auto-discover all running pods in the namespace
        try:
            pods = self.core_api.list_namespaced_pod(
                self.namespace, field_selector="status.phase=Running",
            )
        except Exception:
            pods = None

        if pods:
            # Extract unique app labels, skip excluded services
            seen: set = set()
            for p in pods.items:
                labels = p.metadata.labels or {}
                app = labels.get("app", "")
                if not app or app in exclude or app in seen:
                    continue
                seen.add(app)
                if not (p.status.conditions and any(
                    c.type == "Ready" and c.status == "True"
                    for c in p.status.conditions
                )):
                    continue
                pod_name = p.metadata.name
                resp = self._exec_in_pod(pod_name, ["sh", "-c", "echo ok"])
                if not resp.startswith("ERROR:") and "ok" in resp:
                    with self._cache_lock:
                        if target_service not in self._exec_pod_cache:
                            self._exec_pod_cache[target_service] = pod_name
                        return self._exec_pod_cache[target_service]

        with self._cache_lock:
            self._exec_pod_cache[target_service] = None
        return None

    def _exec_in_pod(self, pod_name: str, command: List[str]) -> str:
        """Execute a command inside a pod and return stdout."""
        return _base_exec_in_pod(self.core_api, self.namespace, pod_name, command)

    def _redis_benchmark(
        self,
        pod_name: str,
        operation: str,
        count: int,
        host: str,
        port: int,
    ) -> ThroughputSample:
        """Run a Redis SET or GET benchmark inside the redis pod.

        Uses 'redis-cli TIME' for microsecond-precision timing since
        Alpine busybox date lacks nanosecond support.
        """
        now = datetime.now(timezone.utc).isoformat()

        # redis-cli TIME returns two lines: seconds and microseconds.
        # We read them inline to get a single microsecond timestamp.
        time_cmd = f"redis-cli -h {host} -p {port} TIME 2>/dev/null " "| tr '\\n' ' '"
        if operation == "write":
            cmd = [
                "sh",
                "-c",
                f"TSTART=$({time_cmd}); "
                f"for i in $(seq 1 {count}); do "
                f"redis-cli -h {host} -p {port} SET chaosprobe:bench:$i value_$i > /dev/null 2>&1; "
                f"done; "
                f"TEND=$({time_cmd}); "
                f'echo "$TSTART $TEND"',
            ]
        else:
            cmd = [
                "sh",
                "-c",
                f"TSTART=$({time_cmd}); "
                f"for i in $(seq 1 {count}); do "
                f"redis-cli -h {host} -p {port} GET chaosprobe:bench:$i > /dev/null 2>&1; "
                f"done; "
                f"TEND=$({time_cmd}); "
                f'echo "$TSTART $TEND"',
            ]

        resp = self._exec_in_pod(pod_name, cmd)

        if resp.startswith("ERROR:"):
            return ThroughputSample(
                operation=operation,
                target="redis",
                ops_per_second=0,
                latency_ms=0,
                status="error",
                timestamp=now,
                error=resp[:200],
            )

        try:
            parts = resp.strip().split()
            # Output format: start_s start_us end_s end_us
            if len(parts) >= 4:
                start_us = int(parts[0]) * 1_000_000 + int(parts[1])
                end_us = int(parts[2]) * 1_000_000 + int(parts[3])
                if start_us > 0 and end_us > start_us:
                    elapsed_ms = (end_us - start_us) / 1_000
                    ops_per_sec = (count / elapsed_ms) * 1000
                    avg_latency = elapsed_ms / count
                    return ThroughputSample(
                        operation=operation,
                        target="redis",
                        ops_per_second=round(ops_per_sec, 2),
                        latency_ms=round(avg_latency, 4),
                        status="ok",
                        timestamp=now,
                    )
        except (ValueError, ZeroDivisionError):
            pass

        return ThroughputSample(
            operation=operation,
            target="redis",
            ops_per_second=0,
            latency_ms=0,
            status="error",
            timestamp=now,
            error=f"Parse failed: {resp[:100]}",
        )

    def _disk_benchmark(
        self,
        pod_name: str,
        operation: str,
        block_size_kb: int,
        count: int,
        disk_path: str = "/tmp/chaosprobe_disktest",
    ) -> ThroughputSample:
        """Run a disk write or read benchmark with dd inside a pod.

        Uses dd's own stderr summary ("copied, X.XXX s") for timing
        instead of shell-wrapped timers — microsecond precision on both
        GNU and busybox, no python3 dependency in the target pod.
        """
        now = datetime.now(timezone.utc).isoformat()
        total_bytes = block_size_kb * 1024 * count

        if operation == "write":
            # Try conv=fdatasync (GNU); if dd rejects it (busybox), retry without.
            # In both cases redirect dd stdout to /dev/null and capture stderr to stdout
            # so the "copied, X s" summary reaches us for parsing.
            cmd = [
                "sh",
                "-c",
                f"dd if=/dev/zero of={disk_path} "
                f"bs={block_size_kb}k count={count} conv=fdatasync 2>&1 >/dev/null "
                f"|| dd if=/dev/zero of={disk_path} "
                f"bs={block_size_kb}k count={count} 2>&1 >/dev/null",
            ]
        else:
            # Write first if the file doesn't exist, then read.
            # Skip drop_caches (requires root) — just read through cache.
            cmd = [
                "sh",
                "-c",
                f"[ -f {disk_path} ] || "
                f"dd if=/dev/zero of={disk_path} "
                f"bs={block_size_kb}k count={count} 2>/dev/null; "
                f"sync; "
                f"dd if={disk_path} of=/dev/null "
                f"bs={block_size_kb}k 2>&1 >/dev/null",
            ]

        resp = self._exec_in_pod(pod_name, cmd)

        if resp.startswith("ERROR:"):
            return ThroughputSample(
                operation=operation,
                target="disk",
                ops_per_second=0,
                latency_ms=0,
                status="error",
                timestamp=now,
                error=resp[:200],
            )

        elapsed_s = _parse_dd_elapsed_seconds(resp)
        if elapsed_s is None:
            return ThroughputSample(
                operation=operation,
                target="disk",
                ops_per_second=0,
                latency_ms=0,
                status="error",
                timestamp=now,
                error=f"dd output unparseable: {resp.strip()[:200]}",
            )
        if elapsed_s <= 0:
            # dd can report "0 s" when the op is below its timer resolution.
            # Flag rather than divide-by-zero.
            return ThroughputSample(
                operation=operation,
                target="disk",
                ops_per_second=0,
                latency_ms=0,
                status="error",
                timestamp=now,
                error=f"dd elapsed<=0 (workload too small): {resp.strip()[:200]}",
            )

        ops_per_sec = count / elapsed_s
        bps = total_bytes / elapsed_s
        avg_latency_ms = (elapsed_s * 1000) / count
        return ThroughputSample(
            operation=operation,
            target="disk",
            ops_per_second=round(ops_per_sec, 2),
            latency_ms=round(avg_latency_ms, 4),
            bytes_per_second=round(bps, 2),
            status="ok",
            timestamp=now,
        )


class ContinuousRedisProber(ContinuousProberBase):
    """Runs Redis throughput benchmarks in a background thread during chaos.

    Usage::

        prober = ContinuousRedisProber("online-boutique")
        prober.start()
        # ... run chaos experiment ...
        prober.stop()
        data = prober.result()
    """

    def __init__(
        self,
        namespace: str,
        interval: float = 10.0,
        ops_per_sample: int = 200,
    ):
        super().__init__(namespace, interval, name="redis-prober")
        self._prober = ThroughputProber(namespace)
        self._ops_per_sample = ops_per_sample

    def result(self) -> Dict[str, Any]:
        with self._lock:
            series = list(self._time_series)
            errors = self._probe_errors
        phases = self._split_phases(series, "redis")
        data: Dict[str, Any] = {
            "timeSeries": series,
            "phases": phases,
            "config": {
                "interval_s": self.interval,
                "namespace": self.namespace,
                "opsPerSample": self._ops_per_sample,
            },
        }
        if errors > 0:
            data["probeErrors"] = errors
        return data

    def _probe_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                now = time.time()
                phase = self._current_phase(now)
                entry = self._make_entry(now, phase)
                entry["redis"] = {}

                results = self._prober.measure_redis_throughput(
                    samples=1,
                    ops_per_sample=self._ops_per_sample,
                )
                for r in results:
                    if r.samples:
                        s = r.samples[0]
                        entry["redis"][r.operation] = {
                            "ops_per_second": s.ops_per_second if s.status == "ok" else None,
                            "latency_ms": s.latency_ms if s.status == "ok" else None,
                            "status": s.status,
                        }

                with self._lock:
                    self._time_series.append(entry)

            except Exception as exc:
                logger.warning("Redis probe failed: %s", exc)
                with self._lock:
                    self._probe_errors += 1

            self._stop_event.wait(timeout=self.interval)


class ContinuousDiskProber(ContinuousProberBase):
    """Runs disk I/O benchmarks from one probe pod per cluster node.

    Placement strategies (colocate, spread, adversarial, …) distribute
    workload across nodes differently, so a single probe pod cannot
    distinguish them: if the chaos-target pod lands on a different node
    than the probe, the probe sees no effect regardless of strategy.

    At ``start()`` the prober discovers one writable probe pod per
    distinct node and remembers (pod, node, path) for each.  Each tick
    then runs ``dd`` in parallel across all per-node probe pods and
    records:

    * a tick-level aggregate (mean across nodes) in the
      ``ops_per_second`` / ``latency_ms`` / ``bytes_per_second`` fields;
    * ``probeCount``, ``errorCount``, ``stddevOpsPerSecond``,
      ``minOpsPerSecond`` and ``maxOpsPerSecond`` describing the
      cross-node distribution;
    * a ``perNode`` map with the full per-node sample.

    The *disk_target* argument only acts as a hint for the
    first-probe-pod preference.

    Usage::

        prober = ContinuousDiskProber("online-boutique")
        prober.start()
        # ... run chaos experiment ...
        prober.stop()
        data = prober.result()
    """

    _CANDIDATE_DIRS = ("/tmp", "/data", "/var/tmp")

    def __init__(
        self,
        namespace: str,
        interval: float = 10.0,
        disk_target: str = "redis-cart",
        block_size_kb: int = 64,
        block_count: int = 4,
        exclude_services: Optional[List[str]] = None,
    ):
        super().__init__(namespace, interval, name="disk-prober")
        self._prober = ThroughputProber(namespace)
        self._disk_target = disk_target
        self._block_size_kb = block_size_kb
        self._block_count = block_count
        self._exclude_services: List[str] = list(exclude_services or [])
        # [(pod_name, node_name, writable_path), ...]
        self._probe_points: List[tuple] = []

    def start(self) -> None:
        """Discover one writable probe pod per node and pre-flight each."""
        candidates = find_probe_pods_per_node(
            self._prober.core_api,
            self._prober.namespace,
            require_python3=False,
            exclude_prefixes=self._exclude_services,
        )

        if not candidates:
            logger.warning(
                "Disk prober: no ready pods found for per-node probing "
                "(namespace=%s, excluded=%s)",
                self._prober.namespace, self._exclude_services,
            )
            super().start()
            return

        for pod, node in candidates:
            path = self._find_writable_path(pod)
            if path:
                self._probe_points.append((pod, node, path))
                logger.info(
                    "Disk prober per-node probe OK: pod=%s node=%s path=%s",
                    pod, node, path,
                )
            else:
                logger.info(
                    "Disk prober: no writable dir on pod %s (node %s) — skipping",
                    pod, node,
                )

        if not self._probe_points:
            logger.warning(
                "Disk prober: none of %d per-node candidate pods had a "
                "writable directory — all samples will be errors.",
                len(candidates),
            )
        else:
            logger.info(
                "Disk prober probing %d node(s): %s",
                len(self._probe_points),
                ", ".join(f"{n}({p})" for p, n, _ in self._probe_points),
            )

        super().start()

    def _find_writable_path(self, pod: str) -> Optional[str]:
        """Return the first writable candidate directory on *pod*, or None."""
        for d in self._CANDIDATE_DIRS:
            path = f"{d}/chaosprobe_disktest"
            resp = self._prober._exec_in_pod(
                pod,
                ["sh", "-c",
                 f"dd if=/dev/zero of={path} bs=1k count=1 2>&1 "
                 f"&& echo OK || echo FAIL"],
            )
            if "OK" in resp:
                return path
        return None

    def result(self) -> Dict[str, Any]:
        with self._lock:
            series = list(self._time_series)
            errors = self._probe_errors
        phases = self._split_phases(series, "disk")
        data: Dict[str, Any] = {
            "timeSeries": series,
            "phases": phases,
            "probePoints": [
                {"pod": p, "node": n, "path": path}
                for p, n, path in self._probe_points
            ],
            "config": {
                "interval_s": self.interval,
                "namespace": self.namespace,
                "diskTarget": self._disk_target,
                "blockSize_kb": self._block_size_kb,
                "blockCount": self._block_count,
            },
        }
        if errors > 0:
            data["probeErrors"] = errors
        return data

    def _probe_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                now = time.time()
                phase = self._current_phase(now)
                entry = self._make_entry(now, phase)
                entry["disk"] = self._run_all_probes()

                with self._lock:
                    self._time_series.append(entry)

            except Exception as exc:
                logger.warning("Disk probe failed: %s", exc)
                with self._lock:
                    self._probe_errors += 1

            self._stop_event.wait(timeout=self.interval)

    def _run_all_probes(self) -> Dict[str, Any]:
        """Run dd benchmarks on every per-node probe pod in parallel.

        Returns ``{"write": <aggregate>, "read": <aggregate>}`` where each
        aggregate contains the tick-level mean plus cross-node distribution
        statistics and a per-node breakdown.
        """
        if not self._probe_points:
            return {}

        def _probe_one(pod: str, node: str, path: str) -> tuple:
            w = self._prober._disk_benchmark(
                pod, "write", self._block_size_kb, self._block_count, path,
            )
            r = self._prober._disk_benchmark(
                pod, "read", self._block_size_kb, self._block_count, path,
            )
            return (pod, node, w, r)

        results: List[tuple] = []
        with ThreadPoolExecutor(max_workers=min(len(self._probe_points), 8)) as pool:
            futs = [
                pool.submit(_probe_one, p, n, path)
                for p, n, path in self._probe_points
            ]
            for f in futs:
                try:
                    results.append(f.result())
                except Exception as exc:
                    logger.warning("per-pod disk probe raised: %s", exc)

        write_samples = [(pod, node, w) for pod, node, w, _ in results]
        read_samples = [(pod, node, r) for pod, node, _, r in results]
        return {
            "write": _aggregate_disk_samples(write_samples),
            "read": _aggregate_disk_samples(read_samples),
        }
