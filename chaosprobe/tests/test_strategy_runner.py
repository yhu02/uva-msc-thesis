"""Unit tests for orchestrator timeout and readiness helpers."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from chaosprobe.orchestrator import strategy_runner
from chaosprobe.orchestrator.readiness import shell_escape
from chaosprobe.orchestrator.strategy_runner import (
    _PROBE_BUDGET_CAP,
    _aggregate_strategy,
    _build_iteration_routes,
    _build_route_view_for_iteration,
    _consolidate_service_routes,
    _is_unknown_dominated,
    _snapshot_cluster_state,
    _sync_neo4j,
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


class TestConsolidateServiceRoutes:
    """`_consolidate_service_routes` collapses dependency routes to one
    probe per target host, preserving the protocol + real ``host:port`` so
    gRPC/TCP backends are probed over their actual port instead of a
    non-existent HTTP ``/healthz``."""

    def test_empty_returns_empty(self):
        assert _consolidate_service_routes([]) == []

    def test_basic_route_preserved(self):
        routes = _consolidate_service_routes(
            [("checkout", "currency", "currency:7000", "grpc", "Currency Service")]
        )
        assert len(routes) == 1
        src, tgt, host, proto, desc = routes[0]
        assert (src, tgt, host, proto) == ("checkout", "currency", "currency:7000", "grpc")
        # Description is rewritten to the source->target edge label.
        assert desc == "checkout->currency"

    def test_duplicate_host_merged_with_combined_label(self):
        """Two services depending on the same backend produce one probe
        whose label records both edges, preserving attribution."""
        routes = _consolidate_service_routes(
            [
                ("checkout", "currency", "currency:7000", "grpc", "x"),
                ("frontend", "currency", "currency:7000", "grpc", "y"),
            ]
        )
        assert len(routes) == 1
        _src, _tgt, host, proto, desc = routes[0]
        assert (host, proto) == ("currency:7000", "grpc")
        assert "checkout->currency" in desc
        assert "frontend->currency" in desc

    def test_tcp_protocol_preserved(self):
        """Redis-style ``tcp`` targets are kept (probed via TCP connect),
        not dropped as the old HTTP-only generator did."""
        routes = _consolidate_service_routes(
            [("cart", "redis-cart", "redis-cart:6379", "tcp", "Redis")]
        )
        assert len(routes) == 1
        assert routes[0][2] == "redis-cart:6379"
        assert routes[0][3] == "tcp"

    def test_distinct_hosts_kept_separate(self):
        routes = _consolidate_service_routes(
            [
                ("checkout", "currency", "currency:7000", "grpc", "x"),
                ("frontend", "checkout", "checkout:5050", "grpc", "y"),
            ]
        )
        assert {r[2] for r in routes} == {"currency:7000", "checkout:5050"}

    def test_edges_missing_source_target_or_host_dropped(self):
        """Defensive: edges lacking a source, target, or host are skipped
        without raising, so only the well-formed edge survives."""
        routes = _consolidate_service_routes(
            [
                ("", "currency", "currency:7000", "grpc", "x"),
                ("checkout", "", "currency:7000", "grpc", "x"),
                ("checkout", "currency", "", "grpc", "x"),
                ("checkout", "currency", "currency:7000", "grpc", "ok"),
            ]
        )
        assert len(routes) == 1
        assert routes[0][0] == "checkout"
        assert routes[0][2] == "currency:7000"

    def test_duplicate_edge_label_not_repeated(self):
        """The same source->target edge appearing twice doesn't duplicate
        its label in the merged description."""
        routes = _consolidate_service_routes(
            [
                ("checkout", "currency", "currency:7000", "grpc", "x"),
                ("checkout", "currency", "currency:7000", "grpc", "x"),
            ]
        )
        assert routes[0][4] == "checkout->currency"


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


def _make_scenario(probe_paths):
    """Build a minimal scenario dict with N httpProbes for the budget-cap tests."""
    probes = []
    for i, path in enumerate(probe_paths):
        probes.append(
            {
                "name": f"frontend-probe-{i}",
                "type": "httpProbe",
                "httpProbe/inputs": {
                    "url": f"http://frontend.online-boutique.svc.cluster.local{path}",
                    "method": {"get": {"criteria": "==", "responseCode": "200"}},
                },
            }
        )
    return {
        "experiments": [
            {
                "spec": {
                    "spec": {
                        "experiments": [
                            {
                                "spec": {
                                    "probe": probes,
                                }
                            }
                        ]
                    }
                }
            }
        ]
    }


def _make_ctx(mutator):
    """Build a stand-in RunContext exposing only the fields
    `_build_iteration_routes` reads."""
    ctx = MagicMock()
    ctx.namespace = "online-boutique"
    ctx.mutator = mutator
    return ctx


class TestBuildIterationRoutes:
    """`_build_iteration_routes` splits the iteration's probe set into
    north-south HTTP routes (always preserved) and east-west service
    routes (grpc/tcp with the real ``host:port``), capping the combined
    count at the probe budget.  Returns a ``(north_south, east_west)``
    tuple."""

    def test_splits_north_south_and_east_west(self):
        scenario = _make_scenario(["/", "/cart"])
        mutator = MagicMock()
        mutator.get_service_dependency_routes.return_value = [
            ("checkout", "currency", "currency:7000", "grpc", "x"),
            ("frontend", "checkout", "checkout:5050", "grpc", "y"),
        ]
        ctx = _make_ctx(mutator)

        north_south, east_west = _build_iteration_routes(scenario, ctx)

        # North-south are scenario-extracted HTTP routes (target = "frontend")
        assert [r[0] for r in north_south] == ["frontend", "frontend"]
        # East-west are dependency-graph service routes with protocol + port,
        # never an HTTP /healthz path.
        assert {r[1] for r in east_west} == {"currency", "checkout"}
        for r in east_west:
            assert r[3] in ("grpc", "tcp")
            assert ":" in r[2]

    def test_no_dependencies_returns_empty_east_west(self):
        scenario = _make_scenario(["/", "/cart"])
        mutator = MagicMock()
        mutator.get_service_dependency_routes.return_value = []
        ctx = _make_ctx(mutator)

        north_south, east_west = _build_iteration_routes(scenario, ctx)
        assert len(north_south) == 2  # only the scenario probes
        assert east_west == []

    def test_dependency_fetch_failure_falls_back_to_north_south(self):
        """A K8s API failure when fetching dependencies must not break the
        iteration — log the warning, fall back to north-south only."""
        scenario = _make_scenario(["/"])
        mutator = MagicMock()
        mutator.get_service_dependency_routes.side_effect = RuntimeError("timeout")
        ctx = _make_ctx(mutator)

        north_south, east_west = _build_iteration_routes(scenario, ctx)
        assert len(north_south) == 1  # north-south survives
        assert east_west == []

    def test_budget_cap_trims_east_west_preserving_north_south(self):
        """With the cap at 15, supplying 7 scenario probes and 12 dep-graph
        edges should keep all 7 scenario probes and trim east-west to 8."""
        scenario = _make_scenario([f"/p{i}" for i in range(7)])
        mutator = MagicMock()
        mutator.get_service_dependency_routes.return_value = [
            (f"src{i}", f"target{i}", f"target{i}:8080", "grpc", "x") for i in range(12)
        ]
        ctx = _make_ctx(mutator)

        north_south, east_west = _build_iteration_routes(scenario, ctx)

        assert len(north_south) == 7
        assert all(r[0] == "frontend" for r in north_south)
        # Exactly headroom-many east-west routes survive.
        assert len(east_west) == _PROBE_BUDGET_CAP - 7

    def test_budget_cap_with_oversized_north_south_drops_all_east_west(self):
        """If the scenario alone exceeds the budget (pathological config),
        keep every scenario probe and emit zero east-west routes — never
        trim a user-defined probe."""
        scenario = _make_scenario([f"/p{i}" for i in range(_PROBE_BUDGET_CAP + 3)])
        mutator = MagicMock()
        mutator.get_service_dependency_routes.return_value = [
            ("a", "b", "b:1", "grpc", "x"),
            ("c", "d", "d:2", "grpc", "y"),
        ]
        ctx = _make_ctx(mutator)

        north_south, east_west = _build_iteration_routes(scenario, ctx)

        # Every scenario probe survives; no east-west snuck in.
        assert len(north_south) == _PROBE_BUDGET_CAP + 3
        assert all(r[0] == "frontend" for r in north_south)
        assert east_west == []


class TestBuildRouteViewForIteration:
    """`_build_route_view_for_iteration` is the extraction-plus-dispatch
    layer that turns iteration-level data (load_gen + recovery) into the
    inputs that build_route_view expects.  Pins the contract so the
    wiring in _run_single_iteration can't silently regress."""

    def test_both_sources_present_dispatches_to_build_route_view(self):
        load_gen = {
            "stats": {
                "endpoints": [
                    {
                        "name": "/",
                        "requests": 100,
                        "failures": 0,
                        "avgResponseTime_ms": 50.0,
                        "p95ResponseTime_ms": 120.0,
                    }
                ],
            },
        }
        recovery = {
            "latency": {
                "phases": {
                    "pre-chaos": {"routes": {"/": {"mean_ms": 48}}},
                    "during-chaos": {"routes": {"/": {"mean_ms": 110}}},
                    "post-chaos": {"routes": {"/": {"mean_ms": 60}}},
                },
            },
        }
        view = _build_route_view_for_iteration(load_gen, recovery)
        assert len(view) == 1
        entry = view[0]
        assert entry["route"] == "/"
        assert entry["locust"] is not None
        assert entry["latencyProber"]["pre-chaos"]["mean_ms"] == 48

    def test_load_gen_missing_yields_locust_none(self):
        recovery = {
            "latency": {
                "phases": {
                    "pre-chaos": {"routes": {"/": {"mean_ms": 50}}},
                    "during-chaos": {"routes": {}},
                    "post-chaos": {"routes": {}},
                },
            },
        }
        view = _build_route_view_for_iteration(None, recovery)
        assert len(view) == 1
        assert view[0]["locust"] is None

    def test_recovery_missing_yields_latency_none(self):
        load_gen = {
            "stats": {
                "endpoints": [
                    {
                        "name": "/",
                        "requests": 1,
                        "failures": 0,
                        "avgResponseTime_ms": 1,
                        "p95ResponseTime_ms": 1,
                    }
                ],
            },
        }
        view = _build_route_view_for_iteration(load_gen, None)
        assert len(view) == 1
        assert view[0]["latencyProber"] is None

    def test_recovery_without_latency_key_yields_latency_none(self):
        """A recovery dict without the `latency` key (e.g. a probe-only
        iteration where the LatencyProber was disabled) maps to None,
        not to an empty-phases dict — distinguishes 'prober off' from
        'prober ran with no samples'."""
        load_gen = {
            "stats": {
                "endpoints": [
                    {
                        "name": "/",
                        "requests": 1,
                        "failures": 0,
                        "avgResponseTime_ms": 1,
                        "p95ResponseTime_ms": 1,
                    }
                ],
            },
        }
        view = _build_route_view_for_iteration(load_gen, {"recovery": {}})
        assert view[0]["latencyProber"] is None

    def test_recovery_with_latency_but_no_phases_yields_latency_none(self):
        load_gen = {
            "stats": {
                "endpoints": [
                    {
                        "name": "/",
                        "requests": 1,
                        "failures": 0,
                        "avgResponseTime_ms": 1,
                        "p95ResponseTime_ms": 1,
                    }
                ],
            },
        }
        view = _build_route_view_for_iteration(load_gen, {"latency": {}})
        assert view[0]["latencyProber"] is None

    def test_both_missing_returns_empty(self):
        assert _build_route_view_for_iteration(None, None) == []
        assert _build_route_view_for_iteration(None, {}) == []
        assert _build_route_view_for_iteration({}, None) == []


