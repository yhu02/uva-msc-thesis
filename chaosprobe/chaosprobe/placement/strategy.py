"""Placement strategy definitions and node assignment logic.

Provides four strategies for pod placement:
- colocate:     Pin all pods to a single node (maximum resource contention)
- spread:       Distribute pods evenly across nodes (minimum contention)
- random:       Random node assignment per deployment (chaotic, reproducible via seed)
- antagonistic: Group resource-heavy pods on the same node (worst-case contention)
"""

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class PlacementStrategy(str, Enum):
    """Pod placement strategy for contention experiments."""

    COLOCATE = "colocate"
    SPREAD = "spread"
    RANDOM = "random"
    ANTAGONISTIC = "antagonistic"

    def describe(self) -> str:
        """Human-readable description of the strategy."""
        descriptions = {
            self.COLOCATE: (
                "Pin all pods to a single node to maximise resource contention "
                "(CPU, memory, IO, network bandwidth all shared)."
            ),
            self.SPREAD: (
                "Distribute pods evenly across available nodes to minimise "
                "resource contention (but increase inter-node network latency)."
            ),
            self.RANDOM: (
                "Assign each deployment to a random node. Creates unpredictable "
                "contention patterns. Use --seed for reproducibility."
            ),
            self.ANTAGONISTIC: (
                "Intentionally co-locate resource-heavy pods on the same node "
                "to create worst-case contention for IO and execution."
            ),
        }
        return descriptions[self]


@dataclass
class NodeInfo:
    """Information about a schedulable Kubernetes node."""

    name: str
    labels: Dict[str, str] = field(default_factory=dict)
    allocatable_cpu_millicores: int = 0
    allocatable_memory_bytes: int = 0
    conditions_ready: bool = False
    taints: List[Dict[str, str]] = field(default_factory=list)

    CONTROL_PLANE_LABEL_KEYS = {
        "node-role.kubernetes.io/master",
        "node-role.kubernetes.io/control-plane",
    }

    @property
    def is_schedulable(self) -> bool:
        """Check if the node accepts regular workloads."""
        for taint in self.taints:
            if taint.get("key") in self.CONTROL_PLANE_LABEL_KEYS and taint.get("effect") == "NoSchedule":
                return False
        return self.conditions_ready

    @property
    def is_control_plane(self) -> bool:
        """Check if the node is a control plane node (by labels or name)."""
        for key in self.CONTROL_PLANE_LABEL_KEYS:
            if key in self.labels:
                return True
        name_lower = self.name.lower()
        return any(p in name_lower for p in ("cp", "master", "control"))


