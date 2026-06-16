#!/usr/bin/env python3
"""V2-H3 confirmatory analysis: replication rescue under node-drain.

Registered test (`01-PREREGISTRATION.md` §V2-H3): an **ART ANOVA** with factors
``r ∈ {1, 3}`` × ``mode ∈ {packed, anti-affine}``; the registered effect is the
**interaction** — replication rescues availability only when replicas do not
share the failure domain (r=3 anti-affine ≪ r=1; r=3 packed ≈ r=1).  Two
co-primary outcomes, **both-must-pass**:

1. **EndpointSlice trough depth** — registered margin 1.0 pod (the M2 A/A 95%
   noise band).  Operationalized here as a **fraction of app ready endpoints
   lost** (deviation D-2026-06-15-01): absolute pod depth scales with the
   replica count (r=3 packed loses 3 pods vs r=1's 1) so the registered
   packed≈r1 TOST control cannot hold in pod units; the fraction is r-invariant.
   The registered 1.0-pod margin is expressed as a fraction by dividing by the
   r=1 app baseline (1.0 pod ÷ r=1 ready ≈ 0.09 for online-boutique).  Trough
   duration (s) is reported alongside.
2. **user-route error rate** — registered margin 0.302.  The during-chaos
   user-route error rate ``user_err_during`` (`user_error_rate(latency,
   'during-chaos')`), the metric the 0.302 band was calibrated on; node-drain
   now emits a user-facing route (PR #290) so it is available.

The packing control is a **TOST**: r=3 packed must fall within the A/A
equivalence band of r=1; falling outside flags the instrument, not a finding.

Data shape (node-drain): each session is one drain = one measurement; the
metrics live at the **top level** of the condition file (not ``iterations[]``).
The unit of analysis is the **session** (n=8 per cell).

Operationalization notes (the prereg pins the test + margins, not every detail):

- **Trough depth** is the fractional ready-endpoint loss over the **app**
  services in the 15s EndpointSlice time series (infra services — chaos/litmus
  controllers, ``*-external`` mirrors, loadgenerator — are excluded so they do
  not dilute the denominator): ``(baseline − min(during+post)) / baseline``,
  with ``baseline`` the last pre-chaos total ready.  Duration reuses
  :func:`m2_aa_analysis.es_trough_duration_real`.
- **r=1 in the 2×2 ART**: r=1's two modes are physically identical, so each r=1
  session enters **both** mode columns.  The direct **r3-anti vs r3-packed**
  Mann–Whitney is reported alongside as an assumption-light cross-check.
- **TOST** uses a **two-sample** bootstrap **90% CI** (= 1−2α at α=0.05) of
  ``mean(packed) − mean(r1)`` ⊂ ±band — resampled independently (NOT the
  packed×r1 Cartesian product, which understates variance).

Usage::

    uv run python scripts/c2_h3_anova.py --results-dir results/c2
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import statistics as st
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from chaosprobe.metrics.statistics import art_anova, mann_whitney_u

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:  # `python scripts/c2_h3_anova.py` adds it; imports may not
    sys.path.insert(0, _SCRIPTS_DIR)

from m2_aa_analysis import (  # noqa: E402  (sys.path bootstrap above)
    _series_total_ready,
    es_trough_duration_real,
    user_error_rate,
)

#: Registered margins (M2 A/A 95% noise bands).  DEPTH_MARGIN_POD is the absolute
#: 1.0-pod band; it is converted to a fraction at analysis time by dividing by
#: the r=1 app baseline (deviation D-2026-06-15-01).  ERROR_MARGIN is already a
#: rate so it applies directly to ``user_err_during``.
DEPTH_MARGIN_POD = 1.0  # pods (registered); fractional margin = this / r1 baseline
ERROR_MARGIN = 0.302  # user-route error-rate fraction (registered)
MODES = ("packed", "anti-affine")

#: Per-test alpha for the ART interaction.  V2-H3 is in the confirmatory family
#: Holm-corrected across all campaigns (applied later); this is the raw per-test
#: threshold the interaction must clear here.
ALPHA = 0.05

#: Substrings of non-app services in the EndpointSlice series — chaos/litmus
#: controllers, the NodePort ``*-external`` mirror (double-counts frontend), and
#: the load generator (not an availability target).  Excluded from the trough
#: denominator so they do not dilute the fractional depth.
_INFRA_PATTERNS = ("chaos-", "litmus", "workflow-controller", "event-tracker", "loadgenerator")


def app_services_from_series(ets: Dict[str, object]) -> List[str]:
    """The **app** services in the EndpointSlice time series (infra excluded)."""
    services: set = set()
    for smp in ets.get("samples") or []:
        services |= set((smp.get("services") or {}).keys())
    return sorted(
        s
        for s in services
        if not s.endswith("-external") and not any(p in s for p in _INFRA_PATTERNS)
    )


def trough_depth_fraction(
    ets: Dict[str, object], app_services: List[str]
) -> Tuple[Optional[float], Optional[float]]:
    """Fractional ready-endpoint loss during the drain + the baseline ready.

    Returns ``(fraction, baseline)`` where ``baseline`` = total ready (over the
    app services) in the last pre-chaos sample and ``fraction = (baseline −
    min(total ready over during+post)) / baseline`` (clamped to ``[0, 1]``).
    Both are ``None`` when there is no usable pre-chaos baseline; ``baseline`` is
    returned so the r=1 cell can set the registered 1.0-pod margin as a fraction.
    """
    samples = ets.get("samples") or []
    pre = [s for s in samples if s.get("phase") == "pre-chaos"]
    if not pre:
        return None, None
    baseline = _series_total_ready(pre[-1], app_services)
    if baseline is None or baseline <= 0:
        return None, None
    after_vals = [
        v
        for s in samples
        if s.get("phase") in ("during-chaos", "post-chaos")
        for v in [_series_total_ready(s, app_services)]
        if v is not None
    ]
    lost = (baseline - min(after_vals)) if after_vals else 0.0
    return max(0.0, min(1.0, lost / baseline)), float(baseline)


def _condition_file(session_dir: str) -> Optional[str]:
    """The single node-drain condition file (``f-NNN.json``) in a session dir."""
    cands = [
        p
        for p in sorted(glob.glob(os.path.join(session_dir, "f-*.json")))
        if "partial" not in os.path.basename(p)
    ]
    return cands[0] if cands else None


class Session:
    """One node-drain session: its cell (r, mode) and per-session outcomes."""

    def __init__(self, run, replicas, mode, depth, baseline, duration, error):
        self.run = run
        self.replicas = replicas
        self.mode = mode
        self.depth = depth  # fractional trough depth
        self.baseline = baseline  # app ready at baseline (for the r=1 margin)
        self.duration = duration
        self.error = error  # user_err_during


def _rejection_reason(v2: Dict[str, Any]) -> Optional[str]:
    """Why this v2 session must be excluded, or ``None`` when it is usable.

    Enforces the registered taint rule (pre-registration §taint): no result is
    quoted from a **rejected** condition (its placement failed live
    verification — e.g. an infeasible packing leaving deployments Pending) or
    from a condition whose **every** iteration is tainted. A node-drain session
    carries one condition; its record lives in ``v2.perLevel``.
    """
    per_level = v2.get("perLevel") or []
    if not per_level:
        return "no perLevel record"
    reasons: List[str] = []
    for rec in per_level:
        if not rec.get("accepted", False):
            rej = rec.get("rejectionReasons") or ["not accepted"]
            reasons.append(f"{rec.get('condition', '?')}:{','.join(rej)}")
            continue
        iters = rec.get("perIteration") or []
        if iters and all(it.get("taintReasons") for it in iters):
            reasons.append(f"{rec.get('condition', '?')}:all-iterations-tainted")
    return "; ".join(reasons) if reasons else None


def collect_sessions(results_dir: str) -> Tuple[List[Session], List[str]]:
    """Every C2 session's cell + co-primary outcomes (top-level condition metrics)."""
    sessions: List[Session] = []
    warnings: List[str] = []
    for summ_path in sorted(glob.glob(os.path.join(results_dir, "*", "summary.json"))):
        session_dir = os.path.dirname(summ_path)
        run = os.path.basename(session_dir)
        with open(summ_path) as fh:
            summary = json.load(fh)
        v2 = summary.get("v2Session") or {}
        replicas, mode = v2.get("replicas"), v2.get("mode")
        if replicas is None or mode is None:
            warnings.append(f"{run}: not a v2 node-drain session — skipped")
            continue
        rejected = _rejection_reason(v2)
        if rejected is not None:
            # Registered taint rule (pre-registration §taint): "No result is
            # ever quoted from a rejected session or from a tainted iteration."
            warnings.append(f"{run}: rejected/tainted placement ({rejected}) — excluded")
            continue
        cond = _condition_file(session_dir)
        if cond is None:
            warnings.append(f"{run}: no condition file — skipped")
            continue
        with open(cond) as fh:
            metrics = (json.load(fh) or {}).get("metrics") or {}
        ets = metrics.get("endpointSliceTimeSeries") or {}
        depth, baseline = trough_depth_fraction(ets, app_services_from_series(ets))
        duration = es_trough_duration_real(ets, app_services_from_series(ets))
        error = user_error_rate(metrics.get("latency") or {}, "during-chaos")
        sessions.append(Session(run, replicas, mode, depth, baseline, duration, error))
    return sessions, warnings


