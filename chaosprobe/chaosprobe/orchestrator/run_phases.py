"""Extracted phases for the ``chaosprobe run`` command.

Breaking the ``run()`` Click command into composable helper functions
so the top-level orchestrator stays small and readable.
"""

from __future__ import annotations

import statistics
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import click

from chaosprobe.orchestrator import portforward as pf
from chaosprobe.orchestrator.preflight import (
    LITMUS_INFRA_DEPLOYMENTS,
    wait_for_healthy_deployments,
)
from chaosprobe.provisioner.setup import LitmusSetup

LOAD_TARGET_LOCAL_PORT = 8089

# ---------------------------------------------------------------------------
# 1.  Pre-flight sub-steps
# ---------------------------------------------------------------------------

def _check_nodes(core_api: Any) -> None:
    """Verify all cluster nodes are Ready; abort if any are not."""
    nodes = core_api.list_node()
    not_ready = [
        n.metadata.name
        for n in nodes.items
        if {c.type: c.status for c in (n.status.conditions or [])}.get("Ready") != "True"
    ]
    if not_ready:
        click.echo(f"  Error: nodes not Ready: {', '.join(not_ready)}", err=True)
        click.echo("  Fix node issues before running experiments.", err=True)
        sys.exit(1)
    click.echo(f"  Nodes:       {len(nodes.items)} Ready")


def _clean_stale_resources(namespace: str) -> None:
    """Remove stale ChaosEngines, ChaosResults, Jobs, and Workflow pods."""
    from kubernetes import client as k8s_client_mod

    custom_api = k8s_client_mod.CustomObjectsApi()

    # ChaosEngines
    try:
        engines = custom_api.list_namespaced_custom_object(
            group="litmuschaos.io", version="v1alpha1",
            namespace=namespace, plural="chaosengines",
        )
        items = engines.get("items", [])
        if items:
            click.echo(f"  Cleaning up {len(items)} stale ChaosEngine(s)...")
            for eng in items:
                custom_api.delete_namespaced_custom_object(
                    group="litmuschaos.io", version="v1alpha1",
                    namespace=namespace, plural="chaosengines",
                    name=eng["metadata"]["name"],
                )
            click.echo("  ChaosEngines: cleaned")
        else:
            click.echo("  ChaosEngines: none (clean)")
    except Exception as e:
        click.echo(f"  ChaosEngines: check skipped ({e})", err=True)

    # ChaosResults
    try:
        results = custom_api.list_namespaced_custom_object(
            group="litmuschaos.io", version="v1alpha1",
            namespace=namespace, plural="chaosresults",
        )
        items = results.get("items", [])
        if items:
            click.echo(f"  Cleaning up {len(items)} stale ChaosResult(s)...")
            for res in items:
                custom_api.delete_namespaced_custom_object(
                    group="litmuschaos.io", version="v1alpha1",
                    namespace=namespace, plural="chaosresults",
                    name=res["metadata"]["name"],
                )
            click.echo("  ChaosResults: cleaned")
    except Exception as e:
        click.echo(f"  ChaosResults: cleanup skipped ({e})", err=True)

    # Stale experiment jobs
    try:
        batch_api = k8s_client_mod.BatchV1Api()
        jobs = batch_api.list_namespaced_job(
            namespace, label_selector="app.kubernetes.io/part-of=litmus",
        )
        for job in jobs.items:
            succeeded = getattr(job.status, "succeeded", None) if job.status else None
            failed = getattr(job.status, "failed", None) if job.status else None
            if succeeded or failed:
                batch_api.delete_namespaced_job(
                    name=job.metadata.name, namespace=namespace,
                    propagation_policy="Background",
                )
    except Exception as e:
        click.echo(f"  Stale jobs: cleanup skipped ({e})", err=True)

    # Stale Argo workflow pods
    try:
        core_api = k8s_client_mod.CoreV1Api()
        wf_pods = core_api.list_namespaced_pod(
            namespace, label_selector="workflows.argoproj.io/workflow",
        )
        stale = [p for p in wf_pods.items if p.status.phase in ("Failed", "Error", "Succeeded")]
        if stale:
            click.echo(f"  Cleaning up {len(stale)} stale workflow pod(s)...")
            for pod in stale:
                core_api.delete_namespaced_pod(name=pod.metadata.name, namespace=namespace)
            click.echo("  Workflow pods: cleaned")
        else:
            click.echo("  Workflow pods: none stale")
    except Exception as e:
        click.echo(f"  Workflow pods: check skipped ({e})", err=True)


