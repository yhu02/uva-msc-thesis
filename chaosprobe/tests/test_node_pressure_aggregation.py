"""Tests for the per-strategy node-pressure event roll-up in
``aggregate_iterations``.

The thesis claim that one placement is harder on the cluster than
another rests on signals like "the colocate node hit MemoryPressure on
3/5 iterations, spread never did".  Per-iteration nodeInfo already
captured the condition status; this aggregation lifts the comparison to
the per-strategy summary.
"""

from chaosprobe.orchestrator.run_phases import (
    _aggregate_node_pressure_events,
    aggregate_iterations,
)


def _iter_single_node(conditions, mean_r=1000.0):
    return {
        "resilienceScore": 80.0,
        "verdict": "PASS",
        "metrics": {
            "recovery": {"summary": {"meanRecovery_ms": mean_r}},
            "nodeInfo": {"nodeName": "w1", "conditions": conditions},
        },
    }


def _iter_multi_node(per_node_conditions, mean_r=1000.0):
    return {
        "resilienceScore": 80.0,
        "verdict": "PASS",
        "metrics": {
            "recovery": {"summary": {"meanRecovery_ms": mean_r}},
            "nodeInfoAll": {
                name: {"nodeName": name, "conditions": conds}
                for name, conds in per_node_conditions.items()
            },
        },
    }


def _ok():
    """Healthy node — every pressure condition has status False."""
    return {
        "MemoryPressure": {"status": "False"},
        "DiskPressure": {"status": "False"},
        "PIDPressure": {"status": "False"},
        "NetworkUnavailable": {"status": "False"},
    }


def _pressured(*types):
    out = _ok()
    for t in types:
        out[t] = {"status": "True"}
    return out


class TestNodePressureAggregationSingleNode:
    def test_no_node_info_returns_no_block(self):
        agg = aggregate_iterations(
            [
                {
                    "resilienceScore": 80,
                    "verdict": "PASS",
                    "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1000}}},
                }
            ]
        )
        assert "nodePressureEvents" not in agg

    def test_clean_iterations_emit_zeroed_block(self):
        agg = aggregate_iterations([_iter_single_node(_ok()), _iter_single_node(_ok())])
        events = agg["nodePressureEvents"]
        for cond in ("MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable"):
            assert events[cond] == {"iterationsWithEvent": 0, "totalNodeEvents": 0}

    def test_counts_iterations_and_node_events(self):
        iters = [
            _iter_single_node(_pressured("MemoryPressure")),
            _iter_single_node(_ok()),
            _iter_single_node(_pressured("MemoryPressure", "DiskPressure")),
        ]
        events = aggregate_iterations(iters)["nodePressureEvents"]
        assert events["MemoryPressure"]["iterationsWithEvent"] == 2
        assert events["MemoryPressure"]["totalNodeEvents"] == 2
        assert events["DiskPressure"]["iterationsWithEvent"] == 1


class TestNodePressureAggregationMultiNode:
    def test_fan_out_recorded_in_total_node_events(self):
        """Three workers all under MemoryPressure in one iteration:
        iterationsWithEvent=1, totalNodeEvents=3."""
        iters = [
            _iter_multi_node(
                {
                    "w1": _pressured("MemoryPressure"),
                    "w2": _pressured("MemoryPressure"),
                    "w3": _pressured("MemoryPressure"),
                }
            )
        ]
        events = aggregate_iterations(iters)["nodePressureEvents"]
        assert events["MemoryPressure"]["iterationsWithEvent"] == 1
        assert events["MemoryPressure"]["totalNodeEvents"] == 3

    def test_node_info_all_preferred_over_single_node_info(self):
        """If both fields exist, the All variant wins (single is a subset)."""
        ir = _iter_multi_node({"w1": _ok(), "w2": _pressured("DiskPressure")})
        ir["metrics"]["nodeInfo"] = {"nodeName": "w1", "conditions": _ok()}
        events = aggregate_iterations([ir])["nodePressureEvents"]
        # DiskPressure should be counted from w2, not missed because the
        # single-node field was clean.
        assert events["DiskPressure"]["totalNodeEvents"] == 1
        assert events["DiskPressure"]["iterationsWithEvent"] == 1

    def test_empty_node_info_all_falls_back_to_single(self):
        ir = _iter_multi_node({})
        ir["metrics"]["nodeInfo"] = {
            "nodeName": "w1",
            "conditions": _pressured("MemoryPressure"),
        }
        events = aggregate_iterations([ir])["nodePressureEvents"]
        assert events["MemoryPressure"]["totalNodeEvents"] == 1


class TestDirectHelper:
    def test_returns_empty_dict_on_empty_input(self):
        assert _aggregate_node_pressure_events([]) == {}

    def test_handles_malformed_condition_entries(self):
        ir = _iter_single_node({"MemoryPressure": "not-a-dict"})
        # Helper should silently skip the bad entry rather than crash.
        events = aggregate_iterations([ir])["nodePressureEvents"]
        assert events["MemoryPressure"]["totalNodeEvents"] == 0

    def test_each_iteration_counts_at_most_once_per_condition(self):
        """When a condition fires on multiple nodes within the same
        iteration, iterationsWithEvent must still count 1 — fan-out
        belongs in totalNodeEvents."""
        ir = _iter_multi_node(
            {
                "w1": _pressured("MemoryPressure"),
                "w2": _pressured("MemoryPressure"),
            }
        )
        events = aggregate_iterations([ir])["nodePressureEvents"]
        assert events["MemoryPressure"]["iterationsWithEvent"] == 1
        assert events["MemoryPressure"]["totalNodeEvents"] == 2
