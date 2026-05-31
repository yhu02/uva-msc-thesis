"""Tests for the load generation module."""

import os
from unittest.mock import MagicMock

import pytest

from chaosprobe.loadgen.runner import (
    DEFAULT_LOCUSTFILE,
    LoadProfile,
    LoadStats,
    LocustRunner,
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

    def test_stop_drain_stderr_failure_no_crash(self):
        runner = LocustRunner(target_url="http://localhost:8080")
        proc = MagicMock()
        proc.poll.return_value = 0  # already exited — skip terminate path
        proc.stderr.read.side_effect = OSError("broken pipe")
        runner._process = proc
        runner.stop()  # must swallow the drain failure, not raise
        proc.stderr.read.assert_called_once()
        proc.stderr.close.assert_not_called()  # read raised before close

    def test_start_invokes_locust_as_module_not_wrapper(self, monkeypatch):
        """Locust must launch via ``python -m locust``, never the console-script
        wrapper (``<venv>/bin/locust``).

        The wrapper's shebang embeds the venv's absolute path at creation time,
        so it dies with FileNotFoundError/ENOENT once the project or venv is
        relocated — which previously aborted every experiment iteration at load
        generation. ``sys.executable -m locust`` uses the live interpreter and
        is relocation-proof (regression).
        """
        import sys

        from chaosprobe.loadgen import runner as runner_mod

        runner = LocustRunner(target_url="http://localhost:8080")
        captured = {}

        def fake_popen(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            proc = MagicMock()
            proc.poll.return_value = None  # still running -> start() sees success
            return proc

        monkeypatch.setattr(runner_mod.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(runner_mod.time, "sleep", lambda *_a, **_k: None)

        runner.start(LoadProfile.from_name("steady"))

        cmd = captured["cmd"]
        assert cmd[:3] == [sys.executable, "-m", "locust"]
        assert "--headless" in cmd
        # argv[0] is the interpreter, not a bare ``.../bin/locust`` wrapper path.
        assert not cmd[0].endswith("/locust")

    def test_get_locustfile_default(self):
        runner = LocustRunner(target_url="http://localhost:8080")
        path = runner._get_locustfile()
        assert os.path.exists(path)
        with open(path) as f:
            content = f.read()
        assert "FrontendUser" in content

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
            "Type,Name,Request Count,Failure Count,Average Response Time,"
            "Min Response Time,Max Response Time,Average Content Size,"
            "Requests/s,Failures/s,50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%\n"
            "GET,/,500,2,45.2,10.0,200.0,1024,16.7,0.07,40,50,60,70,90,120,150,180,195,199,200\n"
            "GET,/product/OLJCESPC7Z,300,1,55.0,15.0,250.0,2048,10.0,0.03,50,60,70,80,100,130,160,190,240,248,250\n"
            "Aggregated,,800,3,48.9,10.0,250.0,1408,26.7,0.1,44,54,64,74,94,124,154,184,215,245,250\n"
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

    def test_default_locustfile_posts_form_encoded_not_json(self):
        """POST tasks must send form data, not JSON.

        The online-boutique frontend reads HTML form fields
        (x-www-form-urlencoded); a JSON body leaves the form empty and the
        handler returns 400, which previously caused 100% Locust failures on
        /cart and /cart/checkout. Lock the tasks to ``data=`` (regression).
        """
        assert "json=" not in DEFAULT_LOCUSTFILE
        # Both POST tasks (add_to_cart, checkout) send form data.
        assert DEFAULT_LOCUSTFILE.count("data={") == 2

    def test_checkout_credit_card_is_digits_only(self):
        """The checkout card number must be digits only.

        A dashed number (``4432-8015-6152-0454``) fails the payment service's
        card validation, returning HTTP 422 on every /cart/checkout (regression).
        """
        import re

        m = re.search(r'"credit_card_number":\s*"([^"]+)"', DEFAULT_LOCUSTFILE)
        assert m is not None, "credit_card_number not found in locustfile"
        assert m.group(1).isdigit(), f"card number must be digits only, got {m.group(1)!r}"


class TestParseStatsCsv:
    """Locust writes 'N/A' for percentiles on zero-request rows; parsing must
    tolerate non-numeric / missing cells instead of raising ValueError."""

    @staticmethod
    def _runner():
        return LocustRunner.__new__(LocustRunner)

    def test_na_percentiles_in_aggregated_row_default_to_zero(self, tmp_path):
        csv_path = tmp_path / "stats.csv"
        csv_path.write_text(
            "Type,Name,Request Count,Failure Count,Average Response Time,"
            "Min Response Time,Max Response Time,Requests/s,Failures/s,50%,95%,99%\n"
            # N/A in the count columns too, exercising the int fallback
            ",Aggregated,N/A,N/A,0,0,0,0,0,N/A,N/A,N/A\n"
        )
        stats = self._runner()._parse_stats_csv(str(csv_path), LoadStats())
        assert stats.total_requests == 0
        assert stats.total_failures == 0
        assert stats.p50_response_time_ms == 0.0
        assert stats.p95_response_time_ms == 0.0
        assert stats.p99_response_time_ms == 0.0

    def test_endpoint_row_with_na_does_not_crash(self, tmp_path):
        csv_path = tmp_path / "stats.csv"
        csv_path.write_text(
            "Type,Name,Request Count,Failure Count,Average Response Time,95%\n"
            "GET,/cart,5,0,12.3,N/A\n"
        )
        stats = self._runner()._parse_stats_csv(str(csv_path), LoadStats())
        assert len(stats.endpoints) == 1
        assert stats.endpoints[0]["requests"] == 5
        assert stats.endpoints[0]["p95ResponseTime_ms"] == 0.0
