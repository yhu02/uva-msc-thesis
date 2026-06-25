#!/usr/bin/env python3
"""H3 — does the reproducible *mechanism* predict a user-visible *outcome*?

M1/M2 establish that placement moves a reconvergence mechanism (conntrack churn,
CoreDNS/TCP disruption) reproducibly, while M4 shows the aggregate resilience
*score* does not reproduce. H3 closes the gap between those two facts: it asks
whether the mechanism metric predicts the **tail latency on the fault-dependent
route** — i.e. whether the reproducible signal actually explains a user-facing
symptom, turning two free-floating observations into a causal chain
(placement -> reconvergence -> tail).

Design
------
One data point per (run, strategy) cell, churn (pod-delete) runs only; baseline
and cpu-hog excluded. For each cell we pair:

  mechanism  x  (during-chaos, per node):
      conntrack_flush_pct  = (pre_mean - during_mean) / pre_mean * 100
      coredns_p99_during   = during-chaos mean of coredns_request_duration_p99
      coredns_p99_delta    = during - pre
      tcp_retx_during      = during-chaos mean of tcp_retransmit_rate_per_node
      tcp_retx_delta       = during - pre

  outcome    y  (during-chaos route tail, ms):
      p95 and max of each route, split into
        DEPENDENT routes  — touch productcatalogservice (the chaos target)
        CONTROL   routes  — do not

Falsification built in
----------------------
A naive worry is a *run-level* confound: a generally slow run lifts the
mechanism AND every route's tail together, manufacturing a correlation. The
CONTROL routes are the antidote — they ride the same run-level slowness but do
*not* depend on the killed service. So H3 is supported only if a mechanism
predicts the DEPENDENT tail while predicting the CONTROL tail *much* less. A
confound would light up both equally.

Stats are rank-based (Spearman), matching the rest of the thesis. p-values use
the t-approximation (rough at small n — read them as a screen, not a proof).

Usage
-----
    uv run python scripts/h3_mechanism_outcome.py [--results-dir results] [--csv out.csv]
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
from typing import Optional

from fault_taxonomy import is_churn

from chaosprobe.metrics.statistics import tost_equivalence_correlation

# --- route classification -------------------------------------------------
# DEPENDENT: the request path touches productcatalogservice (the chaos target).
#   /product/<id>  — product page render
#   /              — homepage lists the product catalog
#   *->productcatalogservice  — east-west calls into the target
# CONTROL: independent of the target; share run-level effects but not the fault.
DEPENDENT_MATCH = ("/product", "productcatalogservice")
CONTROL_ROUTES = (
    "/_healthz",
    "/cart",
    "cartservice->redis-cart",
    "checkoutservice->paymentservice",
)
HOMEPAGE = "/"  # depends on catalog, but reported separately (mixed fan-out)


def _prom(strategy: dict, metric: str, phase: str) -> Optional[float]:
    phases = ((strategy.get("metrics") or {}).get("prometheus") or {}).get("phases") or {}
    entry = ((phases.get(phase) or {}).get("metrics") or {}).get(metric)
    return entry.get("mean") if isinstance(entry, dict) else None


def _route_tail(strategy: dict, classifier, stat: str) -> Optional[float]:
    """Worst (max) `stat` over during-chaos routes matching `classifier`."""
    routes = (((strategy.get("metrics") or {}).get("latency") or {}).get("phases") or {}).get(
        "during-chaos", {}
    ).get("routes") or {}
    vals = [
        r.get(stat)
        for name, r in routes.items()
        if classifier(name) and isinstance(r.get(stat), (int, float))
    ]
    return max(vals) if vals else None


def _dep(name: str) -> bool:
    return any(m in name for m in DEPENDENT_MATCH)


def _ctrl(name: str) -> bool:
    return name in CONTROL_ROUTES


def collect(results_dir: str) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(glob.glob(os.path.join(results_dir, "*", "summary.json"))):
        run = os.path.basename(os.path.dirname(path))
        with open(path) as fh:
            summary = json.load(fh)
        for fault_name, fault in (summary.get("faults") or {}).items():
            if not is_churn(fault_name):
                continue
            for sname, s in ((fault or {}).get("strategies") or {}).items():
                if sname == "baseline":
                    continue
                ct_pre = _prom(s, "conntrack_entries_per_node", "pre-chaos")
                ct_dur = _prom(s, "conntrack_entries_per_node", "during-chaos")
                dns_pre = _prom(s, "coredns_request_duration_p99", "pre-chaos")
                dns_dur = _prom(s, "coredns_request_duration_p99", "during-chaos")
                tcp_pre = _prom(s, "tcp_retransmit_rate_per_node", "pre-chaos")
                tcp_dur = _prom(s, "tcp_retransmit_rate_per_node", "during-chaos")
                rows.append(
                    {
                        "run": run,
                        "strategy": sname,
                        "conntrack_flush_pct": (
                            (ct_pre - ct_dur) / ct_pre * 100
                            if ct_pre and ct_dur is not None and ct_pre > 0
                            else None
                        ),
                        "coredns_p99_during": dns_dur,
                        "coredns_p99_delta": (
                            (dns_dur - dns_pre)
                            if dns_dur is not None and dns_pre is not None
                            else None
                        ),
                        "tcp_retx_during": tcp_dur,
                        "tcp_retx_delta": (
                            (tcp_dur - tcp_pre)
                            if tcp_dur is not None and tcp_pre is not None
                            else None
                        ),
                        "dep_p95": _route_tail(s, _dep, "p95_ms"),
                        "dep_max": _route_tail(s, _dep, "max_ms"),
                        "ctrl_p95": _route_tail(s, _ctrl, "p95_ms"),
                        "ctrl_max": _route_tail(s, _ctrl, "max_ms"),
                        "home_p95": _route_tail(s, lambda n: n == HOMEPAGE, "p95_ms"),
                    }
                )
    return rows


# --- stats (pure stdlib) --------------------------------------------------
def _rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1  # average rank for ties, 1-based
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else float("nan")


def spearman(xs: list[float], ys: list[float]) -> tuple[float, float, int]:
    """Spearman rho, two-sided p (t-approx), n — over rows where both present."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 4:
        return float("nan"), float("nan"), n
    rho = _pearson(_rank([p[0] for p in pairs]), _rank([p[1] for p in pairs]))
    if math.isnan(rho) or abs(rho) >= 1.0:
        return rho, 0.0 if abs(rho) >= 1.0 else float("nan"), n
    t = rho * math.sqrt((n - 2) / (1 - rho**2))
    # two-sided p via Student-t survival (Abramowitz-Stegun continued fraction)
    df = n - 2
    x = df / (df + t * t)
    p = _betainc(df / 2.0, 0.5, x)
    return rho, p, n


