#!/usr/bin/env python3
"""Reconstruct the thesis's mechanism-layer metrics (M1, M2) from run outputs.

The aggregate resilience score is too noisy to rank placements (M4), so the
thesis rests on two *mechanism* metrics that reproduce across runs. This script
recomputes both directly from the per-strategy Prometheus phase aggregates stored
in ``results/<run>/summary.json``, so the headline numbers in the slide deck are
reproducible from a committed artifact rather than an ad-hoc notebook.

Granularity
-----------
Each ``summary.json`` stores one Prometheus phase capture per *strategy per run*
at ``faults.<fault>.strategies.<strategy>.metrics.prometheus.phases`` (pre-chaos /
during-chaos / post-chaos), each phase carrying ``mean/max/min/stdev`` per metric.
There is no per-iteration breakdown of these series, so reproducibility is counted
across *runs* (one data point per strategy per run), which is exactly the
denominator the deck cites ("12/12", "11/13").

M1 — conntrack flush (``conntrack_entries_per_node``)
    flush% = (pre_mean - during_mean) / pre_mean * 100   (positive = entries flushed)
    Claim: spread/default flush a large fraction during the kill cycle; colocate
    stays ~flat. Reproducibility is reported as "spread flush > colocate flush".

M2 — CPU throttling (``cpu_throttling`` = rate of container_cpu_cfs_throttled_seconds_total)
    metric = during-chaos mean throttle rate (NOT a ratio — the deck's "1.54 /
    1.90 / 1.94" are absolute during-chaos rates). "colocate throttles least" is
    reported against the {colocate, default, spread} comparison set, which is the
    only set present in every run (the 4-strategy early runs lack the other four).

Only churn (pod-delete) runs are included; cpu-hog faults are excluded (they are
the S1 contention control, analysed separately).

Usage
-----
    uv run python scripts/mechanism_metrics.py [--results-dir results]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics as st
from typing import Optional

from fault_taxonomy import is_churn

from chaosprobe.metrics.statistics import wilcoxon_signed_rank

COMPARISON_SET = ("colocate", "default", "spread")


def _phase_mean(strategy: dict, metric: str, phase: str) -> Optional[float]:
    """Mean of ``metric`` in ``phase`` for one strategy, or None if absent."""
    phases = ((strategy.get("metrics") or {}).get("prometheus") or {}).get("phases") or {}
    entry = ((phases.get(phase) or {}).get("metrics") or {}).get(metric)
    return entry.get("mean") if isinstance(entry, dict) else None


def collect(results_dir: str) -> dict:
    """Walk every summary.json and gather per-run, per-strategy mechanism metrics."""
    flush: dict[str, list[float]] = {}  # strategy -> [flush% per run]
    throttle: dict[str, list[float]] = {}  # strategy -> [during-chaos rate per run]
    m1_runs: list[tuple[str, float, float]] = []  # (run, spread_flush, colocate_flush)
    m2_runs: list[tuple[str, dict[str, float]]] = []  # (run, {strategy: throttle})

    for path in sorted(glob.glob(os.path.join(results_dir, "*", "summary.json"))):
        run = os.path.basename(os.path.dirname(path))
        with open(path) as fh:
            summary = json.load(fh)
        for fault_name, fault in summary.get("faults", {}).items():
            if not is_churn(fault_name):
                continue
            run_flush: dict[str, float] = {}
            run_throttle: dict[str, float] = {}
            for strat_name, strat in fault.get("strategies", {}).items():
                if strat_name == "baseline":
                    continue
                c_pre = _phase_mean(strat, "conntrack_entries_per_node", "pre-chaos")
                c_dur = _phase_mean(strat, "conntrack_entries_per_node", "during-chaos")
                if c_pre and c_dur is not None and c_pre > 0:
                    pct = (c_pre - c_dur) / c_pre * 100
                    flush.setdefault(strat_name, []).append(pct)
                    run_flush[strat_name] = pct
                t_dur = _phase_mean(strat, "cpu_throttling", "during-chaos")
                if t_dur is not None:
                    throttle.setdefault(strat_name, []).append(t_dur)
                    run_throttle[strat_name] = t_dur
            if "spread" in run_flush and "colocate" in run_flush:
                m1_runs.append((run, run_flush["spread"], run_flush["colocate"]))
            if run_throttle:
                m2_runs.append((run, run_throttle))
    return {"flush": flush, "throttle": throttle, "m1_runs": m1_runs, "m2_runs": m2_runs}


def _fmt_table(title: str, data: dict[str, list[float]], digits: int) -> None:
    print(f"\n=== {title} ===")
    for strat in sorted(data):
        vals = data[strat]
        med = round(st.median(vals), digits)
        print(
            f"  {strat:<16} n={len(vals):<3} median={med:<7} "
            f"range=[{round(min(vals), digits)}, {round(max(vals), digits)}]"
        )


def report(data: dict) -> None:
    _fmt_table("M1  conntrack flush % (median across churn runs)", data["flush"], 1)
    m1 = data["m1_runs"]
    wins = sum(1 for _, sp, co in m1 if sp > co)
    print(f"\n  M1 reproducibility: spread flush > colocate flush in {wins} / {len(m1)} runs")
    if m1:
        # Paired by run: spread vs colocate flush under identical cluster state.
        # The Wilcoxon p tests the magnitude difference; the sign test gives the
        # exact "k/k runs" probability the deck's count is really making.
        w = wilcoxon_signed_rank([sp for _, sp, _ in m1], [co for _, _, co in m1])
        sgn = w["sign_test"]
        print(
            f"  M1 paired test: Wilcoxon W={w['w_statistic']} p={w['p_two_sided']}; "
            f"sign test {sgn['n_pos']}/{sgn['n']} p={sgn['p_two_sided']}"
        )

    _fmt_table("M2  during-chaos CPU throttle rate (median across churn runs)", data["throttle"], 2)
    m2 = data["m2_runs"]
    # "colocate throttles least" against the {colocate, default, spread} set.
    cmp_runs = [(r, t) for r, t in m2 if set(COMPARISON_SET) <= set(t)]
    least = sum(1 for _, t in cmp_runs if t["colocate"] == min(t[s] for s in COMPARISON_SET))
    lt_default = sum(1 for _, t in cmp_runs if t["colocate"] < t["default"])
    print(
        f"\n  M2 reproducibility (vs {{colocate, default, spread}}): "
        f"colocate lowest in {least} / {len(cmp_runs)} runs; "
        f"colocate < default in {lt_default} / {len(cmp_runs)} runs"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--results-dir", default="results", help="directory of <run>/summary.json outputs"
    )
    args = parser.parse_args()
    report(collect(args.results_dir))


if __name__ == "__main__":
    main()
