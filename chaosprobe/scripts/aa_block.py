#!/usr/bin/env python3
"""M2 A/A calibration block — supplementary variance outcomes + noise bands.

Companion to the canonical A/A analysis (``scripts/m2_aa_analysis.py``,
which owns the delta metrics, the pairing, the per-unit
null tests and the liveAchievedF identity check).  Per the D4
consolidation (``docs/design/M2-AA-REPORT.md`` §Decisions,
§Instrumentation gaps) there is ONE per-iteration extraction — this
script imports it from the canonical module
(:func:`m2_aa_analysis.extract_iteration` /
:func:`m2_aa_analysis.load_condition_outcomes`), so the supplementary
numbers can never drift from the canonical ones.  What stays here is the
broader scope the M2 report's variance table is sourced from:

1. **Variance-component estimation** per outcome — within-session
   (iteration-to-iteration) and between-session-within-pair variance, per
   f-condition and pooled, plus the 95 % A/A noise band: the distribution of
   paired |A - B| differences between identical sessions (per condition,
   pooled across complete pairs). These bands feed the M2 power analysis and
   the TBD-at-calibration numbers: the H1 >=15 % SESOI must exceed the band,
   the H3 margin/TOST equivalence band, H4's delta dominance margins,
   and the pre-window UDP-slope taint threshold.

2. **A/A-as-A/B null tests** — within each complete pair, each outcome is
   pushed through the paired comparison the A/B analyses use
   (Wilcoxon signed-rank, conditions as the pairing unit with session
   medians as values, exactly the unit the tests pair on; an
   iteration-level pairing is reported alongside as a higher-resolution
   sensitivity check). Any statistically significant A/A finding is flagged
   loudly: per the rule it triggers investigate -> fix -> rerun.

Outcomes (extracted per iteration from the raw ``f-XXX.json`` files by the
shared canonical extraction; tainted iterations — ``taintReasons`` +
``preChaosTaintReasons`` — are excluded from every outcome, with ``None``
rows preserving pairing alignment):

- east-west p95 latency, pre-chaos (H1, the D4 winner) and
  during-chaos (alt window): median over inter-service routes of the
  route p95 (``loadgenerator->`` routes excluded per DESIGN §4).
- during-churn UDP conntrack-entry drop (H2): cluster UDP entries
  (per-node phase mean, summed over nodes) pre-chaos minus during-chaos —
  the **absolute** drop (no ratio denominator); the percentage
  drop is reported as context only.
- all-protocol conntrack flush % (mechanism context).
- EndpointSlice trough depth (H3) + services driven to zero (blast
  radius). NOTE: the banked sessions carry only pre/during/post snapshots
  (no 15 s trough time series), so trough *duration* is proxied by the
  mean pod recovery time — flagged in the output.
- user-route error rate during fault (H3/H4); the whole-iteration
  Locust ``errorRate`` is reported alongside.
- pre-chaos-window UDP-entry slope (taint-threshold feed), entries/min.
- per-iteration resilience score (H5). Layered sub-scores are NOT yet
  implemented in the chaosprobe package (their definitions are set at the
  M2 commit), so only the aggregate is analyzed; the script notes this.

Pairs are derived from ``summary.json -> session.solverSeed`` plus the
cell fields (fault, replicas, mode, level grid, workers — mirroring the canonical
module's ``PairKey``): identical placements share a solverSeed, order
seeds differ, and a seed reused by a *different* cell never pairs.
Sessions without a ``summary.json`` (in-flight runs) are skipped; a cell
group with fewer than 2 sessions is reported as PENDING, not an error.

Memory: raw per-condition files are 20-100 MB; they are loaded one at a
time and reduced to per-iteration scalars immediately.

Usage
-----
    uv run python scripts/aa_block.py --results-dir results/aa
    uv run python scripts/aa_block.py --results-dir results/aa \
        --exclude 20260612-103215 --json aa_block.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics as st
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from chaosprobe.metrics.statistics import wilcoxon_signed_rank

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:  # `python scripts/aa_block.py` adds it; imports may not
    sys.path.insert(0, _SCRIPTS_DIR)

from m2_aa_analysis import (  # noqa: E402  (sys.path bootstrap above)
    load_condition_outcomes,
    summary_tainted_iterations,
)

ALPHA = 0.05

# Supplementary outcomes: key -> (label, unit).  Keys are the shared
# canonical extraction's (m2_aa_analysis.ITERATION_OUTCOMES) — the
# canonical metrics plus the broader variance outcomes.
OUTCOMES: List[Tuple[str, str, str]] = [
    ("ew_p95_pre_ms", "east-west p95 latency, pre-chaos (H1)", "ms"),
    ("ew_p95_during_ms", "east-west p95 latency, during-chaos (H1 alt window)", "ms"),
    ("udp_conntrack_drop_entries", "during-churn UDP conntrack drop (H2)", "entries"),
    ("udp_conntrack_drop_pct", "during-churn UDP drop, % of pre (context)", "%"),
    ("conntrack_flush_pct", "all-proto conntrack flush, % of pre (context)", "%"),
    ("es_trough_depth_pods", "EndpointSlice trough depth (H3)", "pods"),
    ("es_zero_services", "services driven to 0 ready (blast radius)", "services"),
    ("trough_duration_s", "trough duration proxy = mean pod recovery (H3)", "s"),
    (
        "trough_duration_real_s",
        "trough duration from EndpointSlice time series (H3; None pre-sampler)",
        "s",
    ),
    ("user_err_during", "user-route error rate, during-chaos (H3/H4)", "rate"),
    ("loadgen_err", "Locust whole-iteration error rate (context)", "rate"),
    ("udp_preslope_epm", "pre-window UDP-entry slope (taint feed)", "entries/min"),
    ("score", "aggregate resilience score (H5)", "points"),
]


# ---------------------------------------------------------------------------
# Session loading (extraction shared with the canonical analysis)
# ---------------------------------------------------------------------------


def load_session(session_dir: str) -> Optional[dict]:
    """Load one session: placement metadata + per-condition per-iteration outcomes.

    Returns None (with a note) when the session has no usable summary.json —
    e.g. an in-flight run — so discovery can skip it instead of failing.
    The per-iteration values are the shared canonical extraction
    (``m2_aa_analysis.load_condition_outcomes``): taint-excluded, with
    ``None`` rows preserving index alignment.
    """
    name = os.path.basename(session_dir.rstrip("/"))
    spath = os.path.join(session_dir, "summary.json")
    if not os.path.isfile(spath):
        print(f"  [skip] {name}: no summary.json (in flight / incomplete)")
        return None
    with open(spath) as fh:
        summary = json.load(fh)
    session_block = summary.get("session") or summary.get("v2Session")  # or legacy key
    if not session_block:
        print(f"  [skip] {name}: no session block (not a placement session)")
        return None

    per_level = {
        lvl["condition"]: lvl
        for lvl in session_block.get("perLevel", [])
        if lvl.get("condition")
    }
    conditions = sorted(per_level)
    tainted, taints = summary_tainted_iterations(list(per_level.values()))
    achieved: Dict[str, List[float]] = {}
    assignments: Dict[str, Dict[str, str]] = {}
    for cond, lvl in per_level.items():
        achieved[cond] = [pi.get("liveAchievedF") for pi in lvl.get("perIteration", [])]
        assignments[cond] = dict(lvl.get("assignment") or {})

    faults = sorted(summary.get("faults") or {})
    session = {
        "name": name,
        "fault": faults[0] if faults else "",
        "solverSeed": session_block.get("solverSeed"),
        "orderSeed": session_block.get("orderSeed"),
        "replicas": session_block.get("replicas"),
        "mode": session_block.get("mode"),
        "levels": sorted(float(level) for level in session_block.get("levels") or []),
        "workers": sorted(str(worker) for worker in session_block.get("workers") or []),
        "conditionOrder": session_block.get("conditionOrder"),
        "conditions": conditions,
        "achievedF": achieved,
        "assignments": assignments,
        "taints": taints,
        "values": {},  # cond -> outcome -> [per-iteration values]
    }
    del summary  # free the 60+ MB summary before touching the raw files

    for cond in conditions:
        per_outcome = load_condition_outcomes(session_dir, cond, tainted, taints)
        if per_outcome is None:
            print(f"  [warn] {name}/{cond}: raw file missing; condition skipped")
            continue
        session["values"][cond] = per_outcome
    return session


def discover_sessions(results_dir: str, exclude: Sequence[str]) -> List[dict]:
    sessions = []
    for entry in sorted(os.listdir(results_dir)):
        path = os.path.join(results_dir, entry)
        if not os.path.isdir(path):
            continue
        if entry in exclude:
            print(f"  [skip] {entry}: excluded on the command line")
            continue
        s = load_session(path)
        if s is not None:
            sessions.append(s)
    return sessions


def group_pairs(sessions: List[dict]) -> Dict[str, List[dict]]:
    """Pair label -> sessions of one identical cell.

    Identical placements share a solverSeed, but a solverSeed reused by a
    *different* cell (another fault / replicas / mode / level grid) must
    never form an "A/A pair" — this mirrors the canonical module's
    six-field ``PairKey`` (fault, solverSeed, replicas, mode, levels,
    workers).  Labels stay the familiar ``pair-seed<N>`` when a seed
    maps to one cell (the normal single-fault A/A dir); cells that share
    a seed get the distinguishing fields appended.
    """
    groups: Dict[tuple, List[dict]] = {}
    for s in sessions:
        key = (
            s["solverSeed"],
            s["fault"],
            s["replicas"],
            s["mode"],
            tuple(s["levels"]),
            tuple(s["workers"]),
        )
        groups.setdefault(key, []).append(s)
    seed_cells: Dict[object, int] = {}
    for key in groups:
        seed_cells[key[0]] = seed_cells.get(key[0], 0) + 1
    out: Dict[str, List[dict]] = {}
    for key in sorted(groups, key=lambda k: tuple(str(part) for part in k)):
        seed, fault, replicas, mode = key[:4]
        label = f"pair-seed{seed}"
        if seed_cells[seed] > 1:
            label = f"pair-seed{seed}-{fault or 'nofault'}-r{replicas}-{mode}"
        while label in out:  # same fault/r/mode, different level grid / workers
            label += "+"
        out[label] = groups[key]
    return out


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def _clean(vals: Sequence[Optional[float]]) -> List[float]:
    return [v for v in vals if isinstance(v, (int, float)) and not math.isnan(v)]


def cond_value(session: dict, cond: str, outcome: str) -> Optional[float]:
    """The session x condition unit value: median across iterations (the
    analyses use session medians as units)."""
    vals = _clean((session["values"].get(cond) or {}).get(outcome) or [])
    return st.median(vals) if vals else None


def _quantile(sorted_vals: List[float], q: float) -> float:
    """Linear-interpolated quantile of a small sorted sample."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def variance_components(sessions: List[dict], pairs: Dict[str, List[dict]], outcome: str) -> dict:
    """Within-session and between-session-within-pair variance, per condition + pooled."""
    conditions = sorted({c for s in sessions for c in s["conditions"]})
    per_cond: Dict[str, dict] = {}
    for cond in conditions:
        within = [
            st.pvariance(v)
            for s in sessions
            if len(v := _clean((s["values"].get(cond) or {}).get(outcome) or [])) >= 2
        ]
        between = []
        for members in pairs.values():
            if len(members) < 2:
                continue
            meds = _clean([cond_value(s, cond, outcome) for s in members[:2]])
            if len(meds) == 2:
                between.append(st.pvariance(meds))
        per_cond[cond] = {
            "sig2_within": st.mean(within) if within else None,
            "sig2_between_pair": st.mean(between) if between else None,
            "n_within_cells": len(within),
            "n_pairs": len(between),
        }
    pooled_w = _clean([c["sig2_within"] for c in per_cond.values()])
    pooled_b = _clean([c["sig2_between_pair"] for c in per_cond.values()])
    sig2_w = st.mean(pooled_w) if pooled_w else None
    sig2_b = st.mean(pooled_b) if pooled_b else None
    share = (
        sig2_b / (sig2_b + sig2_w)
        if (sig2_b is not None and sig2_w is not None and (sig2_b + sig2_w) > 0)
        else None
    )
    return {
        "perCondition": per_cond,
        "pooled": {
            "sig2_within": sig2_w,
            "sig2_between_pair": sig2_b,
            "sd_within": math.sqrt(sig2_w) if sig2_w is not None else None,
            "sd_between_pair": math.sqrt(sig2_b) if sig2_b is not None else None,
            "between_share": share,
        },
    }


