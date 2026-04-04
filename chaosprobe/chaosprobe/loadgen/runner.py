"""Locust-based load generator for ChaosProbe.

Provides controllable load patterns and live metric collection
to replace the passive Google loadgenerator.
"""

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
        }


DEFAULT_LOCUSTFILE = '''\"\"\"Default Locust load test for a web frontend.\"\"\"

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
'''


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
            "locust",
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
            stderr=subprocess.DEVNULL,
        )
        self._start_time = time.time()

    def stop(self) -> None:
        """Stop the running Locust process."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
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
                    stats.total_requests = int(row.get("Request Count", 0))
                    stats.total_failures = int(row.get("Failure Count", 0))
                    stats.avg_response_time_ms = float(row.get("Average Response Time", 0))
                    stats.min_response_time_ms = float(row.get("Min Response Time", 0))
                    stats.max_response_time_ms = float(row.get("Max Response Time", 0))
                    stats.p50_response_time_ms = float(row.get("50%", 0))
                    stats.p95_response_time_ms = float(row.get("95%", 0))
                    stats.p99_response_time_ms = float(row.get("99%", 0))
                    stats.requests_per_second = float(row.get("Requests/s", 0))
                    stats.failures_per_second = float(row.get("Failures/s", 0))
                    if stats.total_requests > 0:
                        stats.error_rate = round(stats.total_failures / stats.total_requests, 4)
                else:
                    endpoints.append(
                        {
                            "method": row.get("Type", ""),
                            "name": name,
                            "requests": int(row.get("Request Count", 0)),
                            "failures": int(row.get("Failure Count", 0)),
                            "avgResponseTime_ms": float(row.get("Average Response Time", 0)),
                            "p95ResponseTime_ms": float(row.get("95%", 0)),
                        }
                    )

        stats.endpoints = endpoints
        return stats

    def cleanup(self) -> None:
        """Remove temporary directories created during load tests."""
        import shutil

        for tmpdir in self._temp_dirs:
            if os.path.exists(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
        self._temp_dirs.clear()
        self._stats_dir = None
