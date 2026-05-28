"""Execute a single placement strategy (placement + N iterations).

Extracted from ``cli.py run()`` to keep the top-level command lean.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import click
from kubernetes import client as k8s_client

from chaosprobe.chaos.runner import ChaosRunner
from chaosprobe.collector.result_collector import ResultCollector
from chaosprobe.loadgen.runner import LoadProfile, LocustRunner
from chaosprobe.metrics.collector import MetricsCollector
from chaosprobe.orchestrator import portforward as pf
from chaosprobe.orchestrator.diagnostics import capture_unknown_diagnostics
from chaosprobe.orchestrator.preflight import (
    wait_for_healthy_deployments,
)
from chaosprobe.orchestrator.probers import (
    create_and_start_probers,
    stop_and_collect_probers,
)
from chaosprobe.orchestrator.readiness import (
    wait_for_app_ready,
    wait_for_target_pod,
)
from chaosprobe.orchestrator.recovery import wait_for_k8s_api
from chaosprobe.orchestrator.run_phases import (
    _clean_stale_resources,
    _restart_unhealthy_infra,
    aggregate_iterations,
)
from chaosprobe.orchestrator.timeout import (
    compute_effective_timeout,
    extract_chaos_duration,
)
from chaosprobe.output.generator import OutputGenerator
from chaosprobe.placement.mutator import PlacementMutator
from chaosprobe.placement.strategy import PlacementStrategy

# Environment variables for the baseline trivial fault (pod-cpu-hog).
# 1% CPU stress on 1 core for 1 second — imperceptible, no pods deleted.
# CONTAINER_RUNTIME and SOCKET_PATH are required by pod-cpu-hog to
# inject the stress-ng helper via the container runtime API.
_BASELINE_ENV: Tuple[Dict[str, str], ...] = (
    {"name": "TOTAL_CHAOS_DURATION", "value": "1"},
    {"name": "CPU_CORES", "value": "0"},
    {"name": "CPU_LOAD", "value": "1"},
    {"name": "CONTAINER_RUNTIME", "value": "containerd"},
    {"name": "SOCKET_PATH", "value": "/run/containerd/containerd.sock"},
)


def _swap_to_trivial_fault(scenario: Dict[str, Any]) -> None:
    """Replace the destructive fault with a trivial one for baseline.

    Swaps the experiment from ``pod-delete`` (which always kills at
    least one pod due to the go-runner's ``math.Maximum(1, ...)``
    floor) to ``pod-cpu-hog`` with 1% CPU stress for 1 second.

    The ChaosEngine is still submitted to ChaosCenter so all probes
    (httpProbe, cmdProbe, etc.) execute normally.  The result
    naturally reflects real system health — no score overrides needed.

    Probe timeouts and retries are NOT modified — the baseline must be
    evaluated with identical probe settings as other strategies so that
    resilience scores are directly comparable across placements.
    """
    for exp_entry in scenario.get("experiments", []):
        spec = exp_entry.get("spec", {})
        for exp in spec.get("spec", {}).get("experiments", []):
            # Swap experiment type
            exp["name"] = "pod-cpu-hog"
            # Replace env vars with trivial-fault settings
            components = exp.get("spec", {}).get("components", {})
            components["env"] = list(_BASELINE_ENV)


def _extract_http_routes(
    scenario: Dict[str, Any],
    namespace: str,
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
                service = host.split(".")[0] if host else ""
                if not service:
                    continue  # skip routes with no resolvable service

                method_def = inputs.get("method", {})
                method = "GET" if "get" in method_def else "POST"
                name = probe.get("name", path)

                routes.append((service, path, name, method))

    return routes


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
    load_service: str  # service name for load target port-forward

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
        # ── K8s API recovery gate ──
        # If the API server became unreachable during a previous strategy
        # (control plane overload from cascading evictions/rescheduling),
        # wait for it to recover before attempting cleanup.  Without this,
        # every subsequent strategy fails immediately with "Connection refused".
        # 600s timeout: control plane crashes (etcd compaction, API server
        # OOM-kill) can take 5+ minutes to self-heal on resource-constrained
        # VM clusters.  The previous 300s was insufficient — see run
        # 20260523-093030 where the adversarial strategy killed the API
        # and the 300s timeout wasn't enough.
        wait_for_k8s_api(ctx.namespace, timeout=600)

        # ── Inter-strategy cleanup ──
        # Between back-to-back strategies, lingering ChaosEngines, helper pods,
        # and completed jobs accumulate and degrade service routing (conntrack
        # churn, port-forward leaks) on memory-constrained VM clusters.
        click.echo("\n  Cleaning cluster state from previous strategy...")
        _clean_stale_resources(ctx.namespace)
        _restart_unhealthy_infra(ctx.namespace)

        # Re-establish infrastructure port-forwards that may have died
        # during the previous strategy (especially after heavy packing
        # strategies like colocate/best-fit that starve nodes).
        pf.ensure_all()

        click.echo("  Waiting for all deployments to be ready...")
        wait_for_healthy_deployments(ctx.namespace, timeout=180, strict=True)

        # ── Full app-level health verification ──
        # K8s reporting pods as Ready is necessary but not sufficient:
        # pods can report Ready while their connection pools are broken,
        # gRPC channels are in TRANSIENT_FAILURE, or service endpoints
        # haven't propagated.  Restart all app deployments to clear any
        # post-crash damage, then verify actual HTTP reachability.
        click.echo("  Restarting app deployments for clean strategy start...")
        _restart_app_deployments(ctx.namespace, ctx.target_deployment)

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
    ctx: RunContext,
    strategy_name: str,
    strategy_result: Dict[str, Any],
) -> None:
    click.echo("\n  Step 1: Clearing existing placement...")
    ctx.mutator.clear_placement(wait=True, timeout=120)
    click.echo("    Placement cleared.")

    if strategy_name in ("baseline", "default"):
        click.echo(f"\n  Step 2: {strategy_name.capitalize()} — using default scheduling")
        strategy_result["placement"] = {
            "strategy": strategy_name,
            "description": (
                "No-fault control — no chaos injected"
                if strategy_name == "baseline"
                else "Default Kubernetes scheduling"
            ),
        }
    else:
        click.echo(f"\n  Step 2: Applying {strategy_name} placement...")
        strat = PlacementStrategy(strategy_name)
        _infra_prefixes = (
            "chaos-exporter",
            "chaos-operator",
            "event-tracker",
            "subscriber",
            "workflow-controller",
        )
        all_deps = ctx.mutator.get_deployments()
        app_deps = [
            d.name for d in all_deps if not d.name.startswith(_infra_prefixes) and d.replicas > 0
        ]
        # Heavy placement strategies (adversarial, colocate) pack many
        # services onto few nodes, causing long rollout times due to
        # resource contention.  Use a generous timeout (5 min) instead
        # of the experiment timeout or an arbitrary 120s cap.
        rollout_timeout = max(300, ctx.timeout)
        assignment = ctx.mutator.apply_strategy(
            strategy=strat,
            seed=ctx.seed if strategy_name == "random" else None,
            deployments=app_deps if app_deps else None,
            wait=True,
            timeout=rollout_timeout,
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

    # Clean stale ChaosEngines, ChaosResults, Jobs, and Argo workflow
    # pods from the previous iteration.  Without this, residual
    # resources accumulate and consume cluster memory, causing
    # transient HTTP 500s on memory-constrained clusters.
    click.echo("    Cleaning stale resources from previous iteration...")
    _clean_stale_resources(ctx.namespace)
    _restart_unhealthy_infra(ctx.namespace)

    step_label = "  Step 3" if ctx.iterations == 1 else "    Step A"
    click.echo(f"\n{step_label}: Waiting for cluster readiness...")

    # Dynamic readiness gate — no blind sleep.  The previous fixed
    # ``time.sleep(settle_time)`` here was redundant: the three gates
    # below already poll for cluster recovery and return as soon as
    # the conditions are met.  Replacing the fixed wait with the
    # dynamic chain means a healthy cluster proceeds immediately,
    # while a damaged one waits exactly as long as it needs to.
    click.echo("    Verifying deployment readiness...")
    wait_for_healthy_deployments(ctx.namespace, timeout=180)

    # Ensure the chaos target deployment has at least one ready pod
    # that stays stable (important for colocate where resource pressure
    # can cause pods to crash shortly after starting).
    wait_for_target_pod(ctx.namespace, ctx.target_deployment, timeout=180, stable_secs=10)

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

    # Extract chaos duration for prober phase labeling
    chaos_duration = extract_chaos_duration(scenario)

    # Verify app-level HTTP readiness across ALL probed routes before
    # starting probers.  This prevents cascading poisoning where a
    # previous iteration's post-chaos damage leaks into the next
    # iteration's pre-chaos baseline.
    # 240s upper bound: consecutive-OK (≥15s) + sustained period (15s) +
    # generous slack for slow JVM warm-up between iterations.  The function
    # returns early as soon as the gate passes.
    wait_for_app_ready(
        ctx.namespace,
        ctx.target_deployment,
        timeout=240,
        http_routes=http_routes or None,
    )

    # Per-iteration pod -> node ground truth.  Captured here (after the
    # rolling restart between iterations has settled, just before chaos
    # injection) because pod names change every iteration — this is the
    # only correct moment to record which specific pods chaos is about
    # to act on.  Downstream analysis correlates which-pod-was-killed
    # against which-node-it-lived-on using this map.
    iter_app_deps = sorted((strategy_result.get("placement") or {}).get("assignments") or {})
    if not iter_app_deps:
        # Baseline/default: assignments is empty; observe everything in
        # the namespace that has replicas.
        iter_app_deps = [d.name for d in ctx.mutator.get_deployments() if d.replicas > 0]
    iter_pod_placements = ctx.mutator.observe_pod_placements(iter_app_deps)

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
        expected_chaos_duration=float(chaos_duration),
    )

    # Compute windows before starting Locust so its duration covers the
    # full experiment lifecycle (pre-chaos + experiment + post-chaos).
    #
    # The previous hardcoded ``min(settle_time, 15)`` cap was the source
    # of the 33/75 bimodality in results/20260519-222220: when chaos
    # left the cluster needing 30-60s of recovery (typical for
    # cumulative pod-delete pressure on a microservice cascade), the
    # 15s post-chaos sample window caught only error responses and
    # dragged the iteration's verdict to a cascade-Fail score, while
    # iterations that happened to recover in <15s scored normally.
    # Same chaos, same placement — different measurement outcome.
    # Now: ``settle_time`` directly controls both windows, so a user
    # who wants to measure "did chaos cause damage that persists 60s
    # after stopping" can set --settle-time 60 and actually get a 60s
    # window.  pre_chaos_window still defers to --baseline-duration
    # when explicitly set, since baseline collection has its own knob.
    pre_chaos_window = ctx.baseline_duration if ctx.baseline_duration > 0 else ctx.settle_time
    has_probers = any(
        probers.get(k) for k in ("latency", "redis", "disk", "resource", "prometheus")
    )
    post_chaos_window = ctx.settle_time

    iter_locust_runner = None
    if ctx.load_profile:
        # Re-ensure load-target port-forward is alive (placement changes may
        # have restarted the target pod, killing the kubectl tunnel).
        if ctx.frontend_pf_port and ctx.target_url and "localhost" in ctx.target_url:
            pf.ensure(
                ctx.load_service,
                ctx.namespace,
                [f"{ctx.frontend_pf_port}:80"],
                "localhost",
                ctx.frontend_pf_port,
            )
        base_profile = LoadProfile.from_name(ctx.load_profile)
        # Compute Locust run duration to span the full experiment window:
        # pre-chaos baseline + effective ChaosRunner timeout + post-chaos + buffer
        effective_timeout = compute_effective_timeout(scenario, ctx.timeout)
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

        # Baseline: swap destructive fault for a trivial one (pod-cpu-hog
        # at 1% CPU for 1s) so probes execute without pod deletion.
        if strategy_name == "baseline":
            _swap_to_trivial_fault(scenario)

        effective_timeout = compute_effective_timeout(scenario, ctx.timeout)
        runner = ChaosRunner(
            ctx.namespace,
            timeout=effective_timeout,
            chaoscenter=ctx.chaoscenter_config,
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
        scenario,
        results,
        metrics=recovery,
        placement=placement_info,
        service_routes=ctx.service_routes,
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
        if _sync_neo4j(ctx, output_data):
            click.echo(f"\n    Results synced to Neo4j (run: {output_data.get('runId', '')})")
        else:
            click.echo("\n    Warning: Neo4j sync failed — results saved to disk only", err=True)

    verdict = output_data.get("summary", {}).get("overallVerdict", "UNKNOWN")
    score = output_data.get("summary", {}).get("resilienceScore", 0)
    rec_summary = recovery.get("recovery", {}).get("summary", {})
    avg_recovery = rec_summary.get("meanRecovery_ms")
    recovery_str = f" | Avg Recovery: {avg_recovery:.0f}ms" if avg_recovery else ""

    click.echo(f"\n    Verdict: {verdict} | Resilience Score: {score:.1f}{recovery_str}")

    # Assess pre-chaos baseline health from latency prober data.
    # If most routes had errors before chaos even started, the iteration
    # is "tainted" — its score reflects accumulated damage from a previous
    # iteration rather than the placement strategy's actual resilience.
    pre_chaos_tainted = False
    latency_phases = (prober_results.get("latency") or {}).get("phases", {})
    pre_chaos = latency_phases.get("pre-chaos", {})
    if pre_chaos.get("sampleCount", 0) > 0:
        total_errors = 0
        total_ok = 0
        for route_data in pre_chaos.get("routes", {}).values():
            total_errors += route_data.get("errorCount", 0)
            total_ok += route_data.get("sampleCount", 0)
        # sampleCount counts successful measurements only; errorCount
        # counts failed ones.  Total attempts = total_ok + total_errors.
        # Threshold lowered from 50% → 10%: empirically the BAD iterations
        # observed in the 20260518-131302 run had 0% pre-chaos errors
        # (the marginal-recovery state shows up only under chaos load),
        # so this gate is a backstop for grossly-tainted starts, not a
        # primary detector.  The proactive functional gate in
        # _wait_for_app_ready is the main defence.
        total_attempts = total_ok + total_errors
        if total_attempts > 0 and total_errors / total_attempts > 0.1:
            pre_chaos_tainted = True
            click.echo(
                f"    WARNING: Pre-chaos baseline was degraded "
                f"({total_errors}/{total_attempts} samples had errors). "
                f"Score may not reflect strategy resilience."
            )

        # Latency-based taint check: require BOTH p95 AND mean above
        # threshold, since pre-chaos samples are sparse (N≈15) and p95
        # alone is dominated by single-outlier spikes that don't reflect
        # cluster health.  Investigation in results/20260521-073913:
        # adversarial iter3 had pre-chaos `/` max=1160ms (outlier) but
        # mean=252ms and scored 75 cleanly — high p95 alone falsely
        # flagged it as tainted.  Requiring mean > threshold/2 filters
        # outlier-driven false positives.
        SLOW_BASELINE_P95_MS = 1500.0
        slow_routes = []
        for route_name, route_data in pre_chaos.get("routes", {}).items():
            p95 = route_data.get("p95_ms")
            mean = route_data.get("mean_ms")
            if (
                p95 is not None
                and mean is not None
                and p95 > SLOW_BASELINE_P95_MS
                and mean > SLOW_BASELINE_P95_MS / 2
            ):
                slow_routes.append((route_name, p95, mean))
        if slow_routes:
            pre_chaos_tainted = True
            slow_summary = ", ".join(f"{r}=p95:{p:.0f}/mean:{m:.0f}ms" for r, p, m in slow_routes)
            click.echo(
                f"    WARNING: Pre-chaos baseline latency degraded on "
                f"{len(slow_routes)} route(s) [{slow_summary}]. "
                f"Cluster likely tainted by previous iteration."
            )

    # Extract per-probe verdicts for diagnostic analysis.
    # LitmusChaos probe status is a dict of phase→verdict strings like
    # {"Continuous": "Passed 👍"} rather than a top-level "verdict" key.
    probe_verdicts = {}
    for exp in output_data.get("experiments", []):
        for probe in exp.get("probes", []):
            pname = probe.get("name", "")
            pverdict = "Unknown"

            # First try phaseVerdicts (already parsed by result_collector)
            phase_v = probe.get("phaseVerdicts", {})
            if phase_v:
                pverdict = "Pass" if all(v == "Pass" for v in phase_v.values()) else "Fail"
            else:
                # Fallback: parse the raw status map directly
                pstatus = probe.get("status", {})
                if isinstance(pstatus, dict):
                    phase_results = []
                    for key, val in pstatus.items():
                        if key in ("verdict", "description"):
                            continue
                        if isinstance(val, str):
                            phase_results.append("Pass" if "Passed" in val else "Fail")
                    if phase_results:
                        pverdict = "Pass" if all(v == "Pass" for v in phase_results) else "Fail"

            if pname:
                probe_verdicts[pname] = pverdict

    # If verdicts are empty or all "Unknown" (CRD probeStatuses was empty
    # because ChaosCenter cleaned it up), fall back to ChaosCenter API
    # verdicts extracted from executionData.
    if not probe_verdicts or all(v == "Unknown" for v in probe_verdicts.values()):
        for exp_entry in executed:
            cc_verdicts = exp_entry.get("probeVerdicts", {})
            if cc_verdicts:
                probe_verdicts = cc_verdicts
                break

    # Diagnostic only — do NOT alter the score.  An Unknown verdict
    # means the probe pod didn't report a result (eviction, timeout,
    # scheduling delay).  Often this is itself a real consequence of
    # the placement strategy creating node contention, so counting it
    # toward "Fail" in the score is defensible.  But surfacing the
    # count lets analysis flag iterations where data quality is poor
    # rather than silently trusting a deflated score.
    unknown_probe_count = sum(1 for v in probe_verdicts.values() if v == "Unknown")

    # When ALL probes are Unknown (CRD stuck at "Awaited", ChaosCenter
    # returned empty verdicts), the experiment never actually evaluated
    # probes — typically because the K8s API or ChaosCenter became
    # unreachable mid-experiment.  Score 0.0 in this case is not a
    # valid resilience measurement; it's an infrastructure failure.
    # Mark as ERROR so aggregate_iterations excludes it from statistics
    # rather than dragging down the mean with a meaningless 0.
    if unknown_probe_count > 0 and unknown_probe_count == len(probe_verdicts):
        click.echo(
            f"    WARNING: All {unknown_probe_count} probes returned Unknown — "
            f"experiment did not evaluate probes (infra failure). "
            f"Marking iteration as ERROR."
        )
        verdict = "ERROR"
        score = 0

    iter_result = {
        "iteration": iter_num,
        "verdict": verdict,
        "resilienceScore": score,
        "probeVerdicts": probe_verdicts,
        "unknownProbeCount": unknown_probe_count,
        "metrics": recovery,
        "runId": output_data.get("runId", ""),
        "preChaosHealthy": not pre_chaos_tainted,
        "anomalyLabels": output_data.get("anomalyLabels"),
        "cascadeTimeline": output_data.get("cascadeTimeline"),
        "podPlacements": iter_pod_placements,
    }

    if unknown_probe_count > 0:
        iter_result["unknownDiagnostics"] = capture_unknown_diagnostics(
            namespace=ctx.namespace,
            probe_verdicts=probe_verdicts,
            output_data=output_data,
            executed=executed,
            experiment_start=experiment_start,
            experiment_end=experiment_end,
        )

    return iter_result


def _run_iterations(
    ctx: RunContext,
    strategy_name: str,
    strategy_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Run all iterations for one strategy.

    After every iteration (except the last), trigger a rolling restart
    of app deployments so the next iteration starts from a clean state.
    Post-chaos damage (stuck connections, unhealthy pods, resource
    exhaustion) cascades into subsequent iterations if not cleared.

    If a single iteration fails due to a transient error (K8s API
    unreachable, timeout, etc.), it is recorded as an error iteration
    and the loop continues to the next iteration rather than aborting
    the entire strategy.
    """
    results: List[Dict[str, Any]] = []
    for i in range(1, ctx.iterations + 1):
        try:
            ir = _run_single_iteration(ctx, strategy_name, strategy_result, i)
        except Exception as e:
            click.echo(
                f"\n    ERROR in iteration {i}/{ctx.iterations}: {e}",
                err=True,
            )
            ir = {
                "iteration": i,
                "verdict": "ERROR",
                "resilienceScore": 0,
                "probeVerdicts": {},
                "unknownProbeCount": 0,
                "metrics": {},
                "runId": "",
                "preChaosHealthy": False,
                "error": str(e),
                "podPlacements": {},
            }
        results.append(ir)
        # Restart between iterations to prevent cascading damage.
        # Skip restart after the last iteration (cleanup happens at strategy level).
        if i < ctx.iterations:
            # If the previous iteration ended with a K8s API error (e.g.
            # Connection refused), the API server may still be down.
            # Wait for it to recover before attempting the restart / next
            # iteration, otherwise the restart and the whole next
            # iteration will fail immediately.
            if ir.get("verdict") == "ERROR":
                click.echo("    Waiting for K8s API to recover before next iteration...")
                try:
                    wait_for_k8s_api(ctx.namespace, timeout=600)
                except click.ClickException:
                    click.echo(
                        "    K8s API still unreachable — skipping remaining iterations",
                        err=True,
                    )
                    break
                # Re-clean stale resources that accumulated during the outage
                _clean_stale_resources(ctx.namespace)
                # When the K8s API goes down, kubectl port-forward processes
                # die too — ChaosCenter, Prometheus, and Neo4j tunnels need
                # explicit re-establishment.  Without this, the next
                # iteration would discover the dead tunnels mid-flight
                # (raising ChaosCenter-unreachable on probe registration
                # or Neo4j driver-closed on sync) and trip another ERROR.
                click.echo("    Re-establishing port-forwards after API outage...")
                pf.ensure_all()
                # Verify deployments are healthy before proceeding — a
                # crash may have left pods in CrashLoopBackOff or pending
                # state.  strict=True ensures we don't silently proceed
                # with a broken cluster.
                click.echo("    Verifying deployment health after crash recovery...")
                try:
                    wait_for_healthy_deployments(ctx.namespace, timeout=180, strict=True)
                except click.ClickException:
                    click.echo(
                        "    Deployments not healthy after recovery — "
                        "skipping remaining iterations",
                        err=True,
                    )
                    break
                # Neo4j driver may also be in a closed state — reset it
                # so _sync_neo4j builds a fresh driver on next use.
                if ctx.graph_store is not None:
                    try:
                        ctx.graph_store.close()
                    except Exception:
                        pass
                    try:
                        from chaosprobe.storage.neo4j_store import Neo4jStore

                        ctx.graph_store = Neo4jStore(
                            ctx.neo4j_uri,
                            ctx.neo4j_user,
                            ctx.neo4j_password,
                        )
                    except Exception as exc:
                        click.echo(
                            f"    Neo4j reconnect failed after K8s outage — "
                            f"disabling sync: {exc}",
                            err=True,
                        )
                        ctx.graph_store = None
            click.echo("    Restarting app deployments for clean next iteration...")
            _restart_app_deployments(ctx.namespace, ctx.target_deployment)
    return results


