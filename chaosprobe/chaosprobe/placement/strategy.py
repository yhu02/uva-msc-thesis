"""Placement strategy definitions and node assignment logic.

Provides six strategies for pod placement:
- colocate:         Pin all pods to a single node (maximum resource contention)
- spread:           Distribute pods evenly across nodes (minimum contention)
- random:           Random node assignment per deployment (chaotic, reproducible via seed)
- adversarial:     Group resource-heavy pods on the same node (worst-case, worst-fit)
- best-fit:         Bin-packing: pack deployments into fewest nodes
                    (cf. Borg best-fit scoring; Verma et al., EuroSys 2015)
- dependency-aware: Co-locate communicating services based on the service
                    dependency graph (cf. DeathStarBench, Sinan, μServe)
"""

import random
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class PlacementStrategy(str, Enum):
    """Pod placement strategy for contention experiments."""

    COLOCATE = "colocate"
    SPREAD = "spread"
    RANDOM = "random"
    ADVERSARIAL = "adversarial"
    BEST_FIT = "best-fit"
    DEPENDENCY_AWARE = "dependency-aware"

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
            self.ADVERSARIAL: (
                "Intentionally co-locate resource-heavy pods on the same node "
                "to create worst-case contention for IO and execution "
                "(worst-fit bin-packing)."
            ),
            self.BEST_FIT: (
                "Best-fit bin-packing: place each deployment on the node with "
                "the smallest remaining capacity that still fits. Mimics "
                "Borg/Kubernetes default scoring to concentrate load on fewer nodes."
            ),
            self.DEPENDENCY_AWARE: (
                "Partition deployments by service dependency graph: "
                "co-locate communicating services on the same node and "
                "spread non-communicating ones, minimising cross-node hops."
            ),
        }
        return descriptions[self]

    @property
    def execution_order(self) -> int:
        """Execution priority (lower = earlier).

        High-contention strategies (colocate, best-fit) run last so their
        lingering node pressure doesn't skew results for other strategies.
        """
        order = {
            self.SPREAD: 0,
            self.RANDOM: 1,
            self.DEPENDENCY_AWARE: 2,
            self.ADVERSARIAL: 3,
            self.BEST_FIT: 4,
            self.COLOCATE: 5,
        }
        return order.get(self, 99)


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
            if (
                taint.get("key") in self.CONTROL_PLANE_LABEL_KEYS
                and taint.get("effect") == "NoSchedule"
            ):
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
    namespace: Optional[str] = None


