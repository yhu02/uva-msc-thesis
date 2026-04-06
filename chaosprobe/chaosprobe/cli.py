"""ChaosProbe CLI - Main entry point for the chaos testing framework."""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

from chaosprobe.config.loader import load_scenario
from chaosprobe.config.topology import parse_topology_from_scenario
from chaosprobe.config.validator import validate_scenario
from chaosprobe.metrics.collector import MetricsCollector
from chaosprobe.orchestrator import portforward as pf
from chaosprobe.orchestrator.preflight import (
    LITMUS_INFRA_DEPLOYMENTS as _LITMUS_INFRA_DEPLOYMENTS,
    extract_experiment_types as _extract_experiment_types,
    extract_target_deployment as _extract_target_deployment,
    wait_for_healthy_deployments as _wait_for_healthy_deployments,
)
from chaosprobe.orchestrator.run_phases import (
    init_graph_store,
    run_preflight_checks,
    write_run_results,
)
from chaosprobe.orchestrator.strategy_runner import RunContext, execute_strategy
from chaosprobe.output.comparison import compare_runs
from chaosprobe.output.generator import OutputGenerator
from chaosprobe.placement.mutator import PlacementMutator
from chaosprobe.placement.strategy import PlacementStrategy
from chaosprobe.provisioner.kubernetes import KubernetesProvisioner
from chaosprobe.provisioner.setup import LitmusSetup


def _print_cluster_recovery_hints(setup: LitmusSetup) -> None:
    """Detect cluster state and print concrete recovery commands."""
    import subprocess as _sp

    steps: list[str] = []

    # Detect libvirt VMs (works in WSL unlike vagrant commands)
    has_virsh = False
    try:
        _sp.run(["which", "virsh"], capture_output=True, check=True)
        has_virsh = True
    except (FileNotFoundError, _sp.CalledProcessError):
        pass

    if has_virsh:
        # Use virsh to inspect VM state — reliable in WSL with libvirt
        try:
            result = _sp.run(
                ["virsh", "list", "--all", "--name"],
                capture_output=True,
                text=True,
            )
            all_vms = [v.strip() for v in result.stdout.strip().split("\n") if v.strip()]

            result_running = _sp.run(
                ["virsh", "list", "--state-running", "--name"],
                capture_output=True,
                text=True,
            )
            running_vms = [
                v.strip() for v in result_running.stdout.strip().split("\n") if v.strip()
            ]

            # Find k8s-related VMs
            k8s_vms = [v for v in all_vms if "k8s" in v]
            stopped_vms = [v for v in k8s_vms if v not in running_vms]

            if not k8s_vms:
                steps.append(
                    "No Kubernetes VMs found. Create a cluster first:\n"
                    "    chaosprobe cluster vagrant init\n"
                    "    chaosprobe cluster vagrant up --provider=libvirt"
                )
            elif stopped_vms:
                start_cmds = "\n".join(f"    virsh start {vm}" for vm in stopped_vms)
                steps.append(
                    f"These VMs are stopped: {', '.join(stopped_vms)}\n"
                    f"Start them:\n{start_cmds}"
                )
                steps.append(
                    "Then wait ~30s for kubelet to come up and verify:\n" "    kubectl cluster-info"
                )
            else:
                # All VMs running but cluster unreachable
                cp_vm = next(
                    (v for v in k8s_vms if "k8s-1" in v or "master" in v or "cp" in v), k8s_vms[0]
                )
                steps.append(
                    "VMs are running but the API server is unreachable. Check kubelet:\n"
                    f"    virsh console {cp_vm}\n"
                    "  or SSH via the VM's IP:\n"
                    f"    virsh domifaddr {cp_vm}\n"
                    f"    ssh <ip> sudo systemctl status kubelet"
                )
        except Exception:
            steps.append(
                "Could not query libvirt VM status. Check manually:\n" "    virsh list --all"
            )
    else:
        # No virsh — fall back to kubectl context check
        try:
            result = _sp.run(
                ["kubectl", "config", "current-context"],
                capture_output=True,
                text=True,
            )
            ctx = result.stdout.strip() if result.returncode == 0 else None
        except FileNotFoundError:
            ctx = None

        if ctx:
            steps.append(
                f"kubectl context '{ctx}' is set but the cluster is unreachable.\n"
                "    Verify the node is up and the API server is listening:\n"
                "    kubectl cluster-info"
            )
        else:
            steps.append(
                "No Kubernetes cluster configured. Set one up:\n"
                "    chaosprobe cluster vagrant init && chaosprobe cluster vagrant up --provider=libvirt"
            )

    click.echo("\nRun these commands to recover:", err=True)
    for i, step in enumerate(steps, 1):
        click.echo(f"  {i}. {step}", err=True)