def _iteration(verdict, score):
    return {
        "verdict": verdict,
        "resilienceScore": score,
        "metrics": {
            "recovery": {
                "summary": {
                    "meanRecovery_ms": 1000.0,
                    "maxRecovery_ms": 2000.0,
                    "p95Recovery_ms": 1800.0,
                }
            }
        },
        "runId": "run-1",
        "probeVerdicts": {},
    }


class TestAggregateStrategy:
    def test_single_iteration_pass(self):
        sr = {}
        result = _aggregate_strategy(
            SimpleNamespace(iterations=1), "spread", sr, [_iteration("PASS", 90)]
        )
        assert result is True
        assert sr["status"] == "completed"
        assert sr["experiment"]["resilienceScore"] == 90

    def test_single_iteration_fail(self):
        sr = {}
        result = _aggregate_strategy(
            SimpleNamespace(iterations=1), "spread", sr, [_iteration("FAIL", 30)]
        )
        assert result is False

    def test_multi_iteration_all_pass(self):
        sr = {}
        result = _aggregate_strategy(
            SimpleNamespace(iterations=2),
            "colocate",
            sr,
            [_iteration("PASS", 80), _iteration("PASS", 90)],
        )
        assert result is True
        assert sr["status"] == "completed"
        assert "aggregated" in sr

    def test_multi_iteration_mixed_is_not_full_pass(self):
        sr = {}
        result = _aggregate_strategy(
            SimpleNamespace(iterations=3),
            "colocate",
            sr,
            [_iteration("PASS", 80), _iteration("FAIL", 40), _iteration("PASS", 90)],
        )
        assert result is False

    def test_multi_iteration_recovery_mean_without_max(self):
        # Recovery summaries with meanRecovery_ms but no maxRecovery_ms make
        # aggregate_iterations set meanRecoveryTime_ms while maxRecoveryTime_ms
        # stays None — the echo must not crash formatting a None max.
        def _mean_only(verdict, score):
            it = _iteration(verdict, score)
            it["metrics"]["recovery"]["summary"] = {"meanRecovery_ms": 1000.0}
            return it

        sr = {}
        result = _aggregate_strategy(
            SimpleNamespace(iterations=2),
            "spread",
            sr,
            [_mean_only("PASS", 80), _mean_only("PASS", 90)],
        )
        assert result is True
        assert sr["aggregated"]["meanRecoveryTime_ms"] is not None
        assert sr["aggregated"]["maxRecoveryTime_ms"] is None

    def test_multi_iteration_all_error_renders_na_without_crashing(self):
        # Every iteration ERROR → aggregate_iterations reports
        # meanResilienceScore=None + allIterationsError; the per-strategy echo
        # must render "n/a" rather than crash formatting None with ``:.1f``.
        sr = {}
        result = _aggregate_strategy(
            SimpleNamespace(iterations=2),
            "colocate",
            sr,
            [_iteration("ERROR", 0), _iteration("ERROR", 0)],
        )
        assert result is False
        assert sr["aggregated"]["allIterationsError"] is True
        assert sr["aggregated"]["meanResilienceScore"] is None


