"""Throughput measurement for database and disk I/O operations.

Measures read/write throughput for:
- Redis (database) operations via the cartservice/redis-cart in Online Boutique
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

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream

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

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

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
        redis_pod = self._find_ready_pod("redis-cart")
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

        for i in range(samples):
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
    ) -> List[ThroughputResult]:
        """Measure disk write/read throughput inside a pod.

        Uses dd to write and read blocks, measuring sequential I/O speed.

        Args:
            target_service: Service whose pod to benchmark. Defaults to
                redis-cart since many Online Boutique services are distroless.
            samples: Number of benchmark rounds.
            block_size_kb: Block size in KB for dd.
            count: Number of blocks per dd operation.

        Returns:
            List of ThroughputResult for disk write and read.
        """
        pod = self._find_exec_pod(target_service)
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

        for i in range(samples):
            w_sample = self._disk_benchmark(pod, "write", block_size_kb, count)
            write_result.samples.append(w_sample)

            r_sample = self._disk_benchmark(pod, "read", block_size_kb, count)
            read_result.samples.append(r_sample)

        # Clean up test file
        self._exec_in_pod(
            pod, ["sh", "-c", "rm -f /tmp/chaosprobe_disktest 2>/dev/null; echo done"]
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
        try:
            pods = self.core_api.list_namespaced_pod(
                self.namespace,
                label_selector=f"app={service_name}",
                field_selector="status.phase=Running",
            )
        except ApiException:
            return None

        for pod in pods.items:
            if pod.status.conditions:
                for cond in pod.status.conditions:
                    if cond.type == "Ready" and cond.status == "True":
                        return pod.metadata.name
        return None

    def _find_exec_pod(self, target_service: str) -> Optional[str]:
        """Find a pod that supports shell exec for benchmarks.

        Many Online Boutique pods are distroless (no shell). This tries
        the target first, verifies shell access, then falls back to pods
        known to have a shell. Results are cached to avoid repeated
        exec checks.
        """
        with self._cache_lock:
            if target_service in self._exec_pod_cache:
                return self._exec_pod_cache[target_service]

        candidates = [
            target_service,
            "redis-cart",
            "loadgenerator",
            "currencyservice",
            "emailservice",
        ]
        for svc in candidates:
            pod = self._find_ready_pod(svc)
            if pod:
                resp = self._exec_in_pod(pod, ["sh", "-c", "echo ok"])
                if not resp.startswith("ERROR:") and "ok" in resp:
                    with self._cache_lock:
                        self._exec_pod_cache[target_service] = pod
                    return pod
        with self._cache_lock:
            self._exec_pod_cache[target_service] = None
        return None

    def _exec_in_pod(self, pod_name: str, command: List[str]) -> str:
        """Execute a command inside a pod and return stdout."""
        try:
            return stream(
                self.core_api.connect_get_namespaced_pod_exec,
                pod_name,
                self.namespace,
                command=command,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=True,
            )
        except Exception as e:
            return f"ERROR:{e}"

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
    ) -> ThroughputSample:
        """Run a disk write or read benchmark with dd inside a pod."""
        now = datetime.now(timezone.utc).isoformat()
        total_bytes = block_size_kb * 1024 * count
        nano = self._nano_time_cmd()

        if operation == "write":
            # Use conv=fdatasync if supported (GNU dd), fall back to sync
            cmd = [
                "sh",
                "-c",
                f"START=$({nano}); "
                f"dd if=/dev/zero of=/tmp/chaosprobe_disktest "
                f"bs={block_size_kb}k count={count} conv=fdatasync 2>/dev/null || "
                f"{{ dd if=/dev/zero of=/tmp/chaosprobe_disktest "
                f"bs={block_size_kb}k count={count} 2>/dev/null; sync; }}; "
                f"END=$({nano}); "
                f'echo "$START $END"',
            ]
        else:
            # Write first if file doesn't exist, then read
            cmd = [
                "sh",
                "-c",
                f"[ -f /tmp/chaosprobe_disktest ] || "
                f"dd if=/dev/zero of=/tmp/chaosprobe_disktest "
                f"bs={block_size_kb}k count={count} 2>/dev/null; "
                f"sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null; "
                f"START=$({nano}); "
                f"dd if=/tmp/chaosprobe_disktest of=/dev/null "
                f"bs={block_size_kb}k 2>/dev/null; "
                f"END=$({nano}); "
                f'echo "$START $END"',
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

        try:
            parts = resp.strip().split()
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


class _ContinuousProberBase:
    """Base class for continuous throughput probers with phase tracking."""

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
        if chaos_start is None:
            return "pre-chaos"
        if chaos_end is None:
            return "during-chaos"
        return "post-chaos"

    def _make_entry(self, now: float, phase: str) -> Dict[str, Any]:
        return {
            "timestamp": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "elapsed_s": round(now - (self._start_time or now), 1),
            "phase": phase,
        }

    def _probe_loop(self) -> None:
        raise NotImplementedError

    @staticmethod
    def _aggregate_operations(
        entries: List[Dict[str, Any]],
        key: str,
    ) -> Dict[str, Any]:
        """Aggregate throughput metrics for a given key across entries."""
        ops_by_op: Dict[str, List[float]] = {}
        lat_by_op: Dict[str, List[float]] = {}
        bps_by_op: Dict[str, List[float]] = {}
        err_by_op: Dict[str, int] = {}

        for entry in entries:
            for op, data in entry.get(key, {}).items():
                ops_by_op.setdefault(op, [])
                lat_by_op.setdefault(op, [])
                bps_by_op.setdefault(op, [])
                err_by_op.setdefault(op, 0)
                if data.get("ops_per_second") is not None:
                    ops_by_op[op].append(data["ops_per_second"])
                if data.get("latency_ms") is not None:
                    lat_by_op[op].append(data["latency_ms"])
                if data.get("bytes_per_second") is not None:
                    bps_by_op[op].append(data["bytes_per_second"])
                if data.get("status") != "ok":
                    err_by_op[op] += 1

        summary = {}
        for op in ops_by_op:
            ops = ops_by_op[op]
            lats = lat_by_op.get(op, [])
            bps = bps_by_op.get(op, [])
            errs = err_by_op.get(op, 0)
            if ops:
                summary[op] = {
                    "meanOpsPerSecond": round(statistics.mean(ops), 2),
                    "medianOpsPerSecond": round(statistics.median(ops), 2),
                    "minOpsPerSecond": round(min(ops), 2),
                    "maxOpsPerSecond": round(max(ops), 2),
                    "meanLatency_ms": round(statistics.mean(lats), 2) if lats else None,
                    "meanBytesPerSecond": round(statistics.mean(bps), 2) if bps else None,
                    "sampleCount": len(ops),
                    "errorCount": errs,
                }
            else:
                summary[op] = {"meanOpsPerSecond": None, "sampleCount": 0, "errorCount": errs}
        return summary


class ContinuousRedisProber(_ContinuousProberBase):
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
        phases = self._split_phases(series)
        data: Dict[str, Any] = {
            "timeSeries": series,
            "phases": phases,
            "config": {
                "interval_s": self.interval,
                "namespace": self.namespace,
                "opsPerSample": self._ops_per_sample,
            },
        }
        if self._probe_errors > 0:
            data["probeErrors"] = self._probe_errors
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

    def _split_phases(self, series: List[Dict[str, Any]]) -> Dict[str, Any]:
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
                result[name] = {"sampleCount": 0, "redis": {}}
            else:
                result[name] = {
                    "sampleCount": len(entries),
                    "redis": self._aggregate_operations(entries, "redis"),
                }
        return result


class ContinuousDiskProber(_ContinuousProberBase):
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
    ):
        super().__init__(namespace, interval, name="disk-prober")
        self._prober = ThroughputProber(namespace)
        self._disk_target = disk_target
        self._block_size_kb = block_size_kb
        self._block_count = block_count

    def result(self) -> Dict[str, Any]:
        with self._lock:
            series = list(self._time_series)
        phases = self._split_phases(series)
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
        if self._probe_errors > 0:
            data["probeErrors"] = self._probe_errors
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

    def _split_phases(self, series: List[Dict[str, Any]]) -> Dict[str, Any]:
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
                result[name] = {"sampleCount": 0, "disk": {}}
            else:
                result[name] = {
                    "sampleCount": len(entries),
                    "disk": self._aggregate_operations(entries, "disk"),
                }
        return result


# Backwards-compatible alias
ContinuousThroughputProber = ContinuousRedisProber
