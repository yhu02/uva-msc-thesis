"""ML-ready dataset export pipeline.

Reads experiment results from Neo4j storage and produces aligned,
labeled feature matrices suitable for training anomaly classification
and remediation models.

Supports CSV output format with optional Parquet support (requires
``pyarrow``).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from chaosprobe.metrics.anomaly_labels import generate_anomaly_labels
from chaosprobe.metrics.timeseries import align_time_series, export_aligned_csv


def export_run_to_rows(
    run_data: Dict[str, Any],
    resolution_s: float = 5.0,
) -> List[Dict[str, Any]]:
    """Convert a single experiment run JSON into aligned time-series rows.

    Parameters
    ----------
    run_data:
        A single run output dict (as produced by ``OutputGenerator.generate``).
    resolution_s:
        Bucket width in seconds for time-series alignment.

    Returns
    -------
    List of row dicts, each representing one time bucket with all metrics
    and the anomaly label.
    """
    metrics = run_data.get("metrics")
    if not metrics:
        return []

    anomaly_labels = run_data.get("anomalyLabels")
    if anomaly_labels is None:
        anomaly_labels = generate_anomaly_labels(
            run_data.get("scenario", {}),
            metrics=metrics,
            placement=run_data.get("placement"),
        )

    strategy = None
    placement = run_data.get("placement", {})
    if placement:
        strategy = placement.get("strategy")

    rows = align_time_series(
        metrics,
        anomaly_labels=anomaly_labels,
        resolution_s=resolution_s,
        strategy=strategy,
    )

    # Add run-level metadata to each row
    run_id = run_data.get("runId", "")
    resilience_score = run_data.get("summary", {}).get("resilienceScore", 0)
    verdict = run_data.get("summary", {}).get("overallVerdict", "UNKNOWN")

    for row in rows:
        row["run_id"] = run_id
        row["resilience_score"] = resilience_score
        row["overall_verdict"] = verdict

    return rows


def export_from_neo4j(
    uri: str = "bolt://localhost:7687",
    user: str = "neo4j",
    password: str = "neo4j",
    run_ids: Optional[List[str]] = None,
    strategy: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Export ML-ready rows directly from Neo4j graph storage.

    Uses pre-aligned time-series samples stored as ``MetricsSample``
    nodes, joined with ``AnomalyLabel`` data via graph traversal.

    Parameters
    ----------
    uri:
        Neo4j bolt connection URI.
    user:
        Neo4j username.
    password:
        Neo4j password.
    run_ids:
        Optional list of run IDs to export.
    strategy:
        Optional strategy filter.

    Returns
    -------
    List of row dicts with metrics and anomaly labels.
    """
    from chaosprobe.storage.neo4j_store import Neo4jStore

    store = Neo4jStore(uri, user, password)
    try:
        rows = store.get_ml_samples(run_ids=run_ids, strategy=strategy)
    finally:
        store.close()
    return rows


def write_dataset(
    rows: List[Dict[str, Any]],
    output_path: str,
    format: str = "csv",
) -> str:
    """Write the aligned dataset to a file.

    Parameters
    ----------
    rows:
        Output from any of the ``export_*`` functions.
    output_path:
        Destination file path.
    format:
        Output format: ``csv`` or ``parquet``.

    Returns
    -------
    The absolute path to the written file.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if format == "parquet":
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            raise ImportError(
                "Parquet export requires 'pyarrow'.\n" "Install with:  uv pip install pyarrow"
            ) from None
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, str(path))
    else:
        export_aligned_csv(rows, output_path=str(path))

    return str(path.resolve())
