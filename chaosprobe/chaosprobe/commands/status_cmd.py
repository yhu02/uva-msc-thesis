"""CLI command: chaosprobe status — report ChaosProbe + dependency health."""

import json

import click

from chaosprobe.provisioner.setup import LitmusSetup


@click.command()
@click.option("--json", "json_output", is_flag=True, help="Output status as JSON")
def status(json_output: bool):
    """Check the status of ChaosProbe and its dependencies."""
    setup = LitmusSetup(skip_k8s_init=True)
    setup.init_k8s_client()
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
        click.echo(f"  ChaosCenter ready: {'Yes' if prereqs['chaoscenter_ready'] else 'No'}")
        url = setup.get_dashboard_url()
        if url:
            click.echo(f"  Dashboard URL: {url}")

    if prereqs["all_ready"]:
        click.echo("\nAll systems ready!")
    else:
        if not prereqs["cluster_access"]:
            click.echo("\nNo cluster configured. Options:")
            click.echo("  Option A — Local libvirt/Vagrant cluster:")
            click.echo(
                "    1. chaosprobe cluster vagrant init"
                "        (first time only — generates Vagrantfile)"
            )
            click.echo(
                "    2. chaosprobe cluster vagrant setup"
                "       (first time only — installs libvirt/KVM)"
            )
            click.echo("    3. chaosprobe cluster vagrant up          (start VMs)")
            click.echo(
                "    4. chaosprobe cluster vagrant deploy"
                "      (install Kubernetes via Kubespray)"
            )
            click.echo("    5. chaosprobe cluster vagrant kubeconfig  (fetch kubeconfig)")
            click.echo(
                "    6. chaosprobe init"
                "                        (install ChaosProbe infrastructure)"
            )
            click.echo("    7. chaosprobe run" "                         (run experiments)")
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
