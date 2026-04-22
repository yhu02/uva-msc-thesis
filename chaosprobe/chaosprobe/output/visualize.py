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

from chaosprobe.output.charts import (  # noqa: E402
    chart_latency_by_strategy as _chart_latency_by_strategy,
    chart_latency_degradation as _chart_latency_degradation,
    chart_prometheus_by_phase as _chart_prometheus_by_phase,
    chart_recovery_times as _chart_recovery_times,
    chart_resilience_scores as _chart_resilience_scores,
    chart_resource_by_phase as _chart_resource_by_phase,
    chart_resource_utilization as _chart_resource_utilization,
    chart_strategy_comparison_heatmap as _chart_strategy_comparison_heatmap,
    chart_throughput_by_strategy as _chart_throughput_by_strategy,
    chart_throughput_degradation as _chart_throughput_degradation,
    extract_latency_data as _extract_latency_data,
    extract_prometheus_data as _extract_prometheus_data,
    extract_resource_data as _extract_resource_data,
    extract_throughput_data as _extract_throughput_data,
    strategy_colors as _strategy_colors,
)


def check_matplotlib():
    """Raise an error if matplotlib is not installed."""
    if not HAS_MATPLOTLIB:
        raise ImportError(
            "matplotlib is required for visualization. " "Install it with: pip install matplotlib"
        )


def _compute_pass_rate(exp: Dict[str, Any]) -> float:
    """Compute pass rate from experiment data, handling single-iteration runs."""
    pr = exp.get("passRate")
    if pr is not None:
        return pr
    verdict = exp.get("overallVerdict", "")
    if verdict == "PASS":
        return 1.0
    passed = exp.get("passed", 0)
    total = exp.get("totalExperiments", 0)
    if total > 0:
        return round(passed / total, 2)
    return 0.0


def generate_from_summary(
    summary_path: str,
    output_dir: str,
) -> List[str]:
    """Generate charts from a summary.json file (no database needed).

    Args:
        summary_path: Path to a run summary.json file.
        output_dir: Directory to save chart images.

    Returns:
        List of generated file paths.
    """
    check_matplotlib()

    with open(summary_path) as f:
        summary = json.load(f)

    return generate_from_dict(summary, output_dir)


