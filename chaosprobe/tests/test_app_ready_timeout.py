"""Tests for the configurable per-iteration app-readiness timeout.

``--app-ready-timeout`` lets slow-recovering workloads (e.g. hotelReservation,
whose frontend cannot re-resolve its gRPC backends through Consul for ~2-4 min
after a restart) raise the readiness-gate budget so the clean-baseline restart
does not false-taint every iteration with ``app_ready_timeout``.  The value
flows CLI flag -> command param -> ``RunContext.app_ready_timeout`` -> the
``wait_for_app_ready(timeout=...)`` call site in ``_run_single_iteration``.

These pin all three links: the field round-trips (default 240 + override), the
Click option exists with the right default and binds to the param, and — the
actual behavior change — ``_run_single_iteration`` forwards the context value to
the gate (so a revert to a hardcoded literal fails, which mypy cannot catch).
"""

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from chaosprobe.commands.run_cmd import run
from chaosprobe.orchestrator import strategy_runner
from chaosprobe.orchestrator.strategy_runner import RunContext


def _make_run_context(**overrides):
    """Construct a real ``RunContext`` with dummy collaborators, so attribute
    storage/propagation is exercised for real (not via field metadata)."""
    base = dict(
        namespace="hotel-reservation",
        timeout=300,
        seed=42,
        settle_time=60,
        iterations=1,
        baseline_duration=0,
        measure_latency=False,
        measure_redis=False,
        measure_disk=False,
        measure_resources=False,
        measure_prometheus=False,
        measure_conntrack=False,
        prometheus_url=(),
        collect_logs=False,
        load_profile=None,
        locustfile=None,
        target_url=None,
        neo4j_uri=None,
        neo4j_user="neo4j",
        neo4j_password="pw",
        shared_scenario={},
        service_routes=None,
        target_deployment="frontend",
        core_api=MagicMock(),
        chaoscenter_config=None,
        frontend_pf_port=None,
        load_service="frontend",
        metrics_collector=MagicMock(),
        mutator=MagicMock(),
        graph_store=None,
        ts="20260619-000000",
    )
    base.update(overrides)
    return RunContext(**base)


def test_run_context_app_ready_timeout_defaults_to_240():
    # The OB-suited default is preserved, so existing runs are unchanged.
    assert _make_run_context().app_ready_timeout == 240


def test_run_context_stores_overridden_timeout():
    # A custom budget round-trips on a real instance (slow-recovery workloads
    # pass e.g. 400) — guards the field actually holding an override.
    assert _make_run_context(app_ready_timeout=400).app_ready_timeout == 400


def test_run_command_exposes_app_ready_timeout_flag():
    # The flag exists, defaults to 240, and binds to the `app_ready_timeout`
    # command param (so the value actually reaches RunContext construction).
    opt = {p.name: p for p in run.params}["app_ready_timeout"]
    assert opt.default == 240
    assert "--app-ready-timeout" in opt.opts


def test_cli_flag_rejects_non_integer():
    # Click enforces the int type — a non-numeric value is rejected, not
    # silently coerced.
    result = CliRunner().invoke(run, ["--app-ready-timeout", "soon"])
    assert result.exit_code != 0
    assert "app-ready-timeout" in result.output.lower()


class _GateReached(Exception):
    """Sentinel raised from the patched gate to stop the iteration right after
    the readiness call, without driving the heavyweight downstream body."""


def test_run_single_iteration_forwards_app_ready_timeout_to_gate(monkeypatch):
    # The behavior change: the gate is called with ctx.app_ready_timeout, not a
    # hardcoded 240.  A revert to `timeout=240` (or any literal) fails here —
    # mypy cannot catch that, so this is the regression guard for the one line
    # that actually changed.  All pre-gate collaborators are no-op'd; the gate
    # captures its kwargs then raises to abort before the prober/chaos body.
    for name in (
        "_clean_stale_resources",
        "_restart_unhealthy_infra",
        "_uncordon_orphaned_nodes",
        "wait_for_healthy_deployments",
        "wait_for_target_pod",
        "_snapshot_cluster_state",
    ):
        monkeypatch.setattr(strategy_runner, name, MagicMock())
    monkeypatch.setattr(strategy_runner, "_build_iteration_routes", lambda *a, **k: ([], []))
    monkeypatch.setattr(strategy_runner, "extract_chaos_duration", lambda *a, **k: 0)

    captured = {}

    def _fake_gate(*args, **kwargs):
        captured.update(kwargs)
        raise _GateReached

    monkeypatch.setattr(strategy_runner, "wait_for_app_ready", _fake_gate)

    ctx = _make_run_context(app_ready_timeout=400)
    with pytest.raises(_GateReached):
        strategy_runner._run_single_iteration(ctx, "spread", {}, 1)

    assert captured["timeout"] == 400
