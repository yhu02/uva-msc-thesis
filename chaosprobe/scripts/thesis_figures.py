#!/usr/bin/env python3
"""Generate the thesis's publication figure set from ARCHIVED run artifacts.

Every figure is rendered from committed/archived run outputs (the campaign
sessions in ``campaign-results/s01..s07`` and the H4/H5/H6 runs in
``results/``), and every number printed on a figure is computed from that data
at generation time — nothing is hardcoded. The data-extraction logic reuses the
committed analysis scripts (``campaign_status``, ``cross_node_fraction``,
``blast_radius``, ``h3_mechanism_outcome``) so the figures and the numeric
analyses can never drift apart.

The set:

  fig-01-workflow.png             ChaosProbe pipeline diagram.
  fig-02-core-matrix.png          Strategy x fault-class experiment matrix.
  fig-03-h1-score-distributions   Per-strategy score distributions + ICC.
  fig-04-h1-icc-trajectory        ICC point + CI as sessions accumulate.
  fig-05-h2-conntrack             Conntrack flush %% per strategy + paired test.
  fig-06-h3-scatter               Flush %% vs dependent/control route p95.
  fig-07-h5-fraction-vs-tail      Cross-node fraction vs east-west p95.
  fig-08-h6-trough-timeline       EndpointSlice ready trajectory through drain.
  fig-09-tradeoff                 Latency tail vs blast radius (the capstone).

Usage
-----
    uv run python scripts/thesis_figures.py --out-dir ../thesis/figures \
        [--figure all|1..9] [--gradient-run results/<ts>]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics as st
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")

import blast_radius  # noqa: E402
import campaign_status  # noqa: E402
import cross_node_fraction as xnf  # noqa: E402
import h3_mechanism_outcome as h3  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from fault_taxonomy import CHURN, fault_class, is_churn  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle  # noqa: E402

from chaosprobe.metrics.statistics import icc_bootstrap, wilcoxon_signed_rank  # noqa: E402

# ── shared style ──────────────────────────────────────────────────────────────
# Okabe-Ito colorblind-safe palette, serif text, no chartjunk. Applied once by
# apply_thesis_style(); every figure saves at 200 dpi via _save().

DPI = 200

PALETTE: Dict[str, str] = {  # Okabe-Ito per strategy
    "baseline": "#999999",
    "default": "#0072B2",
    "colocate": "#D55E00",
    "spread": "#009E73",
    "random": "#E69F00",
    "adversarial": "#CC79A7",
    "best-fit": "#56B4E9",
    "dependency-aware": "#F0E442",
}
STRATEGY_ORDER: Tuple[str, ...] = (
    "baseline",
    "default",
    "colocate",
    "spread",
    "random",
    "adversarial",
    "best-fit",
    "dependency-aware",
)
ACCENT = "#0072B2"
DARK = "#222222"

DEFAULT_CAMPAIGN_DIR = "campaign-results"
DEFAULT_H4_RUNS: Tuple[str, ...] = ("results/20260607-193021", "results/20260607-221744")
DEFAULT_H5_RUN = "results/20260608-070606"
DEFAULT_H6_RUNS: Tuple[str, ...] = ("results/20260608-194746", "results/20260608-205147")

# The 3 fault classes of the thesis matrix, with the hypotheses each feeds.
MATRIX_CLASSES: Tuple[str, ...] = ("churn", "load contention", "node drain")
MATRIX_HYPOTHESES: Dict[str, str] = {  # pre-wrapped for the fig-02 column labels
    "churn": "H1 score · H2 conntrack\n· H3 confound",
    "load contention": "H4 contention\n· H5 cross-node tail",
    "node drain": "H6 blast radius",
}
NODE_LOCAL_FRACTION = 0.5  # below this cross-node fraction a placement is node-local


def apply_thesis_style() -> None:
    """Set the shared matplotlib style: serif, colorblind-safe, no chartjunk."""
    matplotlib.rcParams.update(
        {
            "figure.dpi": 100,
            "savefig.dpi": DPI,
            "savefig.bbox": "tight",
            "font.family": "serif",
            "font.serif": ["DejaVu Serif", "Times New Roman", "Georgia"],
            "mathtext.fontset": "dejavuserif",
            "font.size": 9.5,
            "axes.titlesize": 10.5,
            "axes.labelsize": 9.5,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 8.5,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "grid.linewidth": 0.6,
            "axes.axisbelow": True,
            "legend.frameon": False,
            "axes.edgecolor": DARK,
            "text.color": DARK,
            "axes.labelcolor": DARK,
            "xtick.color": DARK,
            "ytick.color": DARK,
        }
    )


def _save(fig: Figure, out_dir: str, name: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return path


# ── loading ───────────────────────────────────────────────────────────────────


def load_summary(path: str) -> Dict[str, Any]:
    """Load one run's summary.json (the per-run analysis artifact)."""
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object at the top level")
    return data


def campaign_session_paths(campaign_dir: str) -> List[Tuple[str, str]]:
    """Sorted (session-name, summary-path) for the sNN campaign sessions only.

    The campaign dir may also hold in-flight runs with only a
    ``partial_summary.json``; restricting to ``s[0-9][0-9]/summary.json`` keeps
    the figure inputs pinned to the archived, doctor-gated sessions.
    """
    out: List[Tuple[str, str]] = []
    for entry in sorted(os.listdir(campaign_dir)):
        if len(entry) == 3 and entry[0] == "s" and entry[1:].isdigit():
            path = os.path.join(campaign_dir, entry, "summary.json")
            if os.path.isfile(path):
                out.append((entry, path))
    return out


# ── extraction: campaign (H1/H2) ─────────────────────────────────────────────

Cells = Dict[Tuple[object, object], List[float]]


@dataclass(frozen=True)
class SessionData:
    """The per-session reduction the H1/H2 figures need (full JSON is dropped)."""

    name: str
    scores: Dict[str, List[float]]  # strategy -> per-iteration scores (churn, incl. baseline)
    flush: Dict[str, float]  # strategy -> conntrack flush % (baseline excluded)


