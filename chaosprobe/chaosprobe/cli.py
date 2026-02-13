"""ChaosProbe CLI - Main entry point for the chaos testing framework."""

import json
import sys
from pathlib import Path
from typing import Optional

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


if __name__ == "__main__":
    main()
