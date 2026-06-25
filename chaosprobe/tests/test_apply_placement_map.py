"""Tests for scripts/apply_placement_map.py — M1a live-validation helper.

Pure-Python per CONTRIBUTING: the Kubernetes mutator is a MagicMock; no
cluster is touched.
"""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "apply_placement_map.py"
_spec = importlib.util.spec_from_file_location("apply_placement_map", _SCRIPT)
assert _spec is not None and _spec.loader is not None
apm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apm)


# ── parse_map ─────────────────────────────────────────────────────────


def test_parse_map_from_json_string():
    assert apm.parse_map('{"frontend": "worker1"}', None) == {"frontend": "worker1"}


def test_parse_map_from_file(tmp_path):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"cartservice": "worker2"}))
    assert apm.parse_map(None, str(path)) == {"cartservice": "worker2"}


@pytest.mark.parametrize("map_json, map_file", [(None, None), ('{"a": "w1"}', "f.json")])
def test_parse_map_requires_exactly_one_source(map_json, map_file):
    with pytest.raises(ValueError, match="exactly one"):
        apm.parse_map(map_json, map_file)


@pytest.mark.parametrize("payload", ["{}", '["worker1"]'])
def test_parse_map_rejects_non_object_or_empty(payload):
    with pytest.raises(ValueError, match="non-empty JSON object"):
        apm.parse_map(payload, None)


# ── apply_map ─────────────────────────────────────────────────────────


def test_apply_map_patches_each_deployment_in_sorted_order():
    mutator = MagicMock()
    apm.apply_map(mutator, {"frontend": "w1", "cartservice": "w2"}, wait=False, timeout=300)
    assert mutator._patch_deployment_placement.call_args_list == [
        call("cartservice", "w2", apm.STRATEGY_NAME),
        call("frontend", "w1", apm.STRATEGY_NAME),
    ]
    mutator._wait_for_rollouts.assert_not_called()


def test_apply_map_waits_for_rollouts_when_asked():
    mutator = MagicMock()
    apm.apply_map(mutator, {"a": "w1", "b": "w2"}, wait=True, timeout=120)
    mutator._wait_for_rollouts.assert_called_once_with(["a", "b"], 120)


# ── live_assignment / graph_edges ────────────────────────────────────


def test_live_assignment_maps_pods_back_to_services():
    mutator = MagicMock()
    mutator.observe_pod_placements.return_value = {
        "frontend-69c8bdd469-nhhrn": "worker3",
        "cartservice-7cdc597bd8-6kq9q": "worker2",
    }
    assert apm.live_assignment(mutator, ["cartservice", "frontend"]) == {
        "frontend": "worker3",
        "cartservice": "worker2",
    }
    mutator.observe_pod_placements.assert_called_once_with(["cartservice", "frontend"])


def test_graph_edges_prefers_summary(tmp_path):
    summary = {
        "strategies": {
            "default": {
                "iterations": [{"podPlacements": {"a-aa1-bb2": "w1", "b-aa1-bb2": "w2"}}],
                "aggregated": {"routeViewAggregate": [{"route": "a->b"}]},
            }
        }
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary))
    edges, source = apm.graph_edges(MagicMock(), str(path))
    assert edges == [("a", "b", 1.0)]
    assert "summary" in source


def test_graph_edges_falls_back_to_live_discovery():
    mutator = MagicMock()
    mutator.get_service_dependencies.return_value = [("a", "b"), ("a", "b"), ("b", "c")]
    edges, source = apm.graph_edges(mutator, None)
    assert edges == [("a", "b", 1.0), ("b", "c", 1.0)]  # deduplicated, uniform weights
    assert "live env-var discovery" in source


# ── report ────────────────────────────────────────────────────────────


def test_report_prints_fraction_and_flags_mismatches(capsys):
    mapping = {"a": "w1", "b": "w2"}
    actual = {"a": "w1", "b": "w1"}  # b landed elsewhere
    apm.report(mapping, actual, [("a", "b", 1.0)], "test graph", target=None)
    out = capsys.readouterr().out
    assert "MISMATCH" in out
    assert "achieved cross-node fraction: 0.0000" in out


def test_report_grades_against_target(capsys):
    apm.report({"a": "w1", "b": "w2"}, {"a": "w1", "b": "w2"}, [("a", "b", 1.0)], "g", target=1.0)
    out = capsys.readouterr().out
    assert "achieved cross-node fraction: 1.0000" in out
    assert "ACCEPTED" in out


def test_report_rejects_target_miss(capsys):
    apm.report({"a": "w1", "b": "w2"}, {"a": "w1", "b": "w2"}, [("a", "b", 1.0)], "g", target=0.0)
    assert "REJECTED" in capsys.readouterr().out


