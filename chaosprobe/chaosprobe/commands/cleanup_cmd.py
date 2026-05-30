"""CLI command: chaosprobe cleanup — remove provisioned resources in a namespace."""

import click

from chaosprobe.provisioner.kubernetes import KubernetesProvisioner


@click.command()
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
