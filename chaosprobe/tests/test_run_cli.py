"""Tests for `chaosprobe run`'s placement-session surface (the session driver CLI).

Pure-Python per CONTRIBUTING: every cluster seam is monkeypatched. Covers the
argument resolution helper (mutual exclusivity with -s/--seeds/--replicas,
parse errors, defaults, the deterministic condition block), the session
initializer, the end-of-run restore helper, the Click parameter-source
detection, and one fully-mocked end-to-end `run` invocation proving the placement
conditions ride the strategy loop in their randomized order and that
``summary.json`` gains the ``session`` block.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

from chaosprobe.commands import run_cmd
from chaosprobe.commands.run_cmd import (
    _init_placement_session,
    _resolve_placement_args,
    _restore_placements,
    _selfheal_dns,
    _strategies_overridden_on_cli,
    run,
)
from chaosprobe.orchestrator import session as session_driver
from chaosprobe.placement import affinity_engine

LEVELS = "0,0.25,0.5,0.75,1.0"
WORKERS = "w1,w2,w3"


def _resolve(**overrides):
    kwargs = dict(
        fraction_levels=LEVELS,
        order_seed=None,
        solver_seed=None,
        replica_degree=None,
        placement_mode=None,
        worker_nodes=WORKERS,
        packed_assignment=None,
        dns_cache=None,
        strategies_overridden=False,
        seeds=None,
        scale_replicas=0,
        experiments=("pod-delete.yaml",),
    )
    kwargs.update(overrides)
    return _resolve_placement_args(
        kwargs["fraction_levels"],
        kwargs["order_seed"],
        kwargs["solver_seed"],
        kwargs["replica_degree"],
        kwargs["placement_mode"],
        kwargs["worker_nodes"],
        kwargs["packed_assignment"],
        kwargs["dns_cache"],
        strategies_overridden=kwargs["strategies_overridden"],
        seeds=kwargs["seeds"],
        scale_replicas=kwargs["scale_replicas"],
        experiments=kwargs["experiments"],
    )


@pytest.mark.parametrize(
    "alias,param",
    [
        ("--v2-levels", "fraction_levels"),
        ("--v2-order-seed", "order_seed"),
        ("--v2-solver-seed", "solver_seed"),
        ("--v2-replicas", "replica_degree"),
        ("--v2-mode", "placement_mode"),
        ("--v2-workers", "worker_nodes"),
        ("--v2-packed-assignment", "packed_assignment"),
        ("--v2-dns-cache", "dns_cache"),
    ],
)
def test_legacy_v2_cli_aliases_map_to_renamed_params(alias, param):
    """Back-compat: the old ``--v2-*`` flags remain accepted aliases of the
    renamed placement-session flags, so pre-existing campaign scripts and the
    reproduction commands cited in the deposits keep working
    (DEVIATIONS D-2026-06-25-01)."""
    by_name = {p.name: p.opts for p in run_cmd.run.params}
    assert param in by_name, f"param {param} missing from the run command"
    assert alias in by_name[param], f"{alias} is not registered as an alias of {param}"


class TestResolvePlacementArgs:
    def test_no_placement_flags_returns_none(self):
        assert _resolve(fraction_levels=None, worker_nodes=None) is None

    @pytest.mark.parametrize(
        "flag,overrides",
        [
            ("--order-seed", {"order_seed": 1}),
            ("--solver-seed", {"solver_seed": 1}),
            ("--replica-degree", {"replica_degree": 3}),
            ("--placement-mode", {"placement_mode": "packed"}),
            ("--worker-nodes", {"worker_nodes": WORKERS}),
            ("--packed-assignment", {"packed_assignment": "round-robin"}),
            ("--dns-cache", {"dns_cache": "off"}),
        ],
    )
    def test_placement_flags_without_levels_raise(self, flag, overrides):
        kwargs = {"fraction_levels": None, "worker_nodes": None}
        kwargs.update(overrides)
        with pytest.raises(click.ClickException, match=rf"{flag}.*require\(s\) --fraction-levels"):
            _resolve(**kwargs)

    def test_explicit_strategies_conflict(self):
        with pytest.raises(click.ClickException, match="mutually exclusive with -s"):
            _resolve(strategies_overridden=True)

    def test_seeds_conflict(self):
        with pytest.raises(click.ClickException, match="mutually exclusive with --seeds"):
            _resolve(seeds="1,2")

    def test_replicas_conflict(self):
        with pytest.raises(click.ClickException, match="mutually exclusive with --replicas"):
            _resolve(scale_replicas=3)

    def test_workers_required(self):
        with pytest.raises(click.ClickException, match="requires --worker-nodes"):
            _resolve(worker_nodes=None)

    def test_level_parse_error_becomes_click_exception(self):
        with pytest.raises(click.ClickException, match="not a number"):
            _resolve(fraction_levels="0,x")

    def test_worker_parse_error_becomes_click_exception(self):
        with pytest.raises(click.ClickException, match="duplicate node names"):
            _resolve(worker_nodes="w1,w1")

    def test_invalid_replicas_raises(self):
        with pytest.raises(click.ClickException, match="--replica-degree must be one of"):
            _resolve(replica_degree=2)

    def test_multi_fault_matrix_rejected(self):
        # A placement session is one fault, one complete block — the per-level
        # records are keyed by condition, so a second fault would silently
        # overwrite the first fault's data.
        with pytest.raises(click.ClickException, match="exactly one fault per session"):
            _resolve(experiments=("pod-delete.yaml", "cpu-hog.yaml"))

    def test_anti_affine_worker_arity_raises(self):
        with pytest.raises(click.ClickException, match="needs at least 3 workers"):
            _resolve(replica_degree=3, placement_mode="anti-affine", worker_nodes="w1,w2")

    def test_defaults_applied(self):
        args = _resolve()
        assert args is not None
        assert args.order_seed == run_cmd._DEFAULT_ORDER_SEED
        assert args.solver_seed == run_cmd._DEFAULT_SOLVER_SEED
        assert args.replicas == run_cmd._DEFAULT_REPLICAS
        assert args.mode == affinity_engine.MODE_PACKED
        assert args.levels == (0.0, 0.25, 0.5, 0.75, 1.0)
        assert args.workers == ("w1", "w2", "w3")
        assert args.packed_assignment == run_cmd._DEFAULT_PACKED_ASSIGNMENT
        assert args.dns_cache is None  # no cache axis unless requested (C1/C2)

    def test_round_robin_packing_resolves(self):
        args = _resolve(packed_assignment="round-robin")
        assert args is not None
        assert args.packed_assignment == "round-robin"

    def test_invalid_packed_assignment_raises(self):
        with pytest.raises(click.ClickException, match="--packed-assignment must be one of"):
            _resolve(packed_assignment="bin-packing")

    @pytest.mark.parametrize("mode", ["on", "off"])
    def test_dns_cache_resolves(self, mode):
        args = _resolve(dns_cache=mode)
        assert args is not None and args.dns_cache == mode

    def test_invalid_dns_cache_raises(self):
        with pytest.raises(click.ClickException, match="--dns-cache must be one of"):
            _resolve(dns_cache="warm")

    def test_conditions_match_order_seed(self):
        args = _resolve(order_seed=7)
        assert args is not None
        assert args.conditions == session_driver.ordered_conditions(args.levels, 7)

    def test_explicit_seeds_and_cell(self):
        args = _resolve(order_seed=9, solver_seed=3, replica_degree=3, placement_mode="anti-affine")
        assert args is not None
        assert (args.order_seed, args.solver_seed) == (9, 3)
        assert (args.replicas, args.mode) == (3, "anti-affine")


class TestStrategiesOverriddenOnCli:
    def test_no_active_context_is_false(self):
        assert _strategies_overridden_on_cli() is False

    def test_explicit_s_flag_conflicts_via_cli(self):
        result = CliRunner().invoke(
            run, ["--fraction-levels", LEVELS, "--worker-nodes", WORKERS, "-s", "spread"]
        )
        assert result.exit_code != 0
        assert "mutually exclusive with -s" in result.output

    def test_default_strategies_do_not_conflict(self, monkeypatch):
        # Without -s the placement path proceeds past resolution (and then fails on
        # the worker-less follow-up validation we feed it, proving the
        # exclusivity check did not fire on the *default* strategies value).
        result = CliRunner().invoke(run, ["--fraction-levels", LEVELS])
        assert "mutually exclusive with -s" not in result.output
        assert "requires --worker-nodes" in result.output


class TestInitPlacementSession:
    def _args(self):
        args = _resolve()
        assert args is not None
        return args

    def _mutator(self):
        mutator = MagicMock()
        mutator.get_deployments.return_value = [
            SimpleNamespace(name="a", replicas=1),
            SimpleNamespace(name="b", replicas=1),
            SimpleNamespace(name="c", replicas=1),
        ]
        return mutator

    def test_builds_session_from_topology(self, monkeypatch):
        api = MagicMock()
        monkeypatch.setattr(
            run_cmd.affinity_engine.K8sApi, "from_cluster", classmethod(lambda cls: api)
        )
        routes = [("a", "b", "b:1", "grpc", "a->b"), ("b", "c", "c:1", "tcp", "b->c")]
        session = _init_placement_session(self._args(), "ns", self._mutator(), routes)
        assert session.namespace == "ns"
        assert session.services == ["a", "b", "c"]
        assert session.edges == [("a", "b", 1.0), ("b", "c", 1.0)]
        assert session.api is api
        assert session.conditions == self._args().conditions

    def test_no_topology_raises(self, monkeypatch):
        monkeypatch.setattr(
            run_cmd.affinity_engine.K8sApi, "from_cluster", classmethod(lambda cls: MagicMock())
        )
        with pytest.raises(click.ClickException, match="no inter-service edges"):
            _init_placement_session(self._args(), "ns", self._mutator(), None)

    def test_falls_back_to_static_topology_when_no_env_edges(self, monkeypatch, tmp_path):
        # Consul/gRPC workloads (hotelReservation) have no *_SERVICE_ADDR env
        # deps → edges_from_routes is empty; the placement session must fall back to the
        # static topology.json adjacent to the scenario so the fraction is defined.
        api = MagicMock()
        monkeypatch.setattr(
            run_cmd.affinity_engine.K8sApi, "from_cluster", classmethod(lambda cls: api)
        )
        (tmp_path / "topology.json").write_text(
            json.dumps({"services": ["a", "b", "c"], "edges": [["a", "b"], ["b", "c"]]})
        )
        session = _init_placement_session(
            self._args(),
            "ns",
            self._mutator(),
            None,  # no env-var routes
            {"path": str(tmp_path / "pod-delete.yaml")},
        )
        assert session.edges == [("a", "b", 1.0), ("b", "c", 1.0)]

    def test_env_edges_take_precedence_over_static_topology(self, monkeypatch, tmp_path):
        # When env-var routes already yield edges, the static fallback is NOT used
        # (Online Boutique keeps its measured/env graph).
        api = MagicMock()
        monkeypatch.setattr(
            run_cmd.affinity_engine.K8sApi, "from_cluster", classmethod(lambda cls: api)
        )
        # A topology.json with DIFFERENT edges — must be ignored when env edges exist.
        (tmp_path / "topology.json").write_text(
            json.dumps({"services": ["a", "b", "c"], "edges": [["a", "c"]]})
        )
        routes = [("a", "b", "b:1", "grpc", "a->b"), ("b", "c", "c:1", "tcp", "b->c")]
        session = _init_placement_session(
            self._args(), "ns", self._mutator(), routes, {"path": str(tmp_path / "x.yaml")}
        )
        assert session.edges == [("a", "b", 1.0), ("b", "c", 1.0)]

    def test_no_routes_and_no_adjacent_topology_still_raises(self, monkeypatch, tmp_path):
        # Scenario dir without a topology.json → the helpful error is preserved.
        monkeypatch.setattr(
            run_cmd.affinity_engine.K8sApi, "from_cluster", classmethod(lambda cls: MagicMock())
        )
        with pytest.raises(click.ClickException, match="no inter-service edges"):
            _init_placement_session(
                self._args(), "ns", self._mutator(), None, {"path": str(tmp_path / "x.yaml")}
            )

    def test_static_edges_filtered_to_live_services(self, monkeypatch, tmp_path):
        # An edge endpoint that is not a live discovered service (mutator yields
        # a,b,c) is dropped, so the solver never validates against a phantom and
        # raises mid-run. Here b->d is dropped, a->b survives.
        api = MagicMock()
        monkeypatch.setattr(
            run_cmd.affinity_engine.K8sApi, "from_cluster", classmethod(lambda cls: api)
        )
        (tmp_path / "topology.json").write_text(
            json.dumps({"services": ["a", "b", "c", "d"], "edges": [["a", "b"], ["b", "d"]]})
        )
        session = _init_placement_session(
            self._args(), "ns", self._mutator(), None, {"path": str(tmp_path / "x.yaml")}
        )
        assert session.edges == [("a", "b", 1.0)]

    def test_malformed_adjacent_topology_raises_click_exception(self, monkeypatch, tmp_path):
        # A structurally unsound topology.json must surface as a ClickException
        # (normalized like every other failure here), not a raw ValueError traceback.
        monkeypatch.setattr(
            run_cmd.affinity_engine.K8sApi, "from_cluster", classmethod(lambda cls: MagicMock())
        )
        (tmp_path / "topology.json").write_text(
            json.dumps({"services": ["a", "b"], "edges": []})  # empty edges → ValueError
        )
        with pytest.raises(click.ClickException):
            _init_placement_session(
                self._args(), "ns", self._mutator(), None, {"path": str(tmp_path / "x.yaml")}
            )


class TestRestorePlacements:
    def _session(self):
        return session_driver.Session(
            namespace="ns",
            levels=(0.0,),
            conditions=session_driver.ordered_conditions((0.0,), 42),
            order_seed=42,
            solver_seed=0,
            replicas=1,
            mode="packed",
            workers=("w1",),
            edges=[("a", "b", 1.0)],
            services=["a", "b"],
            api=MagicMock(),
        )

    def test_restores_via_engine(self, monkeypatch):
        restore = MagicMock()
        monkeypatch.setattr(run_cmd.affinity_engine, "restore", restore)
        session = self._session()
        _restore_placements(session, "ns")
        restore.assert_called_once_with(session.api, "ns", wait=False)

    def test_restore_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            run_cmd.affinity_engine, "restore", MagicMock(side_effect=RuntimeError("api down"))
        )
        _restore_placements(self._session(), "ns")  # must not raise

    def test_c3_session_also_resets_dns_to_cache_on(self, monkeypatch):
        monkeypatch.setattr(run_cmd.affinity_engine, "restore", MagicMock())
        reset = MagicMock()
        monkeypatch.setattr(run_cmd.dns_cache_engine, "apply_dns_cache", reset)
        session = self._session()
        session.dns_cache = "off"  # a C3 session
        _restore_placements(session, "ns")
        api, ns, services, mode = reset.call_args.args[:4]
        assert ns == "ns" and mode == run_cmd.dns_cache_engine.CACHE_ON

    def test_non_c3_session_does_not_touch_dns(self, monkeypatch):
        monkeypatch.setattr(run_cmd.affinity_engine, "restore", MagicMock())
        reset = MagicMock()
        monkeypatch.setattr(run_cmd.dns_cache_engine, "apply_dns_cache", reset)
        _restore_placements(self._session(), "ns")  # dns_cache is None
        reset.assert_not_called()

    def test_dns_reset_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(run_cmd.affinity_engine, "restore", MagicMock())
        monkeypatch.setattr(
            run_cmd.dns_cache_engine,
            "apply_dns_cache",
            MagicMock(side_effect=RuntimeError("api down")),
        )
        session = self._session()
        session.dns_cache = "off"
        _restore_placements(session, "ns")  # must not raise


class TestSelfhealDns:
    """Startup DNS self-heal — recovers a cache-off override a prior aborted run left."""

    def _session(self):
        return TestRestorePlacements._session(self)

    def test_resets_to_cache_on_for_any_session(self, monkeypatch):
        reset = MagicMock()
        monkeypatch.setattr(run_cmd.dns_cache_engine, "apply_dns_cache", reset)
        session = self._session()  # dns_cache None — self-heal still runs (heals a prior leak)
        _selfheal_dns(session, "ns")
        api, ns, services, mode = reset.call_args.args[:4]
        assert ns == "ns" and mode == run_cmd.dns_cache_engine.CACHE_ON
        assert reset.call_args.kwargs.get("wait") is False  # don't block startup

    def test_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            run_cmd.dns_cache_engine,
            "apply_dns_cache",
            MagicMock(side_effect=RuntimeError("api down")),
        )
        _selfheal_dns(self._session(), "ns")  # must not raise


class TestRunPlacementEndToEnd:
    """Fully-mocked `run` invocations: conditions ride the strategy loop."""

    def _invoke(self, tmp_path, monkeypatch, cli_args=None):
        scenario = {"namespace": "demo", "experiments": []}
        routes = [("a", "b", "b:1", "grpc", "a->b"), ("b", "c", "c:1", "tcp", "b->c")]

        monkeypatch.setattr(run_cmd, "_acquire_run_lock", lambda: None)
        monkeypatch.setattr(run_cmd, "ensure_k8s_config", lambda: None)
        monkeypatch.setattr(run_cmd, "k8s_client", MagicMock())
        monkeypatch.setattr(
            run_cmd,
            "_load_and_prepare_scenario",
            lambda *a, **k: (scenario, "demo", Path("pod-delete.yaml"), routes),
        )
        monkeypatch.setattr(
            run_cmd,
            "_build_fault_scenarios",
            lambda *a, **k: [("pod-delete", scenario, ["pod-delete"])],
        )
        monkeypatch.setattr(run_cmd, "_ensure_litmus_setup", lambda *a, **k: True)
        mutator = MagicMock()
        mutator.get_deployments.return_value = [
            SimpleNamespace(name="a", replicas=1),
            SimpleNamespace(name="b", replicas=1),
            SimpleNamespace(name="c", replicas=1),
        ]
        monkeypatch.setattr(run_cmd, "PlacementMutator", MagicMock(return_value=mutator))
        monkeypatch.setattr(run_cmd, "MetricsCollector", MagicMock())
        monkeypatch.setattr(
            run_cmd.affinity_engine.K8sApi, "from_cluster", classmethod(lambda cls: MagicMock())
        )
        monkeypatch.setattr(run_cmd, "_clear_stale_placement", MagicMock())
        monkeypatch.setattr(
            run_cmd,
            "run_preflight_checks",
            lambda *a, **k: {
                "core_api": MagicMock(),
                "chaoscenter_config": None,
                "target_url": "http://localhost:8080",
                "frontend_pf_port": 8080,
            },
        )
        monkeypatch.setattr(run_cmd, "extract_load_service", lambda scn: "frontend")
        monkeypatch.setattr(run_cmd, "extract_target_deployment", lambda scn: "frontend")
        monkeypatch.setattr(run_cmd, "gather_run_metadata", lambda core_api: {})
        monkeypatch.setattr(run_cmd, "hash_scenario_files", lambda scn: [])
        monkeypatch.setattr(run_cmd, "_connect_graph_store", lambda *a, **k: None)
        monkeypatch.setattr(run_cmd, "_snapshot_node_usage_for_bestfit", lambda *a, **k: {})
        monkeypatch.setattr(run_cmd, "_prepull_probe_images_onto_workers", MagicMock())
        monkeypatch.setattr(run_cmd, "_cleanup_conntrack_samplers", MagicMock())
        restore = MagicMock()
        monkeypatch.setattr(run_cmd, "_restore_placements", restore)

        executed = []
        contexts = []

        def fake_execute(ctx, strategy_name, idx, total):
            executed.append((strategy_name, idx, total, ctx.session))
            contexts.append(ctx)
            return (
                {
                    "strategy": strategy_name,
                    "status": "completed",
                    "placement": None,
                    "experiment": None,
                    "metrics": None,
                    "error": None,
                },
                True,
            )

        monkeypatch.setattr(run_cmd, "execute_strategy", fake_execute)
        written = {}

        def fake_write(overall_results, *a, **k):
            written["results"] = overall_results

        monkeypatch.setattr(run_cmd, "write_run_results", fake_write)

        if cli_args is None:
            cli_args = [
                "--fraction-levels",
                LEVELS,
                "--worker-nodes",
                WORKERS,
                "--order-seed",
                "7",
                "--solver-seed",
                "3",
            ]
        result = CliRunner().invoke(
            run,
            ["-n", "demo", "-o", str(tmp_path), "-e", "pod-delete.yaml", *cli_args],
            catch_exceptions=False,
        )
        return result, executed, written, restore, contexts

    def test_conditions_ride_strategy_loop_in_randomized_order(self, tmp_path, monkeypatch):
        result, executed, written, restore, _contexts = self._invoke(tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        expected = [
            c.name
            for c in session_driver.ordered_conditions((0.0, 0.25, 0.5, 0.75, 1.0), 7)
        ]
        assert [name for name, _i, _t, _s in executed] == expected
        # Every condition carried the same live session on the RunContext.
        assert all(s is not None for *_x, s in executed)
        assert len({id(s) for *_x, s in executed}) == 1

    def test_summary_gains_session_block(self, tmp_path, monkeypatch):
        result, executed, written, restore, _contexts = self._invoke(tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        meta = written["results"]["session"]
        assert meta["orderSeed"] == 7
        assert meta["solverSeed"] == 3
        assert meta["levels"] == [0.0, 0.25, 0.5, 0.75, 1.0]
        assert meta["replicas"] == 1
        assert meta["mode"] == "packed"
        assert meta["workers"] == ["w1", "w2", "w3"]
        # No condition reached its placement step (execute_strategy mocked),
        # so every per-level entry is an explicit not-executed record.
        assert all(e["rejectionReasons"] == ["condition_not_executed"] for e in meta["perLevel"])

    def test_placement_restore_runs_at_cleanup(self, tmp_path, monkeypatch):
        result, _executed, _written, restore, _contexts = self._invoke(tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        restore.assert_called_once()

    def test_v1_path_is_untouched(self, tmp_path, monkeypatch):
        # Without the placement-session flags the run takes the named-strategy path:
        # no session, no session block, no engine restore.
        result, executed, written, restore, _contexts = self._invoke(
            tmp_path, monkeypatch, cli_args=["-s", "baseline,default"]
        )
        assert result.exit_code == 0, result.output
        assert [name for name, _i, _t, _s in executed] == ["baseline", "default"]
        assert all(s is None for *_x, s in executed)
        assert "session" not in written["results"]
        restore.assert_not_called()

    def test_app_ready_timeout_flag_flows_to_run_context(self, tmp_path, monkeypatch):
        # The remaining link in the value flow: a user-supplied
        # --app-ready-timeout must reach RunContext construction (guards the
        # `app_ready_timeout=app_ready_timeout` pass-through in run() — removing
        # it would otherwise still pass the rest of the suite).
        result, _executed, _written, _restore, contexts = self._invoke(
            tmp_path, monkeypatch, cli_args=["-s", "baseline", "--app-ready-timeout", "400"]
        )
        assert result.exit_code == 0, result.output
        assert contexts and all(c.app_ready_timeout == 400 for c in contexts)

    def test_app_ready_timeout_defaults_to_240_on_run_context(self, tmp_path, monkeypatch):
        # Omitting the flag leaves the OB-suited 240s default on the context.
        result, _executed, _written, _restore, contexts = self._invoke(
            tmp_path, monkeypatch, cli_args=["-s", "baseline"]
        )
        assert result.exit_code == 0, result.output
        assert contexts and all(c.app_ready_timeout == 240 for c in contexts)

    def test_v1_unknown_strategy_still_rejected(self):
        result = CliRunner().invoke(run, ["-n", "demo", "-s", "bogus"])
        assert result.exit_code == 1
        assert "Unknown strategy 'bogus'" in result.output
