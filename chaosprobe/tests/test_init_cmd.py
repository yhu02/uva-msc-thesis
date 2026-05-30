"""Tests for `chaosprobe init` helpers.

The `init` command itself is a long live-cluster orchestration that the project
leaves untested; these tests cover the registry + crane install helper extracted
from it, which is the part with real branching logic.
"""

from unittest.mock import MagicMock, patch

from chaosprobe.commands.init_cmd import _install_probe_registry


class TestInstallProbeRegistry:
    @patch("chaosprobe.commands.init_cmd.ensure_crane")
    @patch("chaosprobe.commands.init_cmd.get_registry_address", return_value="192.168.56.11:30500")
    def test_existing_registry_and_crane_ok(self, mock_addr, mock_crane, capsys):
        setup = MagicMock()
        setup.is_registry_installed.return_value = True

        _install_probe_registry(setup)

        setup.install_registry.assert_not_called()  # already installed
        mock_crane.assert_called_once()
        out = capsys.readouterr().out
        assert "registry: already installed" in out
        assert "192.168.56.11:30500" in out
        assert "containerd" in out  # node-trust guidance
        assert "crane: available" in out

    @patch("chaosprobe.commands.init_cmd.ensure_crane")
    @patch("chaosprobe.commands.init_cmd.get_registry_address", return_value="192.168.56.11:30500")
    def test_installs_registry_when_absent(self, mock_addr, mock_crane, capsys):
        setup = MagicMock()
        setup.is_registry_installed.return_value = False
        setup.install_registry.return_value = True

        _install_probe_registry(setup)

        setup.install_registry.assert_called_once()
        assert "registry: installed" in capsys.readouterr().out

    @patch("chaosprobe.commands.init_cmd.ensure_crane")
    @patch("chaosprobe.commands.init_cmd.get_registry_address", return_value=None)
    def test_registry_not_ready_and_no_address(self, mock_addr, mock_crane, capsys):
        setup = MagicMock()
        setup.is_registry_installed.return_value = False
        setup.install_registry.return_value = False

        _install_probe_registry(setup)

        captured = capsys.readouterr()
        assert "not yet ready" in captured.err  # echoed to stderr
        assert "registry address" not in captured.out  # no address echoed when None

    @patch("chaosprobe.commands.init_cmd.ensure_crane")
    @patch(
        "chaosprobe.commands.init_cmd.get_registry_address",
        side_effect=RuntimeError("api down"),
    )
    def test_registry_failure_is_warned_crane_still_runs(self, mock_addr, mock_crane, capsys):
        setup = MagicMock()
        setup.is_registry_installed.return_value = True

        _install_probe_registry(setup)

        assert "WARNING: registry install failed: api down" in capsys.readouterr().err
        mock_crane.assert_called_once()  # crane still attempted after registry error

    @patch(
        "chaosprobe.commands.init_cmd.ensure_crane",
        side_effect=RuntimeError("no binary for plan9"),
    )
    @patch("chaosprobe.commands.init_cmd.get_registry_address", return_value="192.168.56.11:30500")
    def test_crane_failure_is_warned(self, mock_addr, mock_crane, capsys):
        setup = MagicMock()
        setup.is_registry_installed.return_value = True

        _install_probe_registry(setup)

        err = capsys.readouterr().err
        assert "WARNING: crane install failed: no binary for plan9" in err
        assert "install before `run`" in err
