"""Smoke tests that lock the example summary fixture against schema drift.

If a defender-facing command's expected shape changes and the fixture
isn't updated, these tests will catch it before the example README
goes stale.
"""

import json
from pathlib import Path

from click.testing import CliRunner

from chaosprobe.commands.diff_cmd import diff
from chaosprobe.commands.doctor_cmd import doctor
from chaosprobe.commands.inspect_cmd import inspect
from chaosprobe.commands.report_cmd import report
from chaosprobe.commands.stats_cmd import stats
from chaosprobe.commands.summarize_cmd import summarize
from chaosprobe.output import SCHEMA_VERSION

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "example-summary.json"


def test_example_file_exists():
    assert EXAMPLE.exists(), f"example fixture missing: {EXAMPLE}"


def test_schema_version_matches_current():
    """If SCHEMA_VERSION moves, the fixture must move with it.

    Otherwise doctor's schema-version check would warn against our
    own README's example commands.
    """
    raw = json.loads(EXAMPLE.read_text())
    assert raw.get("schemaVersion") == SCHEMA_VERSION


def test_doctor_reports_clean():
    result = CliRunner().invoke(doctor, ["-s", str(EXAMPLE)])
    assert result.exit_code == 0, result.output
    assert "no issues" in result.output.lower()


def test_summarize_emits_both_strategies():
    result = CliRunner().invoke(summarize, ["-s", str(EXAMPLE)])
    assert result.exit_code == 0, result.output
    assert "## spread" in result.output
    assert "## colocate" in result.output


def test_stats_finds_significant_resilience_difference():
    result = CliRunner().invoke(
        stats,
        ["-s", str(EXAMPLE), "--metric", "resilience", "--seed", "0"],
    )
    assert result.exit_code == 0, result.output
    # The fixture is calibrated so the spread-vs-colocate pairwise is
    # significant.  This is also the "is the example interesting?" guard.
    assert "✓" in result.output


def test_inspect_drills_into_iteration():
    result = CliRunner().invoke(
        inspect,
        ["-s", str(EXAMPLE), "--strategy", "colocate", "-i", "3"],
    )
    assert result.exit_code == 0, result.output
    assert "verdict: FAIL" in result.output


def test_report_assembles_against_example(tmp_path):
    out = tmp_path / "report.md"
    result = CliRunner().invoke(
        report,
        ["-s", str(EXAMPLE), "-o", str(out), "--seed", "0"],
    )
    assert result.exit_code == 0, result.output
    contents = out.read_text()
    for section in (
        "## Data quality (doctor)",
        "## Per-strategy aggregate (summarize)",
        "## Statistical analysis (stats)",
    ):
        assert section in contents


def test_self_diff_is_all_stable():
    """The README invites users to run `diff --a <ex> --b <ex>`.  Each
    metric should come back as ``stable`` (CIs trivially overlap)."""
    result = CliRunner().invoke(diff, ["--a", str(EXAMPLE), "--b", str(EXAMPLE)])
    assert result.exit_code == 0, result.output
    assert "CHANGED" not in result.output
    assert "stable" in result.output
