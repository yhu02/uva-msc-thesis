"""CLI command that bundles ``doctor`` + ``summarize`` + ``stats`` into a
single markdown report suitable for the thesis appendix.

A defender pasting analysis into a thesis usually wants:

1. Data quality caveats up front (doctor's findings).
2. A per-strategy aggregate view (summarize's tables).
3. Formal CI + pairwise + Cliff's delta tables (stats --markdown).

Today that's three commands and three files.  ``chaosprobe report``
runs all three against the same summary, concatenates the rendered
markdown, and writes one ``report.md`` ready to ``\\input{}``,
``\\include{}``, or paste into Word.
"""

import json
from pathlib import Path
from typing import Optional

import click

from chaosprobe.commands.diff_cmd import _build_report as _build_diff_report
from chaosprobe.commands.diff_cmd import _format_text as _format_diff_text
from chaosprobe.commands.doctor_cmd import (
    _check_cross_strategy,
    _check_run_metadata,
    _check_schema_version,
    _check_strategy,
)
from chaosprobe.commands.stats_cmd import _METRIC_SPECS, _analyse_metric, _format_markdown
from chaosprobe.commands.summarize_cmd import _render_strategy


def _render_doctor_section(raw: dict) -> str:
    """Format doctor findings as a markdown subsection."""
    strategies = raw.get("strategies") or {}
    sections: list = []
    for name in sorted(strategies.keys()):
        issues = _check_strategy(name, strategies[name])
        if issues:
            sections.append((name, issues))
    cross = _check_cross_strategy(strategies)
    if cross:
        sections.append(("cross-strategy", cross))
    metadata = _check_run_metadata(raw)
    if metadata:
        sections.append(("run metadata", metadata))
    schema = _check_schema_version(raw)
    if schema:
        sections.append(("schema version", schema))

    parts = ["## Data quality (doctor)", ""]
    if not sections:
        parts.append(f"_No issues across {len(strategies)} strategies._")
        parts.append("")
        return "\n".join(parts)
    for name, findings in sections:
        parts.append(f"### {name}")
        parts.append("")
        for sev, msg in findings:
            marker = "**error**" if sev == "error" else "warn"
            parts.append(f"- _{marker}_ — {msg}")
        parts.append("")
    return "\n".join(parts)


def _render_summarize_section(raw: dict) -> str:
    """Format the per-strategy aggregate block as markdown."""
    strategies = raw.get("strategies") or {}
    parts = ["## Per-strategy aggregate (summarize)", ""]
    if not strategies:
        parts.append("_No strategies present._")
        parts.append("")
        return "\n".join(parts)
    for name in sorted(strategies.keys()):
        # _render_strategy already starts the block with "## <name>";
        # demote to ### for nesting inside the report.
        lines = _render_strategy(name, strategies[name])
        # First line is "## <name>" → rewrite as "### <name>".
        if lines and lines[0].startswith("## "):
            lines[0] = "### " + lines[0][3:]
        # The summarize helper uses 2-space indentation for body lines;
        # in a thesis appendix we want fenced code so structure renders
        # verbatim across markdown engines.
        body = "\n".join(line[2:] if line.startswith("  ") else line for line in lines[1:])
        parts.append(lines[0])
        parts.append("")
        parts.append("```")
        parts.append(body.rstrip("\n"))
        parts.append("```")
        parts.append("")
    return "\n".join(parts)


def _render_diff_section(raw: dict, baseline_raw: dict, baseline_path: Path) -> str:
    """Format the baseline-vs-current diff as a markdown subsection.

    Wraps the existing diff_cmd ``_format_text`` output in a fenced block
    so per-strategy deltas render verbatim across markdown engines.
    """
    report = _build_diff_report(baseline_raw, raw)
    parts = [
        "## Diff vs. baseline",
        "",
        f"Baseline: `{baseline_path}`",
        "",
        "```",
        _format_diff_text(report),
        "```",
        "",
    ]
    return "\n".join(parts)


def _render_stats_section(raw: dict, confidence: float, seed: Optional[int]) -> str:
    """Format the stats CLI markdown output for every available metric."""
    analyses: dict = {}
    for key in sorted(_METRIC_SPECS.keys()):
        metric_path, metric_label = _METRIC_SPECS[key]
        analysis = _analyse_metric(
            raw,
            metric_path,
            metric_label,
            confidence,
            n_resamples=2000,
            seed=seed,
        )
        if analysis is not None:
            analyses[metric_label] = analysis
    parts = ["## Statistical analysis (stats)", ""]
    if not analyses:
        parts.append("_No strategies carry any supported metric._")
        parts.append("")
        return "\n".join(parts)
    parts.append(_format_markdown(analyses, confidence))
    parts.append("")
    return "\n".join(parts)


@click.command("report")
@click.option(
    "--summary",
    "-s",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to a summary.json produced by `chaosprobe run`.",
)
@click.option(
    "--confidence",
    type=float,
    default=0.95,
    show_default=True,
    help="Bootstrap confidence level for the stats section.",
)
@click.option(
    "--seed",
    type=int,
    default=42,
    show_default=True,
    help="Bootstrap RNG seed for the stats section.  Use -1 for nondeterministic.",
)
@click.option(
    "--diff",
    "baseline",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Compare summary against this baseline summary.json — adds a 'Diff vs. baseline' section.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write to file (default: stdout).",
)
def report(
    summary: Path,
    confidence: float,
    seed: int,
    baseline: Optional[Path],
    output: Optional[Path],
):
    """Generate a thesis-appendix markdown report.

    Bundles ``doctor`` + ``summarize`` + ``stats --markdown`` against the
    same summary.json, in one file ready to paste into a thesis document
    or include via ``\\input{report.md}`` after a pandoc conversion.

    When ``--diff baseline.json`` is supplied, a 'Diff vs. baseline'
    section is appended showing per-strategy deltas + CI-overlap
    stability flags from ``chaosprobe diff``.

    \b
    Examples:
      chaosprobe report -s summary.json -o report.md
      chaosprobe report -s summary.json --confidence 0.99 -o appendix.md
      chaosprobe report -s rerun.json --diff baseline.json -o report.md
    """
    raw = json.loads(summary.read_text())
    actual_seed = None if seed == -1 else seed

    sections = [
        f"# ChaosProbe analysis report\n\nSource: `{summary}`\n",
        _render_doctor_section(raw),
        _render_summarize_section(raw),
        _render_stats_section(raw, confidence, actual_seed),
    ]
    if baseline is not None:
        baseline_raw = json.loads(baseline.read_text())
        sections.append(_render_diff_section(raw, baseline_raw, baseline))

    rendered = "\n".join(sections).rstrip("\n")
    if output:
        output.write_text(rendered + "\n")
        click.echo(f"Wrote {output}")
    else:
        click.echo(rendered)
