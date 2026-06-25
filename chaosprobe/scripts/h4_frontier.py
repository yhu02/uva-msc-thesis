#!/usr/bin/env python3
"""H4 — the descriptive placement Pareto frontier (registered figure + protocol).

H4 is **descriptive, not a confirmatory hypothesis** (``01-PREREGISTRATION.md``
§H4; the headline objective of ``00-DESIGN.md`` §6). For each designed
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
(H3); the missing east-west prober is a stated H4 limitation, not a hidden
omission. Each point carries its fault class as a field/marker (all pod-delete
here); the fault is appended to a point's *display identifier* only when needed
to disambiguate a shared ``campaign:label`` (see :func:`_display_ids`).

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
if _SCRIPTS_DIR not in sys.path:  # `python scripts/h4_frontier.py` adds it; imports may not
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
            # The dominance check compares point estimates directly against δ
            # bands as fine as 0.302 (δ_error), so the point MUST stay unrounded —
            # bootstrap_ci rounds its "point" to 2 dp, which could flip a margin
            # decision. Use the exact median for `point`; bootstrap_ci supplies
            # only the CI bounds (display/plot).
            self.stats[dv.key] = {
                "point": _median_or_none(vals),
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


def _is_complete(p: Placement) -> bool:
    """All three DV point estimates present — required to enter the dominance set.

    A placement missing a DV (every iteration tainted, or a missing raw) can
    never be *dominated* (``dominates`` bails on a ``None`` point), so it would
    spuriously appear non-dominated; such placements are excluded from the
    dominance computation and reported as unranked instead.
    """
    return all(p.stats.get(dv.key, {}).get("point") is not None for dv in DVS)


# ──────────────────────────────────────────────────────────────────────
# Data collection
# ──────────────────────────────────────────────────────────────────────


def _placement_label(f: float, r: int, mode: str) -> str:
    # session.mode is packed|anti-affine (NOT the packedAssignment solver/round-robin).
    # Omit the mode suffix only for the canonical r=1 packed placement; show it otherwise.
    base = f"f={f:g}, r={r}"
    return base if r == 1 and mode in ("packed", "") else f"{base}, {mode}"


def _session_dns_cache(results_dir: str, run: str) -> Optional[str]:
    """``session.dnsCache`` for one session (the m2 ``Session`` model omits it).

    Only read when a ``dns_cache`` filter is in effect (C3) — the other session
    metadata the frontier needs (fault, r, mode, per-level f, per-iteration DV
    rows) is already on the ``Session``/``LevelObs`` from ``discover_sessions``.
    """
    path = os.path.join(results_dir, run, "summary.json")
    try:
        with open(path) as fh:
            summary = json.load(fh) or {}
            block = summary.get("session") or summary.get("v2Session")  # or legacy key
            return (block or {}).get("dnsCache")
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
    keeps only sessions whose ``session.dnsCache`` matches — used to pin C3 to its
    cache-on placement (cache is the H2 intervention, not a placement dimension);
    it is the one field the m2 ``Session`` model omits, so it is read on demand.
    """
    sessions, warnings = discover_sessions(results_dir)
    placements: Dict[Tuple[Any, ...], Placement] = {}
    for s in sessions:
        if dns_cache is not None:
            cache = _session_dns_cache(results_dir, s.run)
            if cache != dns_cache:
                # Surface the exclusion rather than dropping silently — a session
                # must not vanish from a provenance-sensitive set without a trace.
                # A genuinely unreadable summary is already dropped upstream by
                # discover_sessions; reaching here with cache=None means the
                # summary parsed but had no session.dnsCache field.
                why = "no dnsCache field" if cache is None else f"dnsCache={cache!r}"
                warnings.append(f"{s.run}: excluded from {campaign} ({why}, want {dns_cache!r})")
                continue
        fault = s.key.fault or "unknown"
        r = s.key.replicas
        mode = s.key.mode or ""  # placement mode (packed/anti-affine), not the assignment method
        for condition, obs in s.levels.items():
            if not obs.accepted:
                continue
            f = obs.target_f
            # Group by fault too: a Placement carries one fault label, so merging
            # different-fault sessions at the same (f, r, mode) would mislabel the
            # point. Single-fault campaign dirs are unaffected (fault is constant).
            key = (round(f, 4), r, mode, fault)
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
#: are reported in C2-OB-REPORT.md (H3).
CAMPAIGNS: Tuple[Tuple[str, str, str, Optional[str]], ...] = (
    ("C1", "c1-online-boutique", "frontier", None),
    ("C3", "c3-dns", "corroboration", "on"),
)


