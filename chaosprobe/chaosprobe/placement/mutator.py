"""Placement mutator — applies and clears pod placement constraints.

Uses the Kubernetes API to:
- Patch deployments with nodeSelector to pin pods to specific nodes
- Clear placement constraints to restore default scheduling
- Query current placement state
"""

import time
from typing import Any, Dict, List, Optional

import click
from kubernetes import client
from kubernetes.client.rest import ApiException

from chaosprobe.k8s import ensure_k8s_config
from chaosprobe.metrics.resources import parse_cpu_quantity, parse_memory_quantity
from chaosprobe.orchestrator.preflight import LITMUS_INFRA_DEPLOYMENTS
from chaosprobe.placement.strategy import (
    DeploymentInfo,
    NodeAssignment,
    NodeInfo,
    PlacementStrategy,
    compute_assignments,
)

# Built-in Kubernetes label for targeting nodes by hostname
PLACEMENT_LABEL_KEY = "kubernetes.io/hostname"
# Annotation to track which deployments are managed by ChaosProbe placement
MANAGED_ANNOTATION = "chaosprobe.io/placement-strategy"
# Legacy label from previous versions (cleaned up on clear)
_LEGACY_LABEL = "chaosprobe.io/placement-zone"


class PlacementMutator:
    """Applies and clears pod placement constraints on Kubernetes deployments."""

    def __init__(self, namespace: str):
        """Initialise with the target namespace.

        Args:
            namespace: Kubernetes namespace containing the deployments.
        """
        self.namespace = namespace

        ensure_k8s_config()

        self.core_api = client.CoreV1Api()
        self.apps_api = client.AppsV1Api()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_nodes(self) -> List[NodeInfo]:
        """Get all cluster nodes with scheduling information.

        Returns:
            List of NodeInfo with allocatable resources and taints.
        """
        nodes_resp = self.core_api.list_node()
        result: List[NodeInfo] = []

        for node in nodes_resp.items:
            name = node.metadata.name
            labels = dict(node.metadata.labels or {})

            # Parse allocatable resources
            alloc = node.status.allocatable or {}
            cpu_str = alloc.get("cpu", "0")
            mem_str = alloc.get("memory", "0")

            cpu_m = int(parse_cpu_quantity(cpu_str))
            mem_b = parse_memory_quantity(mem_str)

            # Check Ready condition
            ready = False
            for cond in node.status.conditions or []:
                if cond.type == "Ready" and cond.status == "True":
                    ready = True
                    break

            # Parse taints
            taints = []
            for taint in node.spec.taints or []:
                taints.append(
                    {
                        "key": taint.key,
                        "value": taint.value or "",
                        "effect": taint.effect,
                    }
                )

            result.append(
                NodeInfo(
                    name=name,
                    labels=labels,
                    allocatable_cpu_millicores=cpu_m,
                    allocatable_memory_bytes=mem_b,
                    conditions_ready=ready,
                    taints=taints,
                )
            )

        return result

    def get_deployments(self) -> List[DeploymentInfo]:
        """Get all application deployments in the namespace with resource info.

        Litmus chaos infrastructure deployments (chaos-operator,
        subscriber, etc.) are excluded so placement strategies never
        move them.

        Returns:
            List of DeploymentInfo for placement decisions.
        """
        deps = self.apps_api.list_namespaced_deployment(self.namespace)
        result: List[DeploymentInfo] = []

        for dep in deps.items:
            name = dep.metadata.name

            # Never touch chaos infrastructure components
            if name in LITMUS_INFRA_DEPLOYMENTS:
                continue

            replicas = dep.spec.replicas or 1

            # Aggregate resource requests from all containers
            total_cpu = 0
            total_mem = 0
            for container in dep.spec.template.spec.containers or []:
                if container.resources and container.resources.requests:
                    cpu_str = container.resources.requests.get("cpu", "0")
                    mem_str = container.resources.requests.get("memory", "0")
                    total_cpu += int(parse_cpu_quantity(cpu_str))
                    total_mem += parse_memory_quantity(mem_str)

            # Find current node (from first running pod)
            current_node = self._get_pod_node(name)

            result.append(
                DeploymentInfo(
                    name=name,
                    replicas=replicas,
                    cpu_request_millicores=total_cpu,
                    memory_request_bytes=total_mem,
                    current_node=current_node,
                    namespace=self.namespace,
                )
            )

        return result

    def apply_strategy(
        self,
        strategy: PlacementStrategy,
        target_node: Optional[str] = None,
        seed: Optional[int] = None,
        deployments: Optional[List[str]] = None,
        wait: bool = True,
        timeout: int = 300,
    ) -> NodeAssignment:
        """Compute and apply a placement strategy to all deployments.

        Args:
            strategy: The placement strategy to use.
            target_node: For COLOCATE, pin to this specific node.
            seed: Random seed for RANDOM strategy.
            deployments: Optional list of deployment names to target.
                         If None, targets all deployments in the namespace.
            wait: Wait for rollouts to complete after applying.
            timeout: Timeout in seconds for rollout completion.

        Returns:
            The computed NodeAssignment.
        """
        nodes = self.get_nodes()
        all_deps = self.get_deployments()

        if deployments:
            dep_names = set(deployments)
            all_deps = [d for d in all_deps if d.name in dep_names]

        if not all_deps:
            raise ValueError(f"No deployments found in namespace '{self.namespace}'")

        assignment = compute_assignments(
            strategy=strategy,
            deployments=all_deps,
            nodes=nodes,
            target_node=target_node,
            seed=seed,
        )

        self._apply_assignment(assignment)

        if wait:
            self._wait_for_rollouts(list(assignment.assignments.keys()), timeout)

        return assignment

    def clear_placement(
        self,
        deployments: Optional[List[str]] = None,
        wait: bool = True,
        timeout: int = 300,
    ) -> List[str]:
        """Remove all ChaosProbe placement constraints.

        Clears any deployment that has the managed annotation OR a stale
        ``kubernetes.io/hostname`` nodeSelector (defensive — handles the
        case where a previous run was interrupted before cleanup).

        Args:
            deployments: Optional list of deployment names to clear.
                         If None, clears all managed deployments in the namespace.
            wait: Wait for rollouts to complete.
            timeout: Timeout for rollout completion.

        Returns:
            List of deployment names that were cleared.
        """
        all_deps = self.apps_api.list_namespaced_deployment(self.namespace)
        cleared = []

        for dep in all_deps.items:
            name = dep.metadata.name

            if deployments and name not in deployments:
                continue

            # Skip Litmus infrastructure deployments
            if name in LITMUS_INFRA_DEPLOYMENTS:
                continue

            annotations = dep.metadata.annotations or {}
            node_selector = dep.spec.template.spec.node_selector or {}
            has_annotation = MANAGED_ANNOTATION in annotations
            has_node_pin = PLACEMENT_LABEL_KEY in node_selector

            if not has_annotation and not has_node_pin:
                continue

            # Remove kubernetes.io/hostname from nodeSelector
            if has_node_pin:
                del node_selector[PLACEMENT_LABEL_KEY]

            patch = {
                "metadata": {
                    "annotations": {MANAGED_ANNOTATION: None} if has_annotation else {},
                },
                "spec": {
                    "strategy": {
                        "type": "RollingUpdate",
                        "rollingUpdate": {"maxSurge": "25%", "maxUnavailable": "25%"},
                    },
                    "template": {
                        "spec": {
                            "nodeSelector": node_selector if node_selector else None,
                        }
                    },
                },
            }
            self.apps_api.patch_namespaced_deployment(name, self.namespace, patch)
            cleared.append(name)
            click.echo(f"  Cleared placement for: {name}")

        # Clean up legacy labels from previous ChaosProbe versions
        self._cleanup_legacy_labels()

        if wait and cleared:
            self._wait_for_rollouts(cleared, timeout)

        return cleared

    def get_current_placement(self) -> Dict[str, Any]:
        """Get the current placement state of all deployments.

        Returns:
            Dictionary with deployment placement information.
        """
        deps = self.apps_api.list_namespaced_deployment(self.namespace)
        placement: Dict[str, Any] = {}

        for dep in deps.items:
            name = dep.metadata.name
            annotations = dep.metadata.annotations or {}
            node_selector = dep.spec.template.spec.node_selector or {}

            strategy = annotations.get(MANAGED_ANNOTATION)
            target_node = node_selector.get(PLACEMENT_LABEL_KEY)

            current_node = self._get_pod_node(name)

            placement[name] = {
                "strategy": strategy,
                "targetNode": target_node,
                "currentNode": current_node,
                "managed": strategy is not None,
            }

        return placement

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_assignment(self, assignment: NodeAssignment) -> None:
        """Apply a NodeAssignment by patching deployments with nodeSelector.

        Uses the built-in kubernetes.io/hostname label — no custom node
        labeling needed.  Patches are applied in parallel for speed.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        items = list(assignment.assignments.items())
        with ThreadPoolExecutor(max_workers=min(len(items), 8)) as executor:
            futures = {
                executor.submit(
                    self._patch_deployment_placement, dep_name, node_name, assignment.strategy.value
                ): dep_name
                for dep_name, node_name in items
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    dep = futures[future]
                    click.echo(f"  WARNING: Failed to patch '{dep}': {e}")

    def _cleanup_legacy_labels(self) -> None:
        """Remove legacy chaosprobe.io/placement-zone labels from nodes."""
        try:
            nodes = self.core_api.list_node()
            for node in nodes.items:
                labels = node.metadata.labels or {}
                if _LEGACY_LABEL in labels:
                    patch = {"metadata": {"labels": {_LEGACY_LABEL: None}}}
                    try:
                        self.core_api.patch_node(node.metadata.name, patch)
                    except ApiException:
                        pass
        except ApiException:
            pass

    def _patch_deployment_placement(
        self, deployment_name: str, node_name: str, strategy_name: str
    ) -> None:
        """Patch a deployment with nodeSelector for placement.

        Temporarily switches the deployment to ``Recreate`` strategy
        so the old pod is terminated before the new one is created.
        This prevents stuck rollouts when ``maxUnavailable`` rounds to 0
        for single-replica deployments using ``RollingUpdate``.
        """
        # First switch strategy to Recreate (must remove rollingUpdate field)
        try:
            dep = self.apps_api.read_namespaced_deployment(deployment_name, self.namespace)
            current_strategy = dep.spec.strategy
            if current_strategy and current_strategy.type != "Recreate":
                strategy_patch = {
                    "spec": {
                        "strategy": {"type": "Recreate", "rollingUpdate": None},
                    }
                }
                self.apps_api.patch_namespaced_deployment(
                    deployment_name, self.namespace, strategy_patch
                )
        except ApiException:
            pass  # proceed with nodeSelector patch anyway

        # Now apply nodeSelector
        patch = {
            "metadata": {
                "annotations": {MANAGED_ANNOTATION: strategy_name},
            },
            "spec": {
                "template": {
                    "spec": {
                        "nodeSelector": {PLACEMENT_LABEL_KEY: node_name},
                    }
                },
            },
        }
        try:
            self.apps_api.patch_namespaced_deployment(deployment_name, self.namespace, patch)
            click.echo(f"  Pinned '{deployment_name}' -> node '{node_name}'")
        except ApiException as e:
            click.echo(f"  WARNING: Failed to patch '{deployment_name}': {e.reason}")

    def _get_pod_node(self, deployment_name: str) -> Optional[str]:
        """Get the node name where a deployment's pod is running."""
        try:
            pods = self.core_api.list_namespaced_pod(
                self.namespace,
                label_selector=f"app={deployment_name}",
            )
            for pod in pods.items:
                if pod.spec.node_name:
                    return pod.spec.node_name
        except ApiException:
            pass
        return None

    def _wait_for_rollouts(self, deployment_names: List[str], timeout: int) -> None:
        """Wait for deployments to finish rolling out.

        Checks that the deployment controller has observed the latest
        generation AND that all replicas are updated and ready.
        This prevents false positives from stale pod status.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        click.echo(f"  Waiting for {len(deployment_names)} rollout(s) (timeout: {timeout}s)...")
        start = time.time()

        # Brief pause to let the controller start processing the patch
        time.sleep(2)

        pending = set(deployment_names)
        while pending and (time.time() - start) < timeout:
            still_pending = set()
            # Check all pending deployments in parallel
            with ThreadPoolExecutor(max_workers=min(len(pending), 8)) as executor:
                futures = {
                    executor.submit(
                        self.apps_api.read_namespaced_deployment, name, self.namespace
                    ): name
                    for name in pending
                }
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        dep = future.result()
                        desired = dep.spec.replicas or 1
                        generation = dep.metadata.generation or 0
                        observed = (
                            dep.status.observed_generation
                            if dep.status and dep.status.observed_generation
                            else 0
                        )
                        ready = (dep.status.ready_replicas or 0) if dep.status else 0
                        updated = (dep.status.updated_replicas or 0) if dep.status else 0
                        available = (dep.status.available_replicas or 0) if dep.status else 0

                        if (
                            observed >= generation
                            and updated >= desired
                            and ready >= desired
                            and available >= desired
                        ):
                            elapsed = int(time.time() - start)
                            click.echo(f"    {name}: ready ({elapsed}s)")
                        else:
                            still_pending.add(name)
                    except ApiException:
                        still_pending.add(name)

            pending = still_pending
            if pending:
                time.sleep(3)

        if pending:
            elapsed = int(time.time() - start)
            for name in pending:
                click.echo(f"    WARNING: {name}: not ready after {elapsed}s")