def extract_session(name: str, summary: Mapping[str, Any]) -> SessionData:
    """Reduce one campaign summary to the score/flush data the figures use.

    Churn (pod-delete) faults only, matching the H1/H2 statistics. Baseline is
    kept in ``scores`` (shown as the control reference) but excluded from
    ``flush`` and from every statistic, mirroring ``campaign_status``.
    """
    scores: Dict[str, List[float]] = {}
    flush: Dict[str, float] = {}
    for fault_name, fault in (summary.get("faults") or {}).items():
        if not is_churn(fault_name):
            continue
        for sname, strat in ((fault or {}).get("strategies") or {}).items():
            pis = (((strat or {}).get("experiment") or {}).get("perIterationScores")) or []
            if pis:
                scores.setdefault(sname, []).extend(float(v) for v in pis)
            if sname == "baseline":
                continue
            pct = campaign_status._flush_pct(strat or {})
            if pct is not None:
                flush[sname] = pct
    return SessionData(name=name, scores=scores, flush=flush)


def score_cells(sessions: Sequence[SessionData]) -> Cells:
    """(strategy, session) -> per-iteration scores, baseline excluded (H1 shape)."""
    cells: Cells = {}
    for sess in sessions:
        for sname, vals in sess.scores.items():
            if sname != "baseline" and vals:
                cells[(sname, sess.name)] = list(vals)
    return cells


def _as_float(value: object) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) else None


@dataclass(frozen=True)
class IccPoint:
    """ICC point estimate + bootstrap CI after the first n_sessions sessions."""

    n_sessions: int
    icc: Optional[float]
    ci_low: Optional[float]
    ci_high: Optional[float]


def icc_point(sessions: Sequence[SessionData], n_resamples: int = 2000) -> IccPoint:
    """ICC_strategy with cluster-bootstrap CI over the given sessions."""
    result = icc_bootstrap(score_cells(sessions), n_resamples=n_resamples)
    return IccPoint(
        n_sessions=len(sessions),
        icc=_as_float(result.get("icc")),
        ci_low=_as_float(result.get("ci_low")),
        ci_high=_as_float(result.get("ci_high")),
    )


def icc_trajectory(sessions: Sequence[SessionData], n_resamples: int = 2000) -> List[IccPoint]:
    """ICC + CI recomputed on each session prefix s01..s0k (the H1 trajectory)."""
    return [icc_point(sessions[:k], n_resamples) for k in range(1, len(sessions) + 1)]


@dataclass(frozen=True)
class FlushStats:
    """H2: per-strategy conntrack flush across sessions + the paired test."""

    per_strategy: Dict[str, List[float]]
    pairs: List[Tuple[str, float, float]]  # (session, spread flush, colocate flush)
    wins: int  # sessions with spread > colocate
    sign_p: Optional[float]
    wilcoxon_p: Optional[float]

    def median(self, strategy: str) -> Optional[float]:
        vals = self.per_strategy.get(strategy)
        return st.median(vals) if vals else None


def flush_stats(sessions: Sequence[SessionData]) -> FlushStats:
    """Aggregate the H2 flush data and run the paired spread-vs-colocate test."""
    per_strategy: Dict[str, List[float]] = {}
    pairs: List[Tuple[str, float, float]] = []
    for sess in sessions:
        for sname, pct in sess.flush.items():
            per_strategy.setdefault(sname, []).append(pct)
        if "spread" in sess.flush and "colocate" in sess.flush:
            pairs.append((sess.name, sess.flush["spread"], sess.flush["colocate"]))
    wins = sum(1 for _, sp, co in pairs if sp > co)
    sign_p: Optional[float] = None
    wilcoxon_p: Optional[float] = None
    if pairs:
        w = wilcoxon_signed_rank([sp for _, sp, _ in pairs], [co for _, _, co in pairs])
        wilcoxon_p = _as_float(w.get("p_two_sided"))
        sign = w.get("sign_test")
        if isinstance(sign, Mapping):
            sign_p = _as_float(sign.get("p_two_sided"))
    return FlushStats(
        per_strategy=per_strategy, pairs=pairs, wins=wins, sign_p=sign_p, wilcoxon_p=wilcoxon_p
    )


# ── extraction: H3 scatter ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ScatterStats:
    """One mechanism-vs-outcome panel: the points and their rank correlation."""

    pairs: List[Tuple[float, float]]
    rho: float
    p: float
    n: int


def h3_scatter(rows: Sequence[Mapping[str, object]], outcome_key: str) -> ScatterStats:
    """Pair conntrack flush %% with a route-tail outcome across (run, strategy) cells."""
    pairs = [
        (float(x), float(y))
        for x, y in ((row.get("conntrack_flush_pct"), row.get(outcome_key)) for row in rows)
        if isinstance(x, (int, float)) and isinstance(y, (int, float))
    ]
    rho, p, n = h3.spearman([a for a, _ in pairs], [b for _, b in pairs])
    return ScatterStats(pairs=pairs, rho=rho, p=p, n=n)


# ── extraction: H5 (cross-node fraction vs east-west tail) ───────────────────


@dataclass(frozen=True)
class H5Point:
    """One strategy in the load run: realised fraction, tail, locality class."""

    strategy: str
    fraction: float
    ew_p95: float
    node_local: bool


def h5_points(summary: Mapping[str, Any]) -> List[H5Point]:
    """Per-strategy (cross-node fraction, during-load east-west median p95)."""
    points: List[H5Point] = []
    for name, strat in xnf._strategies(dict(summary)).items():
        rva = ((strat.get("aggregated") or {}).get("routeViewAggregate")) or []
        edges = xnf.edges_from_route_view(rva)
        fracs = [
            f
            for it in (strat.get("iterations") or [])
            if (f := xnf.cross_node_fraction(it.get("podPlacements") or {}, edges)) is not None
        ]
        p95 = xnf.east_west_median_p95(rva)
        if fracs and p95 is not None:
            frac = st.mean(fracs)
            points.append(
                H5Point(
                    strategy=name,
                    fraction=frac,
                    ew_p95=p95,
                    node_local=frac < NODE_LOCAL_FRACTION,
                )
            )
    return points


