"""Tests for the registry-trust group_vars injected into the Kubespray inventory.

`generate_inventory` writes a `group_vars/all/chaosprobe-registry.yml` that marks
the in-cluster probe-image registry insecure, so each node's containerd can pull
probe images over plain HTTP (otherwise pulls fail the HTTPS-vs-HTTP handshake).
"""

from unittest.mock import patch

import yaml

from chaosprobe.provisioner.components import REGISTRY_NODEPORT
from chaosprobe.provisioner.setup import LitmusSetup, _registry_trust_group_vars

TRUST_FILE = ("group_vars", "all", "chaosprobe-registry.yml")


def test_group_vars_marks_registry_insecure_and_keeps_dockerio():
    parsed = yaml.safe_load(_registry_trust_group_vars("1.2.3.4:30500"))
    mirrors = parsed["containerd_registries_mirrors"]
    assert len(mirrors) == 2

    dockerio = next(m for m in mirrors if m["prefix"] == "docker.io")
    assert dockerio["mirrors"][0]["skip_verify"] is False

    registry = next(m for m in mirrors if m["prefix"] == "1.2.3.4:30500")
    assert registry["mirrors"][0]["host"] == "http://1.2.3.4:30500"
    assert registry["mirrors"][0]["skip_verify"] is True
    assert registry["mirrors"][0]["capabilities"] == ["pull", "resolve"]


def _generate(tmp_path, hosts):
    """Run generate_inventory with the kubespray clone/copy steps stubbed out."""
    setup = LitmusSetup.__new__(LitmusSetup)
    with patch.object(setup, "_ensure_kubespray", return_value=tmp_path / "kubespray"):
        return setup.generate_inventory(hosts, output_dir=tmp_path / "inv")


def test_generate_inventory_writes_trust_file_for_control_plane(tmp_path):
    hosts = [
        {"name": "cp1", "ip": "10.0.0.1", "roles": ["control_plane"]},
        {"name": "worker1", "ip": "10.0.0.2", "roles": ["worker"]},
    ]
    out = _generate(tmp_path, hosts)

    parsed = yaml.safe_load((out.joinpath(*TRUST_FILE)).read_text())
    prefixes = [m["prefix"] for m in parsed["containerd_registries_mirrors"]]
    assert f"10.0.0.1:{REGISTRY_NODEPORT}" in prefixes


def test_generate_inventory_skips_trust_file_without_control_plane(tmp_path):
    hosts = [{"name": "worker1", "ip": "10.0.0.2", "roles": ["worker"]}]
    out = _generate(tmp_path, hosts)

    assert not out.joinpath(*TRUST_FILE).exists()
