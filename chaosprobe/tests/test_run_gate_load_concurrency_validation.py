"""The ``run`` command bounds ``--gate-load-concurrency`` at the CLI boundary.

``--gate-load-concurrency`` controls how many parallel warm-up loops per route
the sustained-during-gate loader spawns inside the probe pod.  ``IntRange(min=1,
max=64)`` rejects 0/negative (a single loop is the floor) and caps the maximum so
a typo cannot spawn enough wget processes to exhaust the probe pod.  Both bounds
are enforced during option parsing, before any cluster work runs.
"""

from click.testing import CliRunner

from chaosprobe.commands.run_cmd import run


def test_gate_load_concurrency_zero_rejected():
    result = CliRunner().invoke(run, ["-n", "demo", "--gate-load-concurrency", "0"])
    assert result.exit_code != 0
    assert "Invalid value" in result.output


def test_gate_load_concurrency_negative_rejected():
    result = CliRunner().invoke(run, ["-n", "demo", "--gate-load-concurrency", "-2"])
    assert result.exit_code != 0
    assert "Invalid value" in result.output


def test_gate_load_concurrency_above_max_rejected():
    result = CliRunner().invoke(run, ["-n", "demo", "--gate-load-concurrency", "65"])
    assert result.exit_code != 0
    assert "Invalid value" in result.output
