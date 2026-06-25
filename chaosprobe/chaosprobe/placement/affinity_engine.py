"""Replica-level affinity placement engine (M1b).

Implements DESIGN §2.2 / Knob B of ``design/00-DESIGN.md``: placement is
expressed as **replica-level affinity constraints** the scheduler satisfies —
never assumed — for ``r ∈ {1, 3}`` replicas crossed with a binary packing
mode (M1b build item):

- **r = 1** — nodeAffinity ``requiredDuringSchedulingIgnoredDuringExecution``
  pin to the assigned node: the nodeSelector semantics expressed as
  affinity (the comparability anchor).  The packing mode is recorded but the
  patch is the pin either way — with a single replica the two modes are
  physically identical.
- **r = 3 packed** — the same nodeAffinity pin with ``replicas: 3``: all
  replicas co-scheduled on one node, deliberately reproducing the packed
  structural behaviour as the control arm.
- **r = 3 anti-affine** — required ``podAntiAffinity`` on
  ``kubernetes.io/hostname`` against the service's own ``app`` label and
  **no node pin**: the scheduler must put the 3 replicas on 3 distinct
  nodes of its own choosing.  This is the contrast the skipped E1 pilot
  could not realize (DESIGN §2.1).

**r = 2 is deliberately unsupported** (DESIGN §2.3: no hypothesis
samples it, so a middle level would only inflate the M1b acceptance burden).

The base mutator's managed-annotation convention
(``chaosprobe.io/placement-strategy``) is kept so existing restore tooling
recognises engine-managed deployments; the engine's values are namespaced as
``affinity-r<r>-<mode>``.  Verification (:func:`verify_placement`) reads the
**live ready pods** per service and never trusts the patch.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from kubernetes import client
from kubernetes.client.rest import ApiException

from chaosprobe.k8s import ensure_k8s_config
from chaosprobe.orchestrator.preflight import LITMUS_INFRA_DEPLOYMENTS
from chaosprobe.placement.mutator import MANAGED_ANNOTATION, PLACEMENT_LABEL_KEY

logger = logging.getLogger(__name__)

#: Replica counts the engine supports (DESIGN §2.3 — r = 2 deliberately omitted).
SUPPORTED_REPLICAS = (1, 3)

#: All replicas co-scheduled on one node (the packed structural behaviour, the control).
MODE_PACKED = "packed"
#: Replicas forced onto distinct nodes via required podAntiAffinity (E1-enabling).
MODE_ANTI_AFFINE = "anti-affine"
#: The binary replica-packing modes of Knob B.
MODES = (MODE_PACKED, MODE_ANTI_AFFINE)

#: Managed-annotation value prefix marking engine-applied placements.
ANNOTATION_PREFIX = "affinity-r"

#: Deployments the engine never touches on discovery: chaos infra plus the
#: load generator (replicating it would scale the offered load with r).
EXCLUDED_DEPLOYMENTS = set(LITMUS_INFRA_DEPLOYMENTS) | {"loadgenerator"}


@dataclass
class K8sApi:
    """The two Kubernetes API surfaces the engine needs, injectable for tests."""

    apps: client.AppsV1Api
    core: client.CoreV1Api

    @classmethod
    def from_cluster(cls) -> "K8sApi":
        """Build live clients from the active kubeconfig."""
        ensure_k8s_config()
        return cls(apps=client.AppsV1Api(), core=client.CoreV1Api())


@dataclass
class ApplyResult:
    """Outcome of :func:`apply_placement` — what was patched and what settled."""

    applied: List[str]
    pending: List[str]
    duration_seconds: float


@dataclass
class ServiceCheck:
    """Per-service live verification detail (carried into the gate artifact)."""

    service: str
    ok: bool
    reason: str
    ready_replicas: int
    nodes: List[str]
    assigned_node: Optional[str]


@dataclass
class VerificationResult:
    """Live placement verification for one (r, mode) expectation."""

    r: int
    mode: str
    passed: bool
    services: List[ServiceCheck] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-ready shape for the M1b gate artifact (camelCase keys)."""
        return {
            "r": self.r,
            "mode": self.mode,
            "passed": self.passed,
            "services": [
                {
                    "service": check.service,
                    "ok": check.ok,
                    "reason": check.reason,
                    "readyReplicas": check.ready_replicas,
                    "nodes": check.nodes,
                    "assignedNode": check.assigned_node,
                }
                for check in self.services
            ],
        }


