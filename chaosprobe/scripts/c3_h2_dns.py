#!/usr/bin/env python3
"""C3 / V2-H2 confirmatory analysis: placement-dependence + DNS intervention.

Registered test (``01-PREREGISTRATION.md`` §V2-H2) — a **two-part, both-must-pass
conjunction** over the registered **absolute** during-churn UDP-conntrack drop
(``udp_conntrack_drop_entries`` = pre-chaos − during-chaos cluster UDP entries):

1. **(a) placement-dependence (cache-off arms).** Paired Wilcoxon signed-rank,
   one-sided, on per-session ``(spread f=1 − packed f=0)`` UDP-drop differences,
   cache-off only — the v1-H2 replication. Directional, no ratio denominator.
2. **(b) mechanism intervention (within-spread, paired).** One-sided Wilcoxon
   signed-rank of the per-pair **shrinkage** of spread's UDP drop (cache-on vs
   cache-off) against the **50 %** bar (freeze decision D6). Shrinkage =
   ``(off − on) / off``; the denominator is spread's cache-off drop.

**Combination:** (a) AND (b); the single input to the outer Holm family is
``max(p_a, p_b)``. **Secondary (registered, not in family):** the packed (f=0)
arm shows ~no cache effect (its UDP pool sits at the noise floor).

Per-hypothesis p-values are **uncorrected** here — final significance waits on
Holm across the confirmatory family once all campaigns land (so the conjunction
verdict below is the registered *direction + bar* check, reported with the raw
one-sided p-values).

**Data model.** C3 sessions are ``r = 1``, ``dnsCache ∈ {on, off}`` (the
``--v2-dns-cache`` axis), visiting conditions ``f-000`` (packed) and ``f-100``
(spread). The per-condition outcome is the **session-condition median over
untainted iterations** of the UDP drop, via the shared
:func:`m2_aa_analysis.load_condition_outcomes` taint machinery — rejected or
fully-tainted conditions contribute no value (registered "never quoted" rule).

**Pairing for (b).** Cache-off and cache-on spread values are paired
**positionally by collection order** (timestamp, then run id) within each cache
group — the campaign runs matched cache-off/cache-on pairs in randomized cache
order, so the i-th cache-off session pairs with the i-th cache-on session. A
recorded pair-id would be more robust; see the campaign driver.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
from typing import Any, Dict, List, Optional, Tuple

from chaosprobe.metrics.statistics import wilcoxon_signed_rank

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:  # `python scripts/c3_h2_dns.py` adds it; imports may not
    sys.path.insert(0, _SCRIPTS_DIR)

from m2_aa_analysis import (  # noqa: E402  (sys.path bootstrap above)
    _median_or_none,
    discover_sessions,
    load_condition_outcomes,
)

#: Registered V2-H2 outcome: absolute during-churn UDP-conntrack drop.
OUTCOME = "udp_conntrack_drop_entries"

#: The two placement extremes C3 visits (r=1): f=0 packed, f=1 spread.
PACKED = "f-000"
SPREAD = "f-100"

#: Registered V2-H2(b) bar — spread's cache-on UDP drop shrinks ≥ 50 % (D6).
SHRINKAGE_BAR = 0.5


class C3Session:
    """One C3 session reduced to its cache mode + per-placement UDP drop."""

    def __init__(
        self,
        run: str,
        timestamp: Optional[str],
        dns_cache: Optional[str],
        packed: Optional[float],
        spread: Optional[float],
    ) -> None:
        self.run = run
        self.timestamp = timestamp
        self.dns_cache = dns_cache
        self.packed = packed  # UDP drop at f-000
        self.spread = spread  # UDP drop at f-100


def _session_dns_cache(results_dir: str, run: str) -> Optional[str]:
    """Read ``v2Session.dnsCache`` for a session (discover_sessions omits it)."""
    path = os.path.join(results_dir, run, "summary.json")
    try:
        with open(path) as fh:
            return ((json.load(fh) or {}).get("v2Session") or {}).get("dnsCache")
    except (OSError, ValueError):
        return None


def _condition_udp_drop(results_dir: str, session: Any, condition: str) -> Optional[float]:
    """Session-condition median UDP drop for one placement, or None.

    Excludes the condition when it was not accepted (rejected placement) or has
    no untainted iteration — the registered "never quoted" rule, via the shared
    m2 taint machinery (``session.tainted`` / ``session.taints``).
    """
    obs = session.levels.get(condition)
    if obs is None or not obs.accepted:
        return None
    run_dir = os.path.join(results_dir, session.run)
    per_outcome = load_condition_outcomes(run_dir, condition, session.tainted, session.taints)
    if per_outcome is None:
        return None
    return _median_or_none(per_outcome.get(OUTCOME) or [])


def collect_sessions(results_dir: str) -> Tuple[List[C3Session], List[str]]:
    """Every C3 session's cache mode + packed/spread UDP drop."""
    sessions, warnings = discover_sessions(results_dir)
    out: List[C3Session] = []
    for s in sessions:
        dns = _session_dns_cache(results_dir, s.run)
        if dns not in ("on", "off"):
            warnings.append(f"{s.run}: not a C3 session (dnsCache={dns!r}) — skipped")
            continue
        out.append(
            C3Session(
                run=s.run,
                timestamp=s.timestamp,
                dns_cache=dns,
                packed=_condition_udp_drop(results_dir, s, PACKED),
                spread=_condition_udp_drop(results_dir, s, SPREAD),
            )
        )
    return out, warnings


