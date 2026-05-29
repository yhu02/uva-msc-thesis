"""Tests for the ``chaosprobe report`` thesis-appendix CLI command."""

import json
from pathlib import Path

from click.testing import CliRunner

from chaosprobe.commands.report_cmd import (
    _render_doctor_section,
    _render_stats_section,
    _render_summarize_section,
    report,
)
from chaosprobe.output import SCHEMA_VERSION


def _clean_strategy(values, recovery_mean, ci):
    return {
        "iterations": [
            {"resilienceScore": v, "recoveryTime_ms": recovery_mean + i * 10}
            for i, v in enumerate(values)
        ],
        "aggregated": {
            "meanResilienceScore": sum(values) / len(values),
            "stddevResilienceScore": 1.0,
            "p25ResilienceScore": min(values),
            "harmonicMeanResilienceScore": min(values) - 1,
            "meanResilienceScore_ci95": ci,
            "meanRecoveryTime_ms": recovery_mean,
            "stddevRecoveryTime_ms": 50.0,
            "medianRecoveryTime_ms": recovery_mean,
            "maxRecoveryTime_ms": recovery_mean + 100,
            "p95RecoveryTime_ms": recovery_mean + 80,
        },
    }


def _summary_payload():
    return {
        "schemaVersion": SCHEMA_VERSION,
        "runMetadata": {
            "git": {"commit": "abc123", "shortCommit": "abc123", "dirty": False},
            "kubernetes": {
                "serverVersion": "v1.28.6",
                "containerRuntimeOnFirstNode": "containerd",
            },
            "cniHint": "calico",
        },
        "strategies": {
            # Non-overlapping CIs (84–88 vs. 58–62) so the cross-strategy
            # "every CI overlaps" check doesn't fire.
            "spread": _clean_strategy(
                [85, 86, 84, 87, 85, 86, 84, 87, 85, 86],
                recovery_mean=1000,
                ci={"low": 84, "high": 88},
            ),
            "colocate": _clean_strategy(
                [60, 61, 59, 62, 60, 61, 59, 62, 60, 61],
                recovery_mean=2000,
                ci={"low": 58, "high": 62},
            ),
        },
    }


def _write_summary(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "summary.json"
    p.write_text(json.dumps(payload))
    return p


class TestRenderDoctorSection:
    def test_clean_summary_says_no_issues(self):
        out = _render_doctor_section(_summary_payload())
        assert "## Data quality (doctor)" in out
        assert "No issues across 2 strategies" in out

    def test_missing_run_metadata_surfaces_warn(self):
        payload = _summary_payload()
        del payload["runMetadata"]
        out = _render_doctor_section(payload)
        assert "### run metadata" in out
        assert "runMetadata absent" in out

    def test_missing_schema_version_surfaces_warn(self):
        payload = _summary_payload()
        del payload["schemaVersion"]
        out = _render_doctor_section(payload)
        assert "### schema version" in out
        assert "schemaVersion missing" in out


class TestRenderSummarizeSection:
    def test_renders_each_strategy_as_subsection(self):
        out = _render_summarize_section(_summary_payload())
        assert "## Per-strategy aggregate (summarize)" in out
        assert "### colocate" in out
        assert "### spread" in out
        # body is fenced
        assert "```" in out
        # iteration count comes through
        assert "iterations: 10" in out

    def test_empty_strategies_explicit(self):
        out = _render_summarize_section({"strategies": {}})
        assert "No strategies present" in out


class TestRenderStatsSection:
    def test_emits_stats_block_when_strategies_have_metric(self):
        out = _render_stats_section(_summary_payload(), confidence=0.95, seed=0)
        assert "## Statistical analysis (stats)" in out
        # _format_markdown writes the metric label as a heading; resilience
        # score is the only metric these fixtures carry.
        assert "Resilience score" in out or "resilience" in out.lower()

    def test_no_strategies_says_so(self):
        out = _render_stats_section({"strategies": {}}, confidence=0.95, seed=0)
        assert "No strategies carry any supported metric" in out


class TestReportCommand:
    def test_writes_combined_report_to_file(self, tmp_path):
        summary = _write_summary(tmp_path, _summary_payload())
        out_path = tmp_path / "report.md"
        result = CliRunner().invoke(
            report,
            ["-s", str(summary), "-o", str(out_path), "--seed", "0"],
        )
        assert result.exit_code == 0, result.output
        contents = out_path.read_text()
        assert "# ChaosProbe analysis report" in contents
        assert "## Data quality (doctor)" in contents
        assert "## Per-strategy aggregate (summarize)" in contents
        assert "## Statistical analysis (stats)" in contents
        # Source line points back at the input file.
        assert f"Source: `{summary}`" in contents

    def test_stdout_when_no_output_flag(self, tmp_path):
        summary = _write_summary(tmp_path, _summary_payload())
        result = CliRunner().invoke(
            report,
            ["-s", str(summary), "--seed", "0"],
        )
        assert result.exit_code == 0, result.output
        assert "# ChaosProbe analysis report" in result.output
        assert "## Statistical analysis (stats)" in result.output

    def test_nondeterministic_seed_minus_one(self, tmp_path):
        """``--seed -1`` should still produce a report (just nondeterministic)."""
        summary = _write_summary(tmp_path, _summary_payload())
        out_path = tmp_path / "report.md"
        result = CliRunner().invoke(
            report,
            ["-s", str(summary), "-o", str(out_path), "--seed", "-1"],
        )
        assert result.exit_code == 0, result.output
        assert out_path.exists()

    def test_custom_confidence_flows_through(self, tmp_path):
        summary = _write_summary(tmp_path, _summary_payload())
        out_path = tmp_path / "report.md"
        result = CliRunner().invoke(
            report,
            ["-s", str(summary), "-o", str(out_path), "--confidence", "0.99", "--seed", "0"],
        )
        assert result.exit_code == 0, result.output
        contents = out_path.read_text()
        # _format_markdown labels the CI column with the confidence pct.
        assert "99%" in contents or "0.99" in contents
