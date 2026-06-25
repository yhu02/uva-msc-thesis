"""Tests for cascade impact analysis."""

from chaosprobe.metrics.cascade import compute_cascade_timeline


def _make_latency_data(entries, pre_routes=None):
    """Build a latency data dict."""
    phases = {}
    if pre_routes:
        phases["pre-chaos"] = {"sampleCount": 5, "routes": pre_routes}
    return {"timeSeries": entries, "phases": phases}


class TestComputeCascadeTimeline:
    def test_empty_data(self):
        result = compute_cascade_timeline({"timeSeries": [], "phases": {}})
        assert result["affectedRoutes"] == []
        assert result["summary"]["totalAffected"] == 0

    def test_no_degradation(self):
        entries = [
            {
                "timestamp": "2026-04-02T01:35:05+00:00",
                "phase": "during-chaos",
                "routes": {
                    "/": {"latency_ms": 40, "status": "ok"},
                },
            }
        ]
        pre_routes = {"/": {"mean_ms": 50}}
        data = _make_latency_data(entries, pre_routes)
        result = compute_cascade_timeline(data)
        assert result["summary"]["totalAffected"] == 0

    def test_latency_degradation_detected(self):
        pre_routes = {"/": {"mean_ms": 50}}
        entries = [
            {
                "timestamp": "2026-04-02T01:35:00+00:00",
                "phase": "pre-chaos",
                "routes": {"/": {"latency_ms": 48, "status": "ok"}},
            },
            {
                "timestamp": "2026-04-02T01:35:05+00:00",
                "phase": "during-chaos",
                "routes": {"/": {"latency_ms": 200, "status": "ok"}},
            },
            {
                "timestamp": "2026-04-02T01:35:10+00:00",
                "phase": "during-chaos",
                "routes": {"/": {"latency_ms": 350, "status": "ok"}},
            },
            {
                "timestamp": "2026-04-02T01:35:15+00:00",
                "phase": "post-chaos",
                "routes": {"/": {"latency_ms": 45, "status": "ok"}},
            },
        ]
        data = _make_latency_data(entries, pre_routes)
        result = compute_cascade_timeline(data, degradation_factor=2.0)

        assert result["summary"]["totalAffected"] == 1
        affected = result["affectedRoutes"][0]
        assert affected["route"] == "/"
        assert affected["firstDegradation"] == "2026-04-02T01:35:05+00:00"
        assert affected["peakLatency_ms"] == 350
        assert affected["degradedSamples"] == 2

    def test_error_based_degradation(self):
        pre_routes = {"/cart": {"mean_ms": 30}}
        entries = [
            {
                "timestamp": "2026-04-02T01:35:05+00:00",
                "phase": "during-chaos",
                "routes": {"/cart": {"latency_ms": None, "status": "error", "error": "timeout"}},
            },
            {
                "timestamp": "2026-04-02T01:35:10+00:00",
                "phase": "during-chaos",
                "routes": {"/cart": {"latency_ms": None, "status": "error", "error": "timeout"}},
            },
        ]
        data = _make_latency_data(entries, pre_routes)
        result = compute_cascade_timeline(data)

        assert result["summary"]["totalAffected"] == 1
        affected = result["affectedRoutes"][0]
        assert affected["errorCount"] == 2

    def test_multiple_routes_affected(self):
        pre_routes = {
            "/": {"mean_ms": 50},
            "/product/X": {"mean_ms": 40},
            "/_healthz": {"mean_ms": 5},
        }
        entries = [
            {
                "timestamp": "2026-04-02T01:35:05+00:00",
                "phase": "during-chaos",
                "routes": {
                    "/": {"latency_ms": 200, "status": "ok"},
                    "/product/X": {"latency_ms": 300, "status": "ok"},
                    "/_healthz": {"latency_ms": 6, "status": "ok"},
                },
            },
        ]
        data = _make_latency_data(entries, pre_routes)
        result = compute_cascade_timeline(data, degradation_factor=2.0)

        affected_routes = {r["route"] for r in result["affectedRoutes"]}
        assert "/" in affected_routes
        assert "/product/X" in affected_routes
        # healthz should NOT be affected (6ms < 5*2=10ms)
        assert "/_healthz" not in affected_routes

    def test_target_service_from_labels(self):
        data = _make_latency_data([])
        labels = [{"targetService": "productcatalogservice", "faultType": "pod-delete"}]
        result = compute_cascade_timeline(data, anomaly_labels=labels)
        assert result["targetService"] == "productcatalogservice"

    def test_recovery_detected(self):
        pre_routes = {"/": {"mean_ms": 50}}
        entries = [
            {
                "timestamp": "2026-04-02T01:35:05+00:00",
                "phase": "during-chaos",
                "routes": {"/": {"latency_ms": 200, "status": "ok"}},
            },
            {
                "timestamp": "2026-04-02T01:35:10+00:00",
                "phase": "post-chaos",
                "routes": {"/": {"latency_ms": 45, "status": "ok"}},
            },
        ]
        data = _make_latency_data(entries, pre_routes)
        result = compute_cascade_timeline(data, degradation_factor=2.0)

        affected = result["affectedRoutes"][0]
        assert affected["recoveryTime"] == "2026-04-02T01:35:10+00:00"

    def test_cascade_ratio(self):
        pre_routes = {"/": {"mean_ms": 50}, "/cart": {"mean_ms": 30}}
        entries = [
            {
                "timestamp": "2026-04-02T01:35:05+00:00",
                "phase": "during-chaos",
                "routes": {
                    "/": {"latency_ms": 200, "status": "ok"},
                    "/cart": {"latency_ms": 35, "status": "ok"},
                },
            },
        ]
        data = _make_latency_data(entries, pre_routes)
        result = compute_cascade_timeline(data, degradation_factor=2.0)

        assert result["summary"]["cascadeRatio"] == 0.5  # 1 of 2 routes affected
