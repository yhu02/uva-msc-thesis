"""Tests for the app-readiness gate.

``wait_for_app_ready`` gates on the user-facing north-south HTTP routes by
default; east-west service routes (a TCP connect to the real ``host:port`` —
the correct probe for gRPC/TCP backends that serve no HTTP) gate ONLY when
``gate_east_west=True`` (the production default is False). These tests drive the
gate with a mocked pod-exec and clock so the loop runs a fixed number of ticks
without sleeping; ``_run_gate`` passes ``gate_east_west=True`` by default because
most cases here exercise the opt-in east-west path.
"""

import itertools
from unittest.mock import MagicMock, patch

from chaosprobe.orchestrator import readiness

HTTP_ROUTE = [("frontend", "/", "homepage", "GET")]
GRPC_ROUTE = [("checkout", "currency", "currency:7000", "grpc", "checkout->currency")]


def _run_gate(exec_fn, http_routes, service_routes, timeout=4, gate_east_west=True):
    """Drive ``wait_for_app_ready`` with a mocked pod exec + clock.

    With ``itertools.count`` as the clock and ``timeout=4`` the loop runs
    exactly 3 ticks and never reaches the consecutive-OK threshold, so the
    warmup/sustained phase is not entered.  Returns the exec mock so tests
    can inspect which probes were issued. ``gate_east_west`` defaults True here
    because most of these tests exercise the (opt-in) east-west TCP gate; the
    production default is False (north-south-only gate).
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
            gate_east_west=gate_east_west,
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

    def test_east_west_not_gated_by_default(self):
        # Production default (gate_east_west=False): even WITH service_routes, the
        # gate runs north-south HTTP only — no TCP/python3 east-west probe — so a
        # failing east-west edge cannot false-positive-taint the iteration.
        exec_mock = _run_gate(_exec_http_ok_tcp_fail, HTTP_ROUTE, GRPC_ROUTE, gate_east_west=False)
        assert _python3_calls(exec_mock) == []


class TestAppReadyReturnValue:
    """``wait_for_app_ready`` returns ``True`` when ready (or skipped) and
    ``False`` on timeout — the caller turns a timeout into an
    ``app_ready_timeout`` taint reason."""

    def _drive(self, exec_fn, *, timeout, required_consecutive, sustained_period_s):
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
            return readiness.wait_for_app_ready(
                "ns",
                "frontend",
                timeout=timeout,
                http_routes=HTTP_ROUTE,
                service_routes=None,
                required_consecutive=required_consecutive,
                sustained_period_s=sustained_period_s,
            )

    def test_timeout_returns_false(self):
        # Only ~3 ticks before the deadline, but 5 consecutive OKs required —
        # the gate never passes, so it times out.
        assert (
            self._drive(
                _exec_http_ok_tcp_ok, timeout=4, required_consecutive=5, sustained_period_s=15
            )
            is False
        )

    def test_ready_returns_true(self):
        # One OK probe + a zero-length sustained window → the gate passes.
        assert (
            self._drive(
                _exec_http_ok_tcp_ok, timeout=60, required_consecutive=1, sustained_period_s=0
            )
            is True
        )

    def test_north_south_http_failure_blocks_the_gate(self):
        # The positive half of "north-south-only gate": with NO east-west routes,
        # a north-south HTTP route that returns FAIL must prevent the gate from
        # passing (returns False) — north-south is the sole functional signal, so a
        # regression that stopped gating on it (e.g. `if False and "OK" not in out`)
        # would be caught here. Generous timeout/consecutive so only the HTTP
        # failure — not the tick budget — can cause the False.
        def _http_fail(core, ns, pod, cmd):
            return "FAIL"  # the wget HTTP probe never succeeds

        assert (
            self._drive(_http_fail, timeout=60, required_consecutive=1, sustained_period_s=0)
            is False
        )

    def test_no_probe_pod_returns_true(self):
        # No probe pod to assess readiness — a skip, not a timeout, so it must
        # not taint the iteration.
        fake_time = MagicMock()
        fake_time.time.side_effect = itertools.count(0, 1)
        fake_time.sleep.return_value = None
        with (
            patch("chaosprobe.metrics.base.find_probe_pod", return_value=None),
            patch.object(readiness, "time", fake_time),
            patch.object(readiness.k8s_client, "CoreV1Api", return_value=MagicMock()),
        ):
            assert readiness.wait_for_app_ready("ns", "frontend", http_routes=HTTP_ROUTE) is True


def _exec_http_fail(core, ns, pod, cmd):
    """Every probe fails, so the consecutive-OK gate never passes."""
    return "FAIL"


def _run_gate_warmup(pre_gate_warmup_s, exec_fn=_exec_http_fail, timeout=4):
    """Drive wait_for_app_ready capturing the (patched) warmup_application mock.

    With an always-failing exec the gate never reaches the consecutive-OK
    threshold, so the POST-gate warmup (duration_s=20) is never invoked --
    isolating the pre-gate warm-up call.
    """
    exec_mock = MagicMock(side_effect=exec_fn)
    warm_mock = MagicMock()
    fake_time = MagicMock()
    fake_time.time.side_effect = itertools.count(0, 1)
    fake_time.sleep.return_value = None
    with (
        patch("chaosprobe.metrics.base.find_probe_pod", return_value="probe-pod"),
        patch("chaosprobe.metrics.base.exec_in_pod", exec_mock),
        patch.object(readiness, "time", fake_time),
        patch.object(readiness, "warmup_application", warm_mock),
        patch.object(readiness.k8s_client, "CoreV1Api", return_value=MagicMock()),
    ):
        readiness.wait_for_app_ready(
            "ns",
            "frontend",
            timeout=timeout,
            http_routes=HTTP_ROUTE,
            service_routes=None,
            required_consecutive=5,
            pre_gate_warmup_s=pre_gate_warmup_s,
        )
    return warm_mock


class TestPreGateWarmup:
    def test_pre_gate_warmup_runs_before_a_gate_that_never_passes(self):
        """pre_gate_warmup_s>0 pumps sustained load even if the gate times out."""
        warm_mock = _run_gate_warmup(pre_gate_warmup_s=45)
        assert warm_mock.call_count == 1, "pre-gate warm-up should run exactly once"
        # The single call is the PRE-gate one (the post-gate warmup uses 20s and
        # is unreachable here because the gate never passes).
        assert warm_mock.call_args.kwargs["duration_s"] == 45

    def test_no_pre_gate_warmup_by_default(self):
        """pre_gate_warmup_s=0 (default) does not pump pre-gate load."""
        warm_mock = _run_gate_warmup(pre_gate_warmup_s=0)
        assert warm_mock.call_count == 0


class TestSustainedGateLoad:
    """The --gate-sustained-load loader pumps warm-up DURING the gate and is
    always stopped on exit (no leaked thread)."""

    def _run(self, sustained, exec_fn=_exec_http_fail):
        import threading
        import time as real_time

        warm_mock = MagicMock()
        pumped = threading.Event()

        def warm_side_effect(*a, **k):
            pumped.set()
            real_time.sleep(0.01)

        warm_mock.side_effect = warm_side_effect

        def gated_exec(core, ns, pod, cmd):
            # The gate's first route check can't return until the loader has
            # pumped at least once (when enabled), making call counting
            # deterministic regardless of thread scheduling.
            if sustained:
                pumped.wait(timeout=5)
            return exec_fn(core, ns, pod, cmd)

        exec_mock = MagicMock(side_effect=gated_exec)
        fake_time = MagicMock()
        fake_time.time.side_effect = itertools.count(0, 1)
        fake_time.sleep.return_value = None
        with (
            patch("chaosprobe.metrics.base.find_probe_pod", return_value="probe-pod"),
            patch("chaosprobe.metrics.base.exec_in_pod", exec_mock),
            patch.object(readiness, "time", fake_time),
            patch.object(readiness, "warmup_application", warm_mock),
            patch.object(readiness.k8s_client, "CoreV1Api", return_value=MagicMock()),
        ):
            readiness.wait_for_app_ready(
                "ns",
                "frontend",
                timeout=4,
                http_routes=HTTP_ROUTE,
                service_routes=None,
                required_consecutive=5,
                sustained_gate_load=sustained,
            )
        alive = [t.name for t in threading.enumerate() if t.name == "gate-sustained-load"]
        return warm_mock, alive

    def test_sustained_load_pumps_during_gate_and_is_stopped(self):
        warm_mock, alive = self._run(sustained=True)
        assert warm_mock.call_count >= 1, "loader should pump warm-up during the gate"
        assert alive == [], "the gate-sustained-load thread must be stopped on exit"

    def test_off_by_default_no_loader_thread(self):
        warm_mock, alive = self._run(sustained=False)
        # Gate always fails, so the post-gate warmup is unreachable and no loader
        # runs → warmup_application is never called.
        assert warm_mock.call_count == 0
        assert alive == []
