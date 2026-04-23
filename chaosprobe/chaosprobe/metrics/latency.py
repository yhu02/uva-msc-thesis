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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from kubernetes import client
from kubernetes.stream import stream

from chaosprobe.k8s import ensure_k8s_config
from chaosprobe.metrics.base import (
    ContinuousProberBase,
    find_all_probe_pods,
    find_all_probe_pods_with_node,
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


def _aggregate_latency_samples(
    per_pod_samples: List[Tuple[str, str, LatencySample]],
) -> Dict[str, Any]:
    """Aggregate per-pod HTTP latency samples into a single tick-level record.

    Parameters
    ----------
    per_pod_samples:
        ``[(pod_name, node_name, LatencySample), ...]`` — one sample per
        probed pod.  Multiple pods per node are allowed and expected:
        same-node pods can still take different network paths via
        service load-balancing (kube-proxy/IPVS conntrack hash) and can
        be subject to different per-pod network policies or sidecars.

    Returns a dict whose flat fields (``latency_ms``, ``status``) provide
    the tick-level aggregate (mean across all probed pods).
    Cross-pod distribution fields (``probeCount``, ``stddevLatency_ms``,
    ``minLatency_ms``, ``maxLatency_ms``) aggregate across all probes.
    ``perPod`` carries raw per-pod results; ``perNode`` aggregates those
    across same-node pods for placement analysis.
    """
    ok = [(p, n, s) for p, n, s in per_pod_samples if s.status == "ok"]
    err = [(p, n, s) for p, n, s in per_pod_samples if s.status != "ok"]

    per_pod: Dict[str, Dict[str, Any]] = {}
    by_node: Dict[str, List[LatencySample]] = {}
    for pod, node, s in per_pod_samples:
        per_pod[pod] = {
            "node": node,
            "latency_ms": s.latency_ms if s.status == "ok" else None,
            "status": s.status,
        }
        if s.status != "ok" and s.error:
            per_pod[pod]["error"] = s.error[:200]
        by_node.setdefault(node, []).append(s)

    per_node: Dict[str, Dict[str, Any]] = {}
    for node, samples in by_node.items():
        ok_node = [s for s in samples if s.status == "ok"]
        lats_node = [s.latency_ms for s in ok_node]
        per_node[node] = {
            "podCount": len(samples),
            "okCount": len(ok_node),
            "errorCount": len(samples) - len(ok_node),
            "mean_ms": (
                round(statistics.mean(lats_node), 2) if lats_node else None
            ),
            "stddev_ms": (
                round(statistics.stdev(lats_node), 2)
                if len(lats_node) > 1 else 0.0
            ),
        }

    if not ok:
        entry: Dict[str, Any] = {
            "latency_ms": None,
            "status": "error",
            "probeCount": 0,
            "errorCount": len(err),
            "perPod": per_pod,
            "perNode": per_node,
        }
        if err and err[0][2].error:
            entry["error"] = err[0][2].error[:200]
        return entry

    lats = [s.latency_ms for _, _, s in ok]
    return {
        "latency_ms": round(statistics.mean(lats), 2),
        "status": "ok",
        "probeCount": len(ok),
        "errorCount": len(err),
        "minLatency_ms": round(min(lats), 2),
        "maxLatency_ms": round(max(lats), 2),
        "stddevLatency_ms": (
            round(statistics.stdev(lats), 2) if len(lats) > 1 else 0.0
        ),
        "perPod": per_pod,
        "perNode": per_node,
    }


class ContinuousLatencyProber(ContinuousProberBase):
    """Runs HTTP latency probing from every eligible pod during chaos.

    Each pod is an independent vantage point even when co-located on the
    same node: service load-balancing (kube-proxy/IPVS conntrack hash)
    can route same-node pods to different target endpoints, and per-pod
    network policies or sidecars can shape traffic differently.
    Probing every pod therefore gives more independent samples than
    one-per-node and is the right granularity for the placement
    analysis this prober supports.

    At ``start()`` every ready pod with a shell (+ python3) is
    discovered.  Each tick runs the HTTP probe in parallel from every
    vantage point.  Per-tick records carry:

    * ``latency_ms`` / ``status`` — flat aggregate fields (mean across
      pods; ``status='ok'`` if any probe succeeded);
    * ``probeCount`` / ``errorCount`` — how many vantage points
      contributed / failed;
    * ``minLatency_ms`` / ``maxLatency_ms`` / ``stddevLatency_ms`` —
      cross-pod variance;
    * ``perPod`` — raw per-pod samples keyed by pod name;
    * ``perNode`` — same samples aggregated per scheduling node, for
      placement analysis.

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
        # [(pod_name, node_name), ...] populated in start()
        self._probe_points: List[Tuple[str, str]] = []
        self._expected_chaos_duration = expected_chaos_duration

    def start(self) -> None:
        """Discover every eligible probe pod (all ready pods with python3)."""
        self._probe_points = find_all_probe_pods_with_node(
            self._prober.core_api,
            self._prober.namespace,
            require_python3=True,
            exclude_prefixes=self._prober._exclude_prefixes,
        )

        if self._probe_points:
            node_counts: Dict[str, int] = {}
            for _pod, node in self._probe_points:
                node_counts[node] = node_counts.get(node, 0) + 1
            logger.info(
                "Latency prober probing %d pod(s) across %d node(s): %s",
                len(self._probe_points),
                len(node_counts),
                ", ".join(
                    f"{n}({c} pods)" for n, c in sorted(node_counts.items())
                ),
            )
        else:
            # Fallback: keep at least one vantage point if discovery
            # returned nothing (rare — e.g. every pod freshly-created).
            first = self._prober._find_probe_pod()
            if first:
                self._probe_points.append((first, "unknown"))
                logger.warning(
                    "Latency prober: no pods discovered — falling back to %s",
                    first,
                )
            else:
                logger.warning(
                    "Latency prober: no probe pod found at start — will retry",
                )

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
            "probePoints": [
                {"pod": p, "node": n} for p, n in self._probe_points
            ],
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
        while not self._stop_event.is_set():
            try:
                now = time.time()
                entry = self._make_entry(now, self._current_phase(now))
                entry["routes"] = self._run_all_probes()

                with self._lock:
                    self._time_series.append(entry)

            except Exception as exc:
                logger.warning("Latency probe failed: %s", exc)
                with self._lock:
                    self._probe_errors += 1

            self._stop_event.wait(timeout=self.interval)

    def _run_all_probes(self) -> Dict[str, Any]:
        """Probe each route from every probe pod in parallel."""
        if not self._probe_points or not self._http_routes:
            return {}

        # per_route_samples[route] = [(pod, node, LatencySample), ...]
        per_route_samples: Dict[str, List[Tuple[str, str, LatencySample]]] = {
            route: [] for _svc, route, _d, _m in self._http_routes
        }

        def _probe_one(
            pod: str, node: str,
        ) -> List[Tuple[str, str, LatencySample]]:
            results: List[Tuple[str, str, LatencySample]] = []
            for service, route, _desc, method in self._http_routes:
                url = (
                    f"http://{service}.{self.namespace}.svc.cluster.local{route}"
                )
                sample = self._prober._measure_http_from_pod(
                    pod, url, method, route,
                )
                results.append((pod, node, sample))
            return results

        with ThreadPoolExecutor(max_workers=min(len(self._probe_points), 8)) as pool:
            futs = [
                pool.submit(_probe_one, pod, node)
                for pod, node in self._probe_points
            ]
            for f in futs:
                try:
                    for pod, node, sample in f.result():
                        per_route_samples.setdefault(sample.route, []).append(
                            (pod, node, sample),
                        )
                except Exception as exc:
                    logger.warning("per-pod latency probe raised: %s", exc)

        return {
            route: _aggregate_latency_samples(samples)
            for route, samples in per_route_samples.items()
            if samples
        }

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
            route_stddevs: Dict[str, List[float]] = {}
            route_maxes: Dict[str, List[float]] = {}

            for entry in entries:
                for route, data in entry.get("routes", {}).items():
                    route_latencies.setdefault(route, [])
                    route_errors.setdefault(route, 0)
                    route_stddevs.setdefault(route, [])
                    route_maxes.setdefault(route, [])
                    if data.get("latency_ms") is not None:
                        route_latencies[route].append(data["latency_ms"])
                    if data.get("status") != "ok":
                        route_errors[route] += 1
                    # Capture per-tick cross-node spread so the phase summary
                    # can surface "how different were the vantage points?"
                    if data.get("stddevLatency_ms") is not None:
                        route_stddevs[route].append(data["stddevLatency_ms"])
                    if data.get("maxLatency_ms") is not None:
                        route_maxes[route].append(data["maxLatency_ms"])

            routes_summary = {}
            for route, latencies in route_latencies.items():
                if latencies:
                    sorted_lats = sorted(latencies)
                    p95_idx = min(int(len(sorted_lats) * 0.95), len(sorted_lats) - 1)
                    summary = {
                        "mean_ms": round(statistics.mean(latencies), 2),
                        "median_ms": round(statistics.median(latencies), 2),
                        "p95_ms": round(sorted_lats[p95_idx], 2),
                        "min_ms": round(min(latencies), 2),
                        "max_ms": round(max(latencies), 2),
                        "sampleCount": len(latencies),
                        "errorCount": route_errors.get(route, 0),
                    }
                    stddevs = route_stddevs.get(route, [])
                    if stddevs:
                        summary["meanCrossNodeStddev_ms"] = round(
                            statistics.mean(stddevs), 2,
                        )
                    maxes = route_maxes.get(route, [])
                    if maxes:
                        summary["maxCrossNodeLatency_ms"] = round(
                            max(maxes), 2,
                        )
                    routes_summary[route] = summary
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
