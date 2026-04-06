"""Execute a single placement strategy (placement + N iterations).

Extracted from ``cli.py run()`` to keep the top-level command lean.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import click

from chaosprobe.chaos.runner import ChaosRunner
from chaosprobe.collector.result_collector import ResultCollector
from chaosprobe.loadgen.runner import LoadProfile, LocustRunner
from chaosprobe.metrics.collector import MetricsCollector
from chaosprobe.orchestrator import portforward as pf
from chaosprobe.orchestrator.preflight import (
    LITMUS_INFRA_DEPLOYMENTS,
    wait_for_healthy_deployments,
)
from chaosprobe.orchestrator.probers import (
    create_and_start_probers,
    stop_and_collect_probers,
)
from chaosprobe.orchestrator.run_phases import aggregate_iterations
from chaosprobe.output.generator import OutputGenerator
from chaosprobe.placement.mutator import PlacementMutator
from chaosprobe.placement.strategy import PlacementStrategy


# ---------------------------------------------------------------------------
# Context bundle — avoids threading 25+ parameters through every call
# ---------------------------------------------------------------------------

@dataclass
class RunContext:
    """Immutable-ish bundle of everything a strategy execution needs."""

    namespace: str
    timeout: int
    seed: int
    settle_time: int
    iterations: int
    baseline_duration: int

    measure_latency: bool
    measure_redis: bool
    measure_disk: bool
    measure_resources: bool
    measure_prometheus: bool
    prometheus_url: Tuple[str, ...]
    collect_logs: bool
    load_profile: Optional[str]
    locustfile: Optional[str]
    target_url: Optional[str]

    neo4j_uri: Optional[str]
    neo4j_user: str
    neo4j_password: str

    shared_scenario: Dict[str, Any]
    service_routes: Optional[Any]
    target_deployment: str

    core_api: Any  # kubernetes CoreV1Api
    chaoscenter_config: Optional[Dict[str, Any]]
    frontend_pf_port: Optional[int]

    metrics_collector: MetricsCollector
    mutator: PlacementMutator
    graph_store: Any  # Optional[Neo4jStore]
    ts: str  # session timestamp


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def execute_strategy(
    ctx: RunContext,
    strategy_name: str,
    idx: int,
    total: int,
) -> Tuple[Dict[str, Any], bool]:
    """Run one placement strategy (all iterations).

    Returns ``(strategy_result_dict, passed)`` where *passed* is True when
    all iterations pass.
    """
    click.echo(f"\n{'─' * 60}")
    click.echo(f"[{idx}/{total}] Strategy: {strategy_name}")
    click.echo(f"{'─' * 60}")

    strategy_result: Dict[str, Any] = {
        "strategy": strategy_name,
        "status": "pending",
        "placement": None,
        "experiment": None,
        "metrics": None,
        "error": None,
    }

    try:
        _apply_placement(ctx, strategy_name, strategy_result)
        iteration_results = _run_iterations(ctx, strategy_name, strategy_result)
        passed = _aggregate_strategy(ctx, strategy_name, strategy_result, iteration_results)
    except Exception as e:
        click.echo(f"\n    ERROR: {e}", err=True)
        strategy_result["status"] = "error"
        strategy_result["error"] = str(e)
        passed = False

    return strategy_result, passed


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _apply_placement(
    ctx: RunContext, strategy_name: str, strategy_result: Dict[str, Any],
) -> None:
    click.echo("\n  Step 1: Clearing existing placement...")
    ctx.mutator.clear_placement(wait=True)
    click.echo("    Placement cleared.")

    if strategy_name == "baseline":
        click.echo("\n  Step 2: Baseline — using default scheduling")
        strategy_result["placement"] = {
            "strategy": "baseline",
            "description": "Default Kubernetes scheduling",
        }
    else:
        click.echo(f"\n  Step 2: Applying {strategy_name} placement...")
        strat = PlacementStrategy(strategy_name)
        _infra_prefixes = (
            "chaos-exporter", "chaos-operator", "event-tracker",
            "subscriber", "workflow-controller",
        )
        all_deps = ctx.mutator.get_deployments()
        app_deps = [
            d.name for d in all_deps if not d.name.startswith(_infra_prefixes)
        ]
        assignment = ctx.mutator.apply_strategy(
            strategy=strat,
            seed=ctx.seed if strategy_name == "random" else None,
            deployments=app_deps if app_deps else None,
            wait=True,
            timeout=ctx.timeout,
        )
        strategy_result["placement"] = assignment.to_dict()

        nodes_used = set(assignment.assignments.values())
        click.echo(
            f"    Placed {len(assignment.assignments)} deployments "
            f"across {len(nodes_used)} node(s)"
        )
        for node in sorted(nodes_used):
            count = sum(1 for n in assignment.assignments.values() if n == node)
            click.echo(f"      {node}: {count} deployment(s)")


def _ensure_port_forwards(ctx: RunContext) -> None:
    """Re-check infrastructure port-forwards between iterations."""
    if ctx.neo4j_uri:
        pf.ensure("neo4j", "neo4j", ["7687:7687", "7474:7474"], "localhost", 7687)

    if ctx.measure_prometheus:
        for _pns in ("monitoring", "prometheus"):
            proc = pf._procs.get(("prometheus-server", _pns))
            if proc:
                pf.ensure(
                    "prometheus-server", _pns,
                    ["9090:9090"], "localhost", 9090,
                )
                break

    if (
        ctx.load_profile
        and ctx.target_url
        and ctx.target_url.startswith("http://localhost:")
    ):
        try:
            svc_list = ctx.core_api.list_namespaced_service(ctx.namespace)
            for svc in svc_list.items:
                if (
                    "frontend" in svc.metadata.name
                    and "external" not in svc.metadata.name
                ):
                    pf.ensure(
                        svc.metadata.name, ctx.namespace,
                        [f"{ctx.frontend_pf_port}:80"],
                        "localhost", ctx.frontend_pf_port,
                    )
                    break
        except Exception:
            pass


def _run_single_iteration(
    ctx: RunContext,
    strategy_name: str,
    strategy_result: Dict[str, Any],
    iter_num: int,
) -> Dict[str, Any]:
    """Execute one chaos iteration and return its result dict."""
    if ctx.iterations > 1:
        click.echo(f"\n  ── Iteration {iter_num}/{ctx.iterations} ──")

    step_label = "  Step 3" if ctx.iterations == 1 else "    Step A"
    if ctx.settle_time > 0:
        click.echo(f"\n{step_label}: Waiting {ctx.settle_time}s for workloads to settle...")
        time.sleep(ctx.settle_time)

    click.echo("    Verifying deployment readiness...")
    wait_for_healthy_deployments(ctx.namespace, timeout=60)
    _ensure_port_forwards(ctx)
    click.echo("    Ready.")

    # Prepare experiment scenario
    step_label = "  Step 4" if ctx.iterations == 1 else "    Step B"
    click.echo(f"\n{step_label}: Running experiment...")

    scenario = copy.deepcopy(ctx.shared_scenario)
    for exp in scenario.get("experiments", []):
        orig_name = exp["spec"].get("metadata", {}).get("name", "placement-pod-delete")
        suffix = f"-{strategy_name}-i{iter_num}" if ctx.iterations > 1 else f"-{strategy_name}"
        exp["spec"]["metadata"]["name"] = f"{orig_name}{suffix}"

    # Start probers + optional load generation
    probers = create_and_start_probers(
        ctx.namespace,
        ctx.target_deployment,
        measure_latency=ctx.measure_latency,
        measure_redis=ctx.measure_redis,
        measure_disk=ctx.measure_disk,
        measure_resources=ctx.measure_resources,
        measure_prometheus=ctx.measure_prometheus,
        prometheus_url=ctx.prometheus_url,
    )

    iter_locust_runner = None
    if ctx.load_profile:
        profile = LoadProfile.from_name(ctx.load_profile)
        click.echo(f"    Starting Locust ({ctx.load_profile}: {profile.users} users)")
        iter_locust_runner = LocustRunner(target_url=ctx.target_url, locustfile=ctx.locustfile)
        iter_locust_runner.start(profile)

    try:
        # Pre-chaos baseline
        pre_chaos_window = (
            ctx.baseline_duration if ctx.baseline_duration > 0 else min(ctx.settle_time, 15)
        )
        has_probers = any(
            probers.get(k)
            for k in ("latency", "redis", "disk", "resource", "prometheus")
        )
        if has_probers and pre_chaos_window > 0:
            click.echo(f"    Collecting pre-chaos baseline ({pre_chaos_window}s)...")
            time.sleep(pre_chaos_window)

        # Run experiment
        experiment_start = time.time()
        for p in probers.values():
            if p and hasattr(p, "mark_chaos_start"):
                p.mark_chaos_start()
        runner = ChaosRunner(
            ctx.namespace, timeout=ctx.timeout, chaoscenter=ctx.chaoscenter_config,
        )
        runner.run_experiments(scenario.get("experiments", []))
        experiment_end = time.time()
        for p in probers.values():
            if p and hasattr(p, "mark_chaos_end"):
                p.mark_chaos_end()

        # Post-chaos recovery
        post_chaos_window = min(ctx.settle_time, 15)
        if has_probers and post_chaos_window > 0:
            click.echo(f"    Collecting post-chaos samples ({post_chaos_window}s)...")
            time.sleep(post_chaos_window)
    finally:
        prober_results = stop_and_collect_probers(probers, iter_locust_runner)

    # Collect results & metrics
    collector = ResultCollector(ctx.namespace)
    executed = runner.get_executed_experiments()
    results = collector.collect(executed)

    recovery = ctx.metrics_collector.collect(
        deployment_name=ctx.target_deployment,
        since_time=experiment_start,
        until_time=experiment_end,
        recovery_data=prober_results.get("recovery"),
        latency_data=prober_results.get("latency"),
        redis_data=prober_results.get("redis"),
        disk_data=prober_results.get("disk"),
        resource_data=prober_results.get("resource"),
        prometheus_data=prober_results.get("prometheus"),
        collect_logs=ctx.collect_logs,
    )

    # Generate output
    placement_info = strategy_result.get("placement") or {
        "strategy": strategy_name,
        "seed": ctx.seed if strategy_name == "random" else None,
        "assignments": {},
    }
    generator = OutputGenerator(
        scenario, results, metrics=recovery,
        placement=placement_info, service_routes=ctx.service_routes,
    )
    output_data = generator.generate()
    output_data["placement"] = placement_info
    output_data["sessionId"] = ctx.ts

    if prober_results.get("load_stats"):
        output_data["loadGeneration"] = {
            "profile": ctx.load_profile,
            "stats": prober_results["load_stats"].to_dict(),
        }

    # Sync to Neo4j
    if ctx.graph_store:
        _sync_neo4j(ctx, output_data)

    verdict = output_data.get("summary", {}).get("overallVerdict", "UNKNOWN")
    score = output_data.get("summary", {}).get("resilienceScore", 0)
    rec_summary = recovery.get("recovery", {}).get("summary", {})
    avg_recovery = rec_summary.get("meanRecovery_ms")
    recovery_str = f" | Avg Recovery: {avg_recovery:.0f}ms" if avg_recovery else ""

    click.echo(f"\n    Results synced to Neo4j (run: {output_data.get('runId', '')})")
    click.echo(f"    Verdict: {verdict} | Resilience Score: {score:.1f}{recovery_str}")

    return {
        "iteration": iter_num,
        "verdict": verdict,
        "resilienceScore": score,
        "metrics": recovery,
        "runId": output_data.get("runId", ""),
    }


def _run_iterations(
    ctx: RunContext,
    strategy_name: str,
    strategy_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Run all iterations for one strategy."""
    return [
        _run_single_iteration(ctx, strategy_name, strategy_result, i)
        for i in range(1, ctx.iterations + 1)
    ]