def _restart_unhealthy_infra(namespace: str) -> None:
    """Restart Litmus infra deployments stuck in CrashLoopBackOff."""
    from kubernetes import client as k8s_client_mod

    try:
        apps_api = k8s_client_mod.AppsV1Api()
        core_api = k8s_client_mod.CoreV1Api()
        for dep_name in LITMUS_INFRA_DEPLOYMENTS:
            try:
                dep = apps_api.read_namespaced_deployment(dep_name, namespace)
            except k8s_client_mod.rest.ApiException:
                continue
            pods = core_api.list_namespaced_pod(
                namespace,
                label_selector=",".join(
                    f"{k}={v}" for k, v in (dep.spec.selector.match_labels or {}).items()
                ),
            )
            needs_restart = any(
                cs.state and cs.state.waiting
                and cs.state.waiting.reason in ("CrashLoopBackOff", "Error", "CreateContainerError")
                for pod in pods.items
                for cs in (pod.status.container_statuses or [])
            )
            if needs_restart:
                click.echo(f"  Restarting unhealthy {dep_name}...")
                apps_api.patch_namespaced_deployment(
                    dep_name, namespace, {
                        "spec": {"template": {"metadata": {"annotations": {
                            "chaosprobe.io/restartedAt": datetime.now(timezone.utc).isoformat(),
                        }}}},
                    },
                )
                for _ in range(24):
                    time.sleep(5)
                    try:
                        dep = apps_api.read_namespaced_deployment(dep_name, namespace)
                        if (dep.status.ready_replicas or 0) >= dep.spec.replicas:
                            click.echo(f"  {dep_name}: recovered")
                            break
                    except Exception:
                        pass
                else:
                    click.echo(f"  WARNING: {dep_name} did not recover after restart", err=True)
    except Exception as e:
        click.echo(f"  WARNING: infra health check failed ({e})", err=True)


def _setup_prometheus_pf(measure_prometheus: bool) -> None:
    """Verify Prometheus is reachable; auto-establish port-forward if needed."""
    if not measure_prometheus:
        return
    if pf.check_port("localhost", 9090):
        click.echo("  Prometheus:  localhost:9090 reachable")
        return

    click.echo("  Prometheus:  localhost:9090 not reachable, establishing port-forward...")
    pf.ensure("prometheus-server", "prometheus", ["9090:80"], "localhost", 9090)
    if pf.check_port("localhost", 9090):
        click.echo("  Prometheus:  localhost:9090 reachable")
    else:
        click.echo(
            "  Prometheus:  WARNING - localhost:9090 still not reachable. "
            "Run 'chaosprobe init' to set up port-forwards.",
            err=True,
        )


def _setup_neo4j_pf(neo4j_uri: Optional[str]) -> None:
    """Verify Neo4j is reachable (port-forwarded by init)."""
    if not neo4j_uri:
        return
    host, port = "localhost", 7687
    try:
        parsed = neo4j_uri.replace("bolt://", "").replace("neo4j://", "")
        if ":" in parsed:
            host, port_str = parsed.rsplit(":", 1)
            port = int(port_str)
    except Exception:
        pass
    if pf.check_port(host, port):
        click.echo(f"  Neo4j bolt:  {host}:{port} reachable")
    else:
        click.echo(
            f"  Neo4j bolt:  WARNING - {host}:{port} not reachable. "
            "Run 'chaosprobe init' to set up port-forwards.",
            err=True,
        )


