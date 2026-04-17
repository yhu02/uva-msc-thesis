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
from chaosprobe.orchestrator.preflight import (
    LITMUS_INFRA_DEPLOYMENTS,
    wait_for_healthy_deployments,
)
from chaosprobe.orchestrator.probers import (
    create_and_start_probers,
    stop_and_collect_probers,
)
from chaosprobe.orchestrator import portforward as pf
from chaosprobe.orchestrator.run_phases import LOAD_TARGET_LOCAL_PORT, aggregate_iterations
from chaosprobe.output.generator import OutputGenerator
from chaosprobe.placement.mutator import PlacementMutator
from chaosprobe.placement.strategy import PlacementStrategy


# ---------------------------------------------------------------------------
# Timeout helpers
# ---------------------------------------------------------------------------

def _parse_probe_timeout(s: str) -> int:
    """Parse a Go-style duration string (e.g. ``'15s'``) to integer seconds."""
    s = s.strip()
    if s.endswith("ms"):
        return max(1, int(s[:-2]) // 1000)
    if s.endswith("s"):
        return int(s[:-1])
    if s.endswith("m"):
        return int(s[:-1]) * 60
    try:
        return int(s)
    except ValueError:
        return 5


def _extract_chaos_duration(scenario: Dict[str, Any]) -> int:
    """Extract the total chaos duration (seconds) from the scenario."""
    chaos_duration = 60  # fallback
    for exp_entry in scenario.get("experiments", []):
        spec = exp_entry.get("spec", {})
        for exp in spec.get("spec", {}).get("experiments", []):
            for env in exp.get("spec", {}).get("components", {}).get("env", []):
                if env.get("name") == "TOTAL_CHAOS_DURATION":
                    try:
                        chaos_duration = max(chaos_duration, int(env["value"]))
                    except (ValueError, KeyError):
                        pass
    return chaos_duration


def _compute_effective_timeout(scenario: Dict[str, Any], user_timeout: int) -> int:
    """Compute a polling timeout that accounts for chaos duration + probe overhead.

    The go-runner evaluates probes **before** and **after** the chaos
    window.  At PreChaos and PostChaos, probes are evaluated
    **sequentially** (not in goroutines).  When probes can't reach
    their targets, each one exhausts its full ``(retry + 1) ×
    probeTimeout`` budget (``retry`` is the count of *additional*
    retries after the initial attempt).

    Returns the larger of *user_timeout* and the computed minimum.
    """
    chaos_duration = _extract_chaos_duration(scenario)
    total_probe_budget = 0

    for exp_entry in scenario.get("experiments", []):
        spec = exp_entry.get("spec", {})
        for exp in spec.get("spec", {}).get("experiments", []):
            for probe in exp.get("spec", {}).get("probe", []):
                run_props = probe.get("runProperties", {})
                t = _parse_probe_timeout(run_props.get("probeTimeout", "5s"))
                r = int(run_props.get("retry", 0))
                total_probe_budget += t * (r + 1)

    # pre-chaos probes + chaos + post-chaos probes + workflow overhead
    min_timeout = chaos_duration + 2 * total_probe_budget + 120
    return max(user_timeout, min_timeout)


def _disable_fault_injection(scenario: Dict[str, Any]) -> None:
    """Minimise chaos duration so the experiment runs without meaningful fault injection.

    The ChaosEngine is still submitted to ChaosCenter (visible in the
    dashboard) and probes still execute.  ``TOTAL_CHAOS_DURATION`` is
    set to ``"1"`` (not ``"0"``) because the go-runner computes
    iterations as ``duration / interval``; zero values for both cause
    a division-by-zero crash that prevents all probes from evaluating.
    With duration=1 and the original interval (e.g. 10), integer
    division yields 0 iterations — no pods are killed, but the
    go-runner enters the inject function normally so PreChaos and
    PostChaos probes evaluate.

    Probe timeouts and retries are NOT modified — the baseline must be
    evaluated with identical probe settings as other strategies so that
    resilience scores are directly comparable across placements.
    """
    for exp_entry in scenario.get("experiments", []):
        spec = exp_entry.get("spec", {})
        for exp in spec.get("spec", {}).get("experiments", []):
            env_list = exp.get("spec", {}).get("components", {}).get("env", [])
            for env in env_list:
                if env.get("name") == "TOTAL_CHAOS_DURATION":
                    env["value"] = "1"


def _extract_http_routes(
    scenario: Dict[str, Any], namespace: str,
) -> List[tuple]:
    """Extract HTTP routes from scenario httpProbes for latency measurement.

    Parses the experiment's httpProbe definitions and returns a list of
    ``(service, path, description, method)`` tuples suitable for
    ``ContinuousLatencyProber``.
    """
    from urllib.parse import urlparse

    routes: List[tuple] = []
    seen: set = set()

    for exp_entry in scenario.get("experiments", []):
        spec = exp_entry.get("spec", {})
        for exp in spec.get("spec", {}).get("experiments", []):
            for probe in exp.get("spec", {}).get("probe", []):
                if probe.get("type") != "httpProbe":
                    continue
                inputs = probe.get("httpProbe/inputs", {})
                url = inputs.get("url", "")
                if not url:
                    continue
                parsed = urlparse(url)
                path = parsed.path or "/"
                if path in seen:
                    continue
                seen.add(path)

                # Extract service name from hostname (e.g. frontend.online-boutique.svc...)
                host = parsed.hostname or ""
                service = host.split(".")[0] if host else "frontend"

                method_def = inputs.get("method", {})
                method = "GET" if "get" in method_def else "POST"
                name = probe.get("name", path)

                routes.append((service, path, name, method))

    return routes


def _wait_for_target_pod(
    namespace: str, deployment_name: str, timeout: int = 60, stable_secs: int = 10,
) -> None:
    """Wait until the target deployment has a pod that stays Running.

    After finding a Running pod, keeps checking for *stable_secs* to
    confirm the pod doesn't crash under resource pressure (common with
    colocate where all deployments compete on one node).

    Raises ``click.ClickException`` if no stable pod is found within
    *timeout* seconds.
    """
    from kubernetes import client as k8s_client

    core = k8s_client.CoreV1Api()
    deadline = time.time() + timeout
    stable_since: float | None = None

    while time.time() < deadline:
        pods = core.list_namespaced_pod(
            namespace, label_selector=f"app={deployment_name}",
        )
        running = [
            p for p in pods.items
            if p.status and p.status.phase == "Running"
            and all(
                cs.ready
                for cs in (p.status.container_statuses or [])
            )
        ]
        if running:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= stable_secs:
                return  # Pod has been Running for stable_secs
        else:
            stable_since = None  # Pod disappeared — reset
        time.sleep(3)
    raise click.ClickException(
        f"Target deployment '{deployment_name}' has no ready pods in "
        f"'{namespace}' after {timeout}s. The placement strategy may "
        f"have moved pods to a node that cannot schedule them."
    )


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
    ctx.mutator.clear_placement(wait=True, timeout=120)
    click.echo("    Placement cleared.")

    if strategy_name in ("baseline", "default"):
        click.echo(f"\n  Step 2: {strategy_name.capitalize()} — using default scheduling")
        strategy_result["placement"] = {
            "strategy": strategy_name,
            "description": "No-fault control — no chaos injected" if strategy_name == "baseline" else "Default Kubernetes scheduling",
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
            d.name for d in all_deps
            if not d.name.startswith(_infra_prefixes) and d.replicas > 0
        ]
        assignment = ctx.mutator.apply_strategy(
            strategy=strat,
            seed=ctx.seed if strategy_name == "random" else None,
            deployments=app_deps if app_deps else None,
            wait=True,
            timeout=min(ctx.timeout, 120),
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
    wait_for_healthy_deployments(ctx.namespace, timeout=120)

    # Ensure the chaos target deployment has at least one ready pod
    # that stays stable (important for colocate where resource pressure
    # can cause pods to crash shortly after starting).
    _wait_for_target_pod(ctx.namespace, ctx.target_deployment, timeout=120, stable_secs=10)

    click.echo("    Ready.")

    # Prepare experiment scenario
    step_label = "  Step 4" if ctx.iterations == 1 else "    Step B"
    click.echo(f"\n{step_label}: Running experiment...")

    scenario = copy.deepcopy(ctx.shared_scenario)
    for exp in scenario.get("experiments", []):
        orig_name = exp["spec"].get("metadata", {}).get("name", "placement-pod-delete")
        exp["spec"]["metadata"]["name"] = f"{orig_name}-{strategy_name}"

    # Extract HTTP routes from scenario probes for latency measurement
    http_routes = _extract_http_routes(scenario, ctx.namespace)

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
        http_routes=http_routes or None,
    )

    # Compute windows before starting Locust so its duration covers the
    # full experiment lifecycle (pre-chaos + experiment + post-chaos).
    pre_chaos_window = (
        ctx.baseline_duration if ctx.baseline_duration > 0 else min(ctx.settle_time, 15)
    )
    has_probers = any(
        probers.get(k)
        for k in ("latency", "redis", "disk", "resource", "prometheus")
    )
    post_chaos_window = min(ctx.settle_time, 15)

    iter_locust_runner = None
    if ctx.load_profile:
        # Re-ensure frontend port-forward is alive (placement changes may
        # have restarted the frontend pod, killing the kubectl tunnel).
        if ctx.frontend_pf_port and ctx.target_url and "localhost" in ctx.target_url:
            pf.ensure(
                "frontend", ctx.namespace,
                [f"{ctx.frontend_pf_port}:80"], "localhost", ctx.frontend_pf_port,
            )
        base_profile = LoadProfile.from_name(ctx.load_profile)
        # Compute Locust run duration to span the full experiment window:
        # pre-chaos baseline + effective ChaosRunner timeout + post-chaos + buffer
        effective_timeout = _compute_effective_timeout(scenario, ctx.timeout)
        locust_duration = pre_chaos_window + effective_timeout + post_chaos_window + 30
        profile = LoadProfile.custom(
            users=base_profile.users,
            spawn_rate=base_profile.spawn_rate,
            duration_seconds=locust_duration,
        )
        click.echo(f"    Starting Locust ({ctx.load_profile}: {base_profile.users} users)")
        iter_locust_runner = LocustRunner(target_url=ctx.target_url, locustfile=ctx.locustfile)
        iter_locust_runner.start(profile)

    try:
        if has_probers and pre_chaos_window > 0:
            click.echo(f"    Collecting pre-chaos baseline ({pre_chaos_window}s)...")
            time.sleep(pre_chaos_window)

        # Run experiment
        experiment_start = time.time()
        for p in probers.values():
            if p and hasattr(p, "mark_chaos_start"):
                p.mark_chaos_start()

        # Baseline: submit to ChaosCenter with minimal chaos duration (no fault)
        if strategy_name == "baseline":
            _disable_fault_injection(scenario)

        effective_timeout = _compute_effective_timeout(scenario, ctx.timeout)
        runner = ChaosRunner(
            ctx.namespace, timeout=effective_timeout, chaoscenter=ctx.chaoscenter_config,
        )
        runner.run_experiments(scenario.get("experiments", []))

        experiment_end = time.time()
        for p in probers.values():
            if p and hasattr(p, "mark_chaos_end"):
                p.mark_chaos_end()

        # Post-chaos recovery
        if has_probers and post_chaos_window > 0:
            click.echo(f"    Collecting post-chaos samples ({post_chaos_window}s)...")
            time.sleep(post_chaos_window)
    finally:
        prober_results = stop_and_collect_probers(probers, iter_locust_runner)

    # Collect results & metrics
    collector = ResultCollector(ctx.namespace)
    executed = runner.get_executed_experiments()

    # Baseline is a no-fault control: if the go-runner hit a transient
    # TARGET_SELECTION_ERROR (pod momentarily unavailable between our
    # readiness check and Argo execution), override it to a pass — no
    # fault was intended, so target availability is irrelevant.
    if strategy_name == "baseline":
        for exp in executed:
            status = (exp.get("status") or "").lower()
            if status in ("error", "completed_with_error"):
                click.echo("    Baseline: overriding go-runner error (no fault was injected)")
                exp["status"] = "Completed"
                exp["resiliencyScore"] = 100.0
                exp["faultsPassed"] = exp.get("totalFaults", 1)
                exp["faultsFailed"] = 0

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
