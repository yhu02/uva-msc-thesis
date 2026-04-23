"""Correlated time-series alignment for ML feature extraction.

Merges independent metric streams (latency, resources, throughput,
Prometheus, recovery events) into a single aligned time-series with a
common sampling interval.  Each row is a candidate ML training sample
with columns for every metric and the ground-truth anomaly label.
"""

import csv
import io
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _parse_iso(ts: str) -> float:
    """Parse ISO-8601 timestamp to Unix epoch seconds."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).timestamp()


def _bucket(epoch: float, resolution_s: float) -> float:
    """Round *epoch* down to the nearest *resolution_s* bucket."""
    return math.floor(epoch / resolution_s) * resolution_s


# -------------------------------------------------------------------
# Merge helpers — each populates *buckets* from one metric stream
# -------------------------------------------------------------------

_BucketMap = Dict[float, Dict[str, Any]]
_NearestFn = Any  # Callable[[float], float]


def _assign_phases(
    buckets: _BucketMap,
    bucket_keys: List[float],
    anomaly_labels: Optional[List[Dict[str, Any]]],
) -> None:
    anomaly_windows: List[Tuple[float, float, str]] = []
    if anomaly_labels:
        for lbl in anomaly_labels:
            if lbl.get("startTime") and lbl.get("endTime"):
                anomaly_windows.append(
                    (
                        _parse_iso(lbl["startTime"]),
                        _parse_iso(lbl["endTime"]),
                        lbl.get("faultType", "unknown"),
                    )
                )
    for bk in bucket_keys:
        phase = "pre-chaos"
        anomaly_type = "none"
        for a_start, a_end, a_type in anomaly_windows:
            if a_start <= bk <= a_end:
                phase = "during-chaos"
                anomaly_type = a_type
                break
            elif bk > a_end:
                phase = "post-chaos"
        buckets[bk]["phase"] = phase
        buckets[bk]["anomaly_label"] = anomaly_type


def _merge_latency(
    metrics: Dict[str, Any], buckets: _BucketMap, nearest: _NearestFn
) -> None:
    for entry in metrics.get("latency", {}).get("timeSeries", []):
        ts = entry.get("timestamp")
        if ts is None:
            continue
        bk = nearest(_parse_iso(ts))
        if bk not in buckets:
            continue
        for route, data in entry.get("routes", {}).items():
            val = data.get("latency_ms")
            if val is not None:
                buckets[bk][f"latency:{route}:ms"] = val
            buckets[bk][f"latency:{route}:error"] = 1 if data.get("status") != "ok" else 0


def _merge_resources(
    metrics: Dict[str, Any], buckets: _BucketMap, nearest: _NearestFn
) -> None:
    resources = metrics.get("resources", {})
    if not resources.get("available"):
        return
    for entry in resources.get("timeSeries", []):
        ts = entry.get("timestamp")
        if ts is None:
            continue
        bk = nearest(_parse_iso(ts))
        if bk not in buckets:
            continue
        node = entry.get("usedNode", {})
        buckets[bk]["node_cpu_millicores"] = node.get("cpu_millicores")
        buckets[bk]["node_cpu_percent"] = node.get("cpu_percent")
        buckets[bk]["node_memory_bytes"] = node.get("memory_bytes")
        buckets[bk]["node_memory_percent"] = node.get("memory_percent")
        agg = entry.get("podAggregate", {})
        buckets[bk]["pod_total_cpu_millicores"] = agg.get("totalCpu_millicores")
        buckets[bk]["pod_total_memory_bytes"] = agg.get("totalMemory_bytes")
        buckets[bk]["pod_count"] = agg.get("podCount")


def _merge_redis(
    metrics: Dict[str, Any], buckets: _BucketMap, nearest: _NearestFn
) -> None:
    for entry in metrics.get("redis", {}).get("timeSeries", []):
        ts = entry.get("timestamp")
        if ts is None:
            continue
        bk = nearest(_parse_iso(ts))
        if bk not in buckets:
            continue
        for op, data in entry.get("redis", {}).items():
            buckets[bk][f"redis:{op}:ops_per_s"] = data.get("ops_per_second")
            buckets[bk][f"redis:{op}:latency_ms"] = data.get("latency_ms")


def _merge_disk(
    metrics: Dict[str, Any], buckets: _BucketMap, nearest: _NearestFn
) -> None:
    for entry in metrics.get("disk", {}).get("timeSeries", []):
        ts = entry.get("timestamp")
        if ts is None:
            continue
        bk = nearest(_parse_iso(ts))
        if bk not in buckets:
            continue
        for op, data in entry.get("disk", {}).items():
            buckets[bk][f"disk:{op}:ops_per_s"] = data.get("ops_per_second")
            buckets[bk][f"disk:{op}:bytes_per_s"] = data.get("bytes_per_second")


def _merge_prometheus(
    metrics: Dict[str, Any], buckets: _BucketMap, nearest: _NearestFn
) -> None:
    prometheus = metrics.get("prometheus", {})
    if not prometheus.get("available"):
        return
    for entry in prometheus.get("timeSeries", []):
        ts = entry.get("timestamp")
        if ts is None:
            continue
        bk = nearest(_parse_iso(ts))
        if bk not in buckets:
            continue
        for metric_name, values in entry.get("metrics", {}).items():
            total = 0.0
            count = 0
            for v in values:
                try:
                    total += float(v.get("value", [0, "0"])[1])
                    count += 1
                except (ValueError, IndexError, TypeError):
                    pass
            if count > 0:
                buckets[bk][f"prom:{metric_name}:sum"] = round(total, 4)
                buckets[bk][f"prom:{metric_name}:avg"] = round(total / count, 4)


def _merge_recovery(
    metrics: Dict[str, Any], buckets: _BucketMap, bucket_keys: List[float]
) -> None:
    for cycle in metrics.get("recovery", {}).get("recoveryEvents", []):
        del_time = cycle.get("deletionTime")
        ready_time = cycle.get("readyTime")
        if del_time and ready_time:
            del_epoch = _parse_iso(del_time)
            ready_epoch = _parse_iso(ready_time)
            for bk in bucket_keys:
                if del_epoch <= bk <= ready_epoch:
                    buckets[bk]["recovery_in_progress"] = 1
                    buckets[bk]["recovery_total_ms"] = cycle.get("totalRecovery_ms")


def _merge_events(
    metrics: Dict[str, Any], buckets: _BucketMap, nearest: _NearestFn
) -> None:
    for event in metrics.get("eventTimeline", []):
        ts = event.get("time")
        if ts is None:
            continue
        bk = nearest(_parse_iso(ts))
        if bk not in buckets:
            continue
        etype = event.get("type", "")
        key = f"events:{etype.lower()}_count"
        buckets[bk][key] = buckets[bk].get(key, 0) + 1


def align_time_series(
    metrics: Dict[str, Any],
    anomaly_labels: Optional[List[Dict[str, Any]]] = None,
    resolution_s: float = 5.0,
    strategy: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Align multi-source metric streams into a unified time-series.

    Parameters
    ----------
    metrics:
        The ``metrics`` dict from an experiment run output (as written by
        ``MetricsCollector.collect``).  May contain keys: ``latency``,
        ``resources``, ``redis``, ``disk``, ``prometheus``, ``recovery``,
        ``eventTimeline``.
    anomaly_labels:
        List of anomaly label dicts (from ``generate_anomaly_labels``).
        Used to tag each time bucket with the fault type when it falls
        within an anomaly's ``[startTime, endTime]`` window.
    resolution_s:
        Bucket width in seconds.  All metric samples are bucketed into
        fixed intervals of this width.
    strategy:
        Placement strategy name (added as a static column for each row).

    Returns
    -------
    List of dicts, each representing one time bucket.  Suitable for direct
    conversion to a pandas DataFrame or CSV.
    """
    time_window = metrics.get("timeWindow", {})
    if not time_window.get("start") or not time_window.get("end"):
        return []

    window_start = _parse_iso(time_window["start"])
    window_end = _parse_iso(time_window["end"])

    # Build bucket grid
    buckets: Dict[float, Dict[str, Any]] = {}
    t = _bucket(window_start, resolution_s)
    while t <= window_end + resolution_s:
        buckets[t] = {
            "timestamp": datetime.fromtimestamp(t, tz=timezone.utc).isoformat(),
            "epoch_s": round(t, 1),
            "strategy": strategy,
        }
        t += resolution_s

    bucket_keys = sorted(buckets.keys())

    def _nearest_bucket(epoch: float) -> float:
        b = _bucket(epoch, resolution_s)
        return b if b in buckets else min(bucket_keys, key=lambda k: abs(k - epoch))

    # ── Determine phase per bucket ────────────────────────────
    _assign_phases(buckets, bucket_keys, anomaly_labels)

    # ── Merge metric streams into buckets ─────────────────────
    _merge_latency(metrics, buckets, _nearest_bucket)
    _merge_resources(metrics, buckets, _nearest_bucket)
    _merge_redis(metrics, buckets, _nearest_bucket)
    _merge_disk(metrics, buckets, _nearest_bucket)
    _merge_prometheus(metrics, buckets, _nearest_bucket)
    _merge_recovery(metrics, buckets, bucket_keys)
    _merge_events(metrics, buckets, _nearest_bucket)

    # ── Fill defaults and return sorted rows ──────────────────
    rows = [buckets[bk] for bk in bucket_keys]

    # Forward-fill: carry the last observed value into empty buckets
    if rows:
        fill_cols = set()
        for row in rows:
            fill_cols.update(
                k
                for k in row
                if k
                not in (
                    "timestamp",
                    "epoch_s",
                    "phase",
                    "anomaly_label",
                    "strategy",
                )
            )
        last_values: Dict[str, Any] = {}
        for row in rows:
            for col in fill_cols:
                if col in row and row[col] is not None:
                    last_values[col] = row[col]
                elif col in last_values:
                    row[col] = last_values[col]

    # Set recovery_in_progress default and event counts default
    for row in rows:
        row.setdefault("recovery_in_progress", 0)

    return rows


def export_aligned_csv(
    rows: List[Dict[str, Any]],
    output_path: Optional[str] = None,
) -> str:
    """Write aligned time-series rows to CSV.

    Parameters
    ----------
    rows:
        Output from ``align_time_series``.
    output_path:
        File path to write. If ``None``, returns the CSV as a string.

    Returns
    -------
    The CSV content as a string (always), and writes to *output_path* if given.
    """
    if not rows:
        return ""

    # Collect all unique columns across all rows
    all_cols = list(dict.fromkeys(col for row in rows for col in row))

    # Pin important columns to the front
    priority = [
        "timestamp",
        "epoch_s",
        "phase",
        "strategy",
        "anomaly_label",
    ]
    ordered = [c for c in priority if c in all_cols]
    ordered += [c for c in all_cols if c not in ordered]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=ordered, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    csv_text = buf.getvalue()

    if output_path:
        with open(output_path, "w", newline="") as f:
            f.write(csv_text)

    return csv_text
