"""Neo4j read/query operations for ChaosProbe.

Mixin class containing all methods that read data from Neo4j:
graph queries, run detail retrieval, session helpers, time-series
reconstruction, and ML export.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Neo4jReaderMixin:
    """Methods that query/read data from the Neo4j graph."""

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
                "CALL () { "
                "  MATCH (e:ChaosRun {run_id: $rid})"
                "  -[:HAS_POD_SNAPSHOT]->(p:PodSnapshot)"
                "  -[:HAS_CONTAINER_LOG]->(l:ContainerLog) "
                "  RETURN l "
                "  UNION "
                "  MATCH (e:ChaosRun {run_id: $rid})"
                "  -[:HAS_CONTAINER_LOG]->(l:ContainerLog) "
                "  RETURN l "
                "} "
                "RETURN DISTINCT properties(l) AS props",
                rid=run_id,
            )
            container_logs = [dict(r["props"]) for r in logs_result]

            # Probe results (via ExperimentResult)
            probes_result = session.run(
                "MATCH (e:ChaosRun {run_id: $rid})"
                "-[:HAS_RESULT]->(r:ExperimentResult)"
                "-[:HAS_PROBE]->(p:ProbeResult) "
                "RETURN r.experiment_name AS experiment_name, "
                "       properties(p) AS props "
                "ORDER BY r.experiment_name, p.probe_name",
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
                    "name": pr.get("probe_name", pr.get("name", "")),
                    "type": pr.get("type", ""),
                    "mode": pr.get("mode", ""),
                    "status": {
                        "verdict": pr.get("verdict", ""),
                        "description": pr.get("description", ""),
                    },
                }
                for pr in details.get("probeResults", [])
                if pr.get("experiment") == er.get("experiment_name", er.get("name", ""))
            ]
            experiments.append(
                {
                    "name": er.get("experiment_name", er.get("name", "")),
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
        placement = {"strategy": exp.get("strategy", "default")}

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
            strat = r.get("strategy", "default")
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