def _aggregate_strategy(
    ctx: RunContext,
    strategy_name: str,
    strategy_result: Dict[str, Any],
    iteration_results: List[Dict[str, Any]],
) -> bool:
    """Fill in strategy_result with aggregated data. Return True if passed."""
    if ctx.iterations > 1:
        strategy_result["iterations"] = iteration_results
        strategy_result["aggregated"] = aggregate_iterations(iteration_results)
        strategy_result["experiment"] = strategy_result["aggregated"]
        strategy_result["status"] = "completed"

        agg = strategy_result["aggregated"]
        iter_passed = sum(1 for ir in iteration_results if ir["verdict"] == "PASS")
        click.echo(
            f"\n    Aggregated: {iter_passed}/{ctx.iterations} passed | "
            f"Mean Score: {agg['meanResilienceScore']:.1f}"
        )
        if agg.get("meanRecoveryTime_ms") is not None:
            click.echo(
                f"    Mean Recovery: {agg['meanRecoveryTime_ms']:.0f}ms | "
                f"Max: {agg['maxRecoveryTime_ms']:.0f}ms"
            )
        return agg["passRate"] == 1.0
    else:
        ir = iteration_results[0]
        strategy_result["experiment"] = {
            "overallVerdict": ir["verdict"],
            "resilienceScore": ir["resilienceScore"],
            "passed": 1 if ir["verdict"] == "PASS" else 0,
            "failed": 0 if ir["verdict"] == "PASS" else 1,
            "totalExperiments": 1,
        }
        strategy_result["metrics"] = ir["metrics"]
        strategy_result["status"] = "completed"
        strategy_result["runId"] = ir["runId"]
        return ir["verdict"] == "PASS"


def _sync_neo4j(ctx: RunContext, output_data: Dict[str, Any]) -> None:
    """Sync run data to Neo4j with retry logic."""
    for attempt in range(3):
        try:
            ctx.graph_store.sync_run(output_data)
            return
        except Exception as e:
            if attempt < 2:
                click.echo(
                    f"    Neo4j sync attempt {attempt + 1} failed, reconnecting...",
                    err=True,
                )
                pf.ensure(
                    "neo4j", "neo4j",
                    ["7687:7687", "7474:7474"], "localhost", 7687,
                )
                try:
                    ctx.graph_store.close()
                except Exception:
                    pass
                try:
                    from chaosprobe.storage.neo4j_store import Neo4jStore
                    ctx.graph_store = Neo4jStore(
                        ctx.neo4j_uri, ctx.neo4j_user, ctx.neo4j_password,
                    )
                except Exception:
                    time.sleep(5)
                    continue
            else:
                import traceback
                click.echo(
                    f"    Warning: Neo4j sync failed after 3 attempts: {e}",
                    err=True,
                )
                click.echo(traceback.format_exc(), err=True)
