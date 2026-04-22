"""Neo4j write/sync operations for ChaosProbe.

Mixin class containing all methods that write data to Neo4j:
topology sync, run sync, and all ``_sync_*`` helpers.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Neo4jWriterMixin:
    """Methods that write/sync data into the Neo4j graph."""

    # ------------------------------------------------------------------
    # Topology sync
    # ------------------------------------------------------------------

    def sync_topology(
        self,
        nodes: List[Dict[str, Any]],
        deployments: List[Dict[str, Any]],
    ) -> None:
        """Merge Kubernetes nodes and deployments into the graph.

        Parameters
        ----------
        nodes:
            List of dicts with keys ``name``, ``cpu`` (millicores),
            ``memory`` (bytes), ``control_plane`` (bool).
        deployments:
            List of dicts with keys ``name``, ``namespace``, ``replicas``.
        """
        with self._driver.session() as session:
            for node in nodes:
                session.run(
                    "MERGE (n:K8sNode {name: $name}) "
                    "SET n.cpu = $cpu, n.memory = $memory, "
                    "    n.control_plane = $cp",
                    name=node["name"],
                    cpu=node.get("cpu", 0),
                    memory=node.get("memory", 0),
                    cp=node.get("control_plane", False),
                )

            for dep in deployments:
                session.run(
                    "MERGE (d:Deployment {name: $name}) "
                    "SET d.namespace = $ns, d.replicas = $replicas",
                    name=dep["name"],
                    ns=dep.get("namespace", ""),
                    replicas=dep.get("replicas", 1),
                )

    def sync_service_dependencies(self, routes=None) -> None:
        """Populate Service nodes and DEPENDS_ON edges from the service
        dependency graph.

        Parameters
        ----------
        routes
            List of ``(source, target, host, protocol, description)`` tuples,
            discovered via ``config.topology``.  Skips silently when *None*.
        """
        if not routes:
            return

        with self._driver.session() as session:
            for src, tgt, host, protocol, description in routes:
                port = ""
                if ":" in host:
                    port = host.rsplit(":", 1)[1]
                session.run(
                    "MERGE (s:Service {name: $src}) "
                    "MERGE (t:Service {name: $tgt}) "
                    "MERGE (s)-[r:DEPENDS_ON]->(t) "
                    "SET r.protocol = $protocol, r.port = $port, "
                    "    r.description = $desc",
                    src=src,
                    tgt=tgt,
                    protocol=protocol,
                    port=port,
                    desc=description,
                )

            # Link deployments to their services (same name convention)
            session.run(
                "MATCH (d:Deployment), (s:Service) "
                "WHERE d.name = s.name "
                "MERGE (d)-[:EXPOSES]->(s)"
            )

    # ------------------------------------------------------------------
    # Run sync
    # ------------------------------------------------------------------

    def sync_run(self, run_data: Dict[str, Any]) -> None:
        """Import a single experiment run with all metrics into the graph.

        Creates/updates:
        - ``ChaosRun`` node with full summary and recovery stats
        - ``PlacementStrategy`` node + ``USED_STRATEGY`` edge
        - ``TARGETED_BY`` edges from target deployments
        - ``SCHEDULED_ON`` edges from placement assignments
        - ``RecoveryCycle`` nodes for each recovery event
        - ``ExperimentResult`` nodes for each chaos experiment result
        - ``MetricsPhase`` nodes for per-phase metric summaries
        - ``PodSnapshot`` nodes for pod status at collection time
        - ``CascadeEvent`` nodes for cascade timeline entries
        - Load generation stats on the experiment node
        """
        run_id = run_data.get("runId", "unknown")
        timestamp = run_data.get("timestamp", "")
        session_id = run_data.get("sessionId", "")
        summary = run_data.get("summary", {})
        verdict = summary.get("overallVerdict", "UNKNOWN")
        score = summary.get("resilienceScore", 0)

        # Infer strategy from scenario or explicit placement data
        strategy_name = "default"
        seed: Optional[int] = None
        placement = run_data.get("placement") or {}
        if placement:
            strategy_name = placement.get("strategy", strategy_name)
            seed = placement.get("seed")
        else:
            scenario = run_data.get("scenario", {})
            meta = scenario.get("metadata", {})
            strategy_name = meta.get("strategy", strategy_name)

        # Recovery metrics
        metrics = run_data.get("metrics", {})
        recovery = metrics.get("recovery", {})
        recovery_summary = recovery.get("summary", {})

        # Time window
        time_window = metrics.get("timeWindow", {})

        # Pod status
        pod_status = metrics.get("podStatus", {})

        # Node info
        node_info = metrics.get("nodeInfo") or {}

        # Load generation
        load_gen = run_data.get("loadGeneration", {})

        with self._driver.session() as session:
            with session.begin_transaction() as tx:
                # ── Experiment node (enriched) ──────────────────────
                tx.run(
                    "MERGE (e:ChaosRun {run_id: $rid}) "
                    "SET e.name = $strat + ' (' + $verdict + ')', "
                    "    e.timestamp = $ts, e.verdict = $verdict, "
                    "    e.resilience_score = $score, "
                    "    e.strategy = $strat, "
                    "    e.session_id = $session_id, "
                    "    e.total_experiments = $total_exp, "
                    "    e.passed_experiments = $passed, "
                    "    e.failed_experiments = $failed, "
                    "    e.mean_recovery_ms = $mean_rec, "
                    "    e.median_recovery_ms = $median_rec, "
                    "    e.min_recovery_ms = $min_rec, "
                    "    e.max_recovery_ms = $max_rec, "
                    "    e.p95_recovery_ms = $p95_rec, "
                    "    e.recovery_count = $rec_count, "
                    "    e.completed_cycles = $completed, "
                    "    e.incomplete_cycles = $incomplete, "
                    "    e.time_window_start = $tw_start, "
                    "    e.time_window_end = $tw_end, "
                    "    e.duration_s = $duration, "
                    "    e.total_restarts = $restarts, "
                    "    e.load_profile = $load_profile, "
                    "    e.load_total_requests = $load_reqs, "
                    "    e.load_total_failures = $load_fails, "
                    "    e.load_avg_response_ms = $load_avg_resp, "
                    "    e.load_p95_response_ms = $load_p95, "
                    "    e.load_p50_response_ms = $load_p50, "
                    "    e.load_p99_response_ms = $load_p99, "
                    "    e.load_rps = $load_rps, "
                    "    e.load_error_rate = $load_err_rate, "
                    "    e.load_duration_s = $load_duration, "
                    "    e.node_name = $node_name, "
                    "    e.node_capacity_cpu = $node_cap_cpu, "
                    "    e.node_capacity_memory = $node_cap_mem, "
                    "    e.node_allocatable_cpu = $node_alloc_cpu, "
                    "    e.node_allocatable_memory = $node_alloc_mem, "
                    "    e.event_timeline = $timeline",
                    rid=run_id,
                    ts=timestamp,
                    verdict=verdict,
                    score=score,
                    strat=strategy_name,
                    session_id=session_id,
                    total_exp=summary.get("totalExperiments", 0),
                    passed=summary.get("passed", 0),
                    failed=summary.get("failed", 0),
                    mean_rec=recovery_summary.get("meanRecovery_ms"),
                    median_rec=recovery_summary.get("medianRecovery_ms"),
                    min_rec=recovery_summary.get("minRecovery_ms"),
                    max_rec=recovery_summary.get("maxRecovery_ms"),
                    p95_rec=recovery_summary.get("p95Recovery_ms"),
                    rec_count=recovery_summary.get("count", 0),
                    completed=recovery_summary.get("completedCycles", 0),
                    incomplete=recovery_summary.get("incompleteCycles", 0),
                    tw_start=time_window.get("start"),
                    tw_end=time_window.get("end"),
                    duration=time_window.get("duration_s"),
                    restarts=pod_status.get("totalRestarts", 0),
                    load_profile=load_gen.get("profile"),
                    load_reqs=load_gen.get("stats", {}).get("totalRequests"),
                    load_fails=load_gen.get("stats", {}).get("totalFailures"),
                    load_avg_resp=load_gen.get("stats", {}).get("avgResponseTime_ms"),
                    load_p50=load_gen.get("stats", {}).get("p50ResponseTime_ms"),
                    load_p95=load_gen.get("stats", {}).get("p95ResponseTime_ms"),
                    load_p99=load_gen.get("stats", {}).get("p99ResponseTime_ms"),
                    load_rps=load_gen.get("stats", {}).get("requestsPerSecond"),
                    load_err_rate=load_gen.get("stats", {}).get("errorRate"),
                    load_duration=load_gen.get("stats", {}).get("duration_seconds"),
                    node_name=node_info.get("nodeName"),
                    node_cap_cpu=node_info.get("capacity", {}).get("cpu"),
                    node_cap_mem=node_info.get("capacity", {}).get("memory"),
                    node_alloc_cpu=node_info.get("allocatable", {}).get("cpu"),
                    node_alloc_mem=node_info.get("allocatable", {}).get("memory"),
                    timeline=json.dumps(metrics.get("eventTimeline", [])),
                )

                # ── Strategy node + link ──────────────────────────
                params: Dict[str, Any] = {"strat": strategy_name, "rid": run_id}
                if seed is not None:
                    params["seed"] = seed
                tx.run(
                    "MERGE (s:PlacementStrategy {name: $strat}) "
                    + ("SET s.seed = $seed " if seed is not None else "")
                    + "WITH s "
                    "MATCH (e:ChaosRun {run_id: $rid}) "
                    "MERGE (e)-[:USED_STRATEGY]->(s)",
                    **params,
                )

                # ── TARGETED_BY edges ─────────────────────────────
                target_deps: set = set()
                scenario = run_data.get("scenario", {})
                for exp in scenario.get("experiments", []):
                    content = exp.get("content", {})
                    spec = content.get("spec", {})
                    appinfo = spec.get("appinfo", {})
                    applabel = appinfo.get("applabel", "")
                    if applabel.startswith("app="):
                        target_deps.add(applabel.split("=", 1)[1])
                    elif applabel:
                        logger.warning(
                            "Unexpected applabel format %r in run %s — "
                            "expected 'app=<name>', skipping TARGETED_BY edge",
                            applabel,
                            run_id,
                        )

                dep_name = metrics.get("deploymentName", "")
                if dep_name:
                    target_deps.add(dep_name)

                for dep in target_deps:
                    tx.run(
                        "MATCH (d:Deployment {name: $dep}), "
                        "      (e:ChaosRun {run_id: $rid}) "
                        "MERGE (d)-[:TARGETED_BY]->(e)",
                        dep=dep,
                        rid=run_id,
                    )

                # ── SCHEDULED_ON edges ────────────────────────────
                assignments = placement.get("assignments", {})
                for dep_name, node_name in assignments.items():
                    tx.run(
                        "MATCH (d:Deployment {name: $dep}), (n:K8sNode {name: $node}) "
                        "MERGE (d)-[r:SCHEDULED_ON {run_id: $rid}]->(n) "
                        "SET r.strategy = $strat",
                        dep=dep_name,
                        node=node_name,
                        rid=run_id,
                        strat=strategy_name,
                    )

                # ── Recovery cycles ───────────────────────────────
                self._sync_recovery_cycles(tx, run_id, recovery)

                # ── Experiment results ────────────────────────────
                self._sync_experiment_results(tx, run_id, run_data.get("experiments", []))

                # ── Metrics phase summaries ───────────────────────
                self._sync_metrics_phases(tx, run_id, metrics)

                # ── Pod snapshots ─────────────────────────────────
                self._sync_pod_snapshots(tx, run_id, pod_status)

                # ── Raw time-series samples ───────────────────────
                self._sync_time_series(tx, run_id, metrics, strategy_name)

                # ── Anomaly labels ────────────────────────────────
                self._sync_anomaly_labels(
                    tx,
                    run_id,
                    run_data.get("anomalyLabels", []),
                )

                # ── Cascade timeline ──────────────────────────────
                self._sync_cascade_timeline(
                    tx,
                    run_id,
                    run_data.get("cascadeTimeline", []),
                )

                # ── Container logs ────────────────────────────────
                self._sync_container_logs(
                    tx,
                    run_id,
                    metrics.get("containerLogs", {}),
                )

                # ── Scenario metadata ─────────────────────────────
                scenario = run_data.get("scenario", {})
                tx.run(
                    "MATCH (e:ChaosRun {run_id: $rid}) " "SET e.scenario_json = $scenario",
                    rid=run_id,
                    scenario=json.dumps(scenario),
                )

                tx.commit()

    # ------------------------------------------------------------------
    # sync_run helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clear_children(
        tx: Any,
        run_id: str,
        relationship: str,
        label: str,
    ) -> None:
        """Delete all child nodes of a ChaosRun linked by *relationship*."""
        tx.run(
            f"MATCH (e:ChaosRun {{run_id: $rid}})-[:{relationship}]->"
            f"(c:{label}) DETACH DELETE c",
            rid=run_id,
        )

    def _sync_recovery_cycles(
        self,
        tx: Any,
        run_id: str,
        recovery: Dict[str, Any],
    ) -> None:
        """Store individual recovery cycle events."""
        self._clear_children(tx, run_id, "HAS_RECOVERY_CYCLE", "RecoveryCycle")
        for idx, cycle in enumerate(recovery.get("recoveryEvents", [])):
            tx.run(
                "MATCH (e:ChaosRun {run_id: $rid}) "
                "CREATE (c:RecoveryCycle {"
                "  name: 'cycle #' + toString($seq) + ' (' + toString($total) + 'ms)', "
                "  run_id: $rid, seq: $seq, "
                "  deletion_time: $del_t, scheduled_time: $sched_t, ready_time: $ready_t, "
                "  deletion_to_scheduled_ms: $d2s, scheduled_to_ready_ms: $s2r, "
                "  total_recovery_ms: $total, failure_reason: $fail_reason"
                "}) "
                "CREATE (e)-[:HAS_RECOVERY_CYCLE]->(c)",
                rid=run_id,
                seq=idx,
                del_t=cycle.get("deletionTime"),
                sched_t=cycle.get("scheduledTime"),
                ready_t=cycle.get("readyTime"),
                d2s=cycle.get("deletionToScheduled_ms"),
                s2r=cycle.get("scheduledToReady_ms"),
                total=cycle.get("totalRecovery_ms"),
                fail_reason=cycle.get("failure_reason"),
            )

    def _sync_experiment_results(
        self,
        tx: Any,
        run_id: str,
        experiments: List[Dict[str, Any]],
    ) -> None:
        """Store per-chaos-experiment result nodes with probe results."""
        self._clear_children(tx, run_id, "HAS_RESULT", "ExperimentResult")
        # Clear orphaned ProbeResult nodes that were linked to old ExperimentResults
        tx.run(
            "MATCH (p:ProbeResult {run_id: $rid}) DETACH DELETE p",
            rid=run_id,
        )
        for exp in experiments:
            result = exp.get("result", exp.get("chaosResult", {}))
            probes = exp.get("probes", [])
            exp_name = exp.get("name", "")
            engine_name = exp.get("engineName", "")
            tx.run(
                "MATCH (e:ChaosRun {run_id: $rid}) "
                "CREATE (r:ExperimentResult {"
                "  run_id: $rid, name: $name + ' (' + $verdict + ')', "
                "  engine_name: $engine, "
                "  experiment_name: $name, verdict: $verdict, "
                "  probe_success_pct: $probe_pct, "
                "  phase: $phase, fail_step: $fail_step"
                "}) "
                "CREATE (e)-[:HAS_RESULT]->(r)",
                rid=run_id,
                name=exp_name,
                engine=engine_name,
                verdict=result.get("verdict", "Unknown"),
                probe_pct=exp.get(
                    "probeSuccessPercentage", result.get("probeSuccessPercentage", 0)
                ),
                phase=result.get("phase", ""),
                fail_step=result.get("failStep", ""),
            )
            # Create individual ProbeResult nodes linked to ExperimentResult
            for probe in probes:
                p_name = probe.get("name", "")
                p_status = probe.get("status", {})
                # Handle both flat and nested status formats
                if isinstance(p_status, dict):
                    p_verdict = p_status.get("verdict", "")
                    p_description = p_status.get("description", "")
                elif isinstance(p_status, str):
                    p_verdict = p_status
                    p_description = ""
                else:
                    p_verdict = str(p_status)
                    p_description = ""
                tx.run(
                    "MATCH (r:ExperimentResult {"
                    "  run_id: $rid, experiment_name: $exp_name, engine_name: $engine"
                    "}) "
                    "CREATE (p:ProbeResult {"
                    "  run_id: $rid, "
                    "  name: $name + ' (' + $verdict + ')', "
                    "  probe_name: $name, "
                    "  type: $type, "
                    "  mode: $mode, "
                    "  verdict: $verdict, "
                    "  description: $desc"
                    "}) "
                    "CREATE (r)-[:HAS_PROBE]->(p)",
                    rid=run_id,
                    exp_name=exp_name,
                    engine=engine_name,
                    name=p_name,
                    type=probe.get("type", ""),
                    mode=probe.get("mode", ""),
                    verdict=p_verdict,
                    desc=p_description,
                )

    def _sync_metrics_phases(
        self,
        tx: Any,
        run_id: str,
        metrics: Dict[str, Any],
    ) -> None:
        """Store per-phase metric summaries for all metric types."""
        self._clear_children(tx, run_id, "HAS_METRICS_PHASE", "MetricsPhase")

        phase_names = ["pre-chaos", "during-chaos", "post-chaos"]

        # ── JSON-blob metric types (latency, prometheus, redis, disk) ─
        # Each stores phases with a single JSON property alongside sample_count.
        _json_metrics = [
            # (metrics_key, metric_type, json_prop_name, json_data_key, require_available)
            ("latency", "latency", "routes", "routes", False),
            ("prometheus", "prometheus", "metrics_json", "metrics", True),
            ("redis", "redis", "operations", "redis", False),
            ("disk", "disk", "operations", "disk", False),
        ]
        for mkey, mtype, prop_name, data_key, require_avail in _json_metrics:
            section = metrics.get(mkey, {})
            if require_avail and not section.get("available", False):
                continue
            for phase in phase_names:
                phase_data = section.get("phases", {}).get(phase, {})
                if not phase_data or phase_data.get("sampleCount", 0) == 0:
                    continue
                tx.run(
                    "MATCH (e:ChaosRun {run_id: $rid}) "
                    "CREATE (m:MetricsPhase {"
                    f"  name: '{mtype}: ' + $phase, "
                    f"  run_id: $rid, metric_type: '{mtype}', phase: $phase, "
                    f"  sample_count: $samples, "
                    f"  {prop_name}: $json_data"
                    "}) "
                    "CREATE (e)-[:HAS_METRICS_PHASE]->(m)",
                    rid=run_id,
                    phase=phase,
                    samples=phase_data.get("sampleCount", 0),
                    json_data=json.dumps(phase_data.get(data_key, {})),
                )

        # ── Resources (structured, not JSON blob) ─────────────
        resources = metrics.get("resources", {})
        if resources.get("available", False):
            resource_phases = resources.get("phases", {})
            for phase in phase_names:
                phase_data = resource_phases.get(phase, {})
                if not phase_data or phase_data.get("sampleCount", 0) == 0:
                    continue
                node_data = phase_data.get("node", {})
                tx.run(
                    "MATCH (e:ChaosRun {run_id: $rid}) "
                    "CREATE (m:MetricsPhase {"
                    "  name: 'resources: ' + $phase, "
                    "  run_id: $rid, metric_type: 'resources', phase: $phase, "
                    "  sample_count: $samples, "
                    "  node_name: $node_name, "
                    "  mean_cpu_millicores: $mean_cpu, max_cpu_millicores: $max_cpu, "
                    "  mean_memory_bytes: $mean_mem, max_memory_bytes: $max_mem, "
                    "  mean_cpu_percent: $mean_cpu_pct, max_cpu_percent: $max_cpu_pct, "
                    "  mean_memory_percent: $mean_mem_pct, max_memory_percent: $max_mem_pct"
                    "}) "
                    "CREATE (e)-[:HAS_METRICS_PHASE]->(m)",
                    rid=run_id,
                    phase=phase,
                    samples=phase_data.get("sampleCount", 0),
                    node_name=",".join(resources.get("nodeNames", [])) or resources.get("nodeName"),
                    mean_cpu=node_data.get("meanCpu_millicores"),
                    max_cpu=node_data.get("maxCpu_millicores"),
                    mean_mem=node_data.get("meanMemory_bytes"),
                    max_mem=node_data.get("maxMemory_bytes"),
                    mean_cpu_pct=node_data.get("meanCpu_percent"),
                    max_cpu_pct=node_data.get("maxCpu_percent"),
                    mean_mem_pct=node_data.get("meanMemory_percent"),
                    max_mem_pct=node_data.get("maxMemory_percent"),
                )

    def _sync_pod_snapshots(
        self,
        tx: Any,
        run_id: str,
        pod_status: Dict[str, Any],
    ) -> None:
        """Store pod status snapshots with links to Deployment and K8sNode."""
        self._clear_children(tx, run_id, "HAS_POD_SNAPSHOT", "PodSnapshot")
        for pod in pod_status.get("pods", []):
            pod_name = pod.get("name", "")
            tx.run(
                "MATCH (e:ChaosRun {run_id: $rid}) "
                "CREATE (p:PodSnapshot {"
                "  run_id: $rid, name: $name, "
                "  phase: $phase, "
                "  node: $node, restart_count: $restarts, "
                "  conditions: $conditions"
                "}) "
                "CREATE (e)-[:HAS_POD_SNAPSHOT]->(p)",
                rid=run_id,
                name=pod_name,
                phase=pod.get("phase", ""),
                node=pod.get("node"),
                restarts=pod.get("restartCount", 0),
                conditions=json.dumps(pod.get("conditions", {})),
            )
            # Link pod to its K8sNode for graph traversal
            node_name = pod.get("node")
            if node_name:
                tx.run(
                    "MATCH (p:PodSnapshot {run_id: $rid, name: $pname}), "
                    "      (n:K8sNode {name: $node}) "
                    "CREATE (p)-[:RUNNING_ON]->(n)",
                    rid=run_id,
                    pname=pod_name,
                    node=node_name,
                )
            # Link pod to its parent Deployment (pod name = deployment-<hash>)
            # Try matching by stripping the last two hyphen-separated segments
            parts = pod_name.rsplit("-", 2)
            if len(parts) >= 3:
                dep_name = parts[0]
            elif len(parts) == 2:
                dep_name = parts[0]
            else:
                dep_name = pod_name
            if dep_name:
                tx.run(
                    "MATCH (p:PodSnapshot {run_id: $rid, name: $pname}), "
                    "      (d:Deployment {name: $dep}) "
                    "MERGE (p)-[:BELONGS_TO]->(d)",
                    rid=run_id,
                    pname=pod_name,
                    dep=dep_name,
                )

    # ------------------------------------------------------------------
    # Time-series and anomaly label sync
    # ------------------------------------------------------------------

    def _sync_time_series(
        self,
        tx: Any,
        run_id: str,
        metrics: Dict[str, Any],
        strategy: str,
    ) -> None:
        """Store raw time-series samples from all metric streams.

        Creates one ``MetricsSample`` node per timestamp bucket, containing
        flattened metric values from latency, resources, throughput, and
        Prometheus probers.
        """
        self._clear_children(tx, run_id, "HAS_SAMPLE", "MetricsSample")

        # Collect all time-series entries keyed by timestamp
        samples: Dict[str, Dict[str, Any]] = {}

        def _ensure(ts: str) -> Dict[str, Any]:
            if ts not in samples:
                samples[ts] = {"timestamp": ts, "strategy": strategy}
            return samples[ts]

        # Latency time-series
        latency = metrics.get("latency", {})
        for entry in latency.get("timeSeries", []):
            ts = entry.get("timestamp")
            if not ts:
                continue
            s = _ensure(ts)
            s["phase"] = entry.get("phase", "")
            for route, data in entry.get("routes", {}).items():
                s[f"latency:{route}:ms"] = data.get("latency_ms")
                s[f"latency:{route}:error"] = 1 if data.get("status") != "ok" else 0

        # Resource time-series
        resources = metrics.get("resources", {})
        if resources.get("available"):
            for entry in resources.get("timeSeries", []):
                ts = entry.get("timestamp")
                if not ts:
                    continue
                s = _ensure(ts)
                s.setdefault("phase", entry.get("phase", ""))
                node = entry.get("node", {})
                s["node_cpu_millicores"] = node.get("cpu_millicores")
                s["node_cpu_percent"] = node.get("cpu_percent")
                s["node_memory_bytes"] = node.get("memory_bytes")
                s["node_memory_percent"] = node.get("memory_percent")
                agg = entry.get("podAggregate", {})
                s["pod_total_cpu_millicores"] = agg.get("totalCpu_millicores")
                s["pod_total_memory_bytes"] = agg.get("totalMemory_bytes")
                s["pod_count"] = agg.get("podCount")

        # Redis time-series
        redis = metrics.get("redis", {})
        for entry in redis.get("timeSeries", []):
            ts = entry.get("timestamp")
            if not ts:
                continue
            s = _ensure(ts)
            s.setdefault("phase", entry.get("phase", ""))
            for op, data in entry.get("redis", {}).items():
                s[f"redis:{op}:ops_per_s"] = data.get("ops_per_second")
                s[f"redis:{op}:latency_ms"] = data.get("latency_ms")

        # Disk time-series
        disk = metrics.get("disk", {})
        for entry in disk.get("timeSeries", []):
            ts = entry.get("timestamp")
            if not ts:
                continue
            s = _ensure(ts)
            s.setdefault("phase", entry.get("phase", ""))
            for op, data in entry.get("disk", {}).items():
                s[f"disk:{op}:ops_per_s"] = data.get("ops_per_second")
                s[f"disk:{op}:bytes_per_s"] = data.get("bytes_per_second")

        # Prometheus time-series
        prometheus = metrics.get("prometheus", {})
        if prometheus.get("available"):
            for entry in prometheus.get("timeSeries", []):
                ts = entry.get("timestamp")
                if not ts:
                    continue
                s = _ensure(ts)
                s.setdefault("phase", entry.get("phase", ""))
                for metric_name, values in entry.get("metrics", {}).items():
                    total = 0.0
                    count = 0
                    for v in values:
                        try:
                            if isinstance(v, dict):
                                total += float(v.get("value", [0, "0"])[1])
                                count += 1
                        except (ValueError, IndexError, TypeError):
                            pass
                    if count > 0:
                        s[f"prom:{metric_name}:sum"] = round(total, 4)
                        s[f"prom:{metric_name}:avg"] = round(total / count, 4)

        if not samples:
            return

        # Correlate samples with recovery cycles: mark each sample with
        # recovery_in_progress and the cycle index if the sample's
        # timestamp falls within a deletion-to-ready window.
        recovery = metrics.get("recovery", {})
        recovery_cycles = recovery.get("recoveryEvents", [])
        if recovery_cycles:
            self._annotate_recovery_windows(samples, recovery_cycles)

        # Batch-create sample nodes (use UNWIND for efficiency)
        sample_list = sorted(samples.values(), key=lambda s: s["timestamp"])
        for idx, sample in enumerate(sample_list):
            sample["seq"] = idx
            sample["data_json"] = json.dumps(sample)

        tx.run(
            "UNWIND $samples AS s "
            "MATCH (e:ChaosRun {run_id: $rid}) "
            "CREATE (m:MetricsSample {"
            "  name: '#' + toString(s.seq) + ' ' + s.phase, "
            "  run_id: $rid, seq: s.seq, "
            "  timestamp: s.timestamp, "
            "  phase: s.phase, "
            "  strategy: s.strategy, "
            "  recovery_in_progress: s.recovery_in_progress, "
            "  recovery_cycle_id: s.recovery_cycle_id, "
            "  data: s.data_json"
            "}) "
            "CREATE (e)-[:HAS_SAMPLE]->(m)",
            rid=run_id,
            samples=sample_list,
        )

    @staticmethod
    def _annotate_recovery_windows(
        samples: Dict[str, Dict[str, Any]],
        recovery_cycles: List[Dict[str, Any]],
    ) -> None:
        """Mark each sample with recovery_in_progress and cycle index.

        Compares each sample timestamp against recovery cycle windows
        (deletionTime → readyTime).  Sets ``recovery_in_progress`` to
        True and ``recovery_cycle_id`` to the cycle index for samples
        that fall within an active recovery window.
        """
        from datetime import datetime, timezone

        # Parse cycle windows once
        windows: List[tuple] = []
        for idx, cycle in enumerate(recovery_cycles):
            raw_del = cycle.get("deletionTime")
            raw_rdy = cycle.get("readyTime")
            if not raw_del:
                continue
            try:
                t_del = datetime.fromisoformat(raw_del)
                if t_del.tzinfo is None:
                    t_del = t_del.replace(tzinfo=timezone.utc)
                t_del_epoch = t_del.timestamp()
            except (ValueError, TypeError):
                continue

            if raw_rdy:
                try:
                    t_rdy = datetime.fromisoformat(raw_rdy)
                    if t_rdy.tzinfo is None:
                        t_rdy = t_rdy.replace(tzinfo=timezone.utc)
                    t_rdy_epoch = t_rdy.timestamp()
                except (ValueError, TypeError):
                    t_rdy_epoch = None
            else:
                t_rdy_epoch = None  # Incomplete cycle — still in progress

            windows.append((idx, t_del_epoch, t_rdy_epoch))

        if not windows:
            for s in samples.values():
                s.setdefault("recovery_in_progress", False)
                s.setdefault("recovery_cycle_id", None)
            return

        for s in samples.values():
            ts_str = s.get("timestamp", "")
            try:
                ts_dt = datetime.fromisoformat(ts_str)
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                ts_epoch = ts_dt.timestamp()
            except (ValueError, TypeError):
                s["recovery_in_progress"] = False
                s["recovery_cycle_id"] = None
                continue

            in_recovery = False
            cycle_id = None
            for idx, t_del, t_rdy in windows:
                if t_rdy is not None:
                    if t_del <= ts_epoch <= t_rdy:
                        in_recovery = True
                        cycle_id = idx
                        break
                else:
                    # Incomplete cycle — mark everything after deletion
                    if ts_epoch >= t_del:
                        in_recovery = True
                        cycle_id = idx
                        break

            s["recovery_in_progress"] = in_recovery
            s["recovery_cycle_id"] = cycle_id

    def _sync_anomaly_labels(
        self,
        tx: Any,
        run_id: str,
        labels: List[Dict[str, Any]],
    ) -> None:
        """Store anomaly labels with graph edges to affected services."""
        self._clear_children(tx, run_id, "HAS_ANOMALY_LABEL", "AnomalyLabel")
        for lbl in labels:
            tx.run(
                "MATCH (e:ChaosRun {run_id: $rid}) "
                "CREATE (a:AnomalyLabel {"
                "  name: $fault + ' (' + $severity + ')', "
                "  run_id: $rid, "
                "  fault_type: $fault, "
                "  category: $cat, "
                "  resource: $resource, "
                "  severity: $severity, "
                "  target_service: $target, "
                "  target_node: $node, "
                "  start_time: $start, "
                "  end_time: $end, "
                "  observed_cycle_count: $obs_count, "
                "  observed_completed_cycles: $obs_completed, "
                "  observed_incomplete_cycles: $obs_incomplete, "
                "  observed_windows_json: $obs_windows"
                "}) "
                "CREATE (e)-[:HAS_ANOMALY_LABEL]->(a)",
                rid=run_id,
                fault=lbl.get("faultType", "unknown"),
                cat=lbl.get("category", "unknown"),
                resource=lbl.get("resource", "unknown"),
                severity=lbl.get("severity", "medium"),
                target=lbl.get("targetService", ""),
                node=lbl.get("targetNode"),
                start=lbl.get("startTime"),
                end=lbl.get("endTime"),
                obs_count=lbl.get("observedCycleCount", 0),
                obs_completed=lbl.get("observedCompletedCycles", 0),
                obs_incomplete=lbl.get("observedIncompleteCycles", 0),
                obs_windows=json.dumps(lbl.get("observedWindows", [])),
            )
            # Link to targeted service (primary)
            target_svc = lbl.get("targetService")
            fault_type = lbl.get("faultType", "unknown")
            if target_svc:
                tx.run(
                    "MATCH (a:AnomalyLabel {run_id: $rid, fault_type: $fault, "
                    "       target_service: $svc}), "
                    "      (s:Service {name: $svc}) "
                    "MERGE (a)-[:TARGETS]->(s)",
                    rid=run_id,
                    fault=fault_type,
                    svc=target_svc,
                )
            # Link to all affected services via AFFECTS edges
            for affected_svc in lbl.get("affectedServices", []):
                if affected_svc:
                    tx.run(
                        "MATCH (a:AnomalyLabel {run_id: $rid, fault_type: $fault, "
                        "       target_service: $target}), "
                        "      (s:Service {name: $svc}) "
                        "MERGE (a)-[:AFFECTS]->(s)",
                        rid=run_id,
                        fault=fault_type,
                        target=lbl.get("targetService", ""),
                        svc=affected_svc,
                    )

    # ------------------------------------------------------------------
    # Cascade timeline sync
    # ------------------------------------------------------------------

    def _sync_cascade_timeline(
        self,
        tx: Any,
        run_id: str,
        cascade: Any,
    ) -> None:
        """Store cascade timeline events for a run."""
        self._clear_children(tx, run_id, "HAS_CASCADE_EVENT", "CascadeEvent")
        if not cascade:
            return
        # compute_cascade_timeline returns a single dict; normalize to list
        if isinstance(cascade, dict):
            cascade = [cascade]
        for idx, evt in enumerate(cascade):
            tx.run(
                "MATCH (e:ChaosRun {run_id: $rid}) "
                "CREATE (c:CascadeEvent {"
                "  name: 'cascade #' + toString($seq) + ' → ' + $target, "
                "  run_id: $rid, seq: $seq, "
                "  target_service: $target, "
                "  data_json: $data"
                "}) "
                "CREATE (e)-[:HAS_CASCADE_EVENT]->(c)",
                rid=run_id,
                seq=idx,
                target=evt.get("targetService", ""),
                data=json.dumps(evt),
            )

    def _sync_container_logs(
        self,
        tx: Any,
        run_id: str,
        container_logs: Dict[str, Any],
    ) -> None:
        """Store container logs linked to their parent PodSnapshot nodes.

        Creates one ``ContainerLog`` node per container, linked to the
        matching ``PodSnapshot`` via ``HAS_CONTAINER_LOG``.  This models
        the Kubernetes hierarchy: ChaosRun → PodSnapshot → ContainerLog,
        so graph queries can traverse Deployment → Pod → Container logs.

        Falls back to a direct ``ChaosRun`` link when no matching
        ``PodSnapshot`` exists (e.g. pod was already gone at collection
        time).
        """
        # Clear all ContainerLog nodes for this run (may be linked via
        # PodSnapshot or directly to ChaosRun)
        tx.run(
            "MATCH (l:ContainerLog {run_id: $rid}) DETACH DELETE l",
            rid=run_id,
        )
        if not container_logs:
            return
        pods = container_logs.get("pods", {})
        for pod_name, pod_data in pods.items():
            if not isinstance(pod_data, dict):
                continue
            restart_count = pod_data.get("restartCount", 0)
            for container_name, logs in pod_data.get("containers", {}).items():
                if not isinstance(logs, dict):
                    continue
                current_log = logs.get("current") or ""
                previous_log = logs.get("previous") or ""
                # Truncate very large logs to keep Neo4j performant
                max_len = 50_000
                if len(current_log) > max_len:
                    current_log = current_log[-max_len:]
                if len(previous_log) > max_len:
                    previous_log = previous_log[-max_len:]

                # Link to PodSnapshot if one exists for this pod,
                # otherwise fall back to ChaosRun
                tx.run(
                    "OPTIONAL MATCH (p:PodSnapshot {run_id: $rid, name: $pod}) "
                    "WITH p "
                    "MATCH (e:ChaosRun {run_id: $rid}) "
                    "CREATE (l:ContainerLog {"
                    "  name: $pod + '/' + $container, "
                    "  run_id: $rid, pod_name: $pod, "
                    "  container_name: $container, "
                    "  restart_count: $restarts, "
                    "  current_log: $current, "
                    "  previous_log: $previous, "
                    "  has_previous: $has_prev"
                    "}) "
                    "FOREACH (_ IN CASE WHEN p IS NOT NULL THEN [1] ELSE [] END | "
                    "  CREATE (p)-[:HAS_CONTAINER_LOG]->(l)) "
                    "FOREACH (_ IN CASE WHEN p IS NULL THEN [1] ELSE [] END | "
                    "  CREATE (e)-[:HAS_CONTAINER_LOG]->(l))",
                    rid=run_id,
                    pod=pod_name,
                    container=container_name,
                    restarts=restart_count,
                    current=current_log,
                    previous=previous_log,
                    has_prev=bool(previous_log),
                )
