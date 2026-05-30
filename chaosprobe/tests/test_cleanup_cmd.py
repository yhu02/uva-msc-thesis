"""Tests for the ``chaosprobe cleanup`` command (commands/cleanup_cmd.py)."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaosprobe.commands.cleanup_cmd import cleanup


def test_cleanup_default():
    prov = MagicMock()
    with patch("chaosprobe.commands.cleanup_cmd.KubernetesProvisioner", return_value=prov):
        result = CliRunner().invoke(cleanup, ["demo-ns"])
    assert result.exit_code == 0
    assert "Resources cleaned up successfully" in result.output
    prov.cleanup.assert_called_once_with()
    prov.cleanup_namespace.assert_not_called()


def test_cleanup_all():
    prov = MagicMock()
    with patch("chaosprobe.commands.cleanup_cmd.KubernetesProvisioner", return_value=prov):
        result = CliRunner().invoke(cleanup, ["demo-ns", "--all"])
    assert result.exit_code == 0
    assert "All resources cleaned up successfully" in result.output
    prov.cleanup_namespace.assert_called_once_with()
    prov.cleanup.assert_not_called()