def _vals(sessions: List[Session], pred, attr) -> List[float]:
    return [
        getattr(s, attr) for s in sessions if pred(s) and isinstance(getattr(s, attr), (int, float))
    ]


def _art_rows(sessions: List[Session], attr: str) -> List[Tuple[object, object, float]]:
    """ART rows (r, mode, value); each r=1 session enters BOTH mode columns."""
    rows: List[Tuple[object, object, float]] = []
    for s in sessions:
        v = getattr(s, attr)
        if not isinstance(v, (int, float)):
            continue
        if s.replicas == 1:
            rows.append((1, "packed", float(v)))
            rows.append((1, "anti-affine", float(v)))
        else:
            rows.append((s.replicas, s.mode, float(v)))
    return rows


def _two_sample_equiv(
    a: Sequence[float], b: Sequence[float], band: float, n_resamples: int = 2000, seed: int = 42
) -> Dict[str, object]:
    """TOST-style equivalence: two-sample bootstrap 90% CI of mean(a)−mean(b) ⊂ ±band.

    ``a`` and ``b`` are resampled INDEPENDENTLY (with replacement); the 90% CI
    (= 1−2α at α=0.05) of the mean difference must lie within ±band.  This
    replaces a Cartesian product of pairwise differences, which would understate
    variance ~√(n_a·n_b) and bias toward PASS.
    """
    if not a or not b or band is None:
        return {"ciLow": None, "ciHigh": None, "withinBand": False}
    rng = random.Random(seed)
    diffs = sorted(
        st.fmean(rng.choices(a, k=len(a))) - st.fmean(rng.choices(b, k=len(b)))
        for _ in range(n_resamples)
    )
    lo = diffs[int(0.05 * n_resamples)]
    hi = diffs[int(0.95 * n_resamples) - 1]
    return {
        "ciLow": round(lo, 4),
        "ciHigh": round(hi, 4),
        "withinBand": bool(-band <= lo and hi <= band),
    }


