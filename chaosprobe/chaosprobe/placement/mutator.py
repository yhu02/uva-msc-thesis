"""Placement mutator — applies and clears pod placement constraints.

Uses the Kubernetes API to:
- Label nodes for placement targeting
- Patch deployments with nodeSelector to pin pods to specific nodes
- Clear placement constraints to restore default scheduling
- Query current placement state
"""

import json
import time
from typing import Any, Dict, List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from chaosprobe.placement.strategy import (
    DeploymentInfo,
    NodeAssignment,
    NodeInfo,
    PlacementStrategy,
    compute_assignments,
)


# Label used on nodes and in nodeSelector to control placement
PLACEMENT_LABEL_KEY = "chaosprobe.io/placement-zone"
MANAGED_ANNOTATION = "chaosprobe.io/placement-strategy"


class PlacementMutator:
    """Applies and clears pod placement constraints on Kubernetes deployments."""

    def __init__(self, namespace: str):
        """Initialise with the target namespace.

        Args:
            namespace: Kubernetes namespace containing the deployments.
        """
        self.namespace = namespace

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

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

            cpu_m = self._parse_cpu(cpu_str)
            mem_b = self._parse_memory(mem_str)

            # Check Ready condition
            ready = False
            for cond in node.status.conditions or []:
                if cond.type == "Ready" and cond.status == "True":
                    ready = True
                    break

            # Parse taints
            taints = []
            for taint in node.spec.taints or []:
                taints.append({
                    "key": taint.key,
                    "value": taint.value or "",
                    "effect": taint.effect,
                })

            result.append(NodeInfo(
                name=name,
                labels=labels,
                allocatable_cpu_millicores=cpu_m,
                allocatable_memory_bytes=mem_b,
                conditions_ready=ready,
                taints=taints,
            ))

        return result

    def get_deployments(self) -> List[DeploymentInfo]:
        """Get all deployments in the namespace with resource info.

        Returns:
            List of DeploymentInfo for placement decisions.
        """
        deps = self.apps_api.list_namespaced_deployment(self.namespace)
        result: List[DeploymentInfo] = []

        for dep in deps.items:
            name = dep.metadata.name
            replicas = dep.spec.replicas or 1

            # Aggregate resource requests from all containers
            total_cpu = 0
            total_mem = 0
            for container in dep.spec.template.spec.containers or []:
                if container.resources and container.resources.requests:
                    cpu_str = container.resources.requests.get("cpu", "0")
                    mem_str = container.resources.requests.get("memory", "0")
                    total_cpu += self._parse_cpu(cpu_str)
                    total_mem += self._parse_memory(mem_str)

            # Find current node (from first running pod)
            current_node = self._get_pod_node(name)

            result.append(DeploymentInfo(
                name=name,
                replicas=replicas,
                cpu_request_millicores=total_cpu,
                memory_request_bytes=total_mem,
                current_node=current_node,
            ))

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

        self._apply_assignment(assignment, nodes)

        if wait:
            self._wait_for_rollouts(
                list(assignment.assignments.keys()), timeout
            )

        return assignment

    def apply_assignment(
        self,
        assignment: NodeAssignment,
        wait: bool = True,
        timeout: int = 300,
    ) -> None:
        """Apply a pre-computed NodeAssignment.

        Args:
            assignment: The assignment to apply.
            wait: Wait for rollouts to complete.
            timeout: Timeout for rollout completion.
        """
        nodes = self.get_nodes()
        self._apply_assignment(assignment, nodes)

        if wait:
            self._wait_for_rollouts(
                list(assignment.assignments.keys()), timeout
            )

    def clear_placement(
        self,
        deployments: Optional[List[str]] = None,
        wait: bool = True,
        timeout: int = 300,
    ) -> List[str]:
        """Remove all ChaosProbe placement constraints.

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

            annotations = dep.metadata.annotations or {}
            if MANAGED_ANNOTATION not in annotations:
                continue

            # Remove nodeSelector placement label
            node_selector = dep.spec.template.spec.node_selector or {}
            if PLACEMENT_LABEL_KEY in node_selector:
                del node_selector[PLACEMENT_LABEL_KEY]

                patch = {
                    "metadata": {
                        "annotations": {MANAGED_ANNOTATION: None},
                    },
                    "spec": {
                        "template": {
                            "spec": {
                                "nodeSelector": node_selector if node_selector else None,
                            }
                        }
                    },
                }
                self.apps_api.patch_namespaced_deployment(
                    name, self.namespace, patch
                )
                cleared.append(name)
                print(f"  Cleared placement for: {name}")

        # Clean up node labels
        self._cleanup_node_labels()

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
            target_zone = node_selector.get(PLACEMENT_LABEL_KEY)

            current_node = self._get_pod_node(name)

            placement[name] = {
                "strategy": strategy,
                "targetZone": target_zone,
                "currentNode": current_node,
                "managed": strategy is not None,
            }

        return placement

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_assignment(
        self, assignment: NodeAssignment, nodes: List[NodeInfo]
    ) -> None:
        """Apply a NodeAssignment by labelling nodes and patching deployments."""
        # 1. Label target nodes with placement zones
        node_zones: Dict[str, str] = {}
        for dep_name, node_name in assignment.assignments.items():
            if node_name not in node_zones:
                zone = f"zone-{node_name}"
                node_zones[node_name] = zone

        for node_name, zone in node_zones.items():
            self._label_node(node_name, PLACEMENT_LABEL_KEY, zone)

        # 2. Patch each deployment with nodeSelector
        for dep_name, node_name in assignment.assignments.items():
            zone = node_zones[node_name]
            self._patch_deployment_placement(
                dep_name, zone, assignment.strategy.value
            )

    def _label_node(self, node_name: str, label_key: str, label_value: str) -> None:
        """Add a label to a node."""
        patch = {"metadata": {"labels": {label_key: label_value}}}
        try:
            self.core_api.patch_node(node_name, patch)
        except ApiException as e:
            print(f"  WARNING: Failed to label node '{node_name}': {e.reason}")

    def _cleanup_node_labels(self) -> None:
        """Remove ChaosProbe placement labels from all nodes."""
        nodes = self.core_api.list_node()
        for node in nodes.items:
            labels = node.metadata.labels or {}
            if PLACEMENT_LABEL_KEY in labels:
                # Strategic merge patch: setting a label to None removes it
                patch = {"metadata": {"labels": {PLACEMENT_LABEL_KEY: None}}}
                try:
                    self.core_api.patch_node(node.metadata.name, patch)
                except ApiException:
                    pass  # Label may already be gone

    def _patch_deployment_placement(
        self, deployment_name: str, zone: str, strategy_name: str
    ) -> None:
        """Patch a deployment with nodeSelector for placement."""
        patch = {
            "metadata": {
                "annotations": {MANAGED_ANNOTATION: strategy_name},
            },
            "spec": {
                "template": {
                    "spec": {
                        "nodeSelector": {PLACEMENT_LABEL_KEY: zone},
                    }
                }
            },
        }
        try:
            self.apps_api.patch_namespaced_deployment(
                deployment_name, self.namespace, patch
            )
            print(f"  Pinned '{deployment_name}' → zone '{zone}'")
        except ApiException as e:
            print(f"  WARNING: Failed to patch '{deployment_name}': {e.reason}")

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
        print(f"  Waiting for {len(deployment_names)} rollout(s) (timeout: {timeout}s)...")
        start = time.time()

        # Brief pause to let the controller start processing the patch
        time.sleep(2)

        pending = set(deployment_names)
        while pending and (time.time() - start) < timeout:
            still_pending = set()
            for name in pending:
                try:
                    dep = self.apps_api.read_namespaced_deployment(
                        name, self.namespace
                    )
                    desired = dep.spec.replicas or 1
                    generation = dep.metadata.generation or 0
                    observed = (
                        dep.status.observed_generation
                        if dep.status and dep.status.observed_generation
                        else 0
                    )
                    ready = dep.status.ready_replicas or 0 if dep.status else 0
                    updated = dep.status.updated_replicas or 0 if dep.status else 0
                    available = dep.status.available_replicas or 0 if dep.status else 0

                    # Controller must have observed the latest spec change
                    # AND all replicas must be updated, ready, and available
                    if (
                        observed >= generation
                        and updated >= desired
                        and ready >= desired
                        and available >= desired
                    ):
                        elapsed = int(time.time() - start)
                        print(f"    {name}: ready ({elapsed}s)")
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
                print(f"    WARNING: {name}: not ready after {elapsed}s")

    @staticmethod
    def _parse_cpu(cpu_str: str) -> int:
        """Parse a Kubernetes CPU string to millicores."""
        if not cpu_str:
            return 0
        cpu_str = str(cpu_str)
        if cpu_str.endswith("m"):
            return int(cpu_str[:-1])
        try:
            return int(float(cpu_str) * 1000)
        except ValueError:
            return 0

    @staticmethod
    def _parse_memory(mem_str: str) -> int:
        """Parse a Kubernetes memory string to bytes."""
        if not mem_str:
            return 0
        mem_str = str(mem_str)
        suffixes = {
            "Ki": 1024,
            "Mi": 1024 ** 2,
            "Gi": 1024 ** 3,
            "Ti": 1024 ** 4,
            "K": 1000,
            "M": 1000 ** 2,
            "G": 1000 ** 3,
            "T": 1000 ** 4,
        }
        for suffix, multiplier in sorted(suffixes.items(), key=lambda x: -len(x[0])):
            if mem_str.endswith(suffix):
                try:
                    return int(float(mem_str[: -len(suffix)]) * multiplier)
                except ValueError:
                    return 0
        try:
            return int(mem_str)
        except ValueError:
            return 0