def h5_spearman(points: Sequence[H5Point]) -> Tuple[float, float, int]:
    """(rho, p, n) for cross-node fraction vs east-west p95 over the strategies."""
    return h3.spearman([p.fraction for p in points], [p.ew_p95 for p in points])


# ── extraction: H6 (node-drain blast + EndpointSlice trough) ─────────────────


@dataclass(frozen=True)
class H6Blast:
    """Observed node-drain blast for one strategy, across the H6 runs."""

    strategy: str
    blast: int  # deepest observed trough across runs (snapshots can miss it)
    measured: int  # services measurable in the run that produced `blast`
    per_run: Dict[str, int]


def h6_blast(named: Sequence[Tuple[str, Mapping[str, Any]]]) -> Dict[str, H6Blast]:
    """Per-strategy blast radius over the node-drain runs (via blast_radius).

    The duringChaos snapshot is a single sample of a transient outage, so a run
    can under-read the trough; the per-strategy figure is the *deepest* trough
    observed across runs, with the per-run values kept for the caption.
    """
    per_run: Dict[str, Dict[str, Tuple[int, int]]] = {}
    for run, summary in named:
        for sname, metrics in blast_radius.collect(dict(summary)).items():
            per_run.setdefault(sname, {})[run] = (
                metrics["blastRadius"],
                metrics["measuredServices"],
            )
    out: Dict[str, H6Blast] = {}
    for sname, runs in per_run.items():
        best_run = max(runs, key=lambda r: runs[r][0])
        blast, measured = runs[best_run]
        out[sname] = H6Blast(
            strategy=sname,
            blast=blast,
            measured=measured,
            per_run={r: b for r, (b, _) in runs.items()},
        )
    return out


@dataclass(frozen=True)
class ReadyTrajectory:
    """Total ready EndpointSlice endpoints at the pre/during/post snapshots."""

    run: str
    strategy: str
    phases: List[str]  # subset of (preChaos, duringChaos, postChaos), in order
    minutes: List[float]  # capture time, minutes since the preChaos snapshot
    ready: List[int]  # total ready endpoints across all app services
    n_services: int


_ES_PHASES: Tuple[str, ...] = ("preChaos", "duringChaos", "postChaos")


def _total_ready(phase: Mapping[str, Any]) -> int:
    services = phase.get("services") or {}
    return sum(
        v["ready"]
        for v in services.values()
        if isinstance(v, Mapping) and isinstance(v.get("ready"), int)
    )


def endpoint_trajectories(
    named: Sequence[Tuple[str, Mapping[str, Any]]], strategies: Sequence[str]
) -> List[ReadyTrajectory]:
    """EndpointSlice total-ready trajectories for the given strategies per run."""
    out: List[ReadyTrajectory] = []
    for run, summary in named:
        strats = xnf._strategies(dict(summary))
        for sname in strategies:
            es = ((strats.get(sname) or {}).get("metrics") or {}).get("endpointSlices") or {}
            phases: List[str] = []
            stamps: List[datetime] = []
            ready: List[int] = []
            n_services = 0
            for phase_name in _ES_PHASES:
                phase = es.get(phase_name)
                captured = (phase or {}).get("capturedAt")
                if not isinstance(phase, Mapping) or not isinstance(captured, str):
                    continue
                phases.append(phase_name)
                stamps.append(datetime.fromisoformat(captured))
                ready.append(_total_ready(phase))
                n_services = max(n_services, len(phase.get("services") or {}))
            if len(phases) >= 2:
                t0 = stamps[0]
                minutes = [(t - t0).total_seconds() / 60.0 for t in stamps]
                out.append(
                    ReadyTrajectory(
                        run=run,
                        strategy=sname,
                        phases=phases,
                        minutes=minutes,
                        ready=ready,
                        n_services=n_services,
                    )
                )
    return out


# ── extraction: experiment matrix (fig 2) ────────────────────────────────────


def matrix_class(fault_name: str) -> Optional[str]:
    """Map a recorded fault name onto the thesis's three fault classes."""
    if fault_class(fault_name) == CHURN:
        return "churn"
    normalized = fault_name.lower()
    if "load" in normalized or "hog" in normalized or "contention" in normalized:
        return "load contention"
    if "drain" in normalized:
        return "node drain"
    return None


def matrix_counts(named: Sequence[Tuple[str, Mapping[str, Any]]]) -> Dict[Tuple[str, str], int]:
    """(strategy, fault class) -> number of archived runs covering that cell."""
    counts: Dict[Tuple[str, str], int] = {}
    for _, summary in named:
        seen: set[Tuple[str, str]] = set()
        for fault_name, fault in (summary.get("faults") or {}).items():
            cls = matrix_class(fault_name)
            if cls is None:
                continue
            for sname in (fault or {}).get("strategies") or {}:
                seen.add((sname, cls))
        for key in seen:
            counts[key] = counts.get(key, 0) + 1
    return counts


def fault_presence(summary: Mapping[str, Any]) -> Dict[str, List[str]]:
    """fault name -> sorted strategies present (the reduced record fig 2 keeps)."""
    return {
        fault_name: sorted(((fault or {}).get("strategies") or {}).keys())
        for fault_name, fault in (summary.get("faults") or {}).items()
    }


# ── rendering helpers ────────────────────────────────────────────────────────


def _strip(ax: Axes, index: int, values: Sequence[float], color: str) -> None:
    """Jittered raw observations over a box, so n and spread stay visible."""
    n = len(values)
    xs = [index + (j - (n - 1) / 2) * 0.05 for j in range(n)]
    ax.scatter(xs, values, s=14, color=color, edgecolor=DARK, linewidth=0.4, zorder=3, alpha=0.9)


