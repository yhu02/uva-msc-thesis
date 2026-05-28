"""Unit tests for orchestrator timeout and readiness helpers."""

from chaosprobe.orchestrator.readiness import shell_escape
from chaosprobe.orchestrator.strategy_runner import (
    _generate_east_west_routes,
    _is_non_http_target,
)
from chaosprobe.orchestrator.timeout import (
    compute_effective_timeout,
    extract_chaos_duration,
    parse_probe_timeout,
)


class TestParseProbeTimeout:
    def test_seconds(self):
        assert parse_probe_timeout("15s") == 15

    def test_milliseconds(self):
        assert parse_probe_timeout("1500ms") == 1

    def test_milliseconds_rounds_to_min_1(self):
        assert parse_probe_timeout("500ms") == 1

    def test_minutes(self):
        assert parse_probe_timeout("2m") == 120

    def test_plain_integer(self):
        assert parse_probe_timeout("10") == 10

    def test_whitespace(self):
        assert parse_probe_timeout("  5s  ") == 5

    def test_empty_string(self):
        assert parse_probe_timeout("") == 5

    def test_invalid_string(self):
        assert parse_probe_timeout("abc") == 5

    def test_negative_seconds_clamped(self):
        # Negative values should clamp to 1
        assert parse_probe_timeout("-5s") == 1

    def test_zero_seconds_clamped(self):
        assert parse_probe_timeout("0s") == 1

    def test_zero_plain(self):
        assert parse_probe_timeout("0") == 1


