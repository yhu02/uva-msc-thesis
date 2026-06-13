"""Tests for the continuous EndpointSlice time-series sampler (V2-H3 instrument).

Everything runs against a ``MagicMock`` DiscoveryV1Api injected via the
prober's ``discovery_api`` seam (the production prober builds a real
``DiscoveryV1Api`` after ``ensure_k8s_config``), so no cluster is needed:
canned EndpointSlice JSON is fed straight into the listing / sampling paths.
"""

import json
import logging
import time
from unittest.mock import MagicMock, patch

from kubernetes.client.rest import ApiException

from chaosprobe.metrics.collector import MetricsCollector
from chaosprobe.metrics.endpointslice_sampler import (
    DEFAULT_INTERVAL_S,
    EndpointSliceTimeSeriesProber,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slice_json(service_name, endpoints):
    """One raw EndpointSlice item, matching the discovery API's JSON shape."""
    return {
        "metadata": {
            "labels": {"kubernetes.io/service-name": service_name} if service_name else {}
        },
        "endpoints": endpoints,
    }


def _ready_ep(n):
    return [{"conditions": {"ready": True, "terminating": False}} for _ in range(n)]


def _list_resp(items):
    """A discovery-API list response whose ``.data`` is the raw JSON body."""
    return MagicMock(data=json.dumps({"items": items}))


def _make_prober(items=None, interval=DEFAULT_INTERVAL_S):
    """Create a prober with an injected discovery API answering *items*."""
    discovery = MagicMock()
    if items is not None:
        discovery.list_namespaced_endpoint_slice.return_value = _list_resp(items)
    prober = EndpointSliceTimeSeriesProber("test-ns", interval=interval, discovery_api=discovery)
    return prober, discovery


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_injected_discovery_api_skips_cluster_config(self):
        discovery = MagicMock()
        prober = EndpointSliceTimeSeriesProber("ns", discovery_api=discovery)
        assert prober.discovery_api is discovery
        assert prober.interval == DEFAULT_INTERVAL_S
        assert prober.namespace == "ns"

    def test_default_interval_is_15s(self):
        assert DEFAULT_INTERVAL_S == 15.0

    def test_builds_discovery_api_when_not_injected(self):
        with (
            patch("chaosprobe.metrics.endpointslice_sampler.ensure_k8s_config") as ensure,
            patch("chaosprobe.metrics.endpointslice_sampler.client") as mock_client,
        ):
            mock_client.DiscoveryV1Api.return_value = "api-obj"
            prober = EndpointSliceTimeSeriesProber("ns")
        ensure.assert_called_once()
        assert prober.discovery_api == "api-obj"


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


class TestSampling:
    def test_sample_once_records_services_record(self):
        prober, _ = _make_prober(items=[_slice_json("frontend", _ready_ep(3))])
        prober._sample_once()

        samples = prober.result()["samples"]
        assert len(samples) == 1
        record = samples[0]
        assert set(record) == {"ts", "phase", "services"}
        assert record["phase"] == "pre-chaos"
        assert record["services"] == {
            "frontend": {"ready": 3, "terminating": 0, "notReady": 0, "total": 3}
        }
        # ISO-8601 UTC so samples align with recorded chaos windows downstream.
        assert record["ts"].endswith("+00:00")

    def test_null_endpoints_summarizes_to_zero_total_not_crash(self):
        # The node-drain case: API returns endpoints: null for an emptied slice.
        prober, _ = _make_prober(items=[_slice_json("cart", None)])
        prober._sample_once()
        services = prober.result()["samples"][0]["services"]
        assert services["cart"]["total"] == 0
        assert services["cart"]["ready"] == 0

    def test_empty_namespace_records_empty_services(self):
        prober, _ = _make_prober(items=[])
        prober._sample_once()
        sample = prober.result()["samples"][0]
        assert sample["services"] == {}
        # An empty namespace is still a *successful* sample.
        assert prober.result()["meta"]["available"] is True

    def test_phase_tracks_chaos_markers(self):
        prober, _ = _make_prober(items=[_slice_json("frontend", _ready_ep(1))])
        prober._sample_once()
        prober.mark_chaos_start()
        prober._sample_once()
        prober.mark_chaos_end()
        prober._sample_once()
        phases = [s["phase"] for s in prober.result()["samples"]]
        assert phases == ["pre-chaos", "during-chaos", "post-chaos"]

    def test_runs_pre_during_post_so_recovery_tail_is_captured(self):
        # Depth shrinks then recovers — the post-chaos tail must be retained
        # (the legacy min-snapshot loop discarded it), so duration is derivable.
        prober, discovery = _make_prober()
        discovery.list_namespaced_endpoint_slice.side_effect = [
            _list_resp([_slice_json("f", _ready_ep(3))]),  # pre
            _list_resp([_slice_json("f", _ready_ep(0))]),  # during (trough)
            _list_resp([_slice_json("f", _ready_ep(3))]),  # post (recovered)
        ]
        prober._sample_once()
        prober.mark_chaos_start()
        prober._sample_once()
        prober.mark_chaos_end()
        prober._sample_once()
        readies = [s["services"]["f"]["ready"] for s in prober.result()["samples"]]
        assert readies == [3, 0, 3]


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_api_exception_counts_probe_error_and_records_nothing(self, caplog):
        prober, discovery = _make_prober()
        discovery.list_namespaced_endpoint_slice.side_effect = ApiException(status=403)
        with caplog.at_level(logging.WARNING):
            prober._sample_once()
        data = prober.result()
        assert data["samples"] == []
        assert data["meta"]["probeErrors"] == 1
        assert data["meta"]["available"] is False
        assert "list failed" in caplog.text

    def test_decode_error_counts_probe_error(self, caplog):
        prober, discovery = _make_prober()
        discovery.list_namespaced_endpoint_slice.return_value = MagicMock(data="not json{")
        with caplog.at_level(logging.WARNING):
            prober._sample_once()
        assert prober.result()["meta"]["probeErrors"] == 1

    def test_non_list_items_treated_as_empty(self):
        prober, discovery = _make_prober()
        discovery.list_namespaced_endpoint_slice.return_value = MagicMock(
            data=json.dumps({"items": None})
        )
        prober._sample_once()
        sample = prober.result()["samples"][0]
        assert sample["services"] == {}

    def test_probe_loop_survives_sampling_exception(self, caplog):
        prober, _ = _make_prober()

        def boom():
            prober._stop_event.set()  # one tick, then exit the loop
            raise RuntimeError("tick failed")

        with patch.object(prober, "_sample_once", side_effect=boom):
            with caplog.at_level(logging.WARNING):
                prober._probe_loop()
        assert prober.result()["meta"]["probeErrors"] == 1
        assert "tick failed" in caplog.text


# ---------------------------------------------------------------------------
# Full thread lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_full_thread_lifecycle_collects_samples(self):
        prober, _ = _make_prober(items=[_slice_json("frontend", _ready_ep(2))], interval=0.01)
        prober.start()
        try:
            deadline = time.time() + 5.0
            while time.time() < deadline and not prober.result()["samples"]:
                time.sleep(0.01)
        finally:
            prober.stop()
        data = prober.result()
        assert data["samples"], "sampling thread produced no samples"
        assert data["meta"]["available"] is True


# ---------------------------------------------------------------------------
# result() metadata
# ---------------------------------------------------------------------------


class TestResultMeta:
    def test_meta_unavailable_before_any_sample(self):
        prober, _ = _make_prober()
        meta = prober.result()["meta"]
        assert meta["available"] is False
        assert meta["reason"] == "no EndpointSlice sample succeeded"
        assert meta["sampleCount"] == 0
        assert "probeErrors" not in meta

    def test_meta_records_interval_namespace_and_count(self):
        prober, _ = _make_prober(items=[])
        prober._sample_once()
        meta = prober.result()["meta"]
        assert meta["available"] is True
        assert meta["intervalSeconds"] == DEFAULT_INTERVAL_S
        assert meta["namespace"] == "test-ns"
        assert meta["sampleCount"] == 1
        assert "reason" not in meta


# ---------------------------------------------------------------------------
# Collector integration — how the time series lands in summary.json
# ---------------------------------------------------------------------------


def _make_collector():
    with (
        patch("chaosprobe.metrics.collector.ensure_k8s_config"),
        patch("chaosprobe.metrics.collector.client") as mock_client,
    ):
        mock_core = MagicMock()
        mock_client.CoreV1Api.return_value = mock_core
        collector = MetricsCollector("test-ns")
    mock_core.list_namespaced_pod.return_value = MagicMock(items=[])
    collector.discovery_api = MagicMock()
    collector.discovery_api.list_namespaced_endpoint_slice.return_value = MagicMock(
        data=json.dumps({"items": []})
    )
    return collector


class TestCollectorIntegration:
    def test_timeseries_surfaces_as_summary_key(self):
        collector = _make_collector()
        samples = [{"ts": "t", "phase": "pre-chaos", "services": {}}]
        meta = {"available": True, "sampleCount": 1}

        result = collector.collect(
            deployment_name="frontend",
            since_time=0.0,
            until_time=10.0,
            endpoint_slice_timeseries_data={"samples": samples, "meta": meta},
        )
        assert result["endpointSliceTimeSeries"] == {"samples": samples, "meta": meta}

    def test_missing_pieces_default_to_empty_containers(self):
        collector = _make_collector()
        result = collector.collect(
            deployment_name="frontend",
            since_time=0.0,
            until_time=10.0,
            endpoint_slice_timeseries_data={},
        )
        assert result["endpointSliceTimeSeries"] == {"samples": [], "meta": {}}

    def test_omitted_when_not_collected(self):
        collector = _make_collector()
        result = collector.collect(
            deployment_name="frontend",
            since_time=0.0,
            until_time=10.0,
        )
        assert "endpointSliceTimeSeries" not in result
