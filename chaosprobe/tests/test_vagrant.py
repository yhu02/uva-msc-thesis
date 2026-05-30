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
        # vagrant must never fall back to its built-in default provider.
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


class TestVagrantfileMemoryDefaults:
    def _render(self, tmp_path, **kwargs):
        setup = _setup()
        setup.create_vagrantfile(output_dir=tmp_path, **kwargs)
        return (tmp_path / "Vagrantfile").read_text()

    def test_defaults_render_cp_12g_worker_4g(self, tmp_path):
        # Locks the per-role memory defaults: control planes 12 GB, workers 4 GB.
        content = self._render(tmp_path)
        assert "CP_MEMORY = 12288" in content
        assert "WORKER_MEMORY = 4096" in content

    def test_explicit_per_role_memory_overrides_defaults(self, tmp_path):
        content = self._render(tmp_path, cp_memory=8192, worker_memory=2048)
        assert "CP_MEMORY = 8192" in content
        assert "WORKER_MEMORY = 2048" in content

    def test_vm_memory_overrides_both_roles(self, tmp_path):
        # The legacy single --memory flag still wins over the per-role defaults.
        content = self._render(tmp_path, vm_memory=3072)
        assert "CP_MEMORY = 3072" in content
        assert "WORKER_MEMORY = 3072" in content
