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


def _percentile(sorted_values: List[float], p: float) -> float:
    """Linear-interpolated percentile from an already-sorted sequence."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    import math as _m

    k = (len(sorted_values) - 1) * p
    f = _m.floor(k)
    c = _m.ceil(k)
    if f == c:
        return float(sorted_values[int(k)])
    return float(sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f))


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
            group="litmuschaos.io",
            version="v1alpha1",
            namespace=namespace,
            plural="chaosengines",
        )
        items = engines.get("items", [])
        if items:
            click.echo(f"  Cleaning up {len(items)} stale ChaosEngine(s)...")
            for eng in items:
                custom_api.delete_namespaced_custom_object(
                    group="litmuschaos.io",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="chaosengines",
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
            group="litmuschaos.io",
            version="v1alpha1",
            namespace=namespace,
            plural="chaosresults",
        )
        items = results.get("items", [])
        if items:
            click.echo(f"  Cleaning up {len(items)} stale ChaosResult(s)...")
            for res in items:
                custom_api.delete_namespaced_custom_object(
                    group="litmuschaos.io",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="chaosresults",
                    name=res["metadata"]["name"],
                )
            click.echo("  ChaosResults: cleaned")
    except Exception as e:
        click.echo(f"  ChaosResults: cleanup skipped ({e})", err=True)

    # Stale experiment jobs
    try:
        batch_api = k8s_client_mod.BatchV1Api()
        jobs = batch_api.list_namespaced_job(
            namespace,
            label_selector="app.kubernetes.io/part-of=litmus",
        )
        for job in jobs.items:
            succeeded = getattr(job.status, "succeeded", None) if job.status else None
            failed = getattr(job.status, "failed", None) if job.status else None
            if succeeded or failed:
                batch_api.delete_namespaced_job(
                    name=job.metadata.name,
                    namespace=namespace,
                    propagation_policy="Background",
                )
    except Exception as e:
        click.echo(f"  Stale jobs: cleanup skipped ({e})", err=True)

    # Stale Argo workflow pods
    try:
        core_api = k8s_client_mod.CoreV1Api()
        wf_pods = core_api.list_namespaced_pod(
            namespace,
            label_selector="workflows.argoproj.io/workflow",
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
                cs.state
                and cs.state.waiting
                and cs.state.waiting.reason in ("CrashLoopBackOff", "Error", "CreateContainerError")
                for pod in pods.items
                for cs in (pod.status.container_statuses or [])
            )
            if needs_restart:
                click.echo(f"  Restarting unhealthy {dep_name}...")
                apps_api.patch_namespaced_deployment(
                    dep_name,
                    namespace,
                    {
                        "spec": {
                            "template": {
                                "metadata": {
                                    "annotations": {
                                        "chaosprobe.io/restartedAt": datetime.now(
                                            timezone.utc
                                        ).isoformat(),
                                    }
                                }
                            }
                        },
                    },
                )
                for _ in range(24):
                    time.sleep(5)
                    try:
                        dep = apps_api.read_namespaced_deployment(dep_name, namespace)
                        desired = dep.spec.replicas if dep.spec.replicas is not None else 1
                        ready = (dep.status.ready_replicas or 0) if dep.status else 0
                        if ready >= desired:
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
            "Check that Prometheus is deployed.",
            err=True,
        )


def _setup_neo4j_pf(neo4j_uri: Optional[str]) -> None:
    """Verify Neo4j is reachable; auto-establish port-forward if needed."""
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
        return
    # Port not reachable — attempt to establish port-forward
    click.echo(f"  Neo4j bolt:  {host}:{port} not reachable, establishing port-forward...")
    if pf.ensure("neo4j", "neo4j", ["7687:7687", "7474:7474"], host, port):
        click.echo(f"  Neo4j bolt:  {host}:{port} reachable")
    else:
        click.echo(
            f"  Neo4j bolt:  WARNING - {host}:{port} not reachable after port-forward. "
            "Check that Neo4j is deployed.",
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
    """Verify ChaosCenter port-forwards are active, auto-establishing them if
    needed (port-forwards from a previous ``init`` may have died), then
    auto-configure the ChaosCenter environment/infrastructure."""
    cc_frontend_svc = LitmusSetup.CHAOSCENTER_FRONTEND_SVC
    cc_frontend_port = LitmusSetup.CHAOSCENTER_FRONTEND_PORT
    cc_auth_svc = LitmusSetup.CHAOSCENTER_AUTH_SVC
    cc_auth_port = LitmusSetup.CHAOSCENTER_AUTH_PORT
    cc_server_svc = LitmusSetup.CHAOSCENTER_SERVER_SVC
    cc_server_port = LitmusSetup.CHAOSCENTER_SERVER_PORT

    _cc_pf_specs = [
        (cc_frontend_svc, cc_frontend_port, "frontend"),
        (cc_auth_svc, cc_auth_port, "auth server"),
        (cc_server_svc, cc_server_port, "GraphQL server"),
    ]
    for svc_name, port, label in _cc_pf_specs:
        if not pf.check_port("localhost", port):
            pf.start(svc_name, "litmus", [f"{port}:{port}"])
        if not pf.check_port("localhost", port):
            raise click.ClickException(
                f"ChaosCenter {label} not reachable at localhost:{port}.\n"
                "  Ensure ChaosCenter is deployed and its pods are running."
            )

    click.echo(f"  ChaosCenter: http://localhost:{cc_frontend_port}")

    # Auto-configure
    try:
        setup = LitmusSetup(skip_k8s_init=True)
        setup.init_k8s_client()
        cc_result = setup.ensure_chaoscenter_configured(
            namespace=namespace,
            base_host="http://localhost",
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
            group="metrics.k8s.io",
            version="v1beta1",
            plural="nodes",
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
                load_service,
                namespace,
                [f"{local_port}:80"],
                "localhost",
                local_port,
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
        namespace,
        load_profile,
        target_url,
        load_service=load_service,
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


def _strip_iteration_metrics(overall_results: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *overall_results* with bulky per-iteration metrics removed.

    Per-strategy JSON files already contain the full per-iteration data.
    Stripping metrics from the summary reduces file size by ~60-70%.
    """
    import copy

    slim = copy.deepcopy(overall_results)
    for strat_data in slim.get("strategies", {}).values():
        for it in strat_data.get("iterations", []):
            it.pop("metrics", None)
            it.pop("anomalyLabels", None)
            it.pop("cascadeTimeline", None)
            it.pop("unknownDiagnostics", None)
    return slim


