"""Tests for pod placement strategy computation and assignment."""

import pytest

from chaosprobe.placement.strategy import (
    DeploymentInfo,
    NodeAssignment,
    NodeInfo,
    PlacementStrategy,
    compute_assignments,
)
from chaosprobe.placement.mutator import PlacementMutator


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def two_nodes():
    """Two schedulable worker nodes with different capacities."""
    return [
        NodeInfo(
            name="worker1",
            labels={},
            allocatable_cpu_millicores=4000,
            allocatable_memory_bytes=8 * 1024 ** 3,
            conditions_ready=True,
            taints=[],
        ),
        NodeInfo(
            name="worker2",
            labels={},
            allocatable_cpu_millicores=2000,
            allocatable_memory_bytes=4 * 1024 ** 3,
            conditions_ready=True,
            taints=[],
        ),
    ]


@pytest.fixture
def three_nodes():
    """Three schedulable worker nodes."""
    return [
        NodeInfo(
            name="worker1",
            labels={},
            allocatable_cpu_millicores=4000,
            allocatable_memory_bytes=8 * 1024 ** 3,
            conditions_ready=True,
            taints=[],
        ),
        NodeInfo(
            name="worker2",
            labels={},
            allocatable_cpu_millicores=2000,
            allocatable_memory_bytes=4 * 1024 ** 3,
            conditions_ready=True,
            taints=[],
        ),
        NodeInfo(
            name="worker3",
            labels={},
            allocatable_cpu_millicores=2000,
            allocatable_memory_bytes=4 * 1024 ** 3,
            conditions_ready=True,
            taints=[],
        ),
    ]


@pytest.fixture
def control_plane_and_workers():
    """A control plane node (unschedulable) + two workers."""
    return [
        NodeInfo(
            name="cp1",
            labels={"node-role.kubernetes.io/control-plane": ""},
            allocatable_cpu_millicores=2000,
            allocatable_memory_bytes=4 * 1024 ** 3,
            conditions_ready=True,
            taints=[
                {"key": "node-role.kubernetes.io/control-plane", "value": "", "effect": "NoSchedule"}
            ],
        ),
        NodeInfo(
            name="worker1",
            labels={},
            allocatable_cpu_millicores=4000,
            allocatable_memory_bytes=8 * 1024 ** 3,
            conditions_ready=True,
            taints=[],
        ),
        NodeInfo(
            name="worker2",
            labels={},
            allocatable_cpu_millicores=2000,
            allocatable_memory_bytes=4 * 1024 ** 3,
            conditions_ready=True,
            taints=[],
        ),
    ]


@pytest.fixture
def schedulable_cp_and_workers():
    """Control plane node WITHOUT NoSchedule taint (like Kubespray small clusters) + 2 workers.

    All three nodes have identical CPU/RAM, mimicking the user's actual cluster.
    The control plane IS schedulable but should be de-prioritised by strategies.
    """
    return [
        NodeInfo(
            name="cp1",
            labels={"node-role.kubernetes.io/control-plane": ""},
            allocatable_cpu_millicores=2000,
            allocatable_memory_bytes=4 * 1024 ** 3,
            conditions_ready=True,
            taints=[],  # No NoSchedule taint!
        ),
        NodeInfo(
            name="worker1",
            labels={},
            allocatable_cpu_millicores=2000,
            allocatable_memory_bytes=4 * 1024 ** 3,
            conditions_ready=True,
            taints=[],
        ),
        NodeInfo(
            name="worker2",
            labels={},
            allocatable_cpu_millicores=2000,
            allocatable_memory_bytes=4 * 1024 ** 3,
            conditions_ready=True,
            taints=[],
        ),
    ]