def _outcome_block(
    sessions: List[Session], attr: str, margin: Optional[float]
) -> Dict[str, object]:
    """ART interaction + rescue margin + TOST control for one co-primary."""
    r1 = _vals(sessions, lambda s: s.replicas == 1, attr)
    anti = _vals(sessions, lambda s: s.replicas == 3 and s.mode == "anti-affine", attr)
    packed = _vals(sessions, lambda s: s.replicas == 3 and s.mode == "packed", attr)
    interaction = art_anova(_art_rows(sessions, attr)).get("interaction")
    interaction_p = (interaction or {}).get("p")
    interaction_sig = bool(interaction_p is not None and interaction_p < ALPHA)
    rescue_obs = (st.median(r1) - st.median(anti)) if (r1 and anti) else None
    tost = (
        _two_sample_equiv(packed, r1, margin)
        if margin is not None
        else _two_sample_equiv([], [], 0)
    )
    return {
        "n": {"r1": len(r1), "r3_packed": len(packed), "r3_anti": len(anti)},
        "median": {
            "r1": round(st.median(r1), 4) if r1 else None,
            "r3_packed": round(st.median(packed), 4) if packed else None,
            "r3_anti": round(st.median(anti), 4) if anti else None,
        },
        "artInteraction": interaction,
        "interactionSig": interaction_sig,
        "directAntiVsPacked": mann_whitney_u(anti, packed) if (anti and packed) else None,
        "rescueObserved": round(rescue_obs, 4) if rescue_obs is not None else None,
        "rescueMargin": round(margin, 4) if margin is not None else None,
        "rescueMet": bool(rescue_obs is not None and margin is not None and rescue_obs >= margin),
        "tostPackedEqR1": {**tost, "band": round(margin, 4) if margin is not None else None},
    }


def _median_or_none(values) -> Optional[float]:
    clean = [float(v) for v in values if isinstance(v, (int, float))]
    return round(st.median(clean), 4) if clean else None


