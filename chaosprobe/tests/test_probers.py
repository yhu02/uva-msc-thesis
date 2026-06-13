"""Tests for prober wiring in ``create_and_start_probers``.

Covers the east-west ``service_routes`` threading to the
``ContinuousLatencyProber`` (so gRPC/TCP backends get probed) and the
conntrack prober's create/start/stop/collect wiring.
"""

from unittest.mock import MagicMock, patch

from chaosprobe.orchestrator.probers import create_and_start_probers, stop_and_collect_probers

_ALL_PROBER_PATCHES = (
    "chaosprobe.metrics.latency.ContinuousLatencyProber",
    "chaosprobe.metrics.recovery.RecoveryWatcher",
    "chaosprobe.metrics.prometheus.ContinuousPrometheusProber",
    "chaosprobe.metrics.resources.ContinuousResourceProber",
    "chaosprobe.metrics.throughput.ContinuousDiskProber",
    "chaosprobe.metrics.throughput.ContinuousRedisProber",
    "chaosprobe.metrics.conntrack.ConntrackProtocolProber",
    "chaosprobe.metrics.endpointslice_sampler.EndpointSliceTimeSeriesProber",
)


def _create(**overrides):
    kwargs = dict(
        measure_latency=False,
        measure_redis=False,
        measure_disk=False,
        measure_resources=False,
        measure_prometheus=False,
        measure_conntrack=False,
        measure_endpoint_slices=False,
        prometheus_url=(),
        http_routes=None,
        service_routes=None,
        expected_chaos_duration=10.0,
    )
    kwargs.update(overrides)
    with (
        patch(_ALL_PROBER_PATCHES[0]) as latency_cls,
        patch(_ALL_PROBER_PATCHES[1]),
        patch(_ALL_PROBER_PATCHES[2]),
        patch(_ALL_PROBER_PATCHES[3]),
        patch(_ALL_PROBER_PATCHES[4]),
        patch(_ALL_PROBER_PATCHES[5]),
        patch(_ALL_PROBER_PATCHES[6]) as conntrack_cls,
        patch(_ALL_PROBER_PATCHES[7]) as endpointslice_cls,
    ):
        probers = create_and_start_probers("ns", "frontend", **kwargs)
    return probers, latency_cls, conntrack_cls, endpointslice_cls


def test_threads_service_routes_to_latency_prober():
    service_routes = [("checkout", "currency", "currency:7000", "grpc", "checkout->currency")]
    http_routes = [("frontend", "/", "homepage", "GET")]

    probers, latency_cls, _, _ = _create(
        measure_latency=True,
        http_routes=http_routes,
        service_routes=service_routes,
    )

    latency_cls.assert_called_once()
    _args, kwargs = latency_cls.call_args
    assert kwargs["service_routes"] == service_routes
    assert kwargs["http_routes"] == http_routes
    assert probers["latency"] is latency_cls.return_value


def test_conntrack_prober_created_started_and_duration_propagated():
    probers, _, conntrack_cls, _ = _create(measure_conntrack=True)

    conntrack_cls.assert_called_once_with("ns")
    prober = conntrack_cls.return_value
    assert probers["conntrack"] is prober
    prober.start.assert_called_once()
    assert prober._expected_chaos_duration == 10.0


def test_conntrack_prober_disabled():
    probers, _, conntrack_cls, _ = _create(measure_conntrack=False)
    conntrack_cls.assert_not_called()
    assert probers["conntrack"] is None


def test_endpointslice_prober_created_started_and_duration_propagated():
    probers, _, _, endpointslice_cls = _create(measure_endpoint_slices=True)

    endpointslice_cls.assert_called_once_with("ns")
    prober = endpointslice_cls.return_value
    assert probers["endpointSlices"] is prober
    prober.start.assert_called_once()
    assert prober._expected_chaos_duration == 10.0


def test_endpointslice_prober_disabled():
    probers, _, _, endpointslice_cls = _create(measure_endpoint_slices=False)
    endpointslice_cls.assert_not_called()
    assert probers["endpointSlices"] is None


# ---------------------------------------------------------------------------
# stop_and_collect_probers — conntrack collection paths
# ---------------------------------------------------------------------------


def test_collects_conntrack_samples_and_error_breakdown(capsys):
    prober = MagicMock()
    prober.result.return_value = {
        "samples": [{"ts": "t", "node": "w1", "proto": "udp", "count": 1, "phase": "pre-chaos"}],
        "meta": {"available": True, "nodes": ["w1"], "probeErrors": 2},
    }

    results = stop_and_collect_probers({"conntrack": prober})

    prober.stop.assert_called_once()
    assert results["conntrack"]["samples"]
    assert results["probeErrorBreakdown"] == {"conntrack": 2}
    out = capsys.readouterr().out
    assert "Conntrack: 1 protocol samples across 1 node(s)" in out


def test_reports_conntrack_unavailable_reason(capsys):
    prober = MagicMock()
    prober.result.return_value = {
        "samples": [],
        "meta": {"available": False, "reason": "no worker nodes discovered"},
    }

    results = stop_and_collect_probers({"conntrack": prober})

    assert results["conntrack"]["meta"]["available"] is False
    assert "Conntrack: no worker nodes discovered" in capsys.readouterr().out


def test_no_conntrack_prober_collects_nothing():
    results = stop_and_collect_probers({})
    assert "conntrack" not in results


def test_conntrack_collection_failure_degrades_to_warning(capsys):
    prober = MagicMock()
    prober.result.side_effect = RuntimeError("boom")

    results = stop_and_collect_probers({"conntrack": prober})

    assert "conntrack" not in results
    assert "failed to collect conntrack data" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# stop_and_collect_probers — EndpointSlice time-series collection paths
# ---------------------------------------------------------------------------


def test_collects_endpointslice_samples_and_error_breakdown(capsys):
    prober = MagicMock()
    prober.result.return_value = {
        "samples": [{"ts": "t", "phase": "pre-chaos", "services": {}}],
        "meta": {"available": True, "sampleCount": 1, "probeErrors": 3},
    }

    results = stop_and_collect_probers({"endpointSlices": prober})

    prober.stop.assert_called_once()
    assert results["endpointSlices"]["samples"]
    assert results["probeErrorBreakdown"] == {"endpointSlices": 3}
    out = capsys.readouterr().out
    assert "EndpointSlices: 1 time-series samples" in out


def test_reports_endpointslice_unavailable_reason(capsys):
    prober = MagicMock()
    prober.result.return_value = {
        "samples": [],
        "meta": {"available": False, "reason": "no EndpointSlice sample succeeded"},
    }

    results = stop_and_collect_probers({"endpointSlices": prober})

    assert results["endpointSlices"]["meta"]["available"] is False
    assert "EndpointSlices: no EndpointSlice sample succeeded" in capsys.readouterr().out


def test_no_endpointslice_prober_collects_nothing():
    results = stop_and_collect_probers({})
    assert "endpointSlices" not in results


def test_endpointslice_collection_failure_degrades_to_warning(capsys):
    prober = MagicMock()
    prober.result.side_effect = RuntimeError("boom")

    results = stop_and_collect_probers({"endpointSlices": prober})

    assert "endpointSlices" not in results
    assert "failed to collect EndpointSlice time series" in capsys.readouterr().err
