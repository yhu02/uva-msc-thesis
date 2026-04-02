"""Tests for correlated time-series alignment."""

import csv
import io

from chaosprobe.metrics.timeseries import align_time_series, export_aligned_csv


def _make_metrics(
    start="2026-04-02T01:35:00+00:00",
    end="2026-04-02T01:35:30+00:00",
    latency_entries=None,
    resource_entries=None,
    recovery_events=None,
    event_timeline=None,
):
    """Build a minimal metrics dict for testing."""
    m = {
        "timeWindow": {"start": start, "end": end, "duration_s": 30},
    }
    if latency_entries is not None:
        m["latency"] = {"timeSeries": latency_entries, "phases": {}}
    if resource_entries is not None:
        m["resources"] = {"available": True, "timeSeries": resource_entries, "phases": {}}
    if recovery_events is not None:
        m["recovery"] = {"recoveryEvents": recovery_events, "summary": {}}
    if event_timeline is not None:
        m["eventTimeline"] = event_timeline
    return m


class TestAlignTimeSeries:
    def test_empty_metrics(self):
        rows = align_time_series({})
        assert rows == []

    def test_basic_bucket_grid(self):
        metrics = _make_metrics()
        rows = align_time_series(metrics, resolution_s=5.0)
        # 30s window with 5s buckets → ~7 buckets (0,5,10,15,20,25,30)
        assert len(rows) >= 6
        for row in rows:
            assert "timestamp" in row
            assert "phase" in row
            assert "anomaly_label" in row

    def test_strategy_column(self):
        metrics = _make_metrics()
        rows = align_time_series(metrics, resolution_s=5.0, strategy="colocate")
        assert all(row["strategy"] == "colocate" for row in rows)

    def test_anomaly_label_during_chaos(self):
        metrics = _make_metrics()
        labels = [{
            "faultType": "pod-delete",
            "startTime": "2026-04-02T01:35:05+00:00",
            "endTime": "2026-04-02T01:35:25+00:00",
        }]
        rows = align_time_series(metrics, anomaly_labels=labels, resolution_s=5.0)

        phases = [row["phase"] for row in rows]
        anomalies = [row["anomaly_label"] for row in rows]
        assert "during-chaos" in phases
        assert "pod-delete" in anomalies
        # First bucket should be pre-chaos
        assert rows[0]["phase"] == "pre-chaos"
        assert rows[0]["anomaly_label"] == "none"

    def test_latency_data_merged(self):
        latency = [
            {
                "timestamp": "2026-04-02T01:35:05+00:00",
                "elapsed_s": 5.0,
                "phase": "during-chaos",
                "routes": {
                    "/": {"latency_ms": 45.3, "status": "ok", "error": None},
                    "/cart": {"latency_ms": None, "status": "error", "error": "timeout"},
                },
            }
        ]
        metrics = _make_metrics(latency_entries=latency)
        rows = align_time_series(metrics, resolution_s=5.0)

        # Find the bucket that got latency data
        lat_rows = [r for r in rows if "latency:/:ms" in r]
        assert len(lat_rows) >= 1
        assert lat_rows[0]["latency:/:ms"] == 45.3
        assert lat_rows[0]["latency:/:error"] == 0
        assert lat_rows[0]["latency:/cart:error"] == 1

    def test_resource_data_merged(self):
        resource = [
            {
                "timestamp": "2026-04-02T01:35:10+00:00",
                "phase": "during-chaos",
                "node": {
                    "cpu_millicores": 1200.5,
                    "cpu_percent": 30.0,
                    "memory_bytes": 3221225472,
                    "memory_percent": 37.5,
                },
                "podAggregate": {
                    "totalCpu_millicores": 500.0,
                    "totalMemory_bytes": 2147483648,
                    "podCount": 5,
                },
            }
        ]
        metrics = _make_metrics(resource_entries=resource)
        rows = align_time_series(metrics, resolution_s=5.0)

        cpu_rows = [r for r in rows if r.get("node_cpu_percent") == 30.0]
        assert len(cpu_rows) >= 1
        assert cpu_rows[0]["node_cpu_millicores"] == 1200.5

    def test_recovery_signal(self):
        recovery = [
            {
                "deletionTime": "2026-04-02T01:35:10+00:00",
                "readyTime": "2026-04-02T01:35:12+00:00",
                "totalRecovery_ms": 2000,
            }
        ]
        metrics = _make_metrics(recovery_events=recovery)
        rows = align_time_series(metrics, resolution_s=5.0)

        # The bucket containing the recovery window should have recovery_in_progress=1
        rec_rows = [r for r in rows if r.get("recovery_in_progress") == 1]
        assert len(rec_rows) >= 1
        assert rec_rows[0]["recovery_total_ms"] == 2000

    def test_event_timeline_counted(self):
        events = [
            {"time": "2026-04-02T01:35:05+00:00", "type": "DELETED", "pod": "a", "phase": "Running"},
            {"time": "2026-04-02T01:35:05+00:00", "type": "ADDED", "pod": "b", "phase": "Pending"},
            {"time": "2026-04-02T01:35:06+00:00", "type": "MODIFIED", "pod": "b", "phase": "Running"},
        ]
        metrics = _make_metrics(event_timeline=events)
        rows = align_time_series(metrics, resolution_s=5.0)

        # Find the 5s bucket
        bucket = [r for r in rows if r.get("events:deleted_count")]
        assert len(bucket) >= 1
        assert bucket[0]["events:deleted_count"] == 1
        assert bucket[0]["events:added_count"] == 1

    def test_forward_fill(self):
        resource = [
            {
                "timestamp": "2026-04-02T01:35:05+00:00",
                "phase": "pre-chaos",
                "node": {"cpu_percent": 25.0, "cpu_millicores": 1000,
                         "memory_bytes": 0, "memory_percent": 0},
                "podAggregate": {},
            }
        ]
        metrics = _make_metrics(resource_entries=resource)
        rows = align_time_series(metrics, resolution_s=5.0)

        # The value at 5s should be forward-filled to subsequent buckets
        filled = [r for r in rows if r.get("node_cpu_percent") == 25.0]
        # Should be present in multiple buckets (forward filled)
        assert len(filled) > 1


class TestExportAlignedCsv:
    def test_empty_rows(self):
        assert export_aligned_csv([]) == ""

    def test_csv_output(self):
        metrics = _make_metrics(
            latency_entries=[{
                "timestamp": "2026-04-02T01:35:05+00:00",
                "phase": "during-chaos",
                "routes": {"/": {"latency_ms": 50, "status": "ok"}},
            }]
        )
        rows = align_time_series(metrics, resolution_s=5.0)
        csv_text = export_aligned_csv(rows)

        reader = csv.DictReader(io.StringIO(csv_text))
        csv_rows = list(reader)
        assert len(csv_rows) > 0
        # Check that priority columns are at the front
        headers = list(csv_rows[0].keys())
        assert headers[0] == "timestamp"
        assert "anomaly_label" in headers

    def test_csv_file_write(self, tmp_path):
        metrics = _make_metrics()
        rows = align_time_series(metrics, resolution_s=5.0)
        out = str(tmp_path / "test.csv")
        export_aligned_csv(rows, output_path=out)

        with open(out) as f:
            content = f.read()
        assert "timestamp" in content
        assert "anomaly_label" in content
