"""Protocol-labeled conntrack prober — the M1b first-class collector.

The earlier evidence for H2 came from an ad-hoc probe (``thesis/data/
conntrack-probe/``): four hand-applied ``hostNetwork`` alpine pods sampling
``conntrack -L`` protocol counts every 5 s while two single-iteration runs
provided the kill cycles.  That probe had two recorded defects — *i* = 1 (no
replication) and a ramp-contaminated pre-window — plus a toolchain gap: the
``conntrack-tools`` package was installed **unpinned** and the resolved
version was never recorded (M1a finding I2).

Per the design (§4) the probe graduates into a built-in collector: this module
creates one privileged ``hostNetwork`` sampler pod per worker node, samples
each host's conntrack table every 5 s for the whole iteration (pre/chaos/post
phases tracked by :class:`~chaosprobe.metrics.base.ContinuousProberBase`),
and surfaces the samples in ``summary.json`` as ``conntrackProtocolSamples``
(``[{ts, node, proto, count, phase}, …]``) plus ``conntrackProtocolMeta``
(``{toolVersion, intervalSeconds, samplerImage, …}``).  Chaos windows are
already recorded per iteration (``anomalyLabels``), so samples align with
them by timestamp.

The I2 fix is twofold: the package install is **pinned** to the exact
Alpine 3.20 release (``apk add conntrack-tools=1.4.8-r0``) and the running
binary's version is independently **recorded** by exec'ing ``conntrack
--version`` in every sampler after start, landing in
``conntrackProtocolMeta.toolVersion`` / ``toolVersionsByNode``.

Every cluster-facing step degrades gracefully (mirroring the disk prober):
a missing sampler, a failed ``apk`` install, or an exec error produces a
warning and a gap in the samples — never a crashed run.  ``cleanup()``
removes the sampler pods by managed-label selector and is likewise
best-effort.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from kubernetes import client
from kubernetes.client.rest import ApiException

from chaosprobe.k8s import ensure_k8s_config
from chaosprobe.metrics import base as _base
from chaosprobe.metrics.base import ContinuousProberBase

logger = logging.getLogger(__name__)

# Sampler pods are cluster infrastructure, not workload: they live in their
# own namespace so app-namespace tooling (probe-pod discovery, cleanup of the
# workload namespace, blast-radius accounting) never sees them.  The repo has
# no shared infra namespace (the registry uses ``registry``, Litmus uses
# ``litmus``), so the prober owns ``chaosprobe-system``.
SAMPLER_NAMESPACE = "chaosprobe-system"

SAMPLER_IMAGE = "alpine:3.20"

# M1a finding I2: the earlier setup installed conntrack-tools unpinned and never recorded
# the resolved version.  Pin the exact Alpine 3.20 package (resolved from the
# v3.20/main APKINDEX on 2026-06-11); the running binary's version is
# additionally recorded post-start via ``conntrack --version``.  If Alpine
# ever revs the package and the pinned install fails, the sampler never
# becomes ready and the prober degrades gracefully (warning + node dropped)
# rather than silently sampling with an unrecorded version.
CONNTRACK_PACKAGE_PIN = "conntrack-tools=1.4.8-r0"

# Managed labels: cleanup() selects on exactly these, so anything the prober
# creates — and only that — is removed.
MANAGED_LABELS = {
    "app.kubernetes.io/managed-by": "chaosprobe",
    "app.kubernetes.io/component": "conntrack-sampler",
}
MANAGED_LABEL_SELECTOR = ",".join(f"{k}={v}" for k, v in sorted(MANAGED_LABELS.items()))

# Node label key recording which node a sampler pod reads (hostNetwork pods
# report the node's own view, so the binding matters for analysis).
NODE_LABEL_KEY = "chaosprobe.io/node"

# The original probe's exact sampling command (thesis/data/conntrack-probe/
# sampler.sh), kept verbatim so new samples stay comparable with the original CSV:
# one "<count> <proto>" line per protocol in the host's conntrack table.
SAMPLE_COMMAND = [
    "sh",
    "-c",
    "conntrack -L 2>/dev/null | awk '{print $1}' | sort | uniq -c",
]

VERSION_COMMAND = ["sh", "-c", "conntrack --version 2>&1"]

# Control-plane nodes are excluded from sampling: the workload (and therefore
# the placement-dependent conntrack signal) only ever lands on workers.
_CONTROL_PLANE_LABELS = (
    "node-role.kubernetes.io/control-plane",
    "node-role.kubernetes.io/master",
)

# "  910 udp" → ("910", "udp").  Anything else (headers, conntrack warnings,
# partial lines from a dying exec stream) is silently skipped.
_COUNT_PROTO_RE = re.compile(r"^\s*(\d+)\s+(\S+)\s*$")

# "conntrack v1.4.8 (conntrack-tools)" → "1.4.8".
_VERSION_RE = re.compile(r"conntrack\s+v?([0-9][0-9A-Za-z._-]*)")

# Signature of base.exec_in_pod: (core_api, namespace, pod, command) -> str.
ExecFn = Callable[[Any, str, str, List[str]], str]


def parse_conntrack_protocol_counts(output: str) -> List[Dict[str, Any]]:
    """Parse ``uniq -c`` output into ``[{"proto": str, "count": int}, …]``.

    Tolerates an empty conntrack table (empty output → ``[]``) and garbage
    lines (skipped).  Protocol names are lower-cased so ``TCP``/``tcp``
    aggregate identically downstream.
    """
    rows: List[Dict[str, Any]] = []
    for line in (output or "").splitlines():
        m = _COUNT_PROTO_RE.match(line)
        if not m:
            continue
        rows.append({"proto": m.group(2).lower(), "count": int(m.group(1))})
    return rows


def parse_conntrack_version(output: str) -> Optional[str]:
    """Extract the resolved tool version from ``conntrack --version`` output.

    Returns ``None`` when the output is an exec error or doesn't look like a
    version banner (e.g. the ``apk add`` install hasn't finished yet).
    """
    if not output or output.startswith("ERROR:"):
        return None
    m = _VERSION_RE.search(output)
    return m.group(1) if m else None


def sampler_pod_name(node_name: str) -> str:
    """Deterministic sampler pod name for *node_name* (idempotency key)."""
    return f"chaosprobe-conntrack-sampler-{node_name}"


def build_sampler_pod_manifest(node_name: str) -> Dict[str, Any]:
    """Build the sampler pod manifest for *node_name*.

    Mirrors the original ad-hoc probe pods (``thesis/data/conntrack-probe/
    probe-pods.yaml``) — ``hostNetwork`` + privileged so ``conntrack -L``
    reads the *host's* table via netlink, tolerations for all taints so
    cordoned/tainted workers keep reporting through node-level faults —
    plus the two added fixes: managed labels for selector-based cleanup and
    the pinned ``conntrack-tools`` install.
    """
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": sampler_pod_name(node_name),
            "namespace": SAMPLER_NAMESPACE,
            "labels": {**MANAGED_LABELS, NODE_LABEL_KEY: node_name},
        },
        "spec": {
            "nodeName": node_name,
            "hostNetwork": True,
            "restartPolicy": "Never",
            "tolerations": [{"operator": "Exists"}],
            "containers": [
                {
                    "name": "sampler",
                    "image": SAMPLER_IMAGE,
                    "command": [
                        "sh",
                        "-c",
                        f"apk add --no-cache {CONNTRACK_PACKAGE_PIN} && exec sleep 2147483647",
                    ],
                    "securityContext": {"privileged": True},
                }
            ],
        },
    }


def cleanup_sampler_pods(core_api: Any) -> int:
    """Delete all conntrack sampler pods (managed-label selector).

    Best-effort: every failure degrades to a warning so cleanup can never
    crash the run (the disk-prober graceful-degradation precedent).  Returns
    the number of pods successfully deleted.
    """
    try:
        pods = core_api.list_namespaced_pod(
            SAMPLER_NAMESPACE,
            label_selector=MANAGED_LABEL_SELECTOR,
        )
    except Exception as exc:
        logger.warning("conntrack sampler cleanup: could not list sampler pods: %s", exc)
        return 0

    deleted = 0
    for pod in pods.items:
        name = pod.metadata.name
        try:
            core_api.delete_namespaced_pod(name, SAMPLER_NAMESPACE)
            deleted += 1
        except Exception as exc:
            logger.warning("conntrack sampler cleanup: could not delete pod %s: %s", name, exc)
    return deleted


class ConntrackProtocolProber(ContinuousProberBase):
    """Samples per-node, per-protocol conntrack entry counts during chaos.

    Lifecycle matches every other continuous prober (``start()`` /
    ``mark_chaos_start()`` / ``mark_chaos_end()`` / ``stop()`` /
    ``result()``), so ``orchestrator/probers.py`` manages it identically.
    ``start()`` discovers the worker nodes, ensures one sampler pod per
    worker (idempotent — pods persist across iterations and are adopted,
    not re-created), records the resolved conntrack-tools version per node,
    then launches the 5 s sampling thread.

    The Kubernetes exec transport is injectable (*exec_fn*, defaulting to
    :func:`chaosprobe.metrics.base.exec_in_pod` which wraps
    ``kubernetes.stream.stream``) so tests can feed canned outputs without a
    cluster.

    Usage::

        prober = ConntrackProtocolProber("online-boutique")
        prober.start()
        prober.mark_chaos_start()
        # ... chaos ...
        prober.mark_chaos_end()
        prober.stop()
        data = prober.result()   # {"samples": [...], "meta": {...}}
    """

    def __init__(
        self,
        namespace: str,
        interval: float = 5.0,
        exec_fn: Optional[ExecFn] = None,
        ready_timeout: float = 120.0,
        ready_poll_interval: float = 3.0,
    ):
        super().__init__(namespace, interval, name="conntrack-prober")
        ensure_k8s_config()
        self.core_api = client.CoreV1Api()
        self._exec: ExecFn = exec_fn if exec_fn is not None else _base.exec_in_pod
        self._ready_timeout = ready_timeout
        self._ready_poll_interval = ready_poll_interval
        # node -> sampler pod name; populated by ensure_samplers().
        self._samplers: Dict[str, str] = {}
        # node -> resolved conntrack-tools version (M1a I2: must be recorded).
        self._tool_versions: Dict[str, str] = {}
        self._samples: List[Dict[str, Any]] = []
        self._unavailable_reason: Optional[str] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Ensure per-worker sampler pods, then start the sampling thread.

        Sampler setup is best-effort: any failure leaves the prober running
        with zero samplers (``meta.available = False`` + reason in the
        output) rather than crashing the run.
        """
        try:
            nodes = self._list_worker_nodes()
            if nodes:
                self.ensure_samplers(self.core_api, nodes)
                if not self._samplers:
                    self._unavailable_reason = (
                        "no sampler pod became ready (image pull or "
                        f"'apk add {CONNTRACK_PACKAGE_PIN}' may have failed)"
                    )
            else:
                self._unavailable_reason = "no worker nodes discovered"
                logger.warning("Conntrack prober: %s — sampling disabled", self._unavailable_reason)
        except Exception as exc:
            self._unavailable_reason = f"sampler setup failed: {exc}"
            logger.warning("Conntrack prober: %s — sampling disabled", self._unavailable_reason)
        super().start()

    def ensure_samplers(self, core_api: Any, node_names: List[str]) -> Dict[str, str]:
        """Ensure one ready sampler pod per node in *node_names*.

        Idempotent: existing managed sampler pods are adopted (matched by
        the ``chaosprobe.io/node`` label), missing ones are created.  Each
        sampler is then polled until ``conntrack --version`` answers —
        proof the pinned package install finished — and the resolved
        version is recorded.  Nodes whose sampler never becomes ready are
        dropped with a warning.  Returns the ``{node: pod_name}`` map of
        ready samplers.
        """
        self._ensure_namespace(core_api)

        existing = self._existing_samplers_by_node(core_api)
        pending: Dict[str, str] = {}
        for node in node_names:
            if node in existing:
                pending[node] = existing[node]
                continue
            manifest = build_sampler_pod_manifest(node)
            try:
                core_api.create_namespaced_pod(SAMPLER_NAMESPACE, manifest)
                pending[node] = manifest["metadata"]["name"]
            except ApiException as exc:
                if exc.status == 409:
                    # Created concurrently (or label drifted) — adopt by name.
                    pending[node] = sampler_pod_name(node)
                else:
                    logger.warning(
                        "Conntrack prober: could not create sampler pod on node %s: %s",
                        node,
                        exc,
                    )

        # Wait for readiness in parallel: a single broken node must cost at
        # most one ready_timeout, not one per node, before the run proceeds.
        futures = {}
        if pending:
            with ThreadPoolExecutor(max_workers=min(len(pending), 8)) as pool:
                futures = {
                    node: pool.submit(self._wait_for_sampler_ready, core_api, pod)
                    for node, pod in pending.items()
                }
        for node, future in futures.items():
            pod = pending[node]
            try:
                version = future.result()
            except Exception as exc:  # injected exec_fn may raise
                logger.warning(
                    "Conntrack prober: readiness probe for sampler %s (node %s) raised: %s",
                    pod,
                    node,
                    exc,
                )
                version = None
            if version is None:
                logger.warning(
                    "Conntrack prober: sampler %s (node %s) not ready within %.0fs — "
                    "node excluded from sampling",
                    pod,
                    node,
                    self._ready_timeout,
                )
                continue
            self._samplers[node] = pod
            self._tool_versions[node] = version

        if self._samplers:
            logger.info(
                "Conntrack prober sampling %d node(s): %s (conntrack-tools %s)",
                len(self._samplers),
                ", ".join(sorted(self._samplers)),
                ", ".join(sorted(set(self._tool_versions.values()))),
            )
        return dict(self._samplers)

    def cleanup(self, core_api: Optional[Any] = None) -> int:
        """Remove the sampler pods (managed-label selector); best-effort."""
        return cleanup_sampler_pods(core_api if core_api is not None else self.core_api)

    # ------------------------------------------------------------------
    # Sampler setup internals
    # ------------------------------------------------------------------

    def _list_worker_nodes(self) -> List[str]:
        """List schedulable worker node names (control planes excluded)."""
        try:
            nodes = self.core_api.list_node()
        except Exception as exc:
            logger.warning("Conntrack prober: could not list nodes: %s", exc)
            return []
        names: List[str] = []
        for node in nodes.items:
            labels = (node.metadata.labels if node.metadata else None) or {}
            if any(key in labels for key in _CONTROL_PLANE_LABELS):
                continue
            if node.metadata and node.metadata.name:
                names.append(node.metadata.name)
        return sorted(names)

    @staticmethod
    def _ensure_namespace(core_api: Any) -> None:
        """Create the sampler namespace if missing (409 = already exists)."""
        try:
            core_api.create_namespace({"metadata": {"name": SAMPLER_NAMESPACE}})
        except ApiException as exc:
            if exc.status != 409:
                logger.warning(
                    "Conntrack prober: could not create namespace %s: %s",
                    SAMPLER_NAMESPACE,
                    exc,
                )

    @staticmethod
    def _existing_samplers_by_node(core_api: Any) -> Dict[str, str]:
        """Map node → existing managed sampler pod (for idempotent reuse)."""
        try:
            pods = core_api.list_namespaced_pod(
                SAMPLER_NAMESPACE,
                label_selector=MANAGED_LABEL_SELECTOR,
            )
        except Exception as exc:
            logger.warning("Conntrack prober: could not list existing samplers: %s", exc)
            return {}
        by_node: Dict[str, str] = {}
        for pod in pods.items:
            phase = pod.status.phase if pod.status else None
            if phase in ("Failed", "Succeeded"):
                # A dead Never-restart pod can't sample; leave it for cleanup
                # and create a fresh one. Deleting first avoids a 409.
                try:
                    core_api.delete_namespaced_pod(pod.metadata.name, SAMPLER_NAMESPACE)
                except Exception as exc:
                    logger.warning(
                        "Conntrack prober: could not delete dead sampler %s: %s",
                        pod.metadata.name,
                        exc,
                    )
                continue
            labels = (pod.metadata.labels if pod.metadata else None) or {}
            node = labels.get(NODE_LABEL_KEY)
            if node:
                by_node[node] = pod.metadata.name
        return by_node

    def _wait_for_sampler_ready(self, core_api: Any, pod: str) -> Optional[str]:
        """Poll *pod* until ``conntrack --version`` answers; return the version.

        A successful version probe proves both that the pod is running and
        that the pinned ``conntrack-tools`` install completed — and it is
        exactly the recorded-version requirement from M1a finding I2.
        Returns ``None`` on timeout.
        """
        deadline = time.monotonic() + self._ready_timeout
        while True:
            out = self._exec(core_api, SAMPLER_NAMESPACE, pod, VERSION_COMMAND)
            version = parse_conntrack_version(out)
            if version is not None:
                return version
            if time.monotonic() >= deadline:
                return None
            time.sleep(self._ready_poll_interval)

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------

    def _probe_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._sample_once()
            except Exception as exc:  # defensive: the loop must never die
                logger.warning("Conntrack probe tick failed: %s", exc)
                with self._lock:
                    self._probe_errors += 1
            self._stop_event.wait(timeout=self.interval)

    def _sample_once(self) -> None:
        """Sample every node's conntrack table once and record the rows."""
        now = time.time()
        phase = self._current_phase(now)
        ts = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        for node, pod in sorted(self._samplers.items()):
            out = self._exec(self.core_api, SAMPLER_NAMESPACE, pod, SAMPLE_COMMAND)
            if out.startswith("ERROR:"):
                logger.warning(
                    "Conntrack probe exec failed on node %s (pod %s): %s",
                    node,
                    pod,
                    out[:200],
                )
                with self._lock:
                    self._probe_errors += 1
                continue
            rows = parse_conntrack_protocol_counts(out)
            with self._lock:
                for row in rows:
                    self._samples.append(
                        {
                            "ts": ts,
                            "node": node,
                            "proto": row["proto"],
                            "count": row["count"],
                            "phase": phase,
                        }
                    )

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def result(self) -> Dict[str, Any]:
        """Return collected samples + metadata.

        ``samples`` / ``meta`` land in ``summary.json`` as
        ``conntrackProtocolSamples`` / ``conntrackProtocolMeta`` (see
        ``MetricsCollector.collect``).  ``meta.available`` distinguishes
        "no conntrack churn" from "not collected" so an absent signal never
        reads as zero.
        """
        with self._lock:
            samples = list(self._samples)
            errors = self._probe_errors
        versions = sorted(set(self._tool_versions.values()))
        meta: Dict[str, Any] = {
            "available": bool(self._samplers),
            # Single resolved version across nodes, or None when nodes
            # disagree / nothing resolved (per-node detail is always in
            # toolVersionsByNode).
            "toolVersion": versions[0] if len(versions) == 1 else None,
            "toolVersionsByNode": dict(self._tool_versions),
            "intervalSeconds": self.interval,
            "samplerImage": SAMPLER_IMAGE,
            "packagePin": CONNTRACK_PACKAGE_PIN,
            "samplerNamespace": SAMPLER_NAMESPACE,
            "nodes": sorted(self._samplers),
        }
        if self._unavailable_reason is not None:
            meta["reason"] = self._unavailable_reason
        if errors > 0:
            meta["probeErrors"] = errors
        return {"samples": samples, "meta": meta}