def _degenerate_warnings(sessions: List[Session]) -> List[str]:
    """Flag a co-primary that is unmeasured / has no variance (conjunction can't pass)."""
    out: List[str] = []
    for label, attr in [("trough-depth", "depth"), ("user-route error rate", "error")]:
        vals = [getattr(s, attr) for s in sessions if isinstance(getattr(s, attr), (int, float))]
        if not vals:
            out.append(
                f"DEGENERATE: co-primary '{label}' is unavailable in every session "
                "(no numeric values) — the both-must-pass conjunction cannot be evaluated."
            )
        elif len(set(round(v, 6) for v in vals)) == 1:
            out.append(
                f"DEGENERATE: co-primary '{label}' has no variance across sessions "
                f"(all = {vals[0]}) — the interaction/rescue cannot be assessed."
            )
    return out


def analyze(results_dir: str) -> Dict[str, object]:
    """The full V2-H3 analysis as one JSON-ready dict (both co-primaries)."""
    sessions, warnings = collect_sessions(results_dir)
    # Express the registered 1.0-pod depth margin as a fraction of the r=1 app
    # baseline (D-2026-06-15-01): 1.0 pod / median(r1 baseline ready).
    r1_baselines = [s.baseline for s in sessions if s.replicas == 1 and s.baseline]
    depth_margin = (DEPTH_MARGIN_POD / st.median(r1_baselines)) if r1_baselines else None
    warnings += _degenerate_warnings(sessions)
    depth = _outcome_block(sessions, "depth", depth_margin)
    error = _outcome_block(sessions, "error", ERROR_MARGIN)
    duration_median = {
        "r1": _median_or_none([s.duration for s in sessions if s.replicas == 1]),
        "r3_packed": _median_or_none(
            [s.duration for s in sessions if s.replicas == 3 and s.mode == "packed"]
        ),
        "r3_anti": _median_or_none(
            [s.duration for s in sessions if s.replicas == 3 and s.mode == "anti-affine"]
        ),
    }
    # V2-H3 support (prereg §V2-H3): on EACH co-primary the ART interaction is
    # significant AND the rescue margin is met, AND the packing control passes
    # its TOST on both (instrument valid).
    conj = bool(
        depth["interactionSig"]
        and error["interactionSig"]
        and depth["rescueMet"]
        and error["rescueMet"]
        and depth["tostPackedEqR1"]["withinBand"]
        and error["tostPackedEqR1"]["withinBand"]
    )
    return {
        "nSessions": len(sessions),
        "depthMarginFraction": round(depth_margin, 4) if depth_margin is not None else None,
        "troughDepthFraction": depth,
        "userErrorRate": error,
        "troughDurationMedian_s": duration_median,
        "conjunctionRescue": conj,
        "warnings": warnings,
    }


def print_report(result: Dict[str, object]) -> None:
    print("V2-H3 — replication rescue under node-drain (ART ANOVA r × mode)")
    print(f"  sessions: {result['nSessions']}  depth margin: {result['depthMarginFraction']}")
    for label, key in [
        ("trough depth (fraction)", "troughDepthFraction"),
        ("user error rate", "userErrorRate"),
    ]:
        b = result[key]
        m = b["median"]
        p = (b["artInteraction"] or {}).get("p")
        tost = "within band" if b["tostPackedEqR1"]["withinBand"] else "OUTSIDE band"
        met = "MET" if b["rescueMet"] else "not met"
        print(f"\n  {label} — n {b['n']}")
        print(f"    medians: r1={m['r1']}  r3-packed={m['r3_packed']}  r3-anti={m['r3_anti']}")
        print(f"    ART interaction p: {p} (sig={b['interactionSig']})")
        print(f"    rescue (r1−anti)={b['rescueObserved']} vs margin {b['rescueMargin']} -> {met}")
        print(f"    TOST packed≈r1: {tost}")
    print(f"\n  trough duration median (s): {result['troughDurationMedian_s']}")
    print(
        "  CONJUNCTION (interaction sig + rescue on both co-primaries + packing "
        f"control): {result['conjunctionRescue']}"
    )
    for w in result["warnings"]:
        print(f"  ! {w}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--results-dir", default="results/c2", help="C2 node-drain sessions dir")
    parser.add_argument("--json", help="optional: write the analysis dict to this path")
    args = parser.parse_args(argv)
    result = analyze(args.results_dir)
    print_report(result)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"\nJSON written to {args.json}")
    return 0


if __name__ == "__main__":  # pragma: no cover  (CLI entrypoint)
    raise SystemExit(main())
