#!/usr/bin/env python3
"""AX node-drain A/A: variance components across ALL fixed-placement sessions.

Companion to ``scripts/aa_block.py`` for the AX availability-axis pre-registration
([`v2-design/AX-AA-REPORT.md`]).  ``aa_block.py`` pairs sessions by
``v2Session.solverSeed`` + cell fields and chunks a cell group into pairs; the AX
A/A block runs **all** sessions at one solver seed (one canonical placement per
f-level, replicated across order seeds), so ``aa_block.py`` forms a single pair and
reports the rest as "extra ... ignored".  The correct unit for that all-identical-
placement design is N replicates, not one pair — this script computes it.

Unit (prereg AX-H3 / §3): facet ``condition`` = f-level / placement (between);
facet ``session`` = replicate at the fixed placement (within / test-retest).  Per
availability outcome it reports per-session medians at each f, the within-condition
(between-session, at fixed f) sd pooled over f, the between-condition sd, and
ICC_test-retest = between-condition / (between-condition + within-condition).  It
reuses the canonical per-iteration extraction
(:func:`m2_aa_analysis.load_condition_outcomes`) so the numbers cannot drift from
``aa_block.py`` / ``m2_aa_analysis.py``.

Usage
-----
    uv run python scripts/ax_aa_all6_variance.py --results-dir results/ax-aa-nodedrain
"""

from __future__ import annotations

import argparse
import itertools
import os
import statistics as st
import sys
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import m2_aa_analysis as m2  # noqa: E402  (sibling script, not a package)

CONDS = ["f-000", "f-025", "f-050", "f-075", "f-100"]
# availability + latency outcomes; integrated outage is derived per-iteration below
BASE = [
    "es_trough_depth_pods",
    "es_zero_services",
    "trough_duration_real_s",
    "trough_duration_s",
    "user_err_during",
    "ew_p95_pre_ms",
]
OUTCOMES = BASE + ["integrated_outage"]


def _median(values: Sequence[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return st.median(clean) if clean else None


def _pvar(values: Sequence[Optional[float]]) -> float:
    clean = [v for v in values if v is not None]
    return st.pvariance(clean) if clean else 0.0


def _all_pairs_band(
    per_cond_session_medians: Dict[str, List[Optional[float]]],
) -> Optional[Dict[str, float]]:
    """A/A noise band: |session_i - session_j| at fixed f, pooled over conditions.

    The all-identical-placement analogue of ``aa_block.py``'s paired |A-B| band
    (which pairs only two sessions); here every unordered pair of replicate
    sessions at each f contributes one |Δ|. Feeds the SESOI / margin / δ floors.
    """
    diffs: List[float] = []
    for medians in per_cond_session_medians.values():
        clean = [m for m in medians if m is not None]
        diffs.extend(abs(a - b) for a, b in itertools.combinations(clean, 2))
    if not diffs:
        return None
    diffs.sort()
    p95 = diffs[min(len(diffs) - 1, round(0.95 * (len(diffs) - 1)))]
    return {"n": len(diffs), "median": st.median(diffs), "p95": p95, "max": max(diffs)}


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results-dir", default="results/ax-aa-nodedrain")
    args = ap.parse_args(argv)

    sessions, warns = m2.discover_sessions(args.results_dir)
    for w in warns:
        print("WARN", w)
    print(f"sessions discovered: {len(sessions)}")

    # rows[outcome][cond] = per-session medians; within[outcome][cond] = per-session iter pvar
    rows: Dict[str, Dict[str, List[Optional[float]]]] = {
        o: {c: [] for c in CONDS} for o in OUTCOMES
    }
    within: Dict[str, Dict[str, List[float]]] = {o: {c: [] for c in CONDS} for o in OUTCOMES}

    for s in sessions:
        sdir = os.path.join(args.results_dir, s.run)
        for c in CONDS:
            tainted: set = set()
            taints: List[str] = []
            per = m2.load_condition_outcomes(sdir, c, tainted, taints)
            if per is None:
                print(f"  MISSING raw {s.run}/{c}.json")
                continue
            depth, dur = per["es_trough_depth_pods"], per["trough_duration_real_s"]
            per["integrated_outage"] = [
                d * u if (d is not None and u is not None) else None for d, u in zip(depth, dur)
            ]
            for o in OUTCOMES:
                rows[o][c].append(_median(per[o]))
                within[o][c].append(_pvar(per[o]))

    print("\n" + "=" * 78)
    print("AX node-drain A/A — variance components across ALL sessions (fixed placement)")
    print("=" * 78)
    for o in OUTCOMES:
        print(f"\n--- {o} ---")
        cond_means, within_cond_vars = [], []
        for c in CONDS:
            medians = [m for m in rows[o][c] if m is not None]
            if not medians:
                print(f"  {c}: (no data)")
                continue
            between_sess_var = _pvar(medians)
            within_iter_sd = st.mean(within[o][c]) ** 0.5
            cond_means.append(st.mean(medians))
            within_cond_vars.append(between_sess_var)
            print(
                f"  {c}: n_sess={len(medians)} "
                f"sess_medians={[round(m, 4) for m in medians]} "
                f"between-sess_sd={between_sess_var ** 0.5:.4g} "
                f"within-iter_sd={within_iter_sd:.4g}"
            )
        if len(cond_means) >= 2:
            between_cond_var = _pvar(cond_means)
            within_cond_var = st.mean(within_cond_vars)
            denom = between_cond_var + within_cond_var
            icc = between_cond_var / denom if denom > 0 else float("nan")
            deterministic = within_cond_var < 1e-9
            print(
                f"  >> between-condition sd={between_cond_var ** 0.5:.4g} | "
                f"within-condition(between-session) sd={within_cond_var ** 0.5:.4g} | "
                f"ICC_test_retest={icc:.4f}"
                + ("  [DETERMINISTIC at fixed placement]" if deterministic else "")
            )

    print("\n" + "=" * 78)
    print("All-pairs A/A noise bands (|session_i - session_j| at fixed f, pooled)")
    print("=" * 78)
    for o in OUTCOMES:
        band = _all_pairs_band(rows[o])
        if band is None:
            print(f"  {o}: (no data)")
            continue
        print(
            f"  {o:24s} n={int(band['n']):3d} median={band['median']:.4g} "
            f"p95={band['p95']:.4g} max={band['max']:.4g}"
        )


if __name__ == "__main__":
    main()