def ensure_litmus_setup(
    namespace: str,
    experiment_types: list,
    auto_setup: bool = True,
) -> bool:
    """Pre-flight check that all infrastructure is installed.

    Verifies that LitmusChaos, metrics-server, Prometheus, Neo4j, and
    ChaosCenter are available.  Does NOT install anything — run
    'chaosprobe init' first to set up the infrastructure.

    Args:
        namespace: Target namespace for experiments.
        experiment_types: List of experiment type names (e.g. ["pod-delete"]).
        auto_setup: Ignored (kept for backward compatibility).

    Returns:
        True if all infrastructure is ready.
    """
    setup = LitmusSetup(skip_k8s_init=True)
    prereqs = setup.check_prerequisites()

    if not prereqs["kubectl"]:
        click.echo("Error: kubectl not found. Please install kubectl.", err=True)
        return False

    click.echo("Validating cluster...")
    is_valid, message = setup.validate_cluster()
    if not is_valid:
        click.echo(f"Error: {message}", err=True)
        _print_cluster_recovery_hints(setup)
        return False
    click.echo(f"  {message}")

    setup._init_k8s_client()
    prereqs = setup.check_prerequisites()

    if not prereqs["litmus_installed"]:
        click.echo(
            "Error: LitmusChaos not installed. Run 'chaosprobe init -n {0}' first.".format(
                namespace
            ),
            err=True,
        )
        return False

    click.echo("Setting up RBAC for namespace...")
    try:
        setup.setup_rbac(namespace)
        click.echo(f"  RBAC configured for namespace: {namespace}")
    except Exception as e:
        click.echo(f"Error setting up RBAC: {e}", err=True)
        return False

    for exp_type in set(experiment_types):
        click.echo(f"  Installing experiment: {exp_type}")
        if not setup.install_experiment(exp_type, namespace):
            click.echo(f"  WARNING: Failed to install experiment '{exp_type}'", err=True)

    # ── Pre-flight checks: verify infrastructure is available ──
    ok = True

    from concurrent.futures import ThreadPoolExecutor

    def _check_component(check_fn, name):
        return name, check_fn()

    checks = [
        (setup.is_local_path_provisioner_running, "local-path-provisioner"),
        (setup.is_metrics_server_installed, "metrics-server"),
        (setup.is_prometheus_installed, "Prometheus"),
        (setup.is_neo4j_installed, "Neo4j"),
        (setup.is_chaoscenter_installed, "ChaosCenter"),
    ]

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_check_component, fn, name) for fn, name in checks]
        for future in futures:
            name, available = future.result()
            if not available:
                click.echo(f"  {name}: NOT FOUND — run 'chaosprobe init' first", err=True)
                ok = False
            else:
                click.echo(f"  {name}: available")

    if not ok:
        click.echo(
            "\nError: Missing infrastructure. Run 'chaosprobe init -n {0}' to install.".format(
                namespace
            ),
            err=True,
        )
    return ok


@click.group()
@click.version_option()
def main():
    """ChaosProbe - Kubernetes chaos testing framework with AI-consumable output.

    Deploys Kubernetes manifests, runs native LitmusChaos experiments,
    Scenarios are directories containing K8s manifests and ChaosEngine YAML.
    """
    pass


# ---------------------------------------------------------------------------
# Port-forward helpers — thin wrappers around orchestrator.portforward
# ---------------------------------------------------------------------------

def _pf_ensure(svc: str, ns: str, ports: list[str], host: str, port: int) -> bool:
    return pf.ensure(svc, ns, ports, host, port)

def _pf_monitor_start():
    pf.monitor_start()

def _pf_monitor_stop():
    pf.monitor_stop()

def _pf_cleanup():
    pf.cleanup()


