"""Visualization module for ChaosProbe experiment results.

Generates charts correlating placement strategies with performance metrics.
Uses matplotlib for chart generation and exports to PNG/HTML.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def check_matplotlib():
    """Raise an error if matplotlib is not installed."""
    if not HAS_MATPLOTLIB:
        raise ImportError(
            "matplotlib is required for visualization. "
            "Install it with: pip install matplotlib"
        )


def generate_all_charts(
    store,
    output_dir: str,
    scenario: Optional[str] = None,
) -> List[str]:
    """Generate all charts from database data.

    Args:
        store: SQLiteStore instance.
        output_dir: Directory to save chart images.
        scenario: Optional scenario filter.

    Returns:
        List of generated file paths.
    """
    check_matplotlib()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    generated = []

    comparison = store.compare_strategies(scenario=scenario)
    strategies = comparison.get("strategies", {})

    if not strategies:
        return generated

    # 1. Resilience score comparison (box plot style bar chart)
    path = _chart_resilience_scores(strategies, output_path)
    if path:
        generated.append(path)

    # 2. Recovery time comparison
    path = _chart_recovery_times(strategies, output_path)
    if path:
        generated.append(path)

    # 3. Load generation metrics (if available)
    path = _chart_load_metrics(strategies, output_path)
    if path:
        generated.append(path)

    # 4. Pod-node heatmap from individual runs
    runs = store.list_runs(scenario=scenario, limit=100)
    path = _chart_pod_node_heatmap(store, runs, output_path)
    if path:
        generated.append(path)

    # 5. Generate HTML summary
    html_path = _generate_html_summary(generated, strategies, output_path)
    if html_path:
        generated.append(html_path)

    return generated


def generate_from_summary(
    summary_path: str,
    output_dir: str,
) -> List[str]:
    """Generate charts from a summary.json file (no database needed).

    Args:
        summary_path: Path to a run-all summary.json file.
        output_dir: Directory to save chart images.

    Returns:
        List of generated file paths.
    """
    check_matplotlib()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    with open(summary_path) as f:
        summary = json.load(f)

    raw_strategies = summary.get("strategies", {})
    if not raw_strategies:
        return []

    generated = []
    iterations_count = summary.get("iterations", 1)

    # Build strategies dict from the full strategy data (not the flat comparison table)
    strategies = {}
    for name, sdata in raw_strategies.items():
        exp = sdata.get("experiment", {}) or {}
        # Recovery metrics live in experiment for multi-iteration (aggregated),
        # but in metrics.recovery.summary for single-iteration runs.
        rec_summary = (sdata.get("metrics") or {}).get("recovery", {}).get("summary", {})
        strategies[name] = {
            "avgResilienceScore": exp.get("meanResilienceScore", exp.get("resilienceScore", 0)),
            "passRate": exp.get("passRate", 0.0),
            "avgMeanRecovery_ms": exp.get("meanRecoveryTime_ms") or rec_summary.get("meanRecovery_ms"),
            "avgP95Recovery_ms": exp.get("maxRecoveryTime_ms") or rec_summary.get("p95Recovery_ms"),
            "medianRecovery_ms": exp.get("medianRecoveryTime_ms") or rec_summary.get("medianRecovery_ms"),
            "runCount": exp.get("totalExperiments", iterations_count),
        }

    # Collect per-iteration data points for detailed charts
    iteration_data = {}
    for name, sdata in raw_strategies.items():
        iters = sdata.get("iterations", [])
        if iters:
            iteration_data[name] = {
                "resilienceScores": [it.get("resilienceScore", 0) for it in iters],
                "recoveryTimes": [],
            }
            for it in iters:
                metrics = it.get("metrics", {})
                recovery = metrics.get("recovery", {}).get("summary", {})
                mean_rec = recovery.get("meanRecovery_ms")
                if mean_rec is not None:
                    iteration_data[name]["recoveryTimes"].append(mean_rec)

    path = _chart_resilience_scores(strategies, output_path, iteration_data)
    if path:
        generated.append(path)

    path = _chart_recovery_times(strategies, output_path, iteration_data)
    if path:
        generated.append(path)

    # Generate latency charts from per-strategy latency data
    latency_by_strategy = _extract_latency_data(raw_strategies)
    if latency_by_strategy:
        path = _chart_latency_by_strategy(latency_by_strategy, output_path)
        if path:
            generated.append(path)

        path = _chart_latency_degradation(latency_by_strategy, output_path)
        if path:
            generated.append(path)

    # Generate throughput charts from per-strategy throughput data
    throughput_by_strategy = _extract_throughput_data(raw_strategies)
    if throughput_by_strategy:
        path = _chart_throughput_by_strategy(throughput_by_strategy, output_path)
        if path:
            generated.append(path)

        path = _chart_throughput_degradation(throughput_by_strategy, output_path)
        if path:
            generated.append(path)

    html_path = _generate_html_summary(
        generated, strategies, output_path, iterations_count,
        latency_data=latency_by_strategy,
        throughput_data=throughput_by_strategy,
    )
    if html_path:
        generated.append(html_path)

    return generated


def _chart_resilience_scores(
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
    colors = _strategy_colors(names)
    bars = ax.bar(names, scores, color=colors, edgecolor="black", linewidth=0.5, alpha=0.7)

    ax.set_ylabel("Resilience Score (%)")
    ax.set_title("Resilience Score by Placement Strategy")
    ax.set_ylim(0, 105)
    ax.yaxis.set_major_locator(ticker.MultipleLocator(10))
    ax.grid(axis="y", alpha=0.3)

    # Overlay per-iteration data points
    if iteration_data:
        for i, name in enumerate(names):
            idata = iteration_data.get(name, {})
            iter_scores = idata.get("resilienceScores", [])
            if len(iter_scores) > 1:
                jitter = [i + (j - len(iter_scores) / 2) * 0.05 for j in range(len(iter_scores))]
                ax.scatter(jitter, iter_scores, color="black", s=20, zorder=5, alpha=0.8)

    # Add value labels on bars
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


def _chart_recovery_times(
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
        [i - width / 2 for i in x], mean_times, width,
        label="Mean Recovery", color="#4CAF50", edgecolor="black", linewidth=0.5, alpha=0.7,
    )
    bars2 = ax.bar(
        [i + width / 2 for i in x], p95_times, width,
        label="P95 Recovery", color="#FF9800", edgecolor="black", linewidth=0.5, alpha=0.7,
    )

    # Overlay per-iteration recovery data points
    if iteration_data:
        for i, name in enumerate(names):
            idata = iteration_data.get(name, {})
            iter_times = idata.get("recoveryTimes", [])
            if len(iter_times) > 1:
                jitter = [i - width / 2 + (j - len(iter_times) / 2) * 0.03
                          for j in range(len(iter_times))]
                ax.scatter(jitter, iter_times, color="black", s=20, zorder=5, alpha=0.8,
                           label="Per-iteration" if i == 0 else None)

    ax.set_ylabel("Recovery Time (ms)")
    ax.set_title("Pod Recovery Time by Placement Strategy")
    ax.set_xticks(list(x))
    ax.set_xticklabels(names)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    # Value labels
    for bar in bars1:
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 20,
                f"{bar.get_height():.0f}", ha="center", va="bottom", fontsize=9)
    for bar in bars2:
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 20,
                f"{bar.get_height():.0f}", ha="center", va="bottom", fontsize=9)

    plt.tight_layout()
    filepath = str(output_path / "recovery_times.png")
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    return filepath


def _chart_load_metrics(
    strategies: Dict[str, Any], output_path: Path
) -> Optional[str]:
    """Generate load generation metrics comparison chart."""
    names = []
    p95_latencies = []
    error_rates = []

    for name, data in strategies.items():
        p95 = data.get("avgLoadP95_ms")
        err = data.get("avgLoadErrorRate")
        if p95 is not None:
            names.append(name)
            p95_latencies.append(p95)
            error_rates.append((err or 0) * 100)

    if not names:
        return None

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    colors = _strategy_colors(names)

    # P95 latency
    ax1.bar(names, p95_latencies, color=colors, edgecolor="black", linewidth=0.5)
    ax1.set_ylabel("P95 Response Time (ms)")
    ax1.set_title("Load Test P95 Latency by Strategy")
    ax1.grid(axis="y", alpha=0.3)

    # Error rate
    ax2.bar(names, error_rates, color=colors, edgecolor="black", linewidth=0.5)
    ax2.set_ylabel("Error Rate (%)")
    ax2.set_title("Load Test Error Rate by Strategy")
    ax2.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    filepath = str(output_path / "load_metrics.png")
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    return filepath


def _chart_pod_node_heatmap(
    store, runs: List[Dict[str, Any]], output_path: Path
) -> Optional[str]:
    """Generate pod-node placement heatmap from recent runs."""
    # Collect placement data per strategy
    strategy_placements: Dict[str, Dict[str, str]] = {}

    for run_info in runs:
        run = store.get_run(run_info["id"])
        if not run:
            continue
        placement = run.get("placement", {})
        strategy = placement.get("strategy")
        assignments = placement.get("assignments", {})
        if strategy and assignments:
            strategy_placements[strategy] = assignments

    if not strategy_placements:
        return None

    # Build data matrix: pods x strategies, values = node names
    all_pods = sorted(
        set(p for assigns in strategy_placements.values() for p in assigns.keys())
    )
    all_strategies = sorted(strategy_placements.keys())
    all_nodes = sorted(
        set(n for assigns in strategy_placements.values() for n in assigns.values())
    )

    if not all_pods or not all_strategies:
        return None

    # Create node→int mapping for coloring
    node_to_int = {node: i for i, node in enumerate(all_nodes)}

    data = []
    for pod in all_pods:
        row = []
        for strat in all_strategies:
            node = strategy_placements.get(strat, {}).get(pod, "")
            row.append(node_to_int.get(node, -1))
        data.append(row)

    fig, ax = plt.subplots(figsize=(max(8, len(all_strategies) * 2), max(6, len(all_pods) * 0.5)))

    cmap = plt.cm.get_cmap("Set3", len(all_nodes))
    im = ax.imshow(data, cmap=cmap, aspect="auto", vmin=-0.5, vmax=len(all_nodes) - 0.5)

    ax.set_xticks(range(len(all_strategies)))
    ax.set_xticklabels(all_strategies, rotation=45, ha="right")
    ax.set_yticks(range(len(all_pods)))
    ax.set_yticklabels(all_pods, fontsize=8)
    ax.set_title("Pod-to-Node Placement by Strategy")

    # Add node name text in cells
    for i, pod in enumerate(all_pods):
        for j, strat in enumerate(all_strategies):
            node = strategy_placements.get(strat, {}).get(pod, "")
            if node:
                ax.text(j, i, node, ha="center", va="center", fontsize=7)

    plt.tight_layout()
    filepath = str(output_path / "pod_node_heatmap.png")
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    return filepath


def _generate_html_summary(
    chart_paths: List[str],
    strategies: Dict[str, Any],
    output_path: Path,
    iterations: int = 1,
    latency_data: Optional[Dict[str, Dict[str, Any]]] = None,
    throughput_data: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[str]:
    """Generate an HTML page with embedded charts and summary table."""
    if not chart_paths:
        return None

    rows = ""
    for name, data in sorted(strategies.items()):
        avg_rec = data.get('avgMeanRecovery_ms')
        avg_rec_str = f"{avg_rec:.1f}" if avg_rec is not None else "n/a"
        median_rec = data.get('medianRecovery_ms')
        median_str = f"{median_rec:.1f}" if median_rec is not None else "n/a"
        p95_rec = data.get('avgP95Recovery_ms')
        p95_str = f"{p95_rec:.1f}" if p95_rec is not None else "n/a"
        run_count = data.get('runCount', iterations)
        rows += f"""
        <tr>
            <td>{name}</td>
            <td>{run_count}</td>
            <td>{data.get('avgResilienceScore', 0):.1f}</td>
            <td>{data.get('passRate', 0):.0%}</td>
            <td>{avg_rec_str}</td>
            <td>{median_str}</td>
            <td>{p95_str}</td>
        </tr>"""

    # Build latency summary table if data is available
    latency_section = ""
    if latency_data:
        latency_rows = ""
        # Collect all routes
        all_routes = set()
        for lat in latency_data.values():
            phases = lat.get("phases", {})
            for phase in phases.values():
                all_routes.update(phase.get("routes", {}).keys())
        routes = sorted(all_routes)

        for strat_name in sorted(latency_data.keys()):
            phases = latency_data[strat_name].get("phases", {})
            pre = phases.get("pre-chaos", {}).get("routes", {})
            during = phases.get("during-chaos", {}).get("routes", {})
            post = phases.get("post-chaos", {}).get("routes", {})

            for route in routes:
                pre_mean = pre.get(route, {}).get("mean_ms")
                during_mean = during.get(route, {}).get("mean_ms")
                post_mean = post.get(route, {}).get("mean_ms")
                during_p95 = during.get(route, {}).get("p95_ms")
                during_errs = during.get(route, {}).get("errorCount", 0)

                pre_str = f"{pre_mean:.1f}" if pre_mean is not None else "n/a"
                during_str = f"{during_mean:.1f}" if during_mean is not None else "n/a"
                post_str = f"{post_mean:.1f}" if post_mean is not None else "n/a"
                p95_str = f"{during_p95:.1f}" if during_p95 is not None else "n/a"

                # Highlight degradation
                degradation = ""
                if pre_mean and during_mean and during_mean > pre_mean * 1.5:
                    pct = ((during_mean - pre_mean) / pre_mean) * 100
                    degradation = f' <span style="color: red;">+{pct:.0f}%</span>'

                latency_rows += f"""
            <tr>
                <td>{strat_name}</td>
                <td>{route}</td>
                <td>{pre_str}</td>
                <td>{during_str}{degradation}</td>
                <td>{p95_str}</td>
                <td>{post_str}</td>
                <td>{during_errs}</td>
            </tr>"""

        latency_section = f"""
    <h2>Inter-Service Latency</h2>
    <table>
        <tr>
            <th>Strategy</th>
            <th>Route</th>
            <th>Pre-Chaos Mean (ms)</th>
            <th>During Chaos Mean (ms)</th>
            <th>During Chaos P95 (ms)</th>
            <th>Post-Chaos Mean (ms)</th>
            <th>Errors During Chaos</th>
        </tr>
        {latency_rows}
    </table>"""

    # Build throughput summary table if data is available
    throughput_section = ""
    if throughput_data:
        throughput_rows = ""
        for strat_name in sorted(throughput_data.keys()):
            phases = throughput_data[strat_name].get("phases", {})
            pre = phases.get("pre-chaos", {})
            during = phases.get("during-chaos", {})
            post = phases.get("post-chaos", {})

            for target in ("redis", "disk"):
                all_ops = set()
                for phase in (pre, during, post):
                    all_ops.update(phase.get(target, {}).keys())

                for op in sorted(all_ops):
                    pre_ops = pre.get(target, {}).get(op, {}).get("meanOpsPerSecond")
                    during_ops = during.get(target, {}).get(op, {}).get("meanOpsPerSecond")
                    post_ops = post.get(target, {}).get(op, {}).get("meanOpsPerSecond")
                    during_lat = during.get(target, {}).get(op, {}).get("meanLatency_ms")
                    during_bps = during.get(target, {}).get(op, {}).get("meanBytesPerSecond")

                    pre_str = f"{pre_ops:.1f}" if pre_ops is not None else "n/a"
                    during_str = f"{during_ops:.1f}" if during_ops is not None else "n/a"
                    post_str = f"{post_ops:.1f}" if post_ops is not None else "n/a"
                    lat_str = f"{during_lat:.2f}" if during_lat is not None else "n/a"
                    bps_str = f"{during_bps / 1024 / 1024:.1f} MB/s" if during_bps is not None else "n/a"

                    degradation = ""
                    if pre_ops and during_ops and during_ops < pre_ops * 0.7:
                        pct = ((pre_ops - during_ops) / pre_ops) * 100
                        degradation = f' <span style="color: red;">-{pct:.0f}%</span>'

                    throughput_rows += f"""
            <tr>
                <td>{strat_name}</td>
                <td>{target}-{op}</td>
                <td>{pre_str}</td>
                <td>{during_str}{degradation}</td>
                <td>{lat_str}</td>
                <td>{bps_str}</td>
                <td>{post_str}</td>
            </tr>"""

        throughput_section = f"""
    <h2>I/O Throughput</h2>
    <table>
        <tr>
            <th>Strategy</th>
            <th>Operation</th>
            <th>Pre-Chaos Ops/s</th>
            <th>During Chaos Ops/s</th>
            <th>During Chaos Latency (ms)</th>
            <th>During Chaos Bandwidth</th>
            <th>Post-Chaos Ops/s</th>
        </tr>
        {throughput_rows}
    </table>"""

    img_tags = ""
    for path in chart_paths:
        if path.endswith(".png"):
            filename = Path(path).name
            img_tags += f'<img src="{filename}" style="max-width:100%; margin:10px 0;">\n'

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>ChaosProbe Experiment Results</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 40px; background: #f5f5f5; }}
        h1 {{ color: #333; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0; background: white; }}
        th, td {{ border: 1px solid #ddd; padding: 12px 16px; text-align: left; }}
        th {{ background: #4CAF50; color: white; }}
        tr:nth-child(even) {{ background: #f9f9f9; }}
        .charts {{ margin: 20px 0; }}
        img {{ border: 1px solid #ddd; border-radius: 4px; }}
    </style>
</head>
<body>
    <h1>ChaosProbe - Experiment Results</h1>

    <h2>Strategy Comparison ({iterations} iteration{"s" if iterations != 1 else ""} per strategy)</h2>
    <table>
        <tr>
            <th>Strategy</th>
            <th>Runs</th>
            <th>Avg Resilience Score</th>
            <th>Pass Rate</th>
            <th>Mean Recovery (ms)</th>
            <th>Median Recovery (ms)</th>
            <th>P95 Recovery (ms)</th>
        </tr>
        {rows}
    </table>

    {latency_section}

    {throughput_section}

    <h2>Charts</h2>
    <div class="charts">
        {img_tags}
    </div>
</body>
</html>"""

    filepath = str(output_path / "report.html")
    with open(filepath, "w") as f:
        f.write(html)
    return filepath


