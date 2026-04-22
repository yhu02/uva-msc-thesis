"""Tests for the Prometheus metrics collection module."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest

from chaosprobe.metrics.prometheus import (
    ContinuousPrometheusProber,
    DEFAULT_QUERIES,
    _find_prometheus_service,
    _query_prometheus,
    discover_prometheus_urls,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_prom_response(results):
    """Build a Prometheus instant query JSON response."""
    return json.dumps({
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": results,
        },
    }).encode()


def _make_prober(**kwargs):
    """Create a ContinuousPrometheusProber without calling __init__ side-effects."""
    prober = ContinuousPrometheusProber.__new__(ContinuousPrometheusProber)
    prober._lock = threading.Lock()
    prober._stop_event = threading.Event()
    prober._thread = None
    prober._time_series = []
    prober._start_time = time.time()
    prober._chaos_start_time = None
    prober._chaos_end_time = None
    prober._expected_chaos_duration = None
    prober._post_chaos_buffer = 15.0
    prober._probe_errors = 0
    prober._consecutive_failures = 0
    prober._thread_name = "prometheus-prober"
    prober.namespace = kwargs.get("namespace", "online-boutique")
    prober.interval = kwargs.get("interval", 10.0)
    prober._prometheus_urls = kwargs.get("urls", ["http://prometheus:9090"])
    prober._available = kwargs.get("available", True)
    prober._port_forward_procs = []
    prober._queries = kwargs.get("queries", {"test_metric": 'up{namespace="online-boutique"}'})
    return prober


# ---------------------------------------------------------------------------
# _query_prometheus
# ---------------------------------------------------------------------------


class TestQueryPrometheus:
    def test_parses_vector_result(self, tmp_path):
        """Valid Prometheus response is parsed into label/value dicts."""
        handler_body = _make_prom_response([
            {"metric": {"pod": "frontend-abc", "__name__": "up"}, "value": [1711700000, "1.5"]},
            {"metric": {"pod": "cart-def"}, "value": [1711700000, "0.3"]},
        ])

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(handler_body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.handle_request, daemon=True)
        t.start()

        result = _query_prometheus(f"http://127.0.0.1:{port}", "up")
        t.join(timeout=5)
        server.server_close()

        assert result is not None
        assert len(result) == 2
        assert result[0]["metric"] == {"pod": "frontend-abc", "__name__": "up"}
        assert result[0]["value"] == [1711700000, "1.5"]
        assert result[1]["metric"] == {"pod": "cart-def"}
        assert result[1]["value"] == [1711700000, "0.3"]

    def test_returns_none_on_connection_error(self):
        result = _query_prometheus("http://127.0.0.1:1", "up", timeout=0.5)
        assert result is None

    def test_returns_none_on_error_status(self, tmp_path):
        body = json.dumps({"status": "error", "error": "bad query"}).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.handle_request, daemon=True)
        t.start()

        result = _query_prometheus(f"http://127.0.0.1:{port}", "up")
        t.join(timeout=5)
        server.server_close()

        assert result is None

    def test_skips_unparseable_values(self, tmp_path):
        body = _make_prom_response([
            {"metric": {"pod": "a"}, "value": [1711700000, "NaN"]},
            {"metric": {"pod": "b"}, "value": [1711700000, "42"]},
        ])

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.handle_request, daemon=True)
        t.start()

        result = _query_prometheus(f"http://127.0.0.1:{port}", "up")
        t.join(timeout=5)
        server.server_close()

        # NaN is a valid float, so both should parse
        assert result is not None
        assert len(result) == 2


# ---------------------------------------------------------------------------
# discover_prometheus_urls / _find_prometheus_service
# ---------------------------------------------------------------------------


class TestFindPrometheusService:
    @patch("kubernetes.config")
    @patch("kubernetes.client")
    def test_returns_empty_when_no_services(self, mock_client, mock_config):
        mock_core = MagicMock()
        mock_client.CoreV1Api.return_value = mock_core
        mock_core.list_namespaced_service.return_value = MagicMock(items=[])

        result = _find_prometheus_service()
        assert result == []

    @patch("kubernetes.config")
    @patch("kubernetes.client")
    def test_finds_single_prometheus_service(self, mock_client, mock_config):
        svc = MagicMock()
        svc.metadata.name = "prometheus-server"
        port = MagicMock()
        port.port = 9090
        svc.spec.ports = [port]

        mock_core = MagicMock()
        mock_client.CoreV1Api.return_value = mock_core
        # Only the "prometheus" namespace has the service
        mock_core.list_namespaced_service.side_effect = [
            MagicMock(items=[svc]),  # prometheus
        ]

        result = _find_prometheus_service()
        assert result == [("prometheus-server", "prometheus", 9090)]

    @patch("kubernetes.config")
    @patch("kubernetes.client")
    def test_finds_prometheus_service_in_namespace(self, mock_client, mock_config):
        svc = MagicMock()
        svc.metadata.name = "prometheus-server"
        p1 = MagicMock()
        p1.port = 9090
        svc.spec.ports = [p1]

        mock_core = MagicMock()
        mock_client.CoreV1Api.return_value = mock_core
        mock_core.list_namespaced_service.side_effect = [
            MagicMock(items=[svc]),  # prometheus
        ]

        result = _find_prometheus_service()
        assert len(result) == 1
        assert ("prometheus-server", "prometheus", 9090) in result


class TestDiscoverPrometheusUrls:
    @patch("chaosprobe.metrics.prometheus._find_prometheus_service", return_value=[])
    def test_returns_empty_when_no_services(self, _mock):
        result = discover_prometheus_urls()
        assert result == []

    @patch("chaosprobe.metrics.prometheus._check_prometheus_url", return_value=True)
    @patch("chaosprobe.metrics.prometheus._find_prometheus_service",
           return_value=[
               ("prometheus-server", "prometheus", 9090),
           ])
    def test_returns_all_reachable_urls(self, _mock_find, _mock_check):
        result = discover_prometheus_urls()
        assert len(result) == 1
        assert "http://prometheus-server.prometheus:9090" in result

    @patch("chaosprobe.metrics.prometheus._check_prometheus_url",
           side_effect=[False])
    @patch("chaosprobe.metrics.prometheus._find_prometheus_service",
           return_value=[
               ("prometheus-server", "prometheus", 9090),
           ])
    def test_filters_unreachable(self, _mock_find, _mock_check):
        result = discover_prometheus_urls()
        assert result == []


# ---------------------------------------------------------------------------
# ContinuousPrometheusProber — result() and phase splitting
# ---------------------------------------------------------------------------


class TestPrometheusProberResult:
    def test_result_when_no_data(self):
        prober = _make_prober(available=False)
        prober._time_series = []

        result = prober.result()
        assert result["available"] is False
        assert "reason" in result

    def test_result_with_data(self):
        prober = _make_prober()
        prober._time_series = [
            {
                "timestamp": "2026-03-29T12:00:00+00:00",
                "elapsed_s": 0.0,
                "phase": "pre-chaos",
                "metrics": {
                    "test_metric": [{"metric": {"pod": "a"}, "value": [1711700000, "1.0"]}],
                },
            },
            {
                "timestamp": "2026-03-29T12:00:10+00:00",
                "elapsed_s": 10.0,
                "phase": "during-chaos",
                "metrics": {
                    "test_metric": [{"metric": {"pod": "a"}, "value": [1711700010, "5.0"]}],
                },
            },
        ]

        result = prober.result()
        assert result["available"] is True
        assert result["serverUrls"] == ["http://prometheus:9090"]
        assert "timeSeries" in result
        assert "phases" in result
        assert "queries" in result
        assert "probeErrors" not in result

    def test_result_includes_probe_errors(self):
        prober = _make_prober()
        prober._probe_errors = 2
        prober._time_series = [
            {
                "timestamp": "2026-03-29T12:00:00+00:00",
                "elapsed_s": 0.0,
                "phase": "pre-chaos",
                "metrics": {"test_metric": [{"metric": {}, "value": [1711700000, "1.0"]}]},
            },
        ]

        result = prober.result()
        assert result["probeErrors"] == 2


class TestPrometheusProberPhaseSplitting:
    def test_phase_aggregation(self):
        prober = _make_prober()

        series = [
            {
                "phase": "pre-chaos",
                "metrics": {
                    "error_rate": [
                        {"metric": {"svc": "frontend"}, "value": [1711700000, "0.01"]},
                        {"metric": {"svc": "cart"}, "value": [1711700000, "0.02"]},
                    ],
                },
            },
            {
                "phase": "during-chaos",
                "metrics": {
                    "error_rate": [
                        {"metric": {"svc": "frontend"}, "value": [1711700010, "0.40"]},
                        {"metric": {"svc": "cart"}, "value": [1711700010, "0.10"]},
                    ],
                },
            },
            {
                "phase": "during-chaos",
                "metrics": {
                    "error_rate": [
                        {"metric": {"svc": "frontend"}, "value": [1711700020, "0.60"]},
                        {"metric": {"svc": "cart"}, "value": [1711700020, "0.20"]},
                    ],
                },
            },
            {
                "phase": "post-chaos",
                "metrics": {
                    "error_rate": [
                        {"metric": {"svc": "frontend"}, "value": [1711700030, "0.02"]},
                    ],
                },
            },
        ]

        phases = prober._split_phases(series)

        assert phases["pre-chaos"]["sampleCount"] == 1
        assert phases["during-chaos"]["sampleCount"] == 2
        assert phases["post-chaos"]["sampleCount"] == 1

        # During-chaos: sample sums are 0.50 and 0.80, mean = 0.65, max = 0.80
        during = phases["during-chaos"]["metrics"]["error_rate"]
        assert during["mean"] == pytest.approx(0.65, abs=0.001)
        assert during["max"] == pytest.approx(0.80, abs=0.001)
        assert during["min"] == pytest.approx(0.50, abs=0.001)

    def test_empty_phases(self):
        prober = _make_prober()
        phases = prober._split_phases([])

        assert phases["pre-chaos"]["sampleCount"] == 0
        assert phases["during-chaos"]["sampleCount"] == 0
        assert phases["post-chaos"]["sampleCount"] == 0

    def test_no_metrics_in_entry(self):
        prober = _make_prober()
        series = [{"phase": "during-chaos"}]
        phases = prober._split_phases(series)
        assert phases["during-chaos"]["sampleCount"] == 1
        assert phases["during-chaos"]["metrics"] == {}


class TestPrometheusProberPhaseTransitions:
    def test_current_phase_transitions(self):
        prober = _make_prober()
        now = time.time()

        assert prober._current_phase(now) == "pre-chaos"

        prober._chaos_start_time = now - 10
        assert prober._current_phase(now) == "during-chaos"

        prober._chaos_end_time = now - 5
        assert prober._current_phase(now) == "post-chaos"


class TestPrometheusProberLifecycle:
    def test_start_disables_when_no_prometheus(self):
        prober = _make_prober(urls=[])
        prober._prometheus_urls = []

        with patch("chaosprobe.metrics.prometheus.discover_prometheus_urls", return_value=[]), \
             patch("chaosprobe.metrics.prometheus._find_prometheus_service", return_value=[]):
            prober.start()

        assert prober._available is False

    def test_start_disables_when_unreachable(self):
        prober = _make_prober(urls=["http://127.0.0.1:1"])

        with patch("chaosprobe.metrics.prometheus._check_prometheus_url", return_value=False):
            prober.start()

        assert prober._available is False

    def test_start_uses_port_forward_fallback(self):
        prober = _make_prober(urls=[])
        prober._prometheus_urls = []

        with patch("chaosprobe.metrics.prometheus.discover_prometheus_urls", return_value=[]), \
             patch("chaosprobe.metrics.prometheus._find_prometheus_service",
                   return_value=[("prometheus-server", "prometheus", 80)]), \
             patch.object(prober, "_start_port_forward",
                         return_value="http://localhost:19090") as mock_pf, \
             patch("chaosprobe.metrics.prometheus._check_prometheus_url", return_value=True):
            prober.start()

        mock_pf.assert_called_once_with("prometheus-server", "prometheus", 80)
        assert prober._prometheus_urls == ["http://localhost:19090"]
        assert prober._available is True

    def test_start_with_multiple_port_forwards(self):
        prober = _make_prober(urls=[])
        prober._prometheus_urls = []

        with patch("chaosprobe.metrics.prometheus.discover_prometheus_urls", return_value=[]), \
             patch("chaosprobe.metrics.prometheus._find_prometheus_service",
                   return_value=[
                       ("prometheus-server", "prometheus", 80),
                   ]), \
             patch.object(prober, "_start_port_forward",
                         return_value="http://localhost:19090") as mock_pf, \
             patch("chaosprobe.metrics.prometheus._check_prometheus_url", return_value=True):
            prober.start()

        assert mock_pf.call_count == 1
        assert len(prober._prometheus_urls) == 1
        assert prober._available is True

    def test_stop_cleans_up_port_forwards(self):
        prober = _make_prober()
        mock_proc1 = MagicMock()
        mock_proc2 = MagicMock()
        prober._port_forward_procs = [mock_proc1, mock_proc2]

        prober.stop()

        mock_proc1.terminate.assert_called_once()
        mock_proc2.terminate.assert_called_once()
        assert prober._port_forward_procs == []


# ---------------------------------------------------------------------------
# DEFAULT_QUERIES
# ---------------------------------------------------------------------------


class TestDefaultQueries:
    def test_namespace_templating(self):
        """Verify all default queries can be formatted with namespace."""
        for template in DEFAULT_QUERIES.values():
            formatted = template.format(namespace="test-ns")
            assert "test-ns" in formatted
            assert "{namespace}" not in formatted

    def test_all_queries_present(self):
        expected = {
            "pod_ready_count",
            "cpu_usage",
            "cpu_throttling",
            "memory_usage",
            "network_receive_bytes",
        }
        assert set(DEFAULT_QUERIES.keys()) == expected

    def test_litmus_pod_filter_in_all_queries(self):
        """All default queries exclude LitmusChaos experiment pods."""
        for label, template in DEFAULT_QUERIES.items():
            formatted = template.format(namespace="test-ns")
            assert 'pod!~"' in formatted, (
                f"Query {label!r} is missing the LitmusChaos pod exclusion filter"
            )