def annotation_value(r: int, mode: str) -> str:
    """The managed-annotation value recorded for an (r, mode) placement."""
    return f"{ANNOTATION_PREFIX}{r}-{mode}"


def _validate_combo(r: int, mode: str) -> None:
    """Reject unsupported (r, mode) coordinates before any patch is built."""
    if r not in SUPPORTED_REPLICAS:
        raise ValueError(
            f"unsupported replica count r={r}: only r ∈ {SUPPORTED_REPLICAS} "
            "(r=2 is deliberately unsupported per DESIGN §2.3)"
        )
    if mode not in MODES:
        raise ValueError(f"unsupported mode '{mode}': expected one of {MODES}")


def _is_pinned(r: int, mode: str) -> bool:
    """True when the (r, mode) cell pins to a single node (r=1, or r=3 packed)."""
    return r == 1 or mode == MODE_PACKED


def packed_round_robin(services: Sequence[str], workers: Sequence[str]) -> Dict[str, str]:
    """Capacity-feasible packed assignment: sorted service *i* → worker *i mod W*.

    The C2 / H3 **per-service** packing semantics — every service's replicas
    co-scheduled on ONE node — with services distributed ACROSS nodes, *not*
    all services stacked on one node.  The fraction solver's f = 0 assignment
    (which the H1 dose-response sweep needs, but H3 does not) satisfies a
    low cut fraction by stacking services on a single worker, which at r = 3
    needs ~3× the whole app's requests on one node — unschedulable by
    arithmetic on this cluster's 4 GiB workers.  Round-robin minimises the
    per-node service count (⌈S/W⌉), needs no live capacity reads, and
    :func:`verify_placement` still proves the packing (each service's replicas
    on exactly its pinned node) from live pods.

    This is the single source for the round-robin packing
    (§H3 packed-cell semantics) and verified by the M1b
    gate; the gate and the live session orchestrator both call it.
    """
    if not workers:
        raise ValueError("workers must be a non-empty list of worker node names")
    return {svc: workers[i % len(workers)] for i, svc in enumerate(sorted(services))}


def build_patch(
    service: str,
    node_name: Optional[str],
    r: int,
    mode: str,
    node_names: Sequence[str],
) -> Dict[str, Any]:
    """Deployment spec patch for one service at the (r, mode) cell.

    Every patch sets ``replicas``, the managed annotation, a ``Recreate``
    rollout (old pods terminate before new ones schedule, so the verified
    placement is never a mixed generation), and deletes any stale
    ``kubernetes.io/hostname`` nodeSelector pin.

    Args:
        service: Deployment name; for anti-affine its ``app`` label is the
            podAntiAffinity selector (the Online Boutique convention).
        node_name: Target node for pinned cells (r=1 or r=3 packed); must be
            ``None`` for r=3 anti-affine, where the scheduler chooses.
        r: Replica count, one of :data:`SUPPORTED_REPLICAS`.
        mode: :data:`MODE_PACKED` or :data:`MODE_ANTI_AFFINE`.
        node_names: The schedulable worker names — pins are validated against
            this set, and anti-affine requires at least ``r`` distinct names.

    Returns:
        A strategic-merge patch dict for ``patch_namespaced_deployment``.
    """
    _validate_combo(r, mode)
    if not node_names:
        raise ValueError("node_names must be a non-empty list of worker node names")

    if _is_pinned(r, mode):
        if node_name is None:
            raise ValueError(f"r={r} mode={mode} pins to a node: node_name is required")
        if node_name not in node_names:
            raise ValueError(f"node '{node_name}' is not in node_names {sorted(node_names)}")
        affinity: Dict[str, Any] = {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {
                            "matchExpressions": [
                                {
                                    "key": PLACEMENT_LABEL_KEY,
                                    "operator": "In",
                                    "values": [node_name],
                                }
                            ]
                        }
                    ]
                }
            },
            "podAntiAffinity": None,
        }
    else:  # r = 3 anti-affine: no pin, scheduler spreads over distinct hostnames
        if node_name is not None:
            raise ValueError("r=3 anti-affine takes no node pin: the scheduler chooses the nodes")
        if len(set(node_names)) < r:
            raise ValueError(
                f"anti-affine r={r} needs >= {r} distinct nodes, got {len(set(node_names))}"
            )
        affinity = {
            "nodeAffinity": None,
            "podAntiAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": [
                    {
                        "labelSelector": {"matchLabels": {"app": service}},
                        "topologyKey": PLACEMENT_LABEL_KEY,
                    }
                ]
            },
        }

    return {
        "metadata": {"annotations": {MANAGED_ANNOTATION: annotation_value(r, mode)}},
        "spec": {
            "replicas": r,
            "strategy": {"type": "Recreate", "rollingUpdate": None},
            "template": {
                "spec": {
                    # Delete only the hostname key a prior pin may have left behind.
                    "nodeSelector": {PLACEMENT_LABEL_KEY: None},
                    "affinity": affinity,
                }
            },
        },
    }


