"""Inter-service latency measurement via in-cluster probing.

Measures request latency between microservices by executing commands inside
pods.  This captures real service-to-service communication times within the
cluster, including DNS resolution, network traversal, and application
processing.

Service dependencies are discovered dynamically via
``config.topology.parse_topology_from_scenario()`` and passed in by the CLI.

The prober runs a configurable number of samples for each service pair and
computes statistics (mean, median, p95, p99, min, max).
"""

import logging
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from kubernetes import client
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream

from chaosprobe.k8s import ensure_k8s_config
from chaosprobe.metrics.base import (
    ContinuousProberBase,
    exec_in_pod,
    find_all_probe_pods,
    find_probe_pod,
    find_ready_pod,
    pod_has_shell,
)

logger = logging.getLogger(__name__)


def _service_from_url(url: str) -> str:
    """Extract the service name from a cluster-internal URL.

    URLs follow the pattern ``http://{service}.{ns}.svc.cluster.local...``.
    Returns the first hostname segment, or ``"unknown"`` if parsing fails.
    """
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        return host.split(".")[0] if host else "unknown"
    except Exception:
        return "unknown"


@dataclass
class LatencySample:
    """A single latency measurement."""

    source: str
    target: str
    route: str
    protocol: str
    latency_ms: float
    status: str  # "ok", "error", "timeout"
    timestamp: str
    error: Optional[str] = None


@dataclass
class LatencyResult:
    """Aggregated latency results for a service pair."""

    source: str
    target: str
    route: str
    protocol: str
    description: str
    samples: List[LatencySample] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        ok_latencies = [s.latency_ms for s in self.samples if s.status == "ok"]
        error_count = sum(1 for s in self.samples if s.status != "ok")

        if not ok_latencies:
            return {
                "source": self.source,
                "target": self.target,
                "route": self.route,
                "protocol": self.protocol,
                "description": self.description,
                "sampleCount": len(self.samples),
                "errorCount": error_count,
                "errorRate": 1.0 if self.samples else 0.0,
                "mean_ms": None,
                "median_ms": None,
                "p95_ms": None,
                "p99_ms": None,
                "min_ms": None,
                "max_ms": None,
                "stddev_ms": None,
            }

        sorted_latencies = sorted(ok_latencies)
        p95_idx = min(int(len(sorted_latencies) * 0.95), len(sorted_latencies) - 1)
        p99_idx = min(int(len(sorted_latencies) * 0.99), len(sorted_latencies) - 1)

        return {
            "source": self.source,
            "target": self.target,
            "route": self.route,
            "protocol": self.protocol,
            "description": self.description,
            "sampleCount": len(self.samples),
            "errorCount": error_count,
            "errorRate": round(error_count / len(self.samples), 4) if self.samples else 0.0,
            "mean_ms": round(statistics.mean(ok_latencies), 2),
            "median_ms": round(statistics.median(ok_latencies), 2),
            "p95_ms": round(sorted_latencies[p95_idx], 2),
            "p99_ms": round(sorted_latencies[p99_idx], 2),
            "min_ms": round(min(ok_latencies), 2),
            "max_ms": round(max(ok_latencies), 2),
            "stddev_ms": round(statistics.stdev(ok_latencies), 2) if len(ok_latencies) > 1 else 0.0,
        }


