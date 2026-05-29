"""Tests for the reproducibility-metadata gatherer."""

from unittest.mock import MagicMock, patch

from chaosprobe.metrics.reproducibility import (
    _cni_hint,
    _git_describe,
    _kubernetes_server_info,
    gather_run_metadata,
)


class TestGitDescribe:
    def test_returns_commit_and_dirty_flag(self, tmp_path):
        # Simulate a clean repo: rev-parse OK, status empty.
        with patch("chaosprobe.metrics.reproducibility.subprocess.run") as mock_run:

            def side_effect(cmd, *a, **kw):
                if cmd[1] == "rev-parse":
                    return MagicMock(returncode=0, stdout="abc123def456789\n")
                if cmd[1] == "status":
                    return MagicMock(returncode=0, stdout="")
                return MagicMock(returncode=1, stdout="")

            mock_run.side_effect = side_effect
            out = _git_describe(repo_dir=str(tmp_path))

        assert out["commit"] == "abc123def456789"
        assert out["shortCommit"] == "abc123def456"
        assert out["dirty"] is False

    def test_dirty_repo_flag_true(self, tmp_path):
        with patch("chaosprobe.metrics.reproducibility.subprocess.run") as mock_run:

            def side_effect(cmd, *a, **kw):
                if cmd[1] == "rev-parse":
                    return MagicMock(returncode=0, stdout="deadbeef" * 5 + "\n")
                if cmd[1] == "status":
                    return MagicMock(returncode=0, stdout=" M file.py\n")
                return MagicMock(returncode=1, stdout="")

            mock_run.side_effect = side_effect
            out = _git_describe(repo_dir=str(tmp_path))
        assert out["dirty"] is True

    def test_git_not_installed_returns_all_none(self, tmp_path):
        with patch(
            "chaosprobe.metrics.reproducibility.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            out = _git_describe(repo_dir=str(tmp_path))
        assert out == {"commit": None, "shortCommit": None, "dirty": None}

    def test_git_timeout_returns_all_none(self, tmp_path):
        import subprocess as _sp

        with patch(
            "chaosprobe.metrics.reproducibility.subprocess.run",
            side_effect=_sp.TimeoutExpired(cmd="git", timeout=1.0),
        ):
            out = _git_describe(repo_dir=str(tmp_path))
        assert out == {"commit": None, "shortCommit": None, "dirty": None}

    def test_non_repo_dir_no_commit(self, tmp_path):
        with patch("chaosprobe.metrics.reproducibility.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            out = _git_describe(repo_dir=str(tmp_path))
        assert out["commit"] is None
        assert out["dirty"] is None


class TestKubernetesServerInfo:
    def test_none_core_api_returns_none_fields(self):
        out = _kubernetes_server_info(None)
        assert out["serverVersion"] is None
        assert out["containerRuntimeOnFirstNode"] is None
        assert out["firstNodeOS"] is None

    def test_collects_node_runtime_and_os(self):
        core = MagicMock()
        node = MagicMock()
        node.status.node_info.container_runtime_version = "containerd://1.7.11"
        node.status.node_info.os_image = "Ubuntu 22.04.4 LTS"
        core.list_node.return_value = MagicMock(items=[node])
        out = _kubernetes_server_info(core)
        assert out["containerRuntimeOnFirstNode"] == "containerd://1.7.11"
        assert out["firstNodeOS"] == "Ubuntu 22.04.4 LTS"

    def test_node_list_failure_no_crash(self):
        core = MagicMock()
        core.list_node.side_effect = Exception("boom")
        out = _kubernetes_server_info(core)
        assert out["containerRuntimeOnFirstNode"] is None


class TestCNIHint:
    def test_none_core_api_returns_none(self):
        assert _cni_hint(None) is None

    def test_detects_calico(self):
        core = MagicMock()
        pod = MagicMock()
        pod.metadata.name = "calico-node-abc12"
        core.list_namespaced_pod.return_value = MagicMock(items=[pod])
        assert _cni_hint(core) == "calico"

    def test_detects_cilium(self):
        core = MagicMock()
        pod = MagicMock()
        pod.metadata.name = "cilium-xyz"
        core.list_namespaced_pod.return_value = MagicMock(items=[pod])
        assert _cni_hint(core) == "cilium"

    def test_no_match_returns_none(self):
        core = MagicMock()
        pod = MagicMock()
        pod.metadata.name = "coredns-abc"
        core.list_namespaced_pod.return_value = MagicMock(items=[pod])
        assert _cni_hint(core) is None

    def test_listing_failure_returns_none(self):
        core = MagicMock()
        core.list_namespaced_pod.side_effect = Exception("denied")
        assert _cni_hint(core) is None


class TestGatherRunMetadata:
    def test_assembles_all_fields(self, tmp_path):
        # Stub out every external call so this stays fast and
        # deterministic.
        with patch(
            "chaosprobe.metrics.reproducibility._git_describe",
            return_value={"commit": "cafe", "shortCommit": "cafe", "dirty": False},
        ):
            md = gather_run_metadata(core_api=None, repo_dir=str(tmp_path))
        assert md["chaosprobeVersion"]  # any non-empty string
        assert md["pythonVersion"]
        assert md["capturedAt"]
        assert md["git"]["commit"] == "cafe"
        assert md["kubernetes"]["serverVersion"] is None  # no core_api
        assert md["cniHint"] is None  # no core_api

    def test_with_core_api_populates_kubernetes_and_cni(self, tmp_path):
        core = MagicMock()
        # CNI hint
        cni_pod = MagicMock()
        cni_pod.metadata.name = "calico-kube-controllers-xyz"
        core.list_namespaced_pod.return_value = MagicMock(items=[cni_pod])
        # Node info
        node = MagicMock()
        node.status.node_info.container_runtime_version = "containerd://1.7.11"
        node.status.node_info.os_image = "Ubuntu 22.04"
        core.list_node.return_value = MagicMock(items=[node])

        with patch(
            "chaosprobe.metrics.reproducibility._git_describe",
            return_value={"commit": None, "shortCommit": None, "dirty": None},
        ):
            md = gather_run_metadata(core_api=core, repo_dir=str(tmp_path))
        assert md["cniHint"] == "calico"
        assert md["kubernetes"]["containerRuntimeOnFirstNode"] == "containerd://1.7.11"
