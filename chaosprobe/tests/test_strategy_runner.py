"""Unit tests for orchestrator timeout and readiness helpers."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from chaosprobe.orchestrator.readiness import shell_escape
from chaosprobe.orchestrator.strategy_runner import (
    _generate_east_west_routes,
    _is_non_http_target,
    _snapshot_cluster_state,
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


def _make_pod(name, node="worker-1", phase="Running", ready=True, restart_count=0):
    """Build a fake V1Pod-like object for snapshot tests."""
    pod = MagicMock()
    pod.metadata = MagicMock()
    pod.metadata.name = name
    pod.spec = MagicMock()
    pod.spec.node_name = node
    pod.status = MagicMock()
    pod.status.phase = phase

    ready_cond = MagicMock()
    ready_cond.type = "Ready"
    ready_cond.status = "True" if ready else "False"
    pod.status.conditions = [ready_cond]

    cs = MagicMock()
    cs.restart_count = restart_count
    pod.status.container_statuses = [cs]
    return pod


def _make_node(name, conditions=None):
    """Build a fake V1Node-like object for snapshot tests."""
    node = MagicMock()
    node.metadata = MagicMock()
    node.metadata.name = name
    node.status = MagicMock()
    if conditions is None:
        ready_cond = MagicMock()
        ready_cond.type = "Ready"
        ready_cond.status = "True"
        node.status.conditions = [ready_cond]
    else:
        node.status.conditions = conditions
    return node


def _make_condition(type_, status):
    cond = MagicMock()
    cond.type = type_
    cond.status = status
    return cond


class TestClusterStateSnapshot:
    """`_snapshot_cluster_state` is the lightweight per-iteration drift-detection
    surface for the n=3 statistical caveat in the thesis methodology critique."""

    def test_healthy_namespace_with_two_pods_and_two_nodes(self):
        core_api = MagicMock()
        core_api.list_namespaced_pod.return_value = MagicMock(
            items=[
                _make_pod("frontend-abc", node="worker-1", restart_count=0),
                _make_pod("checkout-xyz", node="worker-2", restart_count=1),
            ]
        )
        core_api.list_node.return_value = MagicMock(
            items=[
                _make_node(
                    "worker-1",
                    conditions=[
                        _make_condition("Ready", "True"),
                        _make_condition("MemoryPressure", "False"),
                    ],
                ),
                _make_node(
                    "worker-2",
                    conditions=[
                        _make_condition("Ready", "True"),
                        _make_condition("MemoryPressure", "True"),
                    ],
                ),
            ]
        )

        ts = datetime(2026, 5, 28, 22, 0, 0, tzinfo=timezone.utc)
        snap = _snapshot_cluster_state("online-boutique", core_api, now=ts)

        assert snap["namespace"] == "online-boutique"
        assert snap["timestamp"] == ts.isoformat()
        assert "errors" not in snap

        assert {p["name"] for p in snap["pods"]} == {"frontend-abc", "checkout-xyz"}
        frontend = next(p for p in snap["pods"] if p["name"] == "frontend-abc")
        assert frontend["node"] == "worker-1"
        assert frontend["phase"] == "Running"
        assert frontend["ready"] is True
        assert frontend["restartCount"] == 0
        checkout = next(p for p in snap["pods"] if p["name"] == "checkout-xyz")
        assert checkout["restartCount"] == 1

        assert {n["name"] for n in snap["nodes"]} == {"worker-1", "worker-2"}
        w1 = next(n for n in snap["nodes"] if n["name"] == "worker-1")
        assert w1["conditions"] == {"Ready": "True", "MemoryPressure": "False"}
        w2 = next(n for n in snap["nodes"] if n["name"] == "worker-2")
        # MemoryPressure=True on w2 is the kind of placement-vs-pressure
        # signal the snapshot is designed to catch.
        assert w2["conditions"]["MemoryPressure"] == "True"

    def test_empty_namespace_returns_empty_pod_list(self):
        core_api = MagicMock()
        core_api.list_namespaced_pod.return_value = MagicMock(items=[])
        core_api.list_node.return_value = MagicMock(items=[])

        snap = _snapshot_cluster_state("empty-ns", core_api)
        assert snap["pods"] == []
        assert snap["nodes"] == []
        assert "errors" not in snap

    def test_partial_pressure_only_surfaces_known_condition_types(self):
        """Non-standard condition types from node-problem-detector etc. are
        intentionally NOT surfaced in the snapshot — the snapshot has a fixed
        set of pressure flags; richer details belong in `_collect_node_info`."""
        core_api = MagicMock()
        core_api.list_namespaced_pod.return_value = MagicMock(items=[])
        core_api.list_node.return_value = MagicMock(
            items=[
                _make_node(
                    "worker-1",
                    conditions=[
                        _make_condition("Ready", "True"),
                        _make_condition("DiskPressure", "True"),
                        _make_condition("KernelDeadlock", "True"),  # custom type
                    ],
                )
            ]
        )
        snap = _snapshot_cluster_state("ns", core_api)
        conds = snap["nodes"][0]["conditions"]
        assert "DiskPressure" in conds
        assert "Ready" in conds
        # Custom types are out — keep the snapshot lean
        assert "KernelDeadlock" not in conds

    def test_list_pod_api_failure_surfaces_in_errors(self):
        core_api = MagicMock()
        core_api.list_namespaced_pod.side_effect = RuntimeError("connection refused")
        core_api.list_node.return_value = MagicMock(items=[])

        snap = _snapshot_cluster_state("ns", core_api)
        assert snap["pods"] == []
        assert any("list_namespaced_pod" in e for e in snap["errors"])

    def test_list_node_api_failure_surfaces_in_errors(self):
        core_api = MagicMock()
        core_api.list_namespaced_pod.return_value = MagicMock(items=[])
        core_api.list_node.side_effect = RuntimeError("timeout")

        snap = _snapshot_cluster_state("ns", core_api)
        assert snap["nodes"] == []
        assert any("list_node" in e for e in snap["errors"])

    def test_malformed_pod_without_name_is_skipped(self):
        """Defensive: a pod with no metadata.name (impossible from real K8s
        API, possible from mocks/tests) is silently skipped."""
        core_api = MagicMock()
        bad_pod = MagicMock()
        bad_pod.metadata.name = None
        core_api.list_namespaced_pod.return_value = MagicMock(
            items=[bad_pod, _make_pod("good-pod")]
        )
        core_api.list_node.return_value = MagicMock(items=[])

        snap = _snapshot_cluster_state("ns", core_api)
        names = [p["name"] for p in snap["pods"]]
        assert names == ["good-pod"]

    def test_pod_without_status_conditions_marked_not_ready(self):
        """A pod with no Ready condition (e.g. still Pending) is reported as
        ready=False without raising."""
        core_api = MagicMock()
        pod = MagicMock()
        pod.metadata.name = "pending-pod"
        pod.spec.node_name = None
        pod.status.phase = "Pending"
        pod.status.conditions = None
        pod.status.container_statuses = None
        core_api.list_namespaced_pod.return_value = MagicMock(items=[pod])
        core_api.list_node.return_value = MagicMock(items=[])

        snap = _snapshot_cluster_state("ns", core_api)
        entry = snap["pods"][0]
        assert entry["ready"] is False
        assert entry["phase"] == "Pending"
        assert entry["node"] is None
        assert entry["restartCount"] == 0

    def test_default_now_is_used_when_not_provided(self):
        """`now=None` defaults to `datetime.now(timezone.utc)` so callers
        don't have to thread a clock."""
        core_api = MagicMock()
        core_api.list_namespaced_pod.return_value = MagicMock(items=[])
        core_api.list_node.return_value = MagicMock(items=[])
        snap = _snapshot_cluster_state("ns", core_api)
        # The string is in ISO 8601 with timezone — sufficient to confirm
        # the default-now path executed without raising.
        assert "T" in snap["timestamp"]
        assert snap["timestamp"].endswith("+00:00") or snap["timestamp"].endswith("Z")
