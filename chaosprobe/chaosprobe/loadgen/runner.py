"""Locust-based load generator for ChaosProbe.

Provides controllable load patterns and live metric collection
to replace the passive Google loadgenerator.
"""

import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _safe_int(value: Any) -> int:
    """int() a Locust CSV cell, tolerating missing/non-numeric values (e.g. "N/A")."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    """float() a Locust CSV cell, tolerating missing/non-numeric values (e.g. "N/A")."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class LoadProfile:
    """Configuration for a load generation profile."""

    name: str
    users: int
    spawn_rate: int
    duration_seconds: int

    PROFILES = {
        "steady": {"users": 50, "spawn_rate": 10, "duration_seconds": 120},
        "ramp": {"users": 100, "spawn_rate": 5, "duration_seconds": 180},
        "spike": {"users": 200, "spawn_rate": 50, "duration_seconds": 90},
    }

    @classmethod
    def from_name(cls, name: str) -> "LoadProfile":
        """Create a LoadProfile from a predefined profile name."""
        if name not in cls.PROFILES:
            raise ValueError(
                f"Unknown load profile '{name}'. "
                f"Valid profiles: {', '.join(cls.PROFILES.keys())}"
            )
        return cls(name=name, **cls.PROFILES[name])

    @classmethod
    def custom(cls, users: int, spawn_rate: int, duration_seconds: int) -> "LoadProfile":
        return cls(
            name="custom",
            users=users,
            spawn_rate=spawn_rate,
            duration_seconds=duration_seconds,
        )


@dataclass
class LoadStats:
    """Collected statistics from a load generation run."""

    total_requests: int = 0
    total_failures: int = 0
    avg_response_time_ms: float = 0.0
    min_response_time_ms: float = 0.0
    max_response_time_ms: float = 0.0
    p50_response_time_ms: float = 0.0
    p95_response_time_ms: float = 0.0
    p99_response_time_ms: float = 0.0
    requests_per_second: float = 0.0
    failures_per_second: float = 0.0
    error_rate: float = 0.0
    duration_seconds: float = 0.0
    endpoints: List[Dict[str, Any]] = field(default_factory=list)
    # Per-failure-class counts parsed from Locust's stats_failures.csv:
    # one entry per (method, name, error message) tuple.  Aggregated
    # error rate alone hides whether a strategy's load drift is from
    # timeouts vs connection refused vs HTTP 5xx — each implies a
    # different mechanism (network programming SLO breach, kernel
    # conntrack churn, app-side circuit breaker).
    failure_classes: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "totalRequests": self.total_requests,
            "totalFailures": self.total_failures,
            "avgResponseTime_ms": self.avg_response_time_ms,
            "minResponseTime_ms": self.min_response_time_ms,
            "maxResponseTime_ms": self.max_response_time_ms,
            "p50ResponseTime_ms": self.p50_response_time_ms,
            "p95ResponseTime_ms": self.p95_response_time_ms,
            "p99ResponseTime_ms": self.p99_response_time_ms,
            "requestsPerSecond": self.requests_per_second,
            "failuresPerSecond": self.failures_per_second,
            "errorRate": self.error_rate,
            "duration_seconds": self.duration_seconds,
            "endpoints": self.endpoints,
            "failureClasses": self.failure_classes,
        }