# ──────────────────────────────────────────────────────────────────────
# Live state readers (verification never assumes — it reads pods)
# ──────────────────────────────────────────────────────────────────────


def _ready_pod_nodes(api: K8sApi, namespace: str, dep: Any) -> List[str]:
    """Node names of the deployment's Running+Ready pods (one entry per pod).

    Selects pods by the deployment's own ``spec.selector.matchLabels``
    (falling back to ``app=<name>``), the same convention as the base
    mutator's ``observe_pod_placements``.
    """
    match_labels = (dep.spec.selector.match_labels or {}) if dep.spec.selector else {}
    if not match_labels:
        match_labels = {"app": dep.metadata.name}
    selector = ",".join(f"{k}={v}" for k, v in sorted(match_labels.items()))
    try:
        pods = api.core.list_namespaced_pod(namespace, label_selector=selector).items
    except ApiException:
        return []
    nodes: List[str] = []
    for pod in pods:
        if pod.status is None or pod.status.phase != "Running":
            continue
        if not any(
            cond.type == "Ready" and cond.status == "True" for cond in (pod.status.conditions or [])
        ):
            continue
        node = pod.spec.node_name if pod.spec else None
        if node:
            nodes.append(node)
    return nodes


def _pinned_node(dep: Any) -> Optional[str]:
    """The hostname this deployment's nodeAffinity pins to, if any."""
    affinity = dep.spec.template.spec.affinity
    node_affinity = affinity.node_affinity if affinity else None
    required = (
        node_affinity.required_during_scheduling_ignored_during_execution if node_affinity else None
    )
    terms = required.node_selector_terms if required else None
    for term in terms or []:
        for expr in term.match_expressions or []:
            if expr.key == PLACEMENT_LABEL_KEY and expr.operator == "In" and expr.values:
                return str(expr.values[0])
    return None


def _deployment_ready(api: K8sApi, namespace: str, name: str) -> bool:
    """Rolled out and live: generation observed, all replicas updated/ready/
    available, and the ready-pod count confirmed at pod level (guards the
    Recreate race where deployment status briefly reads stale)."""
    dep = api.apps.read_namespaced_deployment(name, namespace)
    desired = dep.spec.replicas if dep.spec.replicas is not None else 1
    generation = dep.metadata.generation or 0
    status = dep.status
    observed = (status.observed_generation or 0) if status else 0
    ready = (status.ready_replicas or 0) if status else 0
    updated = (status.updated_replicas or 0) if status else 0
    available = (status.available_replicas or 0) if status else 0
    if not (observed >= generation and updated >= desired and ready >= desired):
        return False
    if available < desired:
        return False
    return len(_ready_pod_nodes(api, namespace, dep)) >= desired


def wait_for_rollouts(
    api: K8sApi,
    namespace: str,
    names: Sequence[str],
    timeout: float = 300,
    poll_seconds: float = 3.0,
) -> List[str]:
    """Poll until every named deployment is rolled out or ``timeout`` elapses.

    Returns the names still pending at the deadline (empty on full success).
    """
    deadline = time.monotonic() + timeout
    pending = sorted(names)
    while pending:
        still: List[str] = []
        for name in pending:
            try:
                ok = _deployment_ready(api, namespace, name)
            except ApiException:
                ok = False
            if not ok:
                still.append(name)
        pending = still
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(poll_seconds)
    return pending


