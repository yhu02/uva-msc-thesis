"""Tests for Kubespray bootstrap idempotence."""

from unittest.mock import patch

import pytest

from chaosprobe.provisioner.setup import KubesprayPythonError, LitmusSetup


def _setup_with_kubespray_dir(tmp_path):
    setup = LitmusSetup(skip_k8s_init=True)
    setup.KUBESPRAY_DIR = tmp_path
    (tmp_path / "requirements.txt").write_text("ansible==9.0.0\n")
    return setup


def test_ensure_kubespray_installs_dependencies_for_partial_venv(tmp_path):
    setup = _setup_with_kubespray_dir(tmp_path)
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "pip").write_text("# pip")

    with (
        patch.object(setup, "_select_kubespray_python", return_value="python3.11"),
        patch("chaosprobe.provisioner.setup.subprocess.run") as mock_run,
    ):
        assert setup._ensure_kubespray() == tmp_path

    mock_run.assert_any_call(
        [str(tmp_path / "venv" / "bin" / "pip"), "install", "-U", "pip"],
        check=True,
    )
    mock_run.assert_any_call(
        [
            str(tmp_path / "venv" / "bin" / "pip"),
            "install",
            "-r",
            str(tmp_path / "requirements.txt"),
        ],
        check=True,
    )


def test_ensure_kubespray_skips_dependency_install_for_complete_venv(tmp_path):
    setup = _setup_with_kubespray_dir(tmp_path)
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "ansible-playbook").write_text("# ansible-playbook")

    with (
        patch.object(setup, "_select_kubespray_python", return_value="python3.11"),
        patch("chaosprobe.provisioner.setup.subprocess.run") as mock_run,
    ):
        assert setup._ensure_kubespray() == tmp_path

    mock_run.assert_not_called()


def test_ensure_kubespray_recreates_incompatible_venv(tmp_path):
    setup = _setup_with_kubespray_dir(tmp_path)
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "pyvenv.cfg").write_text("version = 3.14.4\n")

    with (
        patch.object(setup, "_select_kubespray_python", return_value="python3.11"),
        patch("chaosprobe.provisioner.setup.subprocess.run") as mock_run,
    ):
        assert setup._ensure_kubespray() == tmp_path

    mock_run.assert_any_call(["python3.11", "-m", "venv", str(tmp_path / "venv")], check=True)


def test_select_kubespray_python_uses_compatible_candidate():
    setup = LitmusSetup(skip_k8s_init=True)

    def version(candidate):
        return {
            "python3.11": (3, 11),
            "python3.10": (3, 10),
            "python3": (3, 14),
        }[candidate]

    with patch.object(setup, "_python_version", side_effect=version):
        assert setup._select_kubespray_python() == "python3.11"


def test_select_kubespray_python_rejects_python_314_only():
    setup = LitmusSetup(skip_k8s_init=True)
    setup.KUBESPRAY_PYTHON_CANDIDATES = ("python3",)

    with patch.object(setup, "_python_version", return_value=(3, 14)):
        with pytest.raises(KubesprayPythonError, match="require Python"):
            setup._select_kubespray_python()


def test_select_kubespray_python_honors_environment_override(monkeypatch):
    setup = LitmusSetup(skip_k8s_init=True)
    monkeypatch.setenv("CHAOSPROBE_KUBESPRAY_PYTHON", "/opt/python3.11/bin/python")

    with patch.object(setup, "_python_version", return_value=(3, 11)) as mock_version:
        assert setup._select_kubespray_python() == "/opt/python3.11/bin/python"

    mock_version.assert_called_once_with("/opt/python3.11/bin/python")