def _box_strip(ax: Axes, data: Mapping[str, Sequence[float]], order: Sequence[str]) -> List[str]:
    """Box + strip per strategy in palette colors; returns the plotted order."""
    strats = [s for s in order if data.get(s)]
    series = [list(data[s]) for s in strats]
    bp = ax.boxplot(
        series,
        patch_artist=True,
        widths=0.55,
        medianprops={"color": DARK, "linewidth": 1.2},
        whiskerprops={"color": DARK, "linewidth": 0.8},
        capprops={"color": DARK, "linewidth": 0.8},
        flierprops={"marker": ""},
    )
    for patch, sname in zip(bp["boxes"], strats):
        patch.set_facecolor(PALETTE.get(sname, "#888888"))
        patch.set_alpha(0.45)
        patch.set_edgecolor(DARK)
        patch.set_linewidth(0.8)
    for i, sname in enumerate(strats, start=1):
        _strip(ax, i, list(data[sname]), PALETTE.get(sname, "#888888"))
    ax.set_xticks(range(1, len(strats) + 1))
    ax.set_xticklabels(strats, rotation=30, ha="right")
    return strats


def _fmt_p(p: Optional[float]) -> str:
    if p is None or math.isnan(p):
        return "p=n/a"
    return f"p={p:.4f}" if p >= 0.0001 else "p<0.0001"


def _pipeline_box(
    ax: Axes, x: float, y: float, w: float, h: float, title: str, sub: str, color: str
) -> None:
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.06",
            facecolor=color,
            edgecolor=DARK,
            linewidth=1.0,
            alpha=0.85,
        )
    )
    ax.text(x + w / 2, y + h - 0.34, title, ha="center", va="center", fontsize=10, weight="bold")
    ax.text(x + w / 2, y + (h - 0.42) / 2, sub, ha="center", va="center", fontsize=8.2)


def _pipeline_arrow(ax: Axes, x0: float, x1: float, y: float) -> None:
    ax.add_patch(
        FancyArrowPatch(
            (x0, y),
            (x1, y),
            arrowstyle="-|>",
            mutation_scale=16,
            linewidth=1.4,
            color=DARK,
            shrinkA=0,
            shrinkB=0,
        )
    )


# ── figure 1: workflow ───────────────────────────────────────────────────────


def fig01_workflow(out_dir: str) -> str:
    """ChaosProbe pipeline: mutate -> inject -> collect -> gate -> analyze."""
    fig, ax = plt.subplots(figsize=(10.5, 3.7))
    ax.set_xlim(0, 30.2)
    ax.set_ylim(0, 8.4)
    ax.axis("off")
    ax.grid(False)

    y0, h = 2.2, 3.8
    _pipeline_box(
        ax,
        0.5,
        y0,
        4.6,
        h,
        "Placement\nmutation",
        "8 strategies\nnodeSelector pinning\n(Online Boutique)",
        "#E8F1F8",
    )
    _pipeline_box(
        ax,
        6.1,
        y0,
        4.6,
        h,
        "Fault injection",
        "LitmusChaos\npod-delete · cpu-hog\nload · node-drain",
        "#FBEAE2",
    )

    # Cross-layer collection: an outer container with three stacked layers.
    cx, cw = 11.7, 9.4
    ax.add_patch(
        FancyBboxPatch(
            (cx, 1.0),
            cw,
            6.6,
            boxstyle="round,pad=0.06",
            facecolor="#F4F4F4",
            edgecolor=DARK,
            linewidth=1.0,
        )
    )
    ax.text(
        cx + cw / 2,
        7.15,
        "Cross-layer collection",
        ha="center",
        va="center",
        fontsize=10,
        weight="bold",
    )
    layers = (
        ("Score layer\nLitmus probes → resilience score", "#E8F1F8"),
        (
            "Mechanism layer\nPrometheus: conntrack · CoreDNS p99\nthrottling · EndpointSlices",
            "#E5F3EE",
        ),
        ("User layer\nroute latency probes (N–S + E–W)", "#FDF3E0"),
    )
    ly = 5.0
    for label, color in layers:
        ax.add_patch(
            FancyBboxPatch(
                (cx + 0.3, ly),
                cw - 0.6,
                1.7,
                boxstyle="round,pad=0.04",
                facecolor=color,
                edgecolor=DARK,
                linewidth=0.7,
            )
        )
        ax.text(cx + cw / 2, ly + 0.85, label, ha="center", va="center", fontsize=7.3)
        ly -= 1.9

    _pipeline_box(
        ax,
        22.1,
        y0,
        3.6,
        h,
        "Provenance\ngating",
        "doctor --strict\ntaint tracking\nimmutable archives",
        "#F0EAF4",
    )
    _pipeline_box(
        ax,
        26.6,
        y0,
        3.2,
        h,
        "Analysis",
        "H1–H6: ICC,\npaired tests,\nSpearman, blast",
        "#E5F3EE",
    )

    mid = y0 + h / 2
    _pipeline_arrow(ax, 5.2, 6.0, mid)
    _pipeline_arrow(ax, 10.8, 11.6, mid)
    _pipeline_arrow(ax, 21.2, 22.0, mid)
    _pipeline_arrow(ax, 25.8, 26.5, mid)
    ax.text(
        15.1,
        0.3,
        "per iteration: 60 s settle → 120 s chaos → 60 s post; "
        "each (strategy × fault × iteration) cell archived with scenario hashes",
        ha="center",
        va="center",
        fontsize=7.8,
        style="italic",
        color="#555555",
    )
    return _save(fig, out_dir, "fig-01-workflow.png")


# ── figure 2: experiment matrix ──────────────────────────────────────────────

_CLASS_COLOR = {"churn": "#56B4E9", "load contention": "#E69F00", "node drain": "#009E73"}


