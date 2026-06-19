"""Tests for strategy_runner._extract_http_routes (node-drain user-route fallback)."""

from chaosprobe.orchestrator.strategy_runner import _extract_http_routes


def _scenario_with_probe(url, method="get"):
    return {
        "experiments": [
            {
                "spec": {
                    "spec": {
                        "experiments": [
                            {
                                "spec": {
                                    "probe": [
                                        {
                                            "type": "httpProbe",
                                            "name": "home",
                                            "httpProbe/inputs": {
                                                "url": url,
                                                "method": {method: {}},
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                }
            }
        ]
    }


def test_extracts_scenario_httpprobe_routes():
    sc = _scenario_with_probe("http://frontend.online-boutique.svc.cluster.local/cart")
    routes = _extract_http_routes(sc, "online-boutique", fallback_service="frontend")
    assert routes == [("frontend", "/cart", "home", "GET")]  # scenario probe wins; no fallback


def test_preserves_query_string_in_route():
    # hotelReservation routes require query params (/hotels?inDate=...&lat=...);
    # the prober builds its URL from the route, so the query MUST be preserved or
    # the request errors (no params) — a broken-probe artifact. Path-only routes
    # (online-boutique) are unaffected.
    sc = _scenario_with_probe(
        "http://frontend.hotel-reservation.svc.cluster.local/hotels?inDate=2015-04-09&lat=37.7"
    )
    routes = _extract_http_routes(sc, "hotel-reservation", fallback_service="frontend")
    assert routes == [("frontend", "/hotels?inDate=2015-04-09&lat=37.7", "home", "GET")]


def test_dedups_by_base_path_keeping_first_query():
    # Two probes on the same path with different query params → one prober route
    # (dedup by base path), preserving the first occurrence's full route.
    sc = {
        "experiments": [
            {
                "spec": {
                    "spec": {
                        "experiments": [
                            {
                                "spec": {
                                    "probe": [
                                        {
                                            "type": "httpProbe",
                                            "name": "a",
                                            "httpProbe/inputs": {
                                                "url": "http://frontend.hr.svc.cluster.local/hotels?q=1",
                                                "method": {"get": {}},
                                            },
                                        },
                                        {
                                            "type": "httpProbe",
                                            "name": "b",
                                            "httpProbe/inputs": {
                                                "url": "http://frontend.hr.svc.cluster.local/hotels?q=2",
                                                "method": {"get": {}},
                                            },
                                        },
                                    ]
                                }
                            }
                        ]
                    }
                }
            }
        ]
    }
    routes = _extract_http_routes(sc, "hr", fallback_service="frontend")
    assert routes == [("frontend", "/hotels?q=1", "a", "GET")]  # one route, first query kept


def test_no_probes_falls_back_to_user_home_route():
    # node-drain has no httpProbes -> a GET / route on the load service is added
    # so the registered user_err_during outcome exists.
    routes = _extract_http_routes(
        {"experiments": []}, "online-boutique", fallback_service="frontend"
    )
    assert routes == [("frontend", "/", "user-home", "GET")]


def test_no_probes_no_fallback_returns_empty():
    assert _extract_http_routes({"experiments": []}, "online-boutique") == []
