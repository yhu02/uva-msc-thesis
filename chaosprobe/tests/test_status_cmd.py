"""Tests for the ``chaosprobe status`` command (commands/status_cmd.py)."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaosprobe.commands.status_cmd import status


def _prereqs(**overrides):
    """Return a complete prerequisites dict with optional overrides."""
    base = {
        "kubectl": True,
        "helm": True,
        "git": True,
        "ssh": True,
        "ansible": True,
        "vagrant": False,
        "libvirt": False,
        "libvirt_status": {"kvm_available": True, "libvirtd_installed": True},
        "cluster_access": True,
        "litmus_installed": True,
        "litmus_ready": True,
        "chaoscenter_installed": False,
        "chaoscenter_ready": False,
        "all_ready": True,
    }
    base.update(overrides)
    return base


def _setup_mock(prereqs, *, cluster_info=None, dashboard_url=None):
    """Build a MagicMock LitmusSetup instance returning the given prereqs."""
    inst = MagicMock()
    inst.check_prerequisites.return_value = prereqs
    inst.get_cluster_info.return_value = cluster_info or {
        "context": "kind-chaosprobe",
        "server": "https://127.0.0.1:6443",
        "is_local": True,
    }
    inst.get_dashboard_url.return_value = dashboard_url
    return inst


def _run(prereqs, args=None, **mock_kwargs):
    with patch("chaosprobe.commands.status_cmd.LitmusSetup") as cls:
        cls.return_value = _setup_mock(prereqs, **mock_kwargs)
        return CliRunner().invoke(status, args or [])


def test_status_json_output():
    result = _run(_prereqs(), ["--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["cluster_context"] == "kind-chaosprobe"
    assert payload["is_local_cluster"] is True


def test_status_all_ready():
    result = _run(_prereqs(all_ready=True, cluster_access=True))
    assert result.exit_code == 0
    assert "All systems ready!" in result.output
    assert "Context: kind-chaosprobe" in result.output


def test_status_no_cluster_shows_options():
    result = _run(_prereqs(all_ready=False, cluster_access=False))
    assert result.exit_code == 0
    assert "No cluster configured. Options:" in result.output
    assert "Option A" in result.output


def test_status_cluster_but_not_ready_shows_init_hint():
    result = _run(
        _prereqs(
            all_ready=False,
            cluster_access=True,
            chaoscenter_installed=True,
            chaoscenter_ready=True,
        ),
        dashboard_url="http://localhost:9091",
    )
    assert result.exit_code == 0
    assert "Run 'chaosprobe init'" in result.output
    assert "Dashboard URL: http://localhost:9091" in result.output


def test_status_chaoscenter_installed_without_url():
    result = _run(_prereqs(chaoscenter_installed=True), dashboard_url=None)
    assert result.exit_code == 0
    assert "ChaosCenter ready:" in result.output
    assert "Dashboard URL:" not in result.output


def test_status_vagrant_without_kvm():
    result = _run(
        _prereqs(
            vagrant=True,
            libvirt=False,
            libvirt_status={"kvm_available": False, "libvirtd_installed": False},
        )
    )
    assert result.exit_code == 0
    assert "KVM not available" in result.output


def test_status_vagrant_kvm_but_no_libvirtd():
    result = _run(
        _prereqs(
            vagrant=True,
            libvirt=False,
            libvirt_status={"kvm_available": True, "libvirtd_installed": False},
        )
    )
    assert result.exit_code == 0
    assert "chaosprobe cluster vagrant setup" in result.output


def test_status_vagrant_kvm_and_libvirtd_present():
    # vagrant installed, libvirt not "configured" yet, but both KVM and
    # libvirtd are present — neither remediation hint should print.
    result = _run(
        _prereqs(
            vagrant=True,
            libvirt=False,
            libvirt_status={"kvm_available": True, "libvirtd_installed": True},
        )
    )
    assert result.exit_code == 0
    assert "KVM not available" not in result.output
    assert "chaosprobe cluster vagrant setup" not in result.output