def fig02_core_matrix(counts: Mapping[Tuple[str, str], int], out_dir: str) -> str:
    """Strategies x fault classes, shaded by archived-run coverage."""
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    ax.grid(False)
    n_rows, n_cols = len(STRATEGY_ORDER), len(MATRIX_CLASSES)
    max_n = max(counts.values(), default=1)
    for i, sname in enumerate(STRATEGY_ORDER):
        for j, cls in enumerate(MATRIX_CLASSES):
            n = counts.get((sname, cls), 0)
            base = _CLASS_COLOR[cls]
            alpha = 0.15 + 0.75 * (n / max_n) if n else 0.0
            face = base if n else "#FFFFFF"
            ax.add_patch(
                Rectangle(
                    (j, n_rows - 1 - i),
                    1,
                    1,
                    facecolor=face,
                    alpha=alpha if n else 1.0,
                    edgecolor="#CCCCCC",
                    linewidth=0.8,
                )
            )
            label = f"n={n}" if n else "—"
            ax.text(
                j + 0.5,
                n_rows - 1 - i + 0.5,
                label,
                ha="center",
                va="center",
                fontsize=9,
                color=DARK if n else "#AAAAAA",
            )
    # Emphasize the paired H2/H6 contrast rows (colocate, spread).
    for sname in ("colocate", "spread"):
        i = STRATEGY_ORDER.index(sname)
        ax.add_patch(
            Rectangle(
                (0, n_rows - 1 - i),
                n_cols,
                1,
                facecolor="none",
                edgecolor=DARK,
                linewidth=1.6,
                zorder=4,
            )
        )
    ax.set_xlim(0, n_cols)
    ax.set_ylim(0, n_rows)
    ax.set_xticks([j + 0.5 for j in range(n_cols)])
    ax.set_xticklabels(
        [f"{cls}\n→ {MATRIX_HYPOTHESES[cls]}" for cls in MATRIX_CLASSES], fontsize=8.2
    )
    ax.set_yticks([n_rows - 1 - i + 0.5 for i in range(n_rows)])
    ax.set_yticklabels(
        [s + (" (control)" if s == "baseline" else "") for s in STRATEGY_ORDER], fontsize=9
    )
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(
        "Experiment matrix: placement strategies × fault classes\n"
        "(cell = archived runs covering it; outlined rows = the paired colocate/spread contrast)"
    )
    fig.tight_layout()
    return _save(fig, out_dir, "fig-02-core-matrix.png")


# ── figure 3: H1 score distributions ─────────────────────────────────────────


def fig03_score_distributions(sessions: Sequence[SessionData], icc: IccPoint, out_dir: str) -> str:
    """Per-strategy aggregate-score distributions across the campaign sessions."""
    pooled: Dict[str, List[float]] = {}
    for sess in sessions:
        for sname, vals in sess.scores.items():
            pooled.setdefault(sname, []).extend(vals)
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    strats = _box_strip(ax, pooled, STRATEGY_ORDER)
    if "baseline" in strats:
        ax.text(
            strats.index("baseline") + 1,
            ax.get_ylim()[0] + 2,
            "control\n(excluded from ICC)",
            ha="center",
            fontsize=7,
            color="#666666",
        )
    ax.set_ylabel("Aggregate resilience score (per iteration)")
    n_obs = sum(len(v) for s, v in pooled.items() if s != "baseline")
    ax.set_title(
        f"H1 — aggregate score by strategy, {len(sessions)} churn sessions "
        f"(n={n_obs} iterations)"
    )
    if icc.icc is not None and icc.ci_low is not None and icc.ci_high is not None:
        note = (
            f"ICC$_{{strategy}}$ = {icc.icc:.3f}   "
            f"95% CI [{icc.ci_low:.3f}, {icc.ci_high:.3f}] (cluster bootstrap)\n"
            "strategy explains almost none of the score variance"
        )
        ax.text(
            0.985,
            0.04,
            note,
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            fontsize=8.5,
            bbox={"boxstyle": "round,pad=0.35", "facecolor": "#F4F4F4", "edgecolor": "#BBBBBB"},
        )
    fig.tight_layout()
    return _save(fig, out_dir, "fig-03-h1-score-distributions.png")


# ── figure 4: H1 ICC trajectory ──────────────────────────────────────────────


def fig04_icc_trajectory(points: Sequence[IccPoint], out_dir: str) -> str:
    """ICC point + CI as campaign sessions accumulate."""
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ks = [p.n_sessions for p in points]
    ys = [p.icc if p.icc is not None else math.nan for p in points]
    lo = [(p.icc - p.ci_low) if p.icc is not None and p.ci_low is not None else 0.0 for p in points]
    hi = [
        (p.ci_high - p.icc) if p.icc is not None and p.ci_high is not None else 0.0 for p in points
    ]
    ax.errorbar(
        ks,
        ys,
        yerr=[lo, hi],
        fmt="o-",
        color=ACCENT,
        ecolor="#9bbbd4",
        elinewidth=2.5,
        capsize=4,
        markersize=5,
        linewidth=1.4,
    )
    last = points[-1]
    if last.icc is not None and last.ci_low is not None and last.ci_high is not None:
        ax.annotate(
            f"ICC = {last.icc:.3f}\nCI [{last.ci_low:.3f}, {last.ci_high:.3f}]",
            xy=(last.n_sessions, last.icc),
            xytext=(last.n_sessions - 1.9, last.icc + 0.25),
            fontsize=8.5,
            arrowprops={"arrowstyle": "->", "color": "#555555", "lw": 0.9},
        )
    ax.set_xticks(ks)
    ax.set_xlabel("Campaign sessions accumulated (s01 → s0k)")
    ax.set_ylabel("ICC$_{strategy}$ (share of score variance)")
    ax.set_ylim(0, 1.0)
    ax.set_title(
        "H1 — the score's strategy signal collapses once\nrun-to-run variance becomes visible"
    )
    ax.text(
        0.985,
        0.97,
        "k=1: run-to-run variance is structurally 0,\nso the ICC is inflated by design",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=7.8,
        style="italic",
        color="#555555",
    )
    fig.tight_layout()
    return _save(fig, out_dir, "fig-04-h1-icc-trajectory.png")


# ── figure 5: H2 conntrack flush ─────────────────────────────────────────────


