"""``chaosprobe diff`` — compare two summary.json files.

Answers the question every defender eventually gets asked: *"Did
re-running your experiment give consistent results?"*

Reports, per strategy that appears in either summary:

* the mean (and 95% CI) of resilience and recovery in both summaries,
* the delta and % change between them,
* whether the CIs overlap (qualitative stability flag),
* a final exit-code-bearing verdict when ``--strict`` is set.

Strategies present in only one summary are surfaced at the top so they
don't get silently dropped from the comparison.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

_METRICS: List[Tuple[str, str, str]] = [
    ("meanResilienceScore", "meanResilienceScore_ci95", "resilience"),
    ("meanRecoveryTime_ms", "meanRecoveryTime_ms_ci95", "recovery (ms)"),
]


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _strategies(raw: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return raw.get("strategies") or {}


def _agg(sdata: Dict[str, Any]) -> Dict[str, Any]:
    return sdata.get("aggregated") or {}


def _cis_overlap(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> Optional[bool]:
    """``True`` if the two 95% CIs overlap, ``False`` if disjoint,
    ``None`` if either CI is missing/incomplete."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    for ci in (a, b):
        if ci.get("low") is None or ci.get("high") is None:
            return None
    a_lo, a_hi = float(a["low"]), float(a["high"])
    b_lo, b_hi = float(b["low"]), float(b["high"])
    return not (a_hi < b_lo or b_hi < a_lo)


def _pct(delta: float, base: float) -> Optional[float]:
    if base == 0:
        return None
    return (delta / base) * 100.0


def _compare_strategy(sdata_a: Dict[str, Any], sdata_b: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return one row per metric with both means, delta, %, CI overlap."""
    a_agg = _agg(sdata_a)
    b_agg = _agg(sdata_b)
    rows: List[Dict[str, Any]] = []
    for mean_key, ci_key, label in _METRICS:
        a_mean = a_agg.get(mean_key)
        b_mean = b_agg.get(mean_key)
        if a_mean is None or b_mean is None:
            continue
        delta = b_mean - a_mean
        pct = _pct(delta, a_mean)
        rows.append(
            {
                "metric": label,
                "a_mean": a_mean,
                "b_mean": b_mean,
                "delta": delta,
                "pct": pct,
                "ci_overlap": _cis_overlap(a_agg.get(ci_key), b_agg.get(ci_key)),
            }
        )
    return rows


def _build_report(raw_a: Dict[str, Any], raw_b: Dict[str, Any]) -> Dict[str, Any]:
    """Build the full diff payload (consumed by both text and JSON formatters)."""
    strats_a = _strategies(raw_a)
    strats_b = _strategies(raw_b)
    names_a = set(strats_a)
    names_b = set(strats_b)

    return {
        "onlyInA": sorted(names_a - names_b),
        "onlyInB": sorted(names_b - names_a),
        "common": {
            name: _compare_strategy(strats_a[name], strats_b[name])
            for name in sorted(names_a & names_b)
        },
    }


def _fmt(v: Optional[float], digits: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}"


def _format_text(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    if report["onlyInA"]:
        lines.append(f"strategies only in A: {', '.join(report['onlyInA'])}")
    if report["onlyInB"]:
        lines.append(f"strategies only in B: {', '.join(report['onlyInB'])}")
    if not report["common"]:
        if not lines:
            lines.append("no overlapping strategies — nothing to compare")
        return "\n".join(lines)
    if lines:
        lines.append("")
    for name, rows in report["common"].items():
        lines.append(f"{name}:")
        if not rows:
            lines.append("  (no comparable metrics)")
            continue
        for row in rows:
            overlap = row["ci_overlap"]
            flag = (
                "stable (CIs overlap)"
                if overlap is True
                else "CHANGED (CIs disjoint)" if overlap is False else "no CI"
            )
            pct = f"{_fmt(row['pct'], 1)}%" if row["pct"] is not None else "—"
            lines.append(
                f"  {row['metric']}: {_fmt(row['a_mean'])} → {_fmt(row['b_mean'])} "
                f"(Δ={_fmt(row['delta'])}, {pct}, {flag})"
            )
    return "\n".join(lines)


def _has_disjoint_ci(report: Dict[str, Any]) -> bool:
    """True if any metric's CIs are disjoint across the two summaries."""
    return any(row["ci_overlap"] is False for rows in report["common"].values() for row in rows)


@click.command("diff")
@click.option(
    "--a",
    "summary_a",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="First summary.json (baseline).",
)
@click.option(
    "--b",
    "summary_b",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Second summary.json (comparison).",
)
@click.option(
    "--json",
    "json_out",
    is_flag=True,
    help="Emit the diff as JSON instead of text.",
)
@click.option(
    "--strict",
    is_flag=True,
    help="Exit non-zero when any metric's CIs are disjoint between A and B.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write to file (default: stdout).",
)
def diff(
    summary_a: Path,
    summary_b: Path,
    json_out: bool,
    strict: bool,
    output: Optional[Path],
):
    """Compare two ``summary.json`` files and report per-strategy deltas.

    Useful for "did re-running my experiment give consistent results?".
    Each strategy gets a line per metric showing the means in both runs,
    absolute and percent delta, and a qualitative stability flag based on
    95% CI overlap.

    \b
    Examples:
      chaosprobe diff --a run1.json --b run2.json
      chaosprobe diff --a baseline.json --b rerun.json --strict
      chaosprobe diff --a a.json --b b.json --json -o diff.json
    """
    raw_a = _load(summary_a)
    raw_b = _load(summary_b)
    report = _build_report(raw_a, raw_b)

    if json_out:
        rendered = json.dumps(report, indent=2)
    else:
        rendered = _format_text(report)

    if output:
        output.write_text(rendered + "\n")
        click.echo(f"Wrote {output}")
    else:
        click.echo(rendered)

    if strict and _has_disjoint_ci(report):
        raise SystemExit(1)
