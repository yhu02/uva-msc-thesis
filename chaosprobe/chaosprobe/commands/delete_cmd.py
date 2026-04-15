"""CLI command: chaosprobe delete — remove all ChaosProbe infrastructure."""

import sys

import click

from chaosprobe.orchestrator import portforward as pf
from chaosprobe.provisioner.setup import LitmusSetup


@click.command()
@click.option(
    "--namespace",
    "-n",
    default="chaosprobe-test",
    help="Target namespace to clean chaos resources from",
)
@click.option(
    "--keep-app",
    is_flag=True,
    default=True,
    help="Keep application deployments (default: true)",
)
@click.confirmation_option(
    prompt="This will delete ChaosCenter, Prometheus, Neo4j, metrics-server, "
    "and all chaos resources. Continue?",
)
def delete(namespace: str, keep_app: bool):
    """Delete all ChaosProbe infrastructure and experiment artifacts.

    Removes everything installed by 'chaosprobe init' and 'chaosprobe run':
    - ChaosCenter (litmus namespace)
    - Prometheus (prometheus namespace)
    - Neo4j (neo4j namespace)
    - metrics-server
    - local-path-provisioner (local-path-storage namespace)
    - Litmus infra deployments in the target namespace
    - Stale ChaosEngines, ChaosResults, and completed pods

    Namespace deletions run in parallel for speed.
    Application deployments (e.g. Online Boutique) are kept by default.
    """
    import subprocess as _del_sp
    from concurrent.futures import ThreadPoolExecutor, as_completed

    setup = LitmusSetup(skip_k8s_init=True)
    is_valid, _ = setup.validate_cluster()
    if not is_valid:
        click.echo("Error: No reachable cluster.", err=True)
        sys.exit(1)
    setup._init_k8s_client()

    # 1. Kill lingering port-forwards
    click.echo("Stopping port-forwards...")
    _del_sp.run(["pkill", "-f", "kubectl port-forward"],
                capture_output=True, timeout=10)
    pf.cleanup()

    # 2. Clear placement constraints
    click.echo(f"Clearing placement constraints in {namespace}...")
    try:
        from chaosprobe.placement.mutator import PlacementMutator
        mutator = PlacementMutator(namespace)
        cleared = mutator.clear_placement(wait=True, timeout=120)
        if cleared:
            click.echo(f"  Cleared {len(cleared)} deployment(s)")
        else:
            click.echo("  No placement constraints found")
    except Exception as e:
        click.echo(f"  Warning: {e}")

    # 3. Remove Litmus infra deployments from target namespace (before ns deletion)
    click.echo(f"Removing Litmus infra from {namespace}...")
    infra_deps = [
        "chaos-exporter", "chaos-operator-ce", "event-tracker",
        "subscriber", "workflow-controller",
    ]
    for dep in infra_deps:
        _del_sp.run(
            ["kubectl", "delete", "deployment", dep,
             "-n", namespace, "--ignore-not-found"],
            capture_output=True, timeout=30,
        )
    click.echo(f"  Litmus infra deployments removed from {namespace}")

    # 4. Clean stale CRDs and completed pods in target namespace
    click.echo(f"Cleaning chaos resources in {namespace}...")
    for resource in ["chaosengines", "chaosresults"]:
        _del_sp.run(
            ["kubectl", "delete", resource, "--all",
             "-n", namespace, "--ignore-not-found", "--timeout=120s"],
            capture_output=True, timeout=180,
        )
    for phase in ["Succeeded", "Failed", "Completed"]:
        _del_sp.run(
            ["kubectl", "delete", "pods",
             f"--field-selector=status.phase=={phase}",
             "-n", namespace, "--ignore-not-found"],
            capture_output=True, timeout=30,
        )
    click.echo("  Chaos resources cleaned")

    # 5. Delete infrastructure namespaces + metrics-server in PARALLEL
    def _delete_namespace(ns: str, label: str) -> str:
        """Delete a namespace and return a status message."""
        result = _del_sp.run(
            ["kubectl", "delete", "namespace", ns, "--timeout=120s"],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode == 0:
            return f"  {label}: deleted"
        if "not found" in result.stderr.lower():
            return f"  {label}: not found (already deleted)"
        return f"  {label}: Warning: {result.stderr.strip()}"

    def _delete_metrics_server() -> str:
        """Delete metrics-server components and return a status message."""
        _del_sp.run(
            ["kubectl", "delete", "deployment", "metrics-server",
             "-n", "kube-system", "--ignore-not-found"],
            capture_output=True, text=True, timeout=30,
        )
        _del_sp.run(
            ["kubectl", "delete", "service", "metrics-server",
             "-n", "kube-system", "--ignore-not-found"],
            capture_output=True, timeout=30,
        )
        _del_sp.run(
            ["kubectl", "delete", "apiservice", "v1beta1.metrics.k8s.io",
             "--ignore-not-found"],
            capture_output=True, timeout=30,
        )
        return "  metrics-server: deleted"

    click.echo("Deleting infrastructure (parallel)...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_delete_namespace, "litmus", "ChaosCenter (litmus)"): "litmus",
            executor.submit(_delete_namespace, "prometheus", "Prometheus (prometheus)"): "prometheus",
            executor.submit(_delete_namespace, "neo4j", "Neo4j (neo4j)"): "neo4j",
            executor.submit(_delete_namespace, "local-path-storage", "local-path-provisioner (local-path-storage)"): "local-path-storage",
            executor.submit(_delete_metrics_server): "metrics-server",
        }
        for future in as_completed(futures):
            try:
                click.echo(future.result())
            except Exception as e:
                click.echo(f"  {futures[future]}: Warning: {e}")

    click.echo("\nAll ChaosProbe infrastructure deleted.")
    click.echo(f"Application deployments in '{namespace}' were kept.")
    click.echo(f"Run 'chaosprobe init -n {namespace}' to re-initialize.")