@pytest.fixture
def sample_deployments():
    """Deployments resembling online-boutique resource profiles."""
    return [
        DeploymentInfo(name="adservice", replicas=1, cpu_request_millicores=200, memory_request_bytes=180 * 1024 ** 2),
        DeploymentInfo(name="cartservice", replicas=1, cpu_request_millicores=200, memory_request_bytes=64 * 1024 ** 2),
        DeploymentInfo(name="checkoutservice", replicas=1, cpu_request_millicores=100, memory_request_bytes=64 * 1024 ** 2),
        DeploymentInfo(name="currencyservice", replicas=1, cpu_request_millicores=100, memory_request_bytes=64 * 1024 ** 2),
        DeploymentInfo(name="emailservice", replicas=1, cpu_request_millicores=100, memory_request_bytes=64 * 1024 ** 2),
        DeploymentInfo(name="frontend", replicas=1, cpu_request_millicores=100, memory_request_bytes=64 * 1024 ** 2),
        DeploymentInfo(name="loadgenerator", replicas=1, cpu_request_millicores=300, memory_request_bytes=256 * 1024 ** 2),
        DeploymentInfo(name="paymentservice", replicas=1, cpu_request_millicores=100, memory_request_bytes=64 * 1024 ** 2),
        DeploymentInfo(name="productcatalogservice", replicas=1, cpu_request_millicores=100, memory_request_bytes=64 * 1024 ** 2),
        DeploymentInfo(name="recommendationservice", replicas=1, cpu_request_millicores=100, memory_request_bytes=220 * 1024 ** 2),
        DeploymentInfo(name="redis-cart", replicas=1, cpu_request_millicores=70, memory_request_bytes=200 * 1024 ** 2),
        DeploymentInfo(name="shippingservice", replicas=1, cpu_request_millicores=100, memory_request_bytes=64 * 1024 ** 2),
    ]


# ── NodeInfo tests ────────────────────────────────────────────


class TestNodeInfo:
    """Tests for NodeInfo scheduling checks."""

    def test_worker_is_schedulable(self, two_nodes):
        assert two_nodes[0].is_schedulable is True
        assert two_nodes[1].is_schedulable is True

    def test_control_plane_not_schedulable(self, control_plane_and_workers):
        cp = control_plane_and_workers[0]
        assert cp.is_schedulable is False

    def test_not_ready_node(self):
        node = NodeInfo(
            name="bad-node",
            conditions_ready=False,
            taints=[],
        )
        assert node.is_schedulable is False

    def test_control_plane_label_detected(self, control_plane_and_workers):
        cp = control_plane_and_workers[0]
        assert cp.is_control_plane is True

    def test_worker_not_control_plane(self, two_nodes):
        assert two_nodes[0].is_control_plane is False
        assert two_nodes[1].is_control_plane is False

    def test_schedulable_cp_still_identified(self, schedulable_cp_and_workers):
        """A CP without NoSchedule taint is schedulable but still flagged as CP."""
        cp = schedulable_cp_and_workers[0]
        assert cp.is_schedulable is True
        assert cp.is_control_plane is True

    def test_master_label_detected(self):
        node = NodeInfo(
            name="master1",
            labels={"node-role.kubernetes.io/master": ""},
            allocatable_cpu_millicores=2000,
            allocatable_memory_bytes=4 * 1024 ** 3,
            conditions_ready=True,
            taints=[],
        )
        assert node.is_control_plane is True


# ── Colocate strategy tests ──────────────────────────────────


class TestColocateStrategy:
    """Tests for the colocate placement strategy."""

    def test_all_pods_on_same_node(self, two_nodes, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.COLOCATE, sample_deployments, two_nodes
        )
        nodes_used = set(assignment.assignments.values())
        assert len(nodes_used) == 1
        assert assignment.strategy == PlacementStrategy.COLOCATE

    def test_picks_biggest_node_by_default(self, two_nodes, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.COLOCATE, sample_deployments, two_nodes
        )
        # worker1 has 4000m CPU, should be chosen
        assert all(n == "worker1" for n in assignment.assignments.values())

    def test_respects_target_node(self, two_nodes, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.COLOCATE, sample_deployments, two_nodes,
            target_node="worker2",
        )
        assert all(n == "worker2" for n in assignment.assignments.values())

    def test_target_node_not_found_raises(self, two_nodes, sample_deployments):
        with pytest.raises(ValueError, match="not found"):
            compute_assignments(
                PlacementStrategy.COLOCATE, sample_deployments, two_nodes,
                target_node="nonexistent",
            )

    def test_excludes_control_plane(self, control_plane_and_workers, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.COLOCATE, sample_deployments, control_plane_and_workers
        )
        assert "cp1" not in assignment.assignments.values()

    def test_all_deployments_assigned(self, two_nodes, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.COLOCATE, sample_deployments, two_nodes
        )
        assert len(assignment.assignments) == len(sample_deployments)

    def test_prefers_worker_over_schedulable_cp(self, schedulable_cp_and_workers, sample_deployments):
        """When CP is schedulable and all nodes have equal CPU, a worker should be picked."""
        assignment = compute_assignments(
            PlacementStrategy.COLOCATE, sample_deployments, schedulable_cp_and_workers
        )
        chosen = set(assignment.assignments.values())
        assert len(chosen) == 1
        assert "cp1" not in chosen


