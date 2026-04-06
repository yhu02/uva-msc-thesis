"""CLI commands for ChaosCenter dashboard management."""

import json
import sys

import click

from chaosprobe.provisioner.setup import LitmusSetup


@click.group()
def dashboard():
    """Manage the ChaosCenter dashboard.

    Install and interact with the official LitmusChaos web UI
    (ChaosCenter) for visualising running experiments, resilience
    scores, and infrastructure status.

    \b
    Quick start:
      chaosprobe dashboard install
      chaosprobe dashboard open
      chaosprobe dashboard connect -n <namespace>
    """
    pass


@dashboard.command("install")
@click.option(
    "--service-type",
    type=click.Choice(["NodePort", "LoadBalancer"], case_sensitive=False),
    default="NodePort",
    help="Service type for the frontend (default: NodePort)",
)
@click.option("--timeout", default=300, help="Timeout in seconds (default: 300)")
def dashboard_install(service_type: str, timeout: int):
    """Install ChaosCenter dashboard on the current cluster."""
    setup = LitmusSetup()

    if setup.is_chaoscenter_installed():
        click.echo("ChaosCenter is already installed.")
        url = setup.get_dashboard_url()
        if url:
            click.echo(f"Dashboard URL: {url}")
        return

    click.echo("Installing ChaosCenter dashboard...")
    try:
        ok = setup.install_chaoscenter(
            service_type=service_type, wait=True, timeout=timeout,
        )
        if ok:
            click.echo("ChaosCenter installed successfully!")
            url = setup.get_dashboard_url()
            if url:
                click.echo(f"\nDashboard URL: {url}")
            click.echo(
                f"\nDefault credentials:  "
                f"username={setup.CHAOSCENTER_DEFAULT_USER}  "
                f"password={setup.CHAOSCENTER_DEFAULT_PASS}"
            )
        else:
            click.echo("ChaosCenter installation timed out.", err=True)
            sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@dashboard.command("status")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def dashboard_status(json_output: bool):
    """Show ChaosCenter pod health and dashboard URL."""
    setup = LitmusSetup()
    info = setup.get_chaoscenter_status()

    if json_output:
        click.echo(json.dumps(info, indent=2))
        return

    if not info["installed"]:
        click.echo("ChaosCenter is not installed.")
        click.echo("Run 'chaosprobe dashboard install' to install it.")
        return

    click.echo("ChaosCenter Status:")
    click.echo("  Installed: Yes")
    click.echo(f"  Ready: {'Yes' if info['ready'] else 'No'}")
    if info["frontend_url"]:
        click.echo(f"  Dashboard URL: {info['frontend_url']}")

    if info["pods"]:
        click.echo("\n  Pods:")
        for pod in info["pods"]:
            ready_str = "Ready" if pod["ready"] else pod["phase"]
            click.echo(f"    {pod['name']}: {ready_str}")


@dashboard.command("open")
def dashboard_open():
    """Print the ChaosCenter dashboard URL (or start port-forward)."""
    setup = LitmusSetup()

    if not setup.is_chaoscenter_installed():
        click.echo("ChaosCenter is not installed.", err=True)
        click.echo("Run 'chaosprobe dashboard install' first.")
        sys.exit(1)

    url = setup.get_dashboard_url()
    if url:
        click.echo(url)
    else:
        click.echo("Cannot determine external URL. Starting port-forward...")
        click.echo(
            f"  kubectl port-forward svc/{setup.CHAOSCENTER_FRONTEND_SVC} "
            f"{setup.CHAOSCENTER_FRONTEND_PORT}:{setup.CHAOSCENTER_FRONTEND_PORT} "
            f"-n {setup.LITMUS_NAMESPACE}"
        )
        click.echo(f"\nThen open: http://localhost:{setup.CHAOSCENTER_FRONTEND_PORT}")


@dashboard.command("connect")
@click.option(
    "--namespace", "-n", required=True, help="Namespace to register as chaos infrastructure",
)
@click.option("--username", default="", help="ChaosCenter username (default: admin)")
@click.option("--password", default="", help="ChaosCenter password (default: litmus)")
def dashboard_connect(namespace: str, username: str, password: str):
    """Register a namespace as chaos infrastructure in ChaosCenter."""
    setup = LitmusSetup()

    if not setup.is_chaoscenter_installed():
        click.echo("ChaosCenter is not installed.", err=True)
        click.echo("Run 'chaosprobe dashboard install' first.")
        sys.exit(1)

    click.echo(f"Connecting namespace '{namespace}' to ChaosCenter...")
    try:
        result = setup.connect_infrastructure(
            namespace=namespace, username=username, password=password,
        )
        click.echo(f"Infrastructure registered: {result['infra_id']}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@dashboard.command("credentials")
def dashboard_credentials():
    """Show default ChaosCenter login credentials."""
    click.echo(f"Username: {LitmusSetup.CHAOSCENTER_DEFAULT_USER}")
    click.echo(f"Password: {LitmusSetup.CHAOSCENTER_DEFAULT_PASS}")
    click.echo("\nChange the password after first login for production use.")