def _neo4j_ctx(store):
    return SimpleNamespace(
        graph_store=store,
        neo4j_uri="bolt://host:7687",
        neo4j_user="neo4j",
        neo4j_password="pw",
    )


class TestSyncNeo4j:
    def test_success_first_attempt(self):
        store = MagicMock()
        ctx = _neo4j_ctx(store)
        assert _sync_neo4j(ctx, {"run": 1}) is True
        store.sync_run.assert_called_once_with({"run": 1})

    def test_reconnects_then_succeeds(self):
        failing = MagicMock()
        failing.sync_run.side_effect = Exception("driver closed")
        ctx = _neo4j_ctx(failing)
        with (
            patch("chaosprobe.orchestrator.strategy_runner.pf") as mock_pf,
            patch("chaosprobe.storage.neo4j_store.Neo4jStore") as MockStore,
        ):
            mock_pf.check_port.return_value = True
            MockStore.return_value = MagicMock()  # reconnected store syncs cleanly
            result = _sync_neo4j(ctx, {"run": 1})
        assert result is True
        assert ctx.graph_store is MockStore.return_value


class TestIsUnknownDominated:
    """A majority (>50%) of Unknown probe verdicts = operator-level probe-
    evaluation failure (retry candidate); real Fail verdicts never qualify."""

    def test_empty_verdicts_not_dominated(self):
        assert _is_unknown_dominated({"probeVerdicts": {}, "unknownProbeCount": 0}) is False

    def test_missing_keys_not_dominated(self):
        assert _is_unknown_dominated({}) is False

    def test_minority_unknown_not_dominated(self):
        ir = {
            "probeVerdicts": {"a": "Pass", "b": "Fail", "c": "Pass", "d": "Unknown"},
            "unknownProbeCount": 1,
        }
        assert _is_unknown_dominated(ir) is False

    def test_exactly_half_not_dominated(self):
        # 2 of 4 is not a strict majority.
        ir = {
            "probeVerdicts": {"a": "Unknown", "b": "Unknown", "c": "Pass", "d": "Fail"},
            "unknownProbeCount": 2,
        }
        assert _is_unknown_dominated(ir) is False

    def test_majority_unknown_is_dominated(self):
        ir = {
            "probeVerdicts": {"a": "Unknown", "b": "Unknown", "c": "Unknown", "d": "Fail"},
            "unknownProbeCount": 3,
        }
        assert _is_unknown_dominated(ir) is True

    def test_cycle3_eleven_of_twelve(self):
        verdicts = {f"p{i}": "Unknown" for i in range(11)}
        verdicts["check-tcp-connect"] = "Fail"
        ir = {"probeVerdicts": verdicts, "unknownProbeCount": 11}
        assert _is_unknown_dominated(ir) is True

    def test_all_real_fail_not_dominated(self):
        ir = {"probeVerdicts": {"a": "Fail", "b": "Fail"}, "unknownProbeCount": 0}
        assert _is_unknown_dominated(ir) is False


