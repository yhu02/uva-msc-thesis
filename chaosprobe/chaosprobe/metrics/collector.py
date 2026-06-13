"""General-purpose metrics collector for chaos experiments.

Collects multiple categories of metrics during a chaos experiment window:
- Recovery timing (from RecoveryWatcher real-time data)
- Pod restart counts
- Resource pressure on the target node
- Event timeline (all pod lifecycle events)
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes import client
from kubernetes.client.rest import ApiException

from chaosprobe.k8s import ensure_k8s_config
from chaosprobe.metrics.endpointslices import summarize_endpoint_slices_json
from chaosprobe.metrics.utilization import compute_per_pod_utilization


class MetricsCollector:
    """Collects comprehensive metrics from a chaos experiment run.

    Orchestrates multiple metric sources and returns a unified result.
    The recovery data comes from a RecoveryWatcher that ran during the
    experiment (real-time), while pod status and node info are collected
    after the experiment ends.
    """

    def __init__(self, namespace: str):
        self.namespace = namespace

        ensure_k8s_config()

        self.core_api = client.CoreV1Api()
        self.discovery_api = client.DiscoveryV1Api()

    def collect(
        self,
        deployment_name: str,
        since_time: float,
        until_time: float,
        recovery_data: Optional[Dict[str, Any]] = None,
        latency_data: Optional[Dict[str, Any]] = None,
        redis_data: Optional[Dict[str, Any]] = None,
        disk_data: Optional[Dict[str, Any]] = None,
        resource_data: Optional[Dict[str, Any]] = None,
        prometheus_data: Optional[Dict[str, Any]] = None,
        conntrack_data: Optional[Dict[str, Any]] = None,
        endpoint_slices_pre: Optional[Dict[str, Any]] = None,
        endpoint_slices_during: Optional[Dict[str, Any]] = None,
        endpoint_slice_timeseries_data: Optional[Dict[str, Any]] = None,
        collect_logs: bool = False,
    ) -> Dict[str, Any]:
        """Collect all available metrics for a deployment during an experiment.

        Args:
            deployment_name: Target deployment name.
            since_time: Unix timestamp for experiment start.
            until_time: Unix timestamp for experiment end.
            recovery_data: Pre-collected recovery data from RecoveryWatcher.
                           If None, the recovery section will be empty.
            latency_data: Pre-collected inter-service latency data from
                          ContinuousLatencyProber. If None, the latency
                          section will be omitted.
            redis_data: Pre-collected Redis throughput data from
                       ContinuousRedisProber. If None, omitted.
            disk_data: Pre-collected disk I/O throughput data from
                       ContinuousDiskProber. If None, omitted.
            resource_data: Pre-collected node/pod resource utilization
                           from ContinuousResourceProber. If None, omitted.
            prometheus_data: Pre-collected Prometheus metrics from
                             ContinuousPrometheusProber. If None, omitted.
            conntrack_data: Pre-collected per-node protocol-labeled conntrack
                            samples from ConntrackProtocolProber
                            (``{"samples": [...], "meta": {...}}``). Surfaced
                            as ``conntrackProtocolSamples`` +
                            ``conntrackProtocolMeta``. If None, both keys are
                            omitted so "not collected" never reads as zero.
            endpoint_slices_pre: Pre-chaos EndpointSlice snapshot captured by
                                 the caller before the kill cycle. Paired with
                                 a fresh post-chaos snapshot under
                                 ``endpointSlices`` so the net endpoint change
                                 around the fault window is visible. If None,
                                 only the post-chaos snapshot is recorded.
            endpoint_slices_during: Mid-chaos EndpointSlice snapshot captured by
                                 the caller while the fault is active (e.g. at the
                                 drain midpoint). Recorded under ``endpointSlices.
                                 duringChaos``. Unlike the post-chaos snapshot, it
                                 catches the transient outage trough — services
                                 whose endpoints drop to zero during a node drain
                                 but reschedule back before post-chaos. If None,
                                 the key is omitted.
            endpoint_slice_timeseries_data: Pre-collected full EndpointSlice time
                                 series from EndpointSliceTimeSeriesProber
                                 (``{"samples": [...], "meta": {...}}``). Surfaced
                                 as the additive top-level key
                                 ``endpointSliceTimeSeries`` (parallel to
                                 ``conntrackProtocolSamples``). Unlike the
                                 ``endpointSlices`` pre/during/post snapshots,
                                 this retains every 15s sample across the whole
                                 window so the trough's *duration* — not just its
                                 depth — is recoverable. If None, the key is
                                 omitted so "not collected" never reads as zero.
            collect_logs: If True, collect container logs from target pods.

        Returns:
            Unified metrics dictionary with all collected data.
        """
        pod_status = self._collect_pod_status(deployment_name)

        # Collect every distinct node hosting a pod of the target
        # deployment, preserving the order they first appear in
        # pod_status.  A `colocate` run will have a single node; a
        # `spread` run will list multiple.  Without this, leakage /
        # cross-node variance analysis cannot tell whether one node was
        # under pressure while its neighbour was idle.
        hosting_nodes: List[str] = []
        seen: set = set()
        for pod in pod_status.get("pods", []):
            n = pod.get("node")
            if n and n not in seen:
                seen.add(n)
                hosting_nodes.append(n)

        # Keep `nodeInfo` as the first hosting node for backwards compat
        # (existing tooling indexes it as a single dict).
        node_info = self._collect_node_info(hosting_nodes[0] if hosting_nodes else None)
        node_info_all = self._collect_all_node_info(hosting_nodes)

        # Use watcher data if provided, otherwise empty
        if recovery_data is None:
            recovery_data = {
                "deploymentName": deployment_name,
                "recoveryEvents": [],
                "summary": {
                    "count": 0,
                    "completedCycles": 0,
                    "meanRecovery_ms": None,
                    "medianRecovery_ms": None,
                    "minRecovery_ms": None,
                    "maxRecovery_ms": None,
                    "p95Recovery_ms": None,
                },
            }

        # Extract raw events from watcher for the timeline (non-destructive)
        event_timeline = recovery_data.get("rawEvents", [])
        # Exclude rawEvents from the recovery section to avoid duplication
        recovery_section = {k: v for k, v in recovery_data.items() if k != "rawEvents"}

        result = {
            "deploymentName": deployment_name,
            "timeWindow": {
                "start": datetime.fromtimestamp(since_time, tz=timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(until_time, tz=timezone.utc).isoformat(),
                "duration_s": round(until_time - since_time, 1),
            },
            "recovery": recovery_section,
            "podStatus": pod_status,
            "eventTimeline": event_timeline,
            "nodeInfo": node_info,
        }

        if node_info_all:
            result["nodeInfoAll"] = node_info_all

        if latency_data is not None:
            result["latency"] = latency_data

        if redis_data is not None:
            result["redis"] = redis_data

        if disk_data is not None:
            result["disk"] = disk_data

        if resource_data is not None:
            result["resources"] = resource_data

        if prometheus_data is not None:
            result["prometheus"] = prometheus_data
            utilization = compute_per_pod_utilization(pod_status, prometheus_data)
            if utilization.get("pods"):
                result["utilization"] = utilization

        if conntrack_data is not None:
            # Flat sample list ({ts, node, proto, count, phase}) — the chaos
            # windows are recorded separately (anomalyLabels), so analysis
            # aligns samples with windows by timestamp.
            result["conntrackProtocolSamples"] = conntrack_data.get("samples") or []
            result["conntrackProtocolMeta"] = conntrack_data.get("meta") or {}

        if endpoint_slice_timeseries_data is not None:
            # Additive full time series ({ts, phase, services}), parallel to
            # conntrackProtocolSamples — distinct from the pre/during/post
            # ``endpointSlices`` snapshots, which are left untouched so
            # blast_radius.py and the frozen A/A data keep their shape.
            result["endpointSliceTimeSeries"] = {
                "samples": endpoint_slice_timeseries_data.get("samples") or [],
                "meta": endpoint_slice_timeseries_data.get("meta") or {},
            }

        endpoint_slices_post = self.snapshot_endpoint_slices()
        if (
            endpoint_slices_pre is not None
            or endpoint_slices_during is not None
            or endpoint_slices_post is not None
        ):
            result["endpointSlices"] = {
                "preChaos": endpoint_slices_pre,
                "postChaos": endpoint_slices_post,
            }
            # Only emit duringChaos when it was actually captured, so existing
            # consumers that key on pre/post are unaffected by its absence.
            if endpoint_slices_during is not None:
                result["endpointSlices"]["duringChaos"] = endpoint_slices_during

        if collect_logs:
            duration = until_time - since_time
            result["containerLogs"] = self._collect_container_logs(
                deployment_name,
                duration,
            )

        return result

    def snapshot_endpoint_slices(self) -> Optional[Dict[str, Any]]:
        """Snapshot per-service EndpointSlice endpoint counts for the namespace.

        Returns ``{"capturedAt": ISO8601, "services": {name: {ready,
        terminating, notReady, total}}}`` or ``None`` if the discovery API
        call fails (e.g. RBAC gap, older cluster). Called once before chaos
        (by the caller, passed back as ``endpoint_slices_pre``) and once
        after — the pair captures the net endpoint change across the kill
        cycle that underlies the churn/reconvergence story.
        """
        # Read raw JSON (``_preload_content=False``): the typed V1EndpointSlice
        # model rejects ``endpoints: null``, which the API returns for an empty
        # slice (all of a service's pods evicted at once, e.g. mid node-drain),
        # raising ValueError and crashing the snapshot. json.JSONDecodeError is a
        # subclass of ValueError, so one except covers both.
        try:
            resp = self.discovery_api.list_namespaced_endpoint_slice(
                self.namespace, _preload_content=False
            )
            raw = json.loads(resp.data)
        except (ApiException, ValueError):
            return None
        summary = summarize_endpoint_slices_json(raw.get("items") or [])
        summary["capturedAt"] = datetime.now(timezone.utc).isoformat()
        return summary

    def _collect_pod_status(self, deployment_name: str) -> Dict[str, Any]:
        """Collect current pod status and restart counts.

        Includes container-level granularity: per-container restart reasons,
        resource requests/limits, and last termination state.
        """
        try:
            pods = self.core_api.list_namespaced_pod(
                self.namespace,
                label_selector=f"app={deployment_name}",
            )
        except ApiException:
            return {"error": "Failed to query pods"}

        pod_list = []
        total_restarts = 0
        total_oom_kills = 0

        for pod in pods.items:
            restarts = 0
            container_statuses = pod.status.container_statuses or []

            # Container-level detail
            containers = []
            for cs in container_statuses:
                restarts += cs.restart_count
                oom_kill_count = 0

                container_info: Dict[str, Any] = {
                    "name": cs.name,
                    "ready": cs.ready,
                    "restartCount": cs.restart_count,
                    "started": cs.started if hasattr(cs, "started") else None,
                }

                # Current state
                if cs.state:
                    if cs.state.running:
                        container_info["state"] = "running"
                        container_info["startedAt"] = (
                            cs.state.running.started_at.isoformat()
                            if cs.state.running.started_at
                            else None
                        )
                    elif cs.state.waiting:
                        container_info["state"] = "waiting"
                        container_info["waitingReason"] = cs.state.waiting.reason
                        container_info["waitingMessage"] = cs.state.waiting.message
                    elif cs.state.terminated:
                        container_info["state"] = "terminated"
                        container_info["terminatedReason"] = cs.state.terminated.reason
                        container_info["exitCode"] = cs.state.terminated.exit_code
                        if cs.state.terminated.reason == "OOMKilled":
                            oom_kill_count += 1

                # Last termination state (critical for OOMKills, CrashLoopBackOff)
                if cs.last_state and cs.last_state.terminated:
                    term = cs.last_state.terminated
                    container_info["lastTermination"] = {
                        "reason": term.reason,
                        "exitCode": term.exit_code,
                        "startedAt": term.started_at.isoformat() if term.started_at else None,
                        "finishedAt": term.finished_at.isoformat() if term.finished_at else None,
                        "message": term.message,
                    }
                    if term.reason == "OOMKilled":
                        oom_kill_count += 1

                container_info["oomKillCount"] = oom_kill_count
                total_oom_kills += oom_kill_count

                containers.append(container_info)

            # Extract resource requests/limits from pod spec
            resource_specs = []
            for container_spec in pod.spec.containers or []:
                spec_info: Dict[str, Any] = {"name": container_spec.name}
                res = container_spec.resources
                if res:
                    if res.requests:
                        spec_info["requests"] = {k: str(v) for k, v in res.requests.items()}
                    if res.limits:
                        spec_info["limits"] = {k: str(v) for k, v in res.limits.items()}
                resource_specs.append(spec_info)

            total_restarts += restarts

            conditions = {}
            for cond in pod.status.conditions or []:
                conditions[cond.type] = {
                    "status": cond.status,
                    "lastTransition": (
                        cond.last_transition_time.isoformat() if cond.last_transition_time else None
                    ),
                }

            pod_list.append(
                {
                    "name": pod.metadata.name,
                    "phase": pod.status.phase,
                    "node": pod.spec.node_name,
                    "restartCount": restarts,
                    "conditions": conditions,
                    "containers": containers,
                    "resourceSpecs": resource_specs,
                }
            )

        return {
            "pods": pod_list,
            "totalRestarts": total_restarts,
            "totalOOMKills": total_oom_kills,
        }

    def _collect_node_info(self, node_name: Optional[str]) -> Optional[Dict[str, Any]]:
        """Collect resource info and pressure conditions for a node.

        Args:
            node_name: Name of the node to query, or None to skip.

        Returns:
            ``None`` if ``node_name`` is falsy or the K8s API call fails.
            Otherwise a dict with ``allocatable``, ``capacity``, and
            ``conditions`` keys.  ``conditions`` may be empty (``{}``) if
            the node returned no condition data — surfaced explicitly so
            callers can distinguish "no pressure" from "missing data".
        """
        if not node_name:
            return None

        try:
            node = self.core_api.read_node(node_name)
        except ApiException:
            return None

        # Freshly-registered nodes can briefly have ``status=None`` until
        # the kubelet posts its first status update.  Defend against that
        # so a momentary registration race doesn't crash the collector.
        status = getattr(node, "status", None)
        alloc = (getattr(status, "allocatable", None) if status is not None else None) or {}
        capacity = (getattr(status, "capacity", None) if status is not None else None) or {}
        conditions = self._extract_node_conditions(node)

        return {
            "nodeName": node_name,
            "allocatable": {
                "cpu": alloc.get("cpu", "0"),
                "memory": alloc.get("memory", "0"),
            },
            "capacity": {
                "cpu": capacity.get("cpu", "0"),
                "memory": capacity.get("memory", "0"),
            },
            "conditions": conditions,
        }

    def _collect_all_node_info(self, node_names: List[str]) -> Dict[str, Dict[str, Any]]:
        """Collect per-node info for every distinct hosting node.

        Skips nodes whose K8s API call fails — partial coverage is better
        than no data, and the caller can detect missing nodes by
        comparing the returned keys against the input list.
        """
        out: Dict[str, Dict[str, Any]] = {}
        for name in node_names:
            info = self._collect_node_info(name)
            if info is not None:
                out[name] = info
        return out

    @staticmethod
    def _extract_node_conditions(node: Any) -> Dict[str, Dict[str, Any]]:
        """Build a `{condition_type: {status, reason, message, lastTransition}}`
        map from a K8s node object.

        Always emits every condition type the node reports — both the
        standard kubelet pressure flags and any custom ones (e.g. from
        node-problem-detector).  Returns an empty dict if the node
        reports no conditions or the status field is missing entirely.
        """
        status = getattr(node, "status", None)
        if status is None:
            return {}
        raw = getattr(status, "conditions", None) or []
        out: Dict[str, Dict[str, Any]] = {}
        for cond in raw:
            cond_type = getattr(cond, "type", None)
            if not cond_type:
                continue
            entry: Dict[str, Any] = {
                "status": getattr(cond, "status", None),
            }
            reason = getattr(cond, "reason", None)
            message = getattr(cond, "message", None)
            transition = getattr(cond, "last_transition_time", None)
            if reason:
                entry["reason"] = reason
            if message:
                entry["message"] = message
            if transition is not None:
                # Datetime objects expose .isoformat(); strings pass through.
                entry["lastTransition"] = (
                    transition.isoformat() if hasattr(transition, "isoformat") else str(transition)
                )
            out[cond_type] = entry
        return out

    def _collect_container_logs(
        self,
        deployment_name: str,
        duration_seconds: float,
        tail_lines: int = 500,
    ) -> Dict[str, Any]:
        """Collect container logs from the target deployment's pods.

        Captures both current and previous (crashed/restarted) container logs,
        which is important for diagnosing OOMKills and crash loops.

        Args:
            deployment_name: Target deployment name.
            duration_seconds: Experiment duration in seconds.
            tail_lines: Maximum number of log lines per container.

        Returns:
            Dict with logs per pod and collection config.
        """
        since_seconds = int(duration_seconds) + 30

        try:
            pods = self.core_api.list_namespaced_pod(
                self.namespace,
                label_selector=f"app={deployment_name}",
            )
        except ApiException as e:
            return {"error": f"Failed to list pods: {e.reason}"}

        logs_by_pod: Dict[str, Any] = {}

        for pod in pods.items:
            pod_name = pod.metadata.name
            pod_logs: Dict[str, Any] = {"containers": {}}

            for container_status in pod.status.container_statuses or []:
                container_name = container_status.name
                container_logs: Dict[str, Optional[str]] = {}

                # Current container logs
                try:
                    current = self.core_api.read_namespaced_pod_log(
                        name=pod_name,
                        namespace=self.namespace,
                        container=container_name,
                        since_seconds=since_seconds,
                        tail_lines=tail_lines,
                    )
                    container_logs["current"] = current
                except ApiException:
                    container_logs["current"] = None

                # Previous container logs (crashed/restarted — OOMKill evidence)
                try:
                    previous = self.core_api.read_namespaced_pod_log(
                        name=pod_name,
                        namespace=self.namespace,
                        container=container_name,
                        since_seconds=since_seconds,
                        tail_lines=tail_lines,
                        previous=True,
                    )
                    container_logs["previous"] = previous
                except ApiException:
                    container_logs["previous"] = None

                pod_logs["containers"][container_name] = container_logs

            pod_logs["restartCount"] = sum(
                cs.restart_count for cs in (pod.status.container_statuses or [])
            )
            logs_by_pod[pod_name] = pod_logs

        return {
            "pods": logs_by_pod,
            "config": {
                "sinceSeconds": since_seconds,
                "tailLines": tail_lines,
            },
        }