@main.command()
@click.option(
    "--namespace",
    "-n",
    default="chaosprobe-test",
    help="Namespace for chaos experiments",
)
@click.option("--skip-litmus", is_flag=True, help="Skip LitmusChaos installation")
@click.option(
    "--skip-dashboard",
    is_flag=True,
    help="Skip ChaosCenter dashboard installation",
)
def init(namespace: str, skip_litmus: bool, skip_dashboard: bool):
    """Initialize ChaosProbe and install all infrastructure on existing cluster.

    This command sets up all prerequisites for running chaos experiments:
    - Installs Helm and LitmusChaos automatically
    - Creates RBAC configuration
    - Installs metrics-server, Prometheus, and Neo4j
    - Installs the ChaosCenter dashboard by default (disable with --skip-dashboard)
    - Registers the target namespace as ChaosCenter infrastructure

    Requires an existing Kubernetes cluster. Options:
    - Use 'chaosprobe cluster vagrant init/up/deploy' for local development
    - Use 'chaosprobe cluster create' for bare metal/cloud VMs with Kubespray
    """
    click.echo("Initializing ChaosProbe...")

    setup = LitmusSetup(skip_k8s_init=True)
    prereqs = setup.check_prerequisites()

    click.echo("\nChecking prerequisites:")
    click.echo(f"  kubectl: {'OK' if prereqs['kubectl'] else 'MISSING'}")
    click.echo(f"  helm: {'OK' if prereqs['helm'] else 'MISSING'}")
    click.echo(f"  git: {'OK' if prereqs['git'] else 'MISSING'}")
    click.echo(f"  ssh: {'OK' if prereqs['ssh'] else 'MISSING'}")
    click.echo(f"  ansible: {'OK' if prereqs['ansible'] else 'Not installed (optional)'}")
    click.echo(f"  Cluster access: {'OK' if prereqs['cluster_access'] else 'No cluster'}")
    click.echo(f"  LitmusChaos: {'Installed' if prereqs['litmus_installed'] else 'Not installed'}")

    if not prereqs["kubectl"]:
        click.echo("\nError: kubectl is required. Please install it first.", err=True)
        sys.exit(1)

    click.echo("\nValidating cluster...")
    is_valid, message = setup.validate_cluster()
    if not is_valid:
        click.echo(f"  Error: {message}", err=True)
        click.echo("\nNo cluster configured. Options:")
        click.echo(
            "  1. Use 'chaosprobe cluster vagrant up' to start a local libvirt/Vagrant cluster"
        )
        click.echo("  2. Use 'chaosprobe cluster create' to deploy with Kubespray")
        click.echo("  3. Configure kubectl to connect to an existing cluster")
        sys.exit(1)
    click.echo(f"  {message}")

    setup._init_k8s_client()
    prereqs = setup.check_prerequisites()

    # Ensure local-path-provisioner is running (needed for PVCs)
    click.echo("\nEnsuring local-path-provisioner...")
    if setup.is_local_path_provisioner_running():
        click.echo("  local-path-provisioner: already running")
    else:
        click.echo("  local-path-provisioner not found, installing...")
        if setup.ensure_local_path_provisioner():
            click.echo("  local-path-provisioner: running")
        else:
            click.echo("  WARNING: local-path-provisioner may not be ready yet", err=True)

    if not skip_litmus and not prereqs["litmus_installed"]:
        if not prereqs["helm"]:
            click.echo("\nHelm not found. Installing automatically...")
            try:
                setup.ensure_helm()
                click.echo("  Helm installed successfully")
            except Exception as e:
                click.echo(f"  Error installing helm: {e}", err=True)
                sys.exit(1)

        click.echo("\nInstalling LitmusChaos...")
        try:
            setup.install_litmus(wait=True)
            click.echo("  LitmusChaos installed successfully")
        except Exception as e:
            click.echo(f"  Error: {e}", err=True)
            sys.exit(1)

    click.echo(f"\nSetting up RBAC for namespace: {namespace}")
    try:
        setup.setup_rbac(namespace)
        click.echo("  RBAC configured successfully")
    except Exception as e:
        click.echo(f"  Error: {e}", err=True)
        sys.exit(1)

    # ── Install infrastructure components (parallel) ──

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from kubernetes.client import ApiException

    def _deployment_has_pvc(name: str, ns: str) -> bool:
        """Check if a deployment has any PVC volume."""
        try:
            dep = setup.apps_api.read_namespaced_deployment(name, ns)
            volumes = dep.spec.template.spec.volumes or []
            return any(v.persistent_volume_claim is not None for v in volumes)
        except Exception:
            return True  # can't check — assume OK

    def _install_metrics_server():
        if setup.is_metrics_server_installed():
            # Verify --kubelet-insecure-tls is present
            try:
                dep = setup.apps_api.read_namespaced_deployment(
                    "metrics-server", "kube-system"
                )
                containers = dep.spec.template.spec.containers or []
                args = containers[0].args or [] if containers else []
                if "--kubelet-insecure-tls" not in args:
                    setup.install_metrics_server(wait=True)
                    return "metrics-server", "repaired (added --kubelet-insecure-tls)"
            except Exception:
                pass
            return "metrics-server", "already installed"
        if setup.install_metrics_server(wait=True):
            return "metrics-server", "installed"
        return "metrics-server", "installed but not yet ready"

    def _install_prometheus():
        if setup.is_prometheus_installed():
            # Verify PVC is attached
            if not _deployment_has_pvc("prometheus-server", "monitoring"):
                setup.install_prometheus(wait=True)
                return "Prometheus", "repaired (added persistent storage)"
            return "Prometheus", "already installed"
        if setup.install_prometheus(wait=True):
            return "Prometheus", "installed"
        return "Prometheus", "installed but not yet ready"

    def _install_neo4j():
        if setup.is_neo4j_installed():
            # Verify PVC is attached
            if not _deployment_has_pvc("neo4j", "neo4j"):
                setup.install_neo4j(wait=True)
                return "Neo4j", "repaired (added persistent storage)"
            return "Neo4j", "already installed"
        if setup.install_neo4j(wait=True):
            return "Neo4j", "installed"
        return "Neo4j", "installed but not yet ready"

    click.echo("\nInstalling infrastructure components (parallel)...")
    install_errors = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_install_metrics_server): "metrics-server",
            executor.submit(_install_prometheus): "Prometheus",
            executor.submit(_install_neo4j): "Neo4j",
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                name, status = future.result()
                click.echo(f"  {name}: {status}")
            except Exception as e:
                click.echo(f"  WARNING: Failed to install {label}: {e}", err=True)
                install_errors.append(label)

    click.echo("\nChaosProbe initialized successfully!")

    if not skip_dashboard:
        if not setup.is_chaoscenter_installed():
            click.echo("\nInstalling ChaosCenter dashboard...")
            try:
                ok = setup.install_chaoscenter(wait=True)
                if ok:
                    click.echo("  ChaosCenter installed successfully!")
                    url = setup.get_dashboard_url()
                    if url:
                        click.echo(f"  Dashboard URL: {url}")
                    click.echo(
                        f"  Default credentials: "
                        f"username={setup.CHAOSCENTER_DEFAULT_USER} "
                        f"password={setup.CHAOSCENTER_DEFAULT_PASS}"
                    )
                    click.echo(f"\n  Connecting namespace '{namespace}' to ChaosCenter...")
                    try:
                        _cc_auth_svc = LitmusSetup.CHAOSCENTER_AUTH_SVC
                        _cc_auth_port = LitmusSetup.CHAOSCENTER_AUTH_PORT
                        _cc_server_svc = LitmusSetup.CHAOSCENTER_SERVER_SVC
                        _cc_server_port = LitmusSetup.CHAOSCENTER_SERVER_PORT
                        if not _pf_ensure(
                            _cc_auth_svc, "litmus",
                            [f"{_cc_auth_port}:{_cc_auth_port}"],
                            "localhost", _cc_auth_port,
                        ):
                            raise RuntimeError(
                                f"Port-forward to auth server (:{_cc_auth_port}) not reachable"
                            )
                        if not _pf_ensure(
                            _cc_server_svc, "litmus",
                            [f"{_cc_server_port}:{_cc_server_port}"],
                            "localhost", _cc_server_port,
                        ):
                            raise RuntimeError(
                                f"Port-forward to GraphQL server (:{_cc_server_port}) not reachable"
                            )
                        setup.ensure_chaoscenter_configured(
                            namespace=namespace,
                            base_host="http://localhost",
                        )
                        click.echo("  Infrastructure registered successfully!")
                    except Exception as e:
                        click.echo(f"  Warning: Could not register infrastructure: {e}")
                    finally:
                        _pf_cleanup()
                else:
                    click.echo("  ChaosCenter installation timed out.", err=True)
            except Exception as e:
                click.echo(f"  Error installing ChaosCenter: {e}", err=True)
        else:
            click.echo("\nChaosCenter is already installed.")
            url = setup.get_dashboard_url()
            if url:
                click.echo(f"  Dashboard URL: {url}")

    click.echo("\nYou can now run scenarios with:")
    click.echo("  chaosprobe run <scenario-dir> --output-dir results/")


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
            click.echo(
                "\nNo cluster configured. Use 'chaosprobe cluster create' or configure kubectl."
            )
        else:
            click.echo("\nRun 'chaosprobe init' to complete setup.")


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