def _clean_stale_chaoscenter_experiments(
    chaoscenter_config: Optional[Dict[str, Any]],
) -> None:
    """Remove leftover ChaosCenter experiments from previous runs.

    Called once during pre-flight so the dashboard starts clean without
    deleting experiments created during the current session.
    """
    if not chaoscenter_config:
        return
    setup = LitmusSetup(skip_k8s_init=True)
    experiments = setup.chaoscenter_list_experiments(
        gql_url=chaoscenter_config["gql_url"],
        project_id=chaoscenter_config["project_id"],
        token=chaoscenter_config["token"],
    )
    if not experiments:
        return
    click.echo(f"  Cleaning up {len(experiments)} stale experiment(s) from ChaosCenter...")
    for exp in experiments:
        exp_id = exp.get("experimentID", "")
        if exp_id:
            setup.chaoscenter_delete_experiment(
                gql_url=chaoscenter_config["gql_url"],
                project_id=chaoscenter_config["project_id"],
                token=chaoscenter_config["token"],
                experiment_id=exp_id,
            )


def _setup_chaoscenter(namespace: str) -> Optional[Dict[str, Any]]:
    """Verify ChaosCenter port-forwards are active (set up by init) and auto-configure."""
    cc_frontend_port = LitmusSetup.CHAOSCENTER_FRONTEND_PORT
    cc_auth_port = LitmusSetup.CHAOSCENTER_AUTH_PORT
    cc_server_port = LitmusSetup.CHAOSCENTER_SERVER_PORT

    if not pf.check_port("localhost", cc_frontend_port):
        raise click.ClickException(
            f"ChaosCenter frontend not reachable at localhost:{cc_frontend_port}.\n"
            "  Run 'chaosprobe init' to install infrastructure and set up port-forwards."
        )
    click.echo(f"  ChaosCenter: http://localhost:{cc_frontend_port}")

    if not pf.check_port("localhost", cc_auth_port):
        raise click.ClickException(
            f"ChaosCenter auth server not reachable at localhost:{cc_auth_port}.\n"
            "  Run 'chaosprobe init' to set up port-forwards."
        )
    if not pf.check_port("localhost", cc_server_port):
        raise click.ClickException(
            f"ChaosCenter GraphQL server not reachable at localhost:{cc_server_port}.\n"
            "  Run 'chaosprobe init' to set up port-forwards."
        )

    # Auto-configure
    try:
        setup = LitmusSetup(skip_k8s_init=True)
        setup._init_k8s_client()
        cc_result = setup.ensure_chaoscenter_configured(
            namespace=namespace, base_host="http://localhost",
        )
        click.echo("  ChaosCenter: auto-configured for experiment visibility")
        return {
            "token": cc_result["token"],
            "project_id": cc_result["project_id"],
            "infra_id": cc_result["infra_id"],
            "gql_url": f"http://localhost:{cc_server_port}/query",
        }
    except Exception as exc:
        raise click.ClickException(
            f"ChaosCenter auto-setup failed: {exc}\n"
            "  All experiments run through the ChaosCenter API.\n"
            "  Ensure ChaosCenter is installed and reachable."
        ) from exc


def _check_metrics_server() -> None:
    """Verify the Kubernetes metrics-server API is available."""
    from kubernetes import client as k8s_client_mod

    try:
        k8s_client_mod.CustomObjectsApi().list_cluster_custom_object(
            group="metrics.k8s.io", version="v1beta1", plural="nodes",
        )
        click.echo("  metrics-srv: API available")
    except Exception:
        click.echo("  metrics-srv: WARNING - metrics API not available", err=True)


def _setup_load_target(
    namespace: str,
    load_profile: Optional[str],
    target_url: Optional[str],
    load_service: str = "frontend",
) -> Tuple[Optional[str], int]:
    """Ensure load target is reachable, setting up a port-forward if needed."""
    local_port = LOAD_TARGET_LOCAL_PORT
    if target_url is not None:
        click.echo(f"  Load target: {target_url}")
        return target_url, local_port
    if load_profile:
        if not pf.check_port("localhost", local_port):
            pf.ensure(
                load_service, namespace,
                [f"{local_port}:80"], "localhost", local_port,
            )
        target_url = f"http://localhost:{local_port}"
        if pf.check_port("localhost", local_port):
            click.echo(f"  Load target: {target_url}")
        else:
            click.echo(
                f"  Load target: WARNING - localhost:{local_port} "
                "not reachable. Pass --target-url.",
                err=True,
            )
        return target_url, local_port
    return None, local_port


# ---------------------------------------------------------------------------
# 1.  Pre-flight checks — orchestrator
# ---------------------------------------------------------------------------

