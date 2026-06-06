#!/usr/bin/env python3
"""Cross-run distribution charts that back the thesis's central claims.

The per-run charts embedded in the deck are single-run snapshots, which read as
a stable strategy ranking — the exact horse-race the thesis argues *against*.
These charts instead show the *distribution* of each metric per strategy across
every churn (pod-delete) run, which is what M4, M1 and M2 are actually about:

* ``score_distribution.png``      — resilience score per strategy across runs.
  The boxes overlap heavily and span ~33-100, so the score cannot rank
  placements (M4). The baseline control is shown for reference (always 100).
* ``mechanism_distribution.png``  — conntrack flush % (M1) and during-chaos CPU
  throttle rate (M2) per strategy across runs. Here the boxes separate cleanly
  and reproducibly: spread/default flush conntrack while colocate stays flat,
  and colocate throttles below default/spread.

Metric definitions match ``mechanism_metrics.py`` (the committed numeric check).

Usage
-----
    uv run python scripts/distribution_charts.py [--results-dir results] \
        [--out-dir results/<run>/charts]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Deck strategy order / palette (baseline first as the control).
STRATEGY_ORDER = [
    "baseline",
    "default",
    "colocate",
    "spread",
    "random",
    "adversarial",
    "best-fit",
    "dependency-aware",
]
PALETTE = {
    "baseline": "#9aa0a6",
    "default": "#4FC3F7",
    "colocate": "#EF5350",
    "spread": "#66BB6A",
    "random": "#FFA726",
    "adversarial": "#AB47BC",
    "best-fit": "#26C6DA",
    "dependency-aware": "#9CCC65",
}


def _phase_mean(strat: dict, metric: str, phase: str) -> Optional[float]:
    phases = ((strat.get("metrics") or {}).get("prometheus") or {}).get("phases") or {}
    entry = ((phases.get(phase) or {}).get("metrics") or {}).get(metric)
    return entry.get("mean") if isinstance(entry, dict) else None


def _is_churn(fault_name: str) -> bool:
    return "cpuhog" not in fault_name and fault_name != "pod-cpu-hog"


def collect(results_dir: str) -> dict:
    """Per-strategy lists of score, conntrack flush %, and throttle rate."""
    scores: dict[str, list[float]] = {}
    flush: dict[str, list[float]] = {}
    throttle: dict[str, list[float]] = {}
    for path in sorted(glob.glob(os.path.join(results_dir, "*", "summary.json"))):
        with open(path) as fh:
            summary = json.load(fh)
        for fault_name, fault in summary.get("faults", {}).items():
            if not _is_churn(fault_name):
                continue
            for name, strat in fault.get("strategies", {}).items():
                exp = strat.get("experiment") or strat.get("aggregated") or {}
                score = exp.get("meanResilienceScore")
                if score is not None:
                    scores.setdefault(name, []).append(score)
                if name == "baseline":
                    continue
                c_pre = _phase_mean(strat, "conntrack_entries_per_node", "pre-chaos")
                c_dur = _phase_mean(strat, "conntrack_entries_per_node", "during-chaos")
                if c_pre and c_dur is not None and c_pre > 0:
                    flush.setdefault(name, []).append((c_pre - c_dur) / c_pre * 100)
                t_dur = _phase_mean(strat, "cpu_throttling", "during-chaos")
                if t_dur is not None:
                    throttle.setdefault(name, []).append(t_dur)
    return {"scores": scores, "flush": flush, "throttle": throttle}


def _ordered(data: dict[str, list[float]]) -> list[str]:
    return [s for s in STRATEGY_ORDER if data.get(s)]


def _box(ax, data: dict[str, list[float]], ylabel: str, title: str) -> None:
    strats = _ordered(data)
    series = [data[s] for s in strats]
    bp = ax.boxplot(
        series,
        patch_artist=True,
        widths=0.6,
        showmeans=True,
        meanprops={
            "marker": "D",
            "markerfacecolor": "white",
            "markeredgecolor": "black",
            "markersize": 5,
        },
    )
    for patch, s in zip(bp["boxes"], strats):
        patch.set_facecolor(PALETTE.get(s, "#888888"))
        patch.set_alpha(0.75)
    for median in bp["medians"]:
        median.set_color("black")
    # Jittered individual run points so n and spread are visible.
    for i, s in enumerate(strats, start=1):
        ys = data[s]
        xs = [i + (j - (len(ys) - 1) / 2) * 0.04 for j in range(len(ys))]
        ax.scatter(xs, ys, s=12, color="black", alpha=0.5, zorder=3)
    ax.set_xticks(range(1, len(strats) + 1))
    ax.set_xticklabels(strats, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=12)
    ax.grid(axis="y", alpha=0.3)


def render(data: dict, out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    written = []

    # --- M4: score distribution ---
    fig, ax = plt.subplots(figsize=(10, 6))
    _box(
        ax,
        data["scores"],
        "Resilience Score (%)",
        "Resilience Score Distribution Across Runs — the score does not reproduce (M4)",
    )
    ax.set_ylim(0, 105)
    fig.tight_layout()
    p = os.path.join(out_dir, "score_distribution.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    written.append(p)

    # --- M1 / M2: mechanism distributions ---
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 6))
    _box(
        ax1,
        data["flush"],
        "Conntrack entries flushed (%)",
        "M1 — conntrack churn (spread/default flush, colocate flat)",
    )
    ax1.axhline(0, color="gray", linewidth=0.8, linestyle="--")
    _box(
        ax2,
        data["throttle"],
        "During-chaos throttle rate",
        "M2 — CPU throttling (colocate below default/spread)",
    )
    fig.suptitle("Mechanism metrics reproduce across runs, where the score does not", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    p = os.path.join(out_dir, "mechanism_distribution.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    written.append(p)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--results-dir", default="results")
    parser.add_argument(
        "--out-dir",
        default="results/_thesis_charts",
        help="where to write the PNGs (default results/_thesis_charts)",
    )
    args = parser.parse_args()
    data = collect(args.results_dir)
    for s in STRATEGY_ORDER:
        n = len(data["scores"].get(s, []))
        print(
            f"  {s:<16} score n={n}  flush n={len(data['flush'].get(s, []))}  "
            f"throttle n={len(data['throttle'].get(s, []))}"
        )
    for p in render(data, args.out_dir):
        print("wrote", p)


if __name__ == "__main__":
    main()
