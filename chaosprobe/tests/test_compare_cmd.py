"""Tests for the ``chaosprobe compare`` command (commands/compare_cmd.py)."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaosprobe.commands.compare_cmd import compare

_COMPARISON = {
    "comparison": {"resilienceScoreChange": 5.0},
    "conclusion": {"fixEffective": True, "confidence": 0.9},
}


def _write_run(path, data=None):
    path.write_text(json.dumps(data or {"runId": path.stem}))
    return str(path)


def test_compare_file_mode_stdout(tmp_path):
    base = _write_run(tmp_path / "baseline.json")
    after = _write_run(tmp_path / "afterfix.json")
    with patch(
        "chaosprobe.commands.compare_cmd.compare_runs", return_value=_COMPARISON
    ) as cmp_runs:
        result = CliRunner().invoke(compare, [base, after])
    assert result.exit_code == 0
    assert "Comparing JSON files" in result.output
    assert "Fix Effective: True" in result.output
    assert "Resilience Score Change: +5.0" in result.output
    cmp_runs.assert_called_once()


def test_compare_file_mode_writes_output(tmp_path):
    base = _write_run(tmp_path / "baseline.json")
    after = _write_run(tmp_path / "afterfix.json")
    out = tmp_path / "out.json"
    with patch("chaosprobe.commands.compare_cmd.compare_runs", return_value=_COMPARISON):
        result = CliRunner().invoke(compare, [base, after, "-o", str(out)])
    assert result.exit_code == 0
    assert f"Comparison written to {out}" in result.output
    assert json.loads(out.read_text()) == _COMPARISON


def test_compare_file_load_error(tmp_path):
    base = tmp_path / "baseline.json"
    base.write_text("{not valid json")
    after = _write_run(tmp_path / "afterfix.json")
    result = CliRunner().invoke(compare, [str(base), str(after)])
    assert result.exit_code == 1
    assert "Error loading result files" in result.output


def test_compare_neo4j_mode_success():
    store = MagicMock()
    store.get_run_output.side_effect = [{"runId": "a"}, {"runId": "b"}]
    with (
        patch("chaosprobe.commands.compare_cmd.get_graph_store", return_value=store),
        patch("chaosprobe.commands.compare_cmd.compare_runs", return_value=_COMPARISON),
    ):
        result = CliRunner().invoke(compare, ["run-a", "run-b"])
    assert result.exit_code == 0
    assert "Comparing runs from Neo4j" in result.output
    store.close.assert_called_once()


def test_compare_neo4j_baseline_not_found():
    store = MagicMock()
    store.get_run_output.side_effect = [None, {"runId": "b"}]
    with patch("chaosprobe.commands.compare_cmd.get_graph_store", return_value=store):
        result = CliRunner().invoke(compare, ["run-a", "run-b"])
    assert result.exit_code == 1
    assert "run 'run-a' not found" in result.output


def test_compare_neo4j_afterfix_not_found():
    store = MagicMock()
    store.get_run_output.side_effect = [{"runId": "a"}, None]
    with patch("chaosprobe.commands.compare_cmd.get_graph_store", return_value=store):
        result = CliRunner().invoke(compare, ["run-a", "run-b"])
    assert result.exit_code == 1
    assert "run 'run-b' not found" in result.output


def test_compare_no_files_no_neo4j_uri():
    # The --neo4j-uri default is truthy, so the "neither" branch is only
    # reachable by explicitly blanking the URI.
    result = CliRunner().invoke(compare, ["run-a", "run-b", "--neo4j-uri", ""])
    assert result.exit_code == 1
    assert "not existing files and no --neo4j-uri provided" in result.output