class TestExtractChaosDuration:
    def test_extracts_from_env(self):
        scenario = {
            "experiments": [
                {
                    "spec": {
                        "spec": {
                            "experiments": [
                                {
                                    "spec": {
                                        "components": {
                                            "env": [
                                                {"name": "TOTAL_CHAOS_DURATION", "value": "120"},
                                            ]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            ]
        }
        assert extract_chaos_duration(scenario) == 120

    def test_fallback_to_60(self):
        assert extract_chaos_duration({}) == 60
        assert extract_chaos_duration({"experiments": []}) == 60

    def test_takes_max_across_experiments(self):
        scenario = {
            "experiments": [
                {
                    "spec": {
                        "spec": {
                            "experiments": [
                                {
                                    "spec": {
                                        "components": {
                                            "env": [
                                                {"name": "TOTAL_CHAOS_DURATION", "value": "30"},
                                            ]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                },
                {
                    "spec": {
                        "spec": {
                            "experiments": [
                                {
                                    "spec": {
                                        "components": {
                                            "env": [
                                                {"name": "TOTAL_CHAOS_DURATION", "value": "90"},
                                            ]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                },
            ]
        }
        # The floor is 60, so 30 is ignored; 90 > 60
        assert extract_chaos_duration(scenario) == 90


class TestComputeEffectiveTimeout:
    def test_respects_user_timeout_when_larger(self):
        # No probes, chaos_duration=60, min = 60 + 0 + 120 = 180
        scenario = {"experiments": []}
        assert compute_effective_timeout(scenario, 600) == 600

    def test_computes_minimum_with_probes(self):
        scenario = {
            "experiments": [
                {
                    "spec": {
                        "spec": {
                            "experiments": [
                                {
                                    "spec": {
                                        "components": {
                                            "env": [
                                                {"name": "TOTAL_CHAOS_DURATION", "value": "60"},
                                            ]
                                        },
                                        "probe": [
                                            {
                                                "runProperties": {
                                                    "probeTimeout": "10s",
                                                    "retry": "3",
                                                }
                                            }
                                        ],
                                    }
                                }
                            ]
                        }
                    }
                }
            ]
        }
        # chaos=60, probes: 10*(3+1)=40, min=60+2*40+120=260
        assert compute_effective_timeout(scenario, 100) == 260

    def test_handles_malformed_retry(self):
        """Non-integer retry value should not crash — should default to 0."""
        scenario = {
            "experiments": [
                {
                    "spec": {
                        "spec": {
                            "experiments": [
                                {
                                    "spec": {
                                        "components": {
                                            "env": [
                                                {"name": "TOTAL_CHAOS_DURATION", "value": "60"},
                                            ]
                                        },
                                        "probe": [
                                            {
                                                "runProperties": {
                                                    "probeTimeout": "10s",
                                                    "retry": "invalid",
                                                }
                                            }
                                        ],
                                    }
                                }
                            ]
                        }
                    }
                }
            ]
        }
        # chaos=60, probes: 10*(0+1)=10 (retry defaults to 0), min=60+2*10+120=200
        assert compute_effective_timeout(scenario, 100) == 200


class TestParseProbeTimeoutFloats:
    """Tests for float duration parsing (e.g. '1.5s')."""

    def test_float_seconds(self):
        assert parse_probe_timeout("1.5s") == 1

    def test_float_seconds_rounds_down(self):
        assert parse_probe_timeout("2.9s") == 2

    def test_float_minutes(self):
        assert parse_probe_timeout("1.5m") == 90

    def test_float_milliseconds(self):
        assert parse_probe_timeout("1500.0ms") == 1

    def test_float_plain(self):
        assert parse_probe_timeout("2.5") == 2


class TestShellEscape:
    def test_plain_string(self):
        assert shell_escape("hello") == "hello"

    def test_single_quote(self):
        assert shell_escape("it's") == "it'\\''s"

    def test_multiple_quotes(self):
        assert shell_escape("a'b'c") == "a'\\''b'\\''c"

    def test_empty_string(self):
        assert shell_escape("") == ""

    def test_url_with_special_chars(self):
        url = "http://frontend.ns.svc.cluster.local/cart?user=test&qty=1"
        # No transformation needed — special chars are safe inside single quotes
        assert shell_escape(url) == url


class TestIsNonHttpTarget:
    """`_is_non_http_target` skips services that don't speak HTTP so
    east-west route generation doesn't emit useless probes for Redis /
    Postgres / Kafka / etc."""

    def test_redis_variants(self):
        assert _is_non_http_target("redis-cart") is True
        assert _is_non_http_target("redis") is True
        assert _is_non_http_target("Redis-Master") is True  # case-insensitive

    def test_other_databases(self):
        assert _is_non_http_target("postgres-primary") is True
        assert _is_non_http_target("mysql") is True
        assert _is_non_http_target("mongodb") is True
        assert _is_non_http_target("kafka-broker") is True
        assert _is_non_http_target("memcached") is True

    def test_http_services_not_filtered(self):
        for svc in (
            "frontend",
            "productcatalogservice",
            "checkoutservice",
            "currencyservice",
            "recommendationservice",
            "adservice",
            "shippingservice",
            "paymentservice",
            "cartservice",
            "emailservice",
        ):
            assert _is_non_http_target(svc) is False, f"{svc} unexpectedly filtered"


class TestEastWestRouteGeneration:
    """`_generate_east_west_routes` turns a dependency-edge list into
    LatencyProber route tuples so the H1/H6 hypotheses can be tested
    against inter-service paths, not just frontend probes."""

    def test_empty_edge_list_returns_empty(self):
        assert _generate_east_west_routes([]) == []

    def test_basic_edge_becomes_route(self):
        routes = _generate_east_west_routes([("checkout", "currency")])
        assert routes == [("currency", "/healthz", "checkout->currency", "GET")]

    def test_multiple_edges_per_target_dedupe_with_combined_description(self):
        """Two services depending on the same target produce one route whose
        description records both originating edges (so attribution is
        preserved in the surfaced metrics)."""
        routes = _generate_east_west_routes(
            [
                ("checkout", "currency"),
                ("frontend", "currency"),
            ]
        )
        assert len(routes) == 1
        target, path, description, method = routes[0]
        assert target == "currency"
        assert path == "/healthz"
        assert "checkout->currency" in description
        assert "frontend->currency" in description
        assert method == "GET"

    def test_non_http_target_skipped(self):
        """Redis-style targets are excluded so the probe doesn't generate
        always-failing samples on TCP-only backends."""
        routes = _generate_east_west_routes([("cart", "redis-cart")])
        assert routes == []

    def test_mixed_edges_skip_only_non_http(self):
        routes = _generate_east_west_routes(
            [
                ("checkout", "currency"),
                ("cart", "redis-cart"),
                ("frontend", "checkout"),
            ]
        )
        targets = {r[0] for r in routes}
        assert targets == {"currency", "checkout"}
        assert "redis-cart" not in targets

    def test_malformed_edge_skipped(self):
        """Edges that aren't 2-tuples (empty, single-element, None) are
        skipped without raising — defensive against upstream callers that
        emit partial data."""
        routes = _generate_east_west_routes(
            [
                (),
                ("checkout",),
                None,
                ("", "currency"),
                ("checkout", ""),
                ("checkout", "currency"),  # the only valid one
            ]
        )
        assert len(routes) == 1
        assert routes[0][0] == "currency"

    def test_custom_healthz_path(self):
        routes = _generate_east_west_routes(
            [("checkout", "currency")],
            healthz_path="/ready",
        )
        assert routes[0][1] == "/ready"

    def test_all_routes_use_get_method(self):
        """East-west routes always use GET for healthz probes; the LatencyProber
        encodes method via the tuple shape so consumers can rely on it."""
        routes = _generate_east_west_routes(
            [
                ("a", "b"),
                ("c", "d"),
            ]
        )
        for route in routes:
            assert route[3] == "GET"

    def test_route_tuple_shape_matches_extract_http_routes(self):
        """East-west routes drop into the same prober plumbing as the
        scenario-extracted routes — same 4-tuple shape."""
        routes = _generate_east_west_routes([("checkout", "currency")])
        for route in routes:
            assert len(route) == 4
            target, path, description, method = route
            assert isinstance(target, str)
            assert isinstance(path, str)
            assert isinstance(description, str)
            assert isinstance(method, str)
