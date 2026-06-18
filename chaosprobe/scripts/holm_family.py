#!/usr/bin/env python3
"""Confirmatory-family capstone: Holm correction over V2-H1/H2/H3/H5.

The pre-registration (``01-PREREGISTRATION.md`` §"Confirmatory family and
multiplicity") fixes a **four-member** confirmatory family — the single primary
test of each of V2-H1, V2-H2, V2-H3, V2-H5 — **Holm-corrected** across the
family at α = 0.05. This script is the capstone: it reads each hypothesis's
registered *family-input p-value* from that hypothesis's own analysis-driver
JSON (so nothing is transcribed by hand), applies Holm, and prints the family
table.

**Each family-input p comes from the registered primary test, verbatim:**

- **V2-H1** (dose-response, Page's L): ``pageTrendTest.p_one_sided`` from
  ``c1_h1_trend.py``.
- **V2-H2** (placement + DNS conjunction): ``familyInputMaxP`` = max(p_a, p_b)
  from ``c3_h2_dns.py`` (the registered conjunction input).
- **V2-H3** (replication rescue conjunction): max of the two co-primary ART
  interaction p-values (``troughDepthFraction.artInteraction.p`` and
  ``userErrorRate.artInteraction.p``) from ``c2_h3_anova.py`` — matching the
  both-must-pass rule (the family input is the larger).
- **V2-H5** (scorecard ICC conjunction): ``decision.holmInput`` =
  max(p_availability, p_mechanism) from ``scorecard.py``.

**Holm significance is necessary but not sufficient for support.** Each
hypothesis also carries a registered *bar* the Holm p cannot speak to — H1's
SESOI (effect ≥ 15 %), H2/H5's both-must-pass conjunction, H3's anti-affine
rescue margin. A hypothesis is **supported** only if its primary is
Holm-significant **and** its registered bar is met; this script reports both so
the distinction is explicit (a statistically significant but sub-SESOI trend,
or a significant interaction that misses the rescue margin, is *not* support).

V2-H4 is descriptive and V2-H6 exploratory — neither is in the family.
"""

import argparse
import json
from typing import Any, Dict, List, Tuple

#: Registered family size and α (pre-registration §multiplicity).
ALPHA = 0.05


def holm(pvalues: List[float], alpha: float = ALPHA) -> Tuple[List[float], List[bool]]:
    """Holm step-down correction.

    Returns ``(adjusted, reject)`` aligned to the *input* order. The adjusted
    p-value for the rank-``i`` (0-based, ascending) hypothesis is
    ``min(1, max_{j<=i} (m - j) * p_(j))`` — the running max enforces
    monotonicity — and a hypothesis is rejected iff its adjusted p ≤ α (the
    standard equivalent of the step-down "reject until the first failure" rule).
    """
    m = len(pvalues)
    if m == 0:
        return [], []
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * pvalues[idx])
        adjusted[idx] = min(1.0, running)
    reject = [a <= alpha for a in adjusted]
    return adjusted, reject


def _get(obj: Any, *path: str) -> Any:
    """Walk a nested dict by keys; raise KeyError with the full path on a miss."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            raise KeyError(f"missing key {'.'.join(path)!r} (at {key!r})")
        cur = cur[key]
    return cur


def _float_p(doc: Any, hyp: str, *path: str) -> float:
    """A family-input p as float, failing loudly on a present-but-null value.

    Every upstream driver legitimately emits ``null`` for its p-key on sparse /
    degenerate data (no defined trend, an unestimable interaction, a co-primary
    with no paired differences, a missing required sub-score). A ``null`` cannot
    enter the Holm family, so raise a clear, hypothesis-named error here rather
    than letting ``float(None)`` surface an opaque ``TypeError`` — mirroring the
    path-annotated ``KeyError`` ``_get`` raises on a missing key.
    """
    val = _get(doc, *path)
    if val is None:
        raise ValueError(
            f"{hyp} family-input p is null ({'.'.join(path)}) — primary test "
            "not evaluable on this data; cannot enter the Holm family"
        )
    return float(val)


def _load(path: str) -> Any:
    with open(path) as fh:
        return json.load(fh)


def h1_input(doc: Any) -> Tuple[float, bool, str]:
    """V2-H1: Page's L one-sided p; bar = effect meets SESOI (≥ 15 %)."""
    p = _float_p(doc, "V2-H1", "pageTrendTest", "p_one_sided")
    meets_sesoi = bool(_get(doc, "sesoi", "meetsSesoi"))
    pct = _get(doc, "sesoi", "pctChange")
    sesoi_pct = _get(doc, "sesoi", "sesoiPct")
    verdict = "meets" if meets_sesoi else "sub-SESOI"
    note = f"dose-response trend; effect {pct}% vs SESOI {sesoi_pct}% → {verdict}"
    return p, meets_sesoi, note