def _restart_app_deployments(namespace: str, target_deployment: str) -> None:
    """Trigger a rollout restart of all app deployments in the namespace.

    This clears post-chaos damage (stuck connections, unhealthy pods,
    resource exhaustion) that the settle-time alone cannot fix.
    """
    apps_api = k8s_client.AppsV1Api()
    try:
        deps = apps_api.list_namespaced_deployment(namespace)
        infra_prefixes = (
            "chaos-exporter",
            "chaos-operator",
            "event-tracker",
            "subscriber",
            "workflow-controller",
        )
        app_deps = [
            d.metadata.name
            for d in deps.items
            if not d.metadata.name.startswith(infra_prefixes) and (d.spec.replicas or 0) > 0
        ]
        if not app_deps:
            return

        now = datetime.now(timezone.utc).isoformat()
        patch_failures = 0
        for dep_name in app_deps:
            try:
                apps_api.patch_namespaced_deployment(
                    dep_name,
                    namespace,
                    {
                        "spec": {
                            "template": {
                                "metadata": {
                                    "annotations": {
                                        "chaosprobe.io/restartedAt": now,
                                    }
                                }
                            }
                        },
                    },
                )
            except Exception as exc:
                patch_failures += 1
                click.echo(f"    Warning: failed to restart {dep_name}: {exc}")

        restarted = len(app_deps) - patch_failures
        click.echo(f"    Triggered rollout restart for {restarted}/{len(app_deps)} deployment(s)")

        # Wait for all rollouts to complete
        wait_for_healthy_deployments(namespace, timeout=180)
        click.echo("    All rollouts complete.")

        # Brief cooldown after rollout — K8s reports pods Ready before
        # connection pools and service endpoints are fully propagated.
        # Without this, the next iteration's app-ready check may fail
        # on transient connection errors.  A previous attempt to remove
        # this on the grounds that _wait_for_app_ready tolerates it was
        # reverted (results/20260518-175642 produced a persistent
        # broken-infra state; this was one of three dynamic-wait changes
        # rolled back together).
        time.sleep(5)
    except Exception as e:
        click.echo(f"    Warning: deployment restart failed: {e}")


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

        # Expose metrics from the median-scoring iteration at the
        # top-level so that charts.py _extract_metric() can find
        # latency/resource/prometheus data without falling back to
        # iteration[0] which may be unrepresentative.
        sorted_iters = sorted(
            iteration_results,
            key=lambda ir: ir.get("resilienceScore", 0),
        )
        median_iter = sorted_iters[(len(sorted_iters) - 1) // 2]
        strategy_result["metrics"] = median_iter.get("metrics")
        strategy_result["anomalyLabels"] = median_iter.get("anomalyLabels")
        strategy_result["cascadeTimeline"] = median_iter.get("cascadeTimeline")

        agg = strategy_result["aggregated"]
        iter_passed = sum(1 for ir in iteration_results if ir["verdict"] == "PASS")
        tainted = agg.get("taintedIterations", 0)
        taint_str = f" ({tainted} tainted)" if tainted > 0 else ""
        click.echo(
            f"\n    Aggregated: {iter_passed}/{ctx.iterations} passed | "
            f"Mean Score: {agg['meanResilienceScore']:.1f}{taint_str}"
        )
        if tainted > 0:
            click.echo(f"    Healthy-only Mean Score: {agg['meanResilienceScore_healthyOnly']:.1f}")
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
        strategy_result["probeVerdicts"] = ir.get("probeVerdicts", {})
        strategy_result["metrics"] = ir["metrics"]
        strategy_result["anomalyLabels"] = ir.get("anomalyLabels")
        strategy_result["cascadeTimeline"] = ir.get("cascadeTimeline")
        strategy_result["status"] = "completed"
        strategy_result["runId"] = ir["runId"]
        return ir["verdict"] == "PASS"


def _sync_neo4j(ctx: RunContext, output_data: Dict[str, Any]) -> bool:
    """Sync run data to Neo4j with retry logic. Returns True on success.

    Recreates ``ctx.graph_store`` on connection failure (driver closed,
    connection refused) so that subsequent iterations can use a fresh
    driver if Neo4j temporarily disappears.  Aborts early if we can't
    even construct a new Neo4jStore (cluster API is down) — retrying
    a dead store just produces the same "Driver closed" error.
    """
    for attempt in range(3):
        try:
            ctx.graph_store.sync_run(output_data)
            return True
        except Exception as e:
            if attempt >= 2:
                import traceback

                click.echo(
                    f"    Warning: Neo4j sync failed after 3 attempts: {e}",
                    err=True,
                )
                click.echo(traceback.format_exc(), err=True)
                return False

            click.echo(
                f"    Neo4j sync attempt {attempt + 1} failed, reconnecting...",
                err=True,
            )
            try:
                ctx.graph_store.close()
            except Exception:
                pass

            # Ensure Neo4j port-forward is alive before reconnecting.
            # Heavy strategies (colocate/best-fit) can starve nodes and
            # kill kubectl tunnels; without this the driver reconnect
            # will also fail with "Connection refused".
            neo4j_host, neo4j_port = "localhost", 7687
            try:
                parsed = (ctx.neo4j_uri or "").replace("bolt://", "").replace("neo4j://", "")
                if ":" in parsed:
                    neo4j_host, neo4j_port = parsed.split(":", 1)
                    neo4j_port = int(neo4j_port)
            except (ValueError, AttributeError):
                pass
            if not pf.check_port(neo4j_host, neo4j_port):
                # pf.ensure_all() polls check_port internally before
                # returning, so no additional sleep is needed here.
                pf.ensure_all()

            try:
                from chaosprobe.storage.neo4j_store import Neo4jStore

                ctx.graph_store = Neo4jStore(
                    ctx.neo4j_uri,
                    ctx.neo4j_user,
                    ctx.neo4j_password,
                )
            except Exception as ctor_exc:
                # New driver couldn't be constructed (Neo4j port-forward
                # dead, bolt unreachable, cluster API down).  Without
                # this early return, the retry loop would re-call
                # ``sync_run`` on the closed driver we just close()d,
                # producing repeated "Driver closed" errors (this is the
                # failure mode observed in results/20260520-220937
                # during the colocate strategy after the K8s control
                # plane crashed in best-fit iter6).  Mark the store as
                # absent so future iterations don't try either, and
                # return failure for this sync.
                click.echo(
                    f"    Neo4j unreachable — disabling sync for this run: " f"{ctor_exc}",
                    err=True,
                )
                ctx.graph_store = None
                return False
