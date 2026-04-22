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


def wait_for_healthy_deployments(namespace: str, timeout: int = 60) -> None:
    """Wait until all application deployments in the namespace have all replicas ready.

    Litmus infrastructure deployments are excluded from the check.

    Transient K8s API connection errors (e.g. API server restart,
    network blip) are retried within the timeout budget rather than
    immediately propagated.  This prevents a temporary ``Connection
    refused`` from crashing the entire experiment run.
    """
    from kubernetes import client
    from kubernetes.client.rest import ApiException
    from urllib3.exceptions import HTTPError, MaxRetryError, NewConnectionError

    ensure_k8s_config()

    apps_api = client.AppsV1Api()
    deadline = time.time() + timeout
    consecutive_errors = 0
    max_consecutive_errors = 6  # 6 * 5s = 30s of sustained API failure

    while time.time() < deadline:
        try:
            all_ready = True
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
                    break
            if all_ready:
                return
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
                return
        time.sleep(5)

    # Log which deployments are not ready but don't fail
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
