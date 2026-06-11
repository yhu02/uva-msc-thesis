"""Tests for the ``chaosprobe run`` conntrack wiring (banner + end-of-run cleanup)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from chaosprobe.commands.run_cmd import _cleanup_conntrack_samplers, _print_run_banner


def _banner(capsys, **overrides):
    kwargs = dict(
        measure_latency=False,
        measure_redis=False,
        measure_disk=False,
        measure_resources=False,
        measure_prometheus=False,
        measure_conntrack=False,
        prometheus_url=(),
        collect_logs=False,
        baseline_duration=0,
    )
    kwargs.update(overrides)
    _print_run_banner(
        "ns",
        Path("exp.yaml"),
        ["spread"],
        1,
        Path("results/x"),
        300,
        60,
        **kwargs,
    )
    return capsys.readouterr().out


def test_banner_announces_conntrack_sampling(capsys):
    out = _banner(capsys, measure_conntrack=True)
    assert "Conntrack:  Sampling per-node protocol-labeled conntrack counts" in out


def test_banner_silent_when_conntrack_disabled(capsys):
    assert "Conntrack" not in _banner(capsys, measure_conntrack=False)


def test_cleanup_reports_removed_sampler_pods(capsys):
    core_api = MagicMock()
    with patch("chaosprobe.metrics.conntrack.cleanup_sampler_pods", return_value=3) as cleanup_fn:
        _cleanup_conntrack_samplers(core_api)
    cleanup_fn.assert_called_once_with(core_api)
    assert "Removed 3 conntrack sampler pod(s)." in capsys.readouterr().out


def test_cleanup_silent_when_nothing_removed(capsys):
    with patch("chaosprobe.metrics.conntrack.cleanup_sampler_pods", return_value=0):
        _cleanup_conntrack_samplers(MagicMock())
    assert "conntrack" not in capsys.readouterr().out