# ── Spread strategy tests ────────────────────────────────────


class TestSpreadStrategy:
    """Tests for the spread placement strategy."""

    def test_distributes_across_nodes(self, two_nodes, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.SPREAD, sample_deployments, two_nodes
        )
        nodes_used = set(assignment.assignments.values())
        assert len(nodes_used) == 2

    def test_round_robin_balance(self, two_nodes, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.SPREAD, sample_deployments, two_nodes
        )
        per_node = {}
        for node in assignment.assignments.values():
            per_node[node] = per_node.get(node, 0) + 1

        # 12 deployments across 2 nodes: 6 each
        assert per_node["worker1"] == 6
        assert per_node["worker2"] == 6

    def test_three_node_distribution(self, three_nodes, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.SPREAD, sample_deployments, three_nodes
        )
        per_node = {}
        for node in assignment.assignments.values():
            per_node[node] = per_node.get(node, 0) + 1

        # 12 deployments across 3 nodes: 4 each
        assert all(count == 4 for count in per_node.values())

    def test_excludes_control_plane(self, control_plane_and_workers, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.SPREAD, sample_deployments, control_plane_and_workers
        )
        assert "cp1" not in assignment.assignments.values()


# ── Random strategy tests ────────────────────────────────────


class TestRandomStrategy:
    """Tests for the random placement strategy."""

    def test_reproducible_with_seed(self, two_nodes, sample_deployments):
        a1 = compute_assignments(
            PlacementStrategy.RANDOM, sample_deployments, two_nodes, seed=42
        )
        a2 = compute_assignments(
            PlacementStrategy.RANDOM, sample_deployments, two_nodes, seed=42
        )
        assert a1.assignments == a2.assignments

    def test_different_seeds_differ(self, two_nodes, sample_deployments):
        a1 = compute_assignments(
            PlacementStrategy.RANDOM, sample_deployments, two_nodes, seed=1
        )
        a2 = compute_assignments(
            PlacementStrategy.RANDOM, sample_deployments, two_nodes, seed=2
        )
        # With 12 deployments and 2 nodes, different seeds should produce
        # different assignments (extremely unlikely to be identical)
        assert a1.assignments != a2.assignments

    def test_seed_stored_in_assignment(self, two_nodes, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.RANDOM, sample_deployments, two_nodes, seed=42
        )
        assert assignment.seed == 42

    def test_all_nodes_valid(self, two_nodes, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.RANDOM, sample_deployments, two_nodes, seed=123
        )
        valid_names = {n.name for n in two_nodes}
        for node in assignment.assignments.values():
            assert node in valid_names


# ── Antagonistic strategy tests ──────────────────────────────


class TestAntagonisticStrategy:
    """Tests for the antagonistic placement strategy."""

    def test_heavy_pods_grouped(self, two_nodes, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.ANTAGONISTIC, sample_deployments, two_nodes
        )
        heavy_node = assignment.metadata["heavy_node"]
        heavy_deps = set(assignment.metadata["heavy_deployments"])

        # The heaviest deployments should be on the same node
        for dep_name in heavy_deps:
            assert assignment.assignments[dep_name] == heavy_node

    def test_heaviest_services_identified(self, two_nodes, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.ANTAGONISTIC, sample_deployments, two_nodes
        )
        heavy = set(assignment.metadata["heavy_deployments"])

        # loadgenerator (300m+256Mi), adservice (200m+180Mi),
        # recommendationservice (100m+220Mi), redis-cart (70m+200Mi),
        # cartservice (200m+64Mi), checkoutservice (100m+64Mi)
        # should be among the heavy half
        assert "loadgenerator" in heavy
        assert "adservice" in heavy

    def test_single_node_fallback(self, sample_deployments):
        single_node = [
            NodeInfo(
                name="only-node",
                allocatable_cpu_millicores=8000,
                allocatable_memory_bytes=16 * 1024 ** 3,
                conditions_ready=True,
                taints=[],
            )
        ]
        assignment = compute_assignments(
            PlacementStrategy.ANTAGONISTIC, sample_deployments, single_node
        )
        # With only 1 node, all go there
        assert all(n == "only-node" for n in assignment.assignments.values())

    def test_uses_heaviest_node(self, two_nodes, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.ANTAGONISTIC, sample_deployments, two_nodes
        )
        # worker1 has 4000m, should be the heavy node
        assert assignment.metadata["heavy_node"] == "worker1"

    def test_prefers_worker_over_schedulable_cp(self, schedulable_cp_and_workers, sample_deployments):
        """When CP is schedulable and all nodes have equal CPU, a worker should be the heavy node."""
        assignment = compute_assignments(
            PlacementStrategy.ANTAGONISTIC, sample_deployments, schedulable_cp_and_workers
        )
        assert assignment.metadata["heavy_node"] != "cp1"


