"""ChaosProbe CLI - Main entry point for the chaos testing framework."""

import json
import sys
from pathlib import Path
from typing import Optional

import click
from dotenv import find_dotenv, load_dotenv

from chaosprobe.commands.shared import (
    get_graph_store as _get_graph_store,
)
from chaosprobe.commands.shared import (
    neo4j_password_option as _neo4j_password_option,
)
from chaosprobe.commands.shared import (
    neo4j_uri_option as _neo4j_uri_option,
)
from chaosprobe.commands.shared import (
    neo4j_user_option as _neo4j_user_option,
)
from chaosprobe.config.loader import load_scenario
from chaosprobe.config.validator import validate_scenario
from chaosprobe.output.comparison import compare_runs
from chaosprobe.provisioner.kubernetes import KubernetesProvisioner
from chaosprobe.provisioner.setup import LitmusSetup


@click.group()
@click.version_option()
def main():
    """ChaosProbe - Kubernetes chaos testing framework with AI-consumable output.

    Deploys Kubernetes manifests, runs native LitmusChaos experiments,
    Scenarios are directories containing K8s manifests and ChaosEngine YAML.
    """
    # Load .env from CWD or any parent directory. Shell-exported vars win.
    load_dotenv(find_dotenv(usecwd=True), override=False)


@main.command()
@click.option("--json", "json_output", is_flag=True, help="Output status as JSON")
def status(json_output: bool):
    """Check the status of ChaosProbe and its dependencies."""
    setup = LitmusSetup(skip_k8s_init=True)
    setup._init_k8s_client()
    prereqs = setup.check_prerequisites()

    cluster_info = setup.get_cluster_info()
    prereqs["cluster_context"] = cluster_info.get("context")
    prereqs["cluster_server"] = cluster_info.get("server")
    prereqs["is_local_cluster"] = cluster_info.get("is_local")

    if json_output:
        click.echo(json.dumps(prereqs, indent=2))
        return

    click.echo("ChaosProbe Status:")
    click.echo(f"  kubectl: {'OK' if prereqs['kubectl'] else 'MISSING'}")
    click.echo(f"  helm: {'OK' if prereqs['helm'] else 'MISSING'}")
    click.echo(f"  git: {'OK' if prereqs['git'] else 'MISSING'}")
    click.echo(f"  ssh: {'OK' if prereqs['ssh'] else 'MISSING'}")
    click.echo(f"  ansible: {'OK' if prereqs['ansible'] else 'Not installed'}")
    click.echo(f"  vagrant: {'OK' if prereqs['vagrant'] else 'Not installed'}")
    click.echo(f"  libvirt: {'OK' if prereqs['libvirt'] else 'Not configured'}")
    if prereqs["vagrant"] and not prereqs["libvirt"]:
        libvirt_status = prereqs.get("libvirt_status", {})
        if not libvirt_status.get("kvm_available"):
            click.echo("    KVM not available (check BIOS/WSL2 settings)")
        elif not libvirt_status.get("libvirtd_installed"):
            click.echo("    Run: chaosprobe cluster vagrant setup")
    click.echo(f"  Cluster access: {'OK' if prereqs['cluster_access'] else 'No cluster'}")
    if prereqs["cluster_access"]:
        click.echo(f"    Context: {prereqs['cluster_context']}")
        click.echo(f"    Server: {prereqs['cluster_server']}")
    click.echo(f"  LitmusChaos installed: {'Yes' if prereqs['litmus_installed'] else 'No'}")
    click.echo(f"  LitmusChaos ready: {'Yes' if prereqs['litmus_ready'] else 'No'}")
    click.echo(
        f"  ChaosCenter dashboard: "
        f"{'Installed' if prereqs['chaoscenter_installed'] else 'Not installed'}"
    )
    if prereqs["chaoscenter_installed"]:
        click.echo(
            f"  ChaosCenter ready: {'Yes' if prereqs['chaoscenter_ready'] else 'No'}"
        )
        url = setup.get_dashboard_url()
        if url:
            click.echo(f"  Dashboard URL: {url}")

    if prereqs["all_ready"]:
        click.echo("\nAll systems ready!")
    else:
        if not prereqs["cluster_access"]:
            click.echo("\nNo cluster configured. Options:")
            click.echo("  Option A — Local libvirt/Vagrant cluster:")
            click.echo("    1. chaosprobe cluster vagrant init"
                       "        (first time only — generates Vagrantfile)")
            click.echo("    2. chaosprobe cluster vagrant setup"
                       "       (first time only — installs libvirt/KVM)")
            click.echo("    3. chaosprobe cluster vagrant up          (start VMs)")
            click.echo("    4. chaosprobe cluster vagrant deploy"
                       "      (install Kubernetes via Kubespray)")
            click.echo("    5. chaosprobe cluster vagrant kubeconfig  (fetch kubeconfig)")
            click.echo("    6. chaosprobe init"
                       "                        (install ChaosProbe infrastructure)")
            click.echo("    7. chaosprobe run"
                       "                         (run experiments)")
            click.echo("  Option B — Bare metal/cloud VMs with Kubespray:")
            click.echo("    1. chaosprobe cluster create")
            click.echo("    2. chaosprobe init")
            click.echo("    3. chaosprobe run")
            click.echo("  Option C — Existing cluster:")
            click.echo("    1. Configure kubectl to connect to your cluster")
            click.echo("    2. chaosprobe init")
            click.echo("    3. chaosprobe run")
        else:
            click.echo("\nRun 'chaosprobe init' to set up infrastructure,")
            click.echo("or 'chaosprobe run' to auto-install and run experiments.")


# ─────────────────────────────────────────────────────────────
# Scenario commands
# ─────────────────────────────────────────────────────────────


