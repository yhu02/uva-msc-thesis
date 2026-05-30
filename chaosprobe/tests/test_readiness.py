"""Tests for the app-readiness gate's east-west gRPC/TCP probing.

``wait_for_app_ready`` gates north-south routes over HTTP and east-west
service routes over a TCP connect to the real ``host:port`` (the correct
probe for gRPC/TCP backends that serve no HTTP).  These tests drive the
gate with a mocked pod-exec and clock so the loop runs a fixed number of
ticks without sleeping.
"""

import itertools
from unittest.mock import MagicMock, patch

from chaosprobe.orchestrator import readiness

HTTP_ROUTE = [("frontend", "/", "homepage", "GET")]
GRPC_ROUTE = [("checkout", "currency", "currency:7000", "grpc", "checkout->currency")]


def _run_gate(exec_fn, http_routes, service_routes, timeout=4):
    """Drive ``wait_for_app_ready`` with a mocked pod exec + clock.

    With ``itertools.count`` as the clock and ``timeout=4`` the loop runs
    exactly 3 ticks and never reaches the consecutive-OK threshold, so the
    warmup/sustained phase is not entered.  Returns the exec mock so tests
    can inspect which probes were issued.
    """
    exec_mock = MagicMock(side_effect=exec_fn)
    fake_time = MagicMock()
    fake_time.time.side_effect = itertools.count(0, 1)
    fake_time.sleep.return_value = None
    with (
        patch("chaosprobe.metrics.base.find_probe_pod", return_value="probe-pod"),
        patch("chaosprobe.metrics.base.exec_in_pod", exec_mock),
        patch.object(readiness, "time", fake_time),
        patch.object(readiness, "warmup_application"),
        patch.object(readiness.k8s_client, "CoreV1Api", return_value=MagicMock()),
    ):
        readiness.wait_for_app_ready(
            "ns",
            "frontend",
            timeout=timeout,
            http_routes=http_routes,
            service_routes=service_routes,
            required_consecutive=5,
        )
    return exec_mock


def _python3_calls(exec_mock):
    """The pod-exec calls that ran a python3 TCP probe (cmd[0] == 'python3')."""
    return [c for c in exec_mock.call_args_list if c.args[3][0] == "python3"]


def _exec_http_ok_tcp_fail(core, ns, pod, cmd):
    return "FAIL refused" if cmd[0] == "python3" else "OK"


def _exec_http_ok_tcp_ok(core, ns, pod, cmd):
    return "OK"


def _exec_http_ok_python3_missing(core, ns, pod, cmd):
    if cmd[0] == "python3":
        return "OCI runtime exec failed: executable file not found"
    return "OK"


class TestEastWestGate:
    def test_grpc_port_probed_via_tcp(self):
        """The gRPC backend is TCP-probed at its real host:port, not HTTP."""
        exec_mock = _run_gate(_exec_http_ok_tcp_fail, HTTP_ROUTE, GRPC_ROUTE)

        py = _python3_calls(exec_mock)
        assert py, "expected a python3 TCP probe for the gRPC route"
        # cmd = [python3, -c, script, hostname, budget, port]
        assert py[0].args[3][3] == "currency"
        assert py[0].args[3][5] == "7000"

    def test_python3_missing_skips_tcp_after_first_probe(self):
        """Once python3 is found missing, later ticks skip the TCP gate and
        fall back to HTTP-only (K8s-native gRPC readiness covers backends)."""
        exec_mock = _run_gate(_exec_http_ok_python3_missing, HTTP_ROUTE, GRPC_ROUTE)
        assert len(_python3_calls(exec_mock)) == 1

    def test_empty_output_treated_as_python3_missing(self):
        def _exec(core, ns, pod, cmd):
            return "" if cmd[0] == "python3" else "OK"

        exec_mock = _run_gate(_exec, HTTP_ROUTE, GRPC_ROUTE)
        assert len(_python3_calls(exec_mock)) == 1

    def test_grpc_reachable_probes_every_tick(self):
        """With python3 available and the port reachable, the TCP gate runs
        on every tick (3 ticks at timeout=4)."""
        exec_mock = _run_gate(_exec_http_ok_tcp_ok, HTTP_ROUTE, GRPC_ROUTE)
        assert len(_python3_calls(exec_mock)) == 3

    def test_host_without_port_defaults_to_80(self):
        routes = [("a", "b", "barehost", "grpc", "a->b")]
        exec_mock = _run_gate(_exec_http_ok_tcp_ok, HTTP_ROUTE, routes)

        py = _python3_calls(exec_mock)
        assert py[0].args[3][3] == "barehost"
        assert py[0].args[3][5] == "80"

    def test_no_service_routes_runs_http_only(self):
        exec_mock = _run_gate(_exec_http_ok_tcp_ok, HTTP_ROUTE, None)
        assert _python3_calls(exec_mock) == []