def live_service_nodes(
    api: K8sApi, namespace: str, services: Sequence[str]
) -> Dict[str, List[str]]:
    """``{service: sorted distinct nodes of its Running+Ready pods}``.

    The live read the M1b gate's achieved-fraction recomputation uses — a
    service with anything other than exactly one node at r = 1 makes the
    attempt unverifiable rather than silently counted.
    """
    out: Dict[str, List[str]] = {}
    for svc in sorted(services):
        try:
            dep = api.apps.read_namespaced_deployment(svc, namespace)
        except ApiException:
            out[svc] = []
            continue
        out[svc] = sorted(set(_ready_pod_nodes(api, namespace, dep)))
    return out


# ──────────────────────────────────────────────────────────────────────
# Apply / verify / restore
# ──────────────────────────────────────────────────────────────────────


def _discover_services(api: K8sApi, namespace: str) -> List[str]:
    """Application deployments in the namespace (chaos infra + loadgen excluded)."""
    deps = api.apps.list_namespaced_deployment(namespace).items
    return sorted(
        dep.metadata.name for dep in deps if dep.metadata.name not in EXCLUDED_DEPLOYMENTS
    )


def apply_placement(
    api: K8sApi,
    namespace: str,
    assignment: Optional[Mapping[str, str]],
    r: int,
    mode: str,
    node_names: Sequence[str],
    wait: bool = True,
    timeout: float = 300,
    poll_seconds: float = 3.0,
    services: Optional[Sequence[str]] = None,
) -> ApplyResult:
    """Patch the namespace's deployments to the (r, mode) cell and settle.

    Args:
        api: Kubernetes API pair.
        namespace: Application namespace.
        assignment: ``{service: node}`` for pinned cells (r=1, or r=3
            packed).  Must be ``None`` for r=3 anti-affine, where the
            application deployments are patched with the no-pin
            anti-affinity constraint.
        r: Replica count, one of :data:`SUPPORTED_REPLICAS`.
        mode: :data:`MODE_PACKED` or :data:`MODE_ANTI_AFFINE`.
        node_names: Schedulable worker names (pin validation / spread arity).
        wait: Block until rollouts settle (the schedule step of the
            solve→apply→schedule→verify cycle).
        timeout: Rollout wait deadline in seconds.
        poll_seconds: Rollout poll interval.
        services: Explicit service set for the r=3 anti-affine cell (the
            session driver passes its own discovered set so a deliberately
            scaled-to-zero deployment is not resurrected at ``replicas: 3``).
            ``None`` falls back to namespace discovery; ignored for pinned
            cells, whose service set is the ``assignment``'s keys.

    Returns:
        :class:`ApplyResult` with the patched names, any deployments still
        pending at the deadline, and the wall-clock scheduling latency.
    """
    _validate_combo(r, mode)
    targets: Dict[str, Optional[str]]
    if r == 3 and mode == MODE_ANTI_AFFINE:
        if assignment is not None:
            raise ValueError("r=3 anti-affine takes no assignment: pass assignment=None")
        anti_affine_services = (
            list(services) if services is not None else _discover_services(api, namespace)
        )
        targets = {svc: None for svc in anti_affine_services}
        if not targets:
            raise ValueError(f"no application deployments found in namespace '{namespace}'")
    else:
        if not assignment:
            raise ValueError(f"r={r} mode={mode} pins to nodes: a non-empty assignment is required")
        targets = dict(assignment)

    started = time.monotonic()
    names = sorted(targets)
    for svc in names:
        patch = build_patch(svc, targets[svc], r, mode, node_names)
        api.apps.patch_namespaced_deployment(svc, namespace, patch)

    pending: List[str] = []
    if wait:
        pending = wait_for_rollouts(api, namespace, names, timeout, poll_seconds)
        if pending:
            logger.warning(
                "apply_placement(r=%d, mode=%s): %d deployment(s) not ready after %.0fs: %s",
                r,
                mode,
                len(pending),
                timeout,
                ", ".join(pending),
            )
    return ApplyResult(applied=names, pending=pending, duration_seconds=time.monotonic() - started)


