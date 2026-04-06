"""Matplotlib chart generators for ChaosProbe experiment results.

All ``_chart_*`` and ``_extract_*`` helpers live here so that
``visualize.py`` stays focused on orchestration and HTML generation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def strategy_colors(names: List[str]) -> List[str]:
    """Return consistent colors for strategy names."""
    color_map = {
        "baseline": "#2196F3",
        "colocate": "#F44336",
        "spread": "#4CAF50",
        "random": "#FF9800",
        "antagonistic": "#9C27B0",
    }
    default_colors = ["#607D8B", "#795548", "#009688", "#CDDC39", "#FF5722"]
    colors = []
    idx = 0
    for name in names:
        if name in color_map:
            colors.append(color_map[name])
        else:
            colors.append(default_colors[idx % len(default_colors)])
            idx += 1
    return colors


# Keep the old private name as an alias for backwards-compatible imports.
_strategy_colors = strategy_colors


# ---------------------------------------------------------------------------
# Core charts (resilience + recovery)
# ---------------------------------------------------------------------------

def chart_resilience_scores(
    strategies: Dict[str, Any],
    output_path: Path,
    iteration_data: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Generate resilience score bar chart with per-iteration data points."""
    names = list(strategies.keys())
    scores = [strategies[n].get("avgResilienceScore", 0) for n in names]

    if not any(s > 0 for s in scores):
        return None

    fig, ax = plt.subplots(figsize=(10, 6))
    colors = strategy_colors(names)
    bars = ax.bar(names, scores, color=colors, edgecolor="black", linewidth=0.5, alpha=0.7)

    ax.set_ylabel("Resilience Score (%)")
    ax.set_title("Resilience Score by Placement Strategy")
    ax.set_ylim(0, 105)
    ax.yaxis.set_major_locator(ticker.MultipleLocator(10))
    ax.grid(axis="y", alpha=0.3)

    if iteration_data:
        for i, name in enumerate(names):
            idata = iteration_data.get(name, {})
            iter_scores = idata.get("resilienceScores", [])
            if len(iter_scores) > 1:
                jitter = [i + (j - len(iter_scores) / 2) * 0.05 for j in range(len(iter_scores))]
                ax.scatter(jitter, iter_scores, color="black", s=20, zorder=5, alpha=0.8)

    for bar, score in zip(bars, scores):
        label = f"{score:.1f}"
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 1,
            label,
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
        )

    plt.tight_layout()
    filepath = str(output_path / "resilience_scores.png")
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    return filepath


# Keep old private name for backwards-compatible test imports.
_chart_resilience_scores = chart_resilience_scores


