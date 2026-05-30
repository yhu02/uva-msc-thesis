"""Tests for the ``chaosprobe provision`` command (commands/provision_cmd.py)."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaosprobe.commands.provision_cmd import provision


def test_provision_success(tmp_path):
    scenario_file = tmp_path / "scenario.yaml"
    scenario_file.write_text("namespace: demo\n")
    prov = MagicMock()
    with (
        patch(
            "chaosprobe.commands.provision_cmd.load_scenario",
            return_value={"namespace": "demo", "manifests": [{"a": 1}]},
        ),
        patch("chaosprobe.commands.provision_cmd.validate_scenario"),
        patch("chaosprobe.commands.provision_cmd.KubernetesProvisioner", return_value=prov),
    ):
        result = CliRunner().invoke(provision, [str(scenario_file)])
    assert result.exit_code == 0
    assert "Manifests deployed successfully" in result.output
    prov.provision.assert_called_once_with([{"a": 1}])


def test_provision_namespace_override(tmp_path):
    scenario_file = tmp_path / "scenario.yaml"
    scenario_file.write_text("namespace: demo\n")
    with (
        patch(
            "chaosprobe.commands.provision_cmd.load_scenario",
            return_value={"namespace": "demo", "manifests": []},
        ),
        patch("chaosprobe.commands.provision_cmd.validate_scenario"),
        patch("chaosprobe.commands.provision_cmd.KubernetesProvisioner") as cls,
    ):
        result = CliRunner().invoke(provision, [str(scenario_file), "-n", "override-ns"])
    assert result.exit_code == 0
    assert "Namespace: override-ns" in result.output
    cls.assert_called_once_with("override-ns")


def test_provision_scenario_load_error(tmp_path):
    scenario_file = tmp_path / "scenario.yaml"
    scenario_file.write_text("broken\n")
    with patch(
        "chaosprobe.commands.provision_cmd.load_scenario",
        side_effect=ValueError("bad scenario"),
    ):
        result = CliRunner().invoke(provision, [str(scenario_file)])
    assert result.exit_code == 1
    assert "Error loading scenario: bad scenario" in result.output


def test_provision_deploy_error(tmp_path):
    scenario_file = tmp_path / "scenario.yaml"
    scenario_file.write_text("namespace: demo\n")
    prov = MagicMock()
    prov.provision.side_effect = RuntimeError("apply failed")
    with (
        patch(
            "chaosprobe.commands.provision_cmd.load_scenario",
            return_value={"manifests": []},
        ),
        patch("chaosprobe.commands.provision_cmd.validate_scenario"),
        patch("chaosprobe.commands.provision_cmd.KubernetesProvisioner", return_value=prov),
    ):
        result = CliRunner().invoke(provision, [str(scenario_file)])
    assert result.exit_code == 1
    assert "Error deploying manifests: apply failed" in result.output