def _check_service(
    actual_annotation: str,
    expected_annotation: str,
    r: int,
    mode: str,
    ready_nodes: Sequence[str],
    distinct: Sequence[str],
    assigned: Optional[str],
) -> Tuple[bool, str]:
    """The decidable per-service pass/fail predicate for one (r, mode) cell."""
    if actual_annotation != expected_annotation:
        return False, (
            f"managed annotation is '{actual_annotation}', expected '{expected_annotation}'"
        )
    if len(ready_nodes) != r:
        return False, f"{len(ready_nodes)} ready replica(s), expected {r}"
    if not _is_pinned(r, mode):  # r = 3 anti-affine: exactly r distinct nodes
        if len(distinct) != r:
            return False, f"replicas occupy {len(distinct)} distinct node(s), expected {r}"
        return True, ""
    if len(distinct) != 1:
        return False, f"replicas occupy {len(distinct)} distinct node(s), expected exactly 1"
    if assigned is None:
        return False, "no node pin found in the deployment's nodeAffinity"
    if distinct[0] != assigned:
        return False, f"replicas on '{distinct[0]}', pinned to '{assigned}'"
    return True, ""


def verify_placement(api: K8sApi, namespace: str, r: int, mode: str) -> VerificationResult:
    """Verify the **live** placement against the (r, mode) expectation.

    Never assumes: for every engine-managed deployment the Running+Ready
    pods are read back and the decidable predicate applied —

    - r = 1: exactly 1 ready replica, on the pinned node;
    - r = 3 packed: exactly 3 ready replicas, all on the pinned node;
    - r = 3 anti-affine: exactly 3 ready replicas on exactly 3 distinct nodes.

    A deployment annotated with a *different* (r, mode) than asked fails its
    check (stale state is a finding, not a skip).  No managed deployments at
    all is an overall FAIL.  The per-service detail feeds the gate artifact.
    """
    _validate_combo(r, mode)
    expected = annotation_value(r, mode)
    deps = api.apps.list_namespaced_deployment(namespace).items
    checks: List[ServiceCheck] = []
    for dep in deps:
        annotations = dep.metadata.annotations or {}
        value = annotations.get(MANAGED_ANNOTATION) or ""
        if not value.startswith(ANNOTATION_PREFIX):
            continue
        ready_nodes = _ready_pod_nodes(api, namespace, dep)
        distinct = sorted(set(ready_nodes))
        assigned = _pinned_node(dep)
        ok, reason = _check_service(value, expected, r, mode, ready_nodes, distinct, assigned)
        checks.append(
            ServiceCheck(
                service=dep.metadata.name,
                ok=ok,
                reason=reason,
                ready_replicas=len(ready_nodes),
                nodes=distinct,
                assigned_node=assigned,
            )
        )
    passed = bool(checks) and all(check.ok for check in checks)
    return VerificationResult(r=r, mode=mode, passed=passed, services=checks)


def restore(
    api: K8sApi,
    namespace: str,
    wait: bool = True,
    timeout: float = 300,
    poll_seconds: float = 3.0,
) -> List[str]:
    """Clear engine patches back to single-replica unpinned scheduling.

    Extends the base mutator's managed-annotation discovery: any deployment
    carrying the ``chaosprobe.io/placement-strategy`` annotation (engine- or
    base-applied) or a stale ``kubernetes.io/hostname`` nodeSelector is reset
    to ``replicas: 1``, affinity removed, hostname pin deleted, annotation
    cleared, and the default RollingUpdate strategy restored.  Chaos-infra
    deployments are never touched.

    Returns the names of the deployments that were reset.
    """
    deps = api.apps.list_namespaced_deployment(namespace).items
    cleared: List[str] = []
    for dep in deps:
        name = dep.metadata.name
        if name in LITMUS_INFRA_DEPLOYMENTS:
            continue
        annotations = dep.metadata.annotations or {}
        node_selector = dep.spec.template.spec.node_selector or {}
        if MANAGED_ANNOTATION not in annotations and PLACEMENT_LABEL_KEY not in node_selector:
            continue
        patch = {
            "metadata": {"annotations": {MANAGED_ANNOTATION: None}},
            "spec": {
                "replicas": 1,
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0},
                },
                "template": {
                    "spec": {
                        "nodeSelector": {PLACEMENT_LABEL_KEY: None},
                        "affinity": None,
                    }
                },
            },
        }
        api.apps.patch_namespaced_deployment(name, namespace, patch)
        cleared.append(name)
    if wait and cleared:
        pending = wait_for_rollouts(api, namespace, cleared, timeout, poll_seconds)
        if pending:
            logger.warning(
                "restore: %d deployment(s) not ready after %.0fs: %s",
                len(pending),
                timeout,
                ", ".join(pending),
            )
    return cleared
