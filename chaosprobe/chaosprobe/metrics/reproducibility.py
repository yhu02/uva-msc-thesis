"""Reproducibility metadata for a chaosprobe run.

Captures the environment fingerprint a defender needs to argue
"reproducible on a comparable cluster" rather than just on faith:
chaosprobe + Python versions, git commit, Kubernetes server version,
node container runtime, and the CNI hint.  Everything is best-effort —
each lookup wraps its own failure and contributes ``None`` on error
rather than crashing the whole run.
"""

from __future__ import annotations

import os
import platform
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TypedDict

from chaosprobe import __version__ as CHAOSPROBE_VERSION

_GIT_COMMAND_TIMEOUT = 5.0


class GitInfo(TypedDict):
    """Fixed-shape result of :func:`_git_describe`."""

    commit: Optional[str]
    shortCommit: Optional[str]
    dirty: Optional[bool]


def _git_describe(repo_dir: Optional[str] = None) -> GitInfo:
    """Capture the current git commit + dirty flag.

    Returns ``{commit, shortCommit, dirty}`` — any field may be ``None``
    if git isn't available, the directory isn't a repo, or the command
    errors out.
    """
    cwd = repo_dir or os.getcwd()
    out: GitInfo = {"commit": None, "shortCommit": None, "dirty": None}
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_COMMAND_TIMEOUT,
            check=False,
        )
        if commit.returncode == 0:
            out["commit"] = commit.stdout.strip() or None
            if out["commit"]:
                out["shortCommit"] = out["commit"][:12]

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_COMMAND_TIMEOUT,
            check=False,
        )
        if status.returncode == 0:
            out["dirty"] = bool(status.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # git not installed or hung
        pass
    return out


def _kubernetes_server_info(core_api: Optional[Any]) -> Dict[str, Optional[str]]:
    """Best-effort K8s server version + control-plane node container
    runtime via the kubernetes API."""
    info: Dict[str, Optional[str]] = {
        "serverVersion": None,
        "containerRuntimeOnFirstNode": None,
        "firstNodeOS": None,
    }
    if core_api is None:
        return info
    try:
        from kubernetes import client as _k8s_client  # local import — runtime-only

        version_api = _k8s_client.VersionApi(api_client=core_api.api_client)
        ver = version_api.get_code()
        info["serverVersion"] = getattr(ver, "git_version", None)
    except Exception:
        pass
    try:
        nodes = core_api.list_node(limit=1)
        if nodes.items:
            node = nodes.items[0]
            node_info = getattr(node.status, "node_info", None)
            if node_info is not None:
                info["containerRuntimeOnFirstNode"] = getattr(
                    node_info, "container_runtime_version", None
                )
                os_image = getattr(node_info, "os_image", None)
                if os_image:
                    info["firstNodeOS"] = os_image
    except Exception:
        pass
    return info


def _cni_hint(core_api: Optional[Any]) -> Optional[str]:
    """Cheap detection: look for a pod whose name starts with
    ``calico-`` / ``cilium-`` / ``kube-flannel-`` / ``weave-`` in
    ``kube-system``.  Returns the matched prefix or ``None`` if the
    listing fails or nothing matches."""
    if core_api is None:
        return None
    try:
        pods = core_api.list_namespaced_pod("kube-system", limit=200)
    except Exception:
        return None
    prefixes = ("calico-", "cilium-", "kube-flannel-", "weave-")
    for pod in pods.items:
        name = getattr(pod.metadata, "name", "") or ""
        for prefix in prefixes:
            if name.startswith(prefix):
                return prefix.rstrip("-")
    return None


def gather_run_metadata(
    core_api: Optional[Any] = None,
    repo_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Gather a reproducibility fingerprint for the current run.

    All fields are best-effort — missing lookups become ``None`` so a
    minimal report still records what *was* known.

    Returns ``{chaosprobeVersion, pythonVersion, hostname, capturedAt,
    git: {...}, kubernetes: {...}, cniHint}``.
    """
    return {
        "chaosprobeVersion": CHAOSPROBE_VERSION,
        "pythonVersion": platform.python_version(),
        "platform": platform.platform(),
        "hostname": platform.node() or None,
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "git": _git_describe(repo_dir),
        "kubernetes": _kubernetes_server_info(core_api),
        "cniHint": _cni_hint(core_api),
    }