def compute_assignments(
    strategy: PlacementStrategy,
    deployments: List[DeploymentInfo],
    nodes: List[NodeInfo],
    target_node: Optional[str] = None,
    seed: Optional[int] = None,
    dependencies: Optional[List[Tuple[str, str]]] = None,
) -> NodeAssignment:
    """Compute node assignments for a given strategy.

    Args:
        strategy: The placement strategy to apply.
        deployments: List of deployments to place.
        nodes: List of schedulable nodes.
        target_node: For COLOCATE, the specific node to target (optional).
        seed: Random seed for RANDOM strategy reproducibility.
        dependencies: For DEPENDENCY_AWARE, a list of ``(source, target)``
            service dependency edges.  Ignored by other strategies.

    Returns:
        A NodeAssignment with deployment→node mappings.

    Raises:
        ValueError: If no schedulable nodes are available or target_node not found.
    """
    # Prefer worker nodes — scheduling on the control plane can starve
    # the API server and crash the cluster under load.
    workers = [n for n in nodes if n.is_schedulable and not n.is_control_plane]
    if not workers:
        # Fall back to any schedulable node (single-node clusters, etc.)
        workers = [n for n in nodes if n.is_schedulable]
    if not workers:
        raise ValueError("No schedulable worker nodes available in the cluster")

    schedulable = workers
    node_names = [n.name for n in schedulable]

    if strategy == PlacementStrategy.COLOCATE:
        return _compute_colocate(deployments, schedulable, node_names, target_node)
    elif strategy == PlacementStrategy.SPREAD:
        return _compute_spread(deployments, schedulable, node_names)
    elif strategy == PlacementStrategy.RANDOM:
        return _compute_random(deployments, schedulable, node_names, seed)
    elif strategy == PlacementStrategy.ADVERSARIAL:
        return _compute_adversarial(deployments, schedulable, node_names)
    elif strategy == PlacementStrategy.BEST_FIT:
        return _compute_best_fit(deployments, schedulable, node_names)
    elif strategy == PlacementStrategy.DEPENDENCY_AWARE:
        return _compute_dependency_aware(
            deployments, schedulable, node_names, dependencies or []
        )
    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def _pick_best_worker(nodes: List[NodeInfo]) -> str:
    """Pick the best node for heavy workloads, preferring workers over control plane.

    Sorts by: (is_worker, allocatable_memory, allocatable_cpu) so worker
    nodes with the most allocatable memory are preferred.  Memory is the
    primary resource constraint in small clusters.
    """
    return max(
        nodes,
        key=lambda n: (
            not n.is_control_plane,
            n.allocatable_memory_bytes,
            n.allocatable_cpu_millicores,
        ),
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
                f"{len(deployments)} deployments distributed across " f"{len(node_names)} nodes"
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


def _compute_adversarial(
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
            strategy=PlacementStrategy.ADVERSARIAL,
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

    for idx, (_score, dep) in enumerate(scored):
        if idx < midpoint:
            assignments[dep.name] = heavy_node
            heavy_names.append(dep.name)
        else:
            assignments[dep.name] = light_nodes[idx % len(light_nodes)]
            light_names.append(dep.name)

    return NodeAssignment(
        strategy=PlacementStrategy.ADVERSARIAL,
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


def _deployment_weight(d: DeploymentInfo) -> float:
    """Combined CPU + memory weight, in millicore-equivalent units.

    1 MiB of memory is scored as 1 millicore so the two dimensions are
    comparable on a single axis — good enough for the small, roughly
    homogeneous clusters ChaosProbe targets.
    """
    return d.cpu_request_millicores + d.memory_request_bytes / (1024 * 1024)


def _compute_best_fit(
    deployments: List[DeploymentInfo],
    nodes: List[NodeInfo],
    node_names: List[str],
) -> NodeAssignment:
    """Best-fit decreasing bin-packing.

    Sort deployments by weight (largest first) and place each on the node
    with the smallest remaining capacity that still accommodates it.  This
    concentrates load onto the fewest nodes possible — the canonical
    "best-fit" heuristic from the bin-packing literature and the default
    philosophy of Borg's scoring function (Verma et al., EuroSys 2015).
    """
    # Remaining capacity per node, tracked as the same millicore-equivalent.
    remaining: Dict[str, float] = {
        n.name: n.allocatable_cpu_millicores
        + n.allocatable_memory_bytes / (1024 * 1024)
        for n in nodes
    }
    per_node: Dict[str, int] = {name: 0 for name in node_names}
    assignments: Dict[str, str] = {}

    sorted_deps = sorted(deployments, key=_deployment_weight, reverse=True)

    for dep in sorted_deps:
        w = _deployment_weight(dep)

        # Prefer nodes that still have room; among those, pick the one
        # whose post-placement slack is smallest (tightest fit).
        fits = [(remaining[name] - w, name) for name in node_names if remaining[name] >= w]
        if fits:
            fits.sort()
            chosen = fits[0][1]
        else:
            # Nothing fits cleanly — fall back to the node with the most
            # remaining capacity (the scheduler would have to overcommit).
            chosen = max(node_names, key=lambda n: remaining[n])

        assignments[dep.name] = chosen
        remaining[chosen] = max(0.0, remaining[chosen] - w)
        per_node[chosen] += 1

    used_nodes = sum(1 for c in per_node.values() if c > 0)

    return NodeAssignment(
        strategy=PlacementStrategy.BEST_FIT,
        assignments=assignments,
        metadata={
            "distribution": per_node,
            "nodes_used": used_nodes,
            "description": (
                f"Best-fit packing: {len(deployments)} deployments into "
                f"{used_nodes}/{len(node_names)} nodes"
            ),
        },
    )


def _compute_dependency_aware(
    deployments: List[DeploymentInfo],
    nodes: List[NodeInfo],
    node_names: List[str],
    dependencies: List[Tuple[str, str]],
) -> NodeAssignment:
    """Dependency-aware partitioning via BFS on the service dependency graph.

    Co-locates services that communicate (direct ``DEPENDS_ON`` edges) while
    still spreading the workload across nodes.  The approach is a light
    version of balanced k-way graph partitioning (cf. METIS / microservice
    placement work such as μServe, DeathStarBench, Sinan, Orca):

    1. Build an undirected adjacency from the dependency edges.
    2. Pick a root (the node with fewest incoming edges — the entry-point).
    3. BFS from the root, producing a traversal order where dependent
       services are adjacent.
    4. Chunk the order into ``K`` contiguous groups (``K`` = worker nodes);
       each chunk is assigned to one node.  Contiguous BFS chunks keep
       most direct-dependency pairs on the same node.

    Edges whose endpoints land on the same node are "preserved" — this
    count is reported in the metadata as a quality measure.
    """
    if not deployments:
        return NodeAssignment(
            strategy=PlacementStrategy.DEPENDENCY_AWARE,
            assignments={},
            metadata={
                "distribution": {},
                "edges_total": 0,
                "edges_preserved": 0,
                "description": "No deployments to place",
            },
        )

    dep_names = {d.name for d in deployments}
    edges = [(s, t) for s, t in dependencies if s in dep_names and t in dep_names]

    adj: Dict[str, set] = {name: set() for name in dep_names}
    for s, t in edges:
        adj[s].add(t)
        adj[t].add(s)

    # Root selection: prefer the node with fewest incoming (depended-on-by)
    # edges — this is the application entry-point.  Ties broken by highest
    # total degree (most connections), then alphabetically for determinism.
    in_degree: Dict[str, int] = {name: 0 for name in dep_names}
    for _s, t in edges:
        in_degree[t] = in_degree.get(t, 0) + 1
    root = sorted(dep_names, key=lambda n: (in_degree.get(n, 0), -len(adj[n]), n))[0]

    # BFS order (neighbours visited in alphabetical order for determinism).
    order: List[str] = []
    seen = {root}
    queue: deque = deque([root])
    while queue:
        cur = queue.popleft()
        order.append(cur)
        for nb in sorted(adj[cur]):
            if nb not in seen:
                seen.add(nb)
                queue.append(nb)

    # Append any disconnected components in a deterministic order.
    for name in sorted(dep_names):
        if name not in seen:
            order.append(name)
            seen.add(name)

    # Partition the ordered list into K (= number of worker nodes) roughly
    # equal contiguous chunks.
    k = len(node_names)
    n = len(order)
    chunk_size = max(1, n // k)
    assignments: Dict[str, str] = {}
    per_node: Dict[str, int] = {name: 0 for name in node_names}

    for i, name in enumerate(order):
        node_idx = min(i // chunk_size, k - 1)
        chosen = node_names[node_idx]
        assignments[name] = chosen
        per_node[chosen] += 1

    preserved = sum(1 for s, t in edges if assignments[s] == assignments[t])

    return NodeAssignment(
        strategy=PlacementStrategy.DEPENDENCY_AWARE,
        assignments=assignments,
        metadata={
            "distribution": per_node,
            "root": root,
            "edges_total": len(edges),
            "edges_preserved": preserved,
            "description": (
                f"Dependency-aware partition: {len(deployments)} deployments "
                f"across {k} nodes (root={root}, "
                f"{preserved}/{len(edges)} edges co-located)"
            ),
        },
    )
