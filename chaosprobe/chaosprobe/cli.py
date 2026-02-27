"""ChaosProbe CLI - Main entry point for the chaos testing framework."""

import copy
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        click.echo("  1. Use 'chaosprobe cluster create' to deploy with Kubespray")
        click.echo("  2. Configure kubectl to connect to an existing cluster")
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
@click.option("--output", "-o", type=click.Path(), help="Output file for results JSON")
@click.option(
    "--namespace", "-n", default=None, help="Override namespace (default: from scenario)"
)
@click.option(
    "--timeout",
    "-t",
    default=300,
    help="Timeout in seconds for experiment completion",
)
@click.option(
    "--no-auto-setup",
    is_flag=True,
    help="Disable automatic LitmusChaos installation",
)
def run(
    scenario_path: str,
    output: Optional[str],
    namespace: Optional[str],
    timeout: int,
    no_auto_setup: bool,
):
    """Run a chaos scenario and generate AI-consumable output.

    SCENARIO_PATH is a directory containing Kubernetes manifests and
    ChaosEngine YAML files, or a single YAML file.

    \b
    Example scenario directory:
      scenarios/nginx-pod-delete/
        deployment.yaml     # K8s Deployment
        service.yaml        # K8s Service
        experiment.yaml     # ChaosEngine YAML

    ChaosProbe automatically:
    - Classifies files by kind (ChaosEngine vs regular K8s resources)
    - Deploys K8s manifests to the cluster
    - Runs ChaosEngine experiments
    - Generates structured AI-consumable output
    """
    click.echo(f"Loading scenario from {scenario_path}...")

    try:
        scenario = load_scenario(scenario_path)
        validate_scenario(scenario)
    except Exception as e:
        click.echo(f"Error loading scenario: {e}", err=True)
        sys.exit(1)

    # Override namespace if specified
    if namespace:
        scenario["namespace"] = namespace

    target_namespace = scenario.get("namespace", "default")
    experiment_types = _extract_experiment_types(scenario)

    click.echo(f"  Manifests: {len(scenario.get('manifests', []))} files")
    click.echo(f"  Experiments: {len(scenario.get('experiments', []))} ChaosEngine(s)")
    click.echo(f"  Experiment types: {', '.join(experiment_types) or 'none'}")
    click.echo(f"  Namespace: {target_namespace}")

    # Phase 0: Ensure LitmusChaos is set up
    click.echo("\n[0/4] Checking prerequisites...")
    if not ensure_litmus_setup(
        target_namespace, experiment_types, auto_setup=not no_auto_setup
    ):
        sys.exit(1)

    # Phase 1: Deploy K8s manifests
    click.echo("\n[1/4] Deploying manifests...")
    provisioner = KubernetesProvisioner(target_namespace)
    try:
        provisioner.provision(scenario.get("manifests", []))
        click.echo("  Manifests deployed successfully")
    except Exception as e:
        click.echo(f"  Error deploying manifests: {e}", err=True)
        sys.exit(1)

    # Phase 2: Run chaos experiments
    click.echo("\n[2/4] Running chaos experiments...")
    runner = ChaosRunner(target_namespace, timeout=timeout)
    try:
        runner.run_experiments(scenario.get("experiments", []))
        click.echo("  Experiments completed")
    except Exception as e:
        click.echo(f"  Error running experiments: {e}", err=True)
        # Continue to collect results even if experiments fail

    # Phase 3: Collect results
    click.echo("\n[3/4] Collecting results...")
    collector = ResultCollector(target_namespace)
    try:
        executed = runner.get_executed_experiments()
        results = collector.collect(executed)
        click.echo(f"  Collected results from {len(results)} experiments")
    except Exception as e:
        click.echo(f"  Error collecting results: {e}", err=True)
        results = []

    # Phase 4: Generate AI output
    click.echo("\n[4/4] Generating AI output...")
    generator = OutputGenerator(scenario, results)
    output_data = generator.generate()

    if output:
        output_path = Path(output)
        output_path.write_text(json.dumps(output_data, indent=2))
        click.echo(f"  Output written to {output}")
    else:
        click.echo(json.dumps(output_data, indent=2))

    # Print summary
    summary = output_data["summary"]
    click.echo(f"\n{'=' * 50}")
    click.echo("Summary:")
    click.echo(f"  Verdict: {summary['overallVerdict']}")
    click.echo(f"  Resilience Score: {summary['resilienceScore']:.1f}")
    click.echo(
        f"  Experiments: {summary['passed']}/{summary['totalExperiments']} passed"
    )

    if output:
        click.echo(f"\n  Output: {output}")


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
# run-all — automated full experiment matrix
# ─────────────────────────────────────────────────────────────


@main.command("run-all")
@click.option(
    "--namespace", "-n", default="online-boutique",
    help="Namespace containing the application",
)
@click.option(
    "--output-dir", "-o", default=None,
    help="Directory for results (default: results/<timestamp>)",
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
def run_all(
    namespace: str,
    output_dir: Optional[str],
    strategies: str,
    timeout: int,
    seed: int,
    settle_time: int,
    no_auto_setup: bool,
    experiment: str,
    iterations: int,
):
    """Run all placement experiments automatically.

    Iterates through placement strategies (baseline, colocate, spread,
    antagonistic, random), applies each placement, runs the shared
    experiment, collects results (including pod recovery metrics), and
    saves everything to a timestamped results directory.

    \b
    Example:
      chaosprobe run-all -n online-boutique
      chaosprobe run-all -n online-boutique -s colocate,spread
      chaosprobe run-all -n online-boutique -o results/my-run
      chaosprobe run-all -n online-boutique -i 3  # 3 iterations per strategy
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
    if output_dir:
        results_dir = Path(output_dir)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        results_dir = Path("results") / ts
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
    click.echo("")

    # Extract target deployment from experiment spec for recovery metrics
    target_deployment = _extract_target_deployment(shared_scenario)

    overall_results: Dict[str, Any] = {
        "runId": f"run-all-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "namespace": namespace,
        "iterations": iterations,
        "strategies": {},
    }

    total = len(strategy_list)
    passed = 0
    failed = 0

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
                    click.echo("    Ready.")
                else:
                    click.echo(f"\n{step_label}: Skipping settle time.")

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

                experiment_start = time.time()
                runner = ChaosRunner(namespace, timeout=timeout)
                runner.run_experiments(scenario.get("experiments", []))
                experiment_end = time.time()

                # Collect results
                collector = ResultCollector(namespace)
                executed = runner.get_executed_experiments()
                results = collector.collect(executed)

                # Collect recovery metrics
                recovery = metrics_collector.collect(
                    deployment_name=target_deployment,
                    since_time=experiment_start,
                    until_time=experiment_end,
                )

                # Generate output
                generator = OutputGenerator(scenario, results, metrics=recovery)
                output_data = generator.generate()

                # Save result file
                if iterations > 1:
                    result_file = results_dir / f"{strategy_name}-iter-{iter_num}.json"
                else:
                    result_file = results_dir / f"{strategy_name}.json"
                result_file.write_text(json.dumps(output_data, indent=2))

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
    click.echo("")


def _extract_target_deployment(scenario: Dict[str, Any]) -> str:
    """Extract the target deployment name from experiment appinfo."""
    for exp in scenario.get("experiments", []):
        appinfo = exp.get("spec", {}).get("spec", {}).get("appinfo", {})
        applabel = appinfo.get("applabel", "")
        if applabel.startswith("app="):
            return applabel.split("=", 1)[1]
    return "checkoutservice"


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
            "resilienceScore": exp.get("resilienceScore", 0),
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


if __name__ == "__main__":
    main()
