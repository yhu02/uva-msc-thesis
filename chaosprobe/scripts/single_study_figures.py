#!/usr/bin/env python3
"""Thesis figures for the single pre-registered study (campaigns C1/C2/C3).

Each data figure reuses the campaign's confirmatory analysis driver, so every
number printed on a figure is the same number the results chapter reports — no
re-derivation, no drift. Figures read **archived** session data only (no live
cluster).

Figure set (one per hypothesis, plus the workflow schematic):

    fig-01-workflow.png          ChaosProbe experiment workflow (schematic; reused)
    fig-h1-dose-response.png     H1  C1 east-west p95 vs cross-node fraction f
    fig-h2-conntrack-dns.png     H2  C3 conntrack drop: placement reversal + DNS removal
    fig-h3-replication-rescue.png H3 C2 trough depth + user error across r×mode cells
    fig-h4-frontier.png          H4  C1 two-face placement frontier (latency vs availability)
    fig-h5-scorecard-icc.png     H5  C1 layered-scorecard ICC vs naive aggregate

Usage (from ``chaosprobe/``)::

    uv run python scripts/single_study_figures.py --out-dir ../thesis/figures
    uv run python scripts/single_study_figures.py --out-dir ../thesis/figures --figure h1,h5
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Sequence

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import c1_h1_trend  # noqa: E402
import c2_h3_anova  # noqa: E402
import c3_h2_dns  # noqa: E402
import scorecard  # noqa: E402
import v2_h4_frontier  # noqa: E402

# Pull `plt` from thesis_figures (it calls matplotlib.use("Agg") *before*
# importing pyplot) so the Agg backend is selected before pyplot is touched.
from thesis_figures import (  # noqa: E402  (reuse the shared house style + workflow schematic)
    ACCENT,
    DARK,
    _save,
    apply_thesis_style,
    fig01_workflow,
    plt,
)

# Okabe-Ito colorblind-safe accents used across the data figures.
PACKED_C = "#D55E00"  # packed / co-located
SPREAD_C = "#009E73"  # spread
BAR_OK = "#009E73"  # clears a registered bar
BAR_NO = "#999999"  # does not clear the bar
MARGIN_C = "#CC79A7"  # registered margin / SESOI reference

DEFAULT_C1 = "results/c1-online-boutique"
DEFAULT_C2 = "results/c2-roundrobin"
DEFAULT_C3 = "results/c3-dns"
DEFAULT_RESULTS_ROOT = "results"


def _annotate(ax, text: str) -> None:
    """A small bar-verdict caption box, bottom-left of an axes."""
    ax.text(
        0.02,
        0.03,
        text,
        transform=ax.transAxes,
        fontsize=8,
        va="bottom",
        ha="left",
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#cccccc", alpha=0.9),
    )


# ── H1 — dose-response of the east-west tail (C1) ───────────────────────────────
def fig_h1_dose(c1_dir: str, out_dir: str) -> str:
    res: Any = c1_h1_trend.analyze(c1_dir)
    pl = res["sesoi"]["perLevelGrandMedian"]
    levels = [f for _, f in c1_h1_trend.LEVELS]
    y = [pl[cond] for cond, _ in c1_h1_trend.LEVELS]
    page = res["pageTrendTest"]
    sesoi = res["sesoi"]
    f0 = sesoi["f0"]
    sesoi_thresh = f0 * (1.0 + sesoi["sesoiPct"] / 100.0)

    fig, ax = plt.subplots(figsize=(6.2, 4.0))
    ax.plot(levels, y, marker="o", color=ACCENT, lw=2, zorder=3, label="per-level grand median")
    for x, yy in zip(levels, y):
        ax.annotate(
            f"{yy:.1f}",
            (x, yy),
            textcoords="offset points",
            xytext=(0, 8),
            ha="center",
            fontsize=8,
            color=DARK,
        )
    # Registered SESOI threshold: f=0 baseline + 15 %.
    ax.axhline(
        sesoi_thresh,
        ls="--",
        color=MARGIN_C,
        lw=1.4,
        label=f"{sesoi['sesoiPct']:.0f}% SESOI threshold ({sesoi_thresh:.1f} ms)",
    )
    ax.set_xlabel("cross-node fraction $f$ (packing $\\rightarrow$ spreading)")
    ax.set_ylabel("east-west p95 latency (ms), pre-chaos")
    ax.set_xticks(levels)
    ax.set_ylim(bottom=min(y) - 3, top=max(sesoi_thresh, max(y)) + 3)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    _annotate(
        ax,
        f"Page's $L$={page['l_statistic']:.0f}, $z$={page['z']:.2f}, "
        f"$p$={page['p_one_sided']:.4f}\n"
        f"$f$=0$\\rightarrow$1: +{sesoi['pctChange']:.2f}% "
        f"(below {sesoi['sesoiPct']:.0f}% SESOI $\\rightarrow$ not supported)",
    )
    ax.set_title("H1 — dose-response of the east-west tail (C1)")
    return _save(fig, out_dir, "fig-h1-dose-response.png")


# ── H2 — placement reversal + DNS removal (C3) ──────────────────────────────────
def fig_h2_conntrack(c3_dir: str, out_dir: str) -> str:
    res: Any = c3_h2_dns.analyze(c3_dir)
    place = res["placementDependence"]  # median_a=spread, median_b=packed (cache-off)
    sec = res["secondaryPackedCacheEffect"]  # median_a=packed off, median_b=packed on
    mech = res["mechanismShrinkage"]  # spread shrinkage fraction

    packed_off = place["median_b"]
    spread_off = place["median_a"]
    packed_on = sec["median_b"]
    shrink_pct = mech["shrinkageMedian"] * 100.0

    fig, (axa, axb) = plt.subplots(1, 2, figsize=(8.4, 4.0))

    # (a) cache-off placement dependence — the reversal.
    axa.bar(
        ["packed\n($f{=}0$)", "spread\n($f{=}1$)"],
        [packed_off, spread_off],
        color=[PACKED_C, SPREAD_C],
        width=0.6,
        zorder=3,
    )
    for i, v in enumerate([packed_off, spread_off]):
        axa.annotate(
            f"{v:.0f}",
            (i, v),
            textcoords="offset points",
            xytext=(0, 4),
            ha="center",
            fontsize=9,
            color=DARK,
        )
    axa.set_ylabel("during-churn UDP-conntrack drop (entries)")
    axa.set_title("(a) placement dependence, cache off")
    _annotate(
        axa,
        f"packed $>$ spread in {place['n_pairs']}/{place['n_pairs']} pairs\n"
        f"(direction reversed vs registered)",
    )

    # (b) DNS intervention removes the drop.
    axb.bar(
        ["packed\ncache off", "packed\ncache on"],
        [packed_off, packed_on],
        color=[PACKED_C, BAR_OK],
        width=0.6,
        zorder=3,
    )
    for i, v in enumerate([packed_off, packed_on]):
        axb.annotate(
            f"{v:.0f}",
            (i, v),
            textcoords="offset points",
            xytext=(0, 4 if v >= 0 else -12),
            ha="center",
            fontsize=9,
            color=DARK,
        )
    axb.axhline(0, color="#cccccc", lw=1)
    axb.set_ylabel("during-churn UDP-conntrack drop (entries)")
    axb.set_title("(b) NodeLocal DNSCache removes the drop")
    _annotate(
        axb,
        f"spread: $-${shrink_pct:.0f}% with cache on\n"
        f"($p$={mech['p_one_sided']:.4f}, $\\geq$50% bar met)",
    )

    fig.suptitle("H2 — conntrack placement-dependence and the DNS intervention (C3)")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return _save(fig, out_dir, "fig-h2-conntrack-dns.png")


# ── H3 — replication rescue under node-drain (C2) ───────────────────────────────
def fig_h3_rescue(c2_dir: str, out_dir: str) -> str:
    res: Any = c2_h3_anova.analyze(c2_dir)
    depth = res["troughDepthFraction"]
    error = res["userErrorRate"]
    cells = ["r1", "r3_packed", "r3_anti"]
    labels = ["$r{=}1$\npacked", "$r{=}3$\npacked", "$r{=}3$\nanti-affine"]
    colors = [BAR_NO, PACKED_C, SPREAD_C]
    depth_margin = res["depthMarginFraction"]

    fig, (axd, axe) = plt.subplots(1, 2, figsize=(8.4, 4.0))

    dv = [depth["median"][c] for c in cells]
    axd.bar(labels, dv, color=colors, width=0.6, zorder=3)
    for i, v in enumerate(dv):
        axd.annotate(
            f"{v:.4f}",
            (i, v),
            textcoords="offset points",
            xytext=(0, 4),
            ha="center",
            fontsize=8,
            color=DARK,
        )
    axd.axhline(
        depth_margin, ls="--", color=MARGIN_C, lw=1.4, label=f"1-pod margin ({depth_margin:.4f})"
    )
    axd.set_ylabel("trough depth (fraction of app pods)")
    axd.set_title("(a) trough-depth co-primary")
    axd.legend(loc="upper right", fontsize=8)
    _annotate(
        axd,
        f"interaction $p$={depth['artInteraction']['p']:.4f}\n"
        f"rescue {depth['rescueObserved']:.4f} $<$ margin "
        f"$\\rightarrow$ not met",
    )

    ev = [error["median"][c] for c in cells]
    axe.bar(labels, ev, color=colors, width=0.6, zorder=3)
    for i, v in enumerate(ev):
        axe.annotate(
            f"{v:.4f}",
            (i, v),
            textcoords="offset points",
            xytext=(0, 4),
            ha="center",
            fontsize=8,
            color=DARK,
        )
    axe.axhline(
        c2_h3_anova.ERROR_MARGIN,
        ls="--",
        color=MARGIN_C,
        lw=1.4,
        label=f"rescue margin ({c2_h3_anova.ERROR_MARGIN})",
    )
    axe.set_ylabel("user-route error rate")
    axe.set_title("(b) user-route error co-primary")
    axe.legend(loc="upper right", fontsize=8)
    _annotate(
        axe,
        f"interaction $p \\approx 0$\n"
        f"rescue {error['rescueObserved']:.4f} $\\geq$ margin "
        f"$\\rightarrow$ met",
    )

    fig.suptitle("H3 — replication rescue under node-drain (C2)")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return _save(fig, out_dir, "fig-h3-replication-rescue.png")


# ── H4 — placement frontier (descriptive; C1 pod-delete placements) ─────────────
def fig_h4_frontier(results_root: str, out_dir: str) -> str:
    fr: Any = v2_h4_frontier.build_frontier(results_root)
    pts = [p for p in fr["placements"] if p["role"] == "frontier" and p["campaign"] == "C1"]
    pts.sort(key=lambda p: p["f"])
    xs = [p["stats"]["ew_p95_pre_ms"]["point"] for p in pts]
    ys = [p["stats"]["user_err_during"]["point"] for p in pts]
    n_nd = sum(1 for p in pts if p["nonDominated"])

    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    ax.scatter(xs, ys, s=110, facecolor=ACCENT, edgecolor="#b22222", linewidth=2, zorder=3)
    for p, x, y in zip(pts, xs, ys):
        ax.annotate(
            f"$f$={p['f']:g}",
            (x, y),
            textcoords="offset points",
            xytext=(0, 10),
            ha="center",
            fontsize=8,
            color=DARK,
        )
    ax.set_xlabel("east-west p95 latency (ms) — latency face (lower better)")
    ax.set_ylabel("user-route error rate — availability face (lower better)")
    ax.set_ylim(-0.02, max(0.12, max(ys) + 0.04))
    ax.margins(x=0.12)
    ax.set_title("H4 — placement frontier (C1, pod-delete; descriptive)")
    _annotate(
        ax,
        f"all {n_nd}/{len(pts)} placements non-dominated\n"
        "availability face degenerate: trough depth $\\approx$ 1 pod for every placement",
    )
    return _save(fig, out_dir, "fig-h4-frontier.png")


# ── H5 — layered scorecard reliability (C1) ─────────────────────────────────────
def fig_h5_icc(c1_dir: str, out_dir: str) -> str:
    res: Any = scorecard.analyze(c1_dir)
    subs = {row["subscore"]: row for row in res["subscores"]}
    v1 = res["iccV1"]
    bar = res["absoluteIccBar"]

    # Order top-to-bottom: mechanism (passes), availability (fails),
    # user_tail (exploratory), naive aggregate (comparator).
    rows = [
        (
            "mechanism",
            subs["mechanism"]["iccSub"],
            subs["mechanism"]["iccSubCiLow"],
            subs["mechanism"]["iccSubCiHigh"],
            BAR_OK,
            "required",
        ),
        (
            "availability",
            subs["availability"]["iccSub"],
            subs["availability"]["iccSubCiLow"],
            subs["availability"]["iccSubCiHigh"],
            BAR_NO,
            "required",
        ),
        (
            "user-tail",
            subs["user_tail"]["iccSub"],
            subs["user_tail"]["iccSubCiLow"],
            subs["user_tail"]["iccSubCiHigh"],
            "#56B4E9",
            "exploratory",
        ),
        ("naive aggregate", v1["icc"], v1["ciLow"], v1["ciHigh"], "#bbbbbb", "baseline"),
    ]
    ypos = list(range(len(rows)))[::-1]

    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    for y, (name, icc, lo, hi, color, role) in zip(ypos, rows):
        ax.barh(y, icc, color=color, height=0.55, zorder=3)
        ax.errorbar(
            icc,
            y,
            xerr=[[icc - lo], [hi - icc]],
            fmt="none",
            ecolor=DARK,
            elinewidth=1.2,
            capsize=4,
            zorder=4,
        )
        ax.annotate(
            f"{icc:.3f}",
            (icc, y),
            textcoords="offset points",
            xytext=(6, 0),
            va="center",
            fontsize=9,
            color=DARK,
        )
    ax.axvline(bar, ls="--", color=MARGIN_C, lw=1.5, label=f"ICC $\\geq$ {bar} bar")
    ax.set_yticks(ypos)
    ax.set_yticklabels([f"{name}\n({role})" for name, *_, role in rows], fontsize=8)
    ax.set_xlabel("condition-level test-retest reliability (ICC)")
    ax.set_xlim(0, 1.05)
    ax.legend(loc="lower right", fontsize=8)
    ax.set_title("H5 — layered scorecard reliability vs naive aggregate (C1)")
    fig.tight_layout()
    return _save(fig, out_dir, "fig-h5-scorecard-icc.png")


ALL_FIGURES = ("workflow", "h1", "h2", "h3", "h4", "h5")


def parse_figures(spec: str) -> List[str]:
    if spec.strip().lower() == "all":
        return list(ALL_FIGURES)
    figs = [s.strip().lower() for s in spec.split(",") if s.strip()]
    bad = [f for f in figs if f not in ALL_FIGURES]
    if bad:
        raise SystemExit(
            f"unknown figure(s): {', '.join(bad)} (choose from {', '.join(ALL_FIGURES)})"
        )
    return figs


def generate(
    figures: Sequence[str], out_dir: str, c1: str, c2: str, c3: str, results_root: str
) -> Dict[str, str]:
    apply_thesis_style()
    written: Dict[str, str] = {}
    if "workflow" in figures:
        written["workflow"] = fig01_workflow(out_dir)
    if "h1" in figures:
        written["h1"] = fig_h1_dose(c1, out_dir)
    if "h2" in figures:
        written["h2"] = fig_h2_conntrack(c3, out_dir)
    if "h3" in figures:
        written["h3"] = fig_h3_rescue(c2, out_dir)
    if "h4" in figures:
        written["h4"] = fig_h4_frontier(results_root, out_dir)
    if "h5" in figures:
        written["h5"] = fig_h5_icc(c1, out_dir)
    return written


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out-dir", required=True, help="directory to write the PNGs into")
    ap.add_argument(
        "--figure",
        default="all",
        help="'all' or comma-separated subset of " + ", ".join(ALL_FIGURES),
    )
    ap.add_argument("--c1-dir", default=DEFAULT_C1)
    ap.add_argument("--c2-dir", default=DEFAULT_C2)
    ap.add_argument("--c3-dir", default=DEFAULT_C3)
    ap.add_argument(
        "--results-root",
        default=DEFAULT_RESULTS_ROOT,
        help="root containing the campaign dirs (for the H4 frontier)",
    )
    args = ap.parse_args(argv)
    written = generate(
        parse_figures(args.figure),
        args.out_dir,
        args.c1_dir,
        args.c2_dir,
        args.c3_dir,
        args.results_root,
    )
    for key, path in written.items():
        print(f"  {key:9s} -> {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover  (CLI entrypoint)
    raise SystemExit(main())
