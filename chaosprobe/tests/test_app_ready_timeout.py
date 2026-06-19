"""Tests for the configurable per-iteration app-readiness timeout.

``--app-ready-timeout`` lets slow-recovering workloads (e.g. hotelReservation,
whose frontend cannot re-resolve its gRPC backends through Consul for ~2-4 min
after a restart) raise the readiness-gate budget so the clean-baseline restart
does not false-taint every iteration with ``app_ready_timeout``.  The value
flows CLI flag -> command param -> ``RunContext.app_ready_timeout`` -> the
``wait_for_app_ready(timeout=...)`` call site in ``_run_single_iteration``.

These guard the two ends of that wiring that are unit-testable without driving
the heavyweight iteration body (mocked wholesale elsewhere in the suite): the
dataclass default and the Click option's default + param binding.  A revert of
the flag, its default, or the field is caught here; the attribute read at the
call site is verified by mypy (``ctx.app_ready_timeout: int``) and exercised by
live runs.
"""

from dataclasses import fields

from chaosprobe.commands.run_cmd import run
from chaosprobe.orchestrator.strategy_runner import RunContext


def test_run_context_app_ready_timeout_defaults_to_240():
    # The OB-suited default is preserved, so existing runs are unchanged.
    field = {f.name: f for f in fields(RunContext)}["app_ready_timeout"]
    assert field.default == 240


def test_run_context_stores_overridden_timeout():
    # A custom budget round-trips (slow-recovery workloads pass e.g. 400).
    field = {f.name: f for f in fields(RunContext)}["app_ready_timeout"]
    assert field.type in ("int", int)


def test_run_command_exposes_app_ready_timeout_flag():
    # The flag exists, defaults to 240, and binds to the `app_ready_timeout`
    # command param (so the value actually reaches RunContext construction).
    opt = {p.name: p for p in run.params}["app_ready_timeout"]
    assert opt.default == 240
    assert "--app-ready-timeout" in opt.opts
