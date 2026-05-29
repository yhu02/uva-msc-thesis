"""Tests for the per-strategy ``routeViewAggregate`` roll-up.

Without this roll-up, comparing per-route Locust + LatencyProber numbers
across strategies forced a reader to iterate the per-iteration ``routeView``
lists by hand.  The thesis's per-vantage-point (H3) and tail-of-route (H5)
arguments rest on aggregated values.
"""

from chaosprobe.orchestrator.run_phases import (
    _aggregate_route_views,
    aggregate_iterations,
)


def _iter(route_view, mean_r=1000.0):
    return {
        "resilienceScore": 80.0,
        "verdict": "PASS",
        "metrics": {"recovery": {"summary": {"meanRecovery_ms": mean_r}}},
        "routeView": route_view,
    }


class TestAggregateRouteViews:
    def test_empty_inputs_no_key(self):
        assert _aggregate_route_views([]) == []
        assert _aggregate_route_views([_iter([])]) == []
        agg = aggregate_iterations([_iter([])])
        assert "routeViewAggregate" not in agg

    def test_locust_totals_summed_and_p95_averaged(self):
        iters = [
            _iter(
                [
                    {
                        "route": "/cart",
                        "locust": {
                            "requests": 100,
                            "failures": 5,
                            "p95ResponseTime_ms": 120,
                        },
                    }
                ]
            ),
            _iter(
                [
                    {
                        "route": "/cart",
                        "locust": {
                            "requests": 200,
                            "failures": 10,
                            "p95ResponseTime_ms": 180,
                        },
                    }
                ]
            ),
        ]
        agg = aggregate_iterations(iters)
        cart = agg["routeViewAggregate"][0]
        assert cart["route"] == "/cart"
        assert cart["iterations"] == 2
        assert cart["locust"]["totalRequests"] == 300
        assert cart["locust"]["totalFailures"] == 15
        assert cart["locust"]["meanP95_ms"] == 150.0
        assert cart["locust"]["iterationsObserved"] == 2

    def test_latency_prober_per_phase_aggregation(self):
        iters = [
            _iter(
                [
                    {
                        "route": "/",
                        "latencyProber": {
                            "during-chaos": {"p95_ms": 200},
                            "post-chaos": {"p95_ms": 100},
                        },
                    }
                ]
            ),
            _iter(
                [
                    {
                        "route": "/",
                        "latencyProber": {
                            "during-chaos": {"p95_ms": 300},
                            "post-chaos": {"p95_ms": 90},
                        },
                    }
                ]
            ),
        ]
        agg = aggregate_iterations(iters)
        root = agg["routeViewAggregate"][0]
        assert root["route"] == "/"
        lp = root["latencyProber"]
        assert lp["during-chaos"]["meanP95_ms"] == 250.0
        assert lp["during-chaos"]["iterationsObserved"] == 2
        assert lp["post-chaos"]["meanP95_ms"] == 95.0

    def test_locust_only_route_omits_latency_prober_block(self):
        iters = [_iter([{"route": "/a", "locust": {"p95ResponseTime_ms": 100}}])]
        out = aggregate_iterations(iters)["routeViewAggregate"]
        assert "latencyProber" not in out[0]

    def test_latency_only_route_omits_locust_block(self):
        iters = [
            _iter(
                [
                    {
                        "route": "/east-west",
                        "latencyProber": {"during-chaos": {"p95_ms": 50}},
                    }
                ]
            )
        ]
        out = aggregate_iterations(iters)["routeViewAggregate"]
        assert "locust" not in out[0]
        assert "latencyProber" in out[0]

    def test_sort_order_locust_then_alpha(self):
        iters = [
            _iter(
                [
                    {
                        "route": "/zzz",
                        "latencyProber": {"during-chaos": {"p95_ms": 10}},
                    },
                    {
                        "route": "/aaa",
                        "latencyProber": {"during-chaos": {"p95_ms": 10}},
                    },
                    {"route": "/cart", "locust": {"p95ResponseTime_ms": 100}},
                ]
            )
        ]
        out = aggregate_iterations(iters)["routeViewAggregate"]
        # Locust route first, then latency-only routes alphabetically.
        assert [r["route"] for r in out] == ["/cart", "/aaa", "/zzz"]

    def test_malformed_entries_skipped(self):
        iters = [
            _iter(
                [
                    {"route": "/ok", "locust": {"p95ResponseTime_ms": 100}},
                    "not-a-dict",
                    {"route": "", "locust": {"p95ResponseTime_ms": 999}},
                    {"locust": {"p95ResponseTime_ms": 888}},  # no route
                ]
            )
        ]
        out = aggregate_iterations(iters)["routeViewAggregate"]
        assert [r["route"] for r in out] == ["/ok"]

    def test_p95ResponseTime_ms_field_works_too(self):
        """LatencyProber phase data might key the p95 as either
        ``p95_ms`` or ``p95ResponseTime_ms``; both must work."""
        iters = [
            _iter(
                [
                    {
                        "route": "/x",
                        "latencyProber": {
                            "during-chaos": {"p95ResponseTime_ms": 150},
                        },
                    }
                ]
            )
        ]
        out = aggregate_iterations(iters)["routeViewAggregate"]
        assert out[0]["latencyProber"]["during-chaos"]["meanP95_ms"] == 150.0
