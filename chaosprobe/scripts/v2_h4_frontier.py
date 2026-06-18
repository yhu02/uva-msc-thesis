#!/usr/bin/env python3
"""V2-H4 — the descriptive placement Pareto frontier (registered figure + protocol).

V2-H4 is **descriptive, not a confirmatory hypothesis** (``01-PREREGISTRATION.md``
§V2-H4; the headline objective of ``00-DESIGN.md`` §6). For each designed
placement ``(f, r, mode)`` it plots the **latency face** (pre-chaos east-west
p95 tail — steady-state, placement-determined, hence comparable across
campaigns/faults) against the **availability face** (during-chaos blast /
recovery: trough depth in pods + user-route error rate), with cluster-bootstrap
CIs, and reports the **non-dominated set under margins**.

**Dominance is declared only with margins** (registered, frozen at M2): A
dominates B iff A is better than B by ≥ the band on the latency face **and** by
≥ the band on *both* availability DVs — the conservative all-DV reading. Bands
(``M2-AA-REPORT.md``): δ_latency = **4.4 ms** (pre-chaos EW-p95 A/A p95 band),
δ_depth = **1.0 pod**, δ_error = **0.302** (availability-face A/A bands). All
three DVs are "lower is better". A single placement dominating all others by ≥ δ
on every face would be the headline; the margins exist to prevent noise from
manufacturing a frontier.

**Frontier construction (scope decision, after a data-collection finding).** The
frontier set is the **C1 dose-response cells** (f ∈ {0,.25,.5,.75,1}, r = 1),
each with the full latency + availability faces (pod-delete). C3 endpoints
(f = 0/1, r = 1, cache-on) are overlaid as **corroboration**, outside the
dominance computation. **C2 (node-drain replication) is excluded from the
two-face frontier:** it was run with host-side Locust on the ``/`` route only —
no east-west prober — so it has **no pre-chaos east-west latency face**, and its
depth is recorded as a top-level fraction (a different shape and unit from the
per-iteration ``es_trough_depth_pods`` the frontier uses). C2's replication
results live on the availability face and are reported in ``C2-OB-REPORT.md``
(V2-H3); the missing east-west prober is a stated V2-H4 limitation, not a hidden
omission. Every point is labeled by fault class (all pod-delete here).

Session-condition values are the median over untainted iterations (shared m2
taint machinery, "never quoted" exclusion); per placement, the point estimate
and cluster-bootstrap CI resample over **sessions** (the cluster).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from chaosprobe.metrics.statistics import bootstrap_ci

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:  # `python scripts/v2_h4_frontier.py` adds it; imports may not
    sys.path.insert(0, _SCRIPTS_DIR)

from m2_aa_analysis import (  # noqa: E402  (sys.path bootstrap above)
    _median_or_none,
    discover_sessions,
)


@dataclass(frozen=True)
class DV:
    """A decision variable (frontier axis): JSON key, label, δ margin, face."""

    key: str
    label: str
    delta: float
    face: str  # "latency" | "availability"


#: The three registered DVs, all "lower is better". δ frozen at M2.
DVS: Tuple[DV, ...] = (
    DV("ew_p95_pre_ms", "EW p95 pre-chaos [ms]", 4.4, "latency"),
    DV("es_trough_depth_pods", "trough depth [pods]", 1.0, "availability"),
    DV("user_err_during", "user-route error rate", 0.302, "availability"),
)


@dataclass
class Placement:
    """One designed placement (f, r, mode) and its per-DV cluster-bootstrap stats."""

    label: str
    f: float
    r: int
    mode: str
    fault: str
    campaign: str
    role: str  # "frontier" | "corroboration"
    #: DV-key -> list of session-condition medians (one per session)
    session_values: Dict[str, List[float]] = field(default_factory=dict)
    #: DV-key -> {"point","ci_low","ci_high","n"} (filled by summarize())
    stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def summarize(self, seed: int = 42) -> None:
        for dv in DVS:
            vals = self.session_values.get(dv.key, [])
            ci = bootstrap_ci(vals, statistic="median", seed=seed)
            self.stats[dv.key] = {
                "point": ci["point"],
                "ci_low": ci["ci_low"],
                "ci_high": ci["ci_high"],
                "n": ci["n"],
            }


def dominates(a: Placement, b: Placement) -> bool:
    """True iff A dominates B by margin on EVERY DV (all lower-is-better).

    Requires A's point estimate to beat B's by at least the DV's δ band on the
    latency face AND on both availability DVs (the conservative all-DV reading
    of the registered margin rule). Missing point estimates ⇒ no dominance.
    """
    for dv in DVS:
        pa = a.stats.get(dv.key, {}).get("point")
        pb = b.stats.get(dv.key, {}).get("point")
        if pa is None or pb is None:
            return False
        if pb - pa < dv.delta:  # A not better than B by the full margin on this DV
            return False
    # Reached only by beating B by ≥ δ on every (non-empty) DV.
    return True


def non_dominated(placements: List[Placement]) -> List[Placement]:
    """The frontier: placements not margin-dominated by any other in the set."""
    return [p for p in placements if not any(dominates(q, p) for q in placements if q is not p)]


# ──────────────────────────────────────────────────────────────────────
# Data collection
# ──────────────────────────────────────────────────────────────────────


def _placement_label(f: float, r: int, mode: str) -> str:
    base = f"f={f:g}, r={r}"
    return base if r == 1 and mode in ("packed", "solver", "") else f"{base}, {mode}"


def _session_dns_cache(results_dir: str, run: str) -> Optional[str]:
    """``v2Session.dnsCache`` for one session (the m2 ``Session`` model omits it).

    Only read when a ``dns_cache`` filter is in effect (C3) — the other session
    metadata the frontier needs (fault, r, mode, per-level f, per-iteration DV
    rows) is already on the ``Session``/``LevelObs`` from ``discover_sessions``.
    """
    path = os.path.join(results_dir, run, "summary.json")
    try:
        with open(path) as fh:
            return ((json.load(fh) or {}).get("v2Session") or {}).get("dnsCache")
    except (OSError, ValueError):
        return None


def collect_campaign(
    results_dir: str, campaign: str, role: str, dns_cache: Optional[str] = None
) -> Tuple[Dict[Tuple[Any, ...], Placement], List[str]]:
    """Group one campaign's accepted, untainted session-condition values by placement.

    Reuses what ``discover_sessions`` already loaded — ``s.key`` (fault, r, mode),
    ``obs.target_f``, and ``obs.iteration_values`` (the per-iteration DV rows, with
    taint-excluded iterations already folded to ``None``) — so the 20–100 MB raw
    condition files are parsed **once**, not re-read here. ``dns_cache`` (when set)
    keeps only sessions whose ``v2Session.dnsCache`` matches — used to pin C3 to its
    cache-on placement (cache is the V2-H2 intervention, not a placement dimension);
    it is the one field the m2 ``Session`` model omits, so it is read on demand.
    """
    sessions, warnings = discover_sessions(results_dir)
    placements: Dict[Tuple[Any, ...], Placement] = {}
    for s in sessions:
        if dns_cache is not None and _session_dns_cache(results_dir, s.run) != dns_cache:
            continue
        fault = s.key.fault or "unknown"
        r = s.key.replicas
        mode = s.key.mode or ""  # placement mode (packed/anti-affine), not the assignment method
        for condition, obs in s.levels.items():
            if not obs.accepted:
                continue
            f = obs.target_f
            key = (round(f, 4), r, mode)
            p = placements.get(key)
            if p is None:
                p = Placement(
                    label=_placement_label(f, r, mode),
                    f=f,
                    r=r,
                    mode=mode,
                    fault=fault,
                    campaign=campaign,
                    role=role,
                )
                placements[key] = p
            for dv in DVS:
                v = _median_or_none(obs.iteration_values.get(dv.key) or [])
                if v is not None:
                    p.session_values.setdefault(dv.key, []).append(v)
    return placements, warnings


#: (campaign, results-subdir, role, dns_cache filter) — the frontier scope decision.
#: C2 is intentionally absent: node-drain has no east-west latency face (see module
#: docstring), so it cannot sit on the two-face frontier; its availability results
#: are reported in C2-OB-REPORT.md (V2-H3).
CAMPAIGNS: Tuple[Tuple[str, str, str, Optional[str]], ...] = (
    ("C1", "c1-online-boutique", "frontier", None),
    ("C3", "c3-dns", "corroboration", "on"),
)


def build_frontier(results_root: str, seed: int = 42) -> Dict[str, Any]:
    """Collect all campaigns, summarize, compute the non-dominated frontier set."""
    all_placements: List[Placement] = []
    warnings: List[str] = []
    for campaign, subdir, role, dns in CAMPAIGNS:
        rdir = os.path.join(results_root, subdir)
        if not os.path.isdir(rdir):
            warnings.append(f"{campaign}: {rdir} missing — skipped")
            continue
        placements, w = collect_campaign(rdir, campaign, role, dns)
        warnings.extend(w)
        all_placements.extend(placements.values())
    for p in all_placements:
        p.summarize(seed=seed)

    frontier_set = [p for p in all_placements if p.role == "frontier"]
    nd = non_dominated(frontier_set)
    nd_labels = {(p.campaign, p.label) for p in nd}
    return {
        "deltas": {dv.key: dv.delta for dv in DVS},
        "placements": [
            _placement_dict(p, (p.campaign, p.label) in nd_labels) for p in all_placements
        ],
        "nonDominated": [f"{p.campaign}:{p.label}" for p in nd],
        "frontierSize": len(frontier_set),
        "nonDominatedCount": len(nd),
        "warnings": warnings,
    }


def _placement_dict(p: Placement, is_non_dominated: bool) -> Dict[str, Any]:
    return {
        "campaign": p.campaign,
        "label": p.label,
        "f": p.f,
        "r": p.r,
        "mode": p.mode,
        "fault": p.fault,
        "role": p.role,
        "nonDominated": is_non_dominated if p.role == "frontier" else None,
        "stats": p.stats,
    }


# ──────────────────────────────────────────────────────────────────────
# Rendering
# ──────────────────────────────────────────────────────────────────────


def _fmt(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.4g}"


def render(result: Dict[str, Any]) -> str:
    lines = [
        "V2-H4 placement frontier (descriptive) — non-dominated set under margins",
        f"  δ: latency {DVS[0].delta} ms, depth {DVS[1].delta} pod, error {DVS[2].delta}",
        "",
        f"  {'placement':28} {'fault':10} {'EWp95':>9} {'depth':>8} {'err':>8} {'ND?':>4} role",
    ]
    for p in result["placements"]:
        st = p["stats"]
        nd = "—" if p["nonDominated"] is None else ("Y" if p["nonDominated"] else "n")
        lines.append(
            f"  {p['campaign'] + ':' + p['label']:28.28} {p['fault']:10.10} "
            f"{_fmt(st['ew_p95_pre_ms']['point']):>9} "
            f"{_fmt(st['es_trough_depth_pods']['point']):>8} "
            f"{_fmt(st['user_err_during']['point']):>8} {nd:>4} {p['role']}"
        )
    lines.append("")
    lines.append(
        f"  Non-dominated frontier ({result['nonDominatedCount']}/{result['frontierSize']}): "
        + ", ".join(result["nonDominated"])
    )
    if result["warnings"]:
        lines.append(f"  ({len(result['warnings'])} warning(s))")
    return "\n".join(lines)


def plot(result: Dict[str, Any], out_path: str) -> None:
    """Two-face scatter: latency (x) vs trough depth (y), error rate as colour."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 6))
    markers = {"node-drain": "s", "pod-delete": "o"}
    sc = None
    max_depth = 0.0
    for i, p in enumerate(
        sorted(result["placements"], key=lambda q: q["stats"]["ew_p95_pre_ms"]["point"] or 0)
    ):
        st = p["stats"]
        x, y = st["ew_p95_pre_ms"]["point"], st["es_trough_depth_pods"]["point"]
        if x is None or y is None:
            continue
        max_depth = max(max_depth, st["es_trough_depth_pods"]["ci_high"] or y)
        err = st["user_err_during"]["point"]
        corro = p["role"] == "corroboration"
        nd = p["nonDominated"] is True
        sc = ax.scatter(
            x,
            y,
            c=[err if err is not None else 0.0],
            cmap="viridis",
            vmin=0,
            vmax=1,
            marker=markers.get(p["fault"], "o"),
            s=220 if nd else 90,
            edgecolors="red" if nd else ("gray" if corro else "black"),
            linewidths=2.0 if nd else 1.0,
            alpha=0.55 if corro else 0.95,
            zorder=3 if nd else 2,
        )
        ax.errorbar(
            x,
            y,
            xerr=[
                [x - (st["ew_p95_pre_ms"]["ci_low"] or x)],
                [(st["ew_p95_pre_ms"]["ci_high"] or x) - x],
            ],
            yerr=[
                [y - (st["es_trough_depth_pods"]["ci_low"] or y)],
                [(st["es_trough_depth_pods"]["ci_high"] or y) - y],
            ],
            fmt="none",
            ecolor="gray",
            alpha=0.4,
            zorder=1,
        )
        # Stagger labels vertically (alternating) so the clustered points stay legible.
        dy = 12 if i % 2 == 0 else -16
        ax.annotate(
            f"{p['campaign']}:{p['label']}",
            (x, y),
            fontsize=7,
            xytext=(0, dy),
            textcoords="offset points",
            ha="center",
            color="gray" if corro else "black",
        )
    # Honest y-axis: depth is ~constant at 1 pod under pod-delete; show from 0 so the
    # near-zero variation reads as flat, not as a full-height spread of an autoscaled axis.
    ax.set_ylim(0, max(2.0, max_depth * 1.3))
    ax.set_xlabel(f"{DVS[0].label}  (latency face — lower better)")
    ax.set_ylabel(f"{DVS[1].label}  (availability face — lower better)")
    ax.set_title("V2-H4 placement frontier — red ring = non-dominated · colour = user error rate")
    if sc is not None:
        fig.colorbar(sc, ax=ax, label=DVS[2].label)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description="V2-H4 descriptive placement frontier")
    ap.add_argument("--results-root", default="results", help="dir holding c1-/c2-/c3- subdirs")
    ap.add_argument("--json", help="optional: write the full frontier dict here")
    ap.add_argument("--fig", help="optional: write the two-face scatter PNG here")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    result = build_frontier(args.results_root, seed=args.seed)
    print(render(result))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=1)
        print(f"\nJSON written to {args.json}")
    if args.fig:
        plot(result, args.fig)
        print(f"figure written to {args.fig}")


if __name__ == "__main__":
    main()