def run_preflight_checks(
    namespace: str,
    *,
    measure_prometheus: bool,
    prometheus_url: Tuple[str, ...],
    neo4j_uri: Optional[str],
    load_profile: Optional[str],
    target_url: Optional[str],
    timeout: int,
    load_service: str = "frontend",
) -> Dict[str, Any]:
    """Run all pre-flight checks before the strategy loop.

    Returns a dict with keys ``core_api``, ``chaoscenter_config``,
    ``target_url``, ``frontend_pf_port``.
    """
    from kubernetes import client as k8s_client_mod
    from kubernetes import config as k8s_config

    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
    core_api = k8s_client_mod.CoreV1Api()

    _check_nodes(core_api)
    _clean_stale_resources(namespace)
    _restart_unhealthy_infra(namespace)
    _setup_prometheus_pf(measure_prometheus)
    _setup_neo4j_pf(neo4j_uri)
    chaoscenter_config = _setup_chaoscenter(namespace)
    _clean_stale_chaoscenter_experiments(chaoscenter_config)
    _check_metrics_server()

    click.echo("  Deployments: waiting for readiness...")
    wait_for_healthy_deployments(namespace, timeout=120)
    click.echo("  Deployments: all ready")

    target_url, frontend_pf_port = _setup_load_target(
        namespace, load_profile, target_url, load_service=load_service,
    )

    # Start background monitor to auto-restart dead port-forward processes
    pf.monitor_start()

    return {
        "core_api": core_api,
        "chaoscenter_config": chaoscenter_config,
        "target_url": target_url,
        "frontend_pf_port": frontend_pf_port,
    }


# ---------------------------------------------------------------------------
# 2.  Neo4j graph store initialisation
# ---------------------------------------------------------------------------

def init_graph_store(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    namespace: str,
    service_routes: Any = None,
) -> Any:
    """Connect to Neo4j, ensure schema, and sync topology.

    Returns the connected ``Neo4jStore`` instance.
    Raises on failure (caller should handle and exit).
    """
    from chaosprobe.storage.neo4j_store import Neo4jStore

    max_retries = 24  # 24 × 5 s = 120 s total budget
    store: Optional[Neo4jStore] = None
    for attempt in range(max_retries):
        try:
            store = Neo4jStore(neo4j_uri, neo4j_user, neo4j_password)
            break
        except Exception:
            if attempt == max_retries - 1:
                raise
            click.echo(
                f"  Neo4j:      waiting for bolt to become ready"
                f" ({attempt + 1}/{max_retries})..."
            )
            time.sleep(5)

    assert store is not None
    store.ensure_schema()

    # Sync current cluster topology into the graph
    try:
        from chaosprobe.placement.mutator import PlacementMutator

        topo_mutator = PlacementMutator(namespace)
        nodes_raw = topo_mutator.get_nodes()
        deployments_raw = topo_mutator.get_deployments()
        store.sync_topology(
            [
                {
                    "name": n.name,
                    "cpu": n.allocatable_cpu_millicores,
                    "memory": n.allocatable_memory_bytes,
                    "control_plane": n.is_control_plane,
                }
                for n in nodes_raw
            ],
            [
                {"name": d.name, "namespace": d.namespace, "replicas": d.replicas}
                for d in deployments_raw
            ],
        )
    except Exception as e:
        click.echo(f"  Neo4j: topology sync skipped ({e})", err=True)

    store.sync_service_dependencies(routes=service_routes)
    click.echo(f"  Neo4j:      connected ({neo4j_uri})")
    return store


# ---------------------------------------------------------------------------
# 3.  Prober lifecycle helpers (delegated to probers.py)
# ---------------------------------------------------------------------------

from chaosprobe.orchestrator.probers import (  # noqa: E402, F401
    create_and_start_probers,
    stop_and_collect_probers,
)

# ---------------------------------------------------------------------------
# 4.  Final summary output
# ---------------------------------------------------------------------------

def _regenerate_presentation() -> None:
    """Re-run create_presentation.py so the .pptx reflects the latest charts."""
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "create_presentation.py"
    if not script.exists():
        return
    click.echo(f"\n{'─' * 60}")
    click.echo("Regenerating presentation...")
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                click.echo(f"  {line}")
        else:
            click.echo(f"  Warning: presentation generation failed: {result.stderr.strip()}")
    except Exception as e:
        click.echo(f"  Warning: could not regenerate presentation: {e}")


