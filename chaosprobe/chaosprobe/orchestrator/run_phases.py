"""Extracted phases for the ``chaosprobe run`` command.

Breaking the ``run()`` Click command into composable helper functions
so the top-level orchestrator stays small and readable.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import click

from chaosprobe.orchestrator import portforward as pf

# Pure aggregation/comparison helpers were extracted to ``aggregation`` for
# side-effect-free unit testing; re-exported here to preserve the import surface
# (consumers and tests still import these names from run_phases).
from chaosprobe.orchestrator.aggregation import (  # noqa: F401
    _aggregate_node_pressure_events,
    _aggregate_route_views,
    _bucket_recovery_times,
    _build_comparison_table_impl,
    aggregate_iterations,
    summarise_placement_match_rates,
)
from chaosprobe.orchestrator.preflight import (
    LITMUS_INFRA_DEPLOYMENTS,
    wait_for_healthy_deployments,
)
from chaosprobe.output import SCHEMA_VERSION
from chaosprobe.provisioner.setup import LitmusSetup

logger = logging.getLogger(__name__)

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
                        logger.debug("deployment readiness re-check failed", exc_info=True)
                else:
                    click.echo(f"  WARNING: {dep_name} did not recover after restart", err=True)
    except Exception as e:
        click.echo(f"  WARNING: infra health check failed ({e})", err=True)


_CONTROL_PLANE_ROLE_LABELS = (
    "node-role.kubernetes.io/control-plane",
    "node-role.kubernetes.io/master",
)


def _orphaned_cordoned_workers(nodes: List[Any]) -> List[str]:
    """Names of *worker* nodes left cordoned (``spec.unschedulable``).

    Control-plane nodes are excluded — they are never chaos targets and may be
    intentionally cordoned. A worker is only ever cordoned by ChaosProbe via a
    node-drain experiment, so a lingering cordon means litmus never ran its
    uncordon revert (the drain was interrupted, retried, or timed out). Pure
    selection logic, separated from the patch action for unit testing.
    """
    out: List[str] = []
    for node in nodes:
        spec = getattr(node, "spec", None)
        if not (spec and getattr(spec, "unschedulable", False)):
            continue
        labels = getattr(getattr(node, "metadata", None), "labels", None) or {}
        if any(lbl in labels for lbl in _CONTROL_PLANE_ROLE_LABELS):
            continue
        name = getattr(getattr(node, "metadata", None), "name", None)
        if name:
            out.append(name)
    return out


def _uncordon_orphaned_nodes() -> None:
    """Uncordon any worker node a prior node-drain left cordoned.

    Defensive cluster hygiene, run before each iteration's readiness gate: an
    interrupted / retried / timed-out node-drain can leave its target node
    cordoned, after which every pod pinned there is unschedulable and the
    readiness gate fails — cascading into the rest of the run and requiring a
    manual ``kubectl uncordon``. Uncordoning here makes node faults safe to run
    repeatedly. Control-plane nodes are never touched.
    """
    from kubernetes import client as k8s_client_mod

    try:
        core_api = k8s_client_mod.CoreV1Api()
        nodes = core_api.list_node().items
    except Exception:
        logger.debug("uncordon guard: list_node failed", exc_info=True)
        return
    for name in _orphaned_cordoned_workers(nodes):
        try:
            core_api.patch_node(name, {"spec": {"unschedulable": False}})
            click.echo(f"    Uncordoned worker '{name}' left cordoned by a prior node-drain.")
        except Exception:
            logger.debug("uncordon guard: patch_node %s failed", name, exc_info=True)


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
        logger.debug("failed to parse Neo4j URI host:port; using defaults", exc_info=True)
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

    # Stamp the output schema version so analysis tools (doctor, stats,
    # compare) can detect renamed/changed fields — consistent with the
    # single-run / comparison writers in ``chaosprobe.output``.
    overall_results["schemaVersion"] = SCHEMA_VERSION

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
        logger.debug("failed to resolve ChaosCenter dashboard URL", exc_info=True)

    click.echo("")
