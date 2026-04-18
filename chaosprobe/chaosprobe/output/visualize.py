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
        # Recovery metrics live in experiment for multi-iteration (aggregated),
        # but in metrics.recovery.summary for single-iteration runs.
        rec_summary = (sdata.get("metrics") or {}).get("recovery", {}).get("summary", {})
        strategies[name] = {
            "avgResilienceScore": exp.get("meanResilienceScore", exp.get("resilienceScore", 0)),
            "passRate": exp.get("passRate", 0.0),
            "avgMeanRecovery_ms": (
                exp.get("meanRecoveryTime_ms")
                if exp.get("meanRecoveryTime_ms") is not None
                else rec_summary.get("meanRecovery_ms")
            ),
            "avgP95Recovery_ms": (
                exp.get("maxRecoveryTime_ms")
                if exp.get("maxRecoveryTime_ms") is not None
                else rec_summary.get("p95Recovery_ms")
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
    )
    if html_path:
        generated.append(html_path)

    return generated


def _generate_html_summary(
    chart_paths: List[str],
    strategies: Dict[str, Any],
    output_path: Path,
    iterations: int = 1,
    latency_data: Optional[Dict[str, Dict[str, Any]]] = None,
    throughput_data: Optional[Dict[str, Dict[str, Any]]] = None,
    resource_data: Optional[Dict[str, Dict[str, Any]]] = None,
    prometheus_data: Optional[Dict[str, Dict[str, Any]]] = None,
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
                <th>Pass Rate</th>
                <th>Mean Recovery (ms)</th>
                <th>Median Recovery (ms)</th>
                <th>P95 Recovery (ms)</th>
            </tr>
            {rows}
        </table>
        <div class="section-charts">{_img_tags_for("overview")}</div>
    </div>

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