def write_run_results(
    overall_results: Dict[str, Any],
    results_dir: Any,
    *,
    passed: int,
    failed: int,
    total: int,
    ts: str,
    do_visualize: bool,
    graph_store: Any = None,
) -> None:
    """Write JSON result files, print final summary, and clean up."""
    import json as _json_mod
    from pathlib import Path

    from chaosprobe.metrics.remediation import generate_remediation_log

    results_dir = Path(results_dir)

    # Build comparison table
    iterations = overall_results.get("iterations", 1)
    comparison_table = _build_comparison_table_impl(
        overall_results["strategies"], iterations
    )
    overall_results["comparison"] = comparison_table

    # Remediation log
    overall_results["remediationLog"] = generate_remediation_log(overall_results)

    # Write per-strategy JSON files
    for strat_name, strat_data in overall_results.get("strategies", {}).items():
        strat_path = results_dir / f"{strat_name}.json"
        strat_path.write_text(_json_mod.dumps(strat_data, indent=2, default=str))

    summary_path = results_dir / "summary.json"
    summary_path.write_text(_json_mod.dumps(overall_results, indent=2, default=str))

    # Print final summary
    click.echo(f"\n{'=' * 60}")
    click.echo("EXPERIMENT RESULTS")
    click.echo(f"{'=' * 60}")

    has_recovery = any(r.get("avgRecovery_ms") is not None for r in comparison_table)
    if has_recovery:
        click.echo(
            f"\n  {'Strategy':<16s} {'Verdict':<8s} {'Score (mean +/- sd)':<22s} "
            f"{'Range':<10s} {'Avg Rec.':<10s} {'Max Rec.':<10s} {'Status'}"
        )
        click.echo(f"  {'─' * 90}")
        for row in comparison_table:
            avg_r = (
                f"{row['avgRecovery_ms']:.0f}ms"
                if row.get("avgRecovery_ms") is not None
                else "n/a"
            )
            max_r = (
                f"{row['maxRecovery_ms']:.0f}ms"
                if row.get("maxRecovery_ms") is not None
                else "n/a"
            )
            stddev = row.get("stddevScore", 0.0)
            score_str = f"{row['resilienceScore']:.1f} +/- {stddev:.1f}"
            range_str = row.get("scoreRange", "") or "n/a"
            click.echo(
                f"  {row['strategy']:<16s} {row['verdict']:<8s} "
                f"{score_str:<22s} {range_str:<10s} {avg_r:<10s} {max_r:<10s} {row['status']}"
            )
    else:
        click.echo(f"\n  {'Strategy':<20s} {'Verdict':<10s} {'Score':<10s} {'Status'}")
        click.echo(f"  {'─' * 55}")
        for row in comparison_table:
            click.echo(
                f"  {row['strategy']:<20s} {row['verdict']:<10s} "
                f"{row['resilienceScore']:<10.1f} {row['status']}"
            )

    click.echo(f"\n  Session: {ts}")
    click.echo(f"\n  Total: {total} | Passed: {passed} | Failed: {failed}")

    # Per-iteration score breakdown (only for multi-iteration runs)
    has_iters = any(len(r.get("perIterationScores", [])) > 1 for r in comparison_table)
    if has_iters:
        click.echo("\n  Per-Iteration Scores:")
        for row in comparison_table:
            scores = row.get("perIterationScores", [])
            if scores:
                scores_str = ", ".join(f"{s:.0f}" for s in scores)
                click.echo(f"    {row['strategy']:<16s} [{scores_str}]")

    # Generate visualizations if requested
    if do_visualize:
        click.echo(f"\n{'─' * 60}")
        click.echo("Generating visualizations...")
        try:
            from chaosprobe.output.visualize import generate_from_dict

            charts_dir = str(results_dir / "charts")
            generated = generate_from_dict(overall_results, charts_dir)
            if generated:
                click.echo(f"  Generated {len(generated)} file(s) in {charts_dir}")
                html_files = [p for p in generated if p.endswith(".html")]
                if html_files:
                    click.echo(f"  Report: {html_files[0]}")
            else:
                click.echo("  No data available to visualize.")
        except ImportError as e:
            click.echo(f"  Skipping visualization: {e}", err=True)

    # Regenerate thesis presentation (picks up latest charts)
    _regenerate_presentation()

    # Close graph database connection
    if graph_store:
        graph_store.close()

    # Show ChaosCenter dashboard link if available
    try:
        dash_setup = LitmusSetup()
        if dash_setup.is_chaoscenter_installed():
            dash_url = dash_setup.get_dashboard_url()
            if dash_url:
                click.echo(f"  ChaosCenter dashboard: {dash_url}")
    except Exception:
        pass

    click.echo("")