def h2_input(doc: Any) -> Tuple[float, bool, str]:
    """V2-H2: max(p_a, p_b); bar = the two-part conjunction passes."""
    p = _float_p(doc, "V2-H2", "familyInputMaxP")
    conj = bool(_get(doc, "conjunction"))
    note = "placement-dependence ∧ DNS-shrinkage conjunction"
    return p, conj, note


def h3_input(doc: Any) -> Tuple[float, bool, str]:
    """V2-H3: max of the two co-primary ART interaction p's; bar = rescue conjunction."""
    p_depth = _float_p(doc, "V2-H3", "troughDepthFraction", "artInteraction", "p")
    p_err = _float_p(doc, "V2-H3", "userErrorRate", "artInteraction", "p")
    p = max(p_depth, p_err)
    conj = bool(_get(doc, "conjunctionRescue"))
    note = f"co-primary interactions max(depth {p_depth}, err {p_err}); anti-affine rescue margin"
    return p, conj, note


def h5_input(doc: Any) -> Tuple[float, bool, str]:
    """V2-H5: max(p_availability, p_mechanism); bar = required-subscore conjunction."""
    p = _float_p(doc, "V2-H5", "decision", "holmInput")
    conj = bool(_get(doc, "decision", "conjunctionPass"))
    note = "availability ∧ mechanism ICC ≥ 0.5 conjunction"
    return p, conj, note


#: Family members in registered order, each with its driver-JSON extractor.
MEMBERS = [
    ("V2-H1", "dose-response (Page's L)", h1_input),
    ("V2-H2", "placement + DNS intervention", h2_input),
    ("V2-H3", "replication rescue (node-drain)", h3_input),
    ("V2-H5", "layered scorecard ICC", h5_input),
]


def analyze(paths: Dict[str, str], alpha: float = ALPHA) -> Dict[str, Any]:
    """Read the four driver JSONs, apply Holm, return the family verdict dict."""
    rows: List[Dict[str, Any]] = []
    for hyp, label, extractor in MEMBERS:
        p, bar_met, note = extractor(_load(paths[hyp]))
        rows.append({"hyp": hyp, "label": label, "pInput": p, "barMet": bar_met, "note": note})

    adjusted, reject = holm([r["pInput"] for r in rows], alpha)
    for r, adj, rej in zip(rows, adjusted, reject):
        r["holmAdjusted"] = round(adj, 6)
        r["holmSignificant"] = rej
        r["supported"] = bool(rej and r["barMet"])

    return {
        "alpha": alpha,
        "familySize": len(rows),
        "members": rows,
        "anySupported": any(r["supported"] for r in rows),
    }


def _fmt_p(p: float) -> str:
    return f"{p:.4g}"


def render(result: Dict[str, Any]) -> str:
    lines = [
        "V2 confirmatory family — Holm correction "
        f"(m={result['familySize']}, α={result['alpha']})",
        "",
        f"  {'hyp':6} {'p_input':>9} {'holm_adj':>9} {'sig?':>5} "
        f"{'bar?':>5}  {'supported':>9}  primary test",
    ]
    for r in result["members"]:
        lines.append(
            f"  {r['hyp']:6} {_fmt_p(r['pInput']):>9} {_fmt_p(r['holmAdjusted']):>9} "
            f"{('Y' if r['holmSignificant'] else 'N'):>5} "
            f"{('Y' if r['barMet'] else 'N'):>5}  "
            f"{('SUPPORTED' if r['supported'] else 'no'):>9}  {r['label']}"
        )
    lines.append("")
    for r in result["members"]:
        lines.append(f"  {r['hyp']}: {r['note']}")
    lines.append("")
    verdict = (
        "at least one hypothesis is SUPPORTED"
        if result["anySupported"]
        else "NO confirmatory hypothesis is supported "
        "(each fails Holm significance and/or its registered bar)"
    )
    lines.append(f"  Family verdict: {verdict}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Holm correction over the V2 confirmatory family")
    ap.add_argument("--h1", required=True, help="c1_h1_trend.py --json output")
    ap.add_argument("--h2", required=True, help="c3_h2_dns.py --json output")
    ap.add_argument("--h3", required=True, help="c2_h3_anova.py --json output")
    ap.add_argument("--h5", required=True, help="scorecard.py --json output")
    ap.add_argument("--alpha", type=float, default=ALPHA)
    ap.add_argument("--json", help="optional: write the full family dict here")
    args = ap.parse_args()

    result = analyze(
        {"V2-H1": args.h1, "V2-H2": args.h2, "V2-H3": args.h3, "V2-H5": args.h5}, args.alpha
    )
    print(render(result))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=1)
        print(f"\nJSON written to {args.json}")


if __name__ == "__main__":
    main()
