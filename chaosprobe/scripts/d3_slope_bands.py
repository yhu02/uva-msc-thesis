#!/usr/bin/env python3
"""Recompute the D3 per-f-level pre-window UDP-slope taint bands.

The D3 validity check (the session design) taints a
C1 iteration whose pre-chaos UDP-entry slope leaves its f-level's band.  The
bands are stored in :data:`m2_aa_analysis.D3_UDP_SLOPE_BANDS_EPM` (deviation
D-2026-06-14-01); this script is their audit trail
— it re-derives them from the 2026-06-12 M2 A/A block so anyone can verify the
committed constants against the raw data.

Per f-level the band is ``round(mean ± 3·SD)`` of the untainted per-iteration
``udp_preslope_epm`` pooled over the A/A sessions, SD being the population SD of
that reference set (``round`` is Python's round-half-to-even; none of the
edges sit on a half-integer).  Extraction reuses the canonical
:func:`m2_aa_analysis.load_condition_outcomes` and the same accept + per-
iteration taint exclusions the M2 path applies, so the bands derive from the
exact definition the gate applies.

Usage
-----
    uv run python scripts/d3_slope_bands.py --results-dir results/aa
    uv run python scripts/d3_slope_bands.py --results-dir results/aa --check
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics as st
import sys
from typing import Dict, List, Optional, Sequence, Tuple

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:  # `python scripts/d3_slope_bands.py` adds it; imports may not
    sys.path.insert(0, _SCRIPTS_DIR)

from m2_aa_analysis import (  # noqa: E402  (sys.path bootstrap above)
    D3_UDP_SLOPE_BANDS_EPM,
    load_condition_outcomes,
    summary_tainted_iterations,
)

#: Derive bands only for the f-levels — keyed off the dict
#: so the level set can never drift from the constants this audits.
LEVELS: Tuple[str, ...] = tuple(D3_UDP_SLOPE_BANDS_EPM)

#: A level needs at least this many untainted A/A slope samples to form a band.
MIN_SAMPLES = 2


def collect_slopes(results_dir: str, exclude: Sequence[str] = ()) -> Dict[str, List[float]]:
    """Pool untainted per-iteration ``udp_preslope_epm`` per f-level.

    Walks ``<results_dir>/*/summary.json``, skipping non-placement sessions and any
    directory name in ``exclude``, and applies the same exclusions the
    canonical M2 path does: conditions not accepted at apply time
    (``perLevel[].accepted`` false — rejected/drifted/never-run) are dropped,
    and within an accepted condition the per-iteration taint exclusion runs.
    """
    by_level: Dict[str, List[float]] = {lv: [] for lv in LEVELS}
    for summ_path in sorted(glob.glob(os.path.join(results_dir, "*", "summary.json"))):
        session_dir = os.path.dirname(summ_path)
        if os.path.basename(session_dir) in exclude:
            continue
        with open(summ_path) as fh:
            summary = json.load(fh)
        session_block = summary.get("session") or summary.get("v2Session")  # or legacy key
        per_level = (session_block or {}).get("perLevel") or []
        if not per_level:
            continue
        # Mirror the canonical accept gate (m2_aa_analysis: accepted defaults
        # True when absent; a condition is excluded when accepted is false).
        accepted = {
            rec.get("condition")
            for rec in per_level
            if rec.get("condition") and bool(rec.get("accepted", True))
        }
        tainted, taints = summary_tainted_iterations(per_level)
        for lv in LEVELS:
            if lv not in accepted:
                continue
            outcomes = load_condition_outcomes(session_dir, lv, tainted, taints)
            if outcomes is None:
                continue
            for value in outcomes["udp_preslope_epm"]:
                if value is not None:  # tainted/slope-less iterations carry None
                    by_level[lv].append(float(value))
    return by_level


def band_from_slopes(slopes: Sequence[float]) -> Tuple[int, int]:
    """``round(mean ± 3·population-SD)`` of one f-level's A/A slopes."""
    mean = st.fmean(slopes)
    sd = st.pstdev(slopes)
    return (round(mean - 3 * sd), round(mean + 3 * sd))


def derive_bands(results_dir: str, exclude: Sequence[str] = ()) -> Dict[str, Tuple[int, int]]:
    """Re-derive the per-f-level bands from an A/A results directory.

    Levels with fewer than :data:`MIN_SAMPLES` untainted samples are omitted
    (no band can be formed), so a mismatch against the dict is visible.
    """
    by_level = collect_slopes(results_dir, exclude)
    return {lv: band_from_slopes(xs) for lv, xs in by_level.items() if len(xs) >= MIN_SAMPLES}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--results-dir", default="results/aa", help="A/A block results directory")
    ap.add_argument("--exclude", nargs="*", default=[], help="session dir names to exclude")
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero unless the re-derived bands match the constants",
    )
    args = ap.parse_args(argv)

    by_level = collect_slopes(args.results_dir, args.exclude)
    print(
        f"{'f-level':8} {'n':>3} {'mean':>10} {'pop-SD':>10} "
        f"{'derived band':>22} {'reference':>18}"
    )
    derived: Dict[str, Tuple[int, int]] = {}
    for lv in LEVELS:
        xs = by_level[lv]
        frozen = D3_UDP_SLOPE_BANDS_EPM[lv]
        if len(xs) >= MIN_SAMPLES:
            band = band_from_slopes(xs)
            derived[lv] = band
            print(
                f"{lv:8} {len(xs):>3} {st.fmean(xs):>10.1f} {st.pstdev(xs):>10.1f}"
                f" {f'[{band[0]}, {band[1]}]':>22} {f'[{frozen[0]}, {frozen[1]}]':>18}"
            )
        else:
            print(f"{lv:8} {len(xs):>3} {'(insufficient samples — no band)':>44}")

    if args.check:
        mismatch = {
            lv: (derived.get(lv), D3_UDP_SLOPE_BANDS_EPM[lv])
            for lv in LEVELS
            if derived.get(lv) != D3_UDP_SLOPE_BANDS_EPM[lv]
        }
        if mismatch:
            print(f"MISMATCH (derived != reference): {mismatch}", file=sys.stderr)
            return 1
        print("OK — re-derived bands match D3_UDP_SLOPE_BANDS_EPM")
    return 0


if __name__ == "__main__":  # pragma: no cover  (CLI entrypoint)
    raise SystemExit(main())
