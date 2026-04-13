"""Neo4j graph store for ChaosProbe topology and experiment data.

Stores Kubernetes topology (nodes, deployments, services, pods),
service dependency graphs, placement decisions, experiment results,
and **all collected metrics** (recovery, latency, throughput, resources,
Prometheus) in a Neo4j graph database.

This is a standalone store (not inheriting ``ResultStore``) because
the graph query model is fundamentally different from a tabular store.

The ``neo4j`` driver is imported lazily so the rest of ChaosProbe
works without it installed.

The implementation is split across mixin modules for maintainability:
- ``neo4j_writer.py``: all write/sync operations
- ``neo4j_reader.py``: all read/query operations
"""

import logging

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
        ) from None


# Import mixins
from chaosprobe.storage.neo4j_reader import Neo4jReaderMixin  # noqa: E402
from chaosprobe.storage.neo4j_writer import Neo4jWriterMixin  # noqa: E402


class Neo4jStore(Neo4jWriterMixin, Neo4jReaderMixin):
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