def chart_recovery_times(
    strategies: Dict[str, Any],
    output_path: Path,
    iteration_data: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Generate recovery time comparison chart with per-iteration data points."""
    names = []
    mean_times = []
    p95_times = []

    for name, data in strategies.items():
        mean = data.get("avgMeanRecovery_ms")
        p95 = data.get("avgP95Recovery_ms")
        if mean is not None:
            names.append(name)
            mean_times.append(mean)
            p95_times.append(p95 if p95 is not None else mean)

    if not names:
        return None

    fig, ax = plt.subplots(figsize=(10, 6))
    x = range(len(names))
    width = 0.35

    bars1 = ax.bar(
        [i - width / 2 for i in x],
        mean_times,
        width,
        label="Mean Recovery",
        color="#4CAF50",
        edgecolor="black",
        linewidth=0.5,
        alpha=0.7,
    )
    bars2 = ax.bar(
        [i + width / 2 for i in x],
        p95_times,
        width,
        label="P95 Recovery",
        color="#FF9800",
        edgecolor="black",
        linewidth=0.5,
        alpha=0.7,
    )

    if iteration_data:
        for i, name in enumerate(names):
            idata = iteration_data.get(name, {})
            iter_times = idata.get("recoveryTimes", [])
            if len(iter_times) > 1:
                jitter = [
                    i - width / 2 + (j - len(iter_times) / 2) * 0.03 for j in range(len(iter_times))
                ]
                ax.scatter(
                    jitter,
                    iter_times,
                    color="black",
                    s=20,
                    zorder=5,
                    alpha=0.8,
                    label="Per-iteration" if i == 0 else None,
                )

    ax.set_ylabel("Recovery Time (ms)")
    ax.set_title("Pod Recovery Time by Placement Strategy")
    ax.set_xticks(list(x))
    ax.set_xticklabels(names)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    for bar in bars1:
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 20,
            f"{bar.get_height():.0f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    for bar in bars2:
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 20,
            f"{bar.get_height():.0f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    plt.tight_layout()
    filepath = str(output_path / "recovery_times.png")
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    return filepath


_chart_recovery_times = chart_recovery_times


# ---------------------------------------------------------------------------
# Latency charts + extraction
# ---------------------------------------------------------------------------

def extract_latency_data(
    raw_strategies: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Extract latency phase data from raw strategy results."""
    result = {}
    for name, sdata in raw_strategies.items():
        metrics = sdata.get("metrics") or {}
        if metrics and "latency" in metrics:
            result[name] = metrics["latency"]
            continue

        iters = sdata.get("iterations", [])
        if iters:
            for it in iters:
                lat = it.get("metrics", {}).get("latency")
                if lat:
                    result[name] = lat
                    break

    return result


_extract_latency_data = extract_latency_data


def chart_latency_by_strategy(
    latency_by_strategy: Dict[str, Dict[str, Any]],
    output_path: Path,
) -> Optional[str]:
    """Generate a grouped bar chart of HTTP route latency across strategies."""
    all_routes = set()
    for lat_data in latency_by_strategy.values():
        phases = lat_data.get("phases", {})
        during = phases.get("during-chaos", {})
        for route in during.get("routes", {}):
            all_routes.add(route)

    if not all_routes:
        return None

    routes = sorted(all_routes)
    strategy_names = sorted(latency_by_strategy.keys())

    fig, ax = plt.subplots(figsize=(max(10, len(routes) * 2.5), 6))
    width = 0.8 / len(strategy_names)
    colors = strategy_colors(strategy_names)

    for i, strat in enumerate(strategy_names):
        phases = latency_by_strategy[strat].get("phases", {})
        during = phases.get("during-chaos", {}).get("routes", {})

        means = []
        for route in routes:
            route_data = during.get(route, {})
            means.append(route_data.get("mean_ms") or 0)

        x = [j + i * width for j in range(len(routes))]
        bars = ax.bar(
            x,
            means,
            width,
            label=strat,
            color=colors[i],
            edgecolor="black",
            linewidth=0.5,
            alpha=0.7,
        )

        for bar, val in zip(bars, means):
            if val > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 5,
                    f"{val:.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )

    ax.set_ylabel("Mean Latency (ms)")
    ax.set_title("Inter-Service Latency During Chaos by Strategy")
    ax.set_xticks([j + width * (len(strategy_names) - 1) / 2 for j in range(len(routes))])
    ax.set_xticklabels(routes, rotation=30, ha="right", fontsize=9)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    filepath = str(output_path / "latency_by_strategy.png")
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    return filepath


_chart_latency_by_strategy = chart_latency_by_strategy


def chart_latency_degradation(
    latency_by_strategy: Dict[str, Dict[str, Any]],
    output_path: Path,
) -> Optional[str]:
    """Generate a chart showing latency degradation from pre-chaos to during-chaos."""
    valid_strategies = {}
    for strat, lat_data in latency_by_strategy.items():
        phases = lat_data.get("phases", {})
        pre = phases.get("pre-chaos", {}).get("routes", {})
        during = phases.get("during-chaos", {}).get("routes", {})
        if pre and during:
            valid_strategies[strat] = (pre, during)

    if not valid_strategies:
        return None

    all_routes = set()
    for pre, during in valid_strategies.values():
        all_routes.update(pre.keys())
        all_routes.update(during.keys())
    routes = sorted(all_routes)

    if not routes:
        return None

    n_strats = len(valid_strategies)
    fig, axes = plt.subplots(
        1, n_strats, figsize=(max(6, 5 * n_strats), 6), squeeze=False, sharey=True
    )

    for idx, (strat, (pre, during)) in enumerate(sorted(valid_strategies.items())):
        ax = axes[0][idx]
        pre_vals = [pre.get(r, {}).get("mean_ms") or 0 for r in routes]
        during_vals = [during.get(r, {}).get("mean_ms") or 0 for r in routes]

        x = range(len(routes))
        width = 0.35
        ax.bar(
            [i - width / 2 for i in x],
            pre_vals,
            width,
            label="Pre-chaos",
            color="#4CAF50",
            alpha=0.7,
            edgecolor="black",
            linewidth=0.5,
        )
        ax.bar(
            [i + width / 2 for i in x],
            during_vals,
            width,
            label="During chaos",
            color="#F44336",
            alpha=0.7,
            edgecolor="black",
            linewidth=0.5,
        )

        ax.set_title(f"{strat}")
        ax.set_xticks(list(x))
        ax.set_xticklabels(routes, rotation=45, ha="right", fontsize=8)
        ax.legend(fontsize=8)
        ax.grid(axis="y", alpha=0.3)

        if idx == 0:
            ax.set_ylabel("Mean Latency (ms)")

    fig.suptitle("Latency Degradation: Pre-Chaos vs During Chaos", fontsize=13)
    plt.tight_layout()
    filepath = str(output_path / "latency_degradation.png")
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    return filepath


_chart_latency_degradation = chart_latency_degradation


# ---------------------------------------------------------------------------
# Throughput charts + extraction
# ---------------------------------------------------------------------------

def extract_throughput_data(
    raw_strategies: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Extract throughput phase data from raw strategy results."""
    result = {}
    for name, sdata in raw_strategies.items():
        metrics = sdata.get("metrics") or {}

        redis_data = metrics.get("redis", {})
        disk_data = metrics.get("disk", {})

        if not redis_data and not disk_data and "throughput" in metrics:
            result[name] = metrics["throughput"]
            continue

        if not redis_data and not disk_data:
            iters = sdata.get("iterations", [])
            for it in iters:
                m = it.get("metrics", {})
                redis_data = m.get("redis", {})
                disk_data = m.get("disk", {})
                if redis_data or disk_data:
                    break
                tp = m.get("throughput")
                if tp:
                    result[name] = tp
                    break

        if redis_data or disk_data:
            merged: Dict[str, Any] = {"phases": {}}
            all_phases = set()
            for d in (redis_data, disk_data):
                all_phases.update(d.get("phases", {}).keys())
            for phase in all_phases:
                rp = redis_data.get("phases", {}).get(phase, {})
                dp = disk_data.get("phases", {}).get(phase, {})
                merged["phases"][phase] = {
                    "sampleCount": max(
                        rp.get("sampleCount", 0),
                        dp.get("sampleCount", 0),
                    ),
                    "redis": rp.get("redis", {}),
                    "disk": dp.get("disk", {}),
                }
            result[name] = merged

    return result


_extract_throughput_data = extract_throughput_data


def chart_throughput_by_strategy(
    throughput_by_strategy: Dict[str, Dict[str, Any]],
    output_path: Path,
) -> Optional[str]:
    """Generate grouped bar chart of Redis and disk throughput across strategies."""
    all_ops: List[Tuple[str, str]] = []
    for tp_data in throughput_by_strategy.values():
        phases = tp_data.get("phases", {})
        during = phases.get("during-chaos", {})
        for target in ("redis", "disk"):
            target_data = during.get(target, {})
            for op in target_data:
                key = (target, op)
                if key not in all_ops:
                    all_ops.append(key)

    if not all_ops:
        return None

    strategy_names = sorted(throughput_by_strategy.keys())
    labels = [f"{t}-{o}" for t, o in all_ops]

    fig, ax = plt.subplots(figsize=(max(10, len(labels) * 2.5), 6))
    width = 0.8 / max(len(strategy_names), 1)
    colors = strategy_colors(strategy_names)

    for i, strat in enumerate(strategy_names):
        phases = throughput_by_strategy[strat].get("phases", {})
        during = phases.get("during-chaos", {})

        values = []
        for target, op in all_ops:
            op_data = during.get(target, {}).get(op, {})
            values.append(op_data.get("meanOpsPerSecond") or 0)

        x = [j + i * width for j in range(len(labels))]
        bars = ax.bar(
            x,
            values,
            width,
            label=strat,
            color=colors[i],
            edgecolor="black",
            linewidth=0.5,
            alpha=0.7,
        )

        for bar, val in zip(bars, values):
            if val > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 1,
                    f"{val:.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )

    ax.set_ylabel("Mean Ops/Second")
    ax.set_title("Throughput During Chaos by Strategy")
    ax.set_xticks([j + width * (len(strategy_names) - 1) / 2 for j in range(len(labels))])
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    filepath = str(output_path / "throughput_by_strategy.png")
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    return filepath


_chart_throughput_by_strategy = chart_throughput_by_strategy


def chart_throughput_degradation(
    throughput_by_strategy: Dict[str, Dict[str, Any]],
    output_path: Path,
) -> Optional[str]:
    """Generate chart showing throughput degradation from pre-chaos to during-chaos."""
    valid_strategies = {}
    for strat, tp_data in throughput_by_strategy.items():
        phases = tp_data.get("phases", {})
        pre = phases.get("pre-chaos", {})
        during = phases.get("during-chaos", {})
        has_pre = any(pre.get(t) for t in ("redis", "disk"))
        has_during = any(during.get(t) for t in ("redis", "disk"))
        if has_pre and has_during:
            valid_strategies[strat] = (pre, during)

    if not valid_strategies:
        return None

    all_ops = []
    for pre, during in valid_strategies.values():
        for target in ("redis", "disk"):
            for op in set(list(pre.get(target, {}).keys()) + list(during.get(target, {}).keys())):
                key = f"{target}-{op}"
                if key not in all_ops:
                    all_ops.append(key)

    if not all_ops:
        return None

    n_strats = len(valid_strategies)
    fig, axes = plt.subplots(
        1, n_strats, figsize=(max(6, 5 * n_strats), 6), squeeze=False, sharey=True
    )

    for idx, (strat, (pre, during)) in enumerate(sorted(valid_strategies.items())):
        ax = axes[0][idx]

        pre_vals = []
        during_vals = []
        for label in all_ops:
            target, op = label.split("-", 1)
            pre_val = pre.get(target, {}).get(op, {}).get("meanOpsPerSecond") or 0
            during_val = during.get(target, {}).get(op, {}).get("meanOpsPerSecond") or 0
            pre_vals.append(pre_val)
            during_vals.append(during_val)

        x = range(len(all_ops))
        width = 0.35
        ax.bar(
            [i - width / 2 for i in x],
            pre_vals,
            width,
            label="Pre-chaos",
            color="#4CAF50",
            alpha=0.7,
            edgecolor="black",
            linewidth=0.5,
        )
        ax.bar(
            [i + width / 2 for i in x],
            during_vals,
            width,
            label="During chaos",
            color="#F44336",
            alpha=0.7,
            edgecolor="black",
            linewidth=0.5,
        )

        ax.set_title(f"{strat}")
        ax.set_xticks(list(x))
        ax.set_xticklabels(all_ops, rotation=45, ha="right", fontsize=8)
        ax.legend(fontsize=8)
        ax.grid(axis="y", alpha=0.3)

        if idx == 0:
            ax.set_ylabel("Ops/Second")

    fig.suptitle("Throughput Degradation: Pre-Chaos vs During Chaos", fontsize=13)
    plt.tight_layout()
    filepath = str(output_path / "throughput_degradation.png")
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    return filepath


_chart_throughput_degradation = chart_throughput_degradation


# ---------------------------------------------------------------------------
# Resource charts + extraction
# ---------------------------------------------------------------------------

def extract_resource_data(
    raw_strategies: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Extract resource utilization data from raw strategy results."""
    result = {}
    for name, sdata in raw_strategies.items():
        metrics = sdata.get("metrics") or {}
        if metrics and "resources" in metrics:
            res = metrics["resources"]
            if res.get("available", False):
                result[name] = res
                continue

        for it in sdata.get("iterations", []):
            m = it.get("metrics", {})
            res = m.get("resources", {})
            if res.get("available", False):
                result[name] = res
                break

    return result


_extract_resource_data = extract_resource_data


def chart_resource_utilization(
    resource_by_strategy: Dict[str, Dict[str, Any]],
    output_path: Path,
) -> Optional[str]:
    """Generate line chart of CPU% and memory% over time across strategies."""
    if not resource_by_strategy:
        return None

    fig, (ax_cpu, ax_mem) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    strategy_names = sorted(resource_by_strategy.keys())
    colors = strategy_colors(strategy_names)

    for idx, strat in enumerate(strategy_names):
        series = resource_by_strategy[strat].get("timeSeries", [])
        if not series:
            continue

        elapsed = [e["elapsed_s"] for e in series]
        cpu_pct = [e.get("node", {}).get("cpu_percent", 0) for e in series]
        mem_pct = [e.get("node", {}).get("memory_percent", 0) for e in series]

        ax_cpu.plot(
            elapsed,
            cpu_pct,
            label=strat,
            color=colors[idx],
            linewidth=1.5,
            alpha=0.8,
        )
        ax_mem.plot(
            elapsed,
            mem_pct,
            label=strat,
            color=colors[idx],
            linewidth=1.5,
            alpha=0.8,
        )

    ax_cpu.set_ylabel("CPU Utilization (%)")
    ax_cpu.set_title("Node Resource Utilization During Experiment")
    ax_cpu.legend()
    ax_cpu.grid(alpha=0.3)
    ax_cpu.set_ylim(0, 105)

    ax_mem.set_ylabel("Memory Utilization (%)")
    ax_mem.set_xlabel("Elapsed Time (s)")
    ax_mem.legend()
    ax_mem.grid(alpha=0.3)
    ax_mem.set_ylim(0, 105)

    plt.tight_layout()
    filepath = str(output_path / "resource_utilization.png")
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    return filepath


_chart_resource_utilization = chart_resource_utilization


def chart_resource_by_phase(
    resource_by_strategy: Dict[str, Dict[str, Any]],
    output_path: Path,
) -> Optional[str]:
    """Generate bar chart showing mean CPU and memory per phase per strategy."""
    if not resource_by_strategy:
        return None

    strategy_names = sorted(resource_by_strategy.keys())
    phase_names = ["pre-chaos", "during-chaos", "post-chaos"]

    fig, (ax_cpu, ax_mem) = plt.subplots(1, 2, figsize=(14, 6))
    width = 0.8 / max(len(strategy_names), 1)
    colors = strategy_colors(strategy_names)

    for i, strat in enumerate(strategy_names):
        phases = resource_by_strategy[strat].get("phases", {})

        cpu_vals = []
        mem_vals = []
        for phase in phase_names:
            pd = phases.get(phase, {}).get("node", {})
            cpu_vals.append(pd.get("meanCpu_percent") or 0)
            mem_vals.append(pd.get("meanMemory_percent") or 0)

        x = [j + i * width for j in range(len(phase_names))]
        ax_cpu.bar(
            x,
            cpu_vals,
            width,
            label=strat,
            color=colors[i],
            edgecolor="black",
            linewidth=0.5,
            alpha=0.7,
        )
        ax_mem.bar(
            x,
            mem_vals,
            width,
            label=strat,
            color=colors[i],
            edgecolor="black",
            linewidth=0.5,
            alpha=0.7,
        )

    for ax, title, ylabel in [
        (ax_cpu, "CPU Utilization by Phase", "Mean CPU (%)"),
        (ax_mem, "Memory Utilization by Phase", "Mean Memory (%)"),
    ]:
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.set_xticks([j + width * (len(strategy_names) - 1) / 2 for j in range(len(phase_names))])
        ax.set_xticklabels(phase_names, fontsize=9)
        ax.legend()
        ax.grid(axis="y", alpha=0.3)
        ax.set_ylim(0, 105)

    plt.tight_layout()
    filepath = str(output_path / "resource_by_phase.png")
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    return filepath


_chart_resource_by_phase = chart_resource_by_phase


# ---------------------------------------------------------------------------
# Prometheus charts + extraction
# ---------------------------------------------------------------------------

def extract_prometheus_data(
    raw_strategies: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Extract Prometheus metrics data from raw strategy results."""
    result = {}
    for name, sdata in raw_strategies.items():
        metrics = sdata.get("metrics") or {}
        if metrics and "prometheus" in metrics:
            prom = metrics["prometheus"]
            if prom.get("available", False):
                result[name] = prom
                continue

        for it in sdata.get("iterations", []):
            m = it.get("metrics", {})
            prom = m.get("prometheus", {})
            if prom.get("available", False):
                result[name] = prom
                break

    return result


_extract_prometheus_data = extract_prometheus_data


def chart_prometheus_by_phase(
    prometheus_by_strategy: Dict[str, Dict[str, Any]],
    output_path: Path,
) -> Optional[str]:
    """Generate grouped bar chart of Prometheus metrics per phase per strategy."""
    if not prometheus_by_strategy:
        return None

    all_labels: set = set()
    for pdata in prometheus_by_strategy.values():
        phases = pdata.get("phases", {})
        for phase_info in phases.values():
            all_labels.update(phase_info.get("metrics", {}).keys())

    if not all_labels:
        return None

    labels = sorted(all_labels)
    strategy_names = sorted(prometheus_by_strategy.keys())
    phase_names = ["pre-chaos", "during-chaos", "post-chaos"]

    n_metrics = len(labels)
    cols = min(n_metrics, 3)
    rows_count = (n_metrics + cols - 1) // cols
    fig, axes = plt.subplots(
        rows_count,
        cols,
        figsize=(6 * cols, 5 * rows_count),
        squeeze=False,
    )

    width = 0.8 / max(len(strategy_names), 1)
    colors = strategy_colors(strategy_names)

    for m_idx, metric_label in enumerate(labels):
        row_i = m_idx // cols
        col_i = m_idx % cols
        ax = axes[row_i][col_i]

        for s_idx, strat in enumerate(strategy_names):
            phases = prometheus_by_strategy[strat].get("phases", {})
            vals = []
            for phase in phase_names:
                metric_agg = phases.get(phase, {}).get("metrics", {}).get(metric_label, {})
                vals.append(metric_agg.get("mean") or 0)

            x = [j + s_idx * width for j in range(len(phase_names))]
            ax.bar(
                x,
                vals,
                width,
                label=strat,
                color=colors[s_idx],
                edgecolor="black",
                linewidth=0.5,
                alpha=0.7,
            )

        ax.set_title(metric_label.replace("_", " ").title(), fontsize=10)
        ax.set_xticks([j + width * (len(strategy_names) - 1) / 2 for j in range(len(phase_names))])
        ax.set_xticklabels(phase_names, fontsize=8)
        ax.legend(fontsize=7)
        ax.grid(axis="y", alpha=0.3)

    for m_idx in range(n_metrics, rows_count * cols):
        axes[m_idx // cols][m_idx % cols].set_visible(False)

    fig.suptitle("Prometheus Metrics by Phase and Strategy", fontsize=13)
    plt.tight_layout()
    filepath = str(output_path / "prometheus_by_phase.png")
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    return filepath


_chart_prometheus_by_phase = chart_prometheus_by_phase