@dataclass
class NodeAssignment:
    """A mapping from deployment names to target node names."""

    strategy: PlacementStrategy
    assignments: Dict[str, str] = field(default_factory=dict)
    seed: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a dictionary."""
        return {
            "strategy": self.strategy.value,
            "seed": self.seed,
            "assignments": dict(self.assignments),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NodeAssignment":
        """Deserialise from a dictionary."""
        return cls(
            strategy=PlacementStrategy(data["strategy"]),
            assignments=data.get("assignments", {}),
            seed=data.get("seed"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class DeploymentInfo:
    """Lightweight info about a deployment for placement decisions."""

    name: str
    replicas: int = 1
    cpu_request_millicores: int = 0
    memory_request_bytes: int = 0
    current_node: Optional[str] = None


def compute_assignments(
    strategy: PlacementStrategy,
    deployments: List[DeploymentInfo],
    nodes: List[NodeInfo],
    target_node: Optional[str] = None,
    seed: Optional[int] = None,
) -> NodeAssignment:
    """Compute node assignments for a given strategy.

    Args:
        strategy: The placement strategy to apply.
        deployments: List of deployments to place.
        nodes: List of schedulable nodes.
        target_node: For COLOCATE, the specific node to target (optional).
        seed: Random seed for RANDOM strategy reproducibility.

    Returns:
        A NodeAssignment with deployment→node mappings.

    Raises:
        ValueError: If no schedulable nodes are available or target_node not found.
    """
    schedulable = [n for n in nodes if n.is_schedulable]
    if not schedulable:
        raise ValueError("No schedulable worker nodes available in the cluster")

    node_names = [n.name for n in schedulable]

    if strategy == PlacementStrategy.COLOCATE:
        return _compute_colocate(deployments, schedulable, node_names, target_node)
    elif strategy == PlacementStrategy.SPREAD:
        return _compute_spread(deployments, schedulable, node_names)
    elif strategy == PlacementStrategy.RANDOM:
        return _compute_random(deployments, schedulable, node_names, seed)
    elif strategy == PlacementStrategy.ANTAGONISTIC:
        return _compute_antagonistic(deployments, schedulable, node_names)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def _pick_best_worker(nodes: List[NodeInfo]) -> str:
    """Pick the best node for heavy workloads, preferring workers over control plane.

    Sorts by: (is_worker, allocatable_cpu) so worker nodes are always
    preferred. Only falls back to control plane if no workers exist.
    """
    return max(
        nodes,
        key=lambda n: (not n.is_control_plane, n.allocatable_cpu_millicores),
    ).name


def _compute_colocate(
    deployments: List[DeploymentInfo],
    nodes: List[NodeInfo],
    node_names: List[str],
    target_node: Optional[str],
) -> NodeAssignment:
    """All deployments pinned to a single node."""
    if target_node:
        if target_node not in node_names:
            raise ValueError(
                f"Target node '{target_node}' not found among schedulable nodes: "
                f"{', '.join(node_names)}"
            )
        chosen = target_node
    else:
        chosen = _pick_best_worker(nodes)

    assignments = {d.name: chosen for d in deployments}
    return NodeAssignment(
        strategy=PlacementStrategy.COLOCATE,
        assignments=assignments,
        metadata={
            "target_node": chosen,
            "description": f"All {len(deployments)} deployments pinned to node '{chosen}'",
        },
    )


def _compute_spread(
    deployments: List[DeploymentInfo],
    nodes: List[NodeInfo],
    node_names: List[str],
) -> NodeAssignment:
    """Distribute deployments evenly across nodes using round-robin."""
    assignments = {}
    for idx, dep in enumerate(deployments):
        assignments[dep.name] = node_names[idx % len(node_names)]

    # Count per node
    per_node: Dict[str, int] = {}
    for node in assignments.values():
        per_node[node] = per_node.get(node, 0) + 1

    return NodeAssignment(
        strategy=PlacementStrategy.SPREAD,
        assignments=assignments,
        metadata={
            "distribution": per_node,
            "description": (
                f"{len(deployments)} deployments distributed across "
                f"{len(node_names)} nodes"
            ),
        },
    )


def _compute_random(
    deployments: List[DeploymentInfo],
    nodes: List[NodeInfo],
    node_names: List[str],
    seed: Optional[int],
) -> NodeAssignment:
    """Random node assignment per deployment."""
    rng = random.Random(seed)
    assignments = {d.name: rng.choice(node_names) for d in deployments}

    per_node: Dict[str, int] = {}
    for node in assignments.values():
        per_node[node] = per_node.get(node, 0) + 1

    return NodeAssignment(
        strategy=PlacementStrategy.RANDOM,
        assignments=assignments,
        seed=seed,
        metadata={
            "distribution": per_node,
            "description": (
                f"{len(deployments)} deployments randomly assigned across "
                f"{len(node_names)} nodes (seed={seed})"
            ),
        },
    )


def _compute_antagonistic(
    deployments: List[DeploymentInfo],
    nodes: List[NodeInfo],
    node_names: List[str],
) -> NodeAssignment:
    """Group resource-heavy deployments on the same node.

    Sorts deployments by total resource request (CPU + normalised memory)
    and assigns the heaviest ones to the same node, lighter ones elsewhere.

    This creates worst-case contention for IO and execution on the
    node hosting the heavy workloads.
    """
    if len(node_names) < 2:
        # Only one node: same as colocate
        assignments = {d.name: node_names[0] for d in deployments}
        return NodeAssignment(
            strategy=PlacementStrategy.ANTAGONISTIC,
            assignments=assignments,
            metadata={
                "description": "Only 1 schedulable node; behaves like colocate",
                "heavy_node": node_names[0],
                "light_node": node_names[0],
            },
        )

    # Score each deployment by resource weight
    # Normalise memory to millicores equivalent: 1 MiB ≈ 1 millicore for scoring
    scored = []
    for d in deployments:
        mem_score = d.memory_request_bytes / (1024 * 1024)  # MiB
        score = d.cpu_request_millicores + mem_score
        scored.append((score, d))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Heavy half goes to node with most resources, light half to the rest
    heavy_node = _pick_best_worker(nodes)
    light_nodes = [n for n in node_names if n != heavy_node]
    if not light_nodes:
        light_nodes = node_names  # fallback

    midpoint = max(1, len(scored) // 2)
    assignments = {}
    heavy_names = []
    light_names = []

    for idx, (score, dep) in enumerate(scored):
        if idx < midpoint:
            assignments[dep.name] = heavy_node
            heavy_names.append(dep.name)
        else:
            assignments[dep.name] = light_nodes[idx % len(light_nodes)]
            light_names.append(dep.name)

    return NodeAssignment(
        strategy=PlacementStrategy.ANTAGONISTIC,
        assignments=assignments,
        metadata={
            "heavy_node": heavy_node,
            "heavy_deployments": heavy_names,
            "light_deployments": light_names,
            "description": (
                f"Top {len(heavy_names)} resource-heavy deployments pinned to "
                f"'{heavy_node}'; remaining {len(light_names)} distributed elsewhere"
            ),
        },
    )
