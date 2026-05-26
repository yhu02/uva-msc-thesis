"""Pre-flight cluster checks before running experiments.

Verifies node readiness, cleans up stale resources, checks
infrastructure pods, and waits for deployment health.
"""

import time
from typing import Any, Dict, List

import click

from chaosprobe.k8s import ensure_k8s_config

# Litmus infrastructure deployments to exclude from readiness checks
LITMUS_INFRA_DEPLOYMENTS = frozenset({
    "chaos-exporter",
    "chaos-operator-ce",
    "event-tracker",
    "subscriber",
    "workflow-controller",
})


def wait_for_healthy_deployments(
    namespace: str, timeout: int = 60, *, strict: bool = False
) -> None:
    """Wait until all application deployments in the namespace have all replicas ready.

    Litmus infrastructure deployments are excluded from the check.

    **Active remediation**: when deployments remain unhealthy after an
    initial passive wait (30s or half the timeout, whichever is smaller),
    this function actively intervenes:

    1. Identifies pods stuck in CrashLoopBackOff / Error /
       CreateContainerError / ImagePullBackOff.
    2. Deletes those pods so the deployment controller recreates them
       fresh.
    3. If a deployment still isn't progressing after pod deletion,
       triggers a full rollout restart.

    Transient K8s API connection errors are retried within the timeout
    budget.

    When *strict* is True, raise ``click.ClickException`` if deployments
    are still unhealthy at timeout — used after crash recovery to
    guarantee the next iteration starts from a healthy state.
    """
    from kubernetes import client
    from kubernetes.client.rest import ApiException
    from urllib3.exceptions import HTTPError, MaxRetryError, NewConnectionError

    ensure_k8s_config()

    apps_api = client.AppsV1Api()
    core_api = client.CoreV1Api()
    deadline = time.time() + timeout
    consecutive_errors = 0
    max_consecutive_errors = 6  # 6 * 5s = 30s of sustained API failure
    # After this duration of passive waiting, switch to active remediation.
    remediation_after = min(30, timeout // 2)
    remediation_attempted = False
    start = time.time()

    _STUCK_REASONS = frozenset({
        "CrashLoopBackOff",
        "Error",
        "CreateContainerError",
        "ImagePullBackOff",
        "ErrImagePull",
    })

    while time.time() < deadline:
        try:
            all_ready = True
            unhealthy_deps: List[str] = []
            deps = apps_api.list_namespaced_deployment(namespace)
            consecutive_errors = 0  # reset on success
            for dep in deps.items:
                if dep.metadata.name in LITMUS_INFRA_DEPLOYMENTS:
                    continue
                desired = dep.spec.replicas if dep.spec.replicas is not None else 1
                if desired == 0:
                    continue
                ready = (dep.status.ready_replicas or 0) if dep.status else 0
                available = (dep.status.available_replicas or 0) if dep.status else 0
                if ready < desired or available < desired:
                    all_ready = False
                    unhealthy_deps.append(dep.metadata.name)
            if all_ready:
                return

            # ── Active remediation ──
            # If we've waited passively long enough and deployments are
            # still unhealthy, actively fix them.
            elapsed = time.time() - start
            if elapsed >= remediation_after and not remediation_attempted:
                remediation_attempted = True
                click.echo(
                    f"    Deployments still unhealthy after {int(elapsed)}s, "
                    f"attempting active remediation..."
                )
                _remediate_unhealthy_deployments(
                    apps_api, core_api, namespace, unhealthy_deps, _STUCK_REASONS
                )

        except (ApiException, HTTPError, MaxRetryError, NewConnectionError,
                ConnectionError, OSError) as exc:
            consecutive_errors += 1
            click.echo(
                f"    Warning: K8s API error during health check "
                f"(attempt {consecutive_errors}): {exc}",
                err=True,
            )
            if consecutive_errors >= max_consecutive_errors:
                click.echo(
                    f"    Warning: K8s API unreachable for "
                    f"{consecutive_errors} consecutive checks, giving up wait",
                    err=True,
                )
                if strict:
                    raise click.ClickException(
                        f"K8s API unreachable for {consecutive_errors} consecutive "
                        f"health checks — cannot guarantee cluster health."
                    )
                return
        time.sleep(5)

    # Timeout expired — log which deployments are still unhealthy
    try:
        deps = apps_api.list_namespaced_deployment(namespace)
        for dep in deps.items:
            if dep.metadata.name in LITMUS_INFRA_DEPLOYMENTS:
                continue
            desired = dep.spec.replicas if dep.spec.replicas is not None else 1
            if desired == 0:
                continue
            ready = (dep.status.ready_replicas or 0) if dep.status else 0
            if ready < desired:
                click.echo(
                    f"    Warning: {dep.metadata.name} not fully ready "
                    f"({ready}/{desired} replicas)",
                    err=True,
                )
    except (ApiException, HTTPError, MaxRetryError, NewConnectionError,
            ConnectionError, OSError):
        click.echo(
            "    Warning: could not list deployments for readiness summary",
            err=True,
        )

    if strict:
        raise click.ClickException(
            f"Deployments not healthy after {timeout}s — "
            f"cannot guarantee clean state for next iteration."
        )


def _remediate_unhealthy_deployments(
    apps_api: Any,
    core_api: Any,
    namespace: str,
    unhealthy_deps: List[str],
    stuck_reasons: frozenset,
) -> None:
    """Actively fix unhealthy app deployments.

    Strategy:
    1. For each unhealthy deployment, find pods in a stuck state
       (CrashLoopBackOff, Error, ImagePullBackOff, etc.).
    2. Delete stuck pods — the deployment controller will recreate them.
    3. If no stuck pods were found (deployment just slow), trigger a
       rollout restart to force fresh pod creation.
    """
    from datetime import datetime, timezone

    for dep_name in unhealthy_deps:
        try:
            dep = apps_api.read_namespaced_deployment(dep_name, namespace)
        except Exception:
            continue

        # Find pods belonging to this deployment
        match_labels = dep.spec.selector.match_labels or {}
        if not match_labels:
            continue
        label_selector = ",".join(f"{k}={v}" for k, v in match_labels.items())

        try:
            pods = core_api.list_namespaced_pod(namespace, label_selector=label_selector)
        except Exception:
            continue

        # Identify stuck pods
        stuck_pods = []
        for pod in pods.items:
            for cs in pod.status.container_statuses or []:
                if cs.state and cs.state.waiting and cs.state.waiting.reason in stuck_reasons:
                    stuck_pods.append(pod.metadata.name)
                    break

        if stuck_pods:
            # Delete stuck pods — controller will recreate them
            click.echo(
                f"    Deleting {len(stuck_pods)} stuck pod(s) for {dep_name}: "
                f"{', '.join(stuck_pods[:3])}{'...' if len(stuck_pods) > 3 else ''}"
            )
            for pod_name in stuck_pods:
                try:
                    core_api.delete_namespaced_pod(
                        pod_name, namespace, grace_period_seconds=0
                    )
                except Exception:
                    pass
        else:
            # No stuck pods but deployment isn't ready — trigger rollout restart
            click.echo(f"    Triggering rollout restart for {dep_name}")
            try:
                apps_api.patch_namespaced_deployment(
                    dep_name,
                    namespace,
                    {
                        "spec": {
                            "template": {
                                "metadata": {
                                    "annotations": {
                                        "chaosprobe.io/restartedAt": datetime.now(
                                            timezone.utc
                                        ).isoformat(),
                                    }
                                }
                            }
                        }
                    },
                )
            except Exception as exc:
                click.echo(
                    f"    Warning: rollout restart failed for {dep_name}: {exc}",
                    err=True,
                )


def extract_target_deployment(scenario: Dict[str, Any]) -> str:
    """Extract the target deployment name from experiment appinfo."""
    for exp in scenario.get("experiments", []):
        appinfo = exp.get("spec", {}).get("spec", {}).get("appinfo", {})
        applabel = appinfo.get("applabel", "")
        if applabel.startswith("app="):
            return applabel.split("=", 1)[1]
    raise ValueError(
        "Could not extract target deployment from scenario. "
        "Ensure each ChaosEngine experiment has spec.spec.appinfo.applabel "
        "set (e.g. 'app=myservice')."
    )


def extract_load_service(scenario: Dict[str, Any]) -> str:
    """Extract the load-target service from httpProbe URLs in the scenario.

    Returns the hostname of the first httpProbe URL (the service that receives
    user-facing traffic), or ``"frontend"`` as a last-resort default.
    """
    from urllib.parse import urlparse

    for exp_entry in scenario.get("experiments", []):
        spec = exp_entry.get("spec", {})
        for exp in spec.get("spec", {}).get("experiments", []):
            for probe in exp.get("spec", {}).get("probe", []):
                if probe.get("type") != "httpProbe":
                    continue
                url = probe.get("httpProbe/inputs", {}).get("url", "")
                if not url:
                    continue
                host = urlparse(url).hostname or ""
                service = host.split(".")[0] if host else ""
                if service:
                    return service
    return "frontend"


def check_pods_ready(namespace: str, label: str) -> bool:
    """Check that at least one pod matching *label* is Running and Ready."""
    from kubernetes import client

    ensure_k8s_config()

    core_api = client.CoreV1Api()
    try:
        pods = core_api.list_namespaced_pod(namespace, label_selector=label)
        for pod in pods.items:
            if pod.status.phase == "Running":
                ready = all(cs.ready for cs in (pod.status.container_statuses or []))
                if ready:
                    return True
    except Exception as e:
        click.echo(f"    Warning: pod readiness check failed ({e})", err=True)
    return False


def extract_experiment_types(scenario: dict) -> List[str]:
    """Extract experiment type names from a loaded scenario."""
    types: List[str] = []
    for exp in scenario.get("experiments", []):
        spec = exp.get("spec", {})
        for experiment in spec.get("spec", {}).get("experiments", []):
            name = experiment.get("name", "")
            if name:
                types.append(name)
    return types