DEFAULT_LOCUSTFILE = """\"\"\"Default Locust load test for a web frontend.

Seeded for reproducible per-iteration load patterns.  Locust has no
``--seed`` CLI flag, so we seed the ``random`` module that
``between(...)``, ``constant_pacing``, and task selection draw from.

The seed runs at locustfile import time, before any user spawns.  Each
chaos iteration starts a fresh Locust process, so the seed re-applies
and request timing reproduces across iterations.  Note: gevent
greenlet scheduling adds some non-determinism on top, but the
dominant variance source (wait_time + task choice) is now fixed.

Override the seed by setting LOCUST_RANDOM_SEED in the environment.
\"\"\"

import os
import random

random.seed(int(os.environ.get(\"LOCUST_RANDOM_SEED\", \"42\")))

from locust import HttpUser, task, between


class FrontendUser(HttpUser):
    \"\"\"Simulates a user browsing a web storefront.\"\"\"

    wait_time = between(1, 5)

    @task(10)
    def index(self):
        self.client.get("/")

    @task(5)
    def browse_product(self):
        self.client.get("/product/OLJCESPC7Z")

    @task(3)
    def view_cart(self):
        self.client.get("/cart")

    @task(2)
    def add_to_cart(self):
        self.client.post(
            "/cart",
            json={
                "product_id": "OLJCESPC7Z",
                "quantity": 1,
            },
        )

    @task(1)
    def checkout(self):
        self.client.post(
            "/cart/checkout",
            json={
                "email": "user@example.com",
                "street_address": "1600 Amphitheatre Parkway",
                "zip_code": "94043",
                "city": "Mountain View",
                "state": "CA",
                "country": "United States",
                "credit_card_number": "4432-8015-6152-0454",
                "credit_card_expiration_month": 1,
                "credit_card_expiration_year": 2030,
                "credit_card_cvv": 672,
            },
        )
"""


