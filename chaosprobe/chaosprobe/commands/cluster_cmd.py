"""CLI commands for Kubernetes cluster management (Kubespray & Vagrant)."""

import json
import os
import sys
from pathlib import Path
from typing import Optional

import click
import yaml

from chaosprobe.provisioner.setup import LitmusSetup


@click.group()
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
        click.echo("Error: git is required for Kubespray. Please install it.", err=True)
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
def cluster_destroy(inventory: str, become_pass: Optional[str], force: bool):
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
def cluster_kubeconfig(host: str, user: str, output: Optional[str], ssh_key: Optional[str]):
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
        click.echo("\nTo use this cluster:")
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
@click.option("--control-planes", "-c", default=1, type=int, help="Number of control plane nodes")
@click.option("--workers", "-w", default=2, type=int, help="Number of worker nodes")
@click.option(
    "--memory", "-m", default=None, type=int,
    help="Memory for ALL VMs in MB (overrides --cp-memory/--worker-memory)",
)
@click.option(
    "--cpus", default=None, type=int,
    help="CPUs for ALL VMs (overrides --cp-cpus/--worker-cpus)",
)
@click.option("--cp-memory", default=4096, type=int, help="Memory for control plane VMs in MB")
@click.option("--cp-cpus", default=2, type=int, help="CPUs for control plane VMs")
@click.option("--worker-memory", default=4096, type=int, help="Memory for worker VMs in MB")
@click.option("--worker-cpus", default=2, type=int, help="CPUs for worker VMs")
@click.option("--box", default="generic/ubuntu2204", help="Vagrant box image")
@click.option("--network-prefix", default="192.168.56", help="Network prefix for private IPs")
@click.option("--output", "-o", type=click.Path(), help="Output directory for Vagrantfile")
def vagrant_init(
    name: str,
    control_planes: int,
    workers: int,
    memory: Optional[int],
    cpus: Optional[int],
    cp_memory: int,
    cp_cpus: int,
    worker_memory: int,
    worker_cpus: int,
    box: str,
    network_prefix: str,
    output: Optional[str],
):
    """Initialize a Vagrantfile for local cluster VMs."""
    setup = LitmusSetup(skip_k8s_init=True)
    prereqs = setup.check_prerequisites()

    if not prereqs["vagrant"]:
        click.echo("Error: Vagrant not found. Please install Vagrant first.", err=True)
        sys.exit(1)

    output_dir = Path(output) if output else None

    try:
        vagrant_dir = setup.create_vagrantfile(
            cluster_name=name,
            num_control_planes=control_planes,
            num_workers=workers,
            vm_memory=memory,
            vm_cpus=cpus,
            cp_memory=cp_memory,
            cp_cpus=cp_cpus,
            worker_memory=worker_memory,
            worker_cpus=worker_cpus,
            box_image=box,
            network_prefix=network_prefix,
            output_dir=output_dir,
        )
        click.echo(f"\nVagrantfile created at: {vagrant_dir}")
        click.echo("\nNext steps:")
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

    click.echo("\nLibvirt Status:")
    click.echo(f"  KVM available (/dev/kvm): {'OK' if status['kvm_available'] else 'MISSING'}")
    click.echo(f"  libvirtd installed: {'OK' if status['libvirtd_installed'] else 'MISSING'}")
    click.echo(f"  libvirtd running: {'OK' if status['libvirtd_running'] else 'NOT RUNNING'}")
    click.echo(f"  User in libvirt/kvm groups: {'OK' if status['user_in_groups'] else 'MISSING'}")
    click.echo(
        f"  vagrant-libvirt plugin: {'OK' if status['vagrant_libvirt_plugin'] else 'MISSING'}"
    )

    if status["all_ready"]:
        click.echo(click.style("\nLibvirt is fully configured!", fg="green"))
        return

    if check_only:
        click.echo(click.style("\nLibvirt is not fully configured.", fg="yellow"))
        click.echo("Run 'chaosprobe cluster vagrant setup' to install missing components.")
        sys.exit(1)

    if not status["kvm_available"]:
        click.echo(click.style("\nError: KVM is not available.", fg="red"), err=True)
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
    "--dir",
    "-d",
    "vagrant_dir",
    type=click.Path(exists=True),
    help="Vagrant directory",
)
def vagrant_up(name: str, vagrant_dir: Optional[str]):
    """Start Vagrant VMs using libvirt."""
    setup = LitmusSetup(skip_k8s_init=True)

    if vagrant_dir:
        vdir = Path(vagrant_dir)
    else:
        vdir = setup.VAGRANT_DIR / name

    if not (vdir / "Vagrantfile").exists():
        click.echo(f"Error: No Vagrantfile found at {vdir}", err=True)
        sys.exit(1)

    click.echo("Checking libvirt configuration...")
    libvirt_status = setup._check_libvirt()
    if not libvirt_status["all_ready"]:
        click.echo(click.style("\nLibvirt is not fully configured.", fg="yellow"))
        click.echo("\nRun 'chaosprobe cluster vagrant setup' to install libvirt.")
        sys.exit(1)
    click.echo("  Libvirt: OK")

    click.echo(f"\nStarting Vagrant VMs from {vdir}...")

    try:
        setup.vagrant_up(vdir, provider="libvirt")
        click.echo("\nVMs are running. Next steps:")
        click.echo(f"  Deploy K8s: chaosprobe cluster vagrant deploy --name {name}")
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
        click.echo("Error: git is required for Kubespray. Please install it.", err=True)
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
        click.echo("\nCluster deployed successfully!")
        click.echo(f"Inventory: {inventory_dir}")

        hosts = setup.get_vagrant_ssh_config(vdir)
        cp_hosts = [h for h in hosts if "control_plane" in h.get("roles", [])]
        if cp_hosts:
            cp_ip = cp_hosts[0]["ip"]
            cp_user = cp_hosts[0]["ansible_user"]
            click.echo("\nTo get kubeconfig:")
            click.echo(f"  chaosprobe cluster kubeconfig --host {cp_ip} --user {cp_user}")
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
        kubeconfig_path = setup.vagrant_fetch_kubeconfig(vdir, output_path=output_path)
        click.echo("\nTo use this cluster:")
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

    os.chdir(vdir)
    os.execvp("vagrant", ["vagrant", "ssh", vm_name])
