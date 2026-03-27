"""Inter-service latency measurement via in-cluster probing.

Measures request latency between Online Boutique microservices by executing
curl commands inside pods. This captures real service-to-service communication
times within the cluster, including DNS resolution, network traversal, and
application processing.

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

from kubernetes import client, config

logger = logging.getLogger(__name__)
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream


# Online Boutique service dependency graph
# Each entry: (source_service, target_service, target_host, protocol, description)
ONLINE_BOUTIQUE_ROUTES: List[Tuple[str, str, str, str, str]] = [
    # Frontend → backend calls
    ("frontend", "productcatalogservice", "productcatalogservice:3550", "grpc", "Product listing"),
    ("frontend", "currencyservice", "currencyservice:7000", "grpc", "Currency conversion"),
    ("frontend", "cartservice", "cartservice:7070", "grpc", "Cart operations"),
    ("frontend", "recommendationservice", "recommendationservice:8080", "grpc", "Recommendations"),
    ("frontend", "checkoutservice", "checkoutservice:5050", "grpc", "Checkout flow"),
    ("frontend", "adservice", "adservice:9555", "grpc", "Ad serving"),
    ("frontend", "shippingservice", "shippingservice:50051", "grpc", "Shipping quotes"),
    # Checkout → downstream calls
    ("checkoutservice", "productcatalogservice", "productcatalogservice:3550", "grpc", "Product lookup"),
    ("checkoutservice", "cartservice", "cartservice:7070", "grpc", "Cart retrieval"),
    ("checkoutservice", "currencyservice", "currencyservice:7000", "grpc", "Price conversion"),
    ("checkoutservice", "shippingservice", "shippingservice:50051", "grpc", "Shipping cost"),
    ("checkoutservice", "paymentservice", "paymentservice:50051", "grpc", "Payment processing"),
    ("checkoutservice", "emailservice", "emailservice:8080", "grpc", "Order confirmation"),
    # Recommendation → product catalog
    ("recommendationservice", "productcatalogservice", "productcatalogservice:3550", "grpc", "Product list"),
    # Cart → Redis
    ("cartservice", "redis-cart", "redis-cart:6379", "tcp", "Session storage"),
]

# HTTP routes for latency measurement (via frontend)
ONLINE_BOUTIQUE_HTTP_ROUTES: List[Tuple[str, str, str, str]] = [
    ("frontend", "/", "Homepage (product catalog + recommendations + ads)", "GET"),
    ("frontend", "/product/OLJCESPC7Z", "Product page (product catalog + currency)", "GET"),
    ("frontend", "/cart", "Cart page (cart service + recommendations)", "GET"),
    ("frontend", "/_healthz", "Health check (local)", "GET"),
]


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

    def __init__(self, namespace: str, timeout_seconds: int = 5):
        self.namespace = namespace
        self.timeout_seconds = timeout_seconds

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.core_api = client.CoreV1Api()

    def measure_http_routes(
        self,
        samples: int = 10,
        interval: float = 1.0,
        parallel: bool = False,
        probe_pod: Optional[str] = None,
    ) -> List[LatencyResult]:
        """Measure latency for all HTTP routes via the frontend service.

        Args:
            samples: Number of samples per route.
            interval: Seconds between samples.
            parallel: If True, measure all routes concurrently per sample round.
            probe_pod: Optional pre-resolved pod name to use for probing.
                       If None, a suitable pod is discovered automatically.

        Returns:
            List of LatencyResult for each route.
        """
        # Use loadgenerator pod for HTTP measurements — it has Python and
        # proper tools, unlike the distroless frontend/Go pods.
        if probe_pod is None:
            probe_pod = self._find_probe_pod()
        if not probe_pod:
            return []

        routes_info = [
            (src, route, desc, method)
            for src, route, desc, method in ONLINE_BOUTIQUE_HTTP_ROUTES
        ]
        result_map = {}
        for source, route, description, _method in routes_info:
            result_map[route] = LatencyResult(
                source=source,
                target="frontend",
                route=route,
                protocol="http",
                description=description,
            )

        if parallel:
            with ThreadPoolExecutor(max_workers=len(routes_info)) as pool:
                for _ in range(samples):
                    futures = {}
                    for _src, route, _desc, method in routes_info:
                        url = f"http://frontend.{self.namespace}.svc.cluster.local{route}"
                        fut = pool.submit(
                            self._measure_http_from_pod,
                            probe_pod, url, method, route,
                        )
                        futures[fut] = route
                    for fut in as_completed(futures):
                        result_map[futures[fut]].samples.append(fut.result())
                    if interval > 0:
                        time.sleep(interval)
        else:
            for _src, route, _desc, method in routes_info:
                url = f"http://frontend.{self.namespace}.svc.cluster.local{route}"
                for _ in range(samples):
                    sample = self._measure_http_from_pod(
                        probe_pod, url, method, route
                    )
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
        For Redis, measures TCP connection time to port 6379.

        Args:
            routes: Service routes to measure. Uses ONLINE_BOUTIQUE_ROUTES if None.
            samples: Number of samples per route.
            interval: Seconds between samples.
            parallel: If True, measure all pairs concurrently per sample round.

        Returns:
            List of LatencyResult for each service pair.
        """
        if routes is None:
            routes = ONLINE_BOUTIQUE_ROUTES

        # Use a single probe pod for all TCP measurements. Many Online
        # Boutique source pods are distroless (no shell), so we run all
        # probes from the loadgenerator or another pod with a shell.
        probe_pod = self._find_probe_pod()
        if not probe_pod:
            return []

        result_list = []
        valid_routes = []
        for i, (source, target, host, protocol, description) in enumerate(routes):
            result_list.append(LatencyResult(
                source=source, target=target, route=host,
                protocol=protocol, description=description,
            ))
            valid_routes.append((i, source, target, host))

        if parallel and len(valid_routes) > 1:
            with ThreadPoolExecutor(max_workers=min(len(valid_routes), 8)) as pool:
                for _ in range(samples):
                    futures = {}
                    for idx, (ri, source, target, host) in enumerate(valid_routes):
                        fut = pool.submit(
                            self._measure_tcp_from_pod,
                            probe_pod, host, source, target,
                        )
                        futures[fut] = idx
                    for fut in as_completed(futures):
                        result_list[futures[fut]].samples.append(fut.result())
                    if interval > 0:
                        time.sleep(interval)
        else:
            for idx, (ri, source, target, host) in enumerate(valid_routes):
                for _ in range(samples):
                    sample = self._measure_tcp_from_pod(
                        probe_pod, host, source, target
                    )
                    result_list[idx].samples.append(sample)
                    if interval > 0:
                        time.sleep(interval)

        return result_list

    def measure_all(
        self,
        samples: int = 10,
        interval: float = 1.0,
        parallel: bool = True,
    ) -> Dict[str, Any]:
        """Measure both HTTP routes and inter-service latency.

        Args:
            samples: Number of samples per measurement.
            interval: Seconds between samples.
            parallel: If True, measure routes concurrently.

        Returns:
            Dictionary with HTTP route latencies and service pair latencies.
        """
        http_results = self.measure_http_routes(
            samples=samples, interval=interval, parallel=parallel,
        )
        service_results = self.measure_service_pairs(
            samples=samples, interval=interval, parallel=parallel,
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

    def _find_probe_pod(self) -> Optional[str]:
        """Find a pod suitable for running probe commands.

        Many Online Boutique services use distroless images (no shell).
        We prefer pods that have Python (nanosecond-precision timing) and
        HTTP tools. Candidates in order: loadgenerator, currencyservice,
        emailservice, recommendationservice, adservice, paymentservice.
        """
        for svc in [
            "loadgenerator", "currencyservice", "emailservice",
            "recommendationservice", "adservice", "paymentservice",
        ]:
            pod = self._find_ready_pod(svc)
            if pod:
                return pod
        # Last resort: try frontend (will fail on distroless)
        return self._find_ready_pod("frontend")

    @staticmethod
    def _nano_time_cmd() -> str:
        """Return a shell snippet that prints epoch nanoseconds.

        Tries python3 first (nanosecond precision), then GNU date +%s%N
        (also nanosecond), and finally falls back to date +%s with
        millisecond granularity by appending '000000'.
        Handles busybox date which outputs literal '%N' instead of
        nanoseconds.
        """
        return (
            "python3 -c 'import time;print(int(time.time()*1e9))' 2>/dev/null"
            " || { T=$(date +%s%N 2>/dev/null); "
            "case \"$T\" in *N*|*%*) echo $(date +%s)000000000;; "
            "*) [ ${#T} -gt 15 ] && echo $T || echo ${T}000000000;; esac; }"
        )

    def _measure_http_from_pod(
        self,
        pod_name: str,
        url: str,
        method: str,
        route: str,
    ) -> LatencySample:
        """Measure HTTP latency by executing a request inside a pod.

        Tries Python urllib first (nanosecond timing, no external deps),
        then falls back to wget + shell timing.
        """
        now = datetime.now(timezone.utc).isoformat()
        # Python one-liner: precise timing + HTTP request in one shot.
        # Outputs: "<status_code> <start_ns> <end_ns>" or "ERR <msg>"
        py_script = (
            "import time,urllib.request as u;"
            f"s=int(time.time()*1e9);"
            f"r=u.urlopen('{url}',timeout={self.timeout_seconds});"
            f"_ =r.read();"
            f"e=int(time.time()*1e9);"
            f"print(r.status,s,e)"
        )
        cmd = [
            "python3", "-c", py_script
        ]

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
            if len(parts) >= 3:
                status_code = int(parts[0])
                start_ns = int(parts[1])
                end_ns = int(parts[2])
                if start_ns > 0 and end_ns > 0:
                    latency_ms = (end_ns - start_ns) / 1_000_000
                    ok = 200 <= status_code < 400
                    return LatencySample(
                        source="loadgenerator",
                        target="frontend",
                        route=route,
                        protocol="http",
                        latency_ms=round(latency_ms, 2),
                        status="ok" if ok else "error",
                        timestamp=now,
                        error=None if ok else f"HTTP {status_code}",
                    )

            return LatencySample(
                source="loadgenerator", target="frontend", route=route,
                protocol="http", latency_ms=0, status="error",
                timestamp=now, error=f"Unexpected output: {resp[:200]}",
            )

        except Exception as e:
            return LatencySample(
                source="loadgenerator", target="frontend", route=route,
                protocol="http", latency_ms=0, status="error",
                timestamp=now, error=str(e)[:200],
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
            f"s=socket.socket();"
            f"s.settimeout({self.timeout_seconds});"
            f"t0=int(time.time()*1e9);"
            f"s.connect(('{hostname}',{port}));"
            f"t1=int(time.time()*1e9);"
            f"s.close();"
            f"print(t0,t1)"
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
                if start_ns > 0 and end_ns > 0:
                    latency_ms = (end_ns - start_ns) / 1_000_000
                    return LatencySample(
                        source=source, target=target, route=host,
                        protocol="tcp", latency_ms=round(latency_ms, 2),
                        status="ok", timestamp=now,
                    )

            return LatencySample(
                source=source, target=target, route=host,
                protocol="tcp", latency_ms=0, status="error",
                timestamp=now, error=f"Unexpected output: {resp[:200]}",
            )

        except Exception as e:
            return LatencySample(
                source=source, target=target, route=host,
                protocol="tcp", latency_ms=0, status="error",
                timestamp=now, error=str(e)[:200],
            )


class ContinuousLatencyProber:
    """Runs latency probing in a background thread during chaos experiments.

    Similar to RecoveryWatcher but for latency. Takes periodic measurements
    and provides before/during/after comparison data.

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
    ):
        self.namespace = namespace
        self.interval = interval
        self._prober = LatencyProber(namespace, timeout_seconds)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._time_series: List[Dict[str, Any]] = []
        self._start_time: Optional[float] = None
        self._chaos_start_time: Optional[float] = None
        self._chaos_end_time: Optional[float] = None
        self._probe_errors: int = 0
        self._cached_probe_pod: Optional[str] = None

    def start(self) -> None:
        """Start continuous latency probing in background."""
        self._start_time = time.time()
        # Resolve the probe pod once at start to avoid repeated K8s API
        # lookups on every measurement cycle.
        self._cached_probe_pod = self._prober._find_probe_pod()
        if not self._cached_probe_pod:
            logger.warning("No probe pod found at start — will retry during probing")
        self._thread = threading.Thread(
            target=self._probe_loop, daemon=True, name="latency-prober"
        )
        self._thread.start()

    def mark_chaos_start(self) -> None:
        """Mark the start of the chaos injection phase."""
        with self._lock:
            self._chaos_start_time = time.time()

    def mark_chaos_end(self) -> None:
        """Mark the end of the chaos injection phase."""
        with self._lock:
            self._chaos_end_time = time.time()

    def stop(self) -> None:
        """Stop the probing thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

    def result(self) -> Dict[str, Any]:
        """Return structured latency time series and phase summaries."""
        with self._lock:
            series = list(self._time_series)

        phases = self._split_phases(series)
        data: Dict[str, Any] = {
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

    def _probe_loop(self) -> None:
        """Main probe loop running in the background."""
        consecutive_failures = 0
        while not self._stop_event.is_set():
            try:
                http_results = self._prober.measure_http_routes(
                    samples=1, interval=0, parallel=True,
                    probe_pod=self._cached_probe_pod,
                )
                now = time.time()
                timestamp = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()

                entry = {
                    "timestamp": timestamp,
                    "elapsed_s": round(now - (self._start_time or now), 1),
                    "phase": self._current_phase(now),
                    "routes": {},
                }

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

    def _current_phase(self, now: float) -> str:
        """Determine the current experiment phase."""
        with self._lock:
            chaos_start = self._chaos_start_time
            chaos_end = self._chaos_end_time
        if chaos_start is None:
            return "pre-chaos"
        if chaos_end is None:
            return "during-chaos"
        return "post-chaos"

    def _split_phases(self, series: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Split time series into phases and compute per-phase summaries."""
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