def _betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a,b) — Lentz continued fraction."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(math.log(x) * a + math.log(1 - x) * b - lbeta) / a
    f, c, d = 1.0, 1.0, 0.0
    for i in range(0, 200):
        m = i // 2
        if i == 0:
            num = 1.0
        elif i % 2 == 0:
            num = (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            num = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + num * d
        d = 1e-30 if abs(d) < 1e-30 else d
        d = 1.0 / d
        c = 1.0 + num / c
        c = 1e-30 if abs(c) < 1e-30 else c
        f *= d * c
        if abs(1.0 - d * c) < 1e-10:
            break
    return front * (f - 1.0)


MECHANISMS = [
    ("conntrack_flush_pct", "conntrack flush %"),
    ("coredns_p99_during", "CoreDNS p99 (during)"),
    ("coredns_p99_delta", "CoreDNS p99 delta"),
    ("tcp_retx_during", "TCP retransmit (during)"),
    ("tcp_retx_delta", "TCP retransmit delta"),
]


def _star(p: float) -> str:
    if math.isnan(p):
        return "  "
    return "***" if p < 0.001 else "** " if p < 0.01 else "*  " if p < 0.05 else "  "


def _verdict(rho_d: float, p_d: float, rho_c: float, p_c: float, n_d: int) -> str:
    """Classify one mechanism->outcome link, using TOST for the decoupling null.

    A non-significant dependent correlation is *absence of evidence*; the TOST
    equivalence test turns it into *evidence of absence* (the dependent
    correlation is statistically inside the +/-0.3 band), which is what the H3
    decoupling claim actually needs.
    """
    # H3 supported: significant on the dependent route, and clearly weaker on
    # the control route (rules out a run-level confound lifting both).
    supported = not math.isnan(rho_d) and p_d < 0.05 and abs(rho_d) - abs(rho_c) > 0.15
    eq = (
        tost_equivalence_correlation(rho_d, n_d) if not math.isnan(rho_d) else {"equivalent": False}
    )
    if supported:
        return "H3 supported"
    if eq["equivalent"]:
        return "decoupled (TOST)"
    if not math.isnan(p_c) and p_c < 0.05 and abs(rho_c) > 0.15:
        return "confound? (both)"
    return "no link"


def report(rows: list[dict]) -> None:
    print(f"\nH3: mechanism -> fault-route tail  (n={len(rows)} strategy-cells, churn only)\n")
    for outcome, label in (("dep_p95", "p95"), ("dep_max", "max")):
        print(f"=== outcome = DEPENDENT-route {label} vs CONTROL-route {label} ===")
        print(f"  {'mechanism':<24} {'rho(dep)':>9} {'p':>8}   {'rho(ctrl)':>9} {'p':>8}   verdict")
        for key, name in MECHANISMS:
            xs = [r[key] for r in rows]
            rho_d, p_d, n_d = spearman(xs, [r[outcome] for r in rows])
            rho_c, p_c, _ = spearman(xs, [r["ctrl_" + label] for r in rows])
            verdict = _verdict(rho_d, p_d, rho_c, p_c, n_d)
            print(
                f"  {name:<24} {rho_d:>9.2f}{_star(p_d)} {p_d:>7.3f}   "
                f"{rho_c:>9.2f}{_star(p_c)} {p_c:>7.3f}   {verdict}"
            )
        print()


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--csv", help="optional: write the paired per-cell data here")
    args = ap.parse_args()
    rows = collect(args.results_dir)
    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows)} rows -> {args.csv}")
    report(rows)


if __name__ == "__main__":
    main()
