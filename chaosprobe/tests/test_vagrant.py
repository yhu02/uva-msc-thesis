"""Tests for the Vagrant provider handling — libvirt is the only provider."""

import subprocess
from unittest.mock import patch

import pytest

from chaosprobe.provisioner.setup import LitmusSetup


def _setup():
    # skip_k8s_init=True so construction needs no cluster.
    return LitmusSetup(skip_k8s_init=True)


class TestLibvirtOnly:
    def test_get_vagrant_env_forces_libvirt_even_when_not_ready(self):
        setup = _setup()
        # Even when libvirt isn't reported ready, the provider is still forced —
        # vagrant must never fall back to its built-in VirtualBox default.
        with patch.object(setup, "_check_libvirt", return_value={"all_ready": False}):
            env = setup._get_vagrant_env()
        assert env["VAGRANT_DEFAULT_PROVIDER"] == "libvirt"

    def test_get_vagrant_env_does_not_consult_libvirt_check(self):
        # Regression lock: the old code only set the provider when _check_libvirt
        # reported ready. It must now be unconditional.
        setup = _setup()
        with patch.object(setup, "_check_libvirt") as mock_check:
            env = setup._get_vagrant_env()
        assert env["VAGRANT_DEFAULT_PROVIDER"] == "libvirt"
        mock_check.assert_not_called()

    @patch("chaosprobe.provisioner.vagrant.subprocess.run")
    def test_vagrant_up_uses_libvirt_provider_and_env(self, mock_run, tmp_path):
        setup = _setup()
        (tmp_path / "Vagrantfile").write_text("# vagrantfile")
        with patch.object(setup, "_recover_shutoff_libvirt_vms"):
            assert setup.vagrant_up(tmp_path) is True
        args, kwargs = mock_run.call_args
        assert args[0] == ["vagrant", "up", "--provider=libvirt"]
        assert kwargs["env"]["VAGRANT_DEFAULT_PROVIDER"] == "libvirt"

    def test_vagrant_up_raises_without_vagrantfile(self, tmp_path):
        setup = _setup()
        with pytest.raises(RuntimeError, match="No Vagrantfile"):
            setup.vagrant_up(tmp_path)

    @patch(
        "chaosprobe.provisioner.vagrant.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, ["vagrant", "up"]),
    )
    def test_vagrant_up_raises_on_failure(self, mock_run, tmp_path):
        setup = _setup()
        (tmp_path / "Vagrantfile").write_text("# vagrantfile")
        with patch.object(setup, "_recover_shutoff_libvirt_vms"):
            with pytest.raises(RuntimeError, match="Failed to start"):
                setup.vagrant_up(tmp_path)
