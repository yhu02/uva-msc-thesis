"""The ``run`` command rejects non-positive ``--iterations`` at the CLI boundary.

``-i 0`` (or negative) previously slipped through to the aggregate, where
``pass_count / len(verdicts)`` and ``statistics.mean([])`` would raise. Click's
``IntRange(min=1)`` now rejects it during option parsing, before any cluster
work runs.
"""

from click.testing import CliRunner

from chaosprobe.commands.run_cmd import run


def test_iterations_zero_rejected():
    result = CliRunner().invoke(run, ["-n", "demo", "-i", "0"])
    assert result.exit_code != 0
    assert "Invalid value" in result.output


def test_iterations_negative_rejected():
    result = CliRunner().invoke(run, ["-n", "demo", "-i", "-3"])
    assert result.exit_code != 0
    assert "Invalid value" in result.output