def generate_from_dict(
    summary: Dict[str, Any],
    output_dir: str,
) -> List[str]:
    """Generate charts from an in-memory summary dict.

    Args:
        summary: Summary dict (same structure as summary.json).
        output_dir: Directory to save chart images.

    Returns:
        List of generated file paths.
    """
    check_matplotlib()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    raw_strategies = summary.get("strategies", {})
    if not raw_strategies:
        return []

    generated = []
    iterations_count = summary.get("iterations", 1)

    # Build strategies dict from the full strategy data (not the flat comparison table)
    strategies = {}
    for name, sdata in raw_strategies.items():
        exp = sdata.get("experiment", {}) or {}
        agg = sdata.get("aggregated", {}) or {}
        # Recovery metrics live in experiment for multi-iteration (aggregated),
        # but in metrics.recovery.summary for single-iteration runs.
        rec_summary = (sdata.get("metrics") or {}).get("recovery", {}).get("summary", {})

        # Compute stddev/min/max from iterations if not in aggregated (backwards compat)
        iter_scores = [it.get("resilienceScore", 0) for it in sdata.get("iterations", [])]
        if iter_scores and len(iter_scores) > 1:
            import statistics as _stats
            stddev = agg.get("stddevResilienceScore") or round(_stats.stdev(iter_scores), 1)
            min_s = agg.get("minResilienceScore") if agg.get("minResilienceScore") is not None else min(iter_scores)
            max_s = agg.get("maxResilienceScore") if agg.get("maxResilienceScore") is not None else max(iter_scores)
        else:
            stddev = agg.get("stddevResilienceScore", 0.0)
            min_s = agg.get("minResilienceScore")
            max_s = agg.get("maxResilienceScore")

        # Prefer healthy-only mean when tainted iterations exist
        tainted = agg.get("taintedIterations", 0)
        all_tainted = agg.get("allIterationsTainted", False)
        if tainted > 0 and not all_tainted:
            avg_score = agg.get("meanResilienceScore_healthyOnly", exp.get("meanResilienceScore", exp.get("resilienceScore", 0)))
            stddev = agg.get("stddevResilienceScore_healthyOnly") or stddev
        else:
            avg_score = exp.get("meanResilienceScore", exp.get("resilienceScore", 0))

        strategies[name] = {
            "avgResilienceScore": avg_score,
            "stddevResilienceScore": stddev,
            "minResilienceScore": min_s,
            "maxResilienceScore": max_s,
            "passRate": _compute_pass_rate(exp),
            "avgMeanRecovery_ms": (
                exp.get("meanRecoveryTime_ms")
                if exp.get("meanRecoveryTime_ms") is not None
                else rec_summary.get("meanRecovery_ms")
            ),
            "avgP95Recovery_ms": (
                exp.get("p95RecoveryTime_ms")
                if exp.get("p95RecoveryTime_ms") is not None
                else rec_summary.get("p95Recovery_ms")
                if rec_summary.get("p95Recovery_ms") is not None
                else exp.get("maxRecoveryTime_ms")
            ),
            "medianRecovery_ms": exp.get("medianRecoveryTime_ms")
            or rec_summary.get("medianRecovery_ms"),
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

    # Generate resource utilization charts from per-strategy resource data
    resource_by_strategy = _extract_resource_data(raw_strategies)
    if resource_by_strategy:
        path = _chart_resource_utilization(resource_by_strategy, output_path)
        if path:
            generated.append(path)

        path = _chart_resource_by_phase(resource_by_strategy, output_path)
        if path:
            generated.append(path)

    # Generate Prometheus metrics charts from per-strategy data
    prometheus_by_strategy = _extract_prometheus_data(raw_strategies)
    if prometheus_by_strategy:
        path = _chart_prometheus_by_phase(prometheus_by_strategy, output_path)
        if path:
            generated.append(path)

    # Strategy comparison heatmap — all thesis dimensions in one chart
    path = _chart_strategy_comparison_heatmap(
        strategies, output_path,
        latency_data=latency_by_strategy,
        throughput_data=throughput_by_strategy,
        resource_data=resource_by_strategy,
    )
    if path:
        generated.append(path)

    html_path = _generate_html_summary(
        generated,
        strategies,
        output_path,
        iterations_count,
        latency_data=latency_by_strategy,
        throughput_data=throughput_by_strategy,
        resource_data=resource_by_strategy,
        prometheus_data=prometheus_by_strategy,
        raw_strategies=raw_strategies,
    )
    if html_path:
        generated.append(html_path)

    return generated


def _build_hypothesis_evaluation(
    strategies: Dict[str, Any],
    resource_data: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Build HTML section evaluating H1, H2, H3 against actual data."""
    evals = []

    # H3: Baseline == 100?
    baseline = strategies.get("baseline", {})
    baseline_score = baseline.get("avgResilienceScore", 0)
    if baseline_score == 100.0:
        h3_status = "supported"
        h3_color = "#2ECC71"
        h3_detail = f"Baseline scored {baseline_score:.0f}% — methodology validated."
    elif baseline_score >= 90:
        h3_status = "partially supported"
        h3_color = "#F39C12"
        h3_detail = f"Baseline scored {baseline_score:.1f}% — close to expected 100%."
    else:
        h3_status = "refuted"
        h3_color = "#E74C3C"
        h3_detail = f"Baseline scored {baseline_score:.1f}% — expected 100%, methodology issue."

    # H1: Colocate has worst resilience?
    colocate = strategies.get("colocate", {})
    colocate_score = colocate.get("avgResilienceScore", 0)
    non_baseline = {k: v for k, v in strategies.items() if k != "baseline"}
    if non_baseline:
        worst_name = min(non_baseline, key=lambda k: non_baseline[k].get("avgResilienceScore", 0))
        worst_score = non_baseline[worst_name].get("avgResilienceScore", 0)
        best_name = max(non_baseline, key=lambda k: non_baseline[k].get("avgResilienceScore", 0))
        best_score = non_baseline[best_name].get("avgResilienceScore", 0)

        # Use standard deviation to define a meaningful "close" threshold
        # instead of a fixed ±5 margin.  If the overlap between two
        # strategies' error bars is large, the difference is noise.
        colocate_sd = colocate.get("stddevResilienceScore", 0)
        worst_sd = non_baseline[worst_name].get("stddevResilienceScore", 0)
        # Overlap margin: mean of both stddevs (at least 5 to handle zero-variance)
        margin = max(5.0, (colocate_sd + worst_sd) / 2)

        # Check CPU contention
        colocate_cpu = ""
        if resource_data and "colocate" in resource_data:
            cpu = resource_data["colocate"].get("phases", {}).get(
                "during-chaos", {}
            ).get("node", {}).get("meanCpu_percent")
            if cpu is not None:
                other_cpus = []
                for s, rd in resource_data.items():
                    if s not in ("colocate", "baseline"):
                        oc = rd.get("phases", {}).get("during-chaos", {}).get(
                            "node", {}
                        ).get("meanCpu_percent")
                        if oc is not None:
                            other_cpus.append(oc)
                avg_other = sum(other_cpus) / len(other_cpus) if other_cpus else 0
                colocate_cpu = (
                    f" Colocate CPU during chaos: {cpu:.1f}% vs "
                    f"other strategies avg: {avg_other:.1f}%."
                )

        if worst_name == "colocate":
            h1_status = "supported"
            h1_color = "#2ECC71"
            h1_detail = (
                f"Colocate scored {colocate_score:.1f} — worst among all strategies.{colocate_cpu}"
            )
        elif colocate_score <= worst_score + margin:
            h1_status = "partially supported"
            h1_color = "#F39C12"
            h1_detail = (
                f"Colocate scored {colocate_score:.1f}, near worst ({worst_name}: "
                f"{worst_score:.1f}) within noise margin ±{margin:.0f}.{colocate_cpu}"
            )
        else:
            h1_status = "refuted"
            h1_color = "#E74C3C"
            h1_detail = (
                f"Colocate scored {colocate_score:.1f}, but {worst_name} scored "
                f"{worst_score:.1f} (worst). Probe timing may dominate over resource "
                f"contention effects.{colocate_cpu}"
            )
    else:
        h1_status = "inconclusive"
        h1_color = "#95A5A6"
        h1_detail = "Insufficient strategies to evaluate."

    # H2: Spread has best resilience?
    spread = strategies.get("spread", {})
    spread_score = spread.get("avgResilienceScore", 0)
    if non_baseline:
        spread_sd = spread.get("stddevResilienceScore", 0)
        best_sd = non_baseline[best_name].get("stddevResilienceScore", 0)
        h2_margin = max(5.0, (spread_sd + best_sd) / 2)

        if best_name == "spread":
            h2_status = "supported"
            h2_color = "#2ECC71"
            h2_detail = f"Spread scored {spread_score:.1f} — best among all strategies."
        elif spread_score >= best_score - h2_margin:
            h2_status = "partially supported"
            h2_color = "#F39C12"
            h2_detail = (
                f"Spread scored {spread_score:.1f}, near best ({best_name}: "
                f"{best_score:.1f}) within noise margin ±{h2_margin:.0f}."
            )
        else:
            h2_status = "refuted"
            h2_color = "#E74C3C"
            h2_detail = (
                f"Spread scored {spread_score:.1f}, but {best_name} scored "
                f"{best_score:.1f} (best). Score variance (stddev="
                f"{spread.get('stddevResilienceScore', 0):.1f}) suggests probe-timing "
                f"noise exceeds the placement signal."
            )
    else:
        h2_status = "inconclusive"
        h2_color = "#95A5A6"
        h2_detail = "Insufficient strategies to evaluate."

    return f"""
    <h2>Hypothesis Evaluation</h2>
    <div class="dimension">
        <div class="hypothesis h1" style="border-left-color: {h1_color};">
            <span class="label" style="color:{h1_color}">H1</span>
            <span><strong style="color:{h1_color}">{h1_status.upper()}</strong> &mdash; {h1_detail}</span>
        </div>
        <div class="hypothesis h2" style="border-left-color: {h2_color};">
            <span class="label" style="color:{h2_color}">H2</span>
            <span><strong style="color:{h2_color}">{h2_status.upper()}</strong> &mdash; {h2_detail}</span>
        </div>
        <div class="hypothesis h3" style="border-left-color: {h3_color};">
            <span class="label" style="color:{h3_color}">H3</span>
            <span><strong style="color:{h3_color}">{h3_status.upper()}</strong> &mdash; {h3_detail}</span>
        </div>
    </div>"""


def _build_iteration_table(
    raw_strategies: Dict[str, Any],
    iterations: int,
) -> str:
    """Build per-iteration score breakdown table."""
    if iterations <= 1:
        return ""

    header_cells = "".join(f"<th>Iter {i + 1}</th>" for i in range(iterations))
    rows = ""
    for name in sorted(raw_strategies.keys()):
        sdata = raw_strategies[name]
        iters = sdata.get("iterations", [])
        cells = ""
        for it in iters:
            s = it.get("resilienceScore", 0)
            tainted = not it.get("preChaosHealthy", True)
            if s >= 80:
                bg = "#d4edda"
            elif s >= 50:
                bg = "#fff3cd"
            else:
                bg = "#f8d7da"
            duration = (it.get("metrics") or {}).get("timeWindow", {}).get("duration_s")
            dur_str = f"{duration:.1f}s" if isinstance(duration, (int, float)) else "—"
            taint_marker = ' <span title="Pre-chaos baseline was degraded" style="color:#E74C3C; cursor:help;">&#x26A0;</span>' if tainted else ""
            cells += (
                f'<td style="background:{bg}; text-align:center;">'
                f'{s:.0f}{taint_marker}<br><span style="color:#555; font-size:0.8em;">{dur_str}</span>'
                f'</td>'
            )
        # Pad if fewer iterations
        for _ in range(iterations - len(iters)):
            cells += "<td>n/a</td>"
        rows += f"<tr><td>{name}</td>{cells}</tr>\n"

    return f"""
    <h2>Per-Iteration Score Breakdown</h2>
    <div class="dimension">
        <table>
            <tr><th>Strategy</th>{header_cells}</tr>
            {rows}
        </table>
        <p style="color:#666; font-size:0.85em;">
            Each cell shows resilience score and iteration duration.
            Cells: <span style="background:#d4edda; padding:2px 6px;">&ge;80</span>
            <span style="background:#fff3cd; padding:2px 6px;">50&ndash;79</span>
            <span style="background:#f8d7da; padding:2px 6px;">&lt;50</span>
            &ensp;<span style="color:#E74C3C;">&#x26A0;</span> = pre-chaos baseline was degraded
            (score may reflect accumulated damage, not strategy resilience).
        </p>
    </div>"""


def _build_placement_table(raw_strategies: Dict[str, Any]) -> str:
    """Build placement topology table showing pod-to-node assignments."""
    # Collect all deployments and nodes across strategies
    all_deployments: set = set()
    strategy_placements: Dict[str, Dict[str, str]] = {}

    for name, sdata in raw_strategies.items():
        placement = sdata.get("placement", {})
        assignments = placement.get("assignments", {})
        if assignments:
            strategy_placements[name] = assignments
            all_deployments.update(assignments.keys())

    if not strategy_placements:
        return ""

    deployments = sorted(all_deployments)
    strat_names = sorted(strategy_placements.keys())

    # Build node color map for visual grouping
    all_nodes = set()
    for assigns in strategy_placements.values():
        all_nodes.update(assigns.values())
    node_list = sorted(all_nodes)
    node_colors = ["#E3F2FD", "#FFF3E0", "#E8F5E9", "#F3E5F5", "#FBE9E7",
                    "#E0F7FA", "#FFF9C4", "#F1F8E9"]

    header = "".join(f"<th>{s}</th>" for s in strat_names)
    rows = ""
    for dep in deployments:
        cells = ""
        for strat in strat_names:
            node = strategy_placements.get(strat, {}).get(dep, "—")
            idx = node_list.index(node) if node in node_list else 0
            bg = node_colors[idx % len(node_colors)]
            cells += f'<td style="background:{bg}; font-size:0.85em;">{node}</td>'
        rows += f"<tr><td style='font-size:0.85em;'>{dep}</td>{cells}</tr>\n"

    return f"""
    <h2>Placement Topology</h2>
    <div class="dimension">
        <p>Pod-to-node assignments per strategy. Color groups pods on the same node.</p>
        <table>
            <tr><th>Deployment</th>{header}</tr>
            {rows}
        </table>
    </div>"""



def _generate_html_summary(
    chart_paths: List[str],
    strategies: Dict[str, Any],
    output_path: Path,
    iterations: int = 1,
    latency_data: Optional[Dict[str, Dict[str, Any]]] = None,
    throughput_data: Optional[Dict[str, Dict[str, Any]]] = None,
    resource_data: Optional[Dict[str, Dict[str, Any]]] = None,
    prometheus_data: Optional[Dict[str, Dict[str, Any]]] = None,
    raw_strategies: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Generate an HTML page with embedded charts and summary table."""
    if not chart_paths:
        return None

    rows = ""
    for name, data in sorted(strategies.items()):
        avg_rec = data.get("avgMeanRecovery_ms")
        avg_rec_str = f"{avg_rec:.1f}" if avg_rec is not None else "n/a"
        median_rec = data.get("medianRecovery_ms")
        median_str = f"{median_rec:.1f}" if median_rec is not None else "n/a"
        p95_rec = data.get("avgP95Recovery_ms")
        p95_str = f"{p95_rec:.1f}" if p95_rec is not None else "n/a"
        run_count = data.get("runCount", iterations)
        stddev = data.get("stddevResilienceScore", 0.0)
        stddev_str = f"{stddev:.1f}" if stddev else "0.0"
        min_s = data.get("minResilienceScore")
        max_s = data.get("maxResilienceScore")
        range_str = f"{min_s:.0f}&ndash;{max_s:.0f}" if min_s is not None and max_s is not None else "n/a"
        rows += f"""
        <tr>
            <td>{name}</td>
            <td>{run_count}</td>
            <td>{data.get('avgResilienceScore', 0):.1f}</td>
            <td>&plusmn;{stddev_str}</td>
            <td>{range_str}</td>
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
                    bps_str = (
                        f"{during_bps / 1024 / 1024:.1f} MB/s" if during_bps is not None else "n/a"
                    )

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

    resource_section = ""
    if resource_data:
        resource_rows = ""
        for strat_name in sorted(resource_data.keys()):
            phases = resource_data[strat_name].get("phases", {})
            for phase_name in ("pre-chaos", "during-chaos", "post-chaos"):
                nd = phases.get(phase_name, {}).get("node", {})
                cpu = nd.get("meanCpu_percent")
                mem = nd.get("meanMemory_percent")
                cpu_str = f"{cpu:.1f}" if cpu is not None else "n/a"
                mem_str = f"{mem:.1f}" if mem is not None else "n/a"
                samples = phases.get(phase_name, {}).get("sampleCount", 0)
                resource_rows += f"""
            <tr>
                <td>{strat_name}</td>
                <td>{phase_name}</td>
                <td>{cpu_str}</td>
                <td>{mem_str}</td>
                <td>{samples}</td>
            </tr>"""

        resource_section = f"""
    <h2>Node Resource Utilization</h2>
    <table>
        <tr>
            <th>Strategy</th>
            <th>Phase</th>
            <th>Mean CPU (%)</th>
            <th>Mean Memory (%)</th>
            <th>Samples</th>
        </tr>
        {resource_rows}
    </table>"""

    prometheus_section = ""
    if prometheus_data:
        prom_rows = ""
        # Collect all metric labels across strategies
        all_labels: set = set()
        for pdata in prometheus_data.values():
            phases = pdata.get("phases", {})
            for phase_info in phases.values():
                all_labels.update(phase_info.get("metrics", {}).keys())

        for strat_name in sorted(prometheus_data.keys()):
            phases = prometheus_data[strat_name].get("phases", {})
            for label in sorted(all_labels):
                for phase_name in ("pre-chaos", "during-chaos", "post-chaos"):
                    phase_metrics = phases.get(phase_name, {}).get("metrics", {})
                    metric = phase_metrics.get(label, {})
                    mean_val = metric.get("mean")
                    max_val = metric.get("max")
                    mean_str = f"{mean_val:.4f}" if mean_val is not None else "n/a"
                    max_str = f"{max_val:.4f}" if max_val is not None else "n/a"
                    samples = phases.get(phase_name, {}).get("sampleCount", 0)
                    prom_rows += f"""
            <tr>
                <td>{strat_name}</td>
                <td>{label}</td>
                <td>{phase_name}</td>
                <td>{mean_str}</td>
                <td>{max_str}</td>
                <td>{samples}</td>
            </tr>"""

        prometheus_section = f"""
    <h2>Prometheus Cluster Metrics</h2>
    <table>
        <tr>
            <th>Strategy</th>
            <th>Metric</th>
            <th>Phase</th>
            <th>Mean</th>
            <th>Max</th>
            <th>Samples</th>
        </tr>
        {prom_rows}
    </table>"""

    img_tags = ""
    # Group charts by thesis section for structured display
    chart_sections: Dict[str, List[str]] = {
        "overview": [],
        "recovery": [],
        "latency": [],
        "resources": [],
        "throughput": [],
        "prometheus": [],
    }
    for path in chart_paths:
        if not path.endswith(".png"):
            continue
        fname = Path(path).name
        if "heatmap" in fname or "resilience" in fname:
            chart_sections["overview"].append(fname)
        elif "recovery" in fname:
            chart_sections["recovery"].append(fname)
        elif "latency" in fname:
            chart_sections["latency"].append(fname)
        elif "resource" in fname:
            chart_sections["resources"].append(fname)
        elif "throughput" in fname:
            chart_sections["throughput"].append(fname)
        elif "prometheus" in fname:
            chart_sections["prometheus"].append(fname)
        else:
            chart_sections["overview"].append(fname)

    def _img_tags_for(section: str) -> str:
        return "\n".join(
            f'<img src="{f}" style="max-width:100%; margin:10px 0;">'
            for f in chart_sections.get(section, [])
        )

    # --- Hypothesis evaluation section ---
    hypothesis_section = _build_hypothesis_evaluation(strategies, resource_data)

    # --- Per-iteration score breakdown ---
    iteration_section = _build_iteration_table(raw_strategies or {}, iterations)

    # --- Placement topology ---
    placement_section = _build_placement_table(raw_strategies or {})

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>ChaosProbe — Thesis Experiment Report</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               margin: 0; padding: 0; background: #f5f5f5; color: #333; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 40px 20px; }}
        h1 {{ color: #1a1a2e; border-bottom: 3px solid #0096D6; padding-bottom: 10px; }}
        h2 {{ color: #1a1a2e; margin-top: 40px; border-left: 4px solid #0096D6; padding-left: 12px; }}
        h3 {{ color: #555; }}
        .rq {{ background: #e8f4fd; border-left: 4px solid #0096D6; padding: 15px 20px;
               margin: 20px 0; border-radius: 4px; font-style: italic; font-size: 1.1em; }}
        .hypothesis {{ display: flex; gap: 15px; margin: 10px 0; padding: 12px 16px;
                       background: white; border-radius: 6px; border-left: 4px solid #ccc;
                       box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .hypothesis.h1 {{ border-left-color: #E74C3C; }}
        .hypothesis.h2 {{ border-left-color: #2ECC71; }}
        .hypothesis.h3 {{ border-left-color: #7F8C8D; }}
        .hypothesis .label {{ font-weight: bold; min-width: 30px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 15px 0; background: white;
                 box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-radius: 6px; overflow: hidden; }}
        th, td {{ border: 1px solid #e0e0e0; padding: 10px 14px; text-align: left; }}
        th {{ background: #1a1a2e; color: white; font-weight: 600; }}
        tr:nth-child(even) {{ background: #f9f9f9; }}
        .section-charts {{ margin: 20px 0; }}
        .section-charts img {{ border: 1px solid #ddd; border-radius: 6px;
                               box-shadow: 0 2px 4px rgba(0,0,0,0.1); max-width: 100%; }}
        .dimension {{ background: white; border-radius: 8px; padding: 20px 25px; margin: 20px 0;
                      box-shadow: 0 2px 6px rgba(0,0,0,0.08); }}
        .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #ddd;
                   color: #888; font-size: 0.9em; }}
    </style>
</head>
<body>
<div class="container">
    <h1>ChaosProbe — Thesis Experiment Report</h1>

    <div class="rq">
        <strong>RQ:</strong> How does pod placement topology affect microservice resilience
        under fault injection in Kubernetes?
    </div>

    <div class="hypothesis h1">
        <span class="label" style="color:#E74C3C">H1</span>
        <span>Maximum contention (colocate) degrades resilience — colocating all pods on a
        single node maximizes resource contention and produces the worst resilience scores.</span>
    </div>
    <div class="hypothesis h2">
        <span class="label" style="color:#2ECC71">H2</span>
        <span>Spreading improves fault isolation — distributing pods across nodes minimizes
        per-node contention and yields the best resilience scores.</span>
    </div>
    <div class="hypothesis h3">
        <span class="label" style="color:#7F8C8D">H3</span>
        <span>Baseline validates methodology — a trivial fault with default scheduling should
        produce 100% resilience, confirming measurement validity.</span>
    </div>

    <!-- ═══ Overview ═══ -->
    <h2>Strategy Comparison Overview</h2>
    <div class="dimension">
        <table>
            <tr>
                <th>Strategy</th>
                <th>Runs</th>
                <th>Resilience Score</th>
                <th>Std Dev</th>
                <th>Range</th>
                <th>Pass Rate</th>
                <th>Mean Recovery (ms)</th>
                <th>Median Recovery (ms)</th>
                <th>P95 Recovery (ms)</th>
            </tr>
            {rows}
        </table>
        <p style="color:#666; font-size:0.85em; margin-top:8px;">
            <strong>Note:</strong> Resilience scores blend LitmusChaos probe verdicts (25%)
            with continuous metrics: recovery speed (25%), latency preservation (25%),
            error rate (15%), and throughput preservation (10%). This produces finer-grained
            differentiation than probe verdicts alone.
        </p>
        <div class="section-charts">{_img_tags_for("overview")}</div>
    </div>

    <!-- ═══ Hypothesis Evaluation ═══ -->
    {hypothesis_section}

    <!-- ═══ Per-Iteration Breakdown ═══ -->
    {iteration_section}

    <!-- ═══ Placement Topology ═══ -->
    {placement_section}

    <!-- ═══ Dimension 1: Recovery Time ═══ -->
    <h2>Dimension 1 — Recovery Time</h2>
    <div class="dimension">
        <p>Time from pod deletion to pod ready, measured via the Kubernetes Watch API.
        Lower recovery time indicates better fault tolerance under the given placement.</p>
        <div class="section-charts">{_img_tags_for("recovery")}</div>
    </div>

    <!-- ═══ Dimension 2: Inter-Service Latency ═══ -->
    <h2>Dimension 2 — Inter-Service Latency</h2>
    <div class="dimension">
        <p>HTTP route latency measured via <code>kubectl exec</code> using python3/wget probes.
        Degradation from pre-chaos to during-chaos quantifies fault impact on service communication.</p>
        {latency_section}
        <div class="section-charts">{_img_tags_for("latency")}</div>
    </div>

    <!-- ═══ Dimension 3: Resource Utilization ═══ -->
    <h2>Dimension 3 — Resource Utilization</h2>
    <div class="dimension">
        <p>Node-level CPU and memory utilization from the Kubernetes Metrics API.
        Higher utilization during chaos correlates with resource contention from co-location.</p>
        {resource_section}
        <div class="section-charts">{_img_tags_for("resources")}</div>
    </div>

    <!-- ═══ Dimension 4: I/O Throughput ═══ -->
    <h2>Dimension 4 — I/O Throughput</h2>
    <div class="dimension">
        <p>Redis ops/s and sequential disk read/write bandwidth measured via
        <code>redis-cli</code> and <code>dd</code>. Throughput degradation during chaos
        reflects shared I/O contention on co-located nodes.</p>
        {throughput_section}
        <div class="section-charts">{_img_tags_for("throughput")}</div>
    </div>

    <!-- ═══ Prometheus Cluster Metrics ═══ -->
    <h2>Supplementary — Prometheus Cluster Metrics</h2>
    <div class="dimension">
        <p>Pod readiness, CPU throttling, memory working set, and network receive bytes
        collected via PromQL. These metrics supplement the primary four dimensions.</p>
        {prometheus_section}
        <div class="section-charts">{_img_tags_for("prometheus")}</div>
    </div>

    <div class="footer">
        Generated by ChaosProbe &middot;
        {iterations} iteration{"s" if iterations != 1 else ""} per strategy &middot;
        4 metric dimensions &middot; {len(strategies)} strategies evaluated
    </div>
</div>
</body>
</html>"""

    filepath = str(output_path / "report.html")
    with open(filepath, "w") as f:
        f.write(html)
    return filepath