class LatencyProber:
    """Measures inter-service latency within a Kubernetes cluster.

    Executes HTTP requests from source pods to target services and measures
    round-trip time. Uses kubectl exec to run curl inside pods, capturing
    real in-cluster latency including DNS, network, and processing time.

    Usage::

        prober = LatencyProber("online-boutique")
        results = prober.measure_all(samples=10, interval=1.0)
        for r in results:
            print(r.summary())
    """

    def __init__(
        self,
        namespace: str,
        timeout_seconds: int = 5,
        exclude_prefixes: Optional[List[str]] = None,
    ):
        self.namespace = namespace
        self.timeout_seconds = timeout_seconds
        self._use_wget: bool = False  # fallback when python3 unavailable
        self._exclude_prefixes = exclude_prefixes

        ensure_k8s_config()

        self.core_api = client.CoreV1Api()

    def measure_http_routes(
        self,
        samples: int = 10,
        interval: float = 1.0,
        parallel: bool = False,
        probe_pod: Optional[str] = None,
        http_routes: Optional[List[Tuple[str, str, str, str]]] = None,
    ) -> List[LatencyResult]:
        """Measure latency for HTTP routes.

        Args:
            samples: Number of samples per route.
            interval: Seconds between samples.
            parallel: If True, measure all routes concurrently per sample round.
            probe_pod: Optional pre-resolved pod name to use for probing.
                       If None, a suitable pod is discovered automatically.
            http_routes: List of (service, path, description, method) tuples.
                         If None, no HTTP routes are measured.

        Returns:
            List of LatencyResult for each route.
        """
        if not http_routes:
            return []

        if probe_pod is None:
            probe_pod = self._find_probe_pod()
        if not probe_pod:
            return []

        routes_info = list(http_routes)
        result_map = {}
        for service, route, description, _method in routes_info:
            result_map[route] = LatencyResult(
                source=service,
                target=service,
                route=route,
                protocol="http",
                description=description,
            )

        if parallel:
            with ThreadPoolExecutor(max_workers=len(routes_info)) as pool:
                for _ in range(samples):
                    futures = {}
                    for service, route, _desc, method in routes_info:
                        url = f"http://{service}.{self.namespace}.svc.cluster.local{route}"
                        fut = pool.submit(
                            self._measure_http_from_pod,
                            probe_pod,
                            url,
                            method,
                            route,
                        )
                        futures[fut] = route
                    for fut in as_completed(futures):
                        result_map[futures[fut]].samples.append(fut.result())
                    if interval > 0:
                        time.sleep(interval)
        else:
            for service, route, _desc, method in routes_info:
                url = f"http://{service}.{self.namespace}.svc.cluster.local{route}"
                for _ in range(samples):
                    sample = self._measure_http_from_pod(probe_pod, url, method, route)
                    result_map[route].samples.append(sample)
                    if interval > 0:
                        time.sleep(interval)

        return [result_map[r] for _, r, _, _ in routes_info]

    def measure_service_pairs(
        self,
        routes: Optional[List[Tuple[str, str, str, str, str]]] = None,
        samples: int = 10,
        interval: float = 1.0,
        parallel: bool = False,
    ) -> List[LatencyResult]:
        """Measure latency between service pairs using TCP connectivity checks.

        For gRPC services, measures TCP connection time to the service port.
        For TCP services (e.g. Redis), measures TCP connection time.

        Args:
            routes: Service dependency routes as (src, tgt, host, proto, desc)
                    tuples, discovered via ``config.topology``.  If None, no
                    service pairs are measured.
            samples: Number of samples per route.
            interval: Seconds between samples.
            parallel: If True, measure all pairs concurrently per sample round.

        Returns:
            List of LatencyResult for each service pair.
        """
        if not routes:
            return []

        # Use a single probe pod for all TCP measurements.  Many source
        # pods use distroless images (no shell), so we probe from a pod
        # that has networking tools available.
        probe_pod = self._find_probe_pod()
        if not probe_pod:
            return []

        result_list = []
        valid_routes = []
        for i, (source, target, host, protocol, description) in enumerate(routes):
            result_list.append(
                LatencyResult(
                    source=source,
                    target=target,
                    route=host,
                    protocol=protocol,
                    description=description,
                )
            )
            valid_routes.append((i, source, target, host))

        if parallel and len(valid_routes) > 1:
            with ThreadPoolExecutor(max_workers=min(len(valid_routes), 8)) as pool:
                for _ in range(samples):
                    futures = {}
                    for idx, (_ri, source, target, host) in enumerate(valid_routes):
                        fut = pool.submit(
                            self._measure_tcp_from_pod,
                            probe_pod,
                            host,
                            source,
                            target,
                        )
                        futures[fut] = idx
                    for fut in as_completed(futures):
                        result_list[futures[fut]].samples.append(fut.result())
                    if interval > 0:
                        time.sleep(interval)
        else:
            for idx, (_ri, source, target, host) in enumerate(valid_routes):
                for _ in range(samples):
                    sample = self._measure_tcp_from_pod(probe_pod, host, source, target)
                    result_list[idx].samples.append(sample)
                    if interval > 0:
                        time.sleep(interval)

        return result_list

    def measure_all(
        self,
        samples: int = 10,
        interval: float = 1.0,
        parallel: bool = True,
        service_routes: Optional[List[Tuple[str, str, str, str, str]]] = None,
        http_routes: Optional[List[Tuple[str, str, str, str]]] = None,
    ) -> Dict[str, Any]:
        """Measure both HTTP routes and inter-service latency.

        Args:
            samples: Number of samples per measurement.
            interval: Seconds between samples.
            parallel: If True, measure routes concurrently.
            service_routes: Optional service dependency graph tuples.
            http_routes: Optional HTTP route tuples.

        Returns:
            Dictionary with HTTP route latencies and service pair latencies.
        """
        http_results = self.measure_http_routes(
            samples=samples,
            interval=interval,
            parallel=parallel,
            http_routes=http_routes,
        )
        service_results = self.measure_service_pairs(
            routes=service_routes,
            samples=samples,
            interval=interval,
            parallel=parallel,
        )

        return {
            "httpRoutes": [r.summary() for r in http_results],
            "servicePairs": [r.summary() for r in service_results],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": {
                "samples": samples,
                "interval_s": interval,
                "timeout_s": self.timeout_seconds,
            },
        }

    def _find_ready_pod(self, service_name: str) -> Optional[str]:
        """Find a ready pod for a given service."""
        return find_ready_pod(self.core_api, self.namespace, service_name)

    def _find_probe_pod(self) -> Optional[str]:
        """Find a pod suitable for running probe commands.

        Prefers pods with python3 for nanosecond-precision timing.
        Falls back to any pod with a shell (will use wget instead).
        """
        return find_probe_pod(
            self.core_api,
            self.namespace,
            require_python3=True,
            exclude_prefixes=self._exclude_prefixes,
        )

    def _find_all_probe_pods(self) -> List[str]:
        """Return all pods suitable for probing, in alphabetical order."""
        return find_all_probe_pods(
            self.core_api,
            self.namespace,
            require_python3=True,
            exclude_prefixes=self._exclude_prefixes,
        )

    def _pod_has_shell(self, pod_name: str) -> bool:
        """Quick check whether *pod_name* has a usable shell."""
        return pod_has_shell(self.core_api, self.namespace, pod_name)

    def _measure_http_from_pod(
        self,
        pod_name: str,
        url: str,
        method: str,
        route: str,
    ) -> LatencySample:
        """Measure HTTP latency by executing a request inside a pod.

        Tries Python urllib first (nanosecond timing, no external deps),
        then falls back to wget + shell timing if python3 is unavailable.
        """
        now = datetime.now(timezone.utc).isoformat()
        target = _service_from_url(url)

        if self._use_wget:
            return self._measure_http_wget(pod_name, url, route, now)

        # Python one-liner: precise timing + HTTP request in one shot.
        # Outputs: "<status_code> <start_ns> <end_ns>" or "ERR <msg>"
        # Wrapped in try/except so errors always print to stdout
        # (without this, exceptions go to stderr and stdout is empty,
        # causing "Unexpected output: " errors).
        # URL is passed via sys.argv to avoid shell/code injection from
        # URLs containing quotes or other special characters.
        py_script = (
            "import time,sys\n"
            "try:\n"
            " import urllib.request as u;"
            "s=int(time.time()*1e9);"
            "r=u.urlopen(sys.argv[1],timeout=int(sys.argv[2]));"
            "_=r.read();"
            "e=int(time.time()*1e9);"
            "print(r.status,s,e)\n"
            "except Exception as ex:\n"
            " print('ERR',str(ex)[:200])"
        )
        cmd = ["python3", "-c", py_script, url, str(self.timeout_seconds)]

        try:
            resp = stream(
                self.core_api.connect_get_namespaced_pod_exec,
                pod_name,
                self.namespace,
                command=cmd,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=True,
            )

            stripped = resp.strip()

            # Detect python3 not available: empty output or common shell
            # error strings.  Only check short responses to avoid false
            # positives from HTML bodies containing "not found".
            if not stripped or (
                len(stripped) < 120
                and (
                    "not found" in stripped.lower()
                    or "no such file" in stripped.lower()
                    or "executable file not found" in stripped.lower()
                )
            ):
                logger.info(
                    "python3 not available in pod %s, switching to wget",
                    pod_name,
                )
                self._use_wget = True
                return self._measure_http_wget(pod_name, url, route, now)

            parts = stripped.split()
            if len(parts) >= 2 and parts[0] == "ERR":
                return LatencySample(
                    source="probe-pod",
                    target=target,
                    route=route,
                    protocol="http",
                    latency_ms=0,
                    status="error",
                    timestamp=now,
                    error=" ".join(parts[1:])[:200],
                )
            if len(parts) >= 3:
                status_code = int(parts[0])
                start_ns = int(parts[1])
                end_ns = int(parts[2])
                if start_ns > 0 and end_ns > start_ns:
                    latency_ms = (end_ns - start_ns) / 1_000_000
                    ok = 200 <= status_code < 400
                    return LatencySample(
                        source="probe-pod",
                        target=target,
                        route=route,
                        protocol="http",
                        latency_ms=round(latency_ms, 2),
                        status="ok" if ok else "error",
                        timestamp=now,
                        error=None if ok else f"HTTP {status_code}",
                    )

            return LatencySample(
                source="probe-pod",
                target=target,
                route=route,
                protocol="http",
                latency_ms=0,
                status="error",
                timestamp=now,
                error=f"Unexpected output: {resp[:200]}",
            )

        except Exception as e:
            # Exec itself may fail if python3 binary doesn't exist
            err_str = str(e).lower()
            if "not found" in err_str or "executable file" in err_str:
                logger.info(
                    "python3 exec failed in pod %s, switching to wget",
                    pod_name,
                )
                self._use_wget = True
                return self._measure_http_wget(pod_name, url, route, now)
            return LatencySample(
                source="probe-pod",
                target=target,
                route=route,
                protocol="http",
                latency_ms=0,
                status="error",
                timestamp=now,
                error=str(e)[:200],
            )

    def _measure_http_wget(
        self,
        pod_name: str,
        url: str,
        route: str,
        now: str,
    ) -> LatencySample:
        """Measure HTTP latency using wget (fallback for pods without python3).

        Uses shell ``date`` for timing.  GNU date gives nanosecond precision;
        busybox (Alpine) only gives second precision — still far better than
        zero data.
        """
        target = _service_from_url(url)
        # Shell one-liner: timestamp -> wget -> timestamp -> print
        # Handles both GNU date (%s%N = nanoseconds) and busybox (%N -> literal).
        # URL and timeout are passed as positional arguments ($1, $2) to
        # avoid shell injection from URLs containing quotes.
        shell_script = (
            'S=$(date +%s%N 2>/dev/null); '
            'case "$S" in *N*|*%*) S=$(date +%s)000000000;; esac; '
            'wget -q -O /dev/null --timeout="$2" '
            '"$1" 2>/dev/null; RC=$?; '
            'E=$(date +%s%N 2>/dev/null); '
            'case "$E" in *N*|*%*) E=$(date +%s)000000000;; esac; '
            'if [ "$RC" -eq 0 ]; then echo "200 $S $E"; '
            'else echo "ERR wget_rc=$RC"; fi'
        )
        cmd = ["sh", "-c", shell_script, "sh", url, str(self.timeout_seconds)]

        try:
            resp = stream(
                self.core_api.connect_get_namespaced_pod_exec,
                pod_name,
                self.namespace,
                command=cmd,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=True,
            )

            parts = resp.strip().split()
            if len(parts) >= 2 and parts[0] == "ERR":
                return LatencySample(
                    source="probe-pod",
                    target=target,
                    route=route,
                    protocol="http",
                    latency_ms=0,
                    status="error",
                    timestamp=now,
                    error=" ".join(parts[1:])[:200],
                )
            if len(parts) >= 3:
                try:
                    status_code = int(parts[0])
                    start_ns = int(parts[1])
                    end_ns = int(parts[2])
                    if start_ns > 0 and end_ns > start_ns:
                        latency_ms = (end_ns - start_ns) / 1_000_000
                        ok = 200 <= status_code < 400
                        return LatencySample(
                            source="probe-pod",
                            target=target,
                            route=route,
                            protocol="http",
                            latency_ms=round(latency_ms, 2),
                            status="ok" if ok else "error",
                            timestamp=now,
                            error=None if ok else f"HTTP {status_code}",
                        )
                except (ValueError, OverflowError):
                    pass

            return LatencySample(
                source="probe-pod",
                target=target,
                route=route,
                protocol="http",
                latency_ms=0,
                status="error",
                timestamp=now,
                error=f"wget: unexpected output: {resp[:200]}",
            )

        except Exception as e:
            return LatencySample(
                source="probe-pod",
                target=target,
                route=route,
                protocol="http",
                latency_ms=0,
                status="error",
                timestamp=now,
                error=f"wget: {str(e)[:200]}",
            )

    def _measure_tcp_from_pod(
        self,
        pod_name: str,
        host: str,
        source: str,
        target: str,
    ) -> LatencySample:
        """Measure TCP connection latency from a pod to a service.

        Uses Python socket.connect for precise TCP-level timing.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Parse host:port
        if ":" in host:
            hostname, port = host.rsplit(":", 1)
        else:
            hostname, port = host, "80"

        py_script = (
            "import socket,time;"
            "s=socket.socket();"
            f"s.settimeout({int(self.timeout_seconds)});"
            "t0=int(time.time()*1e9);"
            f"s.connect(('{hostname}',{int(port)}));"
            "t1=int(time.time()*1e9);"
            "s.close();"
            "print(t0,t1)"
        )
        cmd = ["python3", "-c", py_script]

        try:
            resp = stream(
                self.core_api.connect_get_namespaced_pod_exec,
                pod_name,
                self.namespace,
                command=cmd,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=True,
            )

            parts = resp.strip().split()
            if len(parts) >= 2:
                start_ns = int(parts[0])
                end_ns = int(parts[1])
                if start_ns > 0 and end_ns > start_ns:
                    latency_ms = (end_ns - start_ns) / 1_000_000
                    return LatencySample(
                        source=source,
                        target=target,
                        route=host,
                        protocol="tcp",
                        latency_ms=round(latency_ms, 2),
                        status="ok",
                        timestamp=now,
                    )

            return LatencySample(
                source=source,
                target=target,
                route=host,
                protocol="tcp",
                latency_ms=0,
                status="error",
                timestamp=now,
                error=f"Unexpected output: {resp[:200]}",
            )

        except Exception as e:
            return LatencySample(
                source=source,
                target=target,
                route=host,
                protocol="tcp",
                latency_ms=0,
                status="error",
                timestamp=now,
                error=str(e)[:200],
            )


class ContinuousLatencyProber(ContinuousProberBase):
    """Runs latency probing in a background thread during chaos experiments.

    Takes periodic measurements and provides before/during/after comparison
    data with per-route latency statistics.

    Usage::

        prober = ContinuousLatencyProber("online-boutique")
        prober.start()
        # ... run chaos experiment ...
        prober.stop()
        data = prober.result()
    """

    def __init__(
        self,
        namespace: str,
        interval: float = 2.0,
        timeout_seconds: int = 5,
        http_routes: Optional[List[Tuple[str, str, str, str]]] = None,
        exclude_prefixes: Optional[List[str]] = None,
        expected_chaos_duration: Optional[float] = None,
    ):
        super().__init__(namespace, interval, name="latency-prober")
        self._http_routes = http_routes
        self._prober = LatencyProber(
            namespace, timeout_seconds, exclude_prefixes=exclude_prefixes,
        )
        self._cached_probe_pod: Optional[str] = None
        self._expected_chaos_duration = expected_chaos_duration

    def start(self) -> None:
        """Start continuous latency probing in background."""
        # Resolve the probe pod once at start.  Try all candidate pods
        # until one passes the pre-flight connectivity check.
        candidates = self._prober._find_all_probe_pods()
        if not candidates:
            candidates = []
            first = self._prober._find_probe_pod()
            if first:
                candidates = [first]

        if self._http_routes:
            preflight_service = self._http_routes[0][0]
            preflight_path = self._http_routes[0][1]
        else:
            preflight_service = "frontend"
            preflight_path = "/_healthz"
        test_url = f"http://{preflight_service}.{self.namespace}.svc.cluster.local{preflight_path}"

        for pod in candidates:
            sample = self._prober._measure_http_from_pod(
                pod, test_url, "GET", preflight_path,
            )
            if sample.status == "ok":
                self._cached_probe_pod = pod
                logger.info(
                    "Latency prober pre-flight OK: pod %s → %s (%.1fms)",
                    pod, test_url, sample.latency_ms,
                )
                break
            else:
                logger.info(
                    "Pre-flight failed from pod %s: %s — trying next candidate",
                    pod, sample.error,
                )

        if not self._cached_probe_pod:
            if candidates:
                self._cached_probe_pod = candidates[0]
                logger.warning(
                    "Latency prober pre-flight FAILED from all %d candidate pods "
                    "— using %s anyway. All latency samples will likely be errors.",
                    len(candidates), self._cached_probe_pod,
                )
            else:
                logger.warning("No probe pod found at start — will retry during probing")

        super().start()

    def result(self) -> Dict[str, Any]:
        """Return structured latency time series and phase summaries."""
        with self._lock:
            series = list(self._time_series)
            errors = self._probe_errors

        phases = self._split_phases(series)
        data: Dict[str, Any] = {
            "timeSeries": series,
            "phases": phases,
            "config": {
                "interval_s": self.interval,
                "namespace": self.namespace,
            },
        }
        if errors > 0:
            data["probeErrors"] = errors
        return data

    def _probe_loop(self) -> None:
        """Main probe loop running in the background."""
        consecutive_failures = 0
        while not self._stop_event.is_set():
            try:
                http_results = self._prober.measure_http_routes(
                    samples=1,
                    interval=0,
                    parallel=True,
                    probe_pod=self._cached_probe_pod,
                    http_routes=self._http_routes,
                )
                now = time.time()
                entry = self._make_entry(now, self._current_phase(now))
                entry["routes"] = {}

                for r in http_results:
                    if r.samples:
                        s = r.samples[0]
                        entry["routes"][r.route] = {
                            "latency_ms": s.latency_ms if s.status == "ok" else None,
                            "status": s.status,
                            "error": s.error,
                        }

                with self._lock:
                    self._time_series.append(entry)

                # If no routes were collected, the cached pod may be stale
                if not entry["routes"]:
                    consecutive_failures += 1
                    if consecutive_failures >= 2:
                        new_pod = self._prober._find_probe_pod()
                        if new_pod:
                            self._cached_probe_pod = new_pod
                            consecutive_failures = 0
                else:
                    consecutive_failures = 0

            except Exception as exc:
                logger.warning("Latency probe failed: %s", exc)
                with self._lock:
                    self._probe_errors += 1
                consecutive_failures += 1
                if consecutive_failures >= 2:
                    new_pod = self._prober._find_probe_pod()
                    if new_pod:
                        self._cached_probe_pod = new_pod
                        consecutive_failures = 0

            self._stop_event.wait(timeout=self.interval)

    def _split_phases(self, series: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Split time series into phases with per-route latency statistics."""
        phases: Dict[str, List[Dict[str, Any]]] = {
            "pre-chaos": [],
            "during-chaos": [],
            "post-chaos": [],
        }

        for entry in series:
            phase = entry.get("phase", "pre-chaos")
            phases.setdefault(phase, []).append(entry)

        result = {}
        for phase_name, entries in phases.items():
            if not entries:
                result[phase_name] = {"sampleCount": 0, "routes": {}}
                continue

            route_latencies: Dict[str, List[float]] = {}
            route_errors: Dict[str, int] = {}

            for entry in entries:
                for route, data in entry.get("routes", {}).items():
                    route_latencies.setdefault(route, [])
                    route_errors.setdefault(route, 0)
                    if data.get("latency_ms") is not None:
                        route_latencies[route].append(data["latency_ms"])
                    if data.get("status") != "ok":
                        route_errors[route] += 1

            routes_summary = {}
            for route, latencies in route_latencies.items():
                if latencies:
                    sorted_lats = sorted(latencies)
                    p95_idx = min(int(len(sorted_lats) * 0.95), len(sorted_lats) - 1)
                    routes_summary[route] = {
                        "mean_ms": round(statistics.mean(latencies), 2),
                        "median_ms": round(statistics.median(latencies), 2),
                        "p95_ms": round(sorted_lats[p95_idx], 2),
                        "min_ms": round(min(latencies), 2),
                        "max_ms": round(max(latencies), 2),
                        "sampleCount": len(latencies),
                        "errorCount": route_errors.get(route, 0),
                    }
                else:
                    routes_summary[route] = {
                        "mean_ms": None,
                        "sampleCount": 0,
                        "errorCount": route_errors.get(route, 0),
                    }

            result[phase_name] = {
                "sampleCount": len(entries),
                "routes": routes_summary,
            }

        return result