def test_report_handles_undefined_fraction(capsys):
    apm.report({"a": "w1"}, {}, [("a", "b", 1.0)], "g", target=0.5)
    out = capsys.readouterr().out
    assert "undefined" in out
    assert "ACCEPTED" not in out and "REJECTED" not in out


def test_report_service_missing_from_actual_shows_dash(capsys):
    apm.report({"a": "w1", "b": "w2"}, {"a": "w1"}, [("a", "b", 1.0)], "g", target=None)
    out = capsys.readouterr().out
    assert "-" in out and "MISMATCH" in out


# ── main ──────────────────────────────────────────────────────────────


def _patched_mutator(monkeypatch):
    mutator = MagicMock()
    monkeypatch.setattr(apm, "PlacementMutator", MagicMock(return_value=mutator))
    return mutator


def test_main_applies_map_and_reports(monkeypatch, capsys):
    mutator = _patched_mutator(monkeypatch)
    mutator.observe_pod_placements.return_value = {
        "a-aa1-bb2": "worker1",
        "b-aa1-bb2": "worker2",
    }
    mutator.get_service_dependencies.return_value = [("a", "b")]
    apm.main(["--map", '{"a": "worker1", "b": "worker2"}', "--target", "1.0"])
    out = capsys.readouterr().out
    assert "Pinning 2 deployment(s) in namespace 'online-boutique'" in out
    assert "achieved cross-node fraction: 1.0000" in out
    assert "ACCEPTED" in out
    apm.PlacementMutator.assert_called_once_with("online-boutique")
    assert mutator._patch_deployment_placement.call_count == 2
    mutator._wait_for_rollouts.assert_not_called()  # no --wait


def test_main_wait_and_custom_namespace(monkeypatch, capsys):
    mutator = _patched_mutator(monkeypatch)
    mutator.observe_pod_placements.return_value = {"a-aa1-bb2": "w1", "b-aa1-bb2": "w1"}
    mutator.get_service_dependencies.return_value = [("a", "b")]
    apm.main(["--map", '{"a": "w1", "b": "w1"}', "-n", "other-ns", "--wait", "--timeout", "60"])
    apm.PlacementMutator.assert_called_once_with("other-ns")
    mutator._wait_for_rollouts.assert_called_once_with(["a", "b"], 60)
    assert "achieved cross-node fraction: 0.0000" in capsys.readouterr().out


def test_main_uses_summary_graph_when_given(monkeypatch, tmp_path, capsys):
    mutator = _patched_mutator(monkeypatch)
    mutator.observe_pod_placements.return_value = {"a-aa1-bb2": "w1", "b-aa1-bb2": "w2"}
    summary = {
        "strategies": {
            "default": {
                "iterations": [{"podPlacements": {"a-aa1-bb2": "w1", "b-aa1-bb2": "w2"}}],
                "aggregated": {"routeViewAggregate": [{"route": "a->b"}]},
            }
        }
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary))
    apm.main(["--map", '{"a": "w1", "b": "w2"}', "--summary", str(path)])
    out = capsys.readouterr().out
    assert f"summary {path}" in out
    mutator.get_service_dependencies.assert_not_called()


def test_main_restore_clears_placement(monkeypatch, capsys):
    mutator = _patched_mutator(monkeypatch)
    mutator.clear_placement.return_value = ["a", "b", "c"]
    apm.main(["--restore", "--wait"])
    mutator.clear_placement.assert_called_once_with(wait=True, timeout=300)
    assert "Restored default scheduling for 3 deployment(s)" in capsys.readouterr().out
    mutator._patch_deployment_placement.assert_not_called()


def test_main_restore_conflicts_with_map(monkeypatch):
    _patched_mutator(monkeypatch)
    with pytest.raises(SystemExit, match="--restore takes no"):
        apm.main(["--restore", "--map", '{"a": "w1"}'])


def test_main_rejects_missing_map(monkeypatch):
    _patched_mutator(monkeypatch)
    with pytest.raises(SystemExit, match="exactly one"):
        apm.main([])


def test_main_map_file(monkeypatch, tmp_path, capsys):
    mutator = _patched_mutator(monkeypatch)
    mutator.observe_pod_placements.return_value = {"a-aa1-bb2": "w1", "b-aa1-bb2": "w2"}
    mutator.get_service_dependencies.return_value = [("a", "b")]
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps({"a": "w1", "b": "w2"}))
    apm.main(["--map-file", str(map_file)])
    assert "achieved cross-node fraction: 1.0000" in capsys.readouterr().out
