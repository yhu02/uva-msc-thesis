"""Placement mutator — applies and clears pod placement constraints.

Uses the Kubernetes API to:
- Patch deployments with nodeSelector to pin pods to specific nodes
- Clear placement constraints to restore default scheduling
- Query current placement state
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import click
from kubernetes import client
from kubernetes.client.rest import ApiException

from chaosprobe.config.topology import ServiceRoute, _extract_dependencies_from_deployment
from chaosprobe.k8s import ensure_k8s_config
from chaosprobe.metrics.resources import parse_cpu_quantity, parse_memory_quantity
from chaosprobe.orchestrator.preflight import LITMUS_INFRA_DEPLOYMENTS
from chaosprobe.placement.fraction_solver import load_static_topology
from chaosprobe.placement.strategy import (
    DeploymentInfo,
    NodeAssignment,
    NodeInfo,
    PlacementStrategy,
    compute_assignments,
)

logger = logging.getLogger(__name__)

# Datastores that speak their own TCP wire protocol (not gRPC). Used to label a
# topology-derived east-west route's protocol accurately — the label is persisted
# in latency summaries / Neo4j, so a default-grpc would misdescribe these backends
# (the prober TCP-connects regardless, so the probe itself is unaffected).
_TCP_DATASTORE_PREFIXES = (
    "memcached",
    "mongodb",
    "redis",
    "mysql",
    "mariadb",
    "postgres",  # covers postgres / postgresql
    "cassandra",
    "rabbitmq",
    "etcd",
    "zookeeper",
)

# Built-in Kubernetes label for targeting nodes by hostname
PLACEMENT_LABEL_KEY = "kubernetes.io/hostname"
# Annotation to track which deployments are managed by ChaosProbe placement
MANAGED_ANNOTATION = "chaosprobe.io/placement-strategy"


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

        # Optional cached snapshot of node pod-request usage.  Set by the
        # run pipeline once at start so best-fit's bin capacity is
        # reproducible across strategies in the same run.  When unset
        # (e.g. standalone `placement apply` invocations), best-fit
        # falls back to a live query.
        self.usage_snapshot: Optional[Dict[str, Tuple[int, int]]] = None

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

            replicas = dep.spec.replicas if dep.spec.replicas is not None else 1

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

    def scale_deployments(self, replicas: int) -> List[str]:
        """Scale every application deployment to ``replicas`` (skips Litmus infra
        and the load generator).

        Used by ``run --replicas N`` to study placement under *multi-replica*
        services — the regime where node-level faults differentiate placements
        (a node failure loses all replicas of a co-located service but only one
        replica of a spread one). The load generator is left untouched so the
        offered load does not scale with the replica count.

        Returns the names of the deployments that were scaled.
        """
        deps = self.apps_api.list_namespaced_deployment(self.namespace)
        scaled: List[str] = []
        for dep in deps.items:
            name = dep.metadata.name
            if name in LITMUS_INFRA_DEPLOYMENTS or name == "loadgenerator":
                continue
            self.apps_api.patch_namespaced_deployment(
                name, self.namespace, {"spec": {"replicas": replicas}}
            )
            scaled.append(name)
        return scaled

    def get_node_pod_usage(
        self,
        exclude_pods: Optional[set] = None,
    ) -> Dict[str, Tuple[int, int]]:
        """Sum pod resource **requests** currently scheduled on each node.

        Returns ``{node_name: (cpu_millicores, memory_bytes)}``.  Used by
        the best-fit strategy so its bin capacity reflects actual free
        room rather than raw allocatable (which ignores kube-system,
        chaos infra, monitoring, load generators, etc.).

        Only pods with a ``node_name`` assigned and a non-terminal phase
        are counted.  Pods without explicit requests contribute 0 — the
        same convention the kube scheduler uses.

        Args:
            exclude_pods: Optional set of ``(namespace, pod_name)`` tuples
                to skip.  Used by the run-time snapshot to exclude the
                very app pods best-fit is about to repack — those pods'
                requests should not count as "already used" capacity,
                otherwise best-fit sees nodes as fuller than they are
                and over-spreads to avoid imagined collisions.
        """
        exclude_pods = exclude_pods or set()
        usage: Dict[str, Tuple[int, int]] = {}
        try:
            pods = self.core_api.list_pod_for_all_namespaces().items
        except ApiException:
            return usage

        for pod in pods:
            if (pod.metadata.namespace, pod.metadata.name) in exclude_pods:
                continue
            node = getattr(pod.spec, "node_name", None)
            if not node:
                continue
            phase = (pod.status.phase or "").lower()
            if phase in ("succeeded", "failed"):
                continue
            cpu_m = 0
            mem_b = 0
            for c in pod.spec.containers or []:
                reqs = getattr(c.resources, "requests", None) if c.resources else None
                if not reqs:
                    continue
                cpu_m += int(parse_cpu_quantity(reqs.get("cpu", "0")))
                mem_b += parse_memory_quantity(reqs.get("memory", "0"))
            prev_cpu, prev_mem = usage.get(node, (0, 0))
            usage[node] = (prev_cpu + cpu_m, prev_mem + mem_b)
        return usage

    def get_service_dependency_routes(self) -> List[ServiceRoute]:
        """Discover full ``(source, target, host:port, protocol, desc)`` routes.

        Reads every Deployment spec in the namespace and extracts
        ``*_SERVICE_ADDR`` / ``*_ADDR`` environment variables using the
        same parser as :mod:`chaosprobe.config.topology`, preserving the
        target host:port and inferred protocol (``grpc`` / ``tcp``) that
        :meth:`get_service_dependencies` discards.  Used to build
        protocol-aware east-west latency routes so gRPC backends are
        probed over their real port instead of a non-existent HTTP one.
        Deduplicated on the ``(source, target)`` pair.
        """
        deps = self.apps_api.list_namespaced_deployment(self.namespace)
        serializer = self.apps_api.api_client.sanitize_for_serialization
        routes: List[ServiceRoute] = []
        seen: set = set()
        for dep in deps.items:
            if dep.metadata.name in LITMUS_INFRA_DEPLOYMENTS:
                continue
            try:
                dep_dict = serializer(dep)
            except Exception:
                # A deployment that won't serialize simply contributes no
                # dependency edges; skipping it is safe (others still parse).
                continue
            for route in _extract_dependencies_from_deployment(dep_dict):
                key = (route[0], route[1])
                if key not in seen:
                    seen.add(key)
                    routes.append(route)
        return routes

    def get_service_dependencies(self) -> List[Tuple[str, str]]:
        """Discover ``(source, target)`` service dependency edges for the namespace.

        Thin ``(source, target)`` projection of
        :meth:`get_service_dependency_routes` for the dependency-aware
        placement strategy, which only needs the edge pairs.
        """
        return [(route[0], route[1]) for route in self.get_service_dependency_routes()]

    def get_topology_dependency_routes(self, topology_path: str) -> List[ServiceRoute]:
        """East-west routes from a static ``topology.json``, ports from live Services.

        The env-var-based :meth:`get_service_dependency_routes` is empty for
        workloads that discover peers another way (e.g. hotelReservation uses
        Consul, not ``*_SERVICE_ADDR`` env vars). This fallback reads the
        hand-curated edges from ``topology.json`` (the same file the solver gate
        consumes) and resolves each target's ``host:port`` from the namespace's
        live Services, so the in-cluster TCP-connect prober
        (:mod:`chaosprobe.metrics.latency`) can still measure inter-service
        latency. Protocol is a label only here (the probe is a TCP connect
        either way): ``tcp`` for datastores (memcached/mongodb/redis), ``grpc``
        otherwise. Targets with no resolvable Service port are skipped.
        """
        edges, _services = load_static_topology(topology_path)

        port_by_service: Dict[str, int] = {}
        for svc in self.core_api.list_namespaced_service(self.namespace).items:
            ports = (svc.spec.ports or []) if svc.spec else []
            if ports and ports[0].port is not None:
                port_by_service[svc.metadata.name] = int(ports[0].port)

        routes: List[ServiceRoute] = []
        for source, target, _weight in edges:
            port = port_by_service.get(target)
            if port is None:
                # No (or headless) Service for this target — nothing to TCP-probe.
                continue
            protocol = "tcp" if target.startswith(_TCP_DATASTORE_PREFIXES) else "grpc"
            routes.append((source, target, f"{target}:{port}", protocol, f"{source}->{target}"))
        return routes

    def apply_strategy(
        self,
        strategy: PlacementStrategy,
        target_node: Optional[str] = None,
        seed: Optional[int] = None,
        deployments: Optional[List[str]] = None,
        dependencies: Optional[List[Tuple[str, str]]] = None,
        wait: bool = True,
        timeout: int = 300,
        node_existing_usage: Optional[Dict[str, Tuple[int, int]]] = None,
    ) -> NodeAssignment:
        """Compute and apply a placement strategy to all deployments.

        Args:
            strategy: The placement strategy to use.
            target_node: For COLOCATE, pin to this specific node.
            seed: Random seed for RANDOM strategy.
            deployments: Optional list of deployment names to target.
                         If None, targets all deployments in the namespace.
            dependencies: For DEPENDENCY_AWARE, ``(source, target)`` edges.
                         If None and the strategy needs them, they are
                         auto-discovered from the namespace.
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

        if strategy == PlacementStrategy.DEPENDENCY_AWARE and dependencies is None:
            dependencies = self.get_service_dependencies()

        # Best-fit needs realistic free capacity — otherwise it packs
        # everything onto alphabetically-first nodes and the resulting
        # pods go Pending because kube-system / chaos / monitoring pods
        # already consumed much of the allocatable.  Resolution order:
        # explicit argument > cached snapshot (set by the run pipeline
        # for cross-strategy reproducibility) > live query (fallback
        # for standalone `placement apply` callers).
        if strategy == PlacementStrategy.BEST_FIT and node_existing_usage is None:
            node_existing_usage = self.usage_snapshot or self.get_node_pod_usage()

        assignment = compute_assignments(
            strategy=strategy,
            deployments=all_deps,
            nodes=nodes,
            target_node=target_node,
            seed=seed,
            dependencies=dependencies,
            node_existing_usage=node_existing_usage,
        )

        self._apply_assignment(assignment)

        if wait:
            self._wait_for_rollouts(list(assignment.assignments.keys()), timeout)

        # Once the rollouts settle (or the wait was skipped), record where
        # the pods actually landed so a non-1.0 match rate is visible in
        # the run's metadata.  Strategies like topology-spread can fail to
        # satisfy their constraint silently when the cluster doesn't have
        # enough fault domains; without this diff, the run looks identical
        # to a successful application.
        diff = self._compute_intent_actual_diff(assignment)
        assignment.metadata["intendedActualDiff"] = diff
        if diff is not None and diff["matchRate"] < 1.0:
            logger.warning(
                "Placement intent-vs-actual mismatch for %s: %d/%d matched (rate %.2f)",
                assignment.strategy.value,
                len(diff["matched"]),
                len(diff["matched"]) + len(diff["mismatched"]),
                diff["matchRate"],
            )

        return assignment

    # ── Intent-vs-actual diff ────────────────────────────────────

    def _get_deployment_pod_nodes(self, deployment_name: str) -> List[str]:
        """Return the distinct, non-empty node names hosting the deployment's
        active pods.  Excludes pods in ``Succeeded`` / ``Failed`` terminal
        phases (those represent prior rollout generations).

        Distinct from `_get_pod_node` which returns the first match; for
        multi-replica deployments we want every node currently in use so
        the intent-vs-actual diff catches partial mismatches.
        """
        try:
            pods = self.core_api.list_namespaced_pod(
                self.namespace,
                label_selector=f"app={deployment_name}",
            )
        except ApiException:
            return []

        nodes: List[str] = []
        for pod in pods.items:
            phase = (pod.status.phase or "").lower() if pod.status else ""
            if phase in ("succeeded", "failed"):
                continue
            node = getattr(pod.spec, "node_name", None) if pod.spec else None
            if node and node not in nodes:
                nodes.append(node)
        return nodes

    def _compute_intent_actual_diff(
        self,
        assignment: NodeAssignment,
    ) -> Optional[Dict[str, Any]]:
        """Build the `intendedActualDiff` metadata block.

        Returns ``None`` when there is nothing to compare (no assignments —
        e.g. the baseline / default-scheduler runs which leave placement
        unspecified).  Otherwise returns::

            {
                "matched":    [{"deployment", "node"}, ...],
                "mismatched": [{"deployment", "intendedNode", "actualNodes"}, ...],
                "matchRate":  float in [0.0, 1.0],
            }

        A deployment is considered matched when every active pod it owns
        is on the intended node.  Pods on a different node, multiple pods
        on multiple nodes, or no observable pod at all all count as
        mismatches — the latter is recorded with an empty ``actualNodes``
        list so the caller can distinguish "scheduled elsewhere" from
        "didn't schedule at all".
        """
        if not assignment.assignments:
            return None

        matched: List[Dict[str, Any]] = []
        mismatched: List[Dict[str, Any]] = []
        for dep_name, intended in assignment.assignments.items():
            actual_nodes = self._get_deployment_pod_nodes(dep_name)
            if actual_nodes == [intended]:
                matched.append({"deployment": dep_name, "node": intended})
            else:
                mismatched.append(
                    {
                        "deployment": dep_name,
                        "intendedNode": intended,
                        "actualNodes": actual_nodes,
                    }
                )

        total = len(matched) + len(mismatched)
        match_rate = (len(matched) / total) if total else 0.0
        return {
            "matched": matched,
            "mismatched": mismatched,
            "matchRate": round(match_rate, 4),
        }

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
                        "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0},
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

    def observe_pod_placements(self, deployment_names: List[str]) -> Dict[str, str]:
        """Return ``{pod_name: node_name}`` for the given deployments.

        Uses each deployment's own ``spec.selector.matchLabels`` rather
        than assuming an ``app=<name>`` convention, so it works for
        scenarios (Online Boutique, custom microservices) whose pod
        labels do not match the deployment name.  Pods without an
        assigned node, or in a terminal phase, are skipped.
        """
        placements: Dict[str, str] = {}
        for dep_name in deployment_names:
            try:
                dep = self.apps_api.read_namespaced_deployment(dep_name, self.namespace)
            except ApiException:
                continue
            match_labels = (dep.spec.selector.match_labels or {}) if dep.spec.selector else {}
            if not match_labels:
                continue
            selector = ",".join(f"{k}={v}" for k, v in match_labels.items())
            try:
                pods = self.core_api.list_namespaced_pod(self.namespace, label_selector=selector)
            except ApiException:
                continue
            for pod in pods.items:
                node = getattr(pod.spec, "node_name", None)
                if not node:
                    continue
                phase = (pod.status.phase or "").lower()
                if phase in ("succeeded", "failed"):
                    continue
                placements[pod.metadata.name] = node
        return placements

    def _get_pod_node(self, deployment_name: str) -> Optional[str]:
        """Get the node name where a deployment's pod is running."""
        try:
            pods = self.core_api.list_namespaced_pod(
                self.namespace,
                label_selector=f"app={deployment_name}",
            )
            for pod in pods.items:
                if pod.spec.node_name:
                    node_name: Optional[str] = pod.spec.node_name
                    return node_name
        except ApiException:
            # API error → node unknown; report None and let the caller decide.
            pass
        return None

    def _wait_for_rollouts(self, deployment_names: List[str], timeout: int) -> None:
        """Wait for deployments to finish rolling out.

        Checks that the deployment controller has observed the latest
        generation AND that all replicas are updated, ready, and that
        at least one pod is actually Running.  The pod-level check
        guards against the Recreate-strategy race where deployment
        status looks healthy before the new pod is scheduled.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        click.echo(f"  Waiting for {len(deployment_names)} rollout(s) (timeout: {timeout}s)...")
        start = time.time()

        # Brief pause to let the controller start processing the patch
        time.sleep(5)

        pending = set(deployment_names)
        while pending and (time.time() - start) < timeout:
            still_pending = set()
            # Check all pending deployments in parallel
            with ThreadPoolExecutor(max_workers=min(len(pending), 8)) as executor:
                futures = {
                    executor.submit(self._check_deployment_ready, name): name for name in pending
                }
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        is_ready = future.result()
                        if is_ready:
                            elapsed = int(time.time() - start)
                            click.echo(f"    {name}: ready ({elapsed}s)")
                        else:
                            still_pending.add(name)
                    except Exception:
                        still_pending.add(name)

            pending = still_pending
            if pending:
                time.sleep(3)

        if pending:
            elapsed = int(time.time() - start)
            for name in pending:
                click.echo(f"    WARNING: {name}: not ready after {elapsed}s")

    def _check_deployment_ready(self, name: str) -> bool:
        """Check if a deployment is fully rolled out with running pods."""
        dep = self.apps_api.read_namespaced_deployment(name, self.namespace)
        desired = dep.spec.replicas or 1
        generation = dep.metadata.generation or 0
        observed = (
            dep.status.observed_generation if dep.status and dep.status.observed_generation else 0
        )
        ready = (dep.status.ready_replicas or 0) if dep.status else 0
        updated = (dep.status.updated_replicas or 0) if dep.status else 0
        available = (dep.status.available_replicas or 0) if dep.status else 0

        if not (
            observed >= generation
            and updated >= desired
            and ready >= desired
            and available >= desired
        ):
            return False

        # Verify at least one pod is actually Running and Ready.
        # Deployment status can briefly report stale values during
        # Recreate-strategy rollouts.
        # Use the deployment's own matchLabels instead of assuming app={name}.
        try:
            match_labels = dep.spec.selector.match_labels or {}
            if not match_labels:
                return True  # can't verify pods, trust deployment status
            label_selector = ",".join(f"{k}={v}" for k, v in match_labels.items())
            pods = self.core_api.list_namespaced_pod(
                self.namespace,
                label_selector=label_selector,
            )
            running_ready = 0
            for pod in pods.items:
                if pod.status.phase != "Running":
                    continue
                for cond in pod.status.conditions or []:
                    if cond.type == "Ready" and cond.status == "True":
                        running_ready += 1
                        break
            return running_ready >= desired
        except ApiException:
            # If we can't list pods, trust the deployment status
            return True