def fig05_conntrack(stats: FlushStats, out_dir: str) -> str:
    """Flush %% per strategy + the paired spread-vs-colocate slopegraph."""
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(9.2, 4.2), gridspec_kw={"width_ratios": [2.4, 1.0]}
    )
    order = [s for s in STRATEGY_ORDER if s != "baseline"]
    _box_strip(ax1, stats.per_strategy, order)
    ax1.axhline(0, color="#888888", linewidth=0.8, linestyle="--")
    ax1.set_ylabel("Conntrack entries flushed during chaos (%)")
    med_sp, med_co = stats.median("spread"), stats.median("colocate")
    title = "H2 — conntrack flush by strategy across sessions"
    if med_sp is not None and med_co is not None:
        title += f"\n(medians: spread {med_sp:.1f}%, colocate {med_co:.1f}%)"
    ax1.set_title(title)

    for _, sp, co in stats.pairs:
        ax2.plot(
            [0, 1],
            [co, sp],
            "-o",
            color="#777777",
            markerfacecolor="white",
            markersize=4,
            linewidth=1.0,
            alpha=0.8,
        )
    ax2.set_xlim(-0.45, 1.45)
    ax2.set_xticks([0, 1])
    ax2.set_xticklabels(["colocate", "spread"])
    for label, sname in ((0, "colocate"), (1, "spread")):
        ax2.get_xticklabels()[label].set_color(PALETTE[sname])
        ax2.get_xticklabels()[label].set_fontweight("bold")
    ax2.set_title(
        f"paired by session:\nspread > colocate in {stats.wins}/{len(stats.pairs)}\n"
        f"sign {_fmt_p(stats.sign_p)}, Wilcoxon {_fmt_p(stats.wilcoxon_p)}",
        fontsize=9,
    )
    fig.tight_layout()
    return _save(fig, out_dir, "fig-05-h2-conntrack.png")


# ── figure 6: H3 confound signature ──────────────────────────────────────────