def noise_band(pairs: Dict[str, List[dict]], outcome: str) -> dict:
    """Paired |A - B| differences between identical sessions, per condition + pooled."""
    per_cond: Dict[str, List[float]] = {}
    grand: List[float] = []
    for members in pairs.values():
        if len(members) < 2:
            continue
        a, b = members[:2]
        for cond in sorted(set(a["conditions"]) & set(b["conditions"])):
            va, vb = cond_value(a, cond, outcome), cond_value(b, cond, outcome)
            if va is None or vb is None:
                continue
            per_cond.setdefault(cond, []).append(abs(va - vb))
            grand.extend([va, vb])
    diffs = sorted(d for v in per_cond.values() for d in v)
    if not diffs:
        return {"perCondition": {}, "n": 0}
    mean_level = st.mean(grand) if grand else None
    p95 = _quantile(diffs, 0.95)
    return {
        "perCondition": {c: sorted(v) for c, v in sorted(per_cond.items())},
        "n": len(diffs),
        "median_abs_diff": st.median(diffs),
        "p95_abs_diff": p95,
        "max_abs_diff": diffs[-1],
        "mean_level": mean_level,
        "p95_pct_of_level": (
            100.0 * p95 / abs(mean_level)
            if mean_level is not None and abs(mean_level) > 1e-9
            else None
        ),
    }