def _ordered(sessions: List[C3Session], dns: str) -> List[C3Session]:
    """Sessions of one cache mode, in stable collection order (for pairing)."""
    grp = [s for s in sessions if s.dns_cache == dns]
    return sorted(grp, key=lambda s: (s.timestamp or "", s.run))


def _one_sided_greater(res: Dict[str, object]) -> Optional[float]:
    """One-sided p for H1: a > b, from a two-sided wilcoxon_signed_rank result.

    ``w_plus`` is the rank mass where a > b. In-direction (w_plus ≥ w_minus) →
    p = p_two/2; against → 1 − p_two/2. None when no non-zero pairs.
    """
    if res.get("n_nonzero", 0) == 0:
        return None
    p_two = float(res["p_two_sided"])  # type: ignore[arg-type]
    w_plus = float(res["w_plus"])  # type: ignore[arg-type]
    w_minus = float(res["w_minus"])  # type: ignore[arg-type]
    return p_two / 2.0 if w_plus >= w_minus else 1.0 - p_two / 2.0


def _block(label: str, a: List[float], b: List[float]) -> Dict[str, Any]:
    """Paired one-sided (a>b) Wilcoxon summary for a co-primary/secondary."""
    res = wilcoxon_signed_rank(a, b)
    return {
        "label": label,
        "n_pairs": len(a),
        "median_a": round(st.median(a), 4) if a else None,
        "median_b": round(st.median(b), 4) if b else None,
        "p_one_sided": _one_sided_greater(res),
        "p_two_sided": res["p_two_sided"],
        "directionGreater": bool(a and b and st.median(a) > st.median(b)),
    }


