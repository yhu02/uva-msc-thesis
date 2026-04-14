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

LOAD_TARGET_LOCAL_PORT = 8089
from chaosprobe.orchestrator.preflight import (
    LITMUS_INFRA_DEPLOYMENTS,
    wait_for_healthy_deployments,
)
from chaosprobe.provisioner.setup import LitmusSetup


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

    # ChaosEngines
    try:
        custom_api = k8s_client_mod.CustomObjectsApi()
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
        custom_api = k8s_client_mod.CustomObjectsApi()
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
    except Exception:
        pass

    # Stale experiment jobs
    try:
        batch_api = k8s_client_mod.BatchV1Api()
        jobs = batch_api.list_namespaced_job(
            namespace, label_selector="app.kubernetes.io/part-of=litmus",
        )
        for job in jobs.items:
            if job.status.succeeded or job.status.failed:
                batch_api.delete_namespaced_job(
                    name=job.metadata.name, namespace=namespace,
                    propagation_policy="Background",
                )
    except Exception:
        pass

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
    except Exception:
        pass


def _setup_prometheus_pf(measure_prometheus: bool) -> None:
    """Verify Prometheus is reachable (port-forwarded by init)."""
    if not measure_prometheus:
        return
    if pf.check_port("localhost", 9090):
        click.echo("  Prometheus:  localhost:9090 reachable")
    else:
        click.echo(
            "  Prometheus:  WARNING - localhost:9090 not reachable. "
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
) -> Tuple[Optional[str], int]:
    """Ensure load target is reachable, setting up a port-forward if needed."""
    local_port = LOAD_TARGET_LOCAL_PORT
    if target_url is not None:
        click.echo(f"  Load target: {target_url}")
        return target_url, local_port
    if load_profile:
        if not pf.check_port("localhost", local_port):
            pf.ensure(
                "frontend", namespace,
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
    _check_metrics_server()

    click.echo("  Deployments: waiting for readiness...")
    wait_for_healthy_deployments(namespace, timeout=120)
    click.echo("  Deployments: all ready")

    target_url, frontend_pf_port = _setup_load_target(
        namespace, load_profile, target_url,
    )

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
            f"\n  {'Strategy':<16s} {'Verdict':<8s} {'Score':<8s} "
            f"{'Avg Rec.':<10s} {'Max Rec.':<10s} {'Status'}"
        )
        click.echo(f"  {'─' * 68}")
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
            click.echo(
                f"  {row['strategy']:<16s} {row['verdict']:<8s} "
                f"{row['resilienceScore']:<8.1f} {avg_r:<10s} {max_r:<10s} {row['status']}"
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
            "avgRecovery_ms": None,
            "maxRecovery_ms": None,
        }
        if data.get("status") == "error":
            row["verdict"] = "ERROR"
            table.append(row)
            continue

        if iterations > 1:
            agg = data.get("aggregated", {})
            row["verdict"] = "PASS" if agg.get("passRate", 0) == 1.0 else "FAIL"
            row["resilienceScore"] = agg.get("meanResilienceScore", 0.0)
            row["avgRecovery_ms"] = agg.get("meanRecoveryTime_ms")
            row["maxRecovery_ms"] = agg.get("maxRecoveryTime_ms")
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
    scores = [ir["resilienceScore"] for ir in iteration_results]
    verdicts = [ir["verdict"] for ir in iteration_results]
    pass_count = sum(1 for v in verdicts if v == "PASS")

    agg: Dict[str, Any] = {
        "overallVerdict": "PASS" if pass_count == len(verdicts) else "FAIL",
        "passRate": round(pass_count / len(verdicts), 2),
        "meanResilienceScore": round(statistics.mean(scores), 1),
        "totalExperiments": len(iteration_results),
        "passed": pass_count,
        "failed": len(verdicts) - pass_count,
    }

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
        for ir in iteration_results:
            rm = ir.get("metrics", {})
            if rm:
                max_r = rm.get("recovery", {}).get("summary", {}).get("maxRecovery_ms")
                if max_r is not None:
                    all_max.append(max_r)

        agg["meanRecoveryTime_ms"] = round(statistics.mean(all_recovery_times), 1)
        agg["medianRecoveryTime_ms"] = round(statistics.median(all_recovery_times), 1)
        agg["maxRecoveryTime_ms"] = max(all_max) if all_max else None
    else:
        agg["meanRecoveryTime_ms"] = None
        agg["medianRecoveryTime_ms"] = None
        agg["maxRecoveryTime_ms"] = None

    return agg