def null_tests(pairs: Dict[str, List[dict]], outcome: str) -> dict:
    """A/A-as-A/B paired Wilcoxon tests, per complete pair and pooled."""
    per_pair: Dict[str, dict] = {}
    pooled_a: List[float] = []
    pooled_b: List[float] = []
    for label, members in pairs.items():
        if len(members) < 2:
            continue
        a, b = members[:2]
        conds = sorted(set(a["conditions"]) & set(b["conditions"]))
        # Condition-level: the pairing unit (session medians per level).
        ca, cb = [], []
        for cond in conds:
            va, vb = cond_value(a, cond, outcome), cond_value(b, cond, outcome)
            if va is not None and vb is not None:
                ca.append(va)
                cb.append(vb)
        # Iteration-level sensitivity: iteration i of condition c in A vs the
        # same (c, i) in B. Iterations within a condition are exchangeable, so
        # this index pairing is arbitrary but valid under the null.
        ia, ib = [], []
        for cond in conds:
            xs = (a["values"].get(cond) or {}).get(outcome) or []
            ys = (b["values"].get(cond) or {}).get(outcome) or []
            for x, y in zip(xs, ys):
                # NaN must not pair: it joins neither Wilcoxon rank sum but
                # still inflates n, silently biasing the p-value.
                if len(_clean([x])) and len(_clean([y])):
                    ia.append(float(x))
                    ib.append(float(y))
        entry: Dict[str, object] = {"sessions": [a["name"], b["name"]]}
        entry["condition_level"] = wilcoxon_signed_rank(ca, cb) if len(ca) >= 2 else None
        entry["iteration_level"] = wilcoxon_signed_rank(ia, ib) if len(ia) >= 2 else None
        per_pair[label] = entry
        pooled_a.extend(ca)
        pooled_b.extend(cb)
    pooled = wilcoxon_signed_rank(pooled_a, pooled_b) if len(pooled_a) >= 2 else None
    return {"perPair": per_pair, "pooled_condition_level": pooled}


