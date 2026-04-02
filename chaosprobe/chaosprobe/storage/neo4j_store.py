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
        store.sync_service_dependencies()
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
        ]
        with self._driver.session() as session:
            for cname, label, prop in constraints:
                session.run(
                    f"CREATE CONSTRAINT {cname} IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
                )
            for iname, label, prop in indexes:
                session.run(
                    f"CREATE INDEX {iname} IF NOT EXISTS "
                    f"FOR (n:{label}) ON (n.{prop})"
                )

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

    def sync_service_dependencies(self) -> None:
        """Populate Service nodes and DEPENDS_ON edges from the Online Boutique
        dependency graph defined in ``metrics/latency.py``.
        """
        from chaosprobe.metrics.latency import ONLINE_BOUTIQUE_ROUTES

        with self._driver.session() as session:
            for src, tgt, host, protocol, description in ONLINE_BOUTIQUE_ROUTES:
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
        - Load generation stats on the experiment node
        """
        run_id = run_data.get("runId", "unknown")
        timestamp = run_data.get("timestamp", "")
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
                    "    e.load_p99_response_ms = $load_p99, "
                    "    e.load_rps = $load_rps, "
                    "    e.event_timeline = $timeline",
                    rid=run_id,
                    ts=timestamp,
                    verdict=verdict,
                    score=score,
                    strat=strategy_name,
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
                    load_p95=load_gen.get("stats", {}).get("p95ResponseTime_ms"),
                    load_p99=load_gen.get("stats", {}).get("p99ResponseTime_ms"),
                    load_rps=load_gen.get("stats", {}).get("requestsPerSecond"),
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
                            applabel, run_id,
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

                tx.commit()

    # ------------------------------------------------------------------
    # sync_run helpers
    # ------------------------------------------------------------------

    def _sync_recovery_cycles(
        self, tx: Any, run_id: str, recovery: Dict[str, Any],
    ) -> None:
        """Store individual recovery cycle events."""
        # Clear old cycles for this run
        tx.run(
            "MATCH (e:ChaosRun {run_id: $rid})-[:HAS_RECOVERY_CYCLE]->(c:RecoveryCycle) "
            "DETACH DELETE c",
            rid=run_id,
        )
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
        self, tx: Any, run_id: str, experiments: List[Dict[str, Any]],
    ) -> None:
        """Store per-chaos-experiment result nodes."""
        tx.run(
            "MATCH (e:ChaosRun {run_id: $rid})-[:HAS_RESULT]->(r:ExperimentResult) "
            "DETACH DELETE r",
            rid=run_id,
        )
        for exp in experiments:
            result = exp.get("result", exp.get("chaosResult", {}))
            probes = exp.get("probes", [])
            tx.run(
                "MATCH (e:ChaosRun {run_id: $rid}) "
                "CREATE (r:ExperimentResult {"
                "  run_id: $rid, name: $name, engine_name: $engine, "
                "  verdict: $verdict, "
                "  probe_success_pct: $probe_pct, "
                "  phase: $phase, fail_step: $fail_step, "
                "  probes: $probes"
                "}) "
                "CREATE (e)-[:HAS_RESULT]->(r)",
                rid=run_id,
                name=exp.get("name", ""),
                engine=exp.get("engineName", ""),
                verdict=result.get("verdict", "Unknown"),
                probe_pct=exp.get("probeSuccessPercentage",
                                  result.get("probeSuccessPercentage", 0)),
                phase=result.get("phase", ""),
                fail_step=result.get("failStep", ""),
                probes=json.dumps(probes),
            )

    def _sync_metrics_phases(
        self, tx: Any, run_id: str, metrics: Dict[str, Any],
    ) -> None:
        """Store per-phase metric summaries for all metric types."""
        # Clear old phase nodes for this run
        tx.run(
            "MATCH (e:ChaosRun {run_id: $rid})-[:HAS_METRICS_PHASE]->(m:MetricsPhase) "
            "DETACH DELETE m",
            rid=run_id,
        )

        phase_names = ["pre-chaos", "during-chaos", "post-chaos"]

        # ── Latency ───────────────────────────────────────────
        latency = metrics.get("latency", {})
        latency_phases = latency.get("phases", {})
        for phase in phase_names:
            phase_data = latency_phases.get(phase, {})
            if not phase_data or phase_data.get("sampleCount", 0) == 0:
                continue
            tx.run(
                "MATCH (e:ChaosRun {run_id: $rid}) "
                "CREATE (m:MetricsPhase {"
                "  run_id: $rid, metric_type: 'latency', phase: $phase, "
                "  sample_count: $samples, "
                "  routes: $routes"
                "}) "
                "CREATE (e)-[:HAS_METRICS_PHASE]->(m)",
                rid=run_id,
                phase=phase,
                samples=phase_data.get("sampleCount", 0),
                routes=json.dumps(phase_data.get("routes", {})),
            )

        # ── Resources ─────────────────────────────────────────
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

        # ── Prometheus ────────────────────────────────────────
        prometheus = metrics.get("prometheus", {})
        if prometheus.get("available", False):
            prom_phases = prometheus.get("phases", {})
            for phase in phase_names:
                phase_data = prom_phases.get(phase, {})
                if not phase_data or phase_data.get("sampleCount", 0) == 0:
                    continue
                tx.run(
                    "MATCH (e:ChaosRun {run_id: $rid}) "
                    "CREATE (m:MetricsPhase {"
                    "  run_id: $rid, metric_type: 'prometheus', phase: $phase, "
                    "  sample_count: $samples, "
                    "  metrics_json: $metrics_json"
                    "}) "
                    "CREATE (e)-[:HAS_METRICS_PHASE]->(m)",
                    rid=run_id,
                    phase=phase,
                    samples=phase_data.get("sampleCount", 0),
                    metrics_json=json.dumps(phase_data.get("metrics", {})),
                )

        # ── Redis ─────────────────────────────────────────────
        redis = metrics.get("redis", {})
        redis_phases = redis.get("phases", {})
        for phase in phase_names:
            phase_data = redis_phases.get(phase, {})
            if not phase_data or phase_data.get("sampleCount", 0) == 0:
                continue
            tx.run(
                "MATCH (e:ChaosRun {run_id: $rid}) "
                "CREATE (m:MetricsPhase {"
                "  run_id: $rid, metric_type: 'redis', phase: $phase, "
                "  sample_count: $samples, "
                "  operations: $operations"
                "}) "
                "CREATE (e)-[:HAS_METRICS_PHASE]->(m)",
                rid=run_id,
                phase=phase,
                samples=phase_data.get("sampleCount", 0),
                operations=json.dumps(phase_data.get("redis", {})),
            )

        # ── Disk ──────────────────────────────────────────────
        disk = metrics.get("disk", {})
        disk_phases = disk.get("phases", {})
        for phase in phase_names:
            phase_data = disk_phases.get(phase, {})
            if not phase_data or phase_data.get("sampleCount", 0) == 0:
                continue
            tx.run(
                "MATCH (e:ChaosRun {run_id: $rid}) "
                "CREATE (m:MetricsPhase {"
                "  run_id: $rid, metric_type: 'disk', phase: $phase, "
                "  sample_count: $samples, "
                "  operations: $operations"
                "}) "
                "CREATE (e)-[:HAS_METRICS_PHASE]->(m)",
                rid=run_id,
                phase=phase,
                samples=phase_data.get("sampleCount", 0),
                operations=json.dumps(phase_data.get("disk", {})),
            )

    def _sync_pod_snapshots(
        self, tx: Any, run_id: str, pod_status: Dict[str, Any],
    ) -> None:
        """Store pod status snapshots."""
        tx.run(
            "MATCH (e:ChaosRun {run_id: $rid})-[:HAS_POD_SNAPSHOT]->(p:PodSnapshot) "
            "DETACH DELETE p",
            rid=run_id,
        )
        for pod in pod_status.get("pods", []):
            tx.run(
                "MATCH (e:ChaosRun {run_id: $rid}) "
                "CREATE (p:PodSnapshot {"
                "  run_id: $rid, name: $name, phase: $phase, "
                "  node: $node, restart_count: $restarts, "
                "  conditions: $conditions"
                "}) "
                "CREATE (e)-[:HAS_POD_SNAPSHOT]->(p)",
                rid=run_id,
                name=pod.get("name", ""),
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
                    pname=pod.get("name", ""),
                    node=node_name,
                )

    # ------------------------------------------------------------------
    # Graph queries
    # ------------------------------------------------------------------

    def get_blast_radius(
        self, service: str, max_hops: int = 3,
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
        self, run_id: str,
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
        self, run_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Compare experiment results across strategies.

        Returns a list of ``{"strategy": str, "run_id": str,
        "resilience_score": float, "mean_recovery_ms": float|None}``
        dicts.
        """
        query = (
            "MATCH (e:ChaosRun)-[:USED_STRATEGY]->(s:PlacementStrategy) "
        )
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
                "MATCH (e:ChaosRun {run_id: $rid}) "
                "RETURN properties(e) AS props",
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

            return {
                "experiment": experiment,
                "recoveryCycles": recovery_cycles,
                "experimentResults": experiment_results,
                "metricsPhases": metrics_phases,
                "podSnapshots": pod_snapshots,
            }

    def get_run_metrics(
        self, run_id: str, metric_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return metrics phase summaries for a run.

        Args:
            run_id: The experiment run ID.
            metric_type: Optional filter (latency, resources, prometheus,
                         redis, disk). If None, returns all types.
        """
        query = (
            "MATCH (e:ChaosRun {run_id: $rid})"
            "-[:HAS_METRICS_PHASE]->(m:MetricsPhase) "
        )
        params: Dict[str, Any] = {"rid": run_id}
        if metric_type:
            query += "WHERE m.metric_type = $mtype "
            params["mtype"] = metric_type
        query += "RETURN properties(m) AS props ORDER BY m.metric_type, m.phase"

        with self._driver.session() as session:
            result = session.run(query, **params)
            return [dict(r["props"]) for r in result]

    def get_recovery_cycles(self, run_id: str) -> List[Dict[str, Any]]:
        """Return all recovery cycles for a run, ordered by sequence."""
        with self._driver.session() as session:
            result = session.run(
                "MATCH (e:ChaosRun {run_id: $rid})"
                "-[:HAS_RECOVERY_CYCLE]->(c:RecoveryCycle) "
                "RETURN properties(c) AS props ORDER BY c.seq",
                rid=run_id,
            )
            return [dict(r["props"]) for r in result]

    def status(self) -> Dict[str, Any]:
        """Return node/relationship counts for a quick health check."""
        with self._driver.session() as session:
            counts = {}
            for label in ("K8sNode", "Deployment", "Service",
                          "ChaosRun", "PlacementStrategy",
                          "RecoveryCycle", "ExperimentResult",
                          "MetricsPhase", "PodSnapshot"):
                result = session.run(
                    f"MATCH (n:{label}) RETURN count(n) AS c"
                )
                counts[label] = result.single()["c"]
            return counts