def _extract_latency_data(
    raw_strategies: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Extract latency phase data from raw strategy results.

    Looks for latency data in metrics (single iteration) or aggregates
    across iterations (multi-iteration).

    Returns:
        Dict mapping strategy name to latency phase data.
    """
    result = {}
    for name, sdata in raw_strategies.items():
        # Single iteration: metrics.latency
        metrics = sdata.get("metrics", {})
        if metrics and "latency" in metrics:
            result[name] = metrics["latency"]
            continue

        # Multi-iteration: aggregate from iterations
        iters = sdata.get("iterations", [])
        if iters:
            # Use the first iteration that has latency data
            for it in iters:
                lat = it.get("metrics", {}).get("latency")
                if lat:
                    result[name] = lat
                    break

    return result


def _chart_latency_by_strategy(
    latency_by_strategy: Dict[str, Dict[str, Any]],
    output_path: Path,
) -> Optional[str]:
    """Generate a grouped bar chart of HTTP route latency across strategies.

    Shows mean latency for each HTTP route under each placement strategy,
    using the during-chaos phase data.
    """
    # Collect all routes across strategies
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
    colors = _strategy_colors(strategy_names)

    for i, strat in enumerate(strategy_names):
        phases = latency_by_strategy[strat].get("phases", {})
        during = phases.get("during-chaos", {}).get("routes", {})

        means = []
        for route in routes:
            route_data = during.get(route, {})
            means.append(route_data.get("mean_ms") or 0)

        x = [j + i * width for j in range(len(routes))]
        bars = ax.bar(x, means, width, label=strat, color=colors[i],
                       edgecolor="black", linewidth=0.5, alpha=0.7)

        for bar, val in zip(bars, means):
            if val > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 5,
                        f"{val:.0f}", ha="center", va="bottom", fontsize=8)

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


def _chart_latency_degradation(
    latency_by_strategy: Dict[str, Dict[str, Any]],
    output_path: Path,
) -> Optional[str]:
    """Generate a chart showing latency degradation from pre-chaos to during-chaos.

    For each strategy, shows how each route's latency changed between the
    pre-chaos baseline and the during-chaos phase.
    """
    # Find strategies with both pre-chaos and during-chaos data
    valid_strategies = {}
    for strat, lat_data in latency_by_strategy.items():
        phases = lat_data.get("phases", {})
        pre = phases.get("pre-chaos", {}).get("routes", {})
        during = phases.get("during-chaos", {}).get("routes", {})
        if pre and during:
            valid_strategies[strat] = (pre, during)

    if not valid_strategies:
        return None

    # Collect all routes
    all_routes = set()
    for pre, during in valid_strategies.values():
        all_routes.update(pre.keys())
        all_routes.update(during.keys())  
    routes = sorted(all_routes)

    if not routes:
        return None

    n_strats = len(valid_strategies)
    fig, axes = plt.subplots(1, n_strats, figsize=(max(6, 5 * n_strats), 6),
                              squeeze=False, sharey=True)

    for idx, (strat, (pre, during)) in enumerate(sorted(valid_strategies.items())):
        ax = axes[0][idx]
        pre_vals = [pre.get(r, {}).get("mean_ms") or 0 for r in routes]
        during_vals = [during.get(r, {}).get("mean_ms") or 0 for r in routes]

        x = range(len(routes))
        width = 0.35
        ax.bar([i - width / 2 for i in x], pre_vals, width,
               label="Pre-chaos", color="#4CAF50", alpha=0.7, edgecolor="black", linewidth=0.5)
        ax.bar([i + width / 2 for i in x], during_vals, width,
               label="During chaos", color="#F44336", alpha=0.7, edgecolor="black", linewidth=0.5)

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


def _extract_throughput_data(
    raw_strategies: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Extract throughput phase data from raw strategy results.

    Returns:
        Dict mapping strategy name to throughput phase data.
    """
    result = {}
    for name, sdata in raw_strategies.items():
        metrics = sdata.get("metrics", {})
        if metrics and "throughput" in metrics:
            result[name] = metrics["throughput"]
            continue

        iters = sdata.get("iterations", [])
        if iters:
            for it in iters:
                tp = it.get("metrics", {}).get("throughput")
                if tp:
                    result[name] = tp
                    break

    return result


def _chart_throughput_by_strategy(
    throughput_by_strategy: Dict[str, Dict[str, Any]],
    output_path: Path,
) -> Optional[str]:
    """Generate grouped bar chart of Redis and disk throughput across strategies.

    Shows mean ops/sec for each operation type (redis-write, redis-read,
    disk-write, disk-read) during the chaos phase.
    """
    # Collect all operation types across strategies
    all_ops: List[Tuple[str, str]] = []  # (target, operation)
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
    colors = _strategy_colors(strategy_names)

    for i, strat in enumerate(strategy_names):
        phases = throughput_by_strategy[strat].get("phases", {})
        during = phases.get("during-chaos", {})

        values = []
        for target, op in all_ops:
            op_data = during.get(target, {}).get(op, {})
            values.append(op_data.get("meanOpsPerSecond") or 0)

        x = [j + i * width for j in range(len(labels))]
        bars = ax.bar(x, values, width, label=strat, color=colors[i],
                       edgecolor="black", linewidth=0.5, alpha=0.7)

        for bar, val in zip(bars, values):
            if val > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                        f"{val:.0f}", ha="center", va="bottom", fontsize=8)

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