def significant_findings(tests_by_outcome: Dict[str, dict], alpha: float) -> List[str]:
    """Every A/A test with p < alpha, named — the halt trigger."""
    hits = []
    for outcome, t in tests_by_outcome.items():
        for pair, entry in t["perPair"].items():
            for level in ("condition_level", "iteration_level"):
                w = entry.get(level)
                p = w.get("p_two_sided") if w else None
                if isinstance(p, (int, float)) and p < alpha:
                    hits.append(
                        f"{outcome} / {pair} / {level}: p={w['p_two_sided']} "
                        f"(W={w['w_statistic']}, n={w['n_pairs']})"
                    )
        pooled = t.get("pooled_condition_level")
        if (
            pooled
            and isinstance(pooled.get("p_two_sided"), (int, float))
            and pooled["p_two_sided"] < alpha
        ):
            hits.append(
                f"{outcome} / pooled-conditions: p={pooled['p_two_sided']} "
                f"(W={pooled['w_statistic']}, n={pooled['n_pairs']})"
            )
    return hits


# ---------------------------------------------------------------------------
# Sanity checks on the A/A design itself
# ---------------------------------------------------------------------------


def design_checks(pairs: Dict[str, List[dict]]) -> List[str]:
    """Identical-placement invariants a pair must satisfy; violations are anomalies."""
    notes = []
    for label, members in pairs.items():
        if len(members) < 2:
            continue
        a, b = members[:2]
        if a["orderSeed"] == b["orderSeed"]:
            notes.append(
                f"{label}: orderSeeds identical ({a['orderSeed']}) — "
                "condition order was supposed to be re-randomized"
            )
        for cond in sorted(set(a["conditions"]) & set(b["conditions"])):
            if a["assignments"].get(cond) != b["assignments"].get(cond):
                notes.append(
                    f"{label}/{cond}: solver assignments differ between "
                    "sessions — pair is NOT identical-placement"
                )
            fa = _clean(a["achievedF"].get(cond) or [])
            fb = _clean(b["achievedF"].get(cond) or [])
            if fa and fb and abs(st.median(fa) - st.median(fb)) > 1e-6:
                notes.append(
                    f"{label}/{cond}: achieved f differs "
                    f"({st.median(fa):.4f} vs {st.median(fb):.4f})"
                )
    return notes


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _fmt(v: Optional[float], digits: int = 3) -> str:
    if v is None:
        return "-"
    return f"{v:.{digits}g}" if abs(v) < 1e6 else f"{v:.3e}"


