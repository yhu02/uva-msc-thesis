"""Extracted phases for the ``chaosprobe run`` command.

Breaking the 1 100-line ``run()`` Click command into composable helper
functions so the top-level orchestrator stays small and readable.
"""

from __future__ import annotations

import statistics
import time
from typing import Any, Dict, List, Optional, Tuple

import click

from chaosprobe.orchestrator import portforward as pf
from chaosprobe.orchestrator.preflight import (
    check_pods_ready,
    wait_for_healthy_deployments,
)
from chaosprobe.provisioner.setup import LitmusSetup


# ---------------------------------------------------------------------------
# 1.  Pre-flight checks
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

    Verifies node readiness, cleans stale ChaosEngines / workflow pods,
    sets up port-forwards (Prometheus, Neo4j, ChaosCenter, frontend),
    auto-configures ChaosCenter, and waits for deployments.

    Returns a dict with keys:
        - ``core_api``: kubernetes CoreV1Api instance
        - ``chaoscenter_config``: dict or None
        - ``target_url``: str (may be auto-discovered)
        - ``frontend_pf_port``: int
    """
    from kubernetes import client as k8s_client_mod
    from kubernetes import config as k8s_config

    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
    core_api = k8s_client_mod.CoreV1Api()

    # 1. Verify all nodes are Ready
    nodes = core_api.list_node()
    not_ready_nodes = []
    for node in nodes.items:
        conditions = {c.type: c.status for c in (node.status.conditions or [])}
        if conditions.get("Ready") != "True":
            not_ready_nodes.append(node.metadata.name)
    if not_ready_nodes:
        click.echo(f"  Error: nodes not Ready: {', '.join(not_ready_nodes)}", err=True)
        click.echo("  Fix node issues before running experiments.", err=True)
        import sys
        sys.exit(1)
    click.echo(f"  Nodes:       {len(nodes.items)} Ready")

    # 2. Clean up stale ChaosEngines
    try:
        custom_api = k8s_client_mod.CustomObjectsApi()
        engines = custom_api.list_namespaced_custom_object(
            group="litmuschaos.io",
            version="v1alpha1",
            namespace=namespace,
            plural="chaosengines",
        )
        engine_items = engines.get("items", [])
        if engine_items:
            click.echo(f"  Cleaning up {len(engine_items)} stale ChaosEngine(s)...")
            for eng in engine_items:
                name = eng["metadata"]["name"]
                custom_api.delete_namespaced_custom_object(
                    group="litmuschaos.io",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="chaosengines",
                    name=name,
                )
            click.echo("  ChaosEngines: cleaned")
        else:
            click.echo("  ChaosEngines: none (clean)")
    except Exception as e:
        click.echo(f"  ChaosEngines: check skipped ({e})", err=True)

    # 2b. Clean up stale Argo workflow pods
    try:
        wf_pods = core_api.list_namespaced_pod(
            namespace,
            label_selector="workflows.argoproj.io/workflow",
        )
        stale_phases = {"Failed", "Error", "Succeeded"}
        stale_pods = [p for p in wf_pods.items if p.status.phase in stale_phases]
        if stale_pods:
            click.echo(f"  Cleaning up {len(stale_pods)} stale workflow pod(s)...")
            for pod in stale_pods:
                core_api.delete_namespaced_pod(name=pod.metadata.name, namespace=namespace)
            click.echo("  Workflow pods: cleaned")
        else:
            click.echo("  Workflow pods: none stale")
    except Exception as e:
        click.echo(f"  Workflow pods: check skipped ({e})", err=True)

    # 3. Verify infrastructure pods and set up port-forwards
    _preflight_setup = None

    def _get_setup():
        nonlocal _preflight_setup
        if _preflight_setup is None:
            _preflight_setup = LitmusSetup(skip_k8s_init=True)
            _preflight_setup._init_k8s_client()
        return _preflight_setup

    # Prometheus
    if measure_prometheus:
        prom_ready = False
        prom_namespaces = ("monitoring", "prometheus", "kube-prometheus")
        prom_labels = ("app=prometheus,component=server", "app.kubernetes.io/name=prometheus")
        for attempt in range(12):
            for ns in prom_namespaces:
                for label in prom_labels:
                    if check_pods_ready(ns, label):
                        prom_ready = True
                        break
                if prom_ready:
                    break
            if prom_ready:
                break
            if attempt == 0:
                click.echo("  Prometheus:  waiting for pod to become ready...")
            time.sleep(5)
        if prom_ready:
            click.echo("  Prometheus:  pod ready")
        else:
            click.echo("  Prometheus:  WARNING - no ready pod found after 60s", err=True)
            click.echo("               Run 'chaosprobe init' to reinstall.", err=True)

    # Neo4j
    if neo4j_uri:
        if check_pods_ready("neo4j", "app=neo4j"):
            click.echo("  Neo4j:       pod ready")
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
                    f"  Neo4j bolt:  {host}:{port} not reachable — starting port-forward...",
                )
                if pf.ensure("neo4j", "neo4j", ["7687:7687", "7474:7474"], host, port):
                    click.echo(f"  Neo4j bolt:  {host}:{port} reachable (port-forward started)")
                else:
                    click.echo(
                        f"  Neo4j bolt:  WARNING - still not reachable at {host}:{port}",
                        err=True,
                    )
        else:
            click.echo("  Neo4j:       WARNING - no ready pod. Run 'chaosprobe init'.", err=True)

    # ChaosCenter — port-forward dashboard + API, then auto-configure
    cc_frontend_svc = LitmusSetup.CHAOSCENTER_FRONTEND_SVC
    cc_frontend_port = LitmusSetup.CHAOSCENTER_FRONTEND_PORT
    cc_auth_svc = LitmusSetup.CHAOSCENTER_AUTH_SVC
    cc_auth_port = LitmusSetup.CHAOSCENTER_AUTH_PORT
    cc_server_svc = LitmusSetup.CHAOSCENTER_SERVER_SVC
    cc_server_port = LitmusSetup.CHAOSCENTER_SERVER_PORT
    chaoscenter_config = None

    if not check_pods_ready("litmus", "app.kubernetes.io/component=litmus-frontend"):
        raise click.ClickException(
            "ChaosCenter frontend pods are not ready in the 'litmus' namespace.\n"
            "  All experiments run through the ChaosCenter API.\n"
            "  Run 'chaosprobe init' first."
        )
    else:
        # Port-forward frontend (dashboard UI)
        if not pf.check_port("localhost", cc_frontend_port):
            pf.start(cc_frontend_svc, "litmus", [f"{cc_frontend_port}:{cc_frontend_port}"])
            if pf.check_port("localhost", cc_frontend_port):
                click.echo(
                    f"  ChaosCenter: http://localhost:{cc_frontend_port} (port-forward started)"
                )
            else:
                click.echo(
                    f"  ChaosCenter: WARNING - port-forward to localhost:{cc_frontend_port} failed",
                    err=True,
                )
        else:
            click.echo(f"  ChaosCenter: http://localhost:{cc_frontend_port}")

        # Port-forward auth server + GraphQL server for API access
        if not pf.check_port("localhost", cc_auth_port):
            pf.start(cc_auth_svc, "litmus", [f"{cc_auth_port}:{cc_auth_port}"])
        if not pf.check_port("localhost", cc_server_port):
            pf.start(cc_server_svc, "litmus", [f"{cc_server_port}:{cc_server_port}"])

        # Auto-configure: environment + infrastructure + subscriber
        try:
            setup = _get_setup()
            cc_result = setup.ensure_chaoscenter_configured(
                namespace=namespace,
                base_host="http://localhost",
            )
            chaoscenter_config = {
                "token": cc_result["token"],
                "project_id": cc_result["project_id"],
                "infra_id": cc_result["infra_id"],
                "gql_url": f"http://localhost:{cc_server_port}/query",
            }
            click.echo("  ChaosCenter: auto-configured for experiment visibility")
        except Exception as exc:
            raise click.ClickException(
                f"ChaosCenter auto-setup failed: {exc}\n"
                "  All experiments run through the ChaosCenter API.\n"
                "  Ensure ChaosCenter is installed and reachable."
            ) from exc

    # metrics-server — verify API works
    try:
        k8s_client_mod.CustomObjectsApi().list_cluster_custom_object(
            group="metrics.k8s.io",
            version="v1beta1",
            plural="nodes",
        )
        click.echo("  metrics-srv: API available")
    except Exception:
        click.echo("  metrics-srv: WARNING - metrics API not available", err=True)
        click.echo("               Run 'chaosprobe init' to install/repair.", err=True)

    # 4. Wait for all application deployments to be healthy
    click.echo("  Deployments: waiting for readiness...")
    wait_for_healthy_deployments(namespace, timeout=120)
    click.echo("  Deployments: all ready")

    # 5. Auto port-forward to frontend service for Locust load generation
    frontend_pf_port = 8089
    if target_url is None and load_profile:
        try:
            svc_list = core_api.list_namespaced_service(namespace)
            frontend_svc = None
            for svc in svc_list.items:
                if "frontend" in svc.metadata.name and "external" not in svc.metadata.name:
                    frontend_svc = svc.metadata.name
                    for p in svc.spec.ports or []:
                        if p.port in (80, 8080):
                            frontend_pf_port = 8089
                            break
                    break
            if frontend_svc:
                pf_mapping = f"{frontend_pf_port}:80"
                if pf.ensure(frontend_svc, namespace, [pf_mapping], "localhost", frontend_pf_port):
                    target_url = f"http://localhost:{frontend_pf_port}"
                    click.echo(f"  Load target: {target_url} (port-forward to {frontend_svc})")
                else:
                    click.echo(
                        f"  Load target: WARNING - port-forward to {frontend_svc} failed",
                        err=True,
                    )
                    target_url = f"http://localhost:{frontend_pf_port}"
            else:
                click.echo("  Load target: WARNING - no frontend service found", err=True)
                target_url = f"http://localhost:{frontend_pf_port}"
        except Exception as e:
            click.echo(
                f"  Load target: WARNING - failed to setup port-forward ({e})", err=True
            )
            target_url = f"http://localhost:{frontend_pf_port}"
    elif target_url is None:
        target_url = f"http://localhost:{frontend_pf_port}"

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

    # Terminate port-forward processes and monitor
    pf.cleanup()

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
