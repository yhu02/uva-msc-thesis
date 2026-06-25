#!/usr/bin/env python3
"""H1 confirmatory analysis: dose-response of the east-west tail in f.

Runs the registered H1 primary test (`01-PREREGISTRATION.md` §H1): a
**Page's L trend test** over the five ordered cross-node-fraction levels
`f ∈ {0, 0.25, 0.5, 0.75, 1.0}`, predicting a monotone *increase* in median
east-west p95 latency.  Each C1 session is a complete ordered block; the unit
entering Page's test is the **session-condition median** of the D4-pinned
outcome `ew_p95_pre_ms` (per iteration, median over inter-service routes of the
route p95, loadgen→ excluded, pre-chaos window) over the session's untainted
iterations.  Extraction reuses the canonical
:func:`m2_aa_analysis.load_condition_outcomes`.

**D3 UDP-slope taint is OFF by default here (deviation D-2026-06-14-02,
DEVIATIONS.md).**  Diagnosis of C1 showed the frozen D3 band (D-2026-06-14-01,
derived from the low-churn A/A block) does not generalize to C1's per-level
re-placement regime — it taints every f-025/f-050 iteration — while the
east-west latency baseline at those levels is in fact the *cleanest* of all
levels.  The pre-window UDP/DNS conntrack pool is not a validity precondition
for this TCP/gRPC latency outcome, so the slope-taint is removed from H1.
``--slope-band-taint`` re-enables it for the sensitivity report.

Alongside the test it reports the registered **SESOI** effect size: the % change
in the per-level grand-median east-west p95 from f = 0 to f = 1 (the bar is a
≥15 % increase). A statistically significant Page's L with a < 15 % effect is
reported as *below the SESOI*, not as support (prereg §H1).

The Spearman-over-designed-levels sensitivity check is non-confirmatory and is
not computed here.

Usage::

    uv run python scripts/c1_h1_trend.py --results-dir results/c1-online-boutique
    uv run python scripts/c1_h1_trend.py --results-dir results/c1-online-boutique --slope-band-taint
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
from typing import Dict, List, Optional, Tuple

from chaosprobe.metrics.statistics import page_trend_test

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:  # `python scripts/c1_h1_trend.py` adds it; imports may not
    sys.path.insert(0, _SCRIPTS_DIR)

from m2_aa_analysis import (  # noqa: E402  (sys.path bootstrap above)
    _median_or_none,
    discover_sessions,
    load_condition_outcomes,
)

#: The registered H1 design: the five f-levels in predicted-increasing order.
LEVELS: Tuple[Tuple[str, float], ...] = (
    ("f-000", 0.0),
    ("f-025", 0.25),
    ("f-050", 0.5),
    ("f-075", 0.75),
    ("f-100", 1.0),
)

#: Registered H1 SESOI: ≥15 % increase in east-west p95 from f = 0 to f = 1.
SESOI_PCT = 15.0

#: The D4-pinned H1 outcome (median east-west p95, pre-chaos).
OUTCOME = "ew_p95_pre_ms"


def collect_blocks(
    results_dir: str, slope_band_taint: bool = False
) -> Tuple[List[List[float]], List[str]]:
    """Per-session complete ordered blocks of the H1 outcome.

    Returns ``(blocks, warnings)``.  ``blocks`` has one entry per session that
    contributed a value for **all five** levels (Page's L requires complete
    blocks); each is the five session-condition medians (median over the
    session's untainted iterations of ``ew_p95_pre_ms``) in f-ascending order.
    Sessions missing a level, with that level not accepted, or whose level has
    no untainted iteration, are dropped (and noted).  The effect size
    (:func:`sesoi_effect`) is computed from these same blocks, so the test and
    the reported effect describe one cohort.

    ``slope_band_taint`` (default ``False`` — deviation D-2026-06-14-02) adds
    the frozen D3 UDP-slope taint; left off for the primary H1 analysis.
    """
    sessions, warnings = discover_sessions(results_dir)
    blocks: List[List[float]] = []
    for session in sessions:
        run_dir = os.path.join(results_dir, session.run)
        row: Dict[str, Optional[float]] = {}
        for cond, _f in LEVELS:
            obs = session.levels.get(cond)
            if obs is None or not obs.accepted:
                row[cond] = None
                continue
            per_outcome = load_condition_outcomes(
                run_dir, cond, session.tainted, session.taints, slope_band_taint=slope_band_taint
            )
            row[cond] = None if per_outcome is None else _median_or_none(per_outcome[OUTCOME])
        if all(row[cond] is not None for cond, _ in LEVELS):
            blocks.append([row[cond] for cond, _ in LEVELS])
        else:
            missing = [cond for cond, _ in LEVELS if row[cond] is None]
            warnings.append(
                f"{session.run}: incomplete H1 block (no value for {', '.join(missing)}) "
                "— excluded from Page's L"
            )
    return blocks, warnings


def sesoi_effect(blocks: List[List[float]]) -> Dict[str, object]:
    """Per-level grand medians + the f=0→f=1 % change vs the 15 % SESOI.

    Computed over the same complete blocks that enter Page's L, so the effect
    size and the test summarize one cohort.
    """
    grand = {
        cond: (st.median([b[i] for b in blocks]) if blocks else None)
        for i, (cond, _) in enumerate(LEVELS)
    }
    lo, hi = grand["f-000"], grand["f-100"]
    if lo is None or hi is None or lo <= 0:
        pct = None
    else:
        pct = round(100.0 * (hi - lo) / lo, 2)
    return {
        "perLevelGrandMedian": {
            cond: (round(v, 4) if v is not None else None) for cond, v in grand.items()
        },
        "f0": round(lo, 4) if lo is not None else None,
        "f1": round(hi, 4) if hi is not None else None,
        "pctChange": pct,
        "sesoiPct": SESOI_PCT,
        "meetsSesoi": (pct is not None and pct >= SESOI_PCT),
    }


def analyze(results_dir: str, slope_band_taint: bool = False) -> Dict[str, object]:
    """The full H1 dose-response analysis as one JSON-ready dict.

    ``slope_band_taint`` defaults OFF (deviation D-2026-06-14-02); set it for
    the sensitivity run that re-applies the frozen D3 UDP-slope taint.
    """
    blocks, warnings = collect_blocks(results_dir, slope_band_taint=slope_band_taint)
    page = page_trend_test(blocks)
    return {
        "outcome": OUTCOME,
        "slopeBandTaint": slope_band_taint,
        "nCompleteBlocks": len(blocks),
        "levels": [f for _, f in LEVELS],
        "pageTrendTest": page,
        "sesoi": sesoi_effect(blocks),
        "warnings": warnings,
    }


def print_report(result: Dict[str, object]) -> None:
    page = result["pageTrendTest"]
    sesoi = result["sesoi"]
    print("H1 — dose-response of the east-west tail (Page's L trend test)")
    print(f"  outcome:           {result['outcome']} (median east-west p95, pre-chaos)")
    taint = "ON (D3 sensitivity)" if result.get("slopeBandTaint") else "OFF (D-2026-06-14-02)"
    print(f"  D3 slope-taint:    {taint}")
    print(f"  complete blocks:   {result['nCompleteBlocks']} sessions")
    print(
        f"  Page's L:          L={page['l_statistic']}  z={page['z']}  "
        f"p(1-sided)={page['p_one_sided']}"
    )
    pls = sesoi["perLevelGrandMedian"]
    print(f"  per-level median:  {pls}")
    print(
        f"  SESOI effect:      f0={sesoi['f0']} → f1={sesoi['f1']}  "
        f"Δ={sesoi['pctChange']}%  (SESOI ≥ {sesoi['sesoiPct']}%) "
        f"→ {'meets' if sesoi['meetsSesoi'] else 'below'} SESOI"
    )
    for warning in result["warnings"]:
        print(f"  ! {warning}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--results-dir",
        default="results/c1-online-boutique",
        help="directory of <run>/summary.json C1 sessions",
    )
    parser.add_argument("--json", help="optional: write the analysis dict to this path")
    parser.add_argument(
        "--slope-band-taint",
        dest="slope_band_taint",
        action="store_true",
        help="re-apply the frozen D3 UDP-slope taint (off by default per "
        "deviation D-2026-06-14-02; use for the sensitivity run)",
    )
    args = parser.parse_args(argv)
    result = analyze(args.results_dir, slope_band_taint=args.slope_band_taint)
    print_report(result)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"\nJSON written to {args.json}")
    return 0


if __name__ == "__main__":  # pragma: no cover  (CLI entrypoint)
    raise SystemExit(main())
