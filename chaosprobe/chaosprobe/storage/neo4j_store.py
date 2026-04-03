"""Neo4j graph store for ChaosProbe topology and experiment data.

Stores Kubernetes topology (nodes, deployments, services, pods),
service dependency graphs, placement decisions, experiment results,
and **all collected metrics** (recovery, latency, throughput, resources,
Prometheus) in a Neo4j graph database.

This is a standalone store (not inheriting ``ResultStore``) because
the graph query model is fundamentally different from the tabular
SQLite store.

The ``neo4j`` driver is imported lazily so the rest of ChaosProbe
works without it installed.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _require_neo4j():
    """Import and return the neo4j module, raising a friendly error if missing."""
    try:
        import neo4j

        return neo4j
    except ImportError:
        raise ImportError(
            "Neo4j support requires the 'neo4j' package.\n"
            "Install it with:  uv pip install chaosprobe[graph]"
        )


class Neo4jStore:
    """Graph store backed by Neo4j.

    Usage::

        store = Neo4jStore("bolt://localhost:7687", "neo4j", "password")
        store.ensure_schema()
        store.sync_topology(nodes, deployments)
        store.sync_service_dependencies(routes=service_routes)
        store.sync_run(run_data)
        store.close()
    """

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "neo4j",
    ):
        neo4j = _require_neo4j()
        self._driver = neo4j.GraphDatabase.driver(uri, auth=(user, password))
        self._driver.verify_connectivity()

    def close(self) -> None:
        """Close the driver connection."""
        self._driver.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create uniqueness constraints and indexes."""
        constraints = [
            ("k8snode_name", "K8sNode", "name"),
            ("deployment_name", "Deployment", "name"),
            ("service_name", "Service", "name"),
            ("chaosrun_run_id", "ChaosRun", "run_id"),
            ("strategy_name", "PlacementStrategy", "name"),
        ]
        # Indexes on run_id for fast per-run lookups on child nodes
        indexes = [
            ("idx_recovery_cycle_run", "RecoveryCycle", "run_id"),
            ("idx_experiment_result_run", "ExperimentResult", "run_id"),
            ("idx_metrics_phase_run", "MetricsPhase", "run_id"),
            ("idx_pod_snapshot_run", "PodSnapshot", "run_id"),
            ("idx_metrics_sample_run", "MetricsSample", "run_id"),
            ("idx_metrics_sample_ts", "MetricsSample", "timestamp"),
            ("idx_anomaly_label_run", "AnomalyLabel", "run_id"),
            ("idx_cascade_event_run", "CascadeEvent", "run_id"),
            ("idx_container_log_run", "ContainerLog", "run_id"),
            ("idx_chaosrun_session", "ChaosRun", "session_id"),
            ("idx_probe_result_run", "ProbeResult", "run_id"),
        ]
        with self._driver.session() as session:
            for cname, label, prop in constraints:
                session.run(
                    f"CREATE CONSTRAINT {cname} IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
                )
            for iname, label, prop in indexes:
                session.run(f"CREATE INDEX {iname} IF NOT EXISTS " f"FOR (n:{label}) ON (n.{prop})")

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
        strategy_name = "baseline"
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
                    "SET e.timestamp = $ts, e.verdict = $verdict, "
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
                "  run_id: $rid, seq: $seq, "
                "  deletion_time: $del_t, scheduled_time: $sched_t, ready_time: $ready_t, "
                "  deletion_to_scheduled_ms: $d2s, scheduled_to_ready_ms: $s2r, "
                "  total_recovery_ms: $total"
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
                "  run_id: $rid, name: $name, engine_name: $engine, "
                "  verdict: $verdict, "
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
                    "  run_id: $rid, name: $exp_name, engine_name: $engine"
                    "}) "
                    "CREATE (p:ProbeResult {"
                    "  run_id: $rid, "
                    "  name: $name, "
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
                    node_name=resources.get("nodeName"),
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
                "  run_id: $rid, name: $name, phase: $phase, "
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
    # Graph queries
    # ------------------------------------------------------------------

    def get_blast_radius(
        self,
        service: str,
        max_hops: int = 3,
    ) -> List[Dict[str, Any]]:
        """Find upstream services that depend on *service*.

        Returns a list of ``{"name": str, "hops": int}`` dicts ordered by
        distance (closest first).
        """
        if not isinstance(max_hops, int) or max_hops < 1:
            max_hops = 3
        with self._driver.session() as session:
            result = session.run(
                f"MATCH path = (t:Service {{name: $svc}})"
                f"<-[:DEPENDS_ON*1..{max_hops}]-(upstream:Service) "
                "RETURN upstream.name AS name, "
                "       length(path) AS hops "
                "ORDER BY hops",
                svc=service,
            )
            return [dict(r) for r in result]

    def get_colocation_analysis(
        self,
        run_id: str,
    ) -> List[Dict[str, Any]]:
        """Find deployments sharing a node for a given run.

        Returns a list of ``{"node": str, "deployments": [str, ...]}``
        dicts, one per node with 2+ deployments.
        """
        with self._driver.session() as session:
            result = session.run(
                "MATCH (d:Deployment)-[:SCHEDULED_ON {run_id: $rid}]->(n:K8sNode) "
                "WITH n, collect(d.name) AS deps "
                "WHERE size(deps) > 1 "
                "RETURN n.name AS node, deps AS deployments "
                "ORDER BY size(deps) DESC",
                rid=run_id,
            )
            return [dict(r) for r in result]

    def compare_strategies_graph(
        self,
        run_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Compare experiment results across strategies.

        Returns a list of ``{"strategy": str, "run_id": str,
        "resilience_score": float, "mean_recovery_ms": float|None}``
        dicts.
        """
        query = "MATCH (e:ChaosRun)-[:USED_STRATEGY]->(s:PlacementStrategy) "
        params: Dict[str, Any] = {}
        if run_ids:
            query += "WHERE e.run_id IN $run_ids "
            params["run_ids"] = run_ids
        query += (
            "RETURN s.name AS strategy, e.run_id AS run_id, "
            "       e.resilience_score AS resilience_score, "
            "       e.mean_recovery_ms AS mean_recovery_ms "
            "ORDER BY s.name, e.timestamp"
        )
        with self._driver.session() as session:
            result = session.run(query, **params)
            return [dict(r) for r in result]

    def get_topology(self, run_id: str) -> Dict[str, Any]:
        """Return full placement topology for a run.

        Returns::

            {
                "nodes": [{"name": ..., "deployments": [...]}],
                "unscheduled": [str, ...],
            }
        """
        with self._driver.session() as session:
            scheduled = session.run(
                "MATCH (d:Deployment)-[:SCHEDULED_ON {run_id: $rid}]->(n:K8sNode) "
                "RETURN n.name AS node, collect(d.name) AS deployments "
                "ORDER BY n.name",
                rid=run_id,
            )
            nodes = [dict(r) for r in scheduled]

            unscheduled = session.run(
                "MATCH (d:Deployment) "
                "WHERE NOT (d)-[:SCHEDULED_ON {run_id: $rid}]->(:K8sNode) "
                "RETURN d.name AS name ORDER BY d.name",
                rid=run_id,
            )
            return {
                "nodes": nodes,
                "unscheduled": [r["name"] for r in unscheduled],
            }

    def get_run_details(self, run_id: str) -> Dict[str, Any]:
        """Return comprehensive data for a single run.

        Includes experiment properties, recovery cycles, experiment results,
        metrics phase summaries, and pod snapshots.
        """
        with self._driver.session() as session:
            # Experiment node
            exp_result = session.run(
                "MATCH (e:ChaosRun {run_id: $rid}) " "RETURN properties(e) AS props",
                rid=run_id,
            )
            exp_record = exp_result.single()
            if not exp_record:
                return {}
            experiment = dict(exp_record["props"])

            # Recovery cycles
            cycles_result = session.run(
                "MATCH (e:ChaosRun {run_id: $rid})"
                "-[:HAS_RECOVERY_CYCLE]->(c:RecoveryCycle) "
                "RETURN properties(c) AS props ORDER BY c.seq",
                rid=run_id,
            )
            recovery_cycles = [dict(r["props"]) for r in cycles_result]

            # Experiment results
            results_result = session.run(
                "MATCH (e:ChaosRun {run_id: $rid})"
                "-[:HAS_RESULT]->(r:ExperimentResult) "
                "RETURN properties(r) AS props",
                rid=run_id,
            )
            experiment_results = [dict(r["props"]) for r in results_result]

            # Metrics phases
            phases_result = session.run(
                "MATCH (e:ChaosRun {run_id: $rid})"
                "-[:HAS_METRICS_PHASE]->(m:MetricsPhase) "
                "RETURN properties(m) AS props "
                "ORDER BY m.metric_type, m.phase",
                rid=run_id,
            )
            metrics_phases = [dict(r["props"]) for r in phases_result]

            # Pod snapshots
            pods_result = session.run(
                "MATCH (e:ChaosRun {run_id: $rid})"
                "-[:HAS_POD_SNAPSHOT]->(p:PodSnapshot) "
                "RETURN properties(p) AS props",
                rid=run_id,
            )
            pod_snapshots = [dict(r["props"]) for r in pods_result]

            # Container logs (via PodSnapshot or direct fallback)
            logs_result = session.run(
                "MATCH (e:ChaosRun {run_id: $rid}) "
                "OPTIONAL MATCH (e)-[:HAS_POD_SNAPSHOT]->(p:PodSnapshot)"
                "-[:HAS_CONTAINER_LOG]->(l:ContainerLog) "
                "WITH e, collect(l) AS pod_logs "
                "OPTIONAL MATCH (e)-[:HAS_CONTAINER_LOG]->(dl:ContainerLog) "
                "WITH pod_logs + collect(dl) AS all_logs "
                "UNWIND all_logs AS l "
                "RETURN DISTINCT properties(l) AS props",
                rid=run_id,
            )
            container_logs = [dict(r["props"]) for r in logs_result]

            # Probe results (via ExperimentResult)
            probes_result = session.run(
                "MATCH (e:ChaosRun {run_id: $rid})"
                "-[:HAS_RESULT]->(r:ExperimentResult)"
                "-[:HAS_PROBE]->(p:ProbeResult) "
                "RETURN r.name AS experiment_name, "
                "       properties(p) AS props "
                "ORDER BY r.name, p.name",
                rid=run_id,
            )
            probe_results = [
                {"experiment": r["experiment_name"], **dict(r["props"])}
                for r in probes_result
            ]

            return {
                "experiment": experiment,
                "recoveryCycles": recovery_cycles,
                "experimentResults": experiment_results,
                "metricsPhases": metrics_phases,
                "podSnapshots": pod_snapshots,
                "containerLogs": container_logs,
                "probeResults": probe_results,
            }

    def status(self) -> Dict[str, Any]:
        """Return node/relationship counts for a quick health check."""
        with self._driver.session() as session:
            counts = {}
            for label in (
                "K8sNode",
                "Deployment",
                "Service",
                "ChaosRun",
                "PlacementStrategy",
                "RecoveryCycle",
                "ExperimentResult",
                "ProbeResult",
                "MetricsPhase",
                "PodSnapshot",
                "MetricsSample",
                "AnomalyLabel",
                "CascadeEvent",
                "ContainerLog",
            ):
                result = session.run(f"MATCH (n:{label}) RETURN count(n) AS c")
                counts[label] = result.single()["c"]
            return counts

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

        # Batch-create sample nodes (use UNWIND for efficiency)
        sample_list = sorted(samples.values(), key=lambda s: s["timestamp"])
        for idx, sample in enumerate(sample_list):
            sample["seq"] = idx
            sample["data_json"] = json.dumps(sample)

        tx.run(
            "UNWIND $samples AS s "
            "MATCH (e:ChaosRun {run_id: $rid}) "
            "CREATE (m:MetricsSample {"
            "  run_id: $rid, seq: s.seq, "
            "  timestamp: s.timestamp, "
            "  phase: s.phase, "
            "  strategy: s.strategy, "
            "  data: s.data_json"
            "}) "
            "CREATE (e)-[:HAS_SAMPLE]->(m)",
            rid=run_id,
            samples=sample_list,
        )

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
                "  run_id: $rid, "
                "  fault_type: $fault, "
                "  category: $cat, "
                "  resource: $resource, "
                "  severity: $severity, "
                "  target_service: $target, "
                "  target_node: $node, "
                "  start_time: $start, "
                "  end_time: $end"
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

    # ------------------------------------------------------------------
    # Session and retrieval helpers
    # ------------------------------------------------------------------

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Return all distinct session_ids with run counts."""
        with self._driver.session() as session:
            result = session.run(
                "MATCH (e:ChaosRun) "
                "WHERE e.session_id IS NOT NULL AND e.session_id <> '' "
                "RETURN e.session_id AS session_id, "
                "       count(e) AS run_count, "
                "       min(e.timestamp) AS first_run, "
                "       collect(DISTINCT e.strategy) AS strategies "
                "ORDER BY first_run DESC"
            )
            return [dict(r) for r in result]

    def get_session_runs(self, session_id: str) -> List[Dict[str, Any]]:
        """Return all ChaosRun nodes for a session."""
        with self._driver.session() as session:
            result = session.run(
                "MATCH (e:ChaosRun {session_id: $sid}) "
                "RETURN properties(e) AS props "
                "ORDER BY e.strategy, e.timestamp",
                sid=session_id,
            )
            return [dict(r["props"]) for r in result]

    def get_run_output(self, run_id: str) -> Dict[str, Any]:
        """Reconstruct the full output_data dict from Neo4j for a run.

        Returns a dict compatible with the format produced by
        ``OutputGenerator.generate()``, suitable for ``compare_runs()``.
        """
        details = self.get_run_details(run_id)
        if not details:
            return {}

        exp = details["experiment"]

        # Reconstruct metrics from MetricsPhase nodes
        metrics: Dict[str, Any] = {}
        time_window: Dict[str, Any] = {}
        if exp.get("time_window_start"):
            time_window = {
                "start": exp["time_window_start"],
                "end": exp.get("time_window_end"),
                "duration_s": exp.get("duration_s"),
            }
        metrics["timeWindow"] = time_window
        metrics["deploymentName"] = ""

        # Recovery
        recovery_events = []
        for c in details.get("recoveryCycles", []):
            recovery_events.append(
                {
                    "deletionTime": c.get("deletion_time"),
                    "scheduledTime": c.get("scheduled_time"),
                    "readyTime": c.get("ready_time"),
                    "deletionToScheduled_ms": c.get("deletion_to_scheduled_ms"),
                    "scheduledToReady_ms": c.get("scheduled_to_ready_ms"),
                    "totalRecovery_ms": c.get("total_recovery_ms"),
                }
            )
        recovery_summary = {}
        if exp.get("recovery_count", 0) > 0:
            recovery_summary = {
                "count": exp.get("recovery_count", 0),
                "completedCycles": exp.get("completed_cycles", 0),
                "incompleteCycles": exp.get("incomplete_cycles", 0),
                "meanRecovery_ms": exp.get("mean_recovery_ms"),
                "medianRecovery_ms": exp.get("median_recovery_ms"),
                "minRecovery_ms": exp.get("min_recovery_ms"),
                "maxRecovery_ms": exp.get("max_recovery_ms"),
                "p95Recovery_ms": exp.get("p95_recovery_ms"),
            }
        metrics["recovery"] = {
            "recoveryEvents": recovery_events,
            "summary": recovery_summary,
        }

        # Pod status
        pods = []
        for p in details.get("podSnapshots", []):
            conditions = {}
            if p.get("conditions"):
                try:
                    conditions = json.loads(p["conditions"])
                except (json.JSONDecodeError, TypeError):
                    pass
            pods.append(
                {
                    "name": p.get("name", ""),
                    "phase": p.get("phase", ""),
                    "node": p.get("node"),
                    "restartCount": p.get("restart_count", 0),
                    "conditions": conditions,
                }
            )
        metrics["podStatus"] = {
            "pods": pods,
            "totalRestarts": exp.get("total_restarts", 0),
        }

        # Event timeline
        timeline = []
        if exp.get("event_timeline"):
            try:
                timeline = json.loads(exp["event_timeline"])
            except (json.JSONDecodeError, TypeError):
                pass
        metrics["eventTimeline"] = timeline

        # Metrics phases → latency, resources, prometheus, redis, disk
        for mp in details.get("metricsPhases", []):
            mtype = mp.get("metric_type", "")
            phase = mp.get("phase", "")
            if mtype == "latency":
                metrics.setdefault("latency", {"phases": {}})
                routes = {}
                if mp.get("routes"):
                    try:
                        routes = json.loads(mp["routes"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                metrics["latency"]["phases"][phase] = {
                    "sampleCount": mp.get("sample_count", 0),
                    "routes": routes,
                }
            elif mtype == "resources":
                metrics.setdefault("resources", {"available": True, "phases": {}})
                metrics["resources"]["nodeName"] = mp.get("node_name")
                metrics["resources"]["phases"][phase] = {
                    "sampleCount": mp.get("sample_count", 0),
                    "node": {
                        "meanCpu_millicores": mp.get("mean_cpu_millicores"),
                        "maxCpu_millicores": mp.get("max_cpu_millicores"),
                        "meanMemory_bytes": mp.get("mean_memory_bytes"),
                        "maxMemory_bytes": mp.get("max_memory_bytes"),
                        "meanCpu_percent": mp.get("mean_cpu_percent"),
                        "maxCpu_percent": mp.get("max_cpu_percent"),
                        "meanMemory_percent": mp.get("mean_memory_percent"),
                        "maxMemory_percent": mp.get("max_memory_percent"),
                    },
                }
            elif mtype == "prometheus":
                metrics.setdefault("prometheus", {"available": True, "phases": {}})
                prom_metrics = {}
                if mp.get("metrics_json"):
                    try:
                        prom_metrics = json.loads(mp["metrics_json"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                metrics["prometheus"]["phases"][phase] = {
                    "sampleCount": mp.get("sample_count", 0),
                    "metrics": prom_metrics,
                }
            elif mtype == "redis":
                metrics.setdefault("redis", {"phases": {}})
                ops = {}
                if mp.get("operations"):
                    try:
                        ops = json.loads(mp["operations"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                metrics["redis"]["phases"][phase] = {
                    "sampleCount": mp.get("sample_count", 0),
                    "redis": ops,
                }
            elif mtype == "disk":
                metrics.setdefault("disk", {"phases": {}})
                ops = {}
                if mp.get("operations"):
                    try:
                        ops = json.loads(mp["operations"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                metrics["disk"]["phases"][phase] = {
                    "sampleCount": mp.get("sample_count", 0),
                    "disk": ops,
                }

        # Experiment results
        experiments = []
        for er in details.get("experimentResults", []):
            # Reconstruct probes from ProbeResult nodes
            probes = [
                {
                    "name": pr.get("name", ""),
                    "type": pr.get("type", ""),
                    "mode": pr.get("mode", ""),
                    "status": {
                        "verdict": pr.get("verdict", ""),
                        "description": pr.get("description", ""),
                    },
                }
                for pr in details.get("probeResults", [])
                if pr.get("experiment") == er.get("name", "")
            ]
            experiments.append(
                {
                    "name": er.get("name", ""),
                    "engineName": er.get("engine_name", ""),
                    "result": {
                        "phase": er.get("phase", ""),
                        "verdict": er.get("verdict", "Unknown"),
                        "probeSuccessPercentage": er.get("probe_success_pct", 0),
                        "failStep": er.get("fail_step", ""),
                    },
                    "probes": probes,
                }
            )

        # Scenario
        scenario = {}
        if exp.get("scenario_json"):
            try:
                scenario = json.loads(exp["scenario_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Anomaly labels
        anomaly_labels = self._get_anomaly_labels(run_id)

        # Cascade timeline
        cascade_timeline = self._get_cascade_timeline(run_id)

        # Placement
        placement = {"strategy": exp.get("strategy", "baseline")}

        # Load generation
        load_gen: Dict[str, Any] = {}
        if exp.get("load_profile"):
            load_gen = {
                "profile": exp["load_profile"],
                "stats": {
                    "totalRequests": exp.get("load_total_requests"),
                    "totalFailures": exp.get("load_total_failures"),
                    "avgResponseTime_ms": exp.get("load_avg_response_ms"),
                    "p50ResponseTime_ms": exp.get("load_p50_response_ms"),
                    "p95ResponseTime_ms": exp.get("load_p95_response_ms"),
                    "p99ResponseTime_ms": exp.get("load_p99_response_ms"),
                    "requestsPerSecond": exp.get("load_rps"),
                    "errorRate": exp.get("load_error_rate"),
                    "duration_seconds": exp.get("load_duration_s"),
                },
            }

        # Node info
        node_info: Dict[str, Any] = {}
        if exp.get("node_name"):
            node_info["nodeName"] = exp["node_name"]
            if exp.get("node_capacity_cpu") is not None:
                node_info["capacity"] = {
                    "cpu": exp.get("node_capacity_cpu"),
                    "memory": exp.get("node_capacity_memory"),
                }
            if exp.get("node_allocatable_cpu") is not None:
                node_info["allocatable"] = {
                    "cpu": exp.get("node_allocatable_cpu"),
                    "memory": exp.get("node_allocatable_memory"),
                }
        if node_info:
            metrics["nodeInfo"] = node_info

        # Container logs
        container_logs_raw = details.get("containerLogs", [])
        if container_logs_raw:
            pods_logs: Dict[str, Any] = {}
            for log_entry in container_logs_raw:
                pod_name = log_entry.get("pod_name", "")
                container_name = log_entry.get("container_name", "")
                pod_dict = pods_logs.setdefault(pod_name, {
                    "restartCount": log_entry.get("restart_count", 0),
                    "containers": {},
                })
                pod_dict["containers"][container_name] = {
                    "current": log_entry.get("current_log", ""),
                    "previous": log_entry.get("previous_log", ""),
                }
            if pods_logs:
                metrics["containerLogs"] = {"pods": pods_logs}

        output = {
            "runId": run_id,
            "timestamp": exp.get("timestamp", ""),
            "sessionId": exp.get("session_id", ""),
            "scenario": scenario,
            "experiments": experiments,
            "summary": {
                "totalExperiments": exp.get("total_experiments", 0),
                "passed": exp.get("passed_experiments", 0),
                "failed": exp.get("failed_experiments", 0),
                "resilienceScore": exp.get("resilience_score", 0),
                "overallVerdict": exp.get("verdict", "UNKNOWN"),
            },
            "metrics": metrics,
            "anomalyLabels": anomaly_labels,
            "cascadeTimeline": cascade_timeline,
            "placement": placement,
        }
        if load_gen:
            output["loadGeneration"] = load_gen

        # Reconstruct time-series arrays from MetricsSample nodes
        self._reconstruct_time_series(run_id, metrics)

        return output

    def _reconstruct_time_series(
        self,
        run_id: str,
        metrics: Dict[str, Any],
    ) -> None:
        """Populate timeSeries arrays in metrics from MetricsSample nodes."""
        with self._driver.session() as session:
            result = session.run(
                "MATCH (e:ChaosRun {run_id: $rid})"
                "-[:HAS_SAMPLE]->(s:MetricsSample) "
                "RETURN s.data AS data "
                "ORDER BY s.seq",
                rid=run_id,
            )
            samples = []
            for r in result:
                try:
                    samples.append(json.loads(r["data"]))
                except (json.JSONDecodeError, TypeError):
                    pass

        if not samples:
            return

        # Rebuild latency timeSeries
        lat_ts = []
        for s in samples:
            routes: Dict[str, Any] = {}
            for k, v in s.items():
                if k.startswith("latency:") and k.endswith(":ms"):
                    route = k[len("latency:"):-len(":ms")]
                    err_key = f"latency:{route}:error"
                    routes[route] = {
                        "latency_ms": v,
                        "status": "error" if s.get(err_key, 0) else "ok",
                    }
            if routes:
                lat_ts.append(
                    {
                        "timestamp": s.get("timestamp", ""),
                        "phase": s.get("phase", ""),
                        "routes": routes,
                    }
                )
        if lat_ts:
            metrics.setdefault("latency", {"phases": {}})
            metrics["latency"]["timeSeries"] = lat_ts

        # Rebuild resources timeSeries
        res_ts = []
        for s in samples:
            if s.get("node_cpu_millicores") is not None:
                res_ts.append(
                    {
                        "timestamp": s.get("timestamp", ""),
                        "phase": s.get("phase", ""),
                        "node": {
                            "cpu_millicores": s.get("node_cpu_millicores"),
                            "cpu_percent": s.get("node_cpu_percent"),
                            "memory_bytes": s.get("node_memory_bytes"),
                            "memory_percent": s.get("node_memory_percent"),
                        },
                        "podAggregate": {
                            "totalCpu_millicores": s.get("pod_total_cpu_millicores"),
                            "totalMemory_bytes": s.get("pod_total_memory_bytes"),
                            "podCount": s.get("pod_count"),
                        },
                    }
                )
        if res_ts:
            metrics.setdefault("resources", {"available": True, "phases": {}})
            metrics["resources"]["timeSeries"] = res_ts

        # Rebuild redis timeSeries
        redis_ts = []
        for s in samples:
            ops: Dict[str, Any] = {}
            for k, v in s.items():
                if k.startswith("redis:") and k.endswith(":ops_per_s"):
                    op = k[len("redis:"):-len(":ops_per_s")]
                    lat_key = f"redis:{op}:latency_ms"
                    ops[op] = {
                        "ops_per_second": v,
                        "latency_ms": s.get(lat_key),
                    }
            if ops:
                redis_ts.append(
                    {
                        "timestamp": s.get("timestamp", ""),
                        "phase": s.get("phase", ""),
                        "redis": ops,
                    }
                )
        if redis_ts:
            metrics.setdefault("redis", {"phases": {}})
            metrics["redis"]["timeSeries"] = redis_ts

        # Rebuild disk timeSeries
        disk_ts = []
        for s in samples:
            ops = {}
            for k, v in s.items():
                if k.startswith("disk:") and k.endswith(":ops_per_s"):
                    op = k[len("disk:"):-len(":ops_per_s")]
                    bps_key = f"disk:{op}:bytes_per_s"
                    ops[op] = {
                        "ops_per_second": v,
                        "bytes_per_second": s.get(bps_key),
                    }
            if ops:
                disk_ts.append(
                    {
                        "timestamp": s.get("timestamp", ""),
                        "phase": s.get("phase", ""),
                        "disk": ops,
                    }
                )
        if disk_ts:
            metrics.setdefault("disk", {"phases": {}})
            metrics["disk"]["timeSeries"] = disk_ts

    def _get_anomaly_labels(self, run_id: str) -> List[Dict[str, Any]]:
        """Return anomaly labels for a run, reconstructing affected_services
        from AFFECTS edges."""
        with self._driver.session() as session:
            result = session.run(
                "MATCH (e:ChaosRun {run_id: $rid})"
                "-[:HAS_ANOMALY_LABEL]->(a:AnomalyLabel) "
                "OPTIONAL MATCH (a)-[:AFFECTS]->(s:Service) "
                "RETURN properties(a) AS props, "
                "       collect(s.name) AS affected",
                rid=run_id,
            )
            labels = []
            for r in result:
                lbl = dict(r["props"])
                lbl["affected_services"] = r["affected"] or []
                labels.append(lbl)
            return labels

    def _get_cascade_timeline(self, run_id: str) -> List[Dict[str, Any]]:
        """Return cascade timeline for a run."""
        with self._driver.session() as session:
            result = session.run(
                "MATCH (e:ChaosRun {run_id: $rid})"
                "-[:HAS_CASCADE_EVENT]->(c:CascadeEvent) "
                "RETURN c.data_json AS data "
                "ORDER BY c.seq",
                rid=run_id,
            )
            events = []
            for r in result:
                try:
                    events.append(json.loads(r["data"]))
                except (json.JSONDecodeError, TypeError):
                    pass
            return events

    def get_session_visualization_data(
        self,
        session_id: str,
        iterations: int = 1,
    ) -> Dict[str, Any]:
        """Reconstruct summary-compatible dict for visualization.

        Returns a dict matching the structure of summary.json so that
        ``generate_from_summary``-style chart generation works.
        """
        runs = self.get_session_runs(session_id)
        if not runs:
            return {}

        # Group runs by strategy
        by_strategy: Dict[str, List[Dict[str, Any]]] = {}
        for r in runs:
            strat = r.get("strategy", "baseline")
            by_strategy.setdefault(strat, []).append(r)

        strategies: Dict[str, Any] = {}
        for strat, strat_runs in by_strategy.items():
            # Get detailed data for each run
            run_details = []
            for sr in strat_runs:
                rid = sr.get("run_id", "")
                output = self.get_run_output(rid)
                if output:
                    run_details.append(output)

            if not run_details:
                continue

            if len(run_details) == 1:
                rd = run_details[0]
                strategies[strat] = {
                    "strategy": strat,
                    "status": "completed",
                    "experiment": rd.get("summary", {}),
                    "metrics": rd.get("metrics", {}),
                }
            else:
                # Multi-iteration: aggregate
                iter_data = []
                for rd in run_details:
                    iter_data.append(
                        {
                            "iteration": len(iter_data) + 1,
                            "verdict": rd.get("summary", {}).get("overallVerdict", "UNKNOWN"),
                            "resilienceScore": rd.get("summary", {}).get("resilienceScore", 0),
                            "metrics": rd.get("metrics", {}),
                        }
                    )
                scores = [it["resilienceScore"] for it in iter_data]
                pass_count = sum(1 for it in iter_data if it["verdict"] == "PASS")
                rec_times = []
                for it in iter_data:
                    mean_rec = (
                        it["metrics"].get("recovery", {}).get("summary", {}).get("meanRecovery_ms")
                    )
                    if mean_rec is not None:
                        rec_times.append(mean_rec)
                agg = {
                    "meanResilienceScore": sum(scores) / len(scores) if scores else 0,
                    "passRate": pass_count / len(iter_data) if iter_data else 0,
                    "totalExperiments": len(iter_data),
                }
                if rec_times:
                    agg["meanRecoveryTime_ms"] = sum(rec_times) / len(rec_times)
                    agg["maxRecoveryTime_ms"] = max(rec_times)
                    agg["medianRecoveryTime_ms"] = sorted(rec_times)[len(rec_times) // 2]
                strategies[strat] = {
                    "strategy": strat,
                    "status": "completed",
                    "experiment": agg,
                    "metrics": run_details[0].get("metrics", {}),
                    "iterations": iter_data,
                    "aggregated": agg,
                }

        return {
            "sessionId": session_id,
            "iterations": iterations,
            "strategies": strategies,
        }

    # ------------------------------------------------------------------
    # ML export from graph
    # ------------------------------------------------------------------

    def get_ml_samples(
        self,
        run_ids: Optional[List[str]] = None,
        strategy: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Export ML-ready rows from stored time-series data.

        Returns flattened sample dicts with anomaly labels joined from
        the graph.  Each row has metric values + anomaly_label column.

        Parameters
        ----------
        run_ids:
            Specific run IDs to export. If ``None``, exports all.
        strategy:
            Filter by placement strategy name.

        Returns
        -------
        List of dicts suitable for DataFrame construction or CSV export.
        """
        where_clauses = []
        params: Dict[str, Any] = {}

        if run_ids:
            where_clauses.append("e.run_id IN $run_ids")
            params["run_ids"] = run_ids
        if strategy:
            where_clauses.append("e.strategy = $strategy")
            params["strategy"] = strategy

        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Fetch samples with their anomaly labels joined via the parent ChaosRun
        query = (
            "MATCH (e:ChaosRun)-[:HAS_SAMPLE]->(s:MetricsSample) "
            f"{where} "
            "OPTIONAL MATCH (e)-[:HAS_ANOMALY_LABEL]->(a:AnomalyLabel) "
            "RETURN s.run_id AS run_id, "
            "       s.timestamp AS timestamp, "
            "       s.phase AS phase, "
            "       s.strategy AS strategy, "
            "       s.data AS data, "
            "       e.resilience_score AS resilience_score, "
            "       e.verdict AS verdict, "
            "       a.fault_type AS fault_type, "
            "       a.start_time AS anomaly_start, "
            "       a.end_time AS anomaly_end "
            "ORDER BY s.run_id, s.seq"
        )

        rows: List[Dict[str, Any]] = []
        with self._driver.session() as session:
            result = session.run(query, **params)
            for record in result:
                data_str = record["data"]
                try:
                    row = json.loads(data_str) if data_str else {}
                except (json.JSONDecodeError, TypeError):
                    row = {}

                row["run_id"] = record["run_id"]
                row["resilience_score"] = record["resilience_score"]
                row["overall_verdict"] = record["verdict"]

                # Determine anomaly label based on timestamp vs window
                fault = record["fault_type"]
                ts = record["timestamp"] or ""
                a_start = record["anomaly_start"] or ""
                a_end = record["anomaly_end"] or ""

                if fault and a_start <= ts <= a_end:
                    row["anomaly_label"] = fault
                else:
                    row["anomaly_label"] = "none"

                # Remove internal fields
                row.pop("seq", None)
                rows.append(row)

        return rows