def fig06_h3_scatter(dep: ScatterStats, ctrl: ScatterStats, out_dir: str) -> str:
    """Flush %% vs dependent- and control-route p95, side by side."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.8, 4.0), sharey=True)
    panels = (
        (ax1, dep, "dependent routes (touch the chaos target)", "#D55E00"),
        (ax2, ctrl, "control routes (independent of the target)", "#0072B2"),
    )
    for ax, stats_, label, color in panels:
        xs = [a for a, _ in stats_.pairs]
        ys = [b for _, b in stats_.pairs]
        ax.scatter(xs, ys, s=26, color=color, alpha=0.75, edgecolor=DARK, linewidth=0.4)
        star = "*" if stats_.p < 0.05 else ""
        ax.set_title(
            f"{label}\nSpearman ρ = {stats_.rho:.2f}{star} ({_fmt_p(stats_.p)}, " f"n={stats_.n})",
            fontsize=9.3,
        )
        ax.set_xlabel("Conntrack flush during chaos (%)")
    ax1.set_ylabel("Worst during-chaos route p95 (ms)")
    sig = "control" if ctrl.p < 0.05 <= dep.p else "neither/both"
    fig.suptitle(
        "H3 — the reproducible mechanism does not predict the fault-dependent tail; "
        f"only the {sig} side correlates (a run-level confound signature)",
        fontsize=10,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    return _save(fig, out_dir, "fig-06-h3-scatter.png")


# ── figure 7: H5 fraction vs tail ────────────────────────────────────────────


def cluster_by_fraction(points: Sequence[H5Point], gap: float = 0.12) -> List[List[H5Point]]:
    """Group points into fraction clusters (split where the gap exceeds ``gap``).

    The spreading strategies tie near the graph's intrinsic cross-node fraction,
    so their markers crowd together; the renderer stacks each crowded cluster's
    labels beside the cluster with leader lines instead of overprinting them.
    """
    ordered = sorted(points, key=lambda p: p.fraction)
    clusters: List[List[H5Point]] = []
    for pt in ordered:
        if clusters and pt.fraction - clusters[-1][-1].fraction <= gap:
            clusters[-1].append(pt)
        else:
            clusters.append([pt])
    return clusters


def fig07_fraction_vs_tail(points: Sequence[H5Point], out_dir: str) -> str:
    """Cross-node fraction vs during-load east-west p95, all strategies labeled."""
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    for pt in points:
        marker = "s" if pt.node_local else "o"
        ax.scatter(
            pt.fraction,
            pt.ew_p95,
            s=70,
            marker=marker,
            color=PALETTE.get(pt.strategy, "#888888"),
            edgecolor=DARK,
            linewidth=0.7,
            zorder=3,
        )
    ys_all = [p.ew_p95 for p in points]
    step = 0.085 * (max(ys_all) - min(ys_all)) if len(ys_all) > 1 else 1.0
    for cluster in cluster_by_fraction(points):
        ranked = sorted(cluster, key=lambda p: (p.ew_p95, p.strategy))
        if len(ranked) <= 2:
            for k, pt in enumerate(ranked):
                ax.annotate(
                    pt.strategy,
                    xy=(pt.fraction, pt.ew_p95),
                    xytext=(7, -4 + 11 * k),
                    textcoords="offset points",
                    fontsize=8.3,
                    ha="left",
                    va="center",
                )
            continue
        # Crowded cluster: stack the labels beside it, leader lines to points.
        x_label = min(p.fraction for p in ranked) - 0.045
        center = st.mean(p.ew_p95 for p in ranked)
        y0 = center - step * (len(ranked) - 1) / 2
        overshoot = (y0 + step * (len(ranked) - 1)) - (max(ys_all) + 0.5 * step)
        y0 -= max(0.0, overshoot)
        for k, pt in enumerate(ranked):
            ax.annotate(
                pt.strategy,
                xy=(pt.fraction, pt.ew_p95),
                xytext=(x_label, y0 + k * step),
                textcoords="data",
                fontsize=8.3,
                ha="right",
                va="center",
                arrowprops={"arrowstyle": "-", "color": "#AAAAAA", "lw": 0.6, "shrinkB": 4},
            )
    rho, p, n = h5_spearman(points)
    locals_ = sorted(pt.strategy for pt in points if pt.node_local)
    if locals_:
        lx = [pt.fraction for pt in points if pt.node_local]
        ly = [pt.ew_p95 for pt in points if pt.node_local]
        ax.annotate(
            "node-local placements\n(" + ", ".join(locals_) + ")",
            xy=(max(lx), max(ly)),
            xytext=(max(lx) + 0.08, max(ly) + 3.2),
            fontsize=8,
            style="italic",
            color="#555555",
            arrowprops={"arrowstyle": "-", "color": "#999999", "lw": 0.8},
        )
    ax.set_xlabel("Cross-node call fraction (realised placement)")
    ax.set_ylabel("During-load east-west median p95 (ms)")
    ax.set_title(
        f"H5 — co-location removes the east-west tail penalty under load\n"
        f"(Spearman ρ = {rho:.2f}, {_fmt_p(p)}, n={n} strategies; "
        "square markers = node-local)"
    )
    fig.tight_layout()
    return _save(fig, out_dir, "fig-07-h5-fraction-vs-tail.png")


# ── figure 8: H6 trough timeline ─────────────────────────────────────────────


def fig08_trough_timeline(
    trajectories: Sequence[ReadyTrajectory],
    blast: Mapping[str, H6Blast],
    out_dir: str,
) -> str:
    """EndpointSlice total-ready through the drain, colocate vs spread, per run."""
    runs = sorted({t.run for t in trajectories})
    fig, axes = plt.subplots(
        1, max(len(runs), 1), figsize=(4.4 * max(len(runs), 1), 4.0), sharey=True, squeeze=False
    )
    phase_labels = {"preChaos": "pre", "duringChaos": "during drain", "postChaos": "post"}
    for ax, run in zip(axes[0], runs):
        run_trajs = [t for t in trajectories if t.run == run]
        top = max((max(t.ready) for t in run_trajs), default=0)
        for traj in run_trajs:
            xs = list(range(len(traj.phases)))
            color = PALETTE.get(traj.strategy, "#888888")
            ax.plot(
                xs,
                traj.ready,
                "-o",
                color=color,
                linewidth=1.6,
                markersize=6,
                markeredgecolor=DARK,
                markeredgewidth=0.5,
                label=traj.strategy,
            )
            if "duringChaos" in traj.phases:
                i = traj.phases.index("duringChaos")
                b = blast.get(traj.strategy)
                detail = (
                    f" · {b.per_run[run]}/{b.measured} pinned down"
                    if b is not None and run in b.per_run
                    else ""
                )
                # Drop the label below the marker when it sits near the top of
                # the panel, so it never collides with the title.
                near_top = traj.ready[i] >= 0.85 * top
                ax.annotate(
                    f"{traj.ready[i]}/{traj.n_services} ready{detail}",
                    xy=(i, traj.ready[i]),
                    xytext=(8, -13 if near_top else 7),
                    textcoords="offset points",
                    fontsize=7.4,
                    color=color,
                )
        # Snapshot cadence, computed from the capture timestamps.
        during_off = [
            t.minutes[t.phases.index("duringChaos")] for t in run_trajs if "duringChaos" in t.phases
        ]
        post_off = [
            t.minutes[t.phases.index("postChaos")] for t in run_trajs if "postChaos" in t.phases
        ]
        if during_off and post_off:
            ax.set_xlabel(
                f"snapshots: drain +{min(during_off):.1f}–{max(during_off):.1f} min, "
                f"post +{min(post_off):.1f}–{max(post_off):.1f} min",
                fontsize=7.8,
                color="#666666",
            )
        ax.set_xticks(range(3))
        ax.set_xticklabels([phase_labels[p] for p in _ES_PHASES])
        ax.set_xlim(-0.25, 2.6)
        ax.set_title(f"run {run}", fontsize=9.5)
        ax.set_ylim(bottom=0)
    axes[0][0].set_ylabel("Total ready EndpointSlice endpoints")
    axes[0][0].legend(loc="center left")
    fig.suptitle(
        "H6 — the drain trough: colocate concentrates the outage, spread contains it",
        fontsize=10.5,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    return _save(fig, out_dir, "fig-08-h6-trough-timeline.png")


# ── figure 9: the capstone trade-off ─────────────────────────────────────────


def fig09_tradeoff(h5: Sequence[H5Point], blast: Mapping[str, H6Blast], out_dir: str) -> str:
    """Same placements on two axes: during-load tail (x) vs drain blast (y)."""
    fig, ax = plt.subplots(figsize=(7.0, 4.8))
    both = [pt for pt in h5 if pt.strategy in blast]
    pending = [pt for pt in h5 if pt.strategy not in blast and pt.strategy != "baseline"]
    for pt in both:
        b = blast[pt.strategy]
        ax.scatter(
            pt.ew_p95,
            b.blast,
            s=110,
            marker="s" if pt.node_local else "o",
            color=PALETTE.get(pt.strategy, "#888888"),
            edgecolor=DARK,
            linewidth=0.8,
            zorder=4,
        )
        ax.annotate(
            f"{pt.strategy}\n({b.blast}/{b.measured} services down)",
            xy=(pt.ew_p95, b.blast),
            xytext=(10, -4),
            textcoords="offset points",
            fontsize=8.5,
        )
    if len(both) >= 2:
        xs = [pt.ew_p95 for pt in both]
        ys = [blast[pt.strategy].blast for pt in both]
        ax.plot(xs, ys, "--", color="#AAAAAA", linewidth=1.0, zorder=2)
    # Strategies measured under load but not yet under drain: rug marks.
    for pt in pending:
        ax.plot(pt.ew_p95, 0, marker="|", color=PALETTE.get(pt.strategy, "#888888"), markersize=12)
    if pending:
        names = ", ".join(sorted(pt.strategy for pt in pending))
        ax.text(
            0.5,
            0.105,
            f"rug marks: load-measured strategies awaiting drain data ({names})",
            transform=ax.transAxes,
            ha="center",
            fontsize=7.6,
            style="italic",
            color="#555555",
        )
    ax.set_xlabel("During-load east-west median p95, ms — H5 run (← better latency)")
    ax.set_ylabel("Node-drain blast radius, services at 0 ready —\nH6 runs (↓ better availability)")
    ax.set_title(
        "Co-location's two faces: the placement that minimises the latency tail\n"
        "maximises the node-failure blast radius"
    )
    ax.set_ylim(bottom=-0.6)
    fig.tight_layout()
    return _save(fig, out_dir, "fig-09-tradeoff.png")


# ── orchestration ────────────────────────────────────────────────────────────

ALL_FIGURES: Tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8, 9)


def parse_figures(spec: str) -> List[int]:
    """'all' or a comma-separated subset of 1..9."""
    if spec.strip().lower() == "all":
        return list(ALL_FIGURES)
    figures = sorted({int(tok) for tok in spec.split(",") if tok.strip()})
    bad = [f for f in figures if f not in ALL_FIGURES]
    if bad:
        raise ValueError(f"unknown figure number(s): {bad} (valid: 1-9 or 'all')")
    return figures


def generate(  # noqa: C901 - a linear per-figure dispatch table
    out_dir: str,
    figures: Sequence[int],
    campaign_dir: str = DEFAULT_CAMPAIGN_DIR,
    h4_runs: Sequence[str] = DEFAULT_H4_RUNS,
    h5_run: str = DEFAULT_H5_RUN,
    h6_runs: Sequence[str] = DEFAULT_H6_RUNS,
    gradient_run: Optional[str] = None,
    n_resamples: int = 2000,
) -> List[str]:
    """Load only what the requested figures need, then render them."""
    apply_thesis_style()
    todo = set(figures)
    written: List[str] = []

    drain_runs = list(h6_runs) + ([gradient_run] if gradient_run else [])

    sessions: List[SessionData] = []
    matrix_named: List[Tuple[str, Dict[str, Any]]] = []
    if todo & {2, 3, 4, 5}:
        for name, path in campaign_session_paths(campaign_dir):
            summary = load_summary(path)
            sessions.append(extract_session(name, summary))
            if 2 in todo:
                matrix_named.append((name, {"faults": _presence_as_faults(summary)}))
            del summary

    h5_summary: Optional[Dict[str, Any]] = None
    if todo & {2, 7, 9}:
        h5_summary = load_summary(os.path.join(h5_run, "summary.json"))
        if 2 in todo:
            matrix_named.append(
                (os.path.basename(h5_run), {"faults": _presence_as_faults(h5_summary)})
            )

    h6_named: List[Tuple[str, Dict[str, Any]]] = []
    if todo & {2, 8, 9}:
        for run_dir in drain_runs:
            summary = load_summary(os.path.join(run_dir, "summary.json"))
            h6_named.append((os.path.basename(run_dir), summary))
        if 2 in todo:
            matrix_named.extend((run, {"faults": _presence_as_faults(s)}) for run, s in h6_named)

    if 2 in todo:
        for run_dir in h4_runs:
            summary = load_summary(os.path.join(run_dir, "summary.json"))
            matrix_named.append(
                (os.path.basename(run_dir), {"faults": _presence_as_faults(summary)})
            )
            del summary

    if 1 in todo:
        written.append(fig01_workflow(out_dir))
    if 2 in todo:
        written.append(fig02_core_matrix(matrix_counts(matrix_named), out_dir))
    if 3 in todo:
        written.append(
            fig03_score_distributions(sessions, icc_point(sessions, n_resamples), out_dir)
        )
    if 4 in todo:
        written.append(fig04_icc_trajectory(icc_trajectory(sessions, n_resamples), out_dir))
    if 5 in todo:
        written.append(fig05_conntrack(flush_stats(sessions), out_dir))
    if 6 in todo:
        rows = h3.collect(campaign_dir)
        written.append(
            fig06_h3_scatter(h3_scatter(rows, "dep_p95"), h3_scatter(rows, "ctrl_p95"), out_dir)
        )
    if 7 in todo and h5_summary is not None:
        written.append(fig07_fraction_vs_tail(h5_points(h5_summary), out_dir))
    if todo & {8, 9}:
        blast = h6_blast(h6_named)
        if 8 in todo:
            trajectories = endpoint_trajectories(h6_named, ("colocate", "spread"))
            written.append(fig08_trough_timeline(trajectories, blast, out_dir))
        if 9 in todo and h5_summary is not None:
            written.append(fig09_tradeoff(h5_points(h5_summary), blast, out_dir))
    return written


def _presence_as_faults(summary: Mapping[str, Any]) -> Dict[str, Any]:
    """Reduce a summary to the fault->strategies skeleton matrix_counts reads."""
    return {
        fault_name: {"strategies": {sname: {} for sname in strategies}}
        for fault_name, strategies in fault_presence(summary).items()
    }


def main() -> None:  # pragma: no cover - CLI glue over the tested functions
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out-dir", required=True, help="directory to write the PNGs into")
    ap.add_argument("--figure", default="all", help="'all' or comma-separated subset of 1..9")
    ap.add_argument("--campaign-dir", default=DEFAULT_CAMPAIGN_DIR)
    ap.add_argument(
        "--h4-run", action="append", default=None, help="H4 load batch run dir (repeatable)"
    )
    ap.add_argument("--h5-run", default=DEFAULT_H5_RUN, help="H5 8-strategy load run dir")
    ap.add_argument(
        "--h6-run", action="append", default=None, help="H6 node-drain run dir (repeatable)"
    )
    ap.add_argument(
        "--gradient-run",
        default=None,
        help="optional extra node-drain gradient run dir (include only if doctor-clean)",
    )
    ap.add_argument("--bootstrap-resamples", type=int, default=2000)
    args = ap.parse_args()
    written = generate(
        out_dir=args.out_dir,
        figures=parse_figures(args.figure),
        campaign_dir=args.campaign_dir,
        h4_runs=tuple(args.h4_run) if args.h4_run else DEFAULT_H4_RUNS,
        h5_run=args.h5_run,
        h6_runs=tuple(args.h6_run) if args.h6_run else DEFAULT_H6_RUNS,
        gradient_run=args.gradient_run,
        n_resamples=args.bootstrap_resamples,
    )
    for path in written:
        print("wrote", path)


if __name__ == "__main__":  # pragma: no cover
    main()
