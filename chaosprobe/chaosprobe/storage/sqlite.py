"""SQLite storage backend for ChaosProbe results."""

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from chaosprobe.storage.base import ResultStore

DEFAULT_DB_PATH = Path.home() / ".chaosprobe" / "results.db"


class SQLiteStore(ResultStore):
    """SQLite-based storage for experiment results.

    Zero-config, file-based storage suitable for single-user thesis work.
    Stores runs, metrics, and pod placements in normalized tables.
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                scenario TEXT,
                strategy TEXT,
                namespace TEXT,
                cluster_config TEXT,
                overall_verdict TEXT,
                resilience_score REAL,
                total_experiments INTEGER,
                passed INTEGER,
                failed INTEGER,
                raw_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                metric_name TEXT NOT NULL,
                metric_value REAL,
                metric_unit TEXT,
                timestamp TEXT
            );

            CREATE TABLE IF NOT EXISTS pod_placements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                pod_name TEXT NOT NULL,
                node_name TEXT NOT NULL,
                deployment TEXT
            );

            CREATE TABLE IF NOT EXISTS load_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                profile TEXT,
                total_requests INTEGER,
                total_failures INTEGER,
                avg_response_time_ms REAL,
                p50_response_time_ms REAL,
                p95_response_time_ms REAL,
                p99_response_time_ms REAL,
                requests_per_second REAL,
                error_rate REAL,
                duration_seconds REAL
            );

            CREATE INDEX IF NOT EXISTS idx_runs_scenario ON runs(scenario);
            CREATE INDEX IF NOT EXISTS idx_runs_strategy ON runs(strategy);
            CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs(timestamp);
            CREATE INDEX IF NOT EXISTS idx_metrics_run_id ON metrics(run_id);
            CREATE INDEX IF NOT EXISTS idx_pod_placements_run_id ON pod_placements(run_id);
            CREATE INDEX IF NOT EXISTS idx_load_stats_run_id ON load_stats(run_id);
        """)
        conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def save_run(self, run_data: Dict[str, Any]) -> str:
        conn = self._get_conn()
        run_id = run_data.get("runId", "")
        summary = run_data.get("summary", {})
        scenario = run_data.get("scenario", {})
        infrastructure = run_data.get("infrastructure", {})

        # Extract strategy from placement metadata if available
        strategy = None
        placement = run_data.get("placement")
        if placement:
            strategy = placement.get("strategy")

        cluster_config = run_data.get("cluster")

        conn.execute(
            """INSERT OR REPLACE INTO runs
               (id, timestamp, scenario, strategy, namespace, cluster_config,
                overall_verdict, resilience_score, total_experiments, passed, failed, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                run_data.get("timestamp", ""),
                scenario.get("directory", ""),
                strategy,
                infrastructure.get("namespace", ""),
                json.dumps(cluster_config) if cluster_config else None,
                summary.get("overallVerdict", ""),
                summary.get("resilienceScore", 0),
                summary.get("totalExperiments", 0),
                summary.get("passed", 0),
                summary.get("failed", 0),
                json.dumps(run_data),
            ),
        )

        # Save metrics
        self._save_metrics(conn, run_id, run_data)

        # Save pod placements
        self._save_placements(conn, run_id, run_data)

        # Save load generation stats
        self._save_load_stats(conn, run_id, run_data)

        conn.commit()
        return run_id

    def _save_metrics(
        self, conn: sqlite3.Connection, run_id: str, run_data: Dict[str, Any]
    ) -> None:
        # Delete old metrics for this run (in case of re-save)
        conn.execute("DELETE FROM metrics WHERE run_id = ?", (run_id,))

        metrics = run_data.get("metrics", {})
        recovery = metrics.get("recovery", {})
        summary = recovery.get("summary", {})

        metric_rows = []
        for key in (
            "meanRecovery_ms",
            "medianRecovery_ms",
            "minRecovery_ms",
            "maxRecovery_ms",
            "p95Recovery_ms",
        ):
            val = summary.get(key)
            if val is not None:
                metric_rows.append((run_id, key, val, "ms", run_data.get("timestamp")))

        # Resilience score as a metric too
        rs = run_data.get("summary", {}).get("resilienceScore")
        if rs is not None:
            metric_rows.append(
                (run_id, "resilienceScore", rs, "percent", run_data.get("timestamp"))
            )

        if metric_rows:
            conn.executemany(
                "INSERT INTO metrics (run_id, metric_name, metric_value, metric_unit, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                metric_rows,
            )

    def _save_placements(
        self, conn: sqlite3.Connection, run_id: str, run_data: Dict[str, Any]
    ) -> None:
        conn.execute("DELETE FROM pod_placements WHERE run_id = ?", (run_id,))

        placement = run_data.get("placement", {})
        assignments = placement.get("assignments", {})

        rows = []
        for pod_name, node_name in assignments.items():
            deployment = pod_name.rsplit("-", 2)[0] if "-" in pod_name else pod_name
            rows.append((run_id, pod_name, node_name, deployment))

        if rows:
            conn.executemany(
                "INSERT INTO pod_placements (run_id, pod_name, node_name, deployment) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )

    def _save_load_stats(
        self, conn: sqlite3.Connection, run_id: str, run_data: Dict[str, Any]
    ) -> None:
        conn.execute("DELETE FROM load_stats WHERE run_id = ?", (run_id,))

        load_gen = run_data.get("loadGeneration", {})
        stats = load_gen.get("stats", {})
        if not stats:
            return

        conn.execute(
            """INSERT INTO load_stats
               (run_id, profile, total_requests, total_failures,
                avg_response_time_ms, p50_response_time_ms, p95_response_time_ms,
                p99_response_time_ms, requests_per_second, error_rate, duration_seconds)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                load_gen.get("profile"),
                stats.get("totalRequests", 0),
                stats.get("totalFailures", 0),
                stats.get("avgResponseTime_ms", 0),
                stats.get("p50ResponseTime_ms", 0),
                stats.get("p95ResponseTime_ms", 0),
                stats.get("p99ResponseTime_ms", 0),
                stats.get("requestsPerSecond", 0),
                stats.get("errorRate", 0),
                stats.get("duration_seconds", 0),
            ),
        )

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        row = conn.execute("SELECT raw_json FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row:
            return json.loads(row["raw_json"])
        return None

    def list_runs(
        self,
        scenario: Optional[str] = None,
        strategy: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        query = "SELECT id, timestamp, scenario, strategy, namespace, overall_verdict, resilience_score FROM runs WHERE 1=1"
        params: list = []

        if scenario:
            query += " AND scenario LIKE ?"
            params.append(f"%{scenario}%")
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_metrics(
        self,
        run_id: str,
        metric_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        if metric_name:
            rows = conn.execute(
                "SELECT * FROM metrics WHERE run_id = ? AND metric_name = ?",
                (run_id, metric_name),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM metrics WHERE run_id = ?", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def compare_strategies(
        self,
        scenario: Optional[str] = None,
        limit_per_strategy: int = 10,
    ) -> Dict[str, Any]:
        conn = self._get_conn()

        where_clause = ""
        params: list = []
        if scenario:
            where_clause = "WHERE r.scenario LIKE ?"
            params.append(f"%{scenario}%")

        # Get aggregate stats per strategy
        query = f"""
            SELECT
                r.strategy,
                COUNT(*) as run_count,
                AVG(r.resilience_score) as avg_resilience,
                MIN(r.resilience_score) as min_resilience,
                MAX(r.resilience_score) as max_resilience,
                SUM(CASE WHEN r.overall_verdict = 'PASS' THEN 1 ELSE 0 END) as pass_count,
                AVG(m_mean.metric_value) as avg_mean_recovery_ms,
                AVG(m_p95.metric_value) as avg_p95_recovery_ms,
                AVG(ls.p95_response_time_ms) as avg_load_p95_ms,
                AVG(ls.error_rate) as avg_load_error_rate
            FROM runs r
            LEFT JOIN metrics m_mean ON r.id = m_mean.run_id AND m_mean.metric_name = 'meanRecovery_ms'
            LEFT JOIN metrics m_p95 ON r.id = m_p95.run_id AND m_p95.metric_name = 'p95Recovery_ms'
            LEFT JOIN load_stats ls ON r.id = ls.run_id
            {where_clause}
            GROUP BY r.strategy
            ORDER BY avg_resilience DESC
        """

        rows = conn.execute(query, params).fetchall()

        strategies = {}
        for row in rows:
            row_dict = dict(row)
            strategy = row_dict.pop("strategy") or "unknown"
            run_count = row_dict["run_count"]
            strategies[strategy] = {
                "runCount": run_count,
                "avgResilienceScore": round(row_dict["avg_resilience"] or 0, 1),
                "minResilienceScore": round(row_dict["min_resilience"] or 0, 1),
                "maxResilienceScore": round(row_dict["max_resilience"] or 0, 1),
                "passRate": round((row_dict["pass_count"] or 0) / run_count, 2)
                if run_count
                else 0,
                "avgMeanRecovery_ms": round(row_dict["avg_mean_recovery_ms"], 1)
                if row_dict["avg_mean_recovery_ms"]
                else None,
                "avgP95Recovery_ms": round(row_dict["avg_p95_recovery_ms"], 1)
                if row_dict["avg_p95_recovery_ms"]
                else None,
                "avgLoadP95_ms": round(row_dict["avg_load_p95_ms"], 1)
                if row_dict["avg_load_p95_ms"]
                else None,
                "avgLoadErrorRate": round(row_dict["avg_load_error_rate"], 4)
                if row_dict["avg_load_error_rate"]
                else None,
            }

        return {"strategies": strategies}

    def export_csv(self, output_path: str) -> str:
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT
                r.id, r.timestamp, r.scenario, r.strategy, r.namespace,
                r.overall_verdict, r.resilience_score,
                r.total_experiments, r.passed, r.failed,
                m_mean.metric_value as mean_recovery_ms,
                m_p95.metric_value as p95_recovery_ms,
                ls.total_requests as load_requests,
                ls.p95_response_time_ms as load_p95_ms,
                ls.error_rate as load_error_rate
            FROM runs r
            LEFT JOIN metrics m_mean ON r.id = m_mean.run_id AND m_mean.metric_name = 'meanRecovery_ms'
            LEFT JOIN metrics m_p95 ON r.id = m_p95.run_id AND m_p95.metric_name = 'p95Recovery_ms'
            LEFT JOIN load_stats ls ON r.id = ls.run_id
            ORDER BY r.timestamp DESC
        """).fetchall()

        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "run_id", "timestamp", "scenario", "strategy", "namespace",
                "verdict", "resilience_score", "total_experiments", "passed", "failed",
                "mean_recovery_ms", "p95_recovery_ms",
                "load_requests", "load_p95_ms", "load_error_rate",
            ])
            for row in rows:
                writer.writerow(list(row))

        return output_path
