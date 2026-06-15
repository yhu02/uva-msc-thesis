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


def test_no_probes_falls_back_to_user_home_route():
    # node-drain has no httpProbes -> a GET / route on the load service is added
    # so the registered user_err_during outcome exists.
    routes = _extract_http_routes(
        {"experiments": []}, "online-boutique", fallback_service="frontend"
    )
    assert routes == [("frontend", "/", "user-home", "GET")]


def test_no_probes_no_fallback_returns_empty():
    assert _extract_http_routes({"experiments": []}, "online-boutique") == []