def _build_comparison_table_impl(
    strategies: Dict[str, Any], iterations: int
) -> List[Dict[str, Any]]:
    """Build the comparison table from strategy results.

    This is the implementation moved from cli.py's _build_comparison_table.
    """
    table: List[Dict[str, Any]] = []
    for name, data in strategies.items():
        row: Dict[str, Any] = {
            "strategy": name,
            "status": data.get("status", "unknown"),
            "verdict": "ERROR",
            "resilienceScore": 0.0,
            "stddevScore": 0.0,
            "scoreRange": "",
            "avgRecovery_ms": None,
            "maxRecovery_ms": None,
            "stddevRecovery_ms": None,
            "perIterationScores": [],
        }
        if data.get("status") == "error":
            row["verdict"] = "ERROR"
            table.append(row)
            continue

        if iterations > 1:
            agg = data.get("aggregated", {})
            row["verdict"] = "PASS" if agg.get("passRate", 0) == 1.0 else "FAIL"
            # Prefer healthy-only mean when tainted iterations exist,
            # so scores reflect actual strategy resilience rather than
            # accumulated damage from cascading iteration poisoning.
            if agg.get("taintedIterations", 0) > 0 and not agg.get("allIterationsTainted", False):
                healthy_mean = agg.get(
                    "meanResilienceScore_healthyOnly",
                )
                row["resilienceScore"] = (
                    healthy_mean if healthy_mean is not None
                    else agg.get("meanResilienceScore", 0.0)
                )
                healthy_sd = agg.get(
                    "stddevResilienceScore_healthyOnly",
                )
                row["stddevScore"] = (
                    healthy_sd if healthy_sd is not None
                    else agg.get("stddevResilienceScore", 0.0)
                )
            else:
                row["resilienceScore"] = agg.get("meanResilienceScore", 0.0)
                row["stddevScore"] = agg.get("stddevResilienceScore", 0.0)
            min_s = agg.get("minResilienceScore")
            max_s = agg.get("maxResilienceScore")
            if min_s is not None and max_s is not None:
                row["scoreRange"] = f"{min_s:.0f}-{max_s:.0f}"
            row["avgRecovery_ms"] = agg.get("meanRecoveryTime_ms")
            row["maxRecovery_ms"] = agg.get("maxRecoveryTime_ms")
            row["stddevRecovery_ms"] = agg.get("stddevRecoveryTime_ms")
            row["perIterationScores"] = agg.get("perIterationScores", [])
        else:
            exp = data.get("experiment", {})
            row["verdict"] = exp.get("overallVerdict", "UNKNOWN")
            row["resilienceScore"] = exp.get("resilienceScore", 0.0)
            metrics = data.get("metrics", {})
            recovery = metrics.get("recovery", {}).get("summary", {}) if metrics else {}
            row["avgRecovery_ms"] = recovery.get("meanRecovery_ms")
            row["maxRecovery_ms"] = recovery.get("maxRecovery_ms")
        table.append(row)
    return table


# ---------------------------------------------------------------------------
# 6.  Multi-iteration aggregation
# ---------------------------------------------------------------------------

