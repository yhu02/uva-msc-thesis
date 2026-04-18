"""Throughput measurement for database and disk I/O operations.

Measures read/write throughput for:
- Redis (database) operations via the cartservice/redis-cart
- Disk I/O operations within pods

Runs benchmark commands inside pods via kubectl exec and collects
operations-per-second and latency statistics.
"""

import logging
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes import client
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream

from chaosprobe.k8s import ensure_k8s_config
from chaosprobe.metrics.base import (
    ContinuousProberBase,
    exec_in_pod as _base_exec_in_pod,
    find_ready_pod,
)

logger = logging.getLogger(__name__)


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

    @staticmethod
    def _nano_time_cmd() -> str:
        """Return a shell snippet that prints epoch nanoseconds.

        Tries python3 first (nanosecond precision), then GNU date +%s%N,
        and finally falls back to date +%s with '000000000' appended.
        Handles busybox date which outputs literal '%N' instead of
        nanoseconds.
        """
        return (
            "python3 -c 'import time;print(int(time.time()*1e9))' 2>/dev/null"
            " || { T=$(date +%s%N 2>/dev/null); "
            'case "$T" in *N*|*%*) echo $(date +%s)000000000;; '
            "*) [ ${#T} -gt 15 ] && echo $T || echo ${T}000000000;; esac; }"
        )

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
                        self._exec_pod_cache[target_service] = pod
                    return pod

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
                        self._exec_pod_cache[target_service] = pod_name
                    return pod_name

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
        """Run a disk write or read benchmark with dd inside a pod."""
        now = datetime.now(timezone.utc).isoformat()
        total_bytes = block_size_kb * 1024 * count
        nano = self._nano_time_cmd()

        if operation == "write":
            # Use conv=fdatasync if supported (GNU dd), fall back to sync.
            # Capture dd stderr so we can diagnose failures (e.g. read-only fs).
            cmd = [
                "sh",
                "-c",
                f"START=$({nano}); "
                f"ERR=$(dd if=/dev/zero of={disk_path} "
                f"bs={block_size_kb}k count={count} conv=fdatasync 2>&1 >/dev/null) || "
                f"ERR=$(dd if=/dev/zero of={disk_path} "
                f"bs={block_size_kb}k count={count} 2>&1 >/dev/null; sync); "
                f"END=$({nano}); "
                f'if [ -n "$START" ] && [ -n "$END" ]; then '
                f'echo "$START $END"; '
                f'else echo "DIAG nano_fail start=$START end=$END err=$ERR"; fi',
            ]
        else:
            # Write first if file doesn't exist, then read.
            # Skip drop_caches (requires root) — just read through cache.
            cmd = [
                "sh",
                "-c",
                f"[ -f {disk_path} ] || "
                f"dd if=/dev/zero of={disk_path} "
                f"bs={block_size_kb}k count={count} 2>/dev/null; "
                f"sync; "
                f"START=$({nano}); "
                f"ERR=$(dd if={disk_path} of=/dev/null "
                f"bs={block_size_kb}k 2>&1 >/dev/null); "
                f"END=$({nano}); "
                f'if [ -n "$START" ] && [ -n "$END" ]; then '
                f'echo "$START $END"; '
                f'else echo "DIAG nano_fail start=$START end=$END err=$ERR"; fi',
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

        stripped = resp.strip()
        if stripped.startswith("DIAG "):
            return ThroughputSample(
                operation=operation,
                target="disk",
                ops_per_second=0,
                latency_ms=0,
                status="error",
                timestamp=now,
                error=stripped[:200],
            )

        try:
            parts = stripped.split()
            if len(parts) >= 2:
                start_ns = int(parts[0])
                end_ns = int(parts[1])
                if start_ns > 0 and end_ns > start_ns:
                    elapsed_ms = (end_ns - start_ns) / 1_000_000
                    elapsed_s = elapsed_ms / 1000
                    ops_per_sec = count / elapsed_s if elapsed_s > 0 else 0
                    bps = total_bytes / elapsed_s if elapsed_s > 0 else 0
                    avg_latency = elapsed_ms / count if count > 0 else 0
                    return ThroughputSample(
                        operation=operation,
                        target="disk",
                        ops_per_second=round(ops_per_sec, 2),
                        latency_ms=round(avg_latency, 4),
                        bytes_per_second=round(bps, 2),
                        status="ok",
                        timestamp=now,
                    )
        except (ValueError, ZeroDivisionError):
            pass

        return ThroughputSample(
            operation=operation,
            target="disk",
            ops_per_second=0,
            latency_ms=0,
            status="error",
            timestamp=now,
            error=f"Parse failed: {resp[:100]}",
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
    """Runs disk I/O throughput benchmarks in a background thread during chaos.

    Usage::

        prober = ContinuousDiskProber("online-boutique")
        prober.start()
        # ... run chaos experiment ...
        prober.stop()
        data = prober.result()
    """

    def __init__(
        self,
        namespace: str,
        interval: float = 10.0,
        disk_target: str = "redis-cart",
        block_size_kb: int = 512,
        block_count: int = 5,
        exclude_services: Optional[List[str]] = None,
    ):
        super().__init__(namespace, interval, name="disk-prober")
        self._prober = ThroughputProber(namespace)
        self._disk_target = disk_target
        self._block_size_kb = block_size_kb
        self._block_count = block_count
        self._disk_path: str = "/tmp/chaosprobe_disktest"
        self._exclude_services: List[str] = list(exclude_services or [])

    def start(self) -> None:
        """Start with a pre-flight disk I/O check.

        Tries multiple writable directories (``/tmp``, ``/data``,
        ``/var/tmp``) to handle pods with read-only root filesystems
        (e.g. redis-cart on Alpine).  Falls back to different pods if
        the primary target can't write anywhere.
        """
        _CANDIDATE_DIRS = ["/tmp", "/data", "/var/tmp"]
        pod = self._prober._find_exec_pod(
            self._disk_target, exclude_services=self._exclude_services,
        )
        if not pod:
            logger.warning(
                "Disk prober: no exec-capable pod found for target %s",
                self._disk_target,
            )
            super().start()
            return

        # Try multiple writable directories on the discovered pod
        for d in _CANDIDATE_DIRS:
            path = f"{d}/chaosprobe_disktest"
            resp = self._prober._exec_in_pod(
                pod,
                ["sh", "-c",
                 f"dd if=/dev/zero of={path} bs=1k count=1 2>&1 "
                 f"&& echo OK || echo FAIL"],
            )
            if "OK" in resp:
                self._disk_path = path
                logger.info(
                    "Disk prober pre-flight OK on pod %s (path: %s)",
                    pod, path,
                )
                super().start()
                return

        # All dirs failed on this pod — try other pods in the namespace
        try:
            all_pods = self._prober.core_api.list_namespaced_pod(
                self._prober.namespace,
                field_selector="status.phase=Running",
            )
        except Exception:
            all_pods = None

        exclude = set(self._exclude_services)
        if all_pods:
            for p in all_pods.items:
                labels = p.metadata.labels or {}
                app = labels.get("app", "")
                if app in exclude:
                    continue
                alt_pod = p.metadata.name
                if alt_pod == pod:
                    continue
                if not (p.status.conditions and any(
                    c.type == "Ready" and c.status == "True"
                    for c in p.status.conditions
                )):
                    continue
                resp = self._prober._exec_in_pod(
                    alt_pod, ["sh", "-c", "echo ok"],
                )
                if "ok" not in resp:
                    continue
                for d in _CANDIDATE_DIRS:
                    path = f"{d}/chaosprobe_disktest"
                    resp = self._prober._exec_in_pod(
                        alt_pod,
                        ["sh", "-c",
                         f"dd if=/dev/zero of={path} bs=1k count=1 2>&1 "
                         f"&& echo OK || echo FAIL"],
                    )
                    if "OK" in resp:
                        self._disk_path = path
                        with self._prober._cache_lock:
                            self._prober._exec_pod_cache[self._disk_target] = alt_pod
                        logger.info(
                            "Disk prober pre-flight OK on fallback pod %s (path: %s)",
                            alt_pod, path,
                        )
                        super().start()
                        return

        logger.warning(
            "Disk prober pre-flight FAILED: no writable directory found "
            "on any candidate pod — disk samples will likely all be errors.",
        )
        super().start()

    def result(self) -> Dict[str, Any]:
        with self._lock:
            series = list(self._time_series)
            errors = self._probe_errors
        phases = self._split_phases(series, "disk")
        data: Dict[str, Any] = {
            "timeSeries": series,
            "phases": phases,
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
                entry["disk"] = {}

                results = self._prober.measure_disk_throughput(
                    target_service=self._disk_target,
                    samples=1,
                    block_size_kb=self._block_size_kb,
                    count=self._block_count,
                    disk_path=self._disk_path,
                    exclude_services=self._exclude_services,
                )
                for r in results:
                    if r.samples:
                        s = r.samples[0]
                        entry["disk"][r.operation] = {
                            "ops_per_second": s.ops_per_second if s.status == "ok" else None,
                            "latency_ms": s.latency_ms if s.status == "ok" else None,
                            "bytes_per_second": s.bytes_per_second if s.status == "ok" else None,
                            "status": s.status,
                        }

                with self._lock:
                    self._time_series.append(entry)

            except Exception as exc:
                logger.warning("Disk probe failed: %s", exc)
                with self._lock:
                    self._probe_errors += 1

            self._stop_event.wait(timeout=self.interval)