def _print_probe_failure_analysis(strategies: Dict[str, Any]) -> None:
    """Print per-probe failure analysis across strategies.

    Shows which probes consistently fail to help diagnose score variance.
    """
    # Collect probe tallies across all strategies
    has_tallies = False
    for sdata in strategies.values():
        agg = sdata.get("aggregated", {})
        if agg.get("probeVerdictTally"):
            has_tallies = True
            break

    if not has_tallies:
        return

    click.echo("\n  Probe Failure Analysis:")
    for strat_name, sdata in strategies.items():
        agg = sdata.get("aggregated", {})
        tally = agg.get("probeVerdictTally", {})
        if not tally:
            continue
        failing_probes = [
            (name, counts) for name, counts in tally.items() if counts.get("Fail", 0) > 0
        ]
        if not failing_probes:
            click.echo(f"    {strat_name:<16s} all probes passed in all iterations")
            continue
        n_iters = agg.get("totalExperiments", 5)
        parts = []
        for pname, counts in sorted(failing_probes, key=lambda x: -x[1].get("Fail", 0)):
            parts.append(f"{pname}({counts['Fail']}/{n_iters})")
        click.echo(f"    {strat_name:<16s} {', '.join(parts)}")


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
    comparison_table = _build_comparison_table_impl(overall_results["strategies"], iterations)
    overall_results["comparison"] = comparison_table

    # Remediation log
    overall_results["remediationLog"] = generate_remediation_log(overall_results)

    # Write per-strategy JSON files (full data including per-iteration metrics)
    for strat_name, strat_data in overall_results.get("strategies", {}).items():
        strat_path = results_dir / f"{strat_name}.json"
        strat_path.write_text(_json_mod.dumps(strat_data, indent=2, default=str))

    # Write summary.json with per-iteration metrics stripped to reduce size.
    # Full per-iteration data is already in the per-strategy JSON files.
    summary_slim = _strip_iteration_metrics(overall_results)
    summary_path = results_dir / "summary.json"
    summary_path.write_text(_json_mod.dumps(summary_slim, indent=2, default=str))

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
                f"{row['avgRecovery_ms']:.0f}ms" if row.get("avgRecovery_ms") is not None else "n/a"
            )
            max_r = (
                f"{row['maxRecovery_ms']:.0f}ms" if row.get("maxRecovery_ms") is not None else "n/a"
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

    # Per-probe failure analysis (helps diagnose which probes drive score variance)
    _print_probe_failure_analysis(overall_results.get("strategies", {}))

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
                    healthy_mean
                    if healthy_mean is not None
                    else agg.get("meanResilienceScore", 0.0)
                )
                healthy_sd = agg.get(
                    "stddevResilienceScore_healthyOnly",
                )
                row["stddevScore"] = (
                    healthy_sd if healthy_sd is not None else agg.get("stddevResilienceScore", 0.0)
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

    # Exclude ERROR iterations (infra failures, all-Unknown probes) from
    # score statistics.  These are not valid measurements — including
    # their 0.0 scores would drag down the mean and inflate stddev
    # without reflecting actual strategy resilience.
    valid_iters = [ir for ir in iteration_results if ir["verdict"] != "ERROR"]
    error_count = len(iteration_results) - len(valid_iters)
    valid_scores = [ir["resilienceScore"] for ir in valid_iters] if valid_iters else scores

    # Track how many iterations had a healthy pre-chaos baseline.
    # Tainted iterations (pre-chaos already degraded) produce unreliable
    # scores because they reflect accumulated damage, not strategy resilience.
    healthy_iters = [ir for ir in valid_iters if ir.get("preChaosHealthy", True)]
    tainted_count = len(valid_iters) - len(healthy_iters)
    all_tainted = len(healthy_iters) == 0 and tainted_count > 0
    healthy_scores = (
        [ir["resilienceScore"] for ir in healthy_iters] if healthy_iters else valid_scores
    )

    healthy_stddev = round(statistics.stdev(healthy_scores), 1) if len(healthy_scores) > 1 else 0.0
    valid_stddev = round(statistics.stdev(valid_scores), 1) if len(valid_scores) > 1 else 0.0

    # ── Tail-aware score variants ──────────────────────────────────────
    # The arithmetic mean hides exactly the tail failures Dean & Barroso
    # ("The Tail at Scale", CACM 2013) argue dominate user-perceived
    # quality.  Surface them so the user can pick the right point estimate
    # for their argument and so the discussion can refer to actual tail
    # percentiles rather than just the mean.
    sorted_valid = sorted(valid_scores)
    p25_score = _percentile(sorted_valid, 0.25)
    # Harmonic mean penalises low values disproportionately.  Compute on
    # (score + 1) to avoid divide-by-zero when a probe was fully wiped
    # out (score=0); subtract 1 after.  Bounded to [0, 100] for sanity.
    harm_mean = statistics.harmonic_mean([s + 1.0 for s in valid_scores]) - 1.0
    harm_mean = max(0.0, min(100.0, harm_mean))

    # ── Bootstrap CI for the mean ──────────────────────────────────────
    # With n=3 and stddev~25-30, the point-estimate gap between many
    # strategies is well inside the noise floor.  Reporting a bootstrap
    # 95% CI makes the uncertainty visible up-front rather than burying
    # it.
    from chaosprobe.metrics.statistics import bootstrap_ci

    mean_ci = bootstrap_ci(valid_scores, statistic="mean")

    agg: Dict[str, Any] = {
        "overallVerdict": "PASS" if pass_count == len(verdicts) else "FAIL",
        "passRate": round(pass_count / len(verdicts), 2),
        "meanResilienceScore": round(statistics.mean(valid_scores), 1),
        "meanResilienceScore_healthyOnly": round(statistics.mean(healthy_scores), 1),
        "stddevResilienceScore": valid_stddev,
        "stddevResilienceScore_healthyOnly": healthy_stddev,
        "minResilienceScore": min(valid_scores),
        "maxResilienceScore": max(valid_scores),
        "p25ResilienceScore": round(p25_score, 1),
        "harmonicMeanResilienceScore": round(harm_mean, 1),
        "meanResilienceScore_ci95": {
            "low": mean_ci["ci_low"],
            "high": mean_ci["ci_high"],
            "n": mean_ci["n"],
            "n_resamples": mean_ci["n_resamples"],
        },
        "totalExperiments": len(iteration_results),
        "passed": pass_count,
        "failed": len(verdicts) - pass_count - error_count,
        "errors": error_count,
        "taintedIterations": tainted_count,
        "allIterationsTainted": all_tainted,
        "perIterationScores": scores,
    }

    # Taint reason taxonomy across iterations.  preChaosTaintReasons is
    # a list (multiple gates can fire on one iteration); counting per
    # reason answers "is the taint pattern consistent" — same reason
    # every time suggests a clear root cause; mixed reasons usually
    # reflect cluster noise.
    taint_reason_counts: Dict[str, int] = {}
    for ir in iteration_results:
        reasons = ir.get("preChaosTaintReasons") or []
        if not isinstance(reasons, list):
            continue
        for reason in reasons:
            if not isinstance(reason, str):
                continue
            taint_reason_counts[reason] = taint_reason_counts.get(reason, 0) + 1
    if taint_reason_counts:
        agg["taintReasonCounts"] = taint_reason_counts

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
        # Per-probe success-rate + Wilson 95% CI.  A defender comparing
        # probe-level success between strategies needs intervals — a
        # 4/5 Pass and a 80/100 Pass are both "80%" by point estimate
        # but have very different uncertainty.
        from chaosprobe.metrics.statistics import wilson_ci as _wilson_ci

        success_rates: Dict[str, Dict[str, Any]] = {}
        for pname, counts in probe_tally.items():
            decided = counts["Pass"] + counts["Fail"]
            success_rates[pname] = {
                **_wilson_ci(counts["Pass"], decided),
                "unknown": counts["Unknown"],
            }
        agg["probeSuccessRates"] = success_rates

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
            round(statistics.stdev(all_recovery_times), 1) if len(all_recovery_times) > 1 else 0.0
        )
        agg["medianRecoveryTime_ms"] = round(statistics.median(all_recovery_times), 1)
        agg["maxRecoveryTime_ms"] = max(all_max) if all_max else None
        # Coefficient of variation = stddev / mean.  Decouples spread
        # from scale: a strategy that always recovers in 1000±100 ms
        # (CV=0.10) is steadier than one at 200±100 ms (CV=0.50) even
        # though their stddevs are identical.  Pure within-strategy
        # jitter signal — a defender pointing at a low-CV strategy can
        # claim "predictable recovery", not just "fast on average".
        if agg["meanRecoveryTime_ms"] and agg["meanRecoveryTime_ms"] > 0:
            agg["recoveryTimeCV"] = round(
                agg["stddevRecoveryTime_ms"] / agg["meanRecoveryTime_ms"], 3
            )
        else:
            agg["recoveryTimeCV"] = None
        # Aggregate p95: use mean of per-iteration p95 values.
        # Each all_p95 element is already a p95 from that iteration;
        # averaging them gives a representative cross-iteration p95.
        # (Taking max() would report the worst-case outlier, not a
        # proper aggregate percentile.)
        agg["p95RecoveryTime_ms"] = round(statistics.mean(all_p95), 1) if all_p95 else None

        # The thesis's H9 attribution is "scheduling latency dominates
        # recovery" — it lives or dies on the mean of meanRecovery_ms and
        # its split. A point estimate without a CI is not defensible at n=5
        # iterations × ~25-30 stddev, so surface the bootstrap interval
        # alongside the point estimate (matches meanResilienceScore_ci95).
        recovery_ci = bootstrap_ci(all_recovery_times, statistic="mean")
        agg["meanRecoveryTime_ms_ci95"] = {
            "low": recovery_ci["ci_low"],
            "high": recovery_ci["ci_high"],
            "n": recovery_ci["n"],
            "n_resamples": recovery_ci["n_resamples"],
        }

        # Surface the deletion->scheduled vs scheduled->ready split.  Lets
        # downstream analysis distinguish scheduler stalls (large d2s, e.g.
        # affinity collision) from genuine container-start latency (large s2r).
        all_d2s: List[float] = []
        all_s2r: List[float] = []
        for ir in iteration_results:
            rm = ir.get("metrics", {})
            if not rm:
                continue
            summary = rm.get("recovery", {}).get("summary", {})
            v = summary.get("meanDeletionToScheduled_ms")
            if v is not None:
                all_d2s.append(v)
            v = summary.get("meanScheduledToReady_ms")
            if v is not None:
                all_s2r.append(v)

        if all_d2s:
            mean_d2s = round(statistics.mean(all_d2s), 1)
            stddev_d2s = round(statistics.stdev(all_d2s), 1) if len(all_d2s) > 1 else 0.0
            agg["meanDeletionToScheduled_ms"] = mean_d2s
            agg["stddevDeletionToScheduled_ms"] = stddev_d2s
            agg["deletionToScheduledCV"] = (
                round(stddev_d2s / mean_d2s, 3) if mean_d2s > 0 else None
            )
            d2s_ci = bootstrap_ci(all_d2s, statistic="mean")
            agg["meanDeletionToScheduled_ms_ci95"] = {
                "low": d2s_ci["ci_low"],
                "high": d2s_ci["ci_high"],
                "n": d2s_ci["n"],
                "n_resamples": d2s_ci["n_resamples"],
            }
        if all_s2r:
            mean_s2r = round(statistics.mean(all_s2r), 1)
            stddev_s2r = round(statistics.stdev(all_s2r), 1) if len(all_s2r) > 1 else 0.0
            agg["meanScheduledToReady_ms"] = mean_s2r
            agg["stddevScheduledToReady_ms"] = stddev_s2r
            agg["scheduledToReadyCV"] = (
                round(stddev_s2r / mean_s2r, 3) if mean_s2r > 0 else None
            )
            s2r_ci = bootstrap_ci(all_s2r, statistic="mean")
            agg["meanScheduledToReady_ms_ci95"] = {
                "low": s2r_ci["ci_low"],
                "high": s2r_ci["ci_high"],
                "n": s2r_ci["n"],
                "n_resamples": s2r_ci["n_resamples"],
            }

        # Aggregate Locust load-generation stats across iterations so each
        # strategy reports the actual offered RPS / error rate that drove
        # its score.  Without this, a reviewer cannot rule out load drift
        # as the cause of inter-strategy score differences.
        rps_vals: List[float] = []
        err_vals: List[float] = []
        resp_vals: List[float] = []
        for ir in iteration_results:
            lg = ir.get("loadGeneration") or {}
            stats = lg.get("stats") or {}
            v = stats.get("requestsPerSecond")
            if v is not None:
                rps_vals.append(float(v))
            v = stats.get("errorRate")
            if v is not None:
                err_vals.append(float(v))
            v = stats.get("p95ResponseTime_ms") or stats.get("avgResponseTime_ms")
            if v is not None:
                resp_vals.append(float(v))
        if rps_vals or err_vals or resp_vals:

            def _ci_block(values: List[float]) -> Dict[str, Any]:
                ci = bootstrap_ci(values, statistic="mean")
                return {
                    "low": ci["ci_low"],
                    "high": ci["ci_high"],
                    "n": ci["n"],
                    "n_resamples": ci["n_resamples"],
                }

            load_agg: Dict[str, Any] = {}
            if rps_vals:
                load_agg["meanRequestsPerSecond"] = round(statistics.mean(rps_vals), 2)
                load_agg["stddevRequestsPerSecond"] = (
                    round(statistics.stdev(rps_vals), 2) if len(rps_vals) > 1 else 0.0
                )
                load_agg["meanRequestsPerSecond_ci95"] = _ci_block(rps_vals)
            if err_vals:
                load_agg["meanErrorRate"] = round(statistics.mean(err_vals), 4)
                load_agg["meanErrorRate_ci95"] = _ci_block(err_vals)
            if resp_vals:
                load_agg["meanResponseTime_ms"] = round(statistics.mean(resp_vals), 1)
                load_agg["meanResponseTime_ms_ci95"] = _ci_block(resp_vals)
            agg["loadGenerationAggregate"] = load_agg
    else:
        agg["meanRecoveryTime_ms"] = None
        agg["stddevRecoveryTime_ms"] = None
        agg["medianRecoveryTime_ms"] = None
        agg["maxRecoveryTime_ms"] = None

    # ── Per-strategy scheduler-event roll-up ──────────────────────────
    # Each iteration carries a metrics.recovery.schedulerEvents list of
    # {reason, ...} dicts.  Aggregating reason counts across iterations
    # makes "FailedScheduling fires 4x more often on adversarial than on
    # spread" — and the same for image-pull / BackOff / Killing — a
    # directly readable per-strategy number rather than something a
    # reader has to compute by hand from the iteration list.
    scheduler_event_totals: Dict[str, int] = {}
    scheduler_event_per_iter: Dict[str, List[int]] = {}
    iterations_with_events = 0
    for ir in iteration_results:
        events = (ir.get("metrics", {}).get("recovery", {}) or {}).get("schedulerEvents")
        if not events:
            continue
        iterations_with_events += 1
        per_iter_counts: Dict[str, int] = {}
        for e in events:
            reason = e.get("reason") if isinstance(e, dict) else None
            if not reason:
                continue
            scheduler_event_totals[reason] = scheduler_event_totals.get(reason, 0) + 1
            per_iter_counts[reason] = per_iter_counts.get(reason, 0) + 1
        for reason, count in per_iter_counts.items():
            scheduler_event_per_iter.setdefault(reason, []).append(count)

    if scheduler_event_totals:
        # `meanPerIteration` denominates by the number of iterations that
        # carried any events, not by total iterations — this prevents a
        # silent zero from non-recording iterations (e.g. probe-only runs)
        # from biasing the per-strategy attribution downward.
        agg["schedulerEventCounts"] = {
            reason: {
                "total": total,
                "meanPerIteration": round(
                    statistics.mean(scheduler_event_per_iter.get(reason, [0])), 2
                ),
                "maxPerIteration": max(scheduler_event_per_iter.get(reason, [0])),
                "iterationsObserved": len(scheduler_event_per_iter.get(reason, [])),
            }
            for reason, total in scheduler_event_totals.items()
        }
        agg["schedulerEventIterationsCovered"] = iterations_with_events

    route_view_agg = _aggregate_route_views(iteration_results)
    if route_view_agg:
        agg["routeViewAggregate"] = route_view_agg

    # ── OOMKill / restart roll-up across iterations ──────────────────────
    # _collect_pod_status records totalOOMKills and totalRestarts per
    # iteration.  Without a per-strategy total, "colocate produced 4×
    # more OOMKills than spread" cannot be read off the summary — a
    # reader had to walk the iterations list by hand.
    oom_per_iter: List[int] = []
    restart_per_iter: List[int] = []
    iters_with_oom = 0
    iters_with_restart = 0
    for ir in iteration_results:
        ps = (ir.get("metrics") or {}).get("podStatus") or {}
        oom = ps.get("totalOOMKills")
        if isinstance(oom, (int, float)):
            oom_per_iter.append(int(oom))
            if int(oom) > 0:
                iters_with_oom += 1
        restarts = ps.get("totalRestarts")
        if isinstance(restarts, (int, float)):
            restart_per_iter.append(int(restarts))
            if int(restarts) > 0:
                iters_with_restart += 1

    if oom_per_iter:
        agg["totalOOMKills"] = sum(oom_per_iter)
        agg["meanOOMKillsPerIteration"] = round(statistics.mean(oom_per_iter), 2)
        agg["maxOOMKillsPerIteration"] = max(oom_per_iter)
        agg["iterationsWithOOMKills"] = iters_with_oom
    if restart_per_iter:
        agg["totalRestarts"] = sum(restart_per_iter)
        agg["meanRestartsPerIteration"] = round(statistics.mean(restart_per_iter), 2)
        agg["maxRestartsPerIteration"] = max(restart_per_iter)
        agg["iterationsWithRestarts"] = iters_with_restart

    node_pressure = _aggregate_node_pressure_events(iteration_results)
    if node_pressure:
        agg["nodePressureEvents"] = node_pressure

    # Per-iteration experimentDuration_s — the end-to-end wall-clock of
    # the chaos window.  Aggregating across iterations lets a defender
    # ask "did this strategy's runs take noticeably longer than that
    # one's" and surfaces between-iteration cluster slow-down.
    durations: List[float] = []
    for ir in iteration_results:
        d = ir.get("experimentDuration_s")
        if isinstance(d, (int, float)):
            durations.append(float(d))
    if durations:
        agg["meanExperimentDuration_s"] = round(statistics.mean(durations), 1)
        agg["maxExperimentDuration_s"] = round(max(durations), 1)
        agg["minExperimentDuration_s"] = round(min(durations), 1)
        if len(durations) > 1:
            agg["stddevExperimentDuration_s"] = round(statistics.stdev(durations), 1)

    # Locust failure-class roll-up: aggregate errorRate already tells
    # us *how often* requests failed; failureClasses tells us *why*.
    # Connection refused vs timeout vs HTTP 5xx have very different
    # mechanisms (network programming SLO breach, kernel conntrack
    # churn, app circuit breaker).  Aggregating per (error, name) key
    # across iterations makes "colocate hit conntrack-timeouts on every
    # iteration, spread never did" a single number per strategy.
    failure_totals: Dict[str, Dict[str, Any]] = {}
    failure_iters_observed: Dict[str, int] = {}
    for ir in iteration_results:
        lg = ir.get("loadGeneration") or {}
        stats_block = lg.get("stats") or {}
        classes = stats_block.get("failureClasses") or []
        if not isinstance(classes, list):
            continue
        per_iter_seen: set = set()
        for entry in classes:
            if not isinstance(entry, dict):
                continue
            error = entry.get("error") or ""
            name = entry.get("name") or ""
            occ = entry.get("occurrences")
            if not isinstance(occ, (int, float)):
                continue
            key_str = f"{error} | {name}" if name else error
            if not key_str:
                continue
            bucket = failure_totals.setdefault(
                key_str,
                {
                    "error": error,
                    "name": name,
                    "totalOccurrences": 0,
                    "iterationsObserved": 0,
                },
            )
            bucket["totalOccurrences"] += int(occ)
            per_iter_seen.add(key_str)
        for key_str in per_iter_seen:
            failure_iters_observed[key_str] = failure_iters_observed.get(key_str, 0) + 1

    if failure_totals:
        for key_str, count in failure_iters_observed.items():
            failure_totals[key_str]["iterationsObserved"] = count
        agg["loadFailureClasses"] = sorted(
            failure_totals.values(), key=lambda v: -v["totalOccurrences"]
        )

    return agg


