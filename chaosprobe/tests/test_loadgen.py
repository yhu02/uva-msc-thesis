"""Tests for the load generation module."""

import os
import pytest

from chaosprobe.loadgen.runner import (
    LoadProfile,
    LoadStats,
    LocustRunner,
    DEFAULT_LOCUSTFILE,
)


class TestLoadProfile:
    """Tests for LoadProfile configuration."""

    def test_from_name_steady(self):
        profile = LoadProfile.from_name("steady")
        assert profile.name == "steady"
        assert profile.users == 50
        assert profile.spawn_rate == 10
        assert profile.duration_seconds == 120

    def test_from_name_ramp(self):
        profile = LoadProfile.from_name("ramp")
        assert profile.name == "ramp"
        assert profile.users == 100
        assert profile.spawn_rate == 5
        assert profile.duration_seconds == 180

    def test_from_name_spike(self):
        profile = LoadProfile.from_name("spike")
        assert profile.name == "spike"
        assert profile.users == 200
        assert profile.spawn_rate == 50
        assert profile.duration_seconds == 90

    def test_from_name_invalid(self):
        with pytest.raises(ValueError, match="Unknown load profile"):
            LoadProfile.from_name("unknown")

    def test_custom_profile(self):
        profile = LoadProfile.custom(users=10, spawn_rate=2, duration_seconds=60)
        assert profile.name == "custom"
        assert profile.users == 10


class TestLoadStats:
    """Tests for LoadStats data class."""

    def test_default_values(self):
        stats = LoadStats()
        assert stats.total_requests == 0
        assert stats.error_rate == 0.0
        assert stats.endpoints == []

    def test_to_dict(self):
        stats = LoadStats(
            total_requests=1000,
            total_failures=5,
            avg_response_time_ms=45.2,
            p95_response_time_ms=120.0,
            requests_per_second=33.3,
            error_rate=0.005,
        )
        d = stats.to_dict()
        assert d["totalRequests"] == 1000
        assert d["totalFailures"] == 5
        assert d["avgResponseTime_ms"] == 45.2
        assert d["p95ResponseTime_ms"] == 120.0
        assert d["requestsPerSecond"] == 33.3
        assert d["errorRate"] == 0.005


class TestLocustRunner:
    """Tests for LocustRunner (unit tests, no actual Locust execution)."""

    def test_get_locustfile_default(self):
        runner = LocustRunner(target_url="http://localhost:8080")
        path = runner._get_locustfile()
        assert os.path.exists(path)
        with open(path) as f:
            content = f.read()
        assert "OnlineBoutiqueUser" in content

    def test_get_locustfile_custom(self, tmp_path):
        custom_file = tmp_path / "custom_locustfile.py"
        custom_file.write_text("# custom locustfile")
        runner = LocustRunner(
            target_url="http://localhost:8080",
            locustfile=str(custom_file),
        )
        assert runner._get_locustfile() == str(custom_file)

    def test_parse_stats_csv(self, tmp_path):
        """Test parsing a Locust stats CSV file."""
        csv_file = tmp_path / "stats_stats.csv"
        csv_file.write_text(
            'Type,Name,Request Count,Failure Count,Average Response Time,'
            'Min Response Time,Max Response Time,Average Content Size,'
            'Requests/s,Failures/s,50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%\n'
            'GET,/,500,2,45.2,10.0,200.0,1024,16.7,0.07,40,50,60,70,90,120,150,180,195,199,200\n'
            'GET,/product/OLJCESPC7Z,300,1,55.0,15.0,250.0,2048,10.0,0.03,50,60,70,80,100,130,160,190,240,248,250\n'
            'Aggregated,,800,3,48.9,10.0,250.0,1408,26.7,0.1,44,54,64,74,94,124,154,184,215,245,250\n'
        )

        runner = LocustRunner(target_url="http://localhost:8080")
        runner._stats_dir = str(tmp_path)
        runner._start_time = 0.0
        runner._end_time = 30.0

        stats = LoadStats(duration_seconds=30.0)
        stats = runner._parse_stats_csv(str(csv_file), stats)

        assert stats.total_requests == 800
        assert stats.total_failures == 3
        assert stats.avg_response_time_ms == 48.9
        assert stats.p95_response_time_ms == 124.0
        assert stats.requests_per_second == 26.7
        assert stats.error_rate == pytest.approx(3 / 800, abs=1e-3)
        assert len(stats.endpoints) == 2
        assert stats.endpoints[0]["name"] == "/"
        assert stats.endpoints[1]["name"] == "/product/OLJCESPC7Z"

    def test_collect_stats_no_dir(self):
        """Test collect_stats returns empty stats when no dir is set."""
        runner = LocustRunner(target_url="http://localhost:8080")
        stats = runner.collect_stats()
        assert stats.total_requests == 0

    def test_default_locustfile_content(self):
        """Test that DEFAULT_LOCUSTFILE is valid Python syntax."""
        compile(DEFAULT_LOCUSTFILE, "<string>", "exec")