class TestRunIterationWithUnknownRetry:
    """Bounded re-measurement of majority-Unknown iterations; taint on exhaustion."""

    @staticmethod
    def _good():
        return {
            "iteration": 1,
            "verdict": "FAIL",
            "resilienceScore": 75,
            "probeVerdicts": {"a": "Pass", "b": "Fail", "c": "Pass"},
            "unknownProbeCount": 0,
        }

    @staticmethod
    def _unknown():
        return {
            "iteration": 1,
            "verdict": "FAIL",
            "resilienceScore": 0,
            "probeVerdicts": {"a": "Unknown", "b": "Unknown", "c": "Fail"},
            "unknownProbeCount": 2,
        }

    def _seq(self, monkeypatch, results):
        it = iter(results)
        monkeypatch.setattr(strategy_runner, "_run_single_iteration", lambda *a, **k: next(it))

    def test_good_first_try_no_retry(self, monkeypatch):
        self._seq(monkeypatch, [self._good()])
        ir = strategy_runner._run_iteration_with_unknown_retry(MagicMock(), "default", {}, 1)
        assert ir["retryCount"] == 0
        assert ir["verdict"] == "FAIL"
        assert "tainted" not in ir

    def test_unknown_then_good_keeps_real_result(self, monkeypatch):
        self._seq(monkeypatch, [self._unknown(), self._good()])
        ir = strategy_runner._run_iteration_with_unknown_retry(MagicMock(), "default", {}, 1)
        assert ir["retryCount"] == 1
        assert ir["verdict"] == "FAIL"
        assert ir["resilienceScore"] == 75
        assert "tainted" not in ir

    def test_persistent_unknown_taints_after_budget(self, monkeypatch):
        # Default budget 2 → 1 initial call + 2 retries = 3 calls, all Unknown.
        self._seq(monkeypatch, [self._unknown(), self._unknown(), self._unknown()])
        ir = strategy_runner._run_iteration_with_unknown_retry(MagicMock(), "default", {}, 1)
        assert ir["retryCount"] == 2
        assert ir["verdict"] == "ERROR"
        assert ir["tainted"] is True
        assert "unknown_probes_after_retries" in ir["taintReasons"]

    def test_taint_after_single_retry_budget_one(self, monkeypatch):
        # budget=1 exercises the singular "retry" wording branch.
        self._seq(monkeypatch, [self._unknown(), self._unknown()])
        ir = strategy_runner._run_iteration_with_unknown_retry(
            MagicMock(), "default", {}, 1, budget=1
        )
        assert ir["retryCount"] == 1
        assert ir["verdict"] == "ERROR"
        assert ir["tainted"] is True

    def test_real_fail_never_retried(self, monkeypatch):
        calls = []

        def fake(*a, **k):
            calls.append(1)
            return {
                "verdict": "FAIL",
                "resilienceScore": 0,
                "probeVerdicts": {"a": "Fail", "b": "Fail"},
                "unknownProbeCount": 0,
            }

        monkeypatch.setattr(strategy_runner, "_run_single_iteration", fake)
        ir = strategy_runner._run_iteration_with_unknown_retry(MagicMock(), "default", {}, 1)
        assert len(calls) == 1
        assert ir["retryCount"] == 0
        assert ir["verdict"] == "FAIL"


class TestRunIterationsRetryWiring:
    """The retry helper is wired into the per-strategy iteration loop."""

    def test_single_iteration_records_retry_count(self, monkeypatch):
        monkeypatch.setattr(
            strategy_runner,
            "_run_single_iteration",
            lambda *a, **k: {
                "iteration": 1,
                "verdict": "PASS",
                "resilienceScore": 100,
                "probeVerdicts": {"a": "Pass"},
                "unknownProbeCount": 0,
            },
        )
        ctx = SimpleNamespace(iterations=1)
        results = strategy_runner._run_iterations(ctx, "default", {})
        assert len(results) == 1
        assert results[0]["retryCount"] == 0
        assert results[0]["verdict"] == "PASS"

    def test_iteration_exception_recorded_as_error(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("k8s down")

        monkeypatch.setattr(strategy_runner, "_run_single_iteration", boom)
        ctx = SimpleNamespace(iterations=1)
        results = strategy_runner._run_iterations(ctx, "default", {})
        assert len(results) == 1
        assert results[0]["verdict"] == "ERROR"
        assert results[0]["retryCount"] == 0
        assert results[0]["error"] == "k8s down"