def _aggregate_route_views(
    iteration_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per-route aggregation of ``routeView`` across iterations.

    Each iteration's ``routeView`` carries a Locust (outside-cluster) and
    a LatencyProber (in-pod) view per route.  This roll-up sums Locust
    request/failure counts, averages Locust + LatencyProber p95s, and
    reports how many iterations each route was observed in — so
    "during-chaos /cart p95 ran 3× higher under colocate than spread"
    becomes a directly readable per-strategy number.
    """
    by_route: Dict[str, Dict[str, Any]] = {}
    for ir in iteration_results:
        rv = ir.get("routeView") or []
        for entry in rv:
            if not isinstance(entry, dict):
                continue
            route = entry.get("route")
            if not route:
                continue
            bucket = by_route.setdefault(
                route,
                {
                    "locust_requests": [],
                    "locust_failures": [],
                    "locust_p95": [],
                    "lp_phase_p95": {},
                    "iterations": 0,
                },
            )
            bucket["iterations"] += 1

            loc = entry.get("locust")
            if isinstance(loc, dict):
                req = loc.get("requests")
                if isinstance(req, (int, float)):
                    bucket["locust_requests"].append(float(req))
                fail = loc.get("failures")
                if isinstance(fail, (int, float)):
                    bucket["locust_failures"].append(float(fail))
                p95 = loc.get("p95ResponseTime_ms")
                if isinstance(p95, (int, float)):
                    bucket["locust_p95"].append(float(p95))

            lp = entry.get("latencyProber")
            if isinstance(lp, dict):
                for phase_name, phase_data in lp.items():
                    if not isinstance(phase_data, dict):
                        continue
                    p95 = phase_data.get("p95_ms") or phase_data.get("p95ResponseTime_ms")
                    if isinstance(p95, (int, float)):
                        bucket["lp_phase_p95"].setdefault(phase_name, []).append(float(p95))

    out: List[Dict[str, Any]] = []
    for route, bucket in by_route.items():
        entry_out: Dict[str, Any] = {
            "route": route,
            "iterations": bucket["iterations"],
        }
        if bucket["locust_requests"] or bucket["locust_failures"] or bucket["locust_p95"]:
            locust_out: Dict[str, Any] = {}
            if bucket["locust_requests"]:
                locust_out["totalRequests"] = int(sum(bucket["locust_requests"]))
            if bucket["locust_failures"]:
                locust_out["totalFailures"] = int(sum(bucket["locust_failures"]))
            if bucket["locust_p95"]:
                locust_out["meanP95_ms"] = round(statistics.mean(bucket["locust_p95"]), 1)
                locust_out["iterationsObserved"] = len(bucket["locust_p95"])
            entry_out["locust"] = locust_out

        if bucket["lp_phase_p95"]:
            lp_out: Dict[str, Any] = {}
            for phase_name, values in bucket["lp_phase_p95"].items():
                lp_out[phase_name] = {
                    "meanP95_ms": round(statistics.mean(values), 1),
                    "iterationsObserved": len(values),
                }
            entry_out["latencyProber"] = lp_out
        out.append(entry_out)

    # Stable order: Locust-side routes first (load-generator perspective),
    # then LatencyProber-only routes alphabetically — matches build_route_view.
    out.sort(key=lambda r: (0 if "locust" in r else 1, r["route"]))
    return out


def summarise_placement_match_rates(
    strategies: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Per-strategy intent-vs-actual placement match rate roll-up.

    Surfaces ``placement.metadata.intendedActualDiff.matchRate`` for every
    strategy that captured one — produced by ``PlacementMutator.apply_strategy``
    after the rollout settles.  A strategy that returns matchRate=1.0
    placed every deployment as intended; lower values mean the scheduler
    overrode the nodeSelector (e.g. taint, resource fit, topology spread
    failure).  The thesis's per-strategy ranking only holds if the
    intended placement actually applied; this is the verification.

    Returns ``{strategy_name: {matchRate, matched, mismatched}}`` — the
    counts are convenient for downstream rendering.  Strategies without
    an intent-vs-actual diff (baseline, default) are omitted.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for name, sdata in (strategies or {}).items():
        placement = (sdata or {}).get("placement") or {}
        metadata = placement.get("metadata") or {}
        diff = metadata.get("intendedActualDiff")
        if not isinstance(diff, dict):
            continue
        match_rate = diff.get("matchRate")
        if match_rate is None:
            continue
        out[name] = {
            "matchRate": match_rate,
            "matched": len(diff.get("matched") or []),
            "mismatched": len(diff.get("mismatched") or []),
        }
    return out


# K8s kubelet conditions that fire ``status="True"`` when the node is
# under pressure.  ``Ready`` is intentionally excluded — it's the
# inverse of an event (False means trouble).
_NODE_PRESSURE_CONDITIONS = (
    "MemoryPressure",
    "DiskPressure",
    "PIDPressure",
    "NetworkUnavailable",
)


def _aggregate_node_pressure_events(
    iteration_results: List[Dict[str, Any]],
) -> Dict[str, Dict[str, int]]:
    """Per-strategy node-pressure event counts.

    A condition fires when its ``status`` field equals ``"True"`` on the
    kubelet's node-status report.  Counts two things per condition:

    * ``iterationsWithEvent`` — number of iterations where *at least one*
      hosting node had this condition firing.  Distinguishes "one bad
      iteration" from "every iteration was under memory pressure".
    * ``totalNodeEvents`` — total ``(iteration, node)`` pairs where the
      condition fired.  Captures fan-out across nodes within an iteration
      (a `spread` placement under pressure on all 4 workers is worse
      than a `colocate` placement under pressure on just one).

    Reads from both ``metrics.nodeInfo`` (single hosting node, the
    pre-existing field) and ``metrics.nodeInfoAll`` (every hosting node,
    added separately).  Returns ``{}`` when no iteration carried either —
    the caller omits the block in that case.
    """
    by_condition: Dict[str, Dict[str, int]] = {
        c: {"iterationsWithEvent": 0, "totalNodeEvents": 0} for c in _NODE_PRESSURE_CONDITIONS
    }
    saw_any_node_info = False
    for ir in iteration_results:
        metrics = ir.get("metrics") or {}
        nodes: List[Dict[str, Any]] = []
        node_info_all = metrics.get("nodeInfoAll")
        if isinstance(node_info_all, dict) and node_info_all:
            saw_any_node_info = True
            for entry in node_info_all.values():
                if isinstance(entry, dict):
                    nodes.append(entry)
        else:
            single = metrics.get("nodeInfo")
            if isinstance(single, dict) and single:
                saw_any_node_info = True
                nodes.append(single)

        if not nodes:
            continue

        fired_this_iter: set = set()
        for entry in nodes:
            conditions = entry.get("conditions") or {}
            if not isinstance(conditions, dict):
                continue
            for cond_name in _NODE_PRESSURE_CONDITIONS:
                cond = conditions.get(cond_name)
                if isinstance(cond, dict) and cond.get("status") == "True":
                    by_condition[cond_name]["totalNodeEvents"] += 1
                    fired_this_iter.add(cond_name)
        for cond_name in fired_this_iter:
            by_condition[cond_name]["iterationsWithEvent"] += 1

    if not saw_any_node_info:
        return {}
    return by_condition
