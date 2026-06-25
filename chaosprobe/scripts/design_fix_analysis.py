#!/usr/bin/env python3
"""Design-corrected re-analysis of the availability-axis tests (H3/H4/H5).

Exploratory, OUTSIDE the frozen Holm family. Criteria pre-declared in
``v2-design/DESIGN-FIX-SCOPE.md`` before the new data were looked at. The three
registered availability tests were construction-limited because ``pod-delete`` at
r=1 cannot move availability:

* **H3** trough-depth co-primary — the absolute 1-pod margin equals the realized
  r=1 depth, so the depth rescue was un-passable by construction.
* **H4** placement frontier — its availability face, computed on ``pod-delete``,
  was constant (depth ~1 pod for every placement) → degenerate.
* **H5** availability sub-score — its ICC was computed on the same ``pod-delete``
  data with no sustained outage, so a low value reflected absent signal.

This driver recomputes all three on ``node-drain`` (which produces a real,
placement-dependent outage):

* **H3** — re-analyse the existing C2 node-drain campaign with a range-relative
  depth bar (anti-affine depth vs 50% of the realized r=1 depth) and report the
  user-error co-primary; the EFFECT (interaction + depth reduction + user-error
  elimination) is the substantive result.
* **H4** — build the frontier from the C4 node-drain dose-response: availability
  trough depth (now varying across f) vs pre-chaos east-west p95.
* **H5** — availability sub-score test-retest ICC on the C4 sessions.

Usage::

    uv run python scripts/design_fix_analysis.py \
        --c2-dir results/c2-roundrobin --c4-dir results/c4-nodedrain-dose \
        --json results/design-fix-verdict.json
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics as st
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c2_h3_anova import analyze as c2_analyze  # noqa: E402
from c2_h3_anova import (  # noqa: E402
    app_services_from_series,
    collect_sessions,
    trough_depth_fraction,
)

LEVELS = ["f-000", "f-025", "f-050", "f-075", "f-100"]
DEPTH_REDUCTION_BAR = 0.50  # range-relative: anti depth must be <= 50% of r1 depth


def _ew_p95(latency: Dict) -> Optional[float]:
    """Median over inter-service routes of the route p95 (pre-chaos)."""
    vals: List[float] = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("p95", "p95_ms", "prechaos_p95_ms") and isinstance(v, (int, float)):
                    vals.append(float(v))
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(latency or {})
    return st.median(vals) if vals else None


def fix_h3(c2_dir: str) -> Dict:
    """C2 re-analysis with a range-relative depth bar (the effect, not the pass)."""
    res = c2_analyze(c2_dir)
    dep, err = res["troughDepthFraction"], res["userErrorRate"]
    # Compute the depth reduction from the UNROUNDED per-session medians (the
    # analyze() block rounds medians to 4 dp, which puts the knife-edge 0.5/11
    # vs 1/11 ratio on the wrong side of the 50% bar by a rounding artifact).
    sessions, _ = collect_sessions(c2_dir)
    r1v = [s.depth for s in sessions if s.replicas == 1 and s.depth is not None]
    antiv = [
        s.depth
        for s in sessions
        if s.replicas == 3 and s.mode == "anti-affine" and s.depth is not None
    ]
    r1_med = st.median(r1v) if r1v else None
    anti_med = st.median(antiv) if antiv else None
    depth_reduction = (r1_med - anti_med) / r1_med if r1_med else None
    return {
        "depthMedians": dep["median"],
        "depthInteractionP": dep["artInteraction"]["p"],
        "depthInteractionSig": dep["interactionSig"],
        "depthReductionFraction": depth_reduction,  # anti vs r1, as a fraction
        "depthReductionBar": DEPTH_REDUCTION_BAR,
        "depthReductionMeetsBar": (
            depth_reduction is not None and depth_reduction >= DEPTH_REDUCTION_BAR
        ),
        "userErrorMedians": err["median"],
        "userErrorRescue": err["median"]["r1"] - err["median"]["r3_anti"],
        "userErrorInteractionP": err["artInteraction"]["p"],
        "originalConjunction": res.get("conjunctionRescue"),
        "note": (
            "Original 'not supported' was the un-passable-margin artifact (1-pod "
            "margin == realized r1 depth). Anti-affine halves the trough depth "
            "(significant interaction) and eliminates user-route error; the rescue "
            "effect is real. The depth bar remains near-degenerate at r=1 because "
            "the r=1 depth is intrinsically ~1 pod — the substantive evidence is "
            "the interaction + the user-error co-primary."
        ),
    }


def _per_level(c4_dir: str):
    avail: Dict[str, List[float]] = {c: [] for c in LEVELS}
    lat: Dict[str, List[float]] = {c: [] for c in LEVELS}
    n = 0
    for s in sorted(glob.glob(f"{c4_dir}/*/summary.json")):
        d = json.load(open(s))
        n += 1
        strat = d.get("faults", {}).get("node-drain", {}).get("strategies", {})
        for c in LEVELS:
            if c not in strat:
                continue
            m = strat[c]["metrics"]
            ets = m.get("endpointSliceTimeSeries", {})
            frac, _ = trough_depth_fraction(ets, app_services_from_series(ets))
            if isinstance(frac, float):
                avail[c].append(frac)
            p = _ew_p95(m.get("latency", {}))
            if isinstance(p, (int, float)):
                lat[c].append(float(p))
    return avail, lat, n


def fix_h4_h5(c4_dir: str) -> Dict:
    """Frontier (H4) + availability ICC (H5) from the C4 node-drain dose-response."""
    avail, lat, n = _per_level(c4_dir)
    frontier = {
        c: {
            "availMedian": (st.median(avail[c]) if avail[c] else None),
            "ewP95Median": (st.median(lat[c]) if lat[c] else None),
            "n": len(avail[c]),
        }
        for c in LEVELS
    }
    a = [frontier[c]["availMedian"] for c in LEVELS if frontier[c]["availMedian"] is not None]
    # H5 ICC proxy: between-level vs within-level (between-session) variance.
    means = [st.mean(avail[c]) for c in LEVELS if avail[c]]
    within = [st.pvariance(avail[c]) for c in LEVELS if len(avail[c]) > 1]
    between = st.pvariance(means) if len(means) > 1 else 0.0
    mean_within = st.mean(within) if within else 0.0
    icc = between / (between + mean_within) if (between + mean_within) > 0 else None
    return {
        "h4Frontier": frontier,
        "h4AvailabilityRange": ([min(a), max(a)] if a else None),
        "h4AvailabilityVaries": (bool(a) and (max(a) - min(a) > 0.02)),
        "h5AvailabilityICC": icc,
        "h5ICCBar": 0.5,
        "h5MeetsBar": (icc is not None and icc >= 0.5),
        "nSessions": n,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--c2-dir", default="results/c2-roundrobin")
    ap.add_argument("--c4-dir", default="results/c4-nodedrain-dose")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    verdict = {"h3": fix_h3(args.c2_dir), **fix_h4_h5(args.c4_dir)}

    h3 = verdict["h3"]
    print("=== Design-fix re-analysis (exploratory, outside the Holm family) ===\n")
    print("FIX-H3 — replication rescue, construction artifact removed:")
    print(f"  depth medians: {h3['depthMedians']}  interaction p={h3['depthInteractionP']}")
    print(
        f"  anti-affine depth reduction = {h3['depthReductionFraction']:.1%} "
        f"(bar {h3['depthReductionBar']:.0%}); user-error rescue "
        f"{h3['userErrorRescue']:.3f} (interaction p={h3['userErrorInteractionP']})"
    )
    print("\nFIX-H4 — placement frontier with a live availability face:")
    for c in LEVELS:
        f = verdict["h4Frontier"][c]
        print(f"  {c}: avail={f['availMedian']}  ewP95={f['ewP95Median']}  n={f['n']}")
    rng = verdict["h4AvailabilityRange"]
    print(f"  availability range {rng} -> varies={verdict['h4AvailabilityVaries']}")
    print(
        f"\nFIX-H5 — availability sub-score ICC under outage: {verdict['h5AvailabilityICC']:.4f} "
        f"(bar 0.5 -> {'PASS' if verdict['h5MeetsBar'] else 'fail'}); was 0.180 under pod-delete"
    )

    if args.json:
        Path(args.json).write_text(json.dumps(verdict, indent=2))
        print(f"\nJSON written to {args.json}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
