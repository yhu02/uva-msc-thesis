"""Tests for the v2 complete-block session driver (orchestrator/v2_session.py).

Pure-Python per CONTRIBUTING: the affinity engine, fraction solver, and
quiescence barrier are monkeypatched/MagicMocked — no cluster is touched.
Covers the CLI-surface parsers (levels, workers, condition names), the
order-randomization determinism per seed, the condition→solver→engine call
shapes for every (r, mode) cell, the live-fraction recomputation, the
pre-registered rejection rule's taint wiring (taint, never drop), the
restore+quiescence sequencing, the v2Session summary metadata, and the
strategy_runner dispatch hooks.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import click
import pytest

import chaosprobe.orchestrator.v2_session as v2
from chaosprobe.orchestrator import strategy_runner
from chaosprobe.placement.affinity_engine import (
    MODE_ANTI_AFFINE,
    MODE_PACKED,
    ApplyResult,
    ServiceCheck,
    VerificationResult,
)
from chaosprobe.placement.fraction_solver import Solution

WORKERS = ("w1", "w2", "w3")
EDGES = [("a", "b", 1.0), ("b", "c", 1.0)]
SERVICES = ["a", "b", "c"]
LEVELS = (0.0, 0.25, 0.5, 0.75, 1.0)


def _session(**overrides):
    kwargs = dict(
        namespace="ns",
        levels=LEVELS,
        conditions=v2.ordered_conditions(LEVELS, order_seed=42),
        order_seed=42,
        solver_seed=7,
        replicas=1,
        mode=MODE_PACKED,
        workers=WORKERS,
        edges=list(EDGES),
        services=list(SERVICES),
        api=MagicMock(),
    )
    kwargs.update(overrides)
    return v2.V2Session(**kwargs)


def _verification(passed=True):
    return VerificationResult(
        r=1,
        mode=MODE_PACKED,
        passed=passed,
        services=[
            ServiceCheck(
                service="a",
                ok=passed,
                reason="",
                ready_replicas=1,
                nodes=["w1"],
                assigned_node="w1",
            )
        ],
    )


def _solution(assignment, achieved, target, accepted=True):
    return Solution(
        assignment=assignment, achieved_f=achieved, target_f=target, accepted=accepted, trace={}
    )


# ── parse_levels / condition_name ─────────────────────────────────────


class TestParseLevels:
    def test_parses_the_preregistered_grid(self):
        assert v2.parse_levels("0,0.25,0.5,0.75,1.0") == (0.0, 0.25, 0.5, 0.75, 1.0)

    def test_tolerates_whitespace_and_blank_tokens(self):
        assert v2.parse_levels(" 0 , 0.5 ,, 1 ") == (0.0, 0.5, 1.0)

    def test_empty_spec_raises(self):
        with pytest.raises(ValueError, match="at least one fraction"):
            v2.parse_levels(" , ")

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError, match="not a number"):
            v2.parse_levels("0,x")

    @pytest.mark.parametrize("bad", ["-0.1", "1.1"])
    def test_out_of_range_raises(self, bad):
        with pytest.raises(ValueError, match="outside"):
            v2.parse_levels(f"0,{bad}")

    def test_duplicate_level_raises(self):
        with pytest.raises(ValueError, match="duplicate or indistinguishable"):
            v2.parse_levels("0.5,0.5")

    def test_indistinguishable_levels_raise(self):
        # Both round to the f-025 condition name (sub-1% resolution).
        with pytest.raises(ValueError, match="duplicate or indistinguishable"):
            v2.parse_levels("0.251,0.252")


class TestConditionName:
    @pytest.mark.parametrize(
        "level,name",
        [(0.0, "f-000"), (0.25, "f-025"), (0.5, "f-050"), (0.75, "f-075"), (1.0, "f-100")],
    )
    def test_grid_names(self, level, name):
        assert v2.condition_name(level) == name

    def test_names_are_dns_safe(self):
        for level in LEVELS:
            assert "." not in v2.condition_name(level)


# ── parse_workers ─────────────────────────────────────────────────────


class TestParseWorkers:
    def test_preserves_order(self):
        assert v2.parse_workers("w2, w1 ,w3") == ("w2", "w1", "w3")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="at least one node"):
            v2.parse_workers(" , ")

    def test_duplicates_raise(self):
        with pytest.raises(ValueError, match="duplicate"):
            v2.parse_workers("w1,w1")


# ── ordered_conditions (complete block, deterministic per seed) ──────


class TestOrderedConditions:
    def test_same_seed_same_order(self):
        assert v2.ordered_conditions(LEVELS, 42) == v2.ordered_conditions(LEVELS, 42)

    def test_complete_block_visits_every_level_once(self):
        conds = v2.ordered_conditions(LEVELS, 42)
        assert sorted(c.target_f for c in conds) == sorted(LEVELS)
        assert len({c.name for c in conds}) == len(LEVELS)

    def test_different_seeds_can_differ(self):
        orders = {tuple(c.name for c in v2.ordered_conditions(LEVELS, seed)) for seed in range(20)}
        assert len(orders) > 1  # 5! = 120 permutations; 20 seeds collide all-equal ~never

    def test_order_is_a_shuffle_not_a_sort(self):
        # With this seed the applied order differs from the given order
        # (regression guard: the block must not be silently re-sorted).
        conds = v2.ordered_conditions(LEVELS, 1)
        assert [c.target_f for c in conds] != list(LEVELS)


# ── edges_from_routes / discover_services ─────────────────────────────


class TestEdgesFromRoutes:
    def test_filters_to_known_services_and_dedupes(self):
        routes = [
            ("a", "b", "b:50051", "grpc", "a->b"),
            ("a", "b", "b:50051", "grpc", "dup"),
            ("a", "zz", "zz:1", "tcp", "unknown target dropped"),
            ("", "b", "b:1", "tcp", "missing source dropped"),
            ("b", "c", "c:6379", "tcp", "b->c"),
        ]
        assert v2.edges_from_routes(routes, ["a", "b", "c"]) == [
            ("a", "b", 1.0),
            ("b", "c", 1.0),
        ]

    def test_empty_routes_give_no_edges(self):
        assert v2.edges_from_routes([], SERVICES) == []


class TestDiscoverServices:
    def test_excludes_infra_loadgen_and_zero_replicas(self):
        mutator = MagicMock()
        mutator.get_deployments.return_value = [
            SimpleNamespace(name="frontend", replicas=1),
            SimpleNamespace(name="loadgenerator", replicas=1),
            SimpleNamespace(name="chaos-operator-ce", replicas=1),
            SimpleNamespace(name="scaled-down", replicas=0),
            SimpleNamespace(name="cartservice", replicas=1),
        ]
        assert v2.discover_services(mutator) == ["cartservice", "frontend"]


# ── build_session validation ──────────────────────────────────────────


class TestBuildSession:
    def _build(self, **overrides):
        kwargs = dict(
            levels=LEVELS,
            order_seed=42,
            solver_seed=7,
            replicas=1,
            mode=MODE_PACKED,
            workers=WORKERS,
            edges=list(EDGES),
            services=list(SERVICES),
            api=MagicMock(),
        )
        kwargs.update(overrides)
        return v2.build_session("ns", **kwargs)

    def test_builds_with_randomized_conditions(self):
        session = self._build()
        assert [c.name for c in session.conditions] == [
            c.name for c in v2.ordered_conditions(LEVELS, 42)
        ]
        assert session.pinned is True

    def test_unsupported_replicas_raises(self):
        with pytest.raises(ValueError, match="--v2-replicas"):
            self._build(replicas=2)

    def test_unsupported_mode_raises(self):
        with pytest.raises(ValueError, match="--v2-mode"):
            self._build(mode="zigzag")

    def test_anti_affine_needs_enough_workers(self):
        with pytest.raises(ValueError, match="distinct workers"):
            self._build(replicas=3, mode=MODE_ANTI_AFFINE, workers=("w1", "w2"))

    def test_anti_affine_with_enough_workers_is_unpinned(self):
        session = self._build(replicas=3, mode=MODE_ANTI_AFFINE)
        assert session.pinned is False

    def test_r3_packed_is_pinned(self):
        session = self._build(replicas=3, mode=MODE_PACKED)
        assert session.pinned is True

    def test_no_services_raises(self):
        with pytest.raises(ValueError, match="no deployable application services"):
            self._build(services=[])

    def test_no_edges_raises(self):
        with pytest.raises(ValueError, match="no inter-service edges"):
            self._build(edges=[])

    def test_condition_lookup(self):
        session = self._build()
        assert session.condition("f-050") == v2.V2Condition("f-050", 0.5)
        assert session.condition("nope") is None

    def test_defaults_to_solver_packing(self):
        assert self._build().packed_assignment == v2.PACKED_ASSIGNMENT_SOLVER

    def test_accepts_round_robin_packing(self):
        session = self._build(packed_assignment=v2.PACKED_ASSIGNMENT_ROUND_ROBIN)
        assert session.packed_assignment == v2.PACKED_ASSIGNMENT_ROUND_ROBIN

    def test_unsupported_packed_assignment_raises(self):
        with pytest.raises(ValueError, match="packed_assignment must be one of"):
            self._build(packed_assignment="bin-packing")

    def test_defaults_to_no_dns_cache_axis(self):
        assert self._build().dns_cache is None  # C1/C2 have no cache axis

    @pytest.mark.parametrize("mode", ["on", "off"])
    def test_accepts_dns_cache_mode(self, mode):
        assert self._build(dns_cache=mode).dns_cache == mode

    def test_invalid_dns_cache_raises(self):
        with pytest.raises(ValueError, match="--v2-dns-cache must be one of"):
            self._build(dns_cache="warm")


# ── apply_condition: call shapes + sequencing per (r, mode) cell ─────


def _wire_engine(monkeypatch, *, solution, verification, live, pending=None, manager=None):
    """Patch the engine/solver/quiescence seams; return the mocks.

    When ``manager`` is given, the restore/quiesce/solve/apply mocks are
    attached to it so call *order* can be asserted.
    """
    restore = MagicMock()
    quiesce = MagicMock(
        return_value={"quiescent": True, "waitedSeconds": 1.0, "notReady": [], "restarts": 0}
    )
    solve = MagicMock(return_value=solution)
    apply_mock = MagicMock(
        return_value=ApplyResult(applied=SERVICES, pending=pending or [], duration_seconds=2.5)
    )
    verify = MagicMock(return_value=verification)
    live_nodes = MagicMock(return_value=live)
    monkeypatch.setattr(v2.engine, "restore", restore)
    monkeypatch.setattr(v2.quiescence, "wait_for_quiescence", quiesce)
    monkeypatch.setattr(v2.fs, "solve", solve)
    monkeypatch.setattr(v2.engine, "apply_placement", apply_mock)
    monkeypatch.setattr(v2.engine, "verify_placement", verify)
    monkeypatch.setattr(v2.engine, "live_service_nodes", live_nodes)
    if manager is not None:
        manager.attach_mock(restore, "restore")
        manager.attach_mock(quiesce, "quiesce")
        manager.attach_mock(solve, "solve")
        manager.attach_mock(apply_mock, "apply")
        manager.attach_mock(verify, "verify")
    return restore, quiesce, solve, apply_mock, verify, live_nodes


class TestApplyConditionPinned:
    def test_solver_to_engine_call_shape_and_acceptance(self, monkeypatch):
        session = _session()
        manager = MagicMock()
        restore, quiesce, solve, apply_mock, verify, live_nodes = _wire_engine(
            monkeypatch,
            solution=_solution({"a": 0, "b": 0, "c": 1}, achieved=0.5, target=0.5),
            verification=_verification(passed=True),
            live={"a": ["w1"], "b": ["w1"], "c": ["w2"]},
            manager=manager,
        )
        placement = v2.apply_condition(session, v2.V2Condition("f-050", 0.5))

        solve.assert_called_once_with(session.edges, session.services, 3, 0.5, seed=7)
        apply_mock.assert_called_once_with(
            session.api,
            "ns",
            {"a": "w1", "b": "w1", "c": "w2"},
            1,
            MODE_PACKED,
            list(WORKERS),
            timeout=session.rollout_timeout,
            services=None,
        )
        verify.assert_called_once_with(session.api, "ns", 1, MODE_PACKED)
        live_nodes.assert_called_once_with(session.api, "ns", session.services)
        # Restore + quiescence run BEFORE solve/apply (session-level restore
        # between conditions, then the m1b barrier).
        names = [c[0] for c in manager.mock_calls]
        assert names.index("restore") < names.index("quiesce") < names.index("solve")
        assert names.index("solve") < names.index("apply") < names.index("verify")

        record = session.per_level["f-050"]
        assert record["accepted"] is True
        assert record["rejectionReasons"] == []
        assert record["solverAchievedF"] == 0.5
        assert record["liveAchievedF"] == 0.5  # 1 of 2 edges crosses w1|w2
        assert record["gap"] == 0.0
        assert record["schedulingLatencySeconds"] == 2.5
        assert record["settle"]["quiescent"] is True
        assert placement["strategy"] == "f-050"
        assert placement["assignments"] == {"a": "w1", "b": "w1", "c": "w2"}
        assert placement["v2"] is record

    def test_fraction_target_missed_rejects(self, monkeypatch):
        session = _session()
        _wire_engine(
            monkeypatch,
            solution=_solution({"a": 0, "b": 1, "c": 2}, achieved=1.0, target=0.0, accepted=False),
            verification=_verification(passed=True),
            live={"a": ["w1"], "b": ["w2"], "c": ["w3"]},  # live f = 1.0, target 0.0
        )
        v2.apply_condition(session, v2.V2Condition("f-000", 0.0))
        record = session.per_level["f-000"]
        assert record["accepted"] is False
        assert record["rejectionReasons"] == ["fraction_target_missed"]
        assert record["liveAchievedF"] == 1.0
        assert record["solverAccepted"] is False

    def test_unverifiable_live_state_rejects(self, monkeypatch):
        session = _session()
        _wire_engine(
            monkeypatch,
            solution=_solution({"a": 0, "b": 0, "c": 0}, achieved=0.0, target=0.0),
            verification=_verification(passed=True),
            live={"a": ["w1"], "b": [], "c": ["w1", "w2"]},  # b gone, c split
        )
        v2.apply_condition(session, v2.V2Condition("f-000", 0.0))
        record = session.per_level["f-000"]
        assert record["accepted"] is False
        assert record["rejectionReasons"] == ["live_fraction_unverifiable:b,c"]
        assert record["liveAchievedF"] is None

    def test_verification_failure_rejects(self, monkeypatch):
        session = _session()
        _wire_engine(
            monkeypatch,
            solution=_solution({"a": 0, "b": 0, "c": 1}, achieved=0.5, target=0.5),
            verification=_verification(passed=False),
            live={"a": ["w1"], "b": ["w1"], "c": ["w2"]},
        )
        v2.apply_condition(session, v2.V2Condition("f-050", 0.5))
        record = session.per_level["f-050"]
        assert record["accepted"] is False
        assert record["rejectionReasons"] == ["placement_verification_failed"]
        assert record["liveAchievedF"] == 0.5  # still recorded, never dropped

    def test_pending_rollout_is_recorded(self, monkeypatch):
        session = _session()
        _wire_engine(
            monkeypatch,
            solution=_solution({"a": 0, "b": 0, "c": 1}, achieved=0.5, target=0.5),
            verification=_verification(passed=True),
            live={"a": ["w1"], "b": ["w1"], "c": ["w2"]},
            pending=["b"],
        )
        v2.apply_condition(session, v2.V2Condition("f-050", 0.5))
        assert session.per_level["f-050"]["pendingDeployments"] == ["b"]

    def test_unquiescent_start_proceeds_but_is_recorded(self, monkeypatch):
        session = _session()
        _, quiesce, _, _, _, _ = _wire_engine(
            monkeypatch,
            solution=_solution({"a": 0, "b": 0, "c": 1}, achieved=0.5, target=0.5),
            verification=_verification(passed=True),
            live={"a": ["w1"], "b": ["w1"], "c": ["w2"]},
        )
        quiesce.return_value = {
            "quiescent": False,
            "waitedSeconds": 300.0,
            "notReady": ["b"],
            "restarts": 4,
        }
        v2.apply_condition(session, v2.V2Condition("f-050", 0.5))
        record = session.per_level["f-050"]
        assert record["settle"]["quiescent"] is False
        assert record["accepted"] is True  # quiescence timeout never rejects on its own

    def test_r3_packed_solves_and_pins(self, monkeypatch):
        session = _session(replicas=3, mode=MODE_PACKED)
        _, _, solve, apply_mock, verify, _ = _wire_engine(
            monkeypatch,
            solution=_solution({"a": 0, "b": 0, "c": 1}, achieved=0.5, target=0.5),
            verification=VerificationResult(r=3, mode=MODE_PACKED, passed=True, services=[]),
            live={"a": ["w1"], "b": ["w1"], "c": ["w2"]},
        )
        v2.apply_condition(session, v2.V2Condition("f-050", 0.5))
        solve.assert_called_once()
        assert apply_mock.call_args.args[2:6] == (
            {"a": "w1", "b": "w1", "c": "w2"},
            3,
            MODE_PACKED,
            list(WORKERS),
        )
        # verify_placement passed=True but services=[] → engine semantics say
        # FAIL only when checks exist and fail; VerificationResult above says
        # passed=True, so the condition is judged on the live fraction.
        assert session.per_level["f-050"]["accepted"] is True


class TestApplyConditionRoundRobinPacked:
    """V2-H3 packed-cell semantics: capacity-feasible round-robin, f-independent."""

    def test_round_robin_bypasses_solver_and_f_gate(self, monkeypatch):
        session = _session(
            replicas=3, mode=MODE_PACKED, packed_assignment=v2.PACKED_ASSIGNMENT_ROUND_ROBIN
        )
        _, _, solve, apply_mock, verify, live_nodes = _wire_engine(
            monkeypatch,
            solution=_solution({"a": 0}, achieved=0.0, target=0.5),  # must be ignored
            verification=VerificationResult(r=3, mode=MODE_PACKED, passed=True, services=[]),
            live={"a": ["w1"], "b": ["w2"], "c": ["w3"]},  # live f = 1.0 ≠ target 0.5
        )
        v2.apply_condition(session, v2.V2Condition("f-050", 0.5))
        # The fraction solver is never consulted — round-robin owns the pins.
        solve.assert_not_called()
        # sorted(a,b,c) → w1,w2,w3 (i mod W).
        assert apply_mock.call_args.args[2:6] == (
            {"a": "w1", "b": "w2", "c": "w3"},
            3,
            MODE_PACKED,
            list(WORKERS),
        )
        record = session.per_level["f-050"]
        assert record["packedAssignmentMethod"] == v2.PACKED_ASSIGNMENT_ROUND_ROBIN
        assert record["solverAccepted"] is None
        assert record["solverAchievedF"] == 1.0  # achieved f of the round-robin
        # f is irrelevant to V2-H3: no gap is recorded and the off-target live
        # fraction never rejects the cell.
        assert "gap" not in record
        assert record["accepted"] is True
        assert record["rejectionReasons"] == []

    def test_round_robin_still_rejects_unverifiable_packing(self, monkeypatch):
        # A genuine packing failure (a service split across nodes / gone) is
        # still caught — acceptance rests on verify_placement, not on f.
        session = _session(
            replicas=3, mode=MODE_PACKED, packed_assignment=v2.PACKED_ASSIGNMENT_ROUND_ROBIN
        )
        _wire_engine(
            monkeypatch,
            solution=_solution({"a": 0}, achieved=0.0, target=0.5),
            verification=VerificationResult(r=3, mode=MODE_PACKED, passed=True, services=[]),
            live={"a": ["w1"], "b": [], "c": ["w1", "w2"]},  # b gone, c split
        )
        v2.apply_condition(session, v2.V2Condition("f-050", 0.5))
        record = session.per_level["f-050"]
        assert record["accepted"] is False
        assert record["rejectionReasons"] == ["live_fraction_unverifiable:b,c"]


class TestApplyConditionDnsCache:
    """C3 / V2-H2 cache axis: applied after placement, recorded, no-op when unset."""

    def _wire_dns(self, monkeypatch, pending=None):
        apply_dns = MagicMock(
            return_value=SimpleNamespace(applied=["a"], pending=pending or [], duration_seconds=4.2)
        )
        monkeypatch.setattr(v2.dns, "apply_dns_cache", apply_dns)
        return apply_dns

    def test_cache_applied_after_placement_and_recorded(self, monkeypatch):
        session = _session(dns_cache="off")
        _wire_engine(
            monkeypatch,
            solution=_solution({"a": 0, "b": 0, "c": 1}, achieved=0.5, target=0.5),
            verification=_verification(passed=True),
            live={"a": ["w1"], "b": ["w1"], "c": ["w2"]},
        )
        apply_dns = self._wire_dns(monkeypatch)
        v2.apply_condition(session, v2.V2Condition("f-050", 0.5))
        # cache applied to the session's services with the session's mode.
        api, ns, services, mode = apply_dns.call_args.args[:4]
        assert ns == "ns" and mode == "off" and sorted(services) == sorted(session.services)
        record = session.per_level["f-050"]
        assert record["dnsCache"] == "off"
        assert record["dnsCacheLatencySeconds"] == 4.2
        assert record["accepted"] is True  # placement still adjudicated

    def test_cache_pending_folds_into_pending(self, monkeypatch):
        session = _session(dns_cache="off")
        _wire_engine(
            monkeypatch,
            solution=_solution({"a": 0, "b": 0, "c": 1}, achieved=0.5, target=0.5),
            verification=_verification(passed=True),
            live={"a": ["w1"], "b": ["w1"], "c": ["w2"]},
            pending=["b"],  # placement-pending
        )
        self._wire_dns(monkeypatch, pending=["c"])  # cache-pending
        v2.apply_condition(session, v2.V2Condition("f-050", 0.5))
        assert session.per_level["f-050"]["pendingDeployments"] == ["b", "c"]

    def test_no_cache_axis_skips_dns(self, monkeypatch):
        session = _session()  # dns_cache None (C1/C2)
        _wire_engine(
            monkeypatch,
            solution=_solution({"a": 0, "b": 0, "c": 1}, achieved=0.5, target=0.5),
            verification=_verification(passed=True),
            live={"a": ["w1"], "b": ["w1"], "c": ["w2"]},
        )
        apply_dns = self._wire_dns(monkeypatch)
        v2.apply_condition(session, v2.V2Condition("f-050", 0.5))
        apply_dns.assert_not_called()
        assert "dnsCache" not in session.per_level["f-050"]


class TestApplyConditionPartialRecord:
    def test_mid_apply_exception_keeps_partial_record(self, monkeypatch):
        # apply_placement raising must not erase the already-gathered settle
        # + solver evidence: the per-level record stays committed, marked
        # incomplete instead of "condition_not_executed".
        session = _session()
        _, _, _, apply_mock, _, _ = _wire_engine(
            monkeypatch,
            solution=_solution({"a": 0, "b": 0, "c": 1}, achieved=0.5, target=0.5),
            verification=_verification(passed=True),
            live={"a": ["w1"], "b": ["w1"], "c": ["w2"]},
        )
        apply_mock.side_effect = RuntimeError("rollout timed out")
        with pytest.raises(RuntimeError, match="rollout timed out"):
            v2.apply_condition(session, v2.V2Condition("f-050", 0.5))
        record = session.per_level["f-050"]
        assert record["accepted"] is False
        assert record["rejectionReasons"] == ["condition_apply_incomplete"]
        assert record["solverAchievedF"] == 0.5  # solver evidence preserved
        assert record["settle"]["quiescent"] is True  # settle evidence preserved
        # session_metadata reports the partial record, not "not executed".
        meta = v2.session_metadata(session)
        entry = next(e for e in meta["perLevel"] if e["condition"] == "f-050")
        assert entry["rejectionReasons"] == ["condition_apply_incomplete"]


class TestApplyConditionAntiAffine:
    def test_no_solve_no_assignment_no_live_fraction(self, monkeypatch):
        session = _session(replicas=3, mode=MODE_ANTI_AFFINE)
        _, _, solve, apply_mock, verify, live_nodes = _wire_engine(
            monkeypatch,
            solution=_solution({}, achieved=0.0, target=0.0),
            verification=VerificationResult(r=3, mode=MODE_ANTI_AFFINE, passed=True, services=[]),
            live={},
        )
        placement = v2.apply_condition(session, v2.V2Condition("f-000", 0.0))
        solve.assert_not_called()
        live_nodes.assert_not_called()
        apply_mock.assert_called_once_with(
            session.api,
            "ns",
            None,
            3,
            MODE_ANTI_AFFINE,
            list(WORKERS),
            timeout=session.rollout_timeout,
            # The session's own service set: the engine must not re-discover
            # (and resurrect) deployments the session excluded.
            services=list(SERVICES),
        )
        record = session.per_level["f-000"]
        assert record["solverAchievedF"] is None
        assert record["liveAchievedF"] is None
        assert record["accepted"] is True
        assert placement["assignments"] == {}

    def test_acceptance_rests_on_verification(self, monkeypatch):
        session = _session(replicas=3, mode=MODE_ANTI_AFFINE)
        _wire_engine(
            monkeypatch,
            solution=_solution({}, achieved=0.0, target=0.0),
            verification=VerificationResult(r=3, mode=MODE_ANTI_AFFINE, passed=False, services=[]),
            live={},
        )
        v2.apply_condition(session, v2.V2Condition("f-000", 0.0))
        record = session.per_level["f-000"]
        assert record["accepted"] is False
        assert record["rejectionReasons"] == ["placement_verification_failed"]


# ── iteration_live_fraction ───────────────────────────────────────────


class TestIterationLiveFraction:
    def test_r1_fraction_from_pod_placements(self):
        session = _session()
        pods = {"a-7d9f-x1": "w1", "b-5c2a-y2": "w1", "c-9e1b-z3": "w2"}
        assert v2.iteration_live_fraction(session, pods) == 0.5

    def test_r3_packed_multiple_pods_same_node_ok(self):
        session = _session(replicas=3, mode=MODE_PACKED)
        pods = {
            "a-1a-p1": "w1",
            "a-1a-p2": "w1",
            "a-1a-p3": "w1",
            "b-2b-p1": "w2",
            "b-2b-p2": "w2",
            "b-2b-p3": "w2",
            "c-3c-p1": "w2",
            "c-3c-p2": "w2",
            "c-3c-p3": "w2",
        }
        # a|b crosses (w1|w2), b|c does not → f = 0.5
        assert v2.iteration_live_fraction(session, pods) == 0.5

    def test_service_spanning_nodes_is_unverifiable(self):
        session = _session()
        pods = {"a-1a-p1": "w1", "a-1a-p2": "w2", "b-2b-p1": "w1", "c-3c-p1": "w1"}
        assert v2.iteration_live_fraction(session, pods) is None

    def test_anti_affine_has_no_live_fraction(self):
        session = _session(replicas=3, mode=MODE_ANTI_AFFINE)
        assert v2.iteration_live_fraction(session, {"a-1a-p1": "w1"}) is None

    def test_empty_map_is_none(self):
        assert v2.iteration_live_fraction(_session(), {}) is None

    def test_unknown_services_and_nodeless_pods_skipped(self):
        session = _session()
        pods = {
            "chaos-runner-1a-p1": "w1",  # not a session service
            "a-1a-p1": "",  # no node yet
        }
        # No edge has both endpoints assigned → fraction undefined → None.
        assert v2.iteration_live_fraction(session, pods) is None


# ── annotate_iteration (taint, never drop) ────────────────────────────


def _accepted_record(session, name="f-050", target=0.5, accepted=True, reasons=None):
    record = {
        "condition": name,
        "targetF": target,
        "accepted": accepted,
        "rejectionReasons": reasons or [],
        "perIteration": [],
    }
    session.per_level[name] = record
    return record


class TestAnnotateIteration:
    def test_in_tolerance_iteration_is_untainted(self):
        session = _session()
        record = _accepted_record(session)
        ir = {
            "iteration": 1,
            "preChaosHealthy": True,
            "preChaosTaintReasons": [],
            "podPlacements": {"a-1a-p1": "w1", "b-2b-p1": "w1", "c-3c-p1": "w2"},
        }
        v2.annotate_iteration(session, "f-050", ir)
        assert record["perIteration"] == [
            {"iteration": 1, "liveAchievedF": 0.5, "taintReasons": []}
        ]
        assert ir["preChaosHealthy"] is True
        assert "tainted" not in ir

    def test_drifted_live_fraction_taints(self):
        session = _session()
        record = _accepted_record(session, target=0.0, name="f-000")
        ir = {
            "iteration": 2,
            "preChaosHealthy": True,
            "preChaosTaintReasons": [],
            "podPlacements": {"a-1a-p1": "w1", "b-2b-p1": "w2", "c-3c-p1": "w3"},  # f = 1.0
        }
        v2.annotate_iteration(session, "f-000", ir)
        assert record["perIteration"][0]["liveAchievedF"] == 1.0
        assert record["perIteration"][0]["taintReasons"] == ["v2_live_fraction_drifted"]
        assert ir["preChaosHealthy"] is False
        assert ir["preChaosTaintReasons"] == ["v2_live_fraction_drifted"]
        assert ir["tainted"] is True
        assert ir["taintReasons"] == ["v2_live_fraction_drifted"]

    def test_round_robin_off_target_live_fraction_is_not_drift(self):
        # V2-H3 round-robin packing achieves an f far from the condition's
        # nominal target by design — that is not a drift, so no taint.
        session = _session(packed_assignment=v2.PACKED_ASSIGNMENT_ROUND_ROBIN)
        record = _accepted_record(session, target=0.0, name="f-000")
        ir = {
            "iteration": 1,
            "preChaosHealthy": True,
            "preChaosTaintReasons": [],
            "podPlacements": {"a-1a-p1": "w1", "b-2b-p1": "w2", "c-3c-p1": "w3"},  # f = 1.0
        }
        v2.annotate_iteration(session, "f-000", ir)
        assert record["perIteration"][0]["liveAchievedF"] == 1.0
        assert record["perIteration"][0]["taintReasons"] == []
        assert ir["preChaosHealthy"] is True
        assert "tainted" not in ir

    def test_round_robin_still_taints_unverifiable_packing(self):
        # The packing-integrity check (each service's replicas on one node) is
        # NOT an f-target check, so it still applies under round-robin.
        session = _session(packed_assignment=v2.PACKED_ASSIGNMENT_ROUND_ROBIN)
        record = _accepted_record(session)
        ir = {"iteration": 1, "preChaosHealthy": True, "podPlacements": {}}
        v2.annotate_iteration(session, "f-050", ir)
        assert record["perIteration"][0]["liveAchievedF"] is None
        assert ir["preChaosTaintReasons"] == ["v2_live_fraction_unverifiable"]
        assert ir["tainted"] is True

    def test_prechaos_taint_is_folded_into_perIteration_record(self):
        # The non-v2 (strategy-runner) pre-chaos taint must appear in the
        # persisted perIteration record — it is the only per-iteration taint
        # channel a node-drain session persists. An iteration with no v2 reason
        # of its own that the runner flagged app_ready_timeout must still record
        # it, so c2_h3_anova's _rejection_reason can exclude it.
        session = _session()
        record = _accepted_record(session)
        ir = {
            "iteration": 1,
            "preChaosHealthy": False,
            "preChaosTaintReasons": ["app_ready_timeout"],
            # in-tolerance placement → no v2 reason of its own
            "podPlacements": {"a-1a-p1": "w1", "b-2b-p1": "w1", "c-3c-p1": "w2"},
        }
        v2.annotate_iteration(session, "f-050", ir)
        assert record["perIteration"][0]["taintReasons"] == ["app_ready_timeout"]

    def test_iteration_taintReasons_channel_is_also_folded(self):
        # The runner can taint via ir["taintReasons"] directly (e.g.
        # unknown_probes_after_retries) without a preChaosTaintReasons entry;
        # that reason must also reach the persisted perIteration record.
        session = _session()
        record = _accepted_record(session)
        ir = {
            "iteration": 1,
            "preChaosHealthy": True,
            "preChaosTaintReasons": [],
            "taintReasons": ["unknown_probes_after_retries"],
            "podPlacements": {"a-1a-p1": "w1", "b-2b-p1": "w1", "c-3c-p1": "w2"},
        }
        v2.annotate_iteration(session, "f-050", ir)
        assert record["perIteration"][0]["taintReasons"] == ["unknown_probes_after_retries"]

    def test_prechaos_and_v2_taints_are_unioned_deduped(self):
        # Pre-chaos reasons first, then new v2 reasons, no duplicates.
        session = _session()
        record = _accepted_record(session, target=0.0, name="f-000")
        ir = {
            "iteration": 1,
            "preChaosHealthy": False,
            "preChaosTaintReasons": ["app_ready_timeout"],
            "podPlacements": {"a-1a-p1": "w1", "b-2b-p1": "w2", "c-3c-p1": "w3"},  # f=1.0 → drift
        }
        v2.annotate_iteration(session, "f-000", ir)
        assert record["perIteration"][0]["taintReasons"] == [
            "app_ready_timeout",
            "v2_live_fraction_drifted",
        ]

    def test_rejected_condition_taints_every_iteration(self):
        session = _session()
        _accepted_record(session, accepted=False, reasons=["fraction_target_missed"])
        ir = {
            "iteration": 1,
            "preChaosHealthy": True,
            "preChaosTaintReasons": ["app_ready_timeout"],
            "podPlacements": {"a-1a-p1": "w1", "b-2b-p1": "w1", "c-3c-p1": "w2"},
        }
        v2.annotate_iteration(session, "f-050", ir)
        assert ir["preChaosHealthy"] is False
        # Existing reasons preserved, v2 reason appended once.
        assert ir["preChaosTaintReasons"] == ["app_ready_timeout", "v2_condition_rejected"]

    def test_unverifiable_iteration_taints(self):
        session = _session()
        record = _accepted_record(session)
        ir = {"iteration": 3, "preChaosHealthy": True, "podPlacements": {}}
        v2.annotate_iteration(session, "f-050", ir)
        assert record["perIteration"][0]["liveAchievedF"] is None
        assert ir["preChaosTaintReasons"] == ["v2_live_fraction_unverifiable"]
        assert ir["tainted"] is True

    def test_anti_affine_untainted_without_live_fraction(self):
        session = _session(replicas=3, mode=MODE_ANTI_AFFINE)
        record = _accepted_record(session, name="f-000", target=0.0)
        ir = {"iteration": 1, "preChaosHealthy": True, "podPlacements": {}}
        v2.annotate_iteration(session, "f-000", ir)
        assert record["perIteration"][0]["liveAchievedF"] is None
        assert ir["preChaosHealthy"] is True
        assert "tainted" not in ir

    def test_unexecuted_condition_is_a_noop(self):
        session = _session()
        ir = {"iteration": 1, "preChaosHealthy": True}
        v2.annotate_iteration(session, "f-050", ir)
        assert ir == {"iteration": 1, "preChaosHealthy": True}

    def test_duplicate_reasons_not_doubled(self):
        session = _session()
        _accepted_record(session, accepted=False, reasons=["fraction_target_missed"])
        ir = {
            "iteration": 1,
            "preChaosHealthy": False,
            "preChaosTaintReasons": ["v2_condition_rejected"],
            "tainted": True,
            "taintReasons": ["v2_condition_rejected"],
            "podPlacements": {"a-1a-p1": "w1", "b-2b-p1": "w1", "c-3c-p1": "w2"},
        }
        v2.annotate_iteration(session, "f-050", ir)
        assert ir["preChaosTaintReasons"] == ["v2_condition_rejected"]
        assert ir["taintReasons"] == ["v2_condition_rejected"]


# ── session_metadata ──────────────────────────────────────────────────


class TestSessionMetadata:
    def test_full_shape(self):
        session = _session()
        executed = _accepted_record(session, name=session.conditions[0].name)
        meta = v2.session_metadata(session)
        assert meta["levels"] == list(LEVELS)
        assert meta["orderApplied"] == [c.target_f for c in session.conditions]
        assert meta["conditionOrder"] == [c.name for c in session.conditions]
        assert meta["orderSeed"] == 42
        assert meta["solverSeed"] == 7
        assert meta["replicas"] == 1
        assert meta["mode"] == MODE_PACKED
        assert meta["packedAssignment"] == v2.PACKED_ASSIGNMENT_SOLVER
        assert meta["dnsCache"] is None  # no cache axis in this session
        assert meta["workers"] == list(WORKERS)
        assert meta["tolerance"] == v2.TOLERANCE
        assert meta["perLevel"][0] is executed

    def test_unexecuted_conditions_are_explicit(self):
        session = _session()
        meta = v2.session_metadata(session)
        assert len(meta["perLevel"]) == len(LEVELS)
        for cond, entry in zip(session.conditions, meta["perLevel"]):
            assert entry["condition"] == cond.name
            assert entry["targetF"] == cond.target_f
            assert entry["accepted"] is False
            assert entry["rejectionReasons"] == ["condition_not_executed"]


# ── strategy_runner dispatch hooks ────────────────────────────────────


class TestStrategyRunnerDispatch:
    def test_apply_placement_routes_v2_conditions(self, monkeypatch):
        placement = {"strategy": "f-050", "assignments": {}, "v2": {}}
        apply_condition = MagicMock(return_value=placement)
        monkeypatch.setattr(strategy_runner, "apply_condition", apply_condition)
        session = _session()
        ctx = SimpleNamespace(v2_session=session)
        result = {}
        strategy_runner._apply_placement(ctx, "f-050", result)
        apply_condition.assert_called_once_with(session, session.condition("f-050"))
        assert result["placement"] is placement

    def test_apply_placement_rejects_unknown_condition(self):
        ctx = SimpleNamespace(v2_session=_session())
        with pytest.raises(click.ClickException, match="unknown v2 condition"):
            strategy_runner._apply_placement(ctx, "spread", {})

    def test_apply_placement_without_session_takes_v1_path(self):
        # The v1 named-strategy path is untouched: with no session the
        # dispatch falls through to the mutator-based flow.
        ctx = SimpleNamespace(v2_session=None, mutator=MagicMock())
        result = {}
        strategy_runner._apply_placement(ctx, "baseline", result)
        ctx.mutator.clear_placement.assert_called_once_with(wait=True, timeout=120)
        assert result["placement"]["strategy"] == "baseline"

    def test_run_iterations_annotates_each_iteration(self, monkeypatch):
        ir = {
            "iteration": 1,
            "verdict": "PASS",
            "resilienceScore": 100,
            "probeVerdicts": {"a": "Pass"},
            "unknownProbeCount": 0,
        }
        monkeypatch.setattr(strategy_runner, "_run_single_iteration", lambda *a, **k: dict(ir))
        annotate = MagicMock()
        monkeypatch.setattr(strategy_runner, "annotate_iteration", annotate)
        session = _session()
        ctx = SimpleNamespace(iterations=1, v2_session=session)
        results = strategy_runner._run_iterations(ctx, "f-050", {})
        annotate.assert_called_once_with(session, "f-050", results[0])

    def _single_ir(self, **overrides):
        ir = {
            "iteration": 1,
            "verdict": "PASS",
            "resilienceScore": 90,
            "probeVerdicts": {"p": "Pass"},
            "metrics": {},
            "runId": "r1",
        }
        ir.update(overrides)
        return ir

    def test_single_iteration_taint_surfaces_in_experiment_block(self):
        # With -i 1 there is no aggregate_iterations pass; the v2 rejection
        # rule's taint must still be visible in the strategies block.
        ir = self._single_ir(
            preChaosHealthy=False,
            preChaosTaintReasons=["v2_live_fraction_drifted"],
            tainted=True,
            taintReasons=["v2_live_fraction_drifted"],
        )
        sr = {}
        strategy_runner._aggregate_strategy(SimpleNamespace(iterations=1), "f-050", sr, [ir])
        assert sr["experiment"]["tainted"] is True
        assert sr["experiment"]["taintReasons"] == ["v2_live_fraction_drifted"]

    def test_single_iteration_clean_run_has_no_taint_keys(self):
        sr = {}
        strategy_runner._aggregate_strategy(
            SimpleNamespace(iterations=1), "f-050", sr, [self._single_ir(preChaosHealthy=True)]
        )
        assert "tainted" not in sr["experiment"]
        assert "taintReasons" not in sr["experiment"]

    def test_run_iterations_annotates_error_iterations_too(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("k8s down")

        monkeypatch.setattr(strategy_runner, "_run_single_iteration", boom)
        annotate = MagicMock()
        monkeypatch.setattr(strategy_runner, "annotate_iteration", annotate)
        session = _session()
        ctx = SimpleNamespace(iterations=1, v2_session=session)
        results = strategy_runner._run_iterations(ctx, "f-050", {})
        assert results[0]["verdict"] == "ERROR"
        annotate.assert_called_once_with(session, "f-050", results[0])