# ─────────────────────────────────────────────────────────────
# Neo4j option decorators (imported from commands.shared)
# ─────────────────────────────────────────────────────────────

from chaosprobe.commands.shared import (
    get_graph_store as _get_graph_store,
    neo4j_password_option as _neo4j_password_option,
    neo4j_uri_option as _neo4j_uri_option,
    neo4j_user_option as _neo4j_user_option,
)


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


@main.command()
@click.option(
    "--namespace",
    "-n",
    default="online-boutique",
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
    - Prometheus (monitoring namespace)
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
    _pf_cleanup()

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
            executor.submit(_delete_namespace, "monitoring", "Prometheus (monitoring)"): "monitoring",
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


# ─────────────────────────────────────────────────────────────
# run — automated full experiment matrix
# ─────────────────────────────────────────────────────────────


@main.command()
@click.option(
    "--namespace",
    "-n",
    default="online-boutique",
    help="Namespace containing the application",
)
@click.option(
    "--output-dir",
    "-o",
    default="results",
    help="Base directory for results (a timestamped subdirectory is created)",
)
@click.option(
    "--strategies",
    "-s",
    default="baseline,colocate,spread,antagonistic,random",
    help="Comma-separated strategies to test (default: all)",
)
@click.option(
    "--timeout",
    "-t",
    default=300,
    type=int,
    help="Timeout per experiment in seconds",
)
@click.option(
    "--seed",
    default=42,
    type=int,
    help="Seed for the random strategy",
)
@click.option(
    "--settle-time",
    default=30,
    type=int,
    help="Seconds to wait after placement before running experiment",
)
@click.option(
    "--experiment",
    "-e",
    default="scenarios/online-boutique/placement-experiment.yaml",
    help="Path to the placement experiment YAML file",
)
@click.option(
    "--iterations",
    "-i",
    default=1,
    type=int,
    help="Number of iterations per strategy (default: 1)",
)
@click.option(
    "--provision",
    is_flag=True,
    help="Provision a fresh cluster from scenario cluster config before running",
)
@click.option(
    "--load-profile",
    type=click.Choice(["steady", "ramp", "spike"]),
    default="steady",
    help="Locust load profile during each experiment (default: steady)",
)
@click.option(
    "--locustfile",
    type=click.Path(exists=True),
    default=None,
    help="Custom Locust file for load generation",
)
@click.option(
    "--target-url",
    default=None,
    help="Target URL for load generation (default: auto port-forward to frontend service)",
)
@click.option(
    "--visualize/--no-visualize",
    "do_visualize",
    default=True,
    show_default=True,
    help="Generate visualization charts after experiments complete",
)
@click.option(
    "--measure-latency/--no-measure-latency",
    "measure_latency",
    default=True,
    show_default=True,
    help="Measure inter-service latency during each experiment",
)
@click.option(
    "--measure-redis/--no-measure-redis",
    "measure_redis",
    default=True,
    show_default=True,
    help="Measure Redis throughput during each experiment",
)
@click.option(
    "--measure-disk/--no-measure-disk",
    "measure_disk",
    default=True,
    show_default=True,
    help="Measure disk I/O throughput during each experiment",
)
@click.option(
    "--measure-resources/--no-measure-resources",
    "measure_resources",
    default=True,
    show_default=True,
    help="Measure node/pod resource utilization during each experiment",
)
@click.option(
    "--collect-logs/--no-collect-logs",
    "collect_logs",
    default=True,
    show_default=True,
    help="Collect container logs from target deployment after each experiment",
)
@click.option(
    "--measure-prometheus/--no-measure-prometheus",
    "measure_prometheus",
    default=True,
    show_default=True,
    help="Query Prometheus for cluster metrics during each experiment",
)
@click.option(
    "--prometheus-url",
    multiple=True,
    help="Prometheus server URL(s); repeat for multiple instances (auto-discovered if omitted)",
)
@click.option(
    "--baseline-duration",
    type=int,
    default=0,
    help="Seconds to collect steady-state 'normal' metrics before chaos (default: 0 = use settle time)",
)
@click.option(
    "--neo4j-uri",
    default="bolt://localhost:7687",
    envvar="NEO4J_URI",
    help="Neo4j connection URI (default: bolt://localhost:7687). Enables graph storage.",
)
@click.option(
    "--neo4j-user",
    default="neo4j",
    envvar="NEO4J_USER",
    help="Neo4j username (default: neo4j)",
)
@click.option(
    "--neo4j-password",
    default="chaosprobe",
    envvar="NEO4J_PASSWORD",
    help="Neo4j password (default: chaosprobe)",
)
def run(
    namespace: str,
    output_dir: Optional[str],
    strategies: str,
    timeout: int,
    seed: int,
    settle_time: int,
    experiment: str,
    iterations: int,
    provision: bool,
    load_profile: Optional[str],
    locustfile: Optional[str],
    target_url: Optional[str],
    do_visualize: bool,
    measure_latency: bool,
    measure_redis: bool,
    measure_disk: bool,
    measure_resources: bool,
    collect_logs: bool,
    measure_prometheus: bool,
    prometheus_url: Tuple[str, ...],
    baseline_duration: int,
    neo4j_uri: Optional[str],
    neo4j_user: str,
    neo4j_password: str,
):
    """Run placement experiments automatically.

    Iterates through placement strategies (baseline, colocate, spread,
    antagonistic, random), applies each placement, runs the shared
    experiment, collects results (including pod recovery metrics), and
    saves everything to a timestamped results directory.

    \b
    Example:
      chaosprobe run -n online-boutique
      chaosprobe run -n online-boutique -s colocate,spread
      chaosprobe run -n online-boutique -o results/my-run
      chaosprobe run -n online-boutique -i 3  # 3 iterations per strategy
    """
    strategy_list = [s.strip() for s in strategies.split(",")]
    valid_strategies = {"baseline"} | {s.value for s in PlacementStrategy}
    for s in strategy_list:
        if s not in valid_strategies:
            click.echo(
                f"Error: Unknown strategy '{s}'. Valid: {', '.join(sorted(valid_strategies))}",
                err=True,
            )
            sys.exit(1)

    if iterations < 1:
        click.echo("Error: --iterations must be >= 1", err=True)
        sys.exit(1)

    # Create output directory
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    results_dir = Path(output_dir) / ts
    results_dir.mkdir(parents=True, exist_ok=True)

    # Load the shared experiment file once
    experiment_file = Path(experiment)
    if not experiment_file.exists():
        # Try package-relative path
        pkg_path = Path(__file__).resolve().parent.parent / experiment
        if pkg_path.exists():
            experiment_file = pkg_path
    try:
        shared_scenario = load_scenario(str(experiment_file))
        validate_scenario(shared_scenario)
        shared_scenario["namespace"] = namespace
    except Exception as e:
        click.echo(f"Error loading experiment: {e}", err=True)
        sys.exit(1)

    # Discover service topology from deployment manifests
    service_routes = parse_topology_from_scenario(shared_scenario) or None
    if service_routes:
        click.echo(
            f"  Topology:   {len(service_routes)} service dependencies"
            " discovered from manifests"
        )
    else:
        click.echo("  Topology:   no deploy/ directory found; service dependency graph empty")

    # Auto-build Rust cmdProbes if probes/ directory exists
    if shared_scenario.get("probes"):
        click.echo(f"\n  Found {len(shared_scenario['probes'])} Rust probe(s), building...")
        try:
            from chaosprobe.probes.builder import RustProbeBuilder, patch_probe_images

            builder = RustProbeBuilder(registry="chaosprobe", load_kind=True)
            built_images = builder.build_all(shared_scenario["path"])
            if built_images:
                n = patch_probe_images(shared_scenario["experiments"], built_images)
                click.echo(f"  Built and patched {n} cmdProbe image(s)")
        except Exception as e:
            click.echo(f"Warning: Rust probe build failed: {e}", err=True)
            click.echo("  Continuing without auto-built probes...", err=True)

    # Optionally provision cluster from scenario's cluster config
    if provision:
        cluster_config = shared_scenario.get("cluster")
        if cluster_config:
            click.echo("\nProvisioning cluster from scenario config...")
            setup = LitmusSetup(skip_k8s_init=True)
            try:
                vagrant_dir = setup.provision_from_cluster_config(cluster_config)
                click.echo(f"  Cluster provisioned at {vagrant_dir}")
                click.echo("  Deploying Kubernetes on Vagrant VMs...")
                setup.vagrant_deploy_cluster(vagrant_dir)
                setup.vagrant_fetch_kubeconfig(vagrant_dir)
                click.echo("  Cluster ready.")
            except Exception as e:
                click.echo(f"Error provisioning cluster: {e}", err=True)
                sys.exit(1)
        else:
            click.echo("Warning: --provision specified but no cluster config in scenario", err=True)

    # Ensure LitmusChaos is ready once (all placement experiments use the same types)
    experiment_types = _extract_experiment_types(shared_scenario)
    if not ensure_litmus_setup(namespace, experiment_types):
        click.echo("Error: LitmusChaos setup failed", err=True)
        sys.exit(1)

    # Create reusable instances
    mutator = PlacementMutator(namespace)
    metrics_collector = MetricsCollector(namespace)

    click.echo("=" * 60)
    click.echo("ChaosProbe — Automated Placement Experiment Runner")
    click.echo("=" * 60)
    click.echo(f"  Namespace:  {namespace}")
    click.echo(f"  Experiment: {experiment_file}")
    click.echo(f"  Strategies: {', '.join(strategy_list)}")
    click.echo(f"  Iterations: {iterations}")
    click.echo(f"  Output:     {results_dir}")
    click.echo(f"  Timeout:    {timeout}s per experiment")
    click.echo(f"  Settle:     {settle_time}s between placement and experiment")
    if measure_latency:
        click.echo("  Latency:    Measuring inter-service latency during experiments")
    if measure_redis:
        click.echo("  Redis:      Measuring Redis throughput during experiments")
    if measure_disk:
        click.echo("  Disk:       Measuring disk I/O throughput during experiments")
    if measure_resources:
        click.echo("  Resources:  Measuring node/pod resource utilization during experiments")
    if measure_prometheus:
        prom_display = ", ".join(prometheus_url) if prometheus_url else "(auto-discover)"
        click.echo(f"  Prometheus: Querying cluster Prometheus at {prom_display}")
    if collect_logs:
        click.echo("  Logs:       Collecting container logs from target deployment")
    if baseline_duration > 0:
        click.echo(f"  Baseline:   {baseline_duration}s steady-state collection before chaos")
    click.echo("")

    # ── Pre-flight checks ──────────────────────────────────────
    click.echo("Pre-flight checks...")
    preflight = run_preflight_checks(
        namespace,
        measure_prometheus=measure_prometheus,
        prometheus_url=prometheus_url,
        neo4j_uri=neo4j_uri,
        load_profile=load_profile,
        target_url=target_url,
        timeout=timeout,
    )
    core_api = preflight["core_api"]
    chaoscenter_config = preflight["chaoscenter_config"]
    target_url = preflight["target_url"]
    frontend_pf_port = preflight["frontend_pf_port"]

    from kubernetes import client as k8s_client_mod

    click.echo("")

    # Start background monitor that auto-restarts dead port-forwards
    _pf_monitor_start()

    # Extract target deployment from experiment spec for recovery metrics
    target_deployment = _extract_target_deployment(shared_scenario)

    overall_results: Dict[str, Any] = {
        "runId": f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "namespace": namespace,
        "iterations": iterations,
        "strategies": {},
    }

    total = len(strategy_list)
    passed = 0
    failed = 0

    # Neo4j graph store — primary data store
    graph_store = None
    if neo4j_uri:
        try:
            graph_store = init_graph_store(
                neo4j_uri, neo4j_user, neo4j_password,
                namespace, service_routes=service_routes,
            )
        except ImportError:
            click.echo(
                "Error: Neo4j driver not installed (install with: uv pip install chaosprobe[graph])",
                err=True,
            )
            sys.exit(1)
        except Exception as e:
            click.echo(f"Error: Neo4j connection failed ({e})", err=True)
            click.echo(
                "Neo4j is required as the primary data store. Check connection and retry.", err=True
            )
            sys.exit(1)

    # Fresh-start: clear any stale placement constraints, then rollout
    # restart all app deployments so every run begins with clean pods.
    click.echo("  Clearing stale placement constraints...")
    mutator.clear_placement(wait=False)
    click.echo("  Restarting app deployments for a clean baseline...")
    try:
        _apps_api = k8s_client_mod.AppsV1Api()
        _all_deps = _apps_api.list_namespaced_deployment(namespace)
        _restart_names = [
            d.metadata.name
            for d in _all_deps.items
            if d.metadata.name not in _LITMUS_INFRA_DEPLOYMENTS
        ]
        _now = datetime.now(timezone.utc).isoformat()
        for _dep_name in _restart_names:
            _apps_api.patch_namespaced_deployment(
                name=_dep_name,
                namespace=namespace,
                body={
                    "spec": {
                        "template": {
                            "metadata": {
                                "annotations": {
                                    "chaosprobe.io/restartedAt": _now,
                                }
                            }
                        }
                    }
                },
            )
        click.echo(f"    Triggered rollout restart for {len(_restart_names)} deployment(s)")
        _wait_for_healthy_deployments(namespace, timeout=180)
        click.echo("    All deployments ready (fresh pods)")
    except Exception as e:
        click.echo(f"    WARNING: rollout restart failed ({e})", err=True)

    # Build shared context for strategy execution
    run_ctx = RunContext(
        namespace=namespace,
        timeout=timeout,
        seed=seed,
        settle_time=settle_time,
        iterations=iterations,
        baseline_duration=baseline_duration,
        measure_latency=measure_latency,
        measure_redis=measure_redis,
        measure_disk=measure_disk,
        measure_resources=measure_resources,
        measure_prometheus=measure_prometheus,
        prometheus_url=prometheus_url,
        collect_logs=collect_logs,
        load_profile=load_profile,
        locustfile=locustfile,
        target_url=target_url,
        neo4j_uri=neo4j_uri,
        neo4j_user=neo4j_user,
        neo4j_password=neo4j_password,
        shared_scenario=shared_scenario,
        service_routes=service_routes,
        target_deployment=target_deployment,
        core_api=core_api,
        chaoscenter_config=chaoscenter_config,
        frontend_pf_port=frontend_pf_port,
        metrics_collector=metrics_collector,
        mutator=mutator,
        graph_store=graph_store,
        ts=ts,
    )

    for idx, strategy_name in enumerate(strategy_list, 1):
        strategy_result, strategy_passed = execute_strategy(
            run_ctx, strategy_name, idx, total,
        )
        overall_results["strategies"][strategy_name] = strategy_result
        if strategy_result["status"] == "error":
            failed += 1
        elif strategy_passed:
            passed += 1
        else:
            failed += 1

    # ── Final cleanup: clear placement ──
    click.echo(f"\n{'─' * 60}")
    click.echo("Cleanup: Clearing placement constraints...")
    try:
        mutator.clear_placement(wait=True)
        click.echo("  Placement cleared.")
    except Exception as e:
        click.echo(f"  Warning: cleanup failed: {e}")

    # ── Write overall summary ──
    overall_results["summary"] = {
        "totalStrategies": total,
        "passed": passed,
        "failed": failed,
        "completedAt": datetime.now(timezone.utc).isoformat(),
    }
    overall_results["iterations"] = iterations

    write_run_results(
        overall_results,
        results_dir,
        passed=passed,
        failed=failed,
        total=total,
        ts=ts,
        do_visualize=do_visualize,
        graph_store=graph_store,
    )


# ─────────────────────────────────────────────────────────────
# Register extracted command modules
# ─────────────────────────────────────────────────────────────
from chaosprobe.commands.cluster_cmd import cluster  # noqa: E402
from chaosprobe.commands.dashboard_cmd import dashboard  # noqa: E402
from chaosprobe.commands.graph_cmd import graph  # noqa: E402
from chaosprobe.commands.placement_cmd import placement  # noqa: E402
from chaosprobe.commands.probe_cmd import probe  # noqa: E402
from chaosprobe.commands.visualize_cmd import ml_export, visualize  # noqa: E402

main.add_command(cluster)
main.add_command(dashboard)
main.add_command(graph)
main.add_command(placement)
main.add_command(probe)
main.add_command(visualize)
main.add_command(ml_export)


if __name__ == "__main__":
    main()