class LocustRunner:
    """Manages Locust load generation runs.

    Starts Locust headlessly, collects CSV stats, and parses them
    into structured LoadStats.
    """

    def __init__(
        self,
        target_url: str,
        locustfile: Optional[str] = None,
    ):
        """Initialize the Locust runner.

        Args:
            target_url: Base URL of the service under test.
            locustfile: Path to a custom locustfile. Uses built-in default if None.
        """
        self.target_url = target_url
        self._custom_locustfile = locustfile
        self._process: Optional[subprocess.Popen] = None
        self._stats_dir: Optional[str] = None
        self._start_time: Optional[float] = None
        self._end_time: Optional[float] = None
        self._temp_dirs: List[str] = []

    def __enter__(self) -> "LocustRunner":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
        self.cleanup()

    def _get_locustfile(self) -> str:
        """Return path to the locustfile, creating default if needed."""
        if self._custom_locustfile:
            return self._custom_locustfile

        tmpdir = tempfile.mkdtemp(prefix="chaosprobe-locust-")
        self._temp_dirs.append(tmpdir)
        locustfile_path = os.path.join(tmpdir, "locustfile.py")
        with open(locustfile_path, "w") as f:
            f.write(DEFAULT_LOCUSTFILE)
        return locustfile_path

    def start(self, profile: LoadProfile) -> None:
        """Start Locust load generation in the background.

        Args:
            profile: Load profile configuration.
        """
        locustfile = self._get_locustfile()
        self._stats_dir = tempfile.mkdtemp(prefix="chaosprobe-locust-stats-")
        self._temp_dirs.append(self._stats_dir)
        stats_prefix = os.path.join(self._stats_dir, "stats")

        cmd = [
            os.path.join(os.path.dirname(sys.executable), "locust"),
            "--headless",
            "--locustfile",
            locustfile,
            "--host",
            self.target_url,
            "--users",
            str(profile.users),
            "--spawn-rate",
            str(profile.spawn_rate),
            "--run-time",
            f"{profile.duration_seconds}s",
            "--csv",
            stats_prefix,
            "--csv-full-history",
            "--only-summary",
        ]

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            cwd=self._stats_dir,
        )
        self._start_time = time.time()

        # Give Locust a moment to start up and catch immediate failures
        time.sleep(2)
        if self._process.poll() is not None:
            stderr_output = ""
            if self._process.stderr:
                stderr_output = self._process.stderr.read().decode(errors="replace").strip()
            raise RuntimeError(
                f"Locust exited immediately (code={self._process.returncode}). "
                f"stderr: {stderr_output[:500]}"
            )

    def is_running(self) -> bool:
        """Check if the Locust process is still running."""
        return self._process is not None and self._process.poll() is None

    def stop(self) -> None:
        """Stop the running Locust process."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        # Drain stderr to avoid broken-pipe issues
        if self._process and self._process.stderr:
            try:
                self._process.stderr.read()
                self._process.stderr.close()
            except Exception:
                logger.debug("failed to drain Locust stderr on stop", exc_info=True)
        self._end_time = time.time()

    def wait(self) -> None:
        """Wait for Locust to complete its run."""
        if self._process:
            self._process.wait()
            self._end_time = time.time()

    def collect_stats(self) -> LoadStats:
        """Parse Locust CSV output into structured LoadStats.

        Returns:
            Collected load generation statistics.
        """
        if not self._stats_dir:
            return LoadStats()

        stats = LoadStats()
        duration = (self._end_time or time.time()) - (self._start_time or time.time())
        stats.duration_seconds = round(duration, 1)

        # Parse failures CSV (Locust generates stats_failures.csv) — best
        # effort, leave the list empty if the file isn't there (no
        # failures during the run, or an older Locust that doesn't
        # emit it).
        failures_file = os.path.join(self._stats_dir, "stats_failures.csv")
        if os.path.exists(failures_file):
            stats.failure_classes = self._parse_failures_csv(failures_file)

        # Parse stats CSV (Locust generates stats_stats.csv)
        stats_file = os.path.join(self._stats_dir, "stats_stats.csv")
        if os.path.exists(stats_file):
            stats = self._parse_stats_csv(stats_file, stats)

        return stats

    def _parse_stats_csv(self, filepath: str, stats: LoadStats) -> LoadStats:
        """Parse Locust stats CSV file."""
        import csv

        endpoints = []
        with open(filepath, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("Name", "")
                req_type = row.get("Type", "")
                if name == "Aggregated" or req_type == "Aggregated":
                    stats.total_requests = _safe_int(row.get("Request Count"))
                    stats.total_failures = _safe_int(row.get("Failure Count"))
                    stats.avg_response_time_ms = _safe_float(row.get("Average Response Time"))
                    stats.min_response_time_ms = _safe_float(row.get("Min Response Time"))
                    stats.max_response_time_ms = _safe_float(row.get("Max Response Time"))
                    stats.p50_response_time_ms = _safe_float(row.get("50%"))
                    stats.p95_response_time_ms = _safe_float(row.get("95%"))
                    stats.p99_response_time_ms = _safe_float(row.get("99%"))
                    stats.requests_per_second = _safe_float(row.get("Requests/s"))
                    stats.failures_per_second = _safe_float(row.get("Failures/s"))
                    if stats.total_requests > 0:
                        stats.error_rate = round(stats.total_failures / stats.total_requests, 4)
                else:
                    endpoints.append(
                        {
                            "method": row.get("Type", ""),
                            "name": name,
                            "requests": _safe_int(row.get("Request Count")),
                            "failures": _safe_int(row.get("Failure Count")),
                            "avgResponseTime_ms": _safe_float(row.get("Average Response Time")),
                            "p95ResponseTime_ms": _safe_float(row.get("95%")),
                        }
                    )

        stats.endpoints = endpoints
        return stats

    def _parse_failures_csv(self, filepath: str) -> List[Dict[str, Any]]:
        """Parse Locust's failure CSV: one row per (method, name, error).

        Columns: Method, Name, Error, Occurrences.  Missing or malformed
        Occurrences values default to 0 so a single bad row doesn't drop
        the rest.
        """
        import csv

        out: List[Dict[str, Any]] = []
        try:
            with open(filepath, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        occurrences = int(row.get("Occurrences", 0))
                    except (TypeError, ValueError):
                        occurrences = 0
                    out.append(
                        {
                            "method": row.get("Method", ""),
                            "name": row.get("Name", ""),
                            "error": row.get("Error", ""),
                            "occurrences": occurrences,
                        }
                    )
        except OSError:
            return []
        return out

    def cleanup(self) -> None:
        """Remove temporary directories created during load tests."""
        import shutil

        for tmpdir in self._temp_dirs:
            if os.path.exists(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
        self._temp_dirs.clear()
        self._stats_dir = None