def analyze(results_dir: str) -> Dict[str, Any]:
    """The V2-H2 conjunction verdict + components."""
    sessions, warnings = collect_sessions(results_dir)
    off = _ordered(sessions, "off")
    on = _ordered(sessions, "on")

    # (a) placement-dependence, cache-off: spread > packed, paired per session.
    a_valid = [s for s in off if s.spread is not None and s.packed is not None]
    spread_a = [float(s.spread) for s in a_valid]  # type: ignore[arg-type]
    packed_a = [float(s.packed) for s in a_valid]  # type: ignore[arg-type]
    place = _block("placement-dependence (spread>packed, cache-off)", spread_a, packed_a)
    place["rescueMet"] = place["directionGreater"]

    # (b) mechanism: spread cache-on vs cache-off shrinkage ≥ 50%, paired by order.
    spread_off = [float(s.spread) for s in off if s.spread is not None]  # type: ignore[arg-type]
    spread_on = [float(s.spread) for s in on if s.spread is not None]  # type: ignore[arg-type]
    n_b = min(len(spread_off), len(spread_on))
    if len(spread_off) != len(spread_on):
        warnings.append(
            f"V2-H2(b): unequal valid spread counts (off={len(spread_off)}, on={len(spread_on)}) "
            f"— paired the first {n_b} by collection order"
        )
    shrink = [(o - n) / o for o, n in zip(spread_off[:n_b], spread_on[:n_b]) if o > 0]
    if len(shrink) < n_b:
        warnings.append(
            f"V2-H2(b): dropped {n_b - len(shrink)} pair(s) with non-positive cache-off drop "
            "(shrinkage denominator undefined)"
        )
    mech = _block(
        f"mechanism shrinkage ≥ {SHRINKAGE_BAR:.0%} (spread cache-on vs off)",
        shrink,
        [SHRINKAGE_BAR] * len(shrink),
    )
    mech["shrinkageMedian"] = round(st.median(shrink), 4) if shrink else None
    mech["barMet"] = bool(shrink and st.median(shrink) >= SHRINKAGE_BAR)

    # Secondary (not in family): packed arm shows ~no cache effect.
    packed_off = [float(s.packed) for s in off if s.packed is not None]  # type: ignore[arg-type]
    packed_on = [float(s.packed) for s in on if s.packed is not None]  # type: ignore[arg-type]
    n_s = min(len(packed_off), len(packed_on))
    secondary = _block(
        "secondary: packed cache effect (expected ~none)",
        packed_off[:n_s],
        packed_on[:n_s],
    )

    p_a, p_b = place["p_one_sided"], mech["p_one_sided"]
    family_input = max(p_a, p_b) if (p_a is not None and p_b is not None) else None
    # Registered direction+bar conjunction (p-values uncorrected, pending Holm).
    conjunction = bool(place["rescueMet"] and mech["barMet"])

    return {
        "nSessions": len(sessions),
        "nCacheOff": len(off),
        "nCacheOn": len(on),
        "placementDependence": place,
        "mechanismShrinkage": mech,
        "secondaryPackedCacheEffect": secondary,
        "familyInputMaxP": family_input,
        "conjunction": conjunction,
        "warnings": warnings,
    }


def _print(out: Dict[str, Any]) -> None:
    print("V2-H2 — placement-dependence + DNS intervention (paired Wilcoxon)")
    print(
        f"  sessions: {out['nSessions']}  (cache-off {out['nCacheOff']}, "
        f"cache-on {out['nCacheOn']})\n"
    )
    p = out["placementDependence"]
    print(f"  (a) {p['label']} — n={p['n_pairs']}")
    print(f"    spread median={p['median_a']}  packed median={p['median_b']}")
    print(f"    one-sided p: {p['p_one_sided']}  direction spread>packed: {p['directionGreater']}")
    m = out["mechanismShrinkage"]
    print(f"  (b) {m['label']} — n={m['n_pairs']}")
    print(
        f"    shrinkage median={m['shrinkageMedian']}  bar≥{SHRINKAGE_BAR:.0%} met: {m['barMet']}"
    )
    print(f"    one-sided p: {m['p_one_sided']}")
    s = out["secondaryPackedCacheEffect"]
    print(f"  secondary packed cache effect: off median={s['median_a']} on median={s['median_b']}")
    print(f"\n  family input max(p_a,p_b): {out['familyInputMaxP']} (uncorrected, pending Holm)")
    print(f"  CONJUNCTION (direction + ≥50% bar, both-must-pass): {out['conjunction']}")
    for w in out["warnings"]:
        print(f"  ! {w}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="V2-H2 (C3) confirmatory analysis.")
    ap.add_argument("--results-dir", required=True)
    ap.add_argument("--json", help="write the full result object to this path")
    args = ap.parse_args(argv)
    out = analyze(args.results_dir)
    _print(out)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=2)
        print(f"\nJSON written to {args.json}")
    return 0


if __name__ == "__main__":  # pragma: no cover  (CLI entrypoint)
    raise SystemExit(main())
