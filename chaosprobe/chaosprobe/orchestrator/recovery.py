"""Control-plane / K8s API recovery helpers.

When a heavy placement strategy overwhelms the control plane (etcd
compaction, API server OOM-kill), subsequent strategies will fail with
``Connection refused`` until the API recovers.  The functions here wait
for that recovery, and as a last-ditch remediation try to SSH into the
control-plane node and force-restart containerd + kubelet.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import click
from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException
from urllib3.exceptions import MaxRetryError, NewConnectionError

from chaosprobe.orchestrator import portforward as pf


def wait_for_k8s_api(namespace: str, timeout: int = 300) -> None:
    """Wait until the K8s API server is reachable.

    Heavy placement strategies can indirectly overwhelm the control plane
    through cascading pressure: worker-node resource starvation triggers
    pod evictions, rescheduling storms, and elevated etcd/API-server
    churn — all of which share the control plane's limited memory.
    Rather than immediately failing all subsequent strategies when the
    API is unreachable, wait for it to recover.

    **Active remediation**: after 60s of passive waiting, attempts to
    SSH into the control plane node and force-restart containerd +
    kubelet.  This handles the case where containerd gets wedged and
    the API server container is stuck in "Created" state — a scenario
    observed after the adversarial strategy overwhelms the node (run
    20260523-093030).

    Re-establishes port-forwards after the API comes back, since the
    kubectl tunnels will have died during the outage.
    """
    try:
        api = k8s_client.CoreV1Api()
        api.list_namespace(limit=1)
        return  # API is reachable, proceed immediately
    except (ApiException, MaxRetryError, NewConnectionError, ConnectionError, OSError):
        # API unreachable → fall through to the wait-for-recovery loop below.
        pass

    click.echo("  K8s API server unreachable — waiting for recovery...")
    deadline = time.time() + timeout
    recovered = False
    remediation_attempted = False
    start = time.time()

    while time.time() < deadline:
        time.sleep(10)
        try:
            api = k8s_client.CoreV1Api()
            api.list_namespace(limit=1)
            recovered = True
            break
        except (ApiException, MaxRetryError, NewConnectionError, ConnectionError, OSError):
            remaining = int(deadline - time.time())
            elapsed = time.time() - start

            # After 60s of passive waiting, attempt active remediation
            # by SSH-ing into the control plane and restarting the
            # container runtime + kubelet.
            if elapsed >= 60 and not remediation_attempted:
                remediation_attempted = True
                _attempt_control_plane_ssh_remediation()

            if remaining > 0 and remaining % 30 < 10:
                click.echo(f"    Still waiting ({remaining}s remaining)...")

    if recovered:
        click.echo("  K8s API server recovered.")
        # Re-establish port-forwards that died during the outage
        pf.ensure_all()
        # Brief stabilisation period for kube-proxy/endpoints to sync.
        # A previous attempt at a dynamic 5-consecutive-OK poll was
        # reverted alongside the portforward.start dynamic-poll change
        # (results/20260518-175642): the chaos infrastructure entered a
        # persistent broken state in that run, and although the cause
        # was not conclusively this gate, the dynamic version was rolled
        # back to restore the known-working pattern.
        time.sleep(10)
    else:
        raise click.ClickException(
            f"K8s API server unreachable for {timeout}s. "
            "The cluster may need manual intervention."
        )


def _attempt_control_plane_ssh_remediation() -> None:
    """SSH into the control plane and force-restart containerd + kubelet.

    Extracts the control plane host from the active kubeconfig's server
    URL.  Tries common SSH key locations (Vagrant insecure key, default
    id_rsa).  This is a best-effort operation — if SSH fails, we fall
    back to passive waiting.
    """
    # Extract control plane host from kubeconfig
    try:
        config = k8s_client.Configuration.get_default_copy()
        parsed = urlparse(config.host)
        cp_host = parsed.hostname
        if not cp_host:
            return
    except Exception:
        return

    click.echo(f"    Attempting SSH remediation on control plane ({cp_host})...")

    # Try common SSH keys in order of likelihood
    ssh_keys = [
        Path.home() / ".vagrant.d" / "insecure_private_key",
        Path.home() / ".ssh" / "id_rsa",
        Path.home() / ".ssh" / "id_ed25519",
    ]
    ssh_key = None
    for key_path in ssh_keys:
        if key_path.exists():
            ssh_key = key_path
            break

    # The remediation command: stop kubelet, force-kill containerd
    # (graceful restart often hangs when it's wedged), restart both.
    remediation_cmd = (
        "sudo systemctl stop kubelet; "
        "sudo systemctl kill -s SIGKILL containerd 2>/dev/null; "
        "sleep 2; "
        "sudo systemctl start containerd; "
        "sleep 3; "
        "sudo systemctl start kubelet"
    )

    ssh_cmd = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "BatchMode=yes",
    ]
    if ssh_key:
        ssh_cmd.extend(["-i", str(ssh_key)])
    ssh_cmd.append(f"vagrant@{cp_host}")
    ssh_cmd.append(remediation_cmd)

    try:
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            click.echo("    SSH remediation: containerd + kubelet restarted, waiting for API...")
        else:
            click.echo(
                f"    SSH remediation failed (exit {result.returncode}): "
                f"{result.stderr.strip()[:100]}",
                err=True,
            )
    except subprocess.TimeoutExpired:
        click.echo("    SSH remediation timed out — continuing passive wait", err=True)
    except Exception as exc:
        click.echo(f"    SSH remediation failed: {exc}", err=True)