def report(sessions: List[dict], pairs: Dict[str, List[dict]], alpha: float) -> dict:
    out: dict = {"sessions": [], "pairs": {}, "outcomes": {}, "anomalies": [], "alpha": alpha}

    print("\n=== A/A calibration block (M2) ===\n")
    print("Sessions (pairs keyed by session.solverSeed + cell fields):")
    for s in sessions:
        print(
            f"  {s['name']}  solverSeed={s['solverSeed']} orderSeed={s['orderSeed']} "
            f"r={s['replicas']} mode={s['mode']} conditions={len(s['conditions'])} "
            f"taints={len(s['taints'])}"
        )
        out["sessions"].append(
            {k: s[k] for k in ("name", "solverSeed", "orderSeed", "replicas", "mode", "taints")}
        )
    complete = 0
    for label, members in pairs.items():
        names = [m["name"] for m in members]
        if len(members) >= 2:
            status = "complete"
            complete += 1
            if len(members) > 2:
                status = f"complete (extra sessions ignored: {names[2:]})"
        else:
            status = f"PENDING — 1/2 sessions banked ({names[0]})"
        print(f"  {label}: {status}")
        out["pairs"][label] = {"sessions": names, "status": status}
    if complete == 0:
        print("\nNo complete pair yet — variance bands and null tests need >=1 pair.")
        return out

    taints = [f"{s['name']}: {t}" for s in sessions for t in s["taints"]]
    if taints:
        print("\nTainted iterations (never quoted in results):")
        for t in taints:
            print(f"  {t}")
    else:
        print("\nNo tainted iterations in any banked session.")
    out["taints"] = taints

    anomalies = design_checks(pairs)
    out["anomalies"] = anomalies
    if anomalies:
        print("\nDESIGN-CHECK ANOMALIES:")
        for n in anomalies:
            print(f"  !! {n}")

    print(
        "\nNOTE  layered sub-scores (availability / mechanism-reconvergence /"
        "\n      user-tail, H5) are not implemented in the chaosprobe package"
        "\n      yet; only the aggregate per-iteration resilience score is"
        "\n      analyzed here. Their definitions are set from the M2 A/A calibration."
        "\nNOTE  EndpointSlice data has pre/during/post snapshots only (no 15 s"
        "\n      trough series); trough *duration* is proxied by mean pod"
        "\n      recovery time. Flag for the M2 instrumentation review."
    )

    tests_by_outcome: Dict[str, dict] = {}
    for key, label, unit in OUTCOMES:
        vc = variance_components(sessions, pairs, key)
        band = noise_band(pairs, key)
        tests = null_tests(pairs, key)
        tests_by_outcome[key] = tests
        out["outcomes"][key] = {
            "label": label,
            "unit": unit,
            "varianceComponents": vc,
            "noiseBand": band,
            "nullTests": tests,
        }

        print(f"\n--- {label} [{unit}] ---")
        print(f"  {'cond':<8}{'per-session medians (A|B per pair)':<44}{'|A-B|':<18}")
        for cond in sorted({c for s in sessions for c in s["conditions"]}):
            meds = []
            for members in pairs.values():
                vals = [cond_value(s, cond, key) for s in members[:2]]
                meds.append("|".join(_fmt(v, 4) for v in vals))
            dstr = ",".join(_fmt(d, 3) for d in (band.get("perCondition") or {}).get(cond, []))
            print(f"  {cond:<8}{'  '.join(meds):<44}{dstr:<18}")
        p = vc["pooled"]
        print(
            f"  variance: sd_within(iter)={_fmt(p['sd_within'])}  "
            f"sd_between_pair(session)={_fmt(p['sd_between_pair'])}  "
            f"between-session share={_fmt(p['between_share'], 2)}"
        )
        if band.get("n"):
            rel = (
                f"  (= {band['p95_pct_of_level']:.1f}% of mean level {_fmt(band['mean_level'])})"
                if band.get("p95_pct_of_level") is not None
                else ""
            )
            print(
                f"  A/A noise band (n={band['n']} paired |A-B|): "
                f"median={_fmt(band['median_abs_diff'])}  "
                f"p95={_fmt(band['p95_abs_diff'])}  "
                f"max={_fmt(band['max_abs_diff'])}{rel}"
            )
        for pair, entry in tests["perPair"].items():
            parts = []
            for lvl, tag in (("condition_level", "cond"), ("iteration_level", "iter")):
                w = entry.get(lvl)
                if w is None:
                    parts.append(f"{tag}: n/a")
                    continue
                sig = "  *** SIGNIFICANT A/A FINDING ***" if w["p_two_sided"] < alpha else ""
                parts.append(
                    f"{tag}: W={w['w_statistic']} p={w['p_two_sided']} n={w['n_pairs']}{sig}"
                )
            print(f"  null test {pair}: " + " | ".join(parts))
        pooled = tests.get("pooled_condition_level")
        if pooled:
            sig = "  *** SIGNIFICANT A/A FINDING ***" if pooled["p_two_sided"] < alpha else ""
            print(
                f"  null test pooled conditions x pairs: W={pooled['w_statistic']} "
                f"p={pooled['p_two_sided']} n={pooled['n_pairs']}{sig}"
            )

    hits = significant_findings(tests_by_outcome, alpha)
    out["significantFindings"] = hits
    print("\n=== Verdict ===")
    if hits:
        print(
            f"  *** {len(hits)} STATISTICALLY SIGNIFICANT A/A FINDING(S) at alpha={alpha} ***\n"
            "  Rule: investigate -> fix the doctor gates / taint rules /\n"
            "  instrumentation -> RERUN the A/A block before any comparison runs.\n"
            "  (A second significant finding after a fix HALTS the campaign.)"
        )
        for h in hits:
            print(f"    - {h}")
        if all("/ iteration_level" in h for h in hits):
            print(
                "  Note: all hits are iteration-level (sensitivity pairing). Within a\n"
                "  pair, iteration-level pairs share the session, so this pairing is\n"
                "  sensitive to a constant session-level offset — i.e. it detects\n"
                "  between-session variance. The analyses pair at the\n"
                "  session-median unit (the condition-level tests above)."
            )
    else:
        print(
            f"  No statistically significant A/A finding at alpha={alpha} across all\n"
            "  outcomes, pairs, and pairing levels. The noise bands above feed the\n"
            "  M2 power analysis and the TBD SESOIs/margins (H1 >=15% SESOI,\n"
            "  H3 margin/TOST band, H4 deltas, UDP-slope taint threshold)."
        )
    # The headline SESOI sanity line for H1.
    h1 = out["outcomes"]["ew_p95_pre_ms"]["noiseBand"]
    if h1.get("p95_pct_of_level") is not None:
        verdict = "EXCEEDS" if h1["p95_pct_of_level"] < 15.0 else "DOES NOT EXCEED"
        print(
            f"\n  H1 SESOI check: 15% of the mean east-west p95 level vs the A/A\n"
            f"  p95 band of {h1['p95_pct_of_level']:.1f}% -> the 15% SESOI "
            f"{verdict} the A/A noise band."
        )
    return out


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--results-dir", default="results/aa", help="directory of A/A session outputs"
    )
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="session dir name to exclude (repeatable; in-flight runs are auto-skipped)",
    )
    ap.add_argument("--json", dest="json_out", help="also write the full results as JSON here")
    ap.add_argument("--alpha", type=float, default=ALPHA, help="significance level (default 0.05)")
    args = ap.parse_args(argv)

    print(f"Scanning {args.results_dir} ...")
    sessions = discover_sessions(args.results_dir, args.exclude)
    if not sessions:
        raise SystemExit("No complete A/A sessions found.")
    pairs = group_pairs(sessions)
    out = report(sessions, pairs, args.alpha)
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(out, fh, indent=2, default=str)
        print(f"\nJSON written to {args.json_out}")


if __name__ == "__main__":  # pragma: no cover
    main()
