#!/usr/bin/env python3
"""Compare during-load route tail latency across placement strategies (H4).

The load-contention experiment (``load-contention.yaml`` + ``--load-profile
spike``) makes *load* the stressor; the metric of interest is during-load route
**tail latency (p95)**, NOT the resilience score (H1 shows the score is too noisy
to rank, and under sustained load it is uniformly degraded). This script reads the
canonical per-route aggregate the runner already computes —
``aggregated.routeViewAggregate[].latencyProber.during-chaos.meanP95_ms`` (mean of
each iteration's during-chaos p95) — and compares placements pairwise, so every
figure quoted in the thesis comes from the committed ``summary.json`` rather than
ad-hoc extraction (which is how earlier hand-computed numbers drifted).

Routes split into:

  north-south (user-facing, ``/...``):
      DEPENDENT — touch productcatalogservice: ``/`` (homepage lists the catalog)
                  and ``/product/<id>`` (product page).
      CONTROL   — ``/_healthz`` (frontend self-check, no backend fan-out).
  east-west (inter-service, ``a->b``): the network-locality mechanism.

H4 propagation question: under load, does the inter-service locality effect reach
the user layer? A colocate<spread gap on the DEPENDENT user routes that clearly
exceeds the CONTROL route is the signature of locality propagating; an effect that
is no larger on dependent routes than on the control is a run-level confound.

Usage
-----
    uv run python scripts/contention_routes.py -s results/<run>/summary.json
        [--vs colocate,spread] [--csv out.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics as st
from typing import Optional

CONTROL_ROUTE = "/_healthz"
DEPENDENT_NS_PREFIXES = ("/product",)  # plus "/" (homepage), handled explicitly


def _strategies(summary: dict) -> dict[str, list]:
    """Return {strategy: routeViewAggregate list}, flattening single- and
    multi-fault summary shapes.

    A single-fault run keys strategies at ``summary["strategies"]``; a multi-fault
    run nests them under ``summary["faults"][<fault>]["strategies"]``.
    """
    out: dict[str, list] = {}

    def _absorb(strats: dict) -> None:
        for name, s in (strats or {}).items():
            rva = ((s or {}).get("aggregated") or {}).get("routeViewAggregate")
            if rva:
                out[name] = rva

    if summary.get("strategies"):
        _absorb(summary["strategies"])
    for fault in (summary.get("faults") or {}).values():
        _absorb((fault or {}).get("strategies") or {})
    return out


def _during_p95(rva: list) -> dict[str, dict[str, Optional[float]]]:
    """route -> {"prober": during-chaos meanP95, "locust": during-chaos meanP95}."""
    out: dict[str, dict[str, Optional[float]]] = {}
    for entry in rva or []:
        route = entry.get("route")
        if not route:
            continue
        prober = (((entry.get("latencyProber") or {}).get("during-chaos")) or {}).get("meanP95_ms")
        locust = (entry.get("locust") or {}).get("meanP95_ms")
        out[route] = {"prober": prober, "locust": locust}
    return out


def _is_ns(route: str) -> bool:
    return route.startswith("/")


def _is_ew(route: str) -> bool:
    return "->" in route


def _is_dependent_ns(route: str) -> bool:
    return route == "/" or route.startswith(DEPENDENT_NS_PREFIXES)


def _ratio(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """b / a — pair is (fast_candidate, slow_candidate); >1 means a (e.g. colocate) wins."""
    if a and b and a > 0:
        return b / a
    return None


def report(summary: dict, vs: tuple[str, str], csv_path: Optional[str]) -> None:
    strats = _strategies(summary)
    a, b = vs
    if a not in strats or b not in strats:
        print(f"  strategies {vs} not both present (have: {sorted(strats)})")
        return
    pa, pb = _during_p95(strats[a]), _during_p95(strats[b])
    routes = sorted(set(pa) | set(pb))

    rows = []
    print(f"\nDuring-load route tail (p95, ms) — {a} vs {b}  [ratio = {b}/{a}, >1 ⇒ {a} faster]\n")

    def _emit(title: str, predicate) -> list[float]:
        print(f"  === {title} ===")
        print(f"  {'route':<40}{a[:9]:>10}{b[:9]:>10}{'ratio':>8}")
        ratios = []
        for r in routes:
            if not predicate(r):
                continue
            va = pa.get(r, {}).get("prober")
            vb = pb.get(r, {}).get("prober")
            rt = _ratio(va, vb)
            if rt is not None:
                ratios.append(rt)
            tag = "  (control)" if r == CONTROL_ROUTE else ""
            print(
                f"  {r[:40]:<40}"
                f"{('%.1f' % va) if va is not None else '-':>10}"
                f"{('%.1f' % vb) if vb is not None else '-':>10}"
                f"{('%.2f' % rt) if rt is not None else '-':>8}{tag}"
            )
            rows.append(
                {"route": r, "group": title, f"{a}_p95_ms": va, f"{b}_p95_ms": vb, "ratio": rt}
            )
        return ratios

    _emit("USER-FACING (north-south)", _is_ns)
    _emit("INTER-SERVICE (east-west)", _is_ew)

    # H4 propagation summary: dependent user routes vs the control route.
    dep = [
        _ratio(pa.get(r, {}).get("prober"), pb.get(r, {}).get("prober"))
        for r in routes
        if _is_ns(r) and _is_dependent_ns(r)
    ]
    dep = [x for x in dep if x is not None]
    ctrl = _ratio(pa.get(CONTROL_ROUTE, {}).get("prober"), pb.get(CONTROL_ROUTE, {}).get("prober"))
    ew = [
        _ratio(pa.get(r, {}).get("prober"), pb.get(r, {}).get("prober"))
        for r in routes
        if _is_ew(r)
    ]
    ew = [x for x in ew if x is not None]
    print("\n  === H4 propagation (does locality reach the user?) ===")
    dep_med = st.median(dep) if dep else None
    ew_med = st.median(ew) if ew else None
    if dep_med is not None:
        print(f"  dependent user routes (/, /product): median = {dep_med:.2f}x (n={len(dep)})")
    print(f"  control route ({CONTROL_ROUTE}): ratio = {('%.2f' % ctrl) if ctrl else '-'}x")
    if ew_med is not None:
        print(f"  inter-service (east-west): median = {ew_med:.2f}x (n={len(ew)})")
    if dep_med is not None and ctrl:
        verdict = (
            "dependent > control => locality propagates to the user layer"
            if dep_med > ctrl
            else "dependent ~/< control => effect not specific to the dependency path"
        )
        print(f"  -> {verdict}")

    if csv_path:
        with open(csv_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n  wrote {len(rows)} rows -> {csv_path}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("-s", "--summary", required=True, help="path to a run's summary.json")
    ap.add_argument(
        "--vs",
        default="colocate,spread",
        help="strategy pair 'fast,slow' to compare (default colocate,spread)",
    )
    ap.add_argument("--csv", help="optional: write the per-route table here")
    args = ap.parse_args()
    with open(args.summary) as fh:
        summary = json.load(fh)
    a, _, b = args.vs.partition(",")
    report(summary, (a, b), args.csv)


if __name__ == "__main__":  # pragma: no cover
    main()
