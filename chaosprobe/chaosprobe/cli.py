"""ChaosProbe CLI - Main entry point for the chaos testing framework."""

import copy
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click
import yaml

from chaosprobe.config.loader import load_scenario
from chaosprobe.config.validator import validate_scenario
from chaosprobe.provisioner.kubernetes import KubernetesProvisioner
from chaosprobe.provisioner.setup import LitmusSetup
from chaosprobe.chaos.runner import ChaosRunner
from chaosprobe.collector.result_collector import ResultCollector
from chaosprobe.output.generator import OutputGenerator
from chaosprobe.output.comparison import compare_runs
from chaosprobe.placement.strategy import PlacementStrategy
from chaosprobe.placement.mutator import PlacementMutator
from chaosprobe.metrics.collector import MetricsCollector
from chaosprobe.metrics.recovery import RecoveryWatcher
from chaosprobe.metrics.latency import ContinuousLatencyProber
from chaosprobe.metrics.throughput import ContinuousRedisProber, ContinuousDiskProber
from chaosprobe.metrics.resources import ContinuousResourceProber
from chaosprobe.metrics.prometheus import ContinuousPrometheusProber
from chaosprobe.loadgen.runner import LocustRunner, LoadProfile


def _get_store(db_path: Optional[str] = None):
    """Get a SQLiteStore instance (uses default path if none given)."""
    from chaosprobe.storage.sqlite import SQLiteStore
    return SQLiteStore(db_path=db_path)


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
                capture_output=True, text=True,
            )
            all_vms = [v.strip() for v in result.stdout.strip().split("\n") if v.strip()]

            result_running = _sp.run(
                ["virsh", "list", "--state-running", "--name"],
                capture_output=True, text=True,
            )
            running_vms = [v.strip() for v in result_running.stdout.strip().split("\n") if v.strip()]

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
                    "Then wait ~30s for kubelet to come up and verify:\n"
                    "    kubectl cluster-info"
                )
            else:
                # All VMs running but cluster unreachable
                cp_vm = next((v for v in k8s_vms if "k8s-1" in v or "master" in v or "cp" in v), k8s_vms[0])
                steps.append(
                    "VMs are running but the API server is unreachable. Check kubelet:\n"
                    f"    virsh console {cp_vm}\n"
                    "  or SSH via the VM's IP:\n"
                    f"    virsh domifaddr {cp_vm}\n"
                    f"    ssh <ip> sudo systemctl status kubelet"
                )
        except Exception:
            steps.append(
                "Could not query libvirt VM status. Check manually:\n"
                "    virsh list --all"
            )
    else:
        # No virsh — fall back to kubectl context check
        try:
            result = _sp.run(
                ["kubectl", "config", "current-context"],
                capture_output=True, text=True,
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
    """Ensure LitmusChaos is installed and configured.

    Args:
        namespace: Target namespace for experiments.
        experiment_types: List of experiment type names (e.g. ["pod-delete"]).
        auto_setup: Whether to automatically install if missing.

    Returns:
        True if setup is ready.
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
        if not auto_setup:
            click.echo(
                "Error: LitmusChaos not installed. Run 'chaosprobe init' first.",
                err=True,
            )
            return False

        if not prereqs["helm"]:
            click.echo("Helm not found. Installing automatically...")
            try:
                setup.ensure_helm()
                click.echo("  Helm installed successfully")
            except Exception as e:
                click.echo(f"Error installing helm: {e}", err=True)
                return False

        click.echo("LitmusChaos not found. Installing automatically...")
        try:
            setup.install_litmus(wait=True)
            click.echo("  LitmusChaos installed successfully")
        except Exception as e:
            click.echo(f"Error installing LitmusChaos: {e}", err=True)
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
            click.echo(
                f"  WARNING: Failed to install experiment '{exp_type}'", err=True
            )

    # Ensure metrics-server is installed (needed for resource probing)
    if not setup.is_metrics_server_installed():
        click.echo("metrics-server not found. Installing automatically...")
        try:
            if setup.install_metrics_server(wait=True):
                click.echo("  metrics-server installed successfully")
            else:
                click.echo("  WARNING: metrics-server installed but not yet ready", err=True)
        except Exception as e:
            click.echo(f"  WARNING: Failed to install metrics-server: {e}", err=True)
    else:
        click.echo("  metrics-server: available")

    # Ensure Prometheus is installed (needed for cluster metrics)
    if not setup.is_prometheus_installed():
        if not prereqs.get("helm"):
            click.echo("  Prometheus: skipped (helm not available)")
        else:
            click.echo("Prometheus not found. Installing automatically...")
            try:
                if setup.install_prometheus(wait=True):
                    click.echo("  Prometheus installed successfully")
                else:
                    click.echo("  WARNING: Prometheus installed but not yet ready", err=True)
            except Exception as e:
                click.echo(f"  WARNING: Failed to install Prometheus: {e}", err=True)
    else:
        click.echo("  Prometheus: available")

    # Ensure Neo4j is installed (needed for graph storage)
    if not setup.is_neo4j_installed():
        if not prereqs.get("helm"):
            click.echo("  Neo4j: skipped (helm not available)")
        else:
            click.echo("Neo4j not found. Installing automatically...")
            try:
                if setup.install_neo4j(wait=True):
                    click.echo("  Neo4j installed successfully")
                else:
                    click.echo("  WARNING: Neo4j installed but not yet ready", err=True)
            except Exception as e:
                click.echo(f"  WARNING: Failed to install Neo4j: {e}", err=True)
    else:
        click.echo("  Neo4j: available")

    return True


def _extract_experiment_types(scenario: dict) -> list:
    """Extract experiment type names from a loaded scenario."""
    types = []
    for exp in scenario.get("experiments", []):
        spec = exp.get("spec", {})
        for experiment in spec.get("spec", {}).get("experiments", []):
            name = experiment.get("name", "")
            if name:
                types.append(name)
    return types


@click.group()
@click.version_option()
def main():
    """ChaosProbe - Kubernetes chaos testing framework with AI-consumable output.

    Deploys Kubernetes manifests, runs native LitmusChaos experiments,
    Scenarios are directories containing K8s manifests and ChaosEngine YAML.
    """
    pass


@main.command()
@click.option(
    "--namespace",
    "-n",
    default="chaosprobe-test",
    help="Namespace for chaos experiments",
)
@click.option("--skip-litmus", is_flag=True, help="Skip LitmusChaos installation")
def init(namespace: str, skip_litmus: bool):
    """Initialize ChaosProbe and install LitmusChaos on existing cluster.

    This command sets up all prerequisites for running chaos experiments:
    - Installs Helm and LitmusChaos automatically
    - Creates RBAC configuration

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
    click.echo(
        f"  ansible: {'OK' if prereqs['ansible'] else 'Not installed (optional)'}"
    )
    click.echo(
        f"  Cluster access: {'OK' if prereqs['cluster_access'] else 'No cluster'}"
    )
    click.echo(
        f"  LitmusChaos: {'Installed' if prereqs['litmus_installed'] else 'Not installed'}"
    )

    if not prereqs["kubectl"]:
        click.echo(
            "\nError: kubectl is required. Please install it first.", err=True
        )
        sys.exit(1)

    click.echo("\nValidating cluster...")
    is_valid, message = setup.validate_cluster()
    if not is_valid:
        click.echo(f"  Error: {message}", err=True)
        click.echo("\nNo cluster configured. Options:")
        click.echo("  1. Use 'chaosprobe cluster vagrant up' to start a local libvirt/Vagrant cluster")
        click.echo("  2. Use 'chaosprobe cluster create' to deploy with Kubespray")
        click.echo("  3. Configure kubectl to connect to an existing cluster")
        sys.exit(1)
    click.echo(f"  {message}")

    setup._init_k8s_client()
    prereqs = setup.check_prerequisites()

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

    click.echo("\nChaosProbe initialized successfully!")
    click.echo("\nYou can now run scenarios with:")
    click.echo("  chaosprobe run <scenario-dir> --output results.json")


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
    click.echo(
        f"  Cluster access: {'OK' if prereqs['cluster_access'] else 'No cluster'}"
    )
    if prereqs["cluster_access"]:
        click.echo(f"    Context: {prereqs['cluster_context']}")
        click.echo(f"    Server: {prereqs['cluster_server']}")
    click.echo(
        f"  LitmusChaos installed: {'Yes' if prereqs['litmus_installed'] else 'No'}"
    )
    click.echo(
        f"  LitmusChaos ready: {'Yes' if prereqs['litmus_ready'] else 'No'}"
    )

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
# Cluster management commands (Kubespray) — unchanged
# ─────────────────────────────────────────────────────────────


@main.group()
def cluster():
    """Manage Kubernetes clusters with Kubespray."""
    pass


@cluster.command("create")
@click.option(
    "--inventory",
    "-i",
    type=click.Path(exists=True),
    help="Path to existing inventory file (hosts.yaml)",
)
@click.option(
    "--hosts-file",
    "-f",
    type=click.Path(exists=True),
    help="Path to hosts definition file (JSON/YAML)",
)
@click.option("--name", "-n", default="chaosprobe", help="Cluster name")
@click.option(
    "--become-pass",
    envvar="ANSIBLE_BECOME_PASS",
    help="Sudo password for ansible become",
)
def cluster_create(
    inventory: Optional[str],
    hosts_file: Optional[str],
    name: str,
    become_pass: Optional[str],
):
    """Create a Kubernetes cluster using Kubespray."""
    setup = LitmusSetup(skip_k8s_init=True)
    prereqs = setup.check_prerequisites()

    if not prereqs["git"]:
        click.echo(
            "Error: git is required for Kubespray. Please install it.", err=True
        )
        sys.exit(1)

    if not prereqs["python_venv"]:
        click.echo(
            "Error: python3-venv is required. Install with: apt install python3-venv",
            err=True,
        )
        sys.exit(1)

    if inventory:
        inventory_dir = Path(inventory).parent
        click.echo(f"Using existing inventory: {inventory}")
    elif hosts_file:
        click.echo(f"Loading hosts from: {hosts_file}")
        with open(hosts_file) as f:
            if hosts_file.endswith(".json"):
                hosts_data = json.load(f)
            else:
                hosts_data = yaml.safe_load(f)

        hosts = hosts_data.get("hosts", hosts_data)
        if not hosts:
            click.echo("Error: No hosts defined in hosts file", err=True)
            sys.exit(1)

        click.echo(f"Found {len(hosts)} hosts:")
        for host in hosts:
            roles = host.get("roles", ["worker"])
            click.echo(f"  {host['name']} ({host['ip']}) - {', '.join(roles)}")

        inventory_dir = setup.generate_inventory(hosts, cluster_name=name)
    else:
        click.echo("Error: Provide --inventory or --hosts-file", err=True)
        sys.exit(1)

    click.echo("\nDeploying Kubernetes cluster...")
    click.echo("This will take 15-30 minutes.\n")

    try:
        setup.deploy_cluster(inventory_dir, become_pass=become_pass)
    except Exception as e:
        click.echo(f"\nError deploying cluster: {e}", err=True)
        sys.exit(1)

    click.echo("\nCluster deployed successfully!")
    click.echo("\nTo get kubeconfig, run:")
    click.echo("  chaosprobe cluster kubeconfig --host <control-plane-ip>")


@cluster.command("destroy")
@click.option(
    "--inventory",
    "-i",
    type=click.Path(exists=True),
    required=True,
    help="Path to inventory directory",
)
@click.option(
    "--become-pass",
    envvar="ANSIBLE_BECOME_PASS",
    help="Sudo password for ansible become",
)
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
def cluster_destroy(
    inventory: str, become_pass: Optional[str], force: bool
):
    """Destroy a Kubernetes cluster using Kubespray reset."""
    inventory_dir = Path(inventory)
    if not (inventory_dir / "hosts.yaml").exists():
        click.echo(f"Error: No hosts.yaml found in {inventory_dir}", err=True)
        sys.exit(1)

    if not force:
        click.confirm(
            "This will destroy the Kubernetes cluster on all nodes. Continue?",
            abort=True,
        )

    setup = LitmusSetup(skip_k8s_init=True)

    try:
        setup.destroy_cluster(inventory_dir, become_pass=become_pass)
    except Exception as e:
        click.echo(f"Error destroying cluster: {e}", err=True)
        sys.exit(1)

    click.echo("Cluster destroyed successfully.")


@cluster.command("kubeconfig")
@click.option("--host", "-h", required=True, help="Control plane host IP or hostname")
@click.option("--user", "-u", default="root", help="SSH user")
@click.option("--output", "-o", type=click.Path(), help="Output path for kubeconfig")
@click.option(
    "--ssh-key",
    "-k",
    type=click.Path(exists=True),
    help="SSH private key file",
)
def cluster_kubeconfig(
    host: str, user: str, output: Optional[str], ssh_key: Optional[str]
):
    """Fetch kubeconfig from a control plane node."""
    setup = LitmusSetup(skip_k8s_init=True)

    output_path = Path(output) if output else None
    key_path = Path(ssh_key) if ssh_key else None

    try:
        kubeconfig_path = setup.fetch_kubeconfig(
            host,
            ansible_user=user,
            output_path=output_path,
            ssh_key=key_path,
        )
        click.echo(f"\nTo use this cluster:")
        click.echo(f"  export KUBECONFIG={kubeconfig_path}")
    except Exception as e:
        click.echo(f"Error fetching kubeconfig: {e}", err=True)
        sys.exit(1)


# ── Vagrant cluster commands ─────────────────────────────────


@cluster.group("vagrant")
def cluster_vagrant():
    """Manage local Vagrant VMs for development clusters."""
    pass


@cluster_vagrant.command("init")
@click.option("--name", "-n", default="chaosprobe", help="Cluster name")
@click.option(
    "--control-planes", "-c", default=1, help="Number of control plane nodes"
)
@click.option("--workers", "-w", default=2, help="Number of worker nodes")
@click.option("--memory", "-m", default=2048, help="Memory per VM in MB")
@click.option("--cpus", default=2, help="CPUs per VM")
@click.option("--box", default="generic/ubuntu2204", help="Vagrant box image")
@click.option(
    "--network-prefix", default="192.168.56", help="Network prefix for private IPs"
)
@click.option(
    "--output", "-o", type=click.Path(), help="Output directory for Vagrantfile"
)
def vagrant_init(
    name: str,
    control_planes: int,
    workers: int,
    memory: int,
    cpus: int,
    box: str,
    network_prefix: str,
    output: Optional[str],
):
    """Initialize a Vagrantfile for local cluster VMs."""
    setup = LitmusSetup(skip_k8s_init=True)
    prereqs = setup.check_prerequisites()

    if not prereqs["vagrant"]:
        click.echo(
            "Error: Vagrant not found. Please install Vagrant first.", err=True
        )
        sys.exit(1)

    output_dir = Path(output) if output else None

    try:
        vagrant_dir = setup.create_vagrantfile(
            cluster_name=name,
            num_control_planes=control_planes,
            num_workers=workers,
            vm_memory=memory,
            vm_cpus=cpus,
            box_image=box,
            network_prefix=network_prefix,
            output_dir=output_dir,
        )
        click.echo(f"\nVagrantfile created at: {vagrant_dir}")
        click.echo(f"\nNext steps:")
        click.echo(f"  1. Start VMs: chaosprobe cluster vagrant up --name {name}")
        click.echo(f"  2. Deploy K8s: chaosprobe cluster vagrant deploy --name {name}")
    except Exception as e:
        click.echo(f"Error creating Vagrantfile: {e}", err=True)
        sys.exit(1)


@cluster_vagrant.command("setup")
@click.option("--check-only", is_flag=True, help="Only check status, don't install")
def vagrant_setup(check_only: bool):
    """Setup libvirt/KVM for Vagrant on Linux."""
    setup = LitmusSetup(skip_k8s_init=True)

    click.echo("Checking libvirt/KVM status...")
    status = setup._check_libvirt()

    click.echo(f"\nLibvirt Status:")
    click.echo(
        f"  KVM available (/dev/kvm): {'OK' if status['kvm_available'] else 'MISSING'}"
    )
    click.echo(
        f"  libvirtd installed: {'OK' if status['libvirtd_installed'] else 'MISSING'}"
    )
    click.echo(
        f"  libvirtd running: {'OK' if status['libvirtd_running'] else 'NOT RUNNING'}"
    )
    click.echo(
        f"  User in libvirt/kvm groups: {'OK' if status['user_in_groups'] else 'MISSING'}"
    )
    click.echo(
        f"  vagrant-libvirt plugin: {'OK' if status['vagrant_libvirt_plugin'] else 'MISSING'}"
    )

    if status["all_ready"]:
        click.echo(click.style("\nLibvirt is fully configured!", fg="green"))
        return

    if check_only:
        click.echo(click.style("\nLibvirt is not fully configured.", fg="yellow"))
        click.echo(
            "Run 'chaosprobe cluster vagrant setup' to install missing components."
        )
        sys.exit(1)

    if not status["kvm_available"]:
        click.echo(
            click.style("\nError: KVM is not available.", fg="red"), err=True
        )
        click.echo("Enable CPU virtualisation in BIOS/WSL2.", err=True)
        sys.exit(1)

    click.echo(click.style("\nInstalling libvirt dependencies...", fg="yellow"))
    click.echo("This requires sudo access.\n")

    try:
        result = setup.install_libvirt()

        if result["needs_relogin"]:
            click.echo(click.style("\nInstallation complete!", fg="green"))
            click.echo(
                click.style(
                    "\nIMPORTANT: Log out and log back in for group changes.",
                    fg="yellow",
                )
            )
        else:
            click.echo(click.style("\nLibvirt setup complete!", fg="green"))
    except Exception as e:
        click.echo(f"\nError during installation: {e}", err=True)
        sys.exit(1)


@cluster_vagrant.command("up")
@click.option("--name", "-n", default="chaosprobe", help="Cluster name")
@click.option(
    "--provider",
    "-p",
    default="virtualbox",
    type=click.Choice(["virtualbox", "libvirt"]),
    help="Vagrant provider",
)
@click.option(
    "--dir",
    "-d",
    "vagrant_dir",
    type=click.Path(exists=True),
    help="Vagrant directory",
)
def vagrant_up(name: str, provider: str, vagrant_dir: Optional[str]):
    """Start Vagrant VMs."""
    setup = LitmusSetup(skip_k8s_init=True)

    if vagrant_dir:
        vdir = Path(vagrant_dir)
    else:
        vdir = setup.VAGRANT_DIR / name

    if not (vdir / "Vagrantfile").exists():
        click.echo(f"Error: No Vagrantfile found at {vdir}", err=True)
        sys.exit(1)

    if provider == "libvirt":
        click.echo("Checking libvirt configuration...")
        libvirt_status = setup._check_libvirt()
        if not libvirt_status["all_ready"]:
            click.echo(
                click.style("\nLibvirt is not fully configured.", fg="yellow")
            )
            click.echo(
                "\nRun 'chaosprobe cluster vagrant setup' to install libvirt."
            )
            sys.exit(1)
        click.echo("  Libvirt: OK")

    click.echo(f"\nStarting Vagrant VMs from {vdir}...")
    click.echo(f"  Provider: {provider}")

    try:
        setup.vagrant_up(vdir, provider=provider)
        click.echo(f"\nVMs are running. Next steps:")
        click.echo(
            f"  Deploy K8s: chaosprobe cluster vagrant deploy --name {name}"
        )
    except Exception as e:
        click.echo(f"\nError starting VMs: {e}", err=True)
        sys.exit(1)


@cluster_vagrant.command("status")
@click.option("--name", "-n", default="chaosprobe", help="Cluster name")
@click.option(
    "--dir",
    "-d",
    "vagrant_dir",
    type=click.Path(exists=True),
    help="Vagrant directory",
)
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def vagrant_status(name: str, vagrant_dir: Optional[str], json_output: bool):
    """Show status of Vagrant VMs."""
    setup = LitmusSetup(skip_k8s_init=True)

    if vagrant_dir:
        vdir = Path(vagrant_dir)
    else:
        vdir = setup.VAGRANT_DIR / name

    if not (vdir / "Vagrantfile").exists():
        click.echo(f"Error: No Vagrantfile found at {vdir}", err=True)
        sys.exit(1)

    try:
        vm_status = setup.vagrant_status(vdir)

        if json_output:
            click.echo(json.dumps(vm_status, indent=2))
            return

        click.echo(f"Vagrant VMs in {vdir}:")
        for vm_name, info in vm_status.items():
            state = info["state"]
            if state == "running":
                state_str = click.style(state, fg="green")
            elif state == "poweroff":
                state_str = click.style(state, fg="yellow")
            else:
                state_str = click.style(state, fg="red")
            click.echo(f"  {vm_name}: {state_str}")
    except Exception as e:
        click.echo(f"Error getting status: {e}", err=True)
        sys.exit(1)


@cluster_vagrant.command("deploy")
@click.option("--name", "-n", default="chaosprobe", help="Cluster name")
@click.option(
    "--dir",
    "-d",
    "vagrant_dir",
    type=click.Path(exists=True),
    help="Vagrant directory",
)
def vagrant_deploy(name: str, vagrant_dir: Optional[str]):
    """Deploy Kubernetes on running Vagrant VMs using Kubespray."""
    setup = LitmusSetup(skip_k8s_init=True)
    prereqs = setup.check_prerequisites()

    if not prereqs["git"]:
        click.echo(
            "Error: git is required for Kubespray. Please install it.", err=True
        )
        sys.exit(1)

    if not prereqs["python_venv"]:
        click.echo(
            "Error: python3-venv is required. Install with: apt install python3-venv",
            err=True,
        )
        sys.exit(1)

    if vagrant_dir:
        vdir = Path(vagrant_dir)
    else:
        vdir = setup.VAGRANT_DIR / name

    if not (vdir / "Vagrantfile").exists():
        click.echo(f"Error: No Vagrantfile found at {vdir}", err=True)
        sys.exit(1)

    try:
        vm_status = setup.vagrant_status(vdir)
        running = [n for n, i in vm_status.items() if i["state"] == "running"]
        if not running:
            click.echo("Error: No running VMs found.", err=True)
            sys.exit(1)
        click.echo(f"Found {len(running)} running VMs: {', '.join(running)}")
    except Exception as e:
        click.echo(f"Error checking VM status: {e}", err=True)
        sys.exit(1)

    click.echo("\nDeploying Kubernetes cluster on Vagrant VMs...")
    click.echo("This will take 15-30 minutes.\n")

    try:
        inventory_dir = setup.vagrant_deploy_cluster(vdir, cluster_name=name)
        click.echo(f"\nCluster deployed successfully!")
        click.echo(f"Inventory: {inventory_dir}")

        hosts = setup.get_vagrant_ssh_config(vdir)
        cp_hosts = [h for h in hosts if "control_plane" in h.get("roles", [])]
        if cp_hosts:
            cp_ip = cp_hosts[0]["ip"]
            cp_user = cp_hosts[0]["ansible_user"]
            click.echo(f"\nTo get kubeconfig:")
            click.echo(
                f"  chaosprobe cluster kubeconfig --host {cp_ip} --user {cp_user}"
            )
    except Exception as e:
        click.echo(f"\nError deploying cluster: {e}", err=True)
        sys.exit(1)


@cluster_vagrant.command("destroy")
@click.option("--name", "-n", default="chaosprobe", help="Cluster name")
@click.option(
    "--dir",
    "-d",
    "vagrant_dir",
    type=click.Path(exists=True),
    help="Vagrant directory",
)
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
def vagrant_destroy(name: str, vagrant_dir: Optional[str], force: bool):
    """Destroy Vagrant VMs."""
    setup = LitmusSetup(skip_k8s_init=True)

    if vagrant_dir:
        vdir = Path(vagrant_dir)
    else:
        vdir = setup.VAGRANT_DIR / name

    if not (vdir / "Vagrantfile").exists():
        click.echo(f"Error: No Vagrantfile found at {vdir}", err=True)
        sys.exit(1)

    if not force:
        click.confirm(
            f"This will destroy all VMs for cluster '{name}'. Continue?",
            abort=True,
        )

    try:
        setup.vagrant_destroy(vdir, force=True)
        click.echo("\nVMs destroyed. Vagrantfile preserved.")
    except Exception as e:
        click.echo(f"Error destroying VMs: {e}", err=True)
        sys.exit(1)


@cluster_vagrant.command("kubeconfig")
@click.option("--name", "-n", default="chaosprobe", help="Cluster name")
@click.option(
    "--dir",
    "-d",
    "vagrant_dir",
    type=click.Path(exists=True),
    help="Vagrant directory",
)
@click.option("--output", "-o", type=click.Path(), help="Output path for kubeconfig")
def vagrant_kubeconfig(name: str, vagrant_dir: Optional[str], output: Optional[str]):
    """Fetch kubeconfig from Vagrant control plane VM."""
    setup = LitmusSetup(skip_k8s_init=True)

    if vagrant_dir:
        vdir = Path(vagrant_dir)
    else:
        vdir = setup.VAGRANT_DIR / name

    if not (vdir / "Vagrantfile").exists():
        click.echo(f"Error: No Vagrantfile found at {vdir}", err=True)
        sys.exit(1)

    try:
        vm_status = setup.vagrant_status(vdir)
        running = [n for n, i in vm_status.items() if i["state"] == "running"]
        if not running:
            click.echo("Error: No running VMs found.", err=True)
            sys.exit(1)
    except Exception as e:
        click.echo(f"Error checking VM status: {e}", err=True)
        sys.exit(1)

    output_path = Path(output) if output else None

    try:
        kubeconfig_path = setup.vagrant_fetch_kubeconfig(
            vdir, output_path=output_path
        )
        click.echo(f"\nTo use this cluster:")
        click.echo(f"  export KUBECONFIG={kubeconfig_path}")
    except Exception as e:
        click.echo(f"Error fetching kubeconfig: {e}", err=True)
        sys.exit(1)


@cluster_vagrant.command("ssh")
@click.argument("vm_name", required=False)
@click.option("--name", "-n", default="chaosprobe", help="Cluster name")
@click.option(
    "--dir",
    "-d",
    "vagrant_dir",
    type=click.Path(exists=True),
    help="Vagrant directory",
)
def vagrant_ssh(vm_name: Optional[str], name: str, vagrant_dir: Optional[str]):
    """SSH into a Vagrant VM."""
    setup = LitmusSetup(skip_k8s_init=True)

    if vagrant_dir:
        vdir = Path(vagrant_dir)
    else:
        vdir = setup.VAGRANT_DIR / name

    if not (vdir / "Vagrantfile").exists():
        click.echo(f"Error: No Vagrantfile found at {vdir}", err=True)
        sys.exit(1)

    if not vm_name:
        try:
            vm_status = setup.vagrant_status(vdir)
            click.echo("Available VMs:")
            for vname, info in vm_status.items():
                click.echo(f"  {vname} ({info['state']})")
            click.echo("\nUsage: chaosprobe cluster vagrant ssh <vm_name>")
        except Exception as e:
            click.echo(f"Error getting VM list: {e}", err=True)
            sys.exit(1)
        return

    import os

    os.chdir(vdir)
    os.execvp("vagrant", ["vagrant", "ssh", vm_name])


# ─────────────────────────────────────────────────────────────
# Scenario commands
# ─────────────────────────────────────────────────────────────




@main.command()
@click.argument("scenario_path", type=click.Path(exists=True))
@click.option(
    "--namespace", "-n", default=None, help="Override namespace (default: from scenario)"
)
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
@click.argument("baseline_file", type=click.Path(exists=True))
@click.argument("afterfix_file", type=click.Path(exists=True))
@click.option(
    "--output", "-o", type=click.Path(), help="Output file for comparison JSON"
)
def compare(baseline_file: str, afterfix_file: str, output: Optional[str]):
    """Compare baseline results with after-fix results.

    BASELINE_FILE: Path to the baseline results JSON file.
    AFTERFIX_FILE: Path to the after-fix results JSON file.
    """
    click.echo(f"Comparing {baseline_file} with {afterfix_file}...")

    try:
        baseline_data = json.loads(Path(baseline_file).read_text())
        afterfix_data = json.loads(Path(afterfix_file).read_text())
    except Exception as e:
        click.echo(f"Error loading result files: {e}", err=True)
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
        f"  Resilience Score Change: "
        f"{comparison['comparison']['resilienceScoreChange']:+.1f}"
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
# Placement commands — chaotic pod scheduling
# ─────────────────────────────────────────────────────────────


@main.group()
def placement():
    """Manipulate pod placement for contention experiments.

    Apply placement strategies to control which nodes pods run on,
    creating deterministic contention patterns for studying the effects
    of pod co-location on IO and execution performance.

    \b
    Strategies:
      colocate      Pin all pods to a single node (max contention)
      spread        Distribute evenly across nodes (min contention)
      random        Random node per deployment (chaotic, --seed for reproducibility)
      antagonistic  Group resource-heavy pods on same node (worst-case)

    \b
    Typical workflow:
      1. Deploy your application:
         chaosprobe provision scenarios/online-boutique/deploy/
      2. Apply a placement strategy:
         chaosprobe placement apply colocate -n online-boutique
      3. Run chaos experiments under that placement:
         chaosprobe run scenarios/online-boutique/placement-experiment.yaml -o results.json
      4. Clear placement and try another strategy:
         chaosprobe placement clear -n online-boutique
    """
    pass


@placement.command("apply")
@click.argument(
    "strategy",
    type=click.Choice([s.value for s in PlacementStrategy], case_sensitive=False),
)
@click.option(
    "--namespace", "-n", default="online-boutique",
    help="Namespace containing deployments",
)
@click.option(
    "--target-node", "-t", default=None,
    help="For 'colocate': pin to this specific node",
)
@click.option(
    "--seed", "-s", default=None, type=int,
    help="For 'random': seed for reproducible assignments",
)
@click.option(
    "--deployments", "-d", default=None,
    help="Comma-separated list of deployment names (default: all in namespace)",
)
@click.option(
    "--no-wait", is_flag=True,
    help="Don't wait for rollouts to complete",
)
@click.option(
    "--timeout", default=300, type=int,
    help="Timeout in seconds for rollout completion",
)
@click.option(
    "--output", "-o", type=click.Path(),
    help="Save assignment to JSON file",
)
def placement_apply(
    strategy: str,
    namespace: str,
    target_node: Optional[str],
    seed: Optional[int],
    deployments: Optional[str],
    no_wait: bool,
    timeout: int,
    output: Optional[str],
):
    """Apply a pod placement strategy to deployments.

    Forces pods onto specific nodes to create contention patterns.
    Uses nodeSelector to deterministically control scheduling.

    \b
    Examples:
      # Pack everything onto one node
      chaosprobe placement apply colocate -n online-boutique

      # Spread across all nodes
      chaosprobe placement apply spread -n online-boutique

      # Random placement with reproducible seed
      chaosprobe placement apply random -n online-boutique --seed 42

      # Worst-case: heavy pods together
      chaosprobe placement apply antagonistic -n online-boutique
    """
    strat = PlacementStrategy(strategy)
    click.echo(f"Applying placement strategy: {strat.value}")
    click.echo(f"  {strat.describe()}")
    click.echo(f"  Namespace: {namespace}")

    dep_list = [d.strip() for d in deployments.split(",")] if deployments else None

    mutator = PlacementMutator(namespace)

    # Show available nodes
    nodes = mutator.get_nodes()
    schedulable = [n for n in nodes if n.is_schedulable]
    click.echo(f"\nCluster nodes ({len(schedulable)} schedulable):")
    for node in schedulable:
        cpu_cores = node.allocatable_cpu_millicores / 1000
        mem_gib = node.allocatable_memory_bytes / (1024 ** 3)
        click.echo(f"  {node.name}: {cpu_cores:.1f} CPU, {mem_gib:.1f} GiB RAM")

    # Show target deployments
    deps = mutator.get_deployments()
    if dep_list:
        deps = [d for d in deps if d.name in dep_list]
    click.echo(f"\nDeployments ({len(deps)}):")
    for d in deps:
        cpu = d.cpu_request_millicores
        mem_mib = d.memory_request_bytes / (1024 ** 2)
        current = d.current_node or "unknown"
        click.echo(f"  {d.name}: {cpu}m CPU, {mem_mib:.0f}Mi RAM (node: {current})")

    # Apply
    click.echo(f"\nApplying {strat.value} placement...")
    try:
        assignment = mutator.apply_strategy(
            strategy=strat,
            target_node=target_node,
            seed=seed,
            deployments=dep_list,
            wait=not no_wait,
            timeout=timeout,
        )
    except Exception as e:
        click.echo(f"Error applying placement: {e}", err=True)
        sys.exit(1)

    # Summary
    click.echo(f"\nPlacement applied:")
    click.echo(f"  Strategy: {assignment.strategy.value}")
    click.echo(f"  Description: {assignment.metadata.get('description', '')}")
    for dep_name, node_name in sorted(assignment.assignments.items()):
        click.echo(f"    {dep_name} → {node_name}")

    if output:
        output_path = Path(output)
        output_path.write_text(json.dumps(assignment.to_dict(), indent=2))
        click.echo(f"\n  Assignment saved to {output}")


@placement.command("clear")
@click.option(
    "--namespace", "-n", default="online-boutique",
    help="Namespace containing deployments",
)
@click.option(
    "--deployments", "-d", default=None,
    help="Comma-separated list of deployment names (default: all managed)",
)
@click.option(
    "--no-wait", is_flag=True,
    help="Don't wait for rollouts to complete",
)
def placement_clear(
    namespace: str,
    deployments: Optional[str],
    no_wait: bool,
):
    """Clear all ChaosProbe placement constraints.

    Removes nodeSelector rules and node labels set by 'placement apply',
    restoring default Kubernetes scheduling.
    """
    click.echo(f"Clearing placement constraints in namespace: {namespace}")

    dep_list = [d.strip() for d in deployments.split(",")] if deployments else None

    mutator = PlacementMutator(namespace)

    try:
        cleared = mutator.clear_placement(
            deployments=dep_list,
            wait=not no_wait,
        )
    except Exception as e:
        click.echo(f"Error clearing placement: {e}", err=True)
        sys.exit(1)

    if cleared:
        click.echo(f"\nCleared placement for {len(cleared)} deployment(s):")
        for name in cleared:
            click.echo(f"  {name}")
    else:
        click.echo("No managed placement constraints found.")


@placement.command("show")
@click.option(
    "--namespace", "-n", default="online-boutique",
    help="Namespace containing deployments",
)
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def placement_show(namespace: str, json_output: bool):
    """Show current pod placement state.

    Displays where each deployment's pods are running and
    any active ChaosProbe placement constraints.
    """
    mutator = PlacementMutator(namespace)

    try:
        placement_state = mutator.get_current_placement()
    except Exception as e:
        click.echo(f"Error reading placement: {e}", err=True)
        sys.exit(1)

    if json_output:
        click.echo(json.dumps(placement_state, indent=2))
        return

    click.echo(f"Pod placement in namespace: {namespace}\n")

    managed_count = sum(1 for v in placement_state.values() if v["managed"])
    click.echo(f"  Managed by ChaosProbe: {managed_count}/{len(placement_state)}")
    click.echo("")

    for dep_name, info in sorted(placement_state.items()):
        node = info["currentNode"] or "unknown"
        strategy = info["strategy"] or "default"
        marker = "*" if info["managed"] else " "
        click.echo(f"  {marker} {dep_name:30s} node={node:15s} strategy={strategy}")

    if managed_count > 0:
        click.echo("\n  * = managed by ChaosProbe placement")


@placement.command("nodes")
def placement_nodes():
    """Show cluster nodes with scheduling information.

    Lists all nodes with their allocatable resources, readiness,
    and any taints that affect scheduling.
    """
    # Use a dummy namespace, we only need node info
    mutator = PlacementMutator("default")

    try:
        nodes = mutator.get_nodes()
    except Exception as e:
        click.echo(f"Error reading nodes: {e}", err=True)
        sys.exit(1)

    click.echo(f"Cluster nodes ({len(nodes)} total):\n")

    for node in nodes:
        cpu_cores = node.allocatable_cpu_millicores / 1000
        mem_gib = node.allocatable_memory_bytes / (1024 ** 3)
        status = "Ready" if node.conditions_ready else "NotReady"
        schedulable = "schedulable" if node.is_schedulable else "unschedulable"

        click.echo(f"  {node.name}")
        click.echo(f"    Status: {status} ({schedulable})")
        click.echo(f"    Resources: {cpu_cores:.1f} CPU, {mem_gib:.1f} GiB RAM")
        if node.taints:
            taints_str = ", ".join(
                f"{t['key']}={t.get('value', '')}:{t['effect']}" for t in node.taints
            )
            click.echo(f"    Taints: {taints_str}")
        click.echo("")


# ─────────────────────────────────────────────────────────────
# run — automated full experiment matrix
# ─────────────────────────────────────────────────────────────


@main.command()
@click.option(
    "--namespace", "-n", default="online-boutique",
    help="Namespace containing the application",
)
@click.option(
    "--output-dir", "-o", default="results",
    help="Base directory for results (a timestamped subdirectory is created)",
)
@click.option(
    "--strategies", "-s", default="baseline,colocate,spread,antagonistic,random",
    help="Comma-separated strategies to test (default: all)",
)
@click.option(
    "--timeout", "-t", default=300, type=int,
    help="Timeout per experiment in seconds",
)
@click.option(
    "--seed", default=42, type=int,
    help="Seed for the random strategy",
)
@click.option(
    "--settle-time", default=30, type=int,
    help="Seconds to wait after placement before running experiment",
)
@click.option(
    "--no-auto-setup", is_flag=True,
    help="Disable automatic LitmusChaos installation",
)
@click.option(
    "--experiment", "-e",
    default="scenarios/online-boutique/placement-experiment.yaml",
    help="Path to the placement experiment YAML file",
)
@click.option(
    "--iterations", "-i", default=1, type=int,
    help="Number of iterations per strategy (default: 1)",
)
@click.option(
    "--provision", is_flag=True,
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
    default="http://frontend.online-boutique.svc.cluster.local",
    help="Target URL for load generation",
)
@click.option(
    "--db",
    default="results.db",
    help="Path to SQLite database for persisting results",
)
@click.option(
    "--visualize/--no-visualize", "do_visualize",
    default=True, show_default=True,
    help="Generate visualization charts after experiments complete",
)
@click.option(
    "--measure-latency/--no-measure-latency", "measure_latency",
    default=True, show_default=True,
    help="Measure inter-service latency during each experiment",
)
@click.option(
    "--measure-redis/--no-measure-redis", "measure_redis",
    default=True, show_default=True,
    help="Measure Redis throughput during each experiment",
)
@click.option(
    "--measure-disk/--no-measure-disk", "measure_disk",
    default=True, show_default=True,
    help="Measure disk I/O throughput during each experiment",
)
@click.option(
    "--measure-resources/--no-measure-resources", "measure_resources",
    default=True, show_default=True,
    help="Measure node/pod resource utilization during each experiment",
)
@click.option(
    "--collect-logs/--no-collect-logs", "collect_logs",
    default=True, show_default=True,
    help="Collect container logs from target deployment after each experiment",
)
@click.option(
    "--measure-prometheus/--no-measure-prometheus", "measure_prometheus",
    default=True, show_default=False,
    help="Query Prometheus for cluster metrics during each experiment",
)
@click.option(
    "--prometheus-url",
    multiple=True,
    help="Prometheus server URL(s); repeat for multiple instances (auto-discovered if omitted)",
)
@click.option(
    "--neo4j-uri",
    default="bolt://localhost:7687", envvar="NEO4J_URI",
    help="Neo4j connection URI (default: bolt://localhost:7687). Enables graph storage.",
)
@click.option(
    "--neo4j-user",
    default="neo4j", envvar="NEO4J_USER",
    help="Neo4j username (default: neo4j)",
)
@click.option(
    "--neo4j-password",
    default="chaosprobe", envvar="NEO4J_PASSWORD",
    help="Neo4j password (default: chaosprobe)",
)
def run(
    namespace: str,
    output_dir: Optional[str],
    strategies: str,
    timeout: int,
    seed: int,
    settle_time: int,
    no_auto_setup: bool,
    experiment: str,
    iterations: int,
    provision: bool,
    load_profile: Optional[str],
    locustfile: Optional[str],
    target_url: Optional[str],
    db: Optional[str],
    do_visualize: bool,
    measure_latency: bool,
    measure_redis: bool,
    measure_disk: bool,
    measure_resources: bool,
    collect_logs: bool,
    measure_prometheus: bool,
    prometheus_url: Tuple[str, ...],
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
    valid_strategies = {"baseline", "colocate", "spread", "antagonistic", "random"}
    for s in strategy_list:
        if s not in valid_strategies:
            click.echo(f"Error: Unknown strategy '{s}'. Valid: {', '.join(sorted(valid_strategies))}", err=True)
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
    try:
        shared_scenario = load_scenario(str(experiment_file))
        validate_scenario(shared_scenario)
        shared_scenario["namespace"] = namespace
    except Exception as e:
        click.echo(f"Error loading experiment: {e}", err=True)
        sys.exit(1)

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
    if not ensure_litmus_setup(namespace, experiment_types, auto_setup=not no_auto_setup):
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
        click.echo(f"  Latency:    Measuring inter-service latency during experiments")
    if measure_redis:
        click.echo(f"  Redis:      Measuring Redis throughput during experiments")
    if measure_disk:
        click.echo(f"  Disk:       Measuring disk I/O throughput during experiments")
    if measure_resources:
        click.echo(f"  Resources:  Measuring node/pod resource utilization during experiments")
    if measure_prometheus:
        prom_display = ", ".join(prometheus_url) if prometheus_url else "(auto-discover)"
        click.echo(f"  Prometheus: Querying cluster Prometheus at {prom_display}")
    if collect_logs:
        click.echo(f"  Logs:       Collecting container logs from target deployment")
    click.echo("")

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
    run_store = _get_store(db)

    # Optional Neo4j graph store
    graph_store = None
    if neo4j_uri:
        try:
            from chaosprobe.storage.neo4j_store import Neo4jStore
            graph_store = Neo4jStore(neo4j_uri, neo4j_user, neo4j_password)
            graph_store.ensure_schema()
            graph_store.sync_service_dependencies()
            click.echo(f"  Neo4j:      connected ({neo4j_uri})")
        except ImportError:
            click.echo("  Neo4j: skipped (install with: uv pip install chaosprobe[graph])", err=True)
        except Exception as e:
            click.echo(f"  Neo4j: connection failed ({e})", err=True)

    for idx, strategy_name in enumerate(strategy_list, 1):
        click.echo(f"\n{'─' * 60}")
        click.echo(f"[{idx}/{total}] Strategy: {strategy_name}")
        click.echo(f"{'─' * 60}")

        strategy_result: Dict[str, Any] = {
            "strategy": strategy_name,
            "status": "pending",
            "placement": None,
            "experiment": None,
            "metrics": None,
            "error": None,
        }

        try:
            # ── Step 1: Clear any existing placement ──
            click.echo("\n  Step 1: Clearing existing placement...")
            mutator.clear_placement(wait=True)
            click.echo("    Placement cleared.")

            # ── Step 2: Apply placement (skip for baseline) ──
            if strategy_name == "baseline":
                click.echo("\n  Step 2: Baseline — using default scheduling")
                strategy_result["placement"] = {"strategy": "baseline", "description": "Default Kubernetes scheduling"}
            else:
                click.echo(f"\n  Step 2: Applying {strategy_name} placement...")
                strat = PlacementStrategy(strategy_name)
                assignment = mutator.apply_strategy(
                    strategy=strat,
                    seed=seed if strategy_name == "random" else None,
                    wait=True,
                    timeout=timeout,
                )
                strategy_result["placement"] = assignment.to_dict()

                # Show summary
                nodes_used = set(assignment.assignments.values())
                click.echo(f"    Placed {len(assignment.assignments)} deployments across {len(nodes_used)} node(s)")
                for node in sorted(nodes_used):
                    count = sum(1 for n in assignment.assignments.values() if n == node)
                    click.echo(f"      {node}: {count} deployment(s)")

            # ── Run iterations ──
            iteration_results: List[Dict[str, Any]] = []

            for iter_num in range(1, iterations + 1):
                if iterations > 1:
                    click.echo(f"\n  ── Iteration {iter_num}/{iterations} ──")

                # Settle
                step_label = f"  Step 3" if iterations == 1 else f"    Step A"
                if settle_time > 0:
                    click.echo(f"\n{step_label}: Waiting {settle_time}s for workloads to settle...")
                    time.sleep(settle_time)

                # Verify all deployments are ready before proceeding
                click.echo("    Verifying deployment readiness...")
                _wait_for_healthy_deployments(namespace, timeout=60)
                click.echo("    Ready.")

                # Run chaos experiment
                step_label = f"  Step 4" if iterations == 1 else f"    Step B"
                click.echo(f"\n{step_label}: Running experiment...")

                scenario = copy.deepcopy(shared_scenario)
                for exp in scenario.get("experiments", []):
                    orig_name = exp["spec"].get("metadata", {}).get("name", "placement-pod-delete")
                    if iterations > 1:
                        exp["spec"]["metadata"]["name"] = f"{orig_name}-{strategy_name}-i{iter_num}"
                    else:
                        exp["spec"]["metadata"]["name"] = f"{orig_name}-{strategy_name}"

                # Start real-time recovery watcher before experiment
                watcher = RecoveryWatcher(namespace, target_deployment)
                watcher.start()

                # Start continuous latency prober if requested
                latency_prober = None
                latency_data = None
                if measure_latency:
                    click.echo("    Starting inter-service latency probing...")
                    latency_prober = ContinuousLatencyProber(namespace)
                    latency_prober.start()

                # Start continuous Redis prober if requested
                redis_prober = None
                redis_data = None
                if measure_redis:
                    click.echo("    Starting Redis throughput probing...")
                    redis_prober = ContinuousRedisProber(namespace)
                    redis_prober.start()

                # Start continuous disk prober if requested
                disk_prober = None
                disk_data = None
                if measure_disk:
                    click.echo("    Starting disk I/O throughput probing...")
                    disk_prober = ContinuousDiskProber(
                        namespace, disk_target=target_deployment,
                    )
                    disk_prober.start()

                # Start continuous resource prober if requested
                resource_prober = None
                resource_data = None
                if measure_resources:
                    click.echo("    Starting resource utilization probing...")
                    resource_prober = ContinuousResourceProber(
                        namespace, target_deployment,
                    )
                    resource_prober.start()

                # Start continuous Prometheus prober if requested
                prometheus_prober = None
                prometheus_data = None
                if measure_prometheus:
                    click.echo("    Starting Prometheus metrics collection...")
                    prometheus_prober = ContinuousPrometheusProber(
                        namespace, prometheus_urls=list(prometheus_url) if prometheus_url else None,
                    )
                    prometheus_prober.start()

                # Start Locust load generation if requested
                iter_locust_runner = None
                iter_load_stats = None
                if load_profile:
                    profile = LoadProfile.from_name(load_profile)
                    url = target_url
                    click.echo(f"    Starting Locust ({load_profile}: {profile.users} users)")
                    iter_locust_runner = LocustRunner(target_url=url, locustfile=locustfile)
                    iter_locust_runner.start(profile)

                try:
                    # Collect pre-chaos baseline samples
                    pre_chaos_window = min(settle_time, 15)
                    if (latency_prober or redis_prober or disk_prober or resource_prober or prometheus_prober) and pre_chaos_window > 0:
                        click.echo(f"    Collecting pre-chaos baseline ({pre_chaos_window}s)...")
                        time.sleep(pre_chaos_window)

                    experiment_start = time.time()
                    if latency_prober:
                        latency_prober.mark_chaos_start()
                    if redis_prober:
                        redis_prober.mark_chaos_start()
                    if disk_prober:
                        disk_prober.mark_chaos_start()
                    if resource_prober:
                        resource_prober.mark_chaos_start()
                    if prometheus_prober:
                        prometheus_prober.mark_chaos_start()
                    runner = ChaosRunner(namespace, timeout=timeout)
                    runner.run_experiments(scenario.get("experiments", []))
                    experiment_end = time.time()
                    if latency_prober:
                        latency_prober.mark_chaos_end()
                    if redis_prober:
                        redis_prober.mark_chaos_end()
                    if disk_prober:
                        disk_prober.mark_chaos_end()
                    if resource_prober:
                        resource_prober.mark_chaos_end()
                    if prometheus_prober:
                        prometheus_prober.mark_chaos_end()

                    # Collect post-chaos recovery samples
                    post_chaos_window = min(settle_time, 15)
                    if (latency_prober or redis_prober or disk_prober or resource_prober or prometheus_prober) and post_chaos_window > 0:
                        click.echo(f"    Collecting post-chaos samples ({post_chaos_window}s)...")
                        time.sleep(post_chaos_window)
                finally:
                    # Always stop Locust, latency prober, and watcher even if experiment fails.
                    # Each block is independent so one failure doesn't skip the rest.
                    if iter_locust_runner:
                        try:
                            iter_locust_runner.stop()
                            iter_load_stats = iter_locust_runner.collect_stats()
                            click.echo(f"    Load: {iter_load_stats.total_requests} reqs, "
                                       f"p95={iter_load_stats.p95_response_time_ms:.0f}ms, "
                                       f"err={iter_load_stats.error_rate:.2%}")
                        except Exception as e:
                            click.echo(f"    Warning: failed to collect load stats: {e}", err=True)
                        finally:
                            iter_locust_runner.cleanup()
                    if latency_prober:
                        try:
                            latency_prober.stop()
                            latency_data = latency_prober.result()
                            phase_data = latency_data.get("phases", {})
                            during = phase_data.get("during-chaos", {})
                            n_samples = during.get("sampleCount", 0)
                            click.echo(f"    Latency: {n_samples} samples during chaos")
                        except Exception as e:
                            click.echo(f"    Warning: failed to collect latency data: {e}", err=True)
                    if redis_prober:
                        try:
                            redis_prober.stop()
                            redis_data = redis_prober.result()
                            rp = redis_data.get("phases", {}).get("during-chaos", {})
                            click.echo(f"    Redis: {rp.get('sampleCount', 0)} samples during chaos")
                        except Exception as e:
                            click.echo(f"    Warning: failed to collect Redis data: {e}", err=True)
                    if disk_prober:
                        try:
                            disk_prober.stop()
                            disk_data = disk_prober.result()
                            dp = disk_data.get("phases", {}).get("during-chaos", {})
                            click.echo(f"    Disk: {dp.get('sampleCount', 0)} samples during chaos")
                        except Exception as e:
                            click.echo(f"    Warning: failed to collect disk data: {e}", err=True)
                    if resource_prober:
                        try:
                            resource_prober.stop()
                            resource_data = resource_prober.result()
                            if resource_data.get("available"):
                                rp = resource_data.get("phases", {}).get("during-chaos", {})
                                click.echo(f"    Resources: {rp.get('sampleCount', 0)} samples during chaos")
                            else:
                                click.echo(f"    Resources: {resource_data.get('reason', 'unavailable')}")
                        except Exception as e:
                            click.echo(f"    Warning: failed to collect resource data: {e}", err=True)
                    if prometheus_prober:
                        try:
                            prometheus_prober.stop()
                            prometheus_data = prometheus_prober.result()
                            if prometheus_data.get("available"):
                                pp = prometheus_data.get("phases", {}).get("during-chaos", {})
                                click.echo(f"    Prometheus: {pp.get('sampleCount', 0)} samples during chaos")
                            else:
                                click.echo(f"    Prometheus: {prometheus_data.get('reason', 'unavailable')}")
                        except Exception as e:
                            click.echo(f"    Warning: failed to collect Prometheus data: {e}", err=True)
                    watcher.stop()

                recovery_data = watcher.result()

                # Collect results
                collector = ResultCollector(namespace)
                executed = runner.get_executed_experiments()
                results = collector.collect(executed)

                # Collect metrics (pod status, node info) + merge watcher data
                recovery = metrics_collector.collect(
                    deployment_name=target_deployment,
                    since_time=experiment_start,
                    until_time=experiment_end,
                    recovery_data=recovery_data,
                    latency_data=latency_data,
                    redis_data=redis_data,
                    disk_data=disk_data,
                    resource_data=resource_data,
                    prometheus_data=prometheus_data,
                    collect_logs=collect_logs,
                )

                # Generate output
                generator = OutputGenerator(scenario, results, metrics=recovery)
                output_data = generator.generate()

                # Merge load stats into output
                if iter_load_stats:
                    output_data["loadGeneration"] = {
                        "profile": load_profile,
                        "stats": iter_load_stats.to_dict(),
                    }

                # Save result file
                if iterations > 1:
                    result_file = results_dir / f"{strategy_name}-iter-{iter_num}.json"
                else:
                    result_file = results_dir / f"{strategy_name}.json"
                result_file.write_text(json.dumps(output_data, indent=2))

                # Persist to database after JSON is finalised — keeps both in sync
                if run_store:
                    try:
                        run_store.save_run(output_data)
                    except Exception as e:
                        import warnings
                        warnings.warn(f"Failed to save results to database: {e}")

                # Sync to Neo4j graph if connected
                if graph_store:
                    try:
                        graph_store.sync_run(output_data)
                    except Exception as e:
                        click.echo(f"    Warning: Neo4j sync failed: {e}", err=True)

                verdict = output_data.get("summary", {}).get("overallVerdict", "UNKNOWN")
                score = output_data.get("summary", {}).get("resilienceScore", 0)
                rec_summary = recovery.get("recovery", {}).get("summary", {})
                avg_recovery = rec_summary.get("meanRecovery_ms")
                recovery_str = f" | Avg Recovery: {avg_recovery:.0f}ms" if avg_recovery else ""

                click.echo(f"\n    Results saved to {result_file}")
                click.echo(f"    Verdict: {verdict} | Resilience Score: {score:.1f}{recovery_str}")

                iteration_results.append({
                    "iteration": iter_num,
                    "verdict": verdict,
                    "resilienceScore": score,
                    "metrics": recovery,
                    "resultFile": str(result_file),
                })

            # Aggregate results across iterations
            if iterations > 1:
                strategy_result["iterations"] = iteration_results
                strategy_result["aggregated"] = _aggregate_iterations(iteration_results)
                strategy_result["experiment"] = strategy_result["aggregated"]
                strategy_result["status"] = "completed"

                agg = strategy_result["aggregated"]
                iter_passed = sum(1 for ir in iteration_results if ir["verdict"] == "PASS")
                click.echo(f"\n    Aggregated: {iter_passed}/{iterations} passed | "
                           f"Mean Score: {agg['meanResilienceScore']:.1f}")
                if agg.get("meanRecoveryTime_ms") is not None:
                    click.echo(f"    Mean Recovery: {agg['meanRecoveryTime_ms']:.0f}ms | "
                               f"Max: {agg['maxRecoveryTime_ms']:.0f}ms")

                if agg["passRate"] == 1.0:
                    passed += 1
                else:
                    failed += 1
            else:
                # Single iteration — keep backward-compatible structure
                ir = iteration_results[0]
                strategy_result["experiment"] = {
                    "overallVerdict": ir["verdict"],
                    "resilienceScore": ir["resilienceScore"],
                    "passed": 1 if ir["verdict"] == "PASS" else 0,
                    "failed": 0 if ir["verdict"] == "PASS" else 1,
                    "totalExperiments": 1,
                }
                strategy_result["metrics"] = ir["metrics"]
                strategy_result["status"] = "completed"
                strategy_result["resultFile"] = ir["resultFile"]

                if ir["verdict"] == "PASS":
                    passed += 1
                else:
                    failed += 1

        except Exception as e:
            click.echo(f"\n    ERROR: {e}", err=True)
            strategy_result["status"] = "error"
            strategy_result["error"] = str(e)
            failed += 1

        overall_results["strategies"][strategy_name] = strategy_result

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

    # Build comparison table
    comparison_table = _build_comparison_table(overall_results["strategies"], iterations)
    overall_results["comparison"] = comparison_table

    summary_file = results_dir / "summary.json"
    summary_file.write_text(json.dumps(overall_results, indent=2))

    # ── Print final summary ──
    click.echo(f"\n{'=' * 60}")
    click.echo("EXPERIMENT RESULTS")
    click.echo(f"{'=' * 60}")

    has_recovery = any(r.get("avgRecovery_ms") is not None for r in comparison_table)
    if has_recovery:
        click.echo(f"\n  {'Strategy':<16s} {'Verdict':<8s} {'Score':<8s} "
                    f"{'Avg Rec.':<10s} {'Max Rec.':<10s} {'Status'}")
        click.echo(f"  {'─' * 68}")
        for row in comparison_table:
            avg_r = f"{row['avgRecovery_ms']:.0f}ms" if row.get("avgRecovery_ms") is not None else "n/a"
            max_r = f"{row['maxRecovery_ms']:.0f}ms" if row.get("maxRecovery_ms") is not None else "n/a"
            click.echo(
                f"  {row['strategy']:<16s} {row['verdict']:<8s} "
                f"{row['resilienceScore']:<8.1f} {avg_r:<10s} {max_r:<10s} {row['status']}"
            )
    else:
        click.echo(f"\n  {'Strategy':<20s} {'Verdict':<10s} {'Score':<10s} {'Status'}")
        click.echo(f"  {'─' * 55}")
        for row in comparison_table:
            click.echo(
                f"  {row['strategy']:<20s} {row['verdict']:<10s} "
                f"{row['resilienceScore']:<10.1f} {row['status']}"
            )

    click.echo(f"\n  Results directory: {results_dir}")
    click.echo(f"  Summary:          {summary_file}")
    click.echo(f"\n  Total: {total} | Passed: {passed} | Failed: {failed}")

    # ── Generate visualizations if requested ──
    if do_visualize:
        click.echo(f"\n{'─' * 60}")
        click.echo("Generating visualizations...")
        try:
            from chaosprobe.output.visualize import generate_from_summary
            charts_dir = str(results_dir / "charts")
            generated = generate_from_summary(str(summary_file), charts_dir)
            if generated:
                click.echo(f"  Generated {len(generated)} file(s) in {charts_dir}")
                html_files = [p for p in generated if p.endswith(".html")]
                if html_files:
                    click.echo(f"  Report: {html_files[0]}")
            else:
                click.echo("  No data available to visualize.")
        except ImportError as e:
            click.echo(f"  Skipping visualization: {e}", err=True)

    # Close shared database connection
    if run_store:
        run_store.close()
    if graph_store:
        graph_store.close()

    click.echo("")


def _extract_target_deployment(scenario: Dict[str, Any]) -> str:
    """Extract the target deployment name from experiment appinfo."""
    for exp in scenario.get("experiments", []):
        appinfo = exp.get("spec", {}).get("spec", {}).get("appinfo", {})
        applabel = appinfo.get("applabel", "")
        if applabel.startswith("app="):
            return applabel.split("=", 1)[1]
    return "checkoutservice"


def _wait_for_healthy_deployments(namespace: str, timeout: int = 60) -> None:
    """Wait until all deployments in the namespace have all replicas ready."""
    from kubernetes import client, config as k8s_config

    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()

    apps_api = client.AppsV1Api()
    deadline = time.time() + timeout

    while time.time() < deadline:
        all_ready = True
        deps = apps_api.list_namespaced_deployment(namespace)
        for dep in deps.items:
            desired = dep.spec.replicas or 1
            ready = (dep.status.ready_replicas or 0) if dep.status else 0
            available = (dep.status.available_replicas or 0) if dep.status else 0
            if ready < desired or available < desired:
                all_ready = False
                break
        if all_ready:
            return
        time.sleep(5)

    # Log which deployments are not ready but don't fail
    deps = apps_api.list_namespaced_deployment(namespace)
    for dep in deps.items:
        desired = dep.spec.replicas or 1
        ready = (dep.status.ready_replicas or 0) if dep.status else 0
        if ready < desired:
            click.echo(
                f"    Warning: {dep.metadata.name} not fully ready "
                f"({ready}/{desired} replicas)",
                err=True,
            )


def _aggregate_iterations(
    iteration_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute aggregated statistics across multiple iterations."""
    scores = [ir["resilienceScore"] for ir in iteration_results]
    verdicts = [ir["verdict"] for ir in iteration_results]
    pass_count = sum(1 for v in verdicts if v == "PASS")

    agg: Dict[str, Any] = {
        "overallVerdict": "PASS" if pass_count == len(verdicts) else "FAIL",
        "passRate": round(pass_count / len(verdicts), 2),
        "meanResilienceScore": round(statistics.mean(scores), 1),
        "totalExperiments": len(iteration_results),
        "passed": pass_count,
        "failed": len(verdicts) - pass_count,
    }

    # Aggregate recovery metrics from metrics.recovery.summary
    all_recovery_times: List[float] = []
    for ir in iteration_results:
        rm = ir.get("metrics", {})
        if rm:
            summary = rm.get("recovery", {}).get("summary", {})
            mean_r = summary.get("meanRecovery_ms")
            if mean_r is not None:
                all_recovery_times.append(mean_r)

    if all_recovery_times:
        all_max = []
        for ir in iteration_results:
            rm = ir.get("metrics", {})
            if rm:
                max_r = rm.get("recovery", {}).get("summary", {}).get("maxRecovery_ms")
                if max_r is not None:
                    all_max.append(max_r)

        agg["meanRecoveryTime_ms"] = round(statistics.mean(all_recovery_times), 1)
        agg["medianRecoveryTime_ms"] = round(statistics.median(all_recovery_times), 1)
        agg["maxRecoveryTime_ms"] = max(all_max) if all_max else None
    else:
        agg["meanRecoveryTime_ms"] = None
        agg["medianRecoveryTime_ms"] = None
        agg["maxRecoveryTime_ms"] = None

    return agg


def _build_comparison_table(
    strategies: Dict[str, Any],
    iterations: int,
) -> List[Dict[str, Any]]:
    """Build comparison table with recovery metrics."""
    table = []
    for sname, sdata in strategies.items():
        exp = sdata.get("experiment", {}) or {}

        row: Dict[str, Any] = {
            "strategy": sname,
            "verdict": exp.get("overallVerdict", "ERROR"),
            "resilienceScore": exp.get("resilienceScore",
                                       exp.get("meanResilienceScore", 0)),
            "passed": exp.get("passed", 0),
            "failed": exp.get("failed", 0),
            "status": sdata["status"],
        }

        # Extract recovery metrics
        if iterations > 1:
            agg = sdata.get("aggregated", {})
            row["avgRecovery_ms"] = agg.get("meanRecoveryTime_ms")
            row["maxRecovery_ms"] = agg.get("maxRecoveryTime_ms")
            row["passRate"] = agg.get("passRate")
        else:
            rm = sdata.get("metrics", {})
            if rm:
                summary = rm.get("recovery", {}).get("summary", {})
                row["avgRecovery_ms"] = summary.get("meanRecovery_ms")
                row["maxRecovery_ms"] = summary.get("maxRecovery_ms")
            else:
                row["avgRecovery_ms"] = None
                row["maxRecovery_ms"] = None

        table.append(row)

    return table


# ─────────────────────────────────────────────────────────────
# Graph commands (Neo4j)
# ─────────────────────────────────────────────────────────────

_neo4j_uri_option = click.option(
    "--neo4j-uri", default="bolt://localhost:7687", envvar="NEO4J_URI",
    help="Neo4j connection URI (default: bolt://localhost:7687)",
)
_neo4j_user_option = click.option(
    "--neo4j-user", default="neo4j", envvar="NEO4J_USER",
    help="Neo4j username",
)
_neo4j_password_option = click.option(
    "--neo4j-password", default="chaosprobe", envvar="NEO4J_PASSWORD",
    help="Neo4j password (default: chaosprobe)",
)


def _get_graph_store(uri, user, password):
    """Create a Neo4jStore, handling missing dependency gracefully."""
    try:
        from chaosprobe.storage.neo4j_store import Neo4jStore
    except ImportError:
        click.echo(
            "Error: Neo4j support not installed.\n"
            "  Install with:  uv pip install chaosprobe[graph]",
            err=True,
        )
        sys.exit(1)
    return Neo4jStore(uri, user, password)


@main.group()
def graph():
    """Neo4j graph commands for topology and blast-radius analysis."""
    pass


@graph.command("status")
@_neo4j_uri_option
@_neo4j_user_option
@_neo4j_password_option
def graph_status(neo4j_uri, neo4j_user, neo4j_password):
    """Check Neo4j connectivity and show node counts."""
    store = _get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
    try:
        counts = store.status()
        click.echo("Neo4j connected ✓")
        click.echo(f"\n  {'Label':<22s} {'Count'}")
        click.echo(f"  {'─' * 32}")
        for label, count in counts.items():
            click.echo(f"  {label:<22s} {count}")
    finally:
        store.close()


@graph.command("sync")
@click.argument("results_dir", required=False, default=None)
@click.option("--namespace", "-n", default="online-boutique", help="Kubernetes namespace")
@_neo4j_uri_option
@_neo4j_user_option
@_neo4j_password_option
def graph_sync(results_dir, namespace, neo4j_uri, neo4j_user, neo4j_password):
    """Bulk-import existing JSON results into Neo4j.

    If RESULTS_DIR is given, imports all JSON files from that directory.
    Otherwise imports from the default results/ directory.
    """
    store = _get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
    try:
        store.ensure_schema()
        store.sync_service_dependencies()

        # Sync cluster topology if we have k8s access
        try:
            mutator = PlacementMutator(namespace)
            nodes_raw = mutator.get_nodes()
            deployments_raw = mutator.get_deployments()
            store.sync_topology(
                [{"name": n.name,
                  "cpu": n.allocatable_cpu_millicores,
                  "memory": n.allocatable_memory_bytes,
                  "control_plane": n.is_control_plane} for n in nodes_raw],
                [{"name": d.name,
                  "namespace": d.namespace,
                  "replicas": d.replicas} for d in deployments_raw],
            )
            click.echo(f"  Synced {len(nodes_raw)} nodes, {len(deployments_raw)} deployments")
        except Exception as e:
            click.echo(f"  Skipping topology sync (no cluster access): {e}", err=True)

        # Find and import result JSON files
        base = Path(results_dir) if results_dir else Path("results")
        if not base.exists():
            click.echo(f"Error: directory '{base}' not found", err=True)
            sys.exit(1)

        json_files = sorted(base.rglob("*.json"))
        # Skip summary.json files — only import per-strategy results
        json_files = [f for f in json_files if f.name != "summary.json"]

        imported = 0
        for jf in json_files:
            try:
                data = json.loads(jf.read_text())
                if "runId" in data:
                    store.sync_run(data)
                    imported += 1
            except Exception as e:
                click.echo(f"  Skipping {jf.name}: {e}", err=True)

        click.echo(f"  Imported {imported} run(s) from {len(json_files)} file(s)")
    finally:
        store.close()


@graph.command("blast-radius")
@click.argument("service_name")
@click.option("--max-hops", default=3, type=int, help="Maximum dependency depth")
@_neo4j_uri_option
@_neo4j_user_option
@_neo4j_password_option
def graph_blast_radius(service_name, max_hops, neo4j_uri, neo4j_user, neo4j_password):
    """Show the blast radius for a service (upstream dependents)."""
    from chaosprobe.graph.analysis import blast_radius_report

    store = _get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
    try:
        report = blast_radius_report(store, service_name, max_hops=max_hops)
        click.echo(f"\nBlast radius for '{service_name}' (max {max_hops} hops):")
        if not report["affectedServices"]:
            click.echo("  No upstream dependents found.")
        else:
            click.echo(f"\n  {'Service':<35s} {'Hops'}")
            click.echo(f"  {'─' * 42}")
            for svc in report["affectedServices"]:
                click.echo(f"  {svc['name']:<35s} {svc['hops']}")
            click.echo(f"\n  Total affected: {report['totalAffected']}")
    finally:
        store.close()


@graph.command("topology")
@click.option("--run-id", required=True, help="Run ID to show topology for")
@_neo4j_uri_option
@_neo4j_user_option
@_neo4j_password_option
def graph_topology(run_id, neo4j_uri, neo4j_user, neo4j_password):
    """Show placement topology for a specific run."""
    store = _get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
    try:
        topo = store.get_topology(run_id)
        if not topo["nodes"] and not topo["unscheduled"]:
            click.echo(f"No topology data found for run {run_id}")
            return

        click.echo(f"\nTopology for run {run_id}:")
        for node_info in topo["nodes"]:
            deps = ", ".join(node_info["deployments"])
            click.echo(f"\n  Node: {node_info['node']}")
            click.echo(f"    Deployments: {deps}")

        if topo["unscheduled"]:
            click.echo(f"\n  Unscheduled: {', '.join(topo['unscheduled'])}")
    finally:
        store.close()


@graph.command("compare")
@click.option("--run-ids", required=True, help="Comma-separated run IDs to compare")
@_neo4j_uri_option
@_neo4j_user_option
@_neo4j_password_option
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def graph_compare(run_ids, neo4j_uri, neo4j_user, neo4j_password, json_output):
    """Compare strategies across runs using graph data."""
    from chaosprobe.graph.analysis import strategy_summary

    ids = [r.strip() for r in run_ids.split(",")]
    store = _get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
    try:
        summary = strategy_summary(store, run_ids=ids)
        if json_output:
            click.echo(json.dumps(summary, indent=2))
            return

        strategies = summary.get("strategies", {})
        if not strategies:
            click.echo("No data found for the given run IDs.")
            return

        click.echo(f"\n  {'Strategy':<18s} {'Runs':<6s} {'Avg Score':<12s} {'Avg Recovery'}")
        click.echo(f"  {'─' * 50}")
        for name, data in strategies.items():
            score_str = f"{data['avgResilienceScore']:.1f}" if data["avgResilienceScore"] is not None else "n/a"
            rec_str = f"{data['avgRecoveryMs']:.0f}ms" if data["avgRecoveryMs"] is not None else "n/a"
            click.echo(f"  {name:<18s} {data['runCount']:<6d} {score_str:<12s} {rec_str}")
    finally:
        store.close()


# ─────────────────────────────────────────────────────────────
# Database query commands
# ─────────────────────────────────────────────────────────────


@main.group()
def query():
    """Query stored experiment results from the database."""
    pass


@query.command("runs")
@click.option("--scenario", "-s", default=None, help="Filter by scenario path")
@click.option("--strategy", default=None, help="Filter by strategy name")
@click.option("--limit", "-l", default=20, type=int, help="Max results to return")
@click.option("--db", default=None, help="Path to database file")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def query_runs(
    scenario: Optional[str],
    strategy: Optional[str],
    limit: int,
    db: Optional[str],
    json_output: bool,
):
    """List experiment runs stored in the database."""
    from chaosprobe.storage.sqlite import SQLiteStore

    store = SQLiteStore(db_path=db)
    runs = store.list_runs(scenario=scenario, strategy=strategy, limit=limit)
    store.close()

    if json_output:
        click.echo(json.dumps(runs, indent=2))
        return

    if not runs:
        click.echo("No runs found.")
        return

    click.echo(f"{'Run ID':<40s} {'Timestamp':<22s} {'Strategy':<14s} {'Verdict':<8s} {'Score'}")
    click.echo("─" * 95)
    for r in runs:
        click.echo(
            f"{r['id']:<40s} {r['timestamp'][:19]:<22s} "
            f"{(r['strategy'] or 'n/a'):<14s} {r['overall_verdict']:<8s} "
            f"{r['resilience_score']:.1f}"
        )


@query.command("compare")
@click.option("--scenario", "-s", default=None, help="Filter by scenario path")
@click.option("--db", default=None, help="Path to database file")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def query_compare(
    scenario: Optional[str],
    db: Optional[str],
    json_output: bool,
):
    """Compare strategies across stored runs."""
    from chaosprobe.storage.sqlite import SQLiteStore

    store = SQLiteStore(db_path=db)
    comparison = store.compare_strategies(scenario=scenario)
    store.close()

    if json_output:
        click.echo(json.dumps(comparison, indent=2))
        return

    strategies = comparison.get("strategies", {})
    if not strategies:
        click.echo("No data found.")
        return

    click.echo(f"\n{'Strategy':<16s} {'Runs':<6s} {'Pass%':<7s} {'Avg Score':<10s} "
               f"{'Avg Rec.':<10s} {'P95 Rec.':<10s}")
    click.echo("─" * 65)
    for name, data in strategies.items():
        avg_rec = f"{data['avgMeanRecovery_ms']:.0f}ms" if data.get("avgMeanRecovery_ms") else "n/a"
        p95_rec = f"{data['avgP95Recovery_ms']:.0f}ms" if data.get("avgP95Recovery_ms") else "n/a"
        click.echo(
            f"{name:<16s} {data['runCount']:<6d} {data['passRate']:<7.0%} "
            f"{data['avgResilienceScore']:<10.1f} {avg_rec:<10s} {p95_rec:<10s}"
        )


@query.command("export")
@click.option("--output", "-o", required=True, type=click.Path(), help="Output CSV file path")
@click.option("--db", default=None, help="Path to database file")
def query_export(output: str, db: Optional[str]):
    """Export all runs to CSV."""
    from chaosprobe.storage.sqlite import SQLiteStore

    store = SQLiteStore(db_path=db)
    path = store.export_csv(output)
    store.close()
    click.echo(f"Exported to {path}")


@query.command("show")
@click.argument("run_id")
@click.option("--db", default=None, help="Path to database file")
def query_show(run_id: str, db: Optional[str]):
    """Show full details of a specific run."""
    from chaosprobe.storage.sqlite import SQLiteStore

    store = SQLiteStore(db_path=db)
    run = store.get_run(run_id)
    store.close()

    if not run:
        click.echo(f"Run '{run_id}' not found.", err=True)
        sys.exit(1)

    click.echo(json.dumps(run, indent=2))


# ─────────────────────────────────────────────────────────────
# Visualization commands
# ─────────────────────────────────────────────────────────────


@main.command("visualize")
@click.option(
    "--db", default=None,
    help="Path to SQLite database (uses default if not set)",
)
@click.option(
    "--summary", "-s", type=click.Path(exists=True), default=None,
    help="Path to a summary.json file (alternative to database)",
)
@click.option(
    "--output-dir", "-o", default="charts",
    help="Directory to save generated charts",
)
@click.option(
    "--scenario", default=None,
    help="Filter by scenario path (database mode only)",
)
def visualize(
    db: Optional[str],
    summary: Optional[str],
    output_dir: str,
    scenario: Optional[str],
):
    """Generate visualization charts from experiment results.

    Can read from the SQLite database or directly from a summary.json file
    produced by the run command.

    \b
    Examples:
      chaosprobe visualize --db results.db -o charts/
      chaosprobe visualize --summary results/20260227-140237/summary.json
      chaosprobe visualize --db results.db --scenario online-boutique
    """
    from chaosprobe.output.visualize import generate_all_charts, generate_from_summary

    if summary:
        click.echo(f"Generating charts from {summary}...")
        try:
            generated = generate_from_summary(summary, output_dir)
        except ImportError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
    else:  # Default to DB mode
        click.echo(f"Generating charts from database...")
        from chaosprobe.storage.sqlite import SQLiteStore
        store = SQLiteStore(db_path=db)
        try:
            generated = generate_all_charts(store, output_dir, scenario=scenario)
        except ImportError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        finally:
            store.close()

    if not generated:
        click.echo("No data available to visualize.")
        return

    click.echo(f"\nGenerated {len(generated)} file(s):")
    for path in generated:
        click.echo(f"  {path}")

    html_files = [p for p in generated if p.endswith(".html")]
    if html_files:
        click.echo(f"\nOpen the report: {html_files[0]}")


if __name__ == "__main__":
    main()