def _chart_throughput_degradation(
    throughput_by_strategy: Dict[str, Dict[str, Any]],
    output_path: Path,
) -> Optional[str]:
    """Generate chart showing throughput degradation from pre-chaos to during-chaos."""
    valid_strategies = {}
    for strat, tp_data in throughput_by_strategy.items():
        phases = tp_data.get("phases", {})
        pre = phases.get("pre-chaos", {})
        during = phases.get("during-chaos", {})
        # Check that at least one target has data in both phases
        has_pre = any(pre.get(t) for t in ("redis", "disk"))
        has_during = any(during.get(t) for t in ("redis", "disk"))
        if has_pre and has_during:
            valid_strategies[strat] = (pre, during)

    if not valid_strategies:
        return None

    # Collect all operation labels
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
    fig, axes = plt.subplots(1, n_strats, figsize=(max(6, 5 * n_strats), 6),
                              squeeze=False, sharey=True)

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
        ax.bar([i - width / 2 for i in x], pre_vals, width,
               label="Pre-chaos", color="#4CAF50", alpha=0.7, edgecolor="black", linewidth=0.5)
        ax.bar([i + width / 2 for i in x], during_vals, width,
               label="During chaos", color="#F44336", alpha=0.7, edgecolor="black", linewidth=0.5)

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


def _strategy_colors(names: List[str]) -> List[str]:
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