# ── NodeAssignment serialisation tests ────────────────────────


class TestNodeAssignment:
    """Tests for NodeAssignment serialisation."""

    def test_round_trip(self, two_nodes, sample_deployments):
        original = compute_assignments(
            PlacementStrategy.RANDOM, sample_deployments, two_nodes, seed=42
        )
        data = original.to_dict()
        restored = NodeAssignment.from_dict(data)

        assert restored.strategy == original.strategy
        assert restored.assignments == original.assignments
        assert restored.seed == original.seed

    def test_to_dict_structure(self, two_nodes, sample_deployments):
        assignment = compute_assignments(
            PlacementStrategy.COLOCATE, sample_deployments, two_nodes
        )
        data = assignment.to_dict()

        assert data["strategy"] == "colocate"
        assert isinstance(data["assignments"], dict)
        assert "metadata" in data


# ── Edge cases ────────────────────────────────────────────────


class TestEdgeCases:
    """Edge case tests for placement strategies."""

    def test_no_schedulable_nodes_raises(self):
        nodes = [
            NodeInfo(
                name="cp1",
                conditions_ready=True,
                taints=[
                    {"key": "node-role.kubernetes.io/control-plane", "value": "", "effect": "NoSchedule"}
                ],
            )
        ]
        deps = [DeploymentInfo(name="test")]

        with pytest.raises(ValueError, match="No schedulable"):
            compute_assignments(PlacementStrategy.COLOCATE, deps, nodes)

    def test_single_deployment(self, two_nodes):
        deps = [DeploymentInfo(name="solo")]
        assignment = compute_assignments(
            PlacementStrategy.SPREAD, deps, two_nodes
        )
        assert len(assignment.assignments) == 1

    def test_empty_deployments_list(self, two_nodes):
        assignment = compute_assignments(
            PlacementStrategy.COLOCATE, [], two_nodes
        )
        assert len(assignment.assignments) == 0


# ── Resource parsing tests ────────────────────────────────────


class TestResourceParsing:
    """Tests for CPU and memory string parsing in PlacementMutator."""

    def test_parse_cpu_millicores(self):
        assert PlacementMutator._parse_cpu("200m") == 200
        assert PlacementMutator._parse_cpu("1000m") == 1000

    def test_parse_cpu_cores(self):
        assert PlacementMutator._parse_cpu("1") == 1000
        assert PlacementMutator._parse_cpu("2") == 2000
        assert PlacementMutator._parse_cpu("0.5") == 500

    def test_parse_cpu_empty(self):
        assert PlacementMutator._parse_cpu("") == 0
        assert PlacementMutator._parse_cpu("0") == 0

    def test_parse_memory_mi(self):
        assert PlacementMutator._parse_memory("128Mi") == 128 * 1024 ** 2

    def test_parse_memory_gi(self):
        assert PlacementMutator._parse_memory("2Gi") == 2 * 1024 ** 3

    def test_parse_memory_ki(self):
        assert PlacementMutator._parse_memory("1024Ki") == 1024 * 1024

    def test_parse_memory_bytes(self):
        assert PlacementMutator._parse_memory("1048576") == 1048576

    def test_parse_memory_empty(self):
        assert PlacementMutator._parse_memory("") == 0
        assert PlacementMutator._parse_memory("0") == 0


# ── Strategy description tests ────────────────────────────────


class TestStrategyDescriptions:
    """Tests for strategy descriptions and enum values."""

    def test_all_strategies_have_descriptions(self):
        for strategy in PlacementStrategy:
            desc = strategy.describe()
            assert len(desc) > 10

    def test_strategy_values(self):
        assert PlacementStrategy.COLOCATE.value == "colocate"
        assert PlacementStrategy.SPREAD.value == "spread"
        assert PlacementStrategy.RANDOM.value == "random"
        assert PlacementStrategy.ANTAGONISTIC.value == "antagonistic"