def _display_ids(placements: List[Placement]) -> Dict[int, str]:
    """A unique human-readable id per placement, keyed by ``id(p)``.

    The base is ``"{campaign}:{label}"``, but ``_placement_label`` omits the mode
    for r=1 and never includes the fault, so two placements can share a base (e.g.
    the same (f, r, mode) under different faults in one results tree). When a base
    is shared, every colliding placement gets its fault appended so the id stays
    unambiguous everywhere it is shown (the non-dominated list, the figure). On
    data with no collisions the id equals the bare ``"{campaign}:{label}"``.
    """
    bases: Dict[str, int] = {}
    for p in placements:
        base = f"{p.campaign}:{p.label}"
        bases[base] = bases.get(base, 0) + 1
    out: Dict[int, str] = {}
    for p in placements:
        base = f"{p.campaign}:{p.label}"
        out[id(p)] = f"{base} ({p.fault})" if bases[base] > 1 else base
    return out


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
    # Only placements with all DVs present can be ranked; an incomplete one is
    # surfaced as unranked rather than silently sitting on the frontier.
    complete = [p for p in frontier_set if _is_complete(p)]
    for p in frontier_set:
        if not _is_complete(p):
            missing = [dv.key for dv in DVS if p.stats.get(dv.key, {}).get("point") is None]
            warnings.append(
                f"{p.campaign}:{p.label}: incomplete (missing {', '.join(missing)}) "
                "— unranked, excluded from dominance"
            )
    nd = non_dominated(complete)
    nd_ids = {id(p) for p in nd}
    # Membership by object identity, not (campaign, label): the label never includes
    # the fault class (and omits mode for r=1), so the same (f, r, mode) under two
    # faults collides on label. The shown identifiers come from _display_ids, which
    # disambiguates any such collision by fault.
    display = _display_ids(all_placements)
    return {
        "deltas": {dv.key: dv.delta for dv in DVS},
        "placements": [_placement_dict(p, nd_ids, display[id(p)]) for p in all_placements],
        # Unique, collision-disambiguated identifiers (NOT bare campaign:label) — so
        # this list is unambiguous even when two placements share a campaign:label.
        "nonDominated": [display[id(p)] for p in nd],
        "frontierSize": len(frontier_set),
        # rankedCount = frontier placements eligible for dominance (all DVs present).
        # frontierSize may exceed it (incomplete placements are unranked, not dominated),
        # so the non-dominated fraction is reported over rankedCount, not frontierSize.
        "rankedCount": len(complete),
        "nonDominatedCount": len(nd),
        "warnings": warnings,
    }


def _placement_dict(p: Placement, nd_ids: set, display_id: str) -> Dict[str, Any]:
    # nonDominated is None for corroboration points AND for incomplete frontier
    # placements (those never enter nd_ids) — i.e. "not ranked", distinct from False.
    if p.role != "frontier" or not _is_complete(p):
        ranked: Optional[bool] = None
    else:
        ranked = id(p) in nd_ids
    return {
        "campaign": p.campaign,
        "label": p.label,
        "displayId": display_id,  # unique, collision-disambiguated identifier
        "f": p.f,
        "r": p.r,
        "mode": p.mode,
        "fault": p.fault,
        "role": p.role,
        "nonDominated": ranked,
        "stats": p.stats,
    }


# ──────────────────────────────────────────────────────────────────────
# Rendering
# ──────────────────────────────────────────────────────────────────────


def _coord_or(v: Optional[float], fallback: float) -> float:
    """A plot coordinate or its fallback — explicit None test so a valid 0.0 is
    NOT treated as missing (``0.0 or fallback`` would wrongly pick the fallback)."""
    return fallback if v is None else v


def _scatter_colour_kw(err: Optional[float]) -> Dict[str, Any]:
    """scatter() colour kwargs for one point's error rate.

    A *missing* error rate plots solid grey — NOT cmap 0.0, which would read as
    "no errors". A present value is colour-mapped on viridis over [0, 1]. The
    cmap/vmin/vmax args are passed ONLY when mapping a value, so the grey string
    colour doesn't trigger matplotlib's "unused colormap args" warning.
    """
    if err is None:
        return {"c": "lightgray"}
    return {"c": [err], "cmap": "viridis", "vmin": 0, "vmax": 1}


def _fmt(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.4g}"