def aggregate_iterations(
    iteration_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute aggregated statistics across multiple iterations."""
    if not iteration_results:
        return {
            "overallVerdict": "FAIL",
            "passRate": 0.0,
            "meanResilienceScore": 0.0,
            "totalExperiments": 0,
            "passed": 0,
            "failed": 0,
            "meanRecoveryTime_ms": None,
            "medianRecoveryTime_ms": None,
            "maxRecoveryTime_ms": None,
        }

    scores = [ir["resilienceScore"] for ir in iteration_results]
    verdicts = [ir["verdict"] for ir in iteration_results]
    pass_count = sum(1 for v in verdicts if v == "PASS")

    # Track how many iterations had a healthy pre-chaos baseline.
    # Tainted iterations (pre-chaos already degraded) produce unreliable
    # scores because they reflect accumulated damage, not strategy resilience.
    healthy_iters = [ir for ir in iteration_results if ir.get("preChaosHealthy", True)]
    tainted_count = len(iteration_results) - len(healthy_iters)
    all_tainted = len(healthy_iters) == 0 and tainted_count > 0
    healthy_scores = [ir["resilienceScore"] for ir in healthy_iters] if healthy_iters else scores

    healthy_stddev = (
        round(statistics.stdev(healthy_scores), 1) if len(healthy_scores) > 1 else 0.0
    )
    agg: Dict[str, Any] = {
        "overallVerdict": "PASS" if pass_count == len(verdicts) else "FAIL",
        "passRate": round(pass_count / len(verdicts), 2),
        "meanResilienceScore": round(statistics.mean(scores), 1),
        "meanResilienceScore_healthyOnly": round(statistics.mean(healthy_scores), 1),
        "stddevResilienceScore": round(statistics.stdev(scores), 1) if len(scores) > 1 else 0.0,
        "stddevResilienceScore_healthyOnly": healthy_stddev,
        "minResilienceScore": min(scores),
        "maxResilienceScore": max(scores),
        "totalExperiments": len(iteration_results),
        "passed": pass_count,
        "failed": len(verdicts) - pass_count,
        "taintedIterations": tainted_count,
        "allIterationsTainted": all_tainted,
        "perIterationScores": scores,
    }

    # Collect per-probe verdict tallies across iterations
    probe_tally: Dict[str, Dict[str, int]] = {}
    for ir in iteration_results:
        for pname, pverdict in ir.get("probeVerdicts", {}).items():
            probe_tally.setdefault(pname, {"Pass": 0, "Fail": 0, "Unknown": 0})
            if pverdict in probe_tally[pname]:
                probe_tally[pname][pverdict] += 1
            else:
                probe_tally[pname]["Unknown"] += 1
    if probe_tally:
        agg["probeVerdictTally"] = probe_tally

    # Aggregate recovery metrics from metrics.recovery.summary
    all_recovery_times: List[float] = []
    for ir in iteration_results:
        rm = ir.get("metrics", {})
        if rm:
            summary = rm.get("recovery", {}).get("summary", {})
            mean_r = summary.get("meanRecovery_ms")
            if mean_r is not None:
                all_recovery_times.append(mean_r)

    if all_recovery_times:
        all_max = []
        all_p95 = []
        for ir in iteration_results:
            rm = ir.get("metrics", {})
            if rm:
                summary = rm.get("recovery", {}).get("summary", {})
                max_r = summary.get("maxRecovery_ms")
                if max_r is not None:
                    all_max.append(max_r)
                p95_r = summary.get("p95Recovery_ms")
                if p95_r is not None:
                    all_p95.append(p95_r)

        agg["meanRecoveryTime_ms"] = round(statistics.mean(all_recovery_times), 1)
        agg["stddevRecoveryTime_ms"] = (
            round(statistics.stdev(all_recovery_times), 1)
            if len(all_recovery_times) > 1
            else 0.0
        )
        agg["medianRecoveryTime_ms"] = round(statistics.median(all_recovery_times), 1)
        agg["maxRecoveryTime_ms"] = max(all_max) if all_max else None
        # Aggregate p95: use mean of per-iteration p95 values.
        # Each all_p95 element is already a p95 from that iteration;
        # averaging them gives a representative cross-iteration p95.
        # (Taking max() would report the worst-case outlier, not a
        # proper aggregate percentile.)
        agg["p95RecoveryTime_ms"] = (
            round(statistics.mean(all_p95), 1) if all_p95 else None
        )
    else:
        agg["meanRecoveryTime_ms"] = None
        agg["stddevRecoveryTime_ms"] = None
        agg["medianRecoveryTime_ms"] = None
        agg["maxRecoveryTime_ms"] = None

    return agg
