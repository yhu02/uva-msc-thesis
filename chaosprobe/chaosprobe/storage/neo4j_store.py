"""Neo4j graph store for ChaosProbe topology and experiment data.

Stores Kubernetes topology (nodes, deployments, services, pods),
service dependency graphs, placement decisions, and experiment results
in a Neo4j graph database for topology-aware analysis.

This is a standalone store (not inheriting ``ResultStore``) because
the graph query model is fundamentally different from the tabular
SQLite store.

The ``neo4j`` driver is imported lazily so the rest of ChaosProbe
works without it installed.
"""

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
            ("node_name", "Node", "name"),
            ("deployment_name", "Deployment", "name"),
            ("service_name", "Service", "name"),
            ("pod_name", "Pod", "name"),
            ("experiment_run_id", "ChaosExperiment", "run_id"),
            ("strategy_name", "PlacementStrategy", "name"),
        ]
        with self._driver.session() as session:
            for cname, label, prop in constraints:
                session.run(
                    f"CREATE CONSTRAINT {cname} IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
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
                    "MERGE (n:Node {name: $name}) "
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
        """Import a single experiment run into the graph.

        Creates/updates:
        - ``ChaosExperiment`` node
        - ``PlacementStrategy`` node + ``USED_STRATEGY`` edge
        - ``TARGETED_BY`` edges from target deployments
        - ``SCHEDULED_ON`` edges from placement assignments
        """
        run_id = run_data.get("runId", "unknown")
        timestamp = run_data.get("timestamp", "")
        summary = run_data.get("summary", {})
        verdict = summary.get("overallVerdict", "UNKNOWN")
        score = summary.get("resilienceScore", 0)

        # Infer strategy from scenario or explicit placement data
        strategy_name = "baseline"
        seed: Optional[int] = None
        placement = run_data.get("placement", {})
        if placement:
            strategy_name = placement.get("strategy", strategy_name)
            seed = placement.get("seed")
        else:
            # Try to infer from scenario metadata
            scenario = run_data.get("scenario", {})
            meta = scenario.get("metadata", {})
            strategy_name = meta.get("strategy", strategy_name)

        # Recovery metrics
        metrics = run_data.get("metrics", {})
        recovery = metrics.get("recovery", {}).get("summary", {})
        mean_recovery = recovery.get("meanRecovery_ms")
        max_recovery = recovery.get("maxRecovery_ms")

        with self._driver.session() as session:
            # Experiment node
            session.run(
                "MERGE (e:ChaosExperiment {run_id: $rid}) "
                "SET e.timestamp = $ts, e.verdict = $verdict, "
                "    e.resilience_score = $score, "
                "    e.strategy = $strat, "
                "    e.mean_recovery_ms = $mean_rec, "
                "    e.max_recovery_ms = $max_rec",
                rid=run_id,
                ts=timestamp,
                verdict=verdict,
                score=score,
                strat=strategy_name,
                mean_rec=mean_recovery,
                max_rec=max_recovery,
            )

            # Strategy node + link
            params: Dict[str, Any] = {"strat": strategy_name, "rid": run_id}
            if seed is not None:
                params["seed"] = seed
            session.run(
                "MERGE (s:PlacementStrategy {name: $strat}) "
                + ("SET s.seed = $seed " if seed is not None else "")
                + "WITH s "
                  "MATCH (e:ChaosExperiment {run_id: $rid}) "
                  "MERGE (e)-[:USED_STRATEGY]->(s)",
                **params,
            )

            # TARGETED_BY edges
            for exp in run_data.get("experiments", []):
                spec = exp.get("spec", {}).get("spec", {})
                appinfo = spec.get("appinfo", {})
                applabel = appinfo.get("applabel", "")
                if applabel.startswith("app="):
                    dep_name = applabel.split("=", 1)[1]
                    session.run(
                        "MATCH (d:Deployment {name: $dep}), "
                        "      (e:ChaosExperiment {run_id: $rid}) "
                        "MERGE (d)-[:TARGETED_BY]->(e)",
                        dep=dep_name,
                        rid=run_id,
                    )

            # SCHEDULED_ON edges from placement assignments
            assignments = placement.get("assignments", {})
            for dep_name, node_name in assignments.items():
                session.run(
                    "MATCH (d:Deployment {name: $dep}), (n:Node {name: $node}) "
                    "MERGE (d)-[r:SCHEDULED_ON {run_id: $rid}]->(n) "
                    "SET r.strategy = $strat",
                    dep=dep_name,
                    node=node_name,
                    rid=run_id,
                    strat=strategy_name,
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
                "MATCH (d:Deployment)-[:SCHEDULED_ON {run_id: $rid}]->(n:Node) "
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
            "MATCH (e:ChaosExperiment)-[:USED_STRATEGY]->(s:PlacementStrategy) "
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
            # Deployments with node assignments
            scheduled = session.run(
                "MATCH (d:Deployment)-[:SCHEDULED_ON {run_id: $rid}]->(n:Node) "
                "RETURN n.name AS node, collect(d.name) AS deployments "
                "ORDER BY n.name",
                rid=run_id,
            )
            nodes = [dict(r) for r in scheduled]

            # Deployments without assignments for this run
            unscheduled = session.run(
                "MATCH (d:Deployment) "
                "WHERE NOT (d)-[:SCHEDULED_ON {run_id: $rid}]->(:Node) "
                "RETURN d.name AS name ORDER BY d.name",
                rid=run_id,
            )
            return {
                "nodes": nodes,
                "unscheduled": [r["name"] for r in unscheduled],
            }

    def status(self) -> Dict[str, Any]:
        """Return node/relationship counts for a quick health check."""
        with self._driver.session() as session:
            counts = {}
            for label in ("Node", "Deployment", "Service", "Pod",
                          "ChaosExperiment", "PlacementStrategy"):
                result = session.run(
                    f"MATCH (n:{label}) RETURN count(n) AS c"
                )
                counts[label] = result.single()["c"]
            return counts