def render(result: Dict[str, Any]) -> str:
    lines = [
        "H4 placement frontier (descriptive) — non-dominated set under margins",
        f"  δ: latency {DVS[0].delta} ms, depth {DVS[1].delta} pod, error {DVS[2].delta}",
        "",
        f"  {'placement':28} {'fault':10} {'EWp95':>9} {'depth':>8} {'err':>8} {'ND?':>4} role",
    ]
    for p in result["placements"]:
        st = p["stats"]
        nd = "—" if p["nonDominated"] is None else ("Y" if p["nonDominated"] else "n")
        lines.append(
            f"  {p['displayId']:28.28} {p['fault']:10.10} "
            f"{_fmt(st['ew_p95_pre_ms']['point']):>9} "
            f"{_fmt(st['es_trough_depth_pods']['point']):>8} "
            f"{_fmt(st['user_err_during']['point']):>8} {nd:>4} {p['role']}"
        )
    lines.append("")
    # Denominator is rankedCount (placements eligible for dominance), not frontierSize —
    # an unranked/incomplete placement is not "dominated". Note any unranked count, and
    # print an explicit placeholder when the non-dominated set is empty.
    ranked = result.get("rankedCount", result["frontierSize"])
    unranked = result["frontierSize"] - ranked
    members = ", ".join(result["nonDominated"]) if result["nonDominated"] else "(none)"
    suffix = f"  [{unranked} unranked]" if unranked else ""
    lines.append(
        f"  Non-dominated frontier ({result['nonDominatedCount']}/{ranked}): {members}{suffix}"
    )
    if result["warnings"]:
        lines.append(f"  ({len(result['warnings'])} warning(s))")
    return "\n".join(lines)


def plot(result: Dict[str, Any], out_path: str) -> Tuple[Any, Any]:
    """Two-face scatter: latency (x) vs trough depth (y), error rate as colour.

    Returns ``(fig, ax)`` (the figure is also saved and closed) so callers/tests
    can inspect rendered properties — e.g. a missing-error point's grey facecolor.
    """
    import matplotlib

    # Select the headless Agg backend only if no one has imported pyplot yet —
    # don't stomp a backend another module already configured (savefig works on
    # any backend, so we never need to force it once one is chosen).
    if "matplotlib.pyplot" not in sys.modules:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _or = _coord_or
    fig, ax = plt.subplots(figsize=(9, 6))
    markers = {"node-drain": "s", "pod-delete": "o"}
    sc = None
    max_depth = 0.0
    for i, p in enumerate(
        sorted(result["placements"], key=lambda q: _or(q["stats"]["ew_p95_pre_ms"]["point"], 0.0))
    ):
        st = p["stats"]
        x, y = st["ew_p95_pre_ms"]["point"], st["es_trough_depth_pods"]["point"]
        if x is None or y is None:
            continue
        max_depth = max(max_depth, _or(st["es_trough_depth_pods"]["ci_high"], y))
        err = st["user_err_during"]["point"]
        corro = p["role"] == "corroboration"
        nd = p["nonDominated"] is True
        colour_kw = _scatter_colour_kw(err)
        scatter = ax.scatter(
            x,
            y,
            marker=markers.get(p["fault"], "o"),
            s=220 if nd else 90,
            edgecolors="red" if nd else ("gray" if corro else "black"),
            linewidths=2.0 if nd else 1.0,
            alpha=0.55 if corro else 0.95,
            zorder=3 if nd else 2,
            **colour_kw,
        )
        if err is not None:
            sc = scatter  # only a colour-mapped point gates the colorbar
        # Clamp arms to ≥ 0: the point is the EXACT median but the CI bounds come
        # from bootstrap_ci ROUNDED to 2 dp, so a bound can land a hair past the
        # point (e.g. ci_low 21.87 vs point 21.869) — a tiny negative arm that
        # matplotlib's errorbar rejects. A 2-dp rounding excursion is visually nil.
        ax.errorbar(
            x,
            y,
            xerr=[
                [max(0.0, x - _or(st["ew_p95_pre_ms"]["ci_low"], x))],
                [max(0.0, _or(st["ew_p95_pre_ms"]["ci_high"], x) - x)],
            ],
            yerr=[
                [max(0.0, y - _or(st["es_trough_depth_pods"]["ci_low"], y))],
                [max(0.0, _or(st["es_trough_depth_pods"]["ci_high"], y) - y)],
            ],
            fmt="none",
            ecolor="gray",
            alpha=0.4,
            zorder=1,
        )
        # Stagger labels vertically (alternating) so the clustered points stay legible.
        dy = 12 if i % 2 == 0 else -16
        ax.annotate(
            p["displayId"],
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
    ax.set_title("H4 placement frontier — red ring = non-dominated · colour = user error rate")
    if sc is not None:
        # Dedicated mappable so the colorbar is correct regardless of whether the
        # last-plotted point used the cmap (a grey "missing error" point would not).
        import matplotlib.cm as cm
        from matplotlib.colors import Normalize

        mappable = cm.ScalarMappable(norm=Normalize(vmin=0, vmax=1), cmap="viridis")
        fig.colorbar(mappable, ax=ax, label=DVS[2].label)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return fig, ax


def main() -> None:
    ap = argparse.ArgumentParser(description="H4 descriptive placement frontier")
    ap.add_argument(
        "--results-root",
        default="results",
        help="dir holding the c1-online-boutique/ and c3-dns/ campaign subdirs "
        "(C2 has no east-west latency face and is excluded — see CAMPAIGNS)",
    )
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