@main.command()
@click.argument("scenario_path", type=click.Path(exists=True))
@click.option("--namespace", "-n", default=None, help="Override namespace (default: from scenario)")
def provision(scenario_path: str, namespace: Optional[str]):
    """Deploy manifests from a scenario without running experiments.

    SCENARIO_PATH: Directory or file containing K8s manifests.
    """
    click.echo(f"Loading scenario from {scenario_path}...")

    try:
        scenario = load_scenario(scenario_path)
        validate_scenario(scenario)
    except Exception as e:
        click.echo(f"Error loading scenario: {e}", err=True)
        sys.exit(1)

    if namespace:
        scenario["namespace"] = namespace

    target_namespace = scenario.get("namespace", "default")

    click.echo(f"Deploying {len(scenario.get('manifests', []))} manifest(s)...")
    click.echo(f"  Namespace: {target_namespace}")

    provisioner = KubernetesProvisioner(target_namespace)
    try:
        provisioner.provision(scenario.get("manifests", []))
        click.echo("Manifests deployed successfully")
    except Exception as e:
        click.echo(f"Error deploying manifests: {e}", err=True)
        sys.exit(1)


@main.command()
@click.argument("baseline", type=str)
@click.argument("afterfix", type=str)
@click.option("--output", "-o", type=click.Path(), help="Output file for comparison JSON")
@_neo4j_uri_option
@_neo4j_user_option
@_neo4j_password_option
def compare(
    baseline: str,
    afterfix: str,
    output: Optional[str],
    neo4j_uri: Optional[str],
    neo4j_user: str,
    neo4j_password: str,
):
    """Compare baseline results with after-fix results.

    BASELINE: Run ID (Neo4j) or path to baseline results JSON file.
    AFTERFIX: Run ID (Neo4j) or path to after-fix results JSON file.

    \b
    Examples:
      chaosprobe compare run-2026-04-02-1234 run-2026-04-02-5678 --neo4j-uri bolt://localhost:7687
      chaosprobe compare baseline.json afterfix.json  # legacy JSON file mode
    """
    # Auto-detect file mode: if both arguments look like file paths, use JSON files
    baseline_is_file = Path(baseline).exists()
    afterfix_is_file = Path(afterfix).exists()

    if baseline_is_file and afterfix_is_file:
        click.echo(f"Comparing JSON files: {baseline} vs {afterfix}...")
        try:
            baseline_data = json.loads(Path(baseline).read_text())
            afterfix_data = json.loads(Path(afterfix).read_text())
        except Exception as e:
            click.echo(f"Error loading result files: {e}", err=True)
            sys.exit(1)
    elif neo4j_uri:
        click.echo(f"Comparing runs from Neo4j: {baseline} vs {afterfix}...")
        store = _get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
        try:
            baseline_data = store.get_run_output(baseline)
            afterfix_data = store.get_run_output(afterfix)
        finally:
            store.close()
        if not baseline_data:
            click.echo(f"Error: run '{baseline}' not found in Neo4j", err=True)
            sys.exit(1)
        if not afterfix_data:
            click.echo(f"Error: run '{afterfix}' not found in Neo4j", err=True)
            sys.exit(1)
    else:
        click.echo(
            "Error: arguments are not existing files and no --neo4j-uri provided",
            err=True,
        )
        sys.exit(1)

    comparison = compare_runs(baseline_data, afterfix_data)

    if output:
        output_path = Path(output)
        output_path.write_text(json.dumps(comparison, indent=2))
        click.echo(f"Comparison written to {output}")
    else:
        click.echo(json.dumps(comparison, indent=2))

    click.echo(f"\n{'=' * 50}")
    click.echo("Comparison Summary:")
    click.echo(f"  Fix Effective: {comparison['conclusion']['fixEffective']}")
    click.echo(f"  Confidence: {comparison['conclusion']['confidence']:.2f}")
    click.echo(
        f"  Resilience Score Change: " f"{comparison['comparison']['resilienceScoreChange']:+.1f}"
    )


@main.command()
@click.argument("namespace")
@click.option("--all", "cleanup_all", is_flag=True, help="Cleanup all resources")
def cleanup(namespace: str, cleanup_all: bool):
    """Cleanup provisioned resources in a namespace.

    NAMESPACE: The Kubernetes namespace to cleanup.
    """
    click.echo(f"Cleaning up resources in namespace: {namespace}")

    provisioner = KubernetesProvisioner(namespace)

    if cleanup_all:
        provisioner.cleanup_namespace()
        click.echo("All resources cleaned up successfully")
    else:
        provisioner.cleanup()
        click.echo("Resources cleaned up successfully")




# ─────────────────────────────────────────────────────────────
# Register extracted command modules
# ─────────────────────────────────────────────────────────────
from chaosprobe.commands.cluster_cmd import cluster  # noqa: E402
from chaosprobe.commands.dashboard_cmd import dashboard  # noqa: E402
from chaosprobe.commands.delete_cmd import delete  # noqa: E402
from chaosprobe.commands.graph_cmd import graph  # noqa: E402
from chaosprobe.commands.init_cmd import init  # noqa: E402
from chaosprobe.commands.placement_cmd import placement  # noqa: E402
from chaosprobe.commands.probe_cmd import probe  # noqa: E402
from chaosprobe.commands.run_cmd import run  # noqa: E402
from chaosprobe.commands.visualize_cmd import ml_export, visualize  # noqa: E402

main.add_command(cluster)
main.add_command(dashboard)
main.add_command(delete)
main.add_command(graph)
main.add_command(init)
main.add_command(placement)
main.add_command(probe)
main.add_command(run)
main.add_command(visualize)
main.add_command(ml_export)


if __name__ == "__main__":
    main()
