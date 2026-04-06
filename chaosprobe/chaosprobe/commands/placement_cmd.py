"""CLI commands for pod placement strategy management."""

import json
import sys
from pathlib import Path
from typing import Optional

import click

from chaosprobe.placement.mutator import PlacementMutator
from chaosprobe.placement.strategy import PlacementStrategy


@click.group()
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
         chaosprobe run scenarios/online-boutique/placement-experiment.yaml --output-dir results/
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
    "--namespace",
    "-n",
    default="chaosprobe-test",
    help="Namespace containing deployments",
)
@click.option(
    "--target-node",
    "-t",
    default=None,
    help="For 'colocate': pin to this specific node",
)
@click.option(
    "--seed",
    "-s",
    default=None,
    type=int,
    help="For 'random': seed for reproducible assignments",
)
@click.option(
    "--deployments",
    "-d",
    default=None,
    help="Comma-separated list of deployment names (default: all in namespace)",
)
@click.option(
    "--no-wait",
    is_flag=True,
    help="Don't wait for rollouts to complete",
)
@click.option(
    "--timeout",
    default=300,
    type=int,
    help="Timeout in seconds for rollout completion",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
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
        mem_gib = node.allocatable_memory_bytes / (1024**3)
        click.echo(f"  {node.name}: {cpu_cores:.1f} CPU, {mem_gib:.1f} GiB RAM")

    # Show target deployments
    deps = mutator.get_deployments()
    if dep_list:
        deps = [d for d in deps if d.name in dep_list]
    click.echo(f"\nDeployments ({len(deps)}):")
    for d in deps:
        cpu = d.cpu_request_millicores
        mem_mib = d.memory_request_bytes / (1024**2)
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
    click.echo("\nPlacement applied:")
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
    "--namespace",
    "-n",
    default="chaosprobe-test",
    help="Namespace containing deployments",
)
@click.option(
    "--deployments",
    "-d",
    default=None,
    help="Comma-separated list of deployment names (default: all managed)",
)
@click.option(
    "--no-wait",
    is_flag=True,
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
    "--namespace",
    "-n",
    default="chaosprobe-test",
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
        mem_gib = node.allocatable_memory_bytes / (1024**3)
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
