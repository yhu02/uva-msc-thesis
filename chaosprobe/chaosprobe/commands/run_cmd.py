"""CLI command: chaosprobe run — automated full experiment matrix."""

import fcntl
import json
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click
from kubernetes import client as k8s_client
from kubernetes.client import Configuration as K8sConfig
from kubernetes.client.rest import ApiException
from urllib3.exceptions import MaxRetryError

from chaosprobe.commands.shared import (
    neo4j_password_option,
    neo4j_uri_option,
    neo4j_user_option,
)
from chaosprobe.config.loader import hash_scenario_files, load_scenario
from chaosprobe.config.topology import parse_topology_from_scenario
from chaosprobe.config.validator import validate_scenario
from chaosprobe.k8s import ensure_k8s_config
from chaosprobe.metrics.collector import MetricsCollector
from chaosprobe.metrics.reproducibility import gather_run_metadata
from chaosprobe.orchestrator.preflight import (
    LITMUS_INFRA_DEPLOYMENTS,
    extract_experiment_types,
    extract_load_service,
    extract_target_deployment,
    is_stateful_infra,
)
from chaosprobe.orchestrator.run_phases import (
    init_graph_store,
    run_preflight_checks,
    summarise_placement_match_rates,
    write_run_results,
)
from chaosprobe.orchestrator.session import (
    PACKED_ASSIGNMENT_ROUND_ROBIN,
    PACKED_ASSIGNMENT_SOLVER,
    PACKED_ASSIGNMENTS,
    Condition,
    Session,
    build_session,
    discover_services,
    edges_from_routes,
    ordered_conditions,
    parse_levels,
    parse_workers,
    session_metadata,
)
from chaosprobe.orchestrator.strategy_runner import (
    RunContext,
    _adjacent_topology_path,
    execute_strategy,
)
from chaosprobe.placement import affinity_engine
from chaosprobe.placement import dns_cache as dns_cache_engine
from chaosprobe.placement.fraction_solver import load_static_topology
from chaosprobe.placement.mutator import PlacementMutator
from chaosprobe.placement.strategy import DEFAULT_RUN_STRATEGIES, PlacementStrategy
from chaosprobe.probes.builder import (
    RustProbeBuilder,
    extract_cmdprobe_images,
    patch_probe_images,
    prepull_probe_images,
)
from chaosprobe.provisioner.components import resolve_probe_registry
from chaosprobe.provisioner.setup import LitmusSetup, UnknownExperimentType

logger = logging.getLogger(__name__)


def _ensure_litmus_setup(
    namespace: str,
    experiment_types: list[str],
) -> bool:
    """Ensure all infrastructure is installed and healthy.

    Automatically installs missing components (Helm, LitmusChaos,
    local-path-provisioner, metrics-server, Prometheus, Neo4j,
    ChaosCenter) and repairs degraded ones (metrics-server flags,
    lost PVCs).  No separate ``init`` step required.
    """
    setup = LitmusSetup(skip_k8s_init=True)
    prereqs = setup.check_prerequisites()

    if not prereqs["kubectl"]:
        click.echo("Error: kubectl not found. Please install kubectl.", err=True)
        return False

    click.echo("Validating cluster...")
    is_valid, message = setup.validate_cluster()
    if not is_valid:
        click.echo(f"Error: {message}", err=True)
        return False
    click.echo(f"  {message}")

    setup.init_k8s_client()
    prereqs = setup.check_prerequisites()

    # ── Ensure Helm is available (needed for LitmusChaos install) ──
    if not prereqs["helm"]:
        click.echo("  Helm not found, installing...")
        try:
            setup.ensure_helm()
            click.echo("  Helm: installed")
        except Exception as e:
            click.echo(f"Error installing Helm: {e}", err=True)
            return False

    # ── Ensure local-path-provisioner (needed for PVCs) ──
    if not setup.is_local_path_provisioner_running():
        click.echo("  local-path-provisioner not found, installing...")
        if setup.ensure_local_path_provisioner():
            click.echo("  local-path-provisioner: installed")
        else:
            click.echo("  WARNING: local-path-provisioner may not be ready yet", err=True)
    else:
        click.echo("  local-path-provisioner: available")

    # ── Ensure LitmusChaos is installed ──
    if not prereqs["litmus_installed"]:
        click.echo("  LitmusChaos not found, installing...")
        try:
            setup.install_litmus(wait=True)
            click.echo("  LitmusChaos: installed")
        except Exception as e:
            click.echo(f"Error installing LitmusChaos: {e}", err=True)
            return False
    else:
        click.echo("  LitmusChaos: available")

    click.echo("Setting up RBAC for namespace...")
    try:
        setup.setup_rbac(namespace)
        click.echo(f"  RBAC configured for namespace: {namespace}")
    except Exception as e:
        click.echo(f"Error setting up RBAC: {e}", err=True)
        return False

    for exp_type in set(experiment_types):
        click.echo(f"  Installing experiment: {exp_type}")
        try:
            installed = setup.install_experiment(exp_type, namespace)
        except UnknownExperimentType as exc:
            click.echo(f"  ERROR: {exc}", err=True)
            return False
        if not installed:
            click.echo(
                f"  WARNING: kubectl apply failed for experiment '{exp_type}' — "
                f"cluster may have transient network issues; continuing",
                err=True,
            )

    # ── Ensure infrastructure components (install or repair, parallel) ──
    _ensure_infrastructure_parallel(setup)

    return True


def _deployment_has_pvc(setup: LitmusSetup, name: str, ns: str) -> bool:
    """Best-effort check whether a deployment mounts a PVC.

    Returns ``True`` on error so callers don't trigger a destructive
    re-install just because the readiness probe failed transiently.
    """
    try:
        dep = setup.apps_api.read_namespaced_deployment(name, ns)
        volumes = dep.spec.template.spec.volumes or []
        return any(v.persistent_volume_claim is not None for v in volumes)
    except Exception:
        return True


def _ensure_metrics_server(setup: LitmusSetup) -> Tuple[str, str]:
    if setup.is_metrics_server_installed():
        try:
            dep = setup.apps_api.read_namespaced_deployment("metrics-server", "kube-system")
            containers = dep.spec.template.spec.containers or []
            args = containers[0].args or [] if containers else []
            if "--kubelet-insecure-tls" not in args:
                setup.install_metrics_server(wait=True)
                return "metrics-server", "repaired (added --kubelet-insecure-tls)"
        except Exception:
            logger.debug("failed to inspect metrics-server args", exc_info=True)
        return "metrics-server", "available"
    if setup.install_metrics_server(wait=True):
        return "metrics-server", "installed"
    return "metrics-server", "installed (not yet ready)"


def _ensure_prometheus(setup: LitmusSetup) -> Tuple[str, str]:
    if setup.is_prometheus_installed():
        if not _deployment_has_pvc(setup, "prometheus-server", "prometheus"):
            setup.install_prometheus(wait=True)
            return "Prometheus", "repaired (restored persistent storage)"
        return "Prometheus", "available"
    if setup.install_prometheus(wait=True):
        return "Prometheus", "installed"
    return "Prometheus", "installed (not yet ready)"


def _ensure_neo4j(setup: LitmusSetup) -> Tuple[str, str]:
    if setup.is_neo4j_installed():
        if not _deployment_has_pvc(setup, "neo4j", "neo4j"):
            setup.install_neo4j(wait=True)
            return "Neo4j", "repaired (restored persistent storage)"
        return "Neo4j", "available"
    if setup.install_neo4j(wait=True):
        return "Neo4j", "installed"
    return "Neo4j", "installed (not yet ready)"


def _ensure_chaoscenter(setup: LitmusSetup) -> Tuple[str, str]:
    if setup.is_chaoscenter_installed():
        return "ChaosCenter", "available"
    if setup.install_chaoscenter(wait=True):
        return "ChaosCenter", "installed"
    return "ChaosCenter", "installed (not yet ready)"


def _ensure_infrastructure_parallel(setup: LitmusSetup) -> None:
    """Install or repair the four infra components concurrently.

    Each component is installed/repaired independently in its own
    thread; results are printed in completion order.
    """
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_ensure_metrics_server, setup): "metrics-server",
            executor.submit(_ensure_prometheus, setup): "Prometheus",
            executor.submit(_ensure_neo4j, setup): "Neo4j",
            executor.submit(_ensure_chaoscenter, setup): "ChaosCenter",
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                name, status = future.result()
                click.echo(f"  {name}: {status}")
            except Exception as e:
                click.echo(f"  WARNING: {label} setup failed: {e}", err=True)


# ------------------------------------------------------------------
# Helpers extracted from run() to keep the main command manageable
# ------------------------------------------------------------------


def _assert_no_unbuilt_cmdprobes(experiments: List[Dict[str, Any]]) -> None:
    """Abort if any cmdProbe still has the placeholder ``image: auto``.

    Earlier this silently dropped unbuilt cmdProbes; we now raise
    instead.  Missing probes are invisible in the final report (the
    rest of the experiment looks normal) but skew every probe-count-
    based metric — see results/20260519-130102, where 2 of 5 cmdProbes
    were stripped without any failure indication in the summary.

    A probe with ``image: auto`` at this point means a cmdProbe is
    declared in the experiment YAML but no matching Rust source exists
    in the scenario's ``probes/`` directory — that's a scenario-level
    misconfiguration, not a transient build issue.  Either add the
    source or remove the probe from the YAML.
    """
    unbuilt = []
    for exp_entry in experiments:
        engine_spec = exp_entry.get("spec", {}).get("spec", {})
        for exp in engine_spec.get("experiments", []):
            for probe in exp.get("spec", {}).get("probe", []):
                if probe.get("type") != "cmdProbe":
                    continue
                source = probe.get("cmdProbe/inputs", {}).get("source", {})
                if source.get("image") in ("auto", "", None):
                    unbuilt.append(probe.get("name", "unknown"))
    if unbuilt:
        raise click.ClickException(
            f"{len(unbuilt)} cmdProbe(s) have no built image and would be "
            f"dropped from the experiment: {', '.join(unbuilt)}.  Add the "
            f"matching Rust probe source to the scenario's probes/ directory, "
            f"or remove the probe from the experiment YAML."
        )


def _collect_built_images(experiments: List[Dict[str, Any]]) -> Dict[str, str]:
    """Map cmdProbe name → resolved image from already-patched experiments.

    The primary scenario's cmdProbe images are patched to the in-cluster
    registry by :func:`patch_probe_images`. Secondary fault scenarios in a
    multi-fault run carry the same probes but with the placeholder
    ``image: auto`` and are loaded with ``deploy=False`` (so they never build
    or patch their own images). Reusing the primary's resolved tags lets the
    fault matrix patch them too; probes still on the placeholder are skipped.
    """
    images: Dict[str, str] = {}
    for exp_entry in experiments:
        engine_spec = exp_entry.get("spec", {}).get("spec", {})
        for exp in engine_spec.get("experiments", []):
            for probe in exp.get("spec", {}).get("probe", []):
                if probe.get("type") != "cmdProbe":
                    continue
                source = probe.get("cmdProbe/inputs", {}).get("source", {})
                image = source.get("image")
                name = probe.get("name")
                if name and image and image not in ("auto", ""):
                    images[name] = image
    return images


def _load_and_prepare_scenario(
    experiment: str,
    namespace: Optional[str],
    deploy: bool = True,
) -> Tuple[Dict[str, Any], str, Path, Optional[List[Tuple[str, str, str, str, str]]]]:
    """Load, validate, deploy manifests, and discover topology.

    Args:
        experiment: Path to the experiment YAML file.
        namespace: Override namespace (None = use scenario's own).
        deploy: If False, skip kubectl apply and Rust-probe build.  Used
            for secondary experiments in a multi-fault matrix where
            manifests have already been applied by the primary one.

    Returns ``(shared_scenario, namespace, experiment_file, service_routes)``.
    """
    experiment_file = Path(experiment)
    if not experiment_file.exists():
        pkg_path = Path(__file__).resolve().parent.parent.parent / experiment
        if pkg_path.exists():
            experiment_file = pkg_path
    try:
        shared_scenario = load_scenario(str(experiment_file))
        validate_scenario(shared_scenario)
        if namespace is not None:
            shared_scenario["namespace"] = namespace
        namespace = shared_scenario["namespace"]
    except Exception as e:
        click.echo(f"Error loading experiment: {e}", err=True)
        sys.exit(1)

    if not deploy:
        # Secondary scenario in a multi-fault matrix.  Manifests and
        # probe images were already taken care of by the primary
        # scenario; we just need the parsed scenario dict so the runner
        # can swap engine specs.
        service_routes = parse_topology_from_scenario(shared_scenario) or None
        return shared_scenario, namespace, experiment_file, service_routes

    # Auto-deploy application manifests from scenario's deploy/ directory.
    # Shells out to kubectl rather than using the python client because
    # ``kubectl apply -f <dir>`` walks the directory, handles multi-doc
    # YAML, and computes 3-way merges — all of which would have to be
    # re-implemented against the API client.  Worth the dual transport.
    deploy_dir = Path(shared_scenario["path"]) / "deploy"
    if deploy_dir.is_dir():
        yamls = sorted(deploy_dir.glob("*.yaml")) + sorted(deploy_dir.glob("*.yml"))
        if yamls:
            click.echo(f"  Deploying {len(yamls)} manifest(s) from {deploy_dir.name}/...")
            subprocess.run(
                ["kubectl", "create", "namespace", namespace],
                capture_output=True,
                text=True,
                timeout=120,
            )
            try:
                result = subprocess.run(
                    ["kubectl", "apply", "-f", str(deploy_dir), "-n", namespace],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            except subprocess.TimeoutExpired:
                click.echo(
                    "  Warning: kubectl apply timed out after 300s — "
                    "the K8s API server may be overloaded or unreachable.",
                    err=True,
                )
                result = None
            if result is not None and result.returncode != 0:
                click.echo(f"  Warning: kubectl apply failed: {result.stderr.strip()}", err=True)
            elif result is not None:
                applied = sum(1 for line in result.stdout.strip().split("\n") if line.strip())
                click.echo(f"  Applied {applied} resource(s) to {namespace}")

    # Discover service topology
    service_routes = parse_topology_from_scenario(shared_scenario) or None
    if service_routes:
        click.echo(
            f"  Topology:   {len(service_routes)} service dependencies" " discovered from manifests"
        )
    else:
        click.echo("  Topology:   no deploy/ directory found; service dependency graph empty")

    # Auto-build Rust cmdProbes if probes/ directory exists. Always pushes to
    # the in-cluster registry (installed by `chaosprobe init`) so cluster nodes
    # can `docker pull` the images; resolve_probe_registry raises if it's absent.
    if shared_scenario.get("probes"):
        click.echo(f"\n  Found {len(shared_scenario['probes'])} Rust probe(s), building...")
        registry = resolve_probe_registry(k8s_client.CoreV1Api())
        builder = RustProbeBuilder(registry=registry, push=True)

        # Build failures must abort the run.  Silently swallowing them
        # (the previous behaviour) produced experiments missing 1-2
        # cmdProbes that downstream analysis would never have caught
        # without manual probe-count audit — see
        # results/20260519-130102, where 2 of 5 cmdProbes never reached
        # LitmusChaos because a transient registry push failed.
        built_images = builder.build_all(shared_scenario["path"])
        expected = {p["name"] for p in shared_scenario["probes"]}
        missing = expected - set(built_images.keys())
        if missing:
            raise click.ClickException(
                f"Rust probe build was missing {len(missing)} probe(s): "
                f"{', '.join(sorted(missing))}.  Re-run after resolving "
                f"the build failure."
            )
        n = patch_probe_images(shared_scenario["experiments"], built_images)
        click.echo(f"  Built and patched {n} cmdProbe image(s)")

        # Defensive belt: if any cmdProbe in the spec still has the
        # placeholder image (shouldn't happen given the strictness above,
        # but covers a probe defined in YAML without a matching binary),
        # abort rather than silently dropping it from the experiment.
        _assert_no_unbuilt_cmdprobes(shared_scenario["experiments"])

    return shared_scenario, namespace, experiment_file, service_routes


def _build_fault_scenarios(
    experiment: Tuple[str, ...],
    primary_experiment: str,
    shared_scenario: Dict[str, Any],
    namespace: str,
) -> List[Tuple[str, Dict[str, Any], List[str]]]:
    """Build the ``(label, scenario, fault_types)`` triples for the fault matrix.

    Each ``--experiment`` path becomes one fault: the label is the filename stem,
    the primary reuses the already-prepared ``shared_scenario`` (deployed +
    topology + probes), and any additional scenarios are loaded with
    ``deploy=False`` against the same namespace. Pre-loading them here fails fast
    on parse errors before cluster setup.

    Because secondary scenarios skip the build/patch step (``deploy=False``),
    their cmdProbes would otherwise ship the placeholder ``image: auto`` and
    fail to pull on the cluster. We reuse the primary's already-resolved probe
    images to patch them, so every fault in the matrix runs the same probes.
    """
    built_images = _collect_built_images(shared_scenario.get("experiments", []))
    fault_scenarios: List[Tuple[str, Dict[str, Any], List[str]]] = []
    for exp_path in experiment:
        label = Path(exp_path).stem
        if exp_path == primary_experiment:
            scenario_dict = shared_scenario
        else:
            scenario_dict, _ns, _file, _routes = _load_and_prepare_scenario(
                exp_path, namespace, deploy=False
            )
            if built_images:
                patch_probe_images(scenario_dict.get("experiments", []), built_images)
                _assert_no_unbuilt_cmdprobes(scenario_dict.get("experiments", []))
        fault_scenarios.append((label, scenario_dict, extract_experiment_types(scenario_dict)))
    return fault_scenarios


def _clear_stale_placement(mutator: PlacementMutator, namespace: str) -> None:
    """Clear leftover nodeSelector constraints and rollout-restart app deployments."""
    click.echo("Clearing stale placement constraints...")
    for attempt in range(3):
        try:
            mutator.clear_placement(wait=True, timeout=120)
            break
        except Exception as e:
            if attempt < 2:
                click.echo(f"  Retry clearing placement ({e})...", err=True)
                time.sleep(5)
            else:
                click.echo(f"  WARNING: could not clear placement ({e})", err=True)

    # Ensure ALL app deployments use RollingUpdate before the restart
    # patch.  Previous runs leave deployments with Recreate strategy,
    # which kills all pods during a rollout restart.
    click.echo("Ensuring safe rollout strategy for all deployments...")
    apps_api = k8s_client.AppsV1Api()
    try:
        all_deps = apps_api.list_namespaced_deployment(namespace)
        for dep in all_deps.items:
            name = dep.metadata.name
            if name in LITMUS_INFRA_DEPLOYMENTS:
                continue
            strat = dep.spec.strategy
            if strat and strat.type == "Recreate":
                apps_api.patch_namespaced_deployment(
                    name=name,
                    namespace=namespace,
                    body={
                        "spec": {
                            "strategy": {
                                "type": "RollingUpdate",
                                "rollingUpdate": {
                                    "maxSurge": 1,
                                    "maxUnavailable": 0,
                                },
                            },
                        },
                    },
                )
    except Exception as e:
        click.echo(f"  WARNING: strategy patch failed ({e})", err=True)

    click.echo("Restarting app deployments for a clean baseline...")
    try:
        all_deps = apps_api.list_namespaced_deployment(namespace)
        restart_names = [
            d.metadata.name
            for d in all_deps.items
            if d.metadata.name not in LITMUS_INFRA_DEPLOYMENTS
            and not is_stateful_infra(d.metadata.name)
        ]
        now = datetime.now(timezone.utc).isoformat()
        for dep_name in restart_names:
            apps_api.patch_namespaced_deployment(
                name=dep_name,
                namespace=namespace,
                body={
                    "spec": {
                        "template": {
                            "metadata": {
                                "annotations": {
                                    "chaosprobe.io/restartedAt": now,
                                }
                            }
                        }
                    }
                },
            )
        click.echo(f"  Triggered rollout restart for {len(restart_names)} deployment(s)")

        # Wait for ALL restarted deployments to finish rolling out before
        # proceeding.  `wait_for_healthy_deployments` only checks
        # ready_replicas >= desired, which is satisfied by the *old* pods
        # while the rollout is still in progress.  Instead, we poll until
        # updated_replicas == desired for every deployment so that fresh
        # pods (with cold caches, JVM warm-up, etc.) are fully serving
        # traffic before the first experiment starts.
        restart_deadline = time.time() + 180
        click.echo(f"  Waiting for {len(restart_names)} rollout(s) to complete (timeout: 180s)...")
        pending = list(restart_names)
        while pending and time.time() < restart_deadline:
            still_pending = []
            deps = apps_api.list_namespaced_deployment(namespace)
            dep_map = {d.metadata.name: d for d in deps.items}
            for name in pending:
                dep = dep_map.get(name)
                if dep is None:
                    continue  # deployment gone, skip
                desired = dep.spec.replicas if dep.spec.replicas is not None else 1
                if desired == 0:
                    continue
                gen = dep.metadata.generation or 0
                obs_gen = (dep.status.observed_generation or 0) if dep.status else 0
                updated = (dep.status.updated_replicas or 0) if dep.status else 0
                avail = (dep.status.available_replicas or 0) if dep.status else 0
                if obs_gen < gen or updated < desired or avail < desired:
                    still_pending.append(name)
            pending = still_pending
            if pending:
                time.sleep(5)
        if pending:
            click.echo(
                f"  WARNING: {len(pending)} rollout(s) did not complete in time: {pending}",
                err=True,
            )
        else:
            click.echo("  All rollouts complete.")
    except Exception as e:
        click.echo(f"  WARNING: rollout restart failed ({e})", err=True)


def _save_partial_results(overall_results: Dict[str, Any], results_dir: Path) -> None:
    """Persist partial results after each strategy completes.

    Writes a ``partial_summary.json`` so that if a later strategy (or
    the final summary step) crashes, all data collected so far is
    recoverable from disk.

    Uses compact JSON (no indentation) to keep the file size manageable
    (~36MB vs ~102MB with indent=2 for a full 8-strategy run).
    """
    partial_path = results_dir / "partial_summary.json"
    try:
        partial_path.write_text(json.dumps(overall_results, separators=(",", ":"), default=str))
    except OSError as exc:
        # Best-effort: a save failure here must not crash the run, but the
        # user has to know that crash-recovery data is unreliable.
        click.echo(
            f"  Warning: could not write partial results to {partial_path}: {exc}",
            err=True,
        )


def _cleanup_conntrack_samplers(core_api: Any) -> None:
    """Remove the per-worker conntrack sampler pods at the end of the run.

    Sampler pods persist across iterations (``ensure_samplers`` is
    idempotent, so each iteration adopts them instead of paying the
    image-pull + ``apk add`` cost again) and are torn down once here.
    ``cleanup_sampler_pods`` is best-effort by contract, so this can never
    fail the run.
    """
    from chaosprobe.metrics.conntrack import cleanup_sampler_pods

    removed = cleanup_sampler_pods(core_api)
    if removed:
        click.echo(f"  Removed {removed} conntrack sampler pod(s).")


def _print_run_banner(
    namespace: str,
    experiment_file: Path,
    strategy_list: List[str],
    iterations: int,
    results_dir: Path,
    timeout: int,
    settle_time: int,
    *,
    measure_latency: bool,
    measure_redis: bool,
    measure_disk: bool,
    measure_resources: bool,
    measure_prometheus: bool,
    measure_conntrack: bool,
    prometheus_url: Tuple[str, ...],
    collect_logs: bool,
    baseline_duration: int,
) -> None:
    """Print the experiment run banner."""
    click.echo("=" * 60)
    click.echo("ChaosProbe — Automated Placement Experiment Runner")
    click.echo("=" * 60)
    click.echo(f"  Namespace:  {namespace}")
    click.echo(f"  Experiment: {experiment_file}")
    click.echo(f"  Strategies: {', '.join(strategy_list)}")
    click.echo(f"  Iterations: {iterations}")
    click.echo(f"  Output:     {results_dir}")
    click.echo(f"  Timeout:    {timeout}s per experiment")
    click.echo(f"  Settle:     dynamic gates + {settle_time}s pre/post sample window")
    if measure_latency:
        click.echo("  Latency:    Measuring inter-service latency during experiments")
    if measure_redis:
        click.echo("  Redis:      Measuring Redis throughput during experiments")
    if measure_disk:
        click.echo("  Disk:       Measuring disk I/O throughput during experiments")
    if measure_resources:
        click.echo("  Resources:  Measuring node/pod resource utilization during experiments")
    if measure_prometheus:
        prom_display = ", ".join(prometheus_url) if prometheus_url else "(auto-discover)"
        click.echo(f"  Prometheus: Querying cluster Prometheus at {prom_display}")
    if measure_conntrack:
        click.echo("  Conntrack:  Sampling per-node protocol-labeled conntrack counts")
    if collect_logs:
        click.echo("  Logs:       Collecting container logs from target deployment")
    if baseline_duration > 0:
        click.echo(f"  Baseline:   {baseline_duration}s steady-state collection before chaos")
    click.echo("")


def _strategy_execution_order(name: str) -> int:
    """Sort key for the strategy run order.

    Low-contention strategies run first so lingering node pressure from heavy
    strategies doesn't skew later results. ``baseline``/``default`` aren't in
    the placement enum, so they sort first (-1 / 0). A ``:seed`` suffix on
    multi-seed variants (e.g. ``random:42``) is stripped before the enum lookup.
    """
    base = name.split(":", 1)[0]
    try:
        return PlacementStrategy(base).execution_order
    except ValueError:
        return -1 if base == "baseline" else 0


def _global_strategy_index(fault_pos: int, strat_pos: int, n_strategies: int) -> int:
    """1-based position of a strategy within the full multi-fault matrix.

    ``fault_pos`` is the 0-based index of the fault scenario in the matrix and
    ``strat_pos`` the 1-based index of the strategy within that fault. Counting
    continuously across faults means a 2-fault x 8-strategy run reports
    ``[1/16]`` .. ``[16/16]`` instead of restarting at ``[1/16]`` for the second
    fault — which looks like the run restarted and under-reports progress.
    """
    return fault_pos * n_strategies + strat_pos


def _expand_random_seeds(strategy_list: List[str], seeds: Optional[str]) -> List[str]:
    """Expand the ``random`` strategy into one entry per ``--seeds`` value.

    Each becomes ``random:<seed>`` so downstream tooling (stats, doctor,
    compare) sees per-seed variants as distinct strategies and can separate
    per-seed variance from cross-strategy variance. Returns the list unchanged
    when ``--seeds`` is unset or ``random`` isn't selected. Raises
    ``click.ClickException`` on a non-integer or empty seed list.
    """
    if not seeds or "random" not in strategy_list:
        return strategy_list

    seed_list: List[int] = []
    for tok in seeds.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            seed_list.append(int(tok))
        except ValueError:
            raise click.ClickException(f"--seeds entry '{tok}' is not an integer.")
    if not seed_list:
        raise click.ClickException("--seeds needs at least one integer.")

    expanded: List[str] = []
    for s in strategy_list:
        if s == "random":
            expanded.extend(f"random:{seed_val}" for seed_val in seed_list)
        else:
            expanded.append(s)
    return expanded


def _collect_experiment_types(
    fault_scenarios: List[Tuple[str, Dict[str, Any], List[str]]],
    strategy_list: List[str],
) -> List[str]:
    """Order-preserving union of the LitmusChaos experiment types needed across
    the whole fault matrix, so LitmusChaos can be set up once for all of them.

    Adds ``pod-cpu-hog`` when ``baseline`` is selected — baseline swaps the
    destructive fault for that trivial one.
    """
    experiment_types: List[str] = []
    for _label, _scn, types in fault_scenarios:
        for t in types:
            if t not in experiment_types:
                experiment_types.append(t)
    if "baseline" in strategy_list and "pod-cpu-hog" not in experiment_types:
        experiment_types.append("pod-cpu-hog")
    return experiment_types


def _unique_probe_images(
    fault_scenarios: List[Tuple[str, Dict[str, Any], List[str]]],
) -> List[str]:
    """Order-preserving union of cmdProbe images across the fault matrix.

    Pre-pulling this union before iterations start stops a later fault from
    triggering a fresh image pull mid-run.
    """
    images: List[str] = []
    seen: set = set()
    for _label, scn, _types in fault_scenarios:
        for img in extract_cmdprobe_images(scn.get("experiments", [])):
            if img not in seen:
                seen.add(img)
                images.append(img)
    return images


def _snapshot_node_usage_for_bestfit(
    mutator: PlacementMutator, namespace: str
) -> Dict[str, Tuple[int, int]]:
    """Snapshot per-node pod-request usage ONCE per run for reproducible best-fit.

    Reused for every best-fit invocation so its computed topology is stable
    across strategies (otherwise lingering chaos-infra / monitoring pods from
    earlier strategies silently shift best-fit's bin capacity, making results
    non-comparable within a run).

    CRITICAL: excludes the app deployments' own pods — best-fit is about to
    repack them, so their current footprint isn't "already used" capacity it
    must work around. Leaving them in would make nodes look fuller than reality
    and push best-fit to over-spread. The snapshot represents only the immovable
    baseline (kube-system, monitoring, chaos infra, loadgen, etc.).

    Stores the result on ``mutator.usage_snapshot`` and returns it.
    """
    click.echo("Snapshotting node pod-request usage for reproducible best-fit...")
    app_dep_names = [d.name for d in mutator.get_deployments() if d.replicas > 0]
    app_pods_by_node = mutator.observe_pod_placements(app_dep_names)
    app_pod_keys = {(namespace, pod_name) for pod_name in app_pods_by_node}
    node_usage_snapshot = mutator.get_node_pod_usage(exclude_pods=app_pod_keys)
    mutator.usage_snapshot = node_usage_snapshot
    click.echo(
        f"  Excluded {len(app_pod_keys)} app-deployment pod(s) from "
        f"snapshot (they are about to be repacked)"
    )
    for node_name in sorted(node_usage_snapshot):
        cpu_m, mem_b = node_usage_snapshot[node_name]
        click.echo(f"  {node_name}: {cpu_m}m CPU, {mem_b // (1024 * 1024)}MiB memory in use")
    return node_usage_snapshot


def _error_strategy_result(strategy_name: str, fault_label: str, error: str) -> Dict[str, Any]:
    """The result record for a strategy whose ``execute_strategy`` raised."""
    return {
        "strategy": strategy_name,
        "fault": fault_label,
        "status": "error",
        "placement": None,
        "experiment": None,
        "metrics": None,
        "error": error,
    }


def _record_strategy_result(
    overall_results: Dict[str, Any],
    fault_label: str,
    strategy_name: str,
    strategy_result: Dict[str, Any],
    strategy_passed: bool,
    *,
    multi_fault: bool,
) -> bool:
    """Store a strategy result in both the per-fault and flat views.

    The flat key is the bare strategy name for a single-fault run, or
    ``f"{fault}__{strategy}"`` across a multi-fault matrix. Both views point at
    the same dict. Returns whether the result counts as a *pass* — only when it
    didn't error AND its verdict passed; an errored or failed verdict is a
    failure.
    """
    strategy_result["fault"] = fault_label
    overall_results["faults"][fault_label]["strategies"][strategy_name] = strategy_result
    flat_key = f"{fault_label}__{strategy_name}" if multi_fault else strategy_name
    overall_results["strategies"][flat_key] = strategy_result
    return strategy_result.get("status") != "error" and strategy_passed


def _prepull_probe_images_onto_workers(
    mutator: PlacementMutator,
    namespace: str,
    fault_scenarios: List[Tuple[str, Dict[str, Any], List[str]]],
) -> None:
    """Pre-pull cmdProbe images onto every schedulable worker node before
    iterations start.

    Combined with ``imagePullPolicy: IfNotPresent`` on the probe specs, this
    eliminates the per-tick registry round-trips that were the dominant source
    of "Unknown" probe verdicts under chaos (a cmdProbe pod couldn't pull in
    time while the chaos pod burst CPU/network on the same node, dropping the
    score ~8 points per missed probe even though the SUT was healthy). Pulls the
    union of images across all fault scenarios so a later fault doesn't trigger
    a fresh pull mid-run. No-op when there are no probe images or no workers.
    """
    all_probe_images = _unique_probe_images(fault_scenarios)
    worker_node_names = [
        n.name for n in mutator.get_nodes() if n.is_schedulable and not n.is_control_plane
    ]
    if all_probe_images and worker_node_names:
        click.echo(
            f"Pre-pulling {len(all_probe_images)} probe image(s) onto "
            f"{len(worker_node_names)} worker node(s)..."
        )
        pulled = prepull_probe_images(namespace, all_probe_images, worker_node_names)
        click.echo(f"  Pre-pulled {pulled} (node x image) combinations")


def _connect_graph_store(
    neo4j_uri: Optional[str],
    neo4j_user: str,
    neo4j_password: str,
    namespace: str,
    service_routes: Any,
) -> Any:
    """Connect to the Neo4j graph store (the primary data store) and return it.

    Returns ``None`` when ``neo4j_uri`` is unset. Neo4j is required, so a missing
    driver or a failed connection raises ``click.ClickException`` (exit 1) to
    abort the run rather than silently degrade.
    """
    if not neo4j_uri:
        return None
    click.echo("Connecting to Neo4j graph store...")
    try:
        graph_store = init_graph_store(
            neo4j_uri,
            neo4j_user,
            neo4j_password,
            namespace,
            service_routes=service_routes,
        )
        click.echo("  Neo4j: connected and schema ready")
        return graph_store
    except ImportError:
        raise click.ClickException(
            "Neo4j driver not installed (install with: uv pip install chaosprobe[graph])"
        ) from None
    except Exception as e:
        raise click.ClickException(
            f"Neo4j connection failed ({e}). Neo4j is required as the primary data "
            "store — check the connection and retry."
        ) from e


def _collect_scenario_hashes(
    fault_scenarios: List[Tuple[str, Dict[str, Any], List[str]]],
) -> List[Dict[str, str]]:
    """SHA-256 every YAML backing the run, deduped across the fault matrix.

    Multi-fault runs share the same deploy manifests across their per-`-e`
    scenarios, so hashes are merged into one ``{file, sha256}`` list keyed by
    file path. Persisted so a reviewer can confirm a quoted result came from
    the exact scenario YAMLs on disk, not a since-edited copy.
    """
    merged: Dict[str, str] = {}
    for _label, scenario, _types in fault_scenarios:
        for entry in hash_scenario_files(scenario):
            merged[entry["file"]] = entry["sha256"]
    return [{"file": key, "sha256": merged[key]} for key in sorted(merged)]


def _resolve_batch_id(batch_id: Optional[str]) -> str:
    """Batch label for grouping runs, defaulting to the current UTC date.

    A run launched without ``--batch-id`` is still day-stamped so mixed-run
    analysis can separate run-to-run cluster drift from strategy effects
    without the operator having to remember the flag.
    """
    if batch_id and batch_id.strip():
        return batch_id.strip()
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _init_overall_results(
    fault_scenarios: List[Tuple[str, Dict[str, Any], List[str]]],
    namespace: str,
    iterations: int,
    core_api: Any,
    batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the run's top-level results dict.

    ``faults`` is the multi-fault matrix keyed ``faults[label][strategy]``; the
    outer key is the scenario filename stem so downstream consumers have a
    stable label even for the single-fault default. ``strategies`` is the flat
    back-compat view used by the visualizer / HTML report / per-strategy file
    writer — bare strategy names for a single fault, ``f"{fault}__{strategy}"``
    across a matrix. Both views point at the same per-strategy dict, so a write
    through either is observed by both.
    """
    return {
        "runId": f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "batchId": _resolve_batch_id(batch_id),
        "runMetadata": gather_run_metadata(core_api=core_api),
        "scenarioHashes": _collect_scenario_hashes(fault_scenarios),
        "namespace": namespace,
        "iterations": iterations,
        "faults": {label: {"strategies": {}} for label, _, _ in fault_scenarios},
        "faultExperiments": [label for label, _, _ in fault_scenarios],
        "strategies": {},
    }


# ------------------------------------------------------------------
# complete-block session plumbing (the placement-session flags)
# ------------------------------------------------------------------

#: Defaults applied when a --fraction-levels session is active but the seed flags
#: are omitted.  Order: matches --seed's 42 convention; solver: matches the
#: M1b gate's base seed.
_DEFAULT_ORDER_SEED = 42
_DEFAULT_SOLVER_SEED = 0
_DEFAULT_REPLICAS = 1
_DEFAULT_MODE = affinity_engine.MODE_PACKED
#: Default pinned-cell assignment: the fraction solver (the H1 dose-response
#: sweep is the f knob). H3 passes --packed-assignment round-robin.
_DEFAULT_PACKED_ASSIGNMENT = PACKED_ASSIGNMENT_SOLVER


@dataclass(frozen=True)
class RunArgs:
    """Validated the placement-session CLI surface, resolved before the cluster is touched."""

    levels: Tuple[float, ...]
    conditions: List[Condition]
    order_seed: int
    solver_seed: int
    replicas: int
    mode: str
    workers: Tuple[str, ...]
    packed_assignment: str
    dns_cache: Optional[str]


def _resolve_placement_args(
    fraction_levels: Optional[str],
    order_seed: Optional[int],
    solver_seed: Optional[int],
    replica_degree: Optional[int],
    placement_mode: Optional[str],
    worker_nodes: Optional[str],
    packed_assignment: Optional[str],
    dns_cache: Optional[str],
    *,
    strategies_overridden: bool,
    seeds: Optional[str],
    scale_replicas: int,
    experiments: Tuple[str, ...] = (),
) -> Optional[RunArgs]:
    """Validate the placement-session flag combination and build the condition block.

    Returns ``None`` when no ``--fraction-levels`` was given (the named-strategy
    path, untouched).  The placement-session surface is mutually exclusive with
    ``-s/--strategies``, ``--seeds``, and ``--replicas`` — the session owns
    both the condition axis and the replica count — and a session runs
    exactly one fault (the session = one fault, one
    block; the per-level records are keyed by condition, so a multi-fault
    matrix would silently overwrite the first fault's data).
    """
    if fraction_levels is None:
        leftover = [
            flag
            for flag, value in (
                ("--order-seed", order_seed),
                ("--solver-seed", solver_seed),
                ("--replica-degree", replica_degree),
                ("--placement-mode", placement_mode),
                ("--worker-nodes", worker_nodes),
                ("--packed-assignment", packed_assignment),
                ("--dns-cache", dns_cache),
            )
            if value is not None
        ]
        if leftover:
            raise click.ClickException(f"{', '.join(leftover)} require(s) --fraction-levels")
        return None

    if strategies_overridden:
        raise click.ClickException(
            "--fraction-levels is mutually exclusive with -s/--strategies: a placement-session "
            "session's conditions replace the named-strategy axis"
        )
    if seeds:
        raise click.ClickException(
            "--fraction-levels is mutually exclusive with --seeds (use --solver-seed "
            "and --order-seed)"
        )
    if scale_replicas:
        raise click.ClickException(
            "--fraction-levels is mutually exclusive with --replicas: the session's "
            "--replica-degree owns the replica count"
        )
    if not worker_nodes:
        raise click.ClickException(
            "--fraction-levels requires --worker-nodes (ordered worker node names; "
            "solver node index i maps to the i-th name)"
        )
    if len(experiments) != 1:
        raise click.ClickException(
            f"--fraction-levels runs exactly one fault per session (one complete "
            f"block per fault, per the session design) but "
            f"{len(experiments)} experiment files are selected — pass exactly "
            f"one -e/--experiment (the default selects "
            f"{len(experiments)})"
        )

    try:
        levels = parse_levels(fraction_levels)
        workers = parse_workers(worker_nodes)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    order_seed = order_seed if order_seed is not None else _DEFAULT_ORDER_SEED
    solver_seed = solver_seed if solver_seed is not None else _DEFAULT_SOLVER_SEED
    replicas = replica_degree if replica_degree is not None else _DEFAULT_REPLICAS
    mode = placement_mode if placement_mode is not None else _DEFAULT_MODE
    packed_assignment = (
        packed_assignment if packed_assignment is not None else _DEFAULT_PACKED_ASSIGNMENT
    )
    if packed_assignment not in PACKED_ASSIGNMENTS:
        raise click.ClickException(
            f"--packed-assignment must be one of {PACKED_ASSIGNMENTS}, "
            f"got '{packed_assignment}'"
        )
    if dns_cache is not None and dns_cache not in dns_cache_engine.CACHE_MODES:
        raise click.ClickException(
            f"--dns-cache must be one of {dns_cache_engine.CACHE_MODES}, got '{dns_cache}'"
        )
    if replicas not in affinity_engine.SUPPORTED_REPLICAS:
        raise click.ClickException(
            f"--replica-degree must be one of {sorted(affinity_engine.SUPPORTED_REPLICAS)} "
            f"(r=2 is deliberately unsupported per DESIGN §2.3), got {replicas}"
        )
    if mode == affinity_engine.MODE_ANTI_AFFINE and replicas > 1 and len(workers) < replicas:
        raise click.ClickException(
            f"--placement-mode anti-affine with --replica-degree {replicas} needs at least "
            f"{replicas} workers, got {len(workers)}"
        )

    return RunArgs(
        levels=levels,
        conditions=ordered_conditions(levels, order_seed),
        order_seed=order_seed,
        solver_seed=solver_seed,
        replicas=replicas,
        mode=mode,
        workers=workers,
        packed_assignment=packed_assignment,
        dns_cache=dns_cache,
    )


def _init_placement_session(
    args: RunArgs,
    namespace: str,
    mutator: PlacementMutator,
    service_routes: Optional[List[Tuple[str, str, str, str, str]]],
    scenario: Optional[Dict[str, Any]] = None,
) -> Session:
    """Build the live session: discover services, derive the solver graph,
    and bind the affinity-engine API.  Raises ``click.ClickException`` when
    the scenario carries no usable dependency topology."""
    services = discover_services(mutator)
    edges = edges_from_routes(service_routes or [], services)
    try:
        if not edges and scenario is not None:
            # Workloads with no `*_SERVICE_ADDR` env-var dependencies (e.g.
            # hotelReservation, which resolves its gRPC backends through Consul)
            # yield no env-derived edges.  Fall back to the hand-curated static
            # `topology.json` adjacent to the scenario — the same edge set the
            # fraction-solver gate consumes via load_static_topology — so the placement-session
            # cross-node fraction is defined.  Online Boutique keeps its
            # env-derived edges (this branch is skipped when edges are present).
            topo_path = _adjacent_topology_path(scenario)
            if topo_path:
                static_edges, _ = load_static_topology(topo_path)
                # Keep only edges whose endpoints are live discovered services.
                # A topology that names a not-yet-deployed, scaled-to-zero, or
                # excluded endpoint then degrades to the helpful "no inter-service
                # edges" error below (and is normalized to a ClickException),
                # instead of raising a raw ValueError mid-run when the solver
                # later validates edges against the live service set.
                live = set(services)
                edges = [e for e in static_edges if e[0] in live and e[1] in live]
        session = build_session(
            namespace,
            levels=args.levels,
            order_seed=args.order_seed,
            solver_seed=args.solver_seed,
            replicas=args.replicas,
            mode=args.mode,
            workers=args.workers,
            packed_assignment=args.packed_assignment,
            dns_cache=args.dns_cache,
            edges=edges,
            services=services,
            api=affinity_engine.K8sApi.from_cluster(),
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    # Honour the conditions already shown to the user at resolve time (the
    # block + order are identical: same levels, same order seed).
    session.conditions = list(args.conditions)
    click.echo(
        f"placement session: complete block of {len(session.conditions)} condition(s) "
        f"[{', '.join(c.name for c in session.conditions)}] "
        f"r={session.replicas} mode={session.mode} "
        f"packing={session.packed_assignment} "
        f"dnsCache={session.dns_cache or 'default'} "
        f"orderSeed={session.order_seed} solverSeed={session.solver_seed}"
    )
    return session


def _restore_placements(session: Session, namespace: str) -> None:
    """End-of-run cleanup: clear engine affinity patches back to defaults.

    For a C3 session (DNS-cache axis), also reset the pod DNS resolver to the
    cluster default (cache-on) — ``affinity_engine.restore`` does not touch
    ``dnsConfig``, so a cache-off override would otherwise leak past the run.
    """
    click.echo("Cleanup: restoring affinity placements to default scheduling...")
    try:
        affinity_engine.restore(session.api, namespace, wait=False)
        click.echo("  placements restored.")
    except Exception as e:
        click.echo(f"  Warning: placement restore failed: {e}", err=True)
    if session.dns_cache is not None:
        try:
            dns_cache_engine.apply_dns_cache(
                session.api,
                namespace,
                list(session.services),
                dns_cache_engine.CACHE_ON,
                wait=False,
            )
            click.echo("  DNS-cache reset to cluster default (cache-on).")
        except Exception as e:
            click.echo(f"  Warning: DNS-cache reset failed: {e}", err=True)


def _selfheal_dns(session: Session, namespace: str) -> None:
    """Startup self-heal: reset app DNS to the cluster default (cache-on).

    A C3 cache-off override is applied per condition and reset at clean
    cleanup (:func:`_restore_placements`), but an **aborted or killed** run
    (Ctrl-C / SIGTERM / crash) can leave the override in place — neither
    ``affinity_engine.restore`` nor :func:`_clear_stale_placement` ever touch
    ``dnsConfig``. This clears any stale override **before** the run measures
    anything, making the DNS path symmetric with the placement self-heal so a
    leaked cache-off override cannot silently corrupt a later placement-session run's
    cache-on baseline (the H2 control assumption). Runs for **every** placement-session
    session regardless of its own cache axis (the leak it heals is from a
    *prior* run); a no-op (no rollout) when no override is present.
    """
    try:
        dns_cache_engine.apply_dns_cache(
            session.api,
            namespace,
            list(session.services),
            dns_cache_engine.CACHE_ON,
            wait=False,
        )
    except Exception as e:
        click.echo(f"  Warning: DNS-cache startup self-heal failed: {e}", err=True)


def _strategies_overridden_on_cli() -> bool:
    """True when -s/--strategies was given explicitly (vs. its default).

    Uses the live Click context, so it must be called from inside the
    command; returns ``False`` when no context is active (unit tests calling
    helpers directly).
    """
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return False
    from click.core import ParameterSource

    return ctx.get_parameter_source("strategies") == ParameterSource.COMMANDLINE


# Holds the open lock file object for the process lifetime so the advisory
# flock stays held until this process exits. The OS releases it automatically
# on any exit (clean, crash, kill -9), so no stale-lock cleanup is needed.
_run_lock_file: Optional[Any] = None


def _acquire_run_lock() -> None:
    """Serialize ``chaosprobe run`` via an advisory lock on ``~/.chaosprobe/run.lock``.

    Two concurrent runs mutate the same cluster (placement constraints, rollout
    restarts, prepull pods) and corrupt each other. This takes a non-blocking
    ``flock``; if another run already holds it, report the holder and exit 1.
    """
    global _run_lock_file
    lock_dir = Path.home() / ".chaosprobe"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "run.lock"
    fd = open(lock_path, "a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fd.seek(0)
        holder = fd.read().strip() or "unknown"
        fd.close()
        click.echo(
            f"Error: another `chaosprobe run` is already active ({holder}).\n"
            f"  Concurrent runs mutate the same cluster (placement, rollouts,\n"
            f"  prepull) and corrupt each other. Wait for it to finish or stop\n"
            f"  it, then retry.\n"
            f"  Lock: {lock_path}",
            err=True,
        )
        sys.exit(1)
    fd.seek(0)
    fd.truncate()
    fd.write(f"PID {os.getpid()} started {datetime.now(timezone.utc).isoformat()}\n")
    fd.flush()
    _run_lock_file = fd  # keep alive → lock held until process exit


@click.command()
@click.option(
    "--namespace",
    "-n",
    default=None,
    help="Namespace containing the application (default: read from experiment YAML)",
)
@click.option(
    "--output-dir",
    "-o",
    default="results",
    help="Base directory for results (a timestamped subdirectory is created)",
)
@click.option(
    "--strategies",
    "-s",
    default=",".join(DEFAULT_RUN_STRATEGIES),
    help="Comma-separated strategies to test (default: all)",
)
@click.option("--timeout", "-t", default=300, type=int, help="Timeout per experiment in seconds")
@click.option("--seed", default=42, type=int, help="Seed for the random strategy")
@click.option(
    "--seeds",
    default=None,
    type=str,
    help=(
        "Comma-separated seed list (e.g. '42,137,271') for multi-seed random "
        "runs.  Expands the random strategy into one per seed (named "
        "'random:42', 'random:137', ...).  Overrides --seed.  All other "
        "strategies are unaffected."
    ),
)
@click.option(
    "--settle-time",
    default=60,
    type=int,
    help=(
        "Length (in seconds) of the pre/post-chaos prober sample "
        "windows.  Was previously capped at 15s by a hardcoded min(), "
        "which created bimodal scores because chaos-induced recovery "
        "often took 20-60s and the 15s cap caught only the error "
        "phase.  60s default gives the cluster enough time to recover "
        "post-chaos so the sample window captures actual resilience, "
        "not just transient damage.  Adds no fixed sleep — the dynamic "
        "readiness gates handle pre-iteration waiting."
    ),
)
@click.option(
    "--app-ready-timeout",
    default=240,
    type=click.IntRange(min=1),
    help=(
        "Upper bound (seconds) for the per-iteration functional app-readiness "
        "gate after the clean-baseline restart.  The default 240s suits "
        "fast-restarting apps (Online Boutique).  Raise it for slow-recovering "
        "workloads — e.g. hotelReservation, whose frontend cannot re-resolve "
        "its gRPC backends through Consul for ~2-4 min after a restart — so "
        "the gate does not false-taint every iteration with "
        "'app_ready_timeout'.  The gate returns early as soon as it passes, so "
        "a larger budget costs nothing when the app recovers quickly."
    ),
)
@click.option(
    "--pre-gate-warmup",
    default=0,
    type=click.IntRange(min=0),
    help=(
        "Seconds of sustained warm-up load to pump on the probed routes "
        "BEFORE the app-readiness gate starts counting (default 0 = off).  "
        "Some workloads only become reachable under sustained traffic -- "
        "hotelReservation's gRPC clients enter a too_many_pings/GoAway "
        "keepalive storm in the no-traffic window after a restart that only "
        "settles once traffic keeps the streams active.  The post-gate "
        "warm-up cannot help (it runs only after the gate passes, which the "
        "storm prevents), so set this for such workloads (e.g. 90) to let "
        "the gate pass cleanly instead of false-tainting every iteration "
        "with 'app_ready_timeout'.  Fast-recovering apps leave it at 0."
    ),
)
@click.option(
    "--gate-sustained-load/--no-gate-sustained-load",
    default=False,
    help=(
        "Keep sustained warm-up load flowing on the probed routes THROUGHOUT the "
        "app-readiness gate, not just before it (default off).  A one-shot "
        "--pre-gate-warmup is undone by the gate's own intermittent probing: "
        "between the 3s probes a traffic-dependent app (hotelReservation's gRPC "
        "keepalive storm) goes idle and re-destabilises, so the consecutive-OK "
        "count never builds.  This pumps load via a background thread for the "
        "whole gate window so its probes land on a continuously-exercised app.  "
        "Pair with --pre-gate-warmup for a head start.  Online Boutique leaves "
        "it off."
    ),
)
@click.option(
    "--gate-load-concurrency",
    default=6,
    type=click.IntRange(min=1, max=64),
    help=(
        "Parallel warm-up loops PER ROUTE for --gate-sustained-load (default 6, "
        "max 64; no effect unless --gate-sustained-load is set).  A single loop "
        "per route keeps only one request in flight, which does NOT settle a deep "
        "gRPC fan-out's keepalive storm: measured on hotelReservation, 1 loop/route "
        "never passed the gate in 420s while 6 loops/route settled /hotels in "
        "~15s.  Raise for workloads whose deepest route needs more concurrent "
        "in-flight requests to keep every stream active.  Capped at 64/route so a "
        "typo cannot spawn enough wget processes to exhaust the probe pod."
    ),
)
@click.option(
    "--experiment",
    "-e",
    multiple=True,
    default=(
        "scenarios/online-boutique/pod-delete.yaml",
        "scenarios/online-boutique/cpu-hog.yaml",
    ),
    help=(
        "Path to a placement-experiment YAML file. Pass -e multiple times "
        "to run a multi-fault matrix: every placement strategy is executed "
        "once per experiment file. Results are keyed as "
        "'<fault-label>__<strategy>' so the two faults can be compared "
        "side-by-side."
    ),
)
@click.option(
    "--iterations",
    "-i",
    default=1,
    type=click.IntRange(min=1),
    help="Number of iterations per strategy (default: 1)",
)
@click.option(
    "--load-profile",
    type=click.Choice(["steady", "ramp", "spike"]),
    default="steady",
    help="Locust load profile during each experiment (default: steady)",
)
@click.option(
    "--locustfile",
    type=click.Path(exists=True),
    default=None,
    help="Custom Locust file for load generation",
)
@click.option(
    "--target-url",
    default=None,
    help="Target URL for load generation (default: auto port-forward)",
)
@click.option(
    "--visualize/--no-visualize",
    "do_visualize",
    default=True,
    show_default=True,
    help="Generate visualization charts after experiments complete",
)
@click.option(
    "--measure-latency/--no-measure-latency",
    "measure_latency",
    default=True,
    show_default=True,
    help="Measure inter-service latency during each experiment",
)
@click.option(
    "--measure-redis/--no-measure-redis",
    "measure_redis",
    default=True,
    show_default=True,
    help="Measure Redis throughput during each experiment",
)
@click.option(
    "--measure-disk/--no-measure-disk",
    "measure_disk",
    default=True,
    show_default=True,
    help="Measure disk I/O throughput during each experiment",
)
@click.option(
    "--measure-resources/--no-measure-resources",
    "measure_resources",
    default=True,
    show_default=True,
    help="Measure node/pod resource utilization during each experiment",
)
@click.option(
    "--collect-logs/--no-collect-logs",
    "collect_logs",
    default=True,
    show_default=True,
    help="Collect container logs from target deployment after each experiment",
)
@click.option(
    "--measure-prometheus/--no-measure-prometheus",
    "measure_prometheus",
    default=True,
    show_default=True,
    help="Query Prometheus for cluster metrics during each experiment",
)
@click.option(
    "--prometheus-url",
    multiple=True,
    help="Prometheus server URL(s); repeat for multiple instances (auto-discovered if omitted)",
)
@click.option(
    "--measure-conntrack/--no-measure-conntrack",
    "measure_conntrack",
    default=True,
    show_default=True,
    help=(
        "Sample per-node protocol-labeled conntrack entry counts during each "
        "experiment (privileged hostNetwork sampler pod per worker)"
    ),
)
@click.option(
    "--baseline-duration",
    type=int,
    default=0,
    help=(
        "Seconds to collect steady-state 'normal' metrics before chaos"
        " (default: 0 = use settle time)"
    ),
)
@click.option(
    "--batch-id",
    default=None,
    help=(
        "Label grouping this run with others from the same batch/session "
        "(default: the current UTC date, YYYY-MM-DD). Recorded as "
        "summary.json -> batchId and emitted by `export` so mixed-run "
        "analysis can separate run-to-run cluster drift from strategy effects."
    ),
)
@click.option(
    "--replicas",
    type=int,
    default=0,
    help=(
        "Scale all app deployments to this many replicas before the run "
        "(0 = leave the manifests unchanged). Use for multi-replica placement "
        "experiments, where node-level faults differentiate placements."
    ),
)
@click.option(
    "--fraction-levels",
    "--v2-levels",
    "fraction_levels",
    default=None,
    help=(
        "Comma-separated target cross-node fractions (e.g. '0,0.25,0.5,0.75,1.0'). "
        "Activates the complete-block session driver: every level becomes one "
        "condition (solver-targeted placement via the affinity engine) executed "
        "through the same iteration pipeline as a strategy, visited in a "
        "randomized order drawn from --order-seed. Mutually exclusive with "
        "-s/--strategies, --seeds, and --replicas; runs exactly one fault per "
        "session (pass exactly one -e). A/A pair = two runs with identical "
        "placement-session args incl. --solver-seed (identical placements); "
        "--order-seed may differ. (--v2-levels: back-compat alias.)"
    ),
)
@click.option(
    "--order-seed",
    "--v2-order-seed",
    "order_seed",
    type=int,
    default=None,
    help=(
        "Seed for the randomized condition order of the complete block "
        f"(default: {_DEFAULT_ORDER_SEED}). Recorded in summary.json -> "
        "session.orderSeed/orderApplied."
    ),
)
@click.option(
    "--solver-seed",
    "--v2-solver-seed",
    "solver_seed",
    type=int,
    default=None,
    help=(
        "Seed for the fraction solver's placements (default: "
        f"{_DEFAULT_SOLVER_SEED}). Identical-placement A/A session pairs "
        "share this seed while --order-seed may differ."
    ),
)
@click.option(
    "--replica-degree",
    "--v2-replicas",
    "replica_degree",
    type=int,
    default=None,
    help=(
        f"Replica count per service, one of {sorted(affinity_engine.SUPPORTED_REPLICAS)} "
        f"(default: {_DEFAULT_REPLICAS}; r=2 deliberately unsupported per DESIGN §2.3)."
    ),
)
@click.option(
    "--placement-mode",
    "--v2-mode",
    "placement_mode",
    type=click.Choice([affinity_engine.MODE_PACKED, affinity_engine.MODE_ANTI_AFFINE]),
    default=None,
    help=(
        "Replica packing mode (default: packed). r=1: the two modes are "
        "physically identical (node pin). r=3 packed: all replicas pinned to "
        "the solver's node. r=3 anti-affine: required podAntiAffinity, the "
        "scheduler chooses 3 distinct nodes (no solver pin, no live fraction)."
    ),
)
@click.option(
    "--worker-nodes",
    "--v2-workers",
    "worker_nodes",
    default=None,
    help=(
        "Ordered comma-separated worker node names; solver node index i maps "
        "to the i-th name (same convention as scripts/m1b_gate.py --workers). "
        "Required with --fraction-levels. (--v2-workers: back-compat alias.)"
    ),
)
@click.option(
    "--packed-assignment",
    "--v2-packed-assignment",
    "packed_assignment",
    type=click.Choice(list(PACKED_ASSIGNMENTS)),
    default=None,
    help=(
        f"Pinned-cell (r=1 / r=3 packed) assignment (default: "
        f"{_DEFAULT_PACKED_ASSIGNMENT}). '{PACKED_ASSIGNMENT_SOLVER}' uses the "
        f"fraction solver to hit the condition's target f (the H1 dose-response "
        f"sweep). '{PACKED_ASSIGNMENT_ROUND_ROBIN}' uses the capacity-feasible "
        f"per-service round-robin packing (H3 replication-rescue; f-independent, "
        f"matches the M1b-verified packed semantics)."
    ),
)
@click.option(
    "--dns-cache",
    "--v2-dns-cache",
    "dns_cache",
    type=click.Choice(list(dns_cache_engine.CACHE_MODES)),
    default=None,
    help=(
        "DNS-cache axis for the C3 / H2 campaign (default: unset = cluster "
        "default, no override). 'off' overrides each app pod's dnsConfig to the "
        "CoreDNS clusterIP over UDP (the cross-node-UDP baseline); 'on' uses "
        "the kubelet-default NodeLocal DNSCache. Applied per condition after "
        "placement; reset to cluster default at cleanup."
    ),
)
@neo4j_uri_option
@neo4j_user_option
@neo4j_password_option
def run(
    namespace: Optional[str],
    output_dir: Optional[str],
    strategies: str,
    timeout: int,
    seed: int,
    seeds: Optional[str],
    settle_time: int,
    app_ready_timeout: int,
    pre_gate_warmup: int,
    gate_sustained_load: bool,
    gate_load_concurrency: int,
    experiment: Tuple[str, ...],
    iterations: int,
    load_profile: Optional[str],
    locustfile: Optional[str],
    target_url: Optional[str],
    do_visualize: bool,
    measure_latency: bool,
    measure_redis: bool,
    measure_disk: bool,
    measure_resources: bool,
    collect_logs: bool,
    measure_prometheus: bool,
    measure_conntrack: bool,
    prometheus_url: Tuple[str, ...],
    baseline_duration: int,
    batch_id: Optional[str],
    replicas: int,
    fraction_levels: Optional[str],
    order_seed: Optional[int],
    solver_seed: Optional[int],
    replica_degree: Optional[int],
    placement_mode: Optional[str],
    worker_nodes: Optional[str],
    packed_assignment: Optional[str],
    dns_cache: Optional[str],
    neo4j_uri: Optional[str],
    neo4j_user: str,
    neo4j_password: str,
):
    """Run placement experiments automatically.

    Iterates through the default set of strategies (the two methodology
    controls plus the six placement strategies; see --strategies), applies
    each placement, runs the shared experiment, collects results
    (including pod recovery metrics), and saves everything to a
    timestamped results directory.

    With --fraction-levels the run becomes a complete-block session instead:
    each target cross-node fraction is one condition (fraction-solver
    placement realized through the replica-level affinity engine, achieved
    placement verified from live pods), visited in a randomized order drawn
    from --order-seed. An A/A pair is two runs with identical placement-session
    args including --solver-seed; --order-seed may differ.

    \b
    Example:
      chaosprobe run -n online-boutique
      chaosprobe run -n online-boutique -s colocate,spread
      chaosprobe run -n online-boutique -o results/my-run
      chaosprobe run -n online-boutique -i 3  # 3 iterations per strategy
      chaosprobe run -n online-boutique -i 3 \\
          -e scenarios/online-boutique/pod-delete.yaml \\
          --fraction-levels 0,0.25,0.5,0.75,1.0 --worker-nodes worker1,worker2,worker3
    """
    # ── complete-block session resolution (validated before any mutation) ──
    placement_args = _resolve_placement_args(
        fraction_levels,
        order_seed,
        solver_seed,
        replica_degree,
        placement_mode,
        worker_nodes,
        packed_assignment,
        dns_cache,
        strategies_overridden=_strategies_overridden_on_cli(),
        seeds=seeds,
        scale_replicas=replicas,
        experiments=experiment,
    )

    if placement_args is None:
        strategy_list = [s.strip() for s in strategies.split(",")]
        valid_strategies = {"baseline", "default"} | {s.value for s in PlacementStrategy}
        for s in strategy_list:
            if s not in valid_strategies:
                click.echo(
                    f"Error: Unknown strategy '{s}'. Valid: {', '.join(sorted(valid_strategies))}",
                    err=True,
                )
                sys.exit(1)
    else:
        # The complete block in its randomized applied order; never re-sorted
        # (the recorded order is what licenses Page's L for H1).
        strategy_list = [c.name for c in placement_args.conditions]

    # Serialize against any other active run before touching the cluster — two
    # concurrent runs corrupt each other's placement/rollout/prepull state.
    _acquire_run_lock()

    if placement_args is None:
        # Expand `random` into per-seed variants (when --seeds is set), then
        # order strategies by contention severity so lingering node pressure
        # from heavy strategies doesn't skew later runs. (--iterations >= 1 is
        # already enforced by the option's IntRange.)
        strategy_list = _expand_random_seeds(strategy_list, seeds)
        strategy_list.sort(key=_strategy_execution_order)

    # Create output directory
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    results_dir = Path(output_dir or "results") / ts
    results_dir.mkdir(parents=True, exist_ok=True)

    # ── Fail-fast: verify K8s API is reachable ──
    # If the API server is down (e.g. control plane crashed in a previous
    # run), there's no point loading scenarios, deploying manifests, or
    # building probes.  Detect this early and give a clear error.
    ensure_k8s_config()
    try:
        k8s_client.CoreV1Api().list_namespace(limit=1)
    except (ApiException, MaxRetryError, ConnectionError, OSError) as exc:
        try:
            api_host = K8sConfig.get_default_copy().host or "<unknown>"
        except Exception:
            api_host = "<unknown>"
        click.echo(
            f"Error: K8s API server at {api_host} unreachable — cannot proceed.\n"
            f"  Detail: {exc}\n"
            f"  The control plane may need restarting (e.g. after an adversarial crash).\n"
            f"  Restart the control plane node and retry.",
            err=True,
        )
        sys.exit(1)

    # ── Multi-fault matrix support ──────────────────────────────────────
    # ``experiment`` is a tuple of YAML paths (Click multi-option).  The
    # first one is loaded as the "primary" scenario used for namespace
    # detection, image pre-pull, and probe-image extraction.  All others
    # are loaded separately and iterated as a second outer loop wrapped
    # around the strategy loop further down.
    if not experiment:
        click.echo("Error: at least one --experiment / -e must be provided", err=True)
        sys.exit(1)

    primary_experiment = experiment[0]
    # Load scenario, deploy manifests, discover topology, build probes.
    # All fault experiments must target the same namespace.
    shared_scenario, namespace, experiment_file, service_routes = _load_and_prepare_scenario(
        primary_experiment, namespace
    )

    # Pre-load every scenario into (label, scenario, fault_types) triples so
    # parse errors fail fast before cluster setup (see _build_fault_scenarios).
    fault_scenarios = _build_fault_scenarios(
        experiment, primary_experiment, shared_scenario, namespace
    )

    # Ensure LitmusChaos is ready once with all required experiment types
    # across the whole fault matrix.
    experiment_types = _collect_experiment_types(fault_scenarios, strategy_list)
    if not _ensure_litmus_setup(namespace, experiment_types):
        click.echo("Error: LitmusChaos setup failed", err=True)
        sys.exit(1)

    # Create reusable instances
    mutator = PlacementMutator(namespace)
    metrics_collector = MetricsCollector(namespace)

    # ── placement session: discover services, derive the solver graph, bind the API ──
    placement_session: Optional[Session] = None
    if placement_args is not None:
        placement_session = _init_placement_session(
            placement_args, namespace, mutator, service_routes, shared_scenario
        )
        # Self-heal a DNS-cache override a prior aborted C3 run may have left:
        # reset to the cluster default before this run measures anything, so a
        # stale cache-off path cannot corrupt the cache-on baseline. Symmetric
        # with the placement self-heal below (_clear_stale_placement).
        _selfheal_dns(placement_session, namespace)

    if replicas:
        click.echo(f"Scaling app deployments to {replicas} replica(s)...")
        scaled = mutator.scale_deployments(replicas)
        click.echo(f"  Scaled {len(scaled)} deployment(s) to {replicas} replica(s)")

    _print_run_banner(
        namespace,
        experiment_file,
        strategy_list,
        iterations,
        results_dir,
        timeout,
        settle_time,
        measure_latency=measure_latency,
        measure_redis=measure_redis,
        measure_disk=measure_disk,
        measure_resources=measure_resources,
        measure_prometheus=measure_prometheus,
        measure_conntrack=measure_conntrack,
        prometheus_url=prometheus_url,
        collect_logs=collect_logs,
        baseline_duration=baseline_duration,
    )

    # ── Fresh-start: clear stale placement before pre-flight ──
    _clear_stale_placement(mutator, namespace)

    # ── Pre-flight checks ──────────────────────────────────────
    click.echo("\nPre-flight checks...")
    load_service = extract_load_service(shared_scenario)
    preflight = run_preflight_checks(
        namespace,
        measure_prometheus=measure_prometheus,
        prometheus_url=prometheus_url,
        neo4j_uri=neo4j_uri,
        load_profile=load_profile,
        target_url=target_url,
        timeout=timeout,
        load_service=load_service,
    )
    core_api = preflight["core_api"]
    chaoscenter_config = preflight["chaoscenter_config"]
    target_url = preflight["target_url"]
    frontend_pf_port = preflight["frontend_pf_port"]

    click.echo("")

    overall_results: Dict[str, Any] = _init_overall_results(
        fault_scenarios, namespace, iterations, core_api, batch_id
    )
    _multi_fault = len(fault_scenarios) > 1

    total = len(strategy_list) * len(fault_scenarios)
    passed = 0
    failed = 0

    # Neo4j graph store — the primary data store (see _connect_graph_store).
    graph_store = _connect_graph_store(
        neo4j_uri, neo4j_user, neo4j_password, namespace, service_routes
    )

    # Snapshot node pod-request usage once for reproducible best-fit (see
    # _snapshot_node_usage_for_bestfit), then persist the exact view best-fit
    # was placed against so analysis can reproduce its decisions from the JSON.
    node_usage_snapshot = _snapshot_node_usage_for_bestfit(mutator, namespace)
    overall_results["nodeUsageSnapshot"] = {
        node: {"cpu_millicores": cpu_m, "memory_bytes": mem_b}
        for node, (cpu_m, mem_b) in node_usage_snapshot.items()
    }

    # Pre-pull cmdProbe images onto the workers before iterations start (see
    # _prepull_probe_images_onto_workers).
    _prepull_probe_images_onto_workers(mutator, namespace, fault_scenarios)

    # ── Outer loop: per-fault scenario ─────────────────────────────────
    # When multiple --experiment / -e flags were passed, run the full
    # placement matrix once per fault.  This realises the "test fault
    # class to refute or confirm the churn-vs-contention story"
    # recommendation from the critical review.
    for fault_pos, (fault_label, fault_scenario, _fault_types) in enumerate(fault_scenarios):
        click.echo(f"\n{'═' * 60}")
        click.echo(f"  FAULT: {fault_label}")
        click.echo(f"{'═' * 60}")
        fault_target = extract_target_deployment(fault_scenario)
        # Build per-fault context so each fault's scenario / target is
        # swapped in fresh for execute_strategy.
        run_ctx = RunContext(
            namespace=namespace,
            timeout=timeout,
            seed=seed,
            settle_time=settle_time,
            app_ready_timeout=app_ready_timeout,
            pre_gate_warmup_s=pre_gate_warmup,
            sustained_gate_load=gate_sustained_load,
            gate_load_concurrency=gate_load_concurrency,
            iterations=iterations,
            baseline_duration=baseline_duration,
            measure_latency=measure_latency,
            measure_redis=measure_redis,
            measure_disk=measure_disk,
            measure_resources=measure_resources,
            measure_prometheus=measure_prometheus,
            measure_conntrack=measure_conntrack,
            prometheus_url=prometheus_url,
            collect_logs=collect_logs,
            load_profile=load_profile,
            locustfile=locustfile,
            target_url=target_url,
            neo4j_uri=neo4j_uri,
            neo4j_user=neo4j_user,
            neo4j_password=neo4j_password,
            shared_scenario=fault_scenario,
            service_routes=service_routes,
            target_deployment=fault_target,
            core_api=core_api,
            chaoscenter_config=chaoscenter_config,
            frontend_pf_port=frontend_pf_port,
            load_service=load_service,
            metrics_collector=metrics_collector,
            mutator=mutator,
            graph_store=graph_store,
            ts=ts,
            session=placement_session,
        )

        for strat_pos, strategy_name in enumerate(strategy_list, 1):
            idx = _global_strategy_index(fault_pos, strat_pos, len(strategy_list))
            try:
                strategy_result, strategy_passed = execute_strategy(
                    run_ctx,
                    strategy_name,
                    idx,
                    total,
                )
            except Exception as e:
                click.echo(
                    f"\n  FATAL ERROR in strategy '{strategy_name}' "
                    f"under fault '{fault_label}': {e}",
                    err=True,
                )
                strategy_result = _error_strategy_result(strategy_name, fault_label, str(e))
                strategy_passed = False
            if _record_strategy_result(
                overall_results,
                fault_label,
                strategy_name,
                strategy_result,
                strategy_passed,
                multi_fault=_multi_fault,
            ):
                passed += 1
            else:
                failed += 1

            # Persist partial results after each strategy so that a crash
            # in a later strategy doesn't lose everything collected so far.
            _save_partial_results(overall_results, results_dir)

    # ── Final cleanup: clear placement ──
    click.echo(f"\n{'─' * 60}")
    if placement_session is not None:
        # Engine-managed sessions also need replicas/affinity reset, which the
        # named-strategy nodeSelector clear below does not touch.
        _restore_placements(placement_session, namespace)
    click.echo("Cleanup: Clearing placement constraints...")
    try:
        mutator.clear_placement(wait=False)
        click.echo("  Placement cleared.")
    except Exception as e:
        click.echo(f"  Warning: cleanup failed: {e}")

    if measure_conntrack:
        _cleanup_conntrack_samplers(core_api)

    # ── Write overall summary ──
    overall_results["summary"] = {
        "totalStrategies": total,
        "passed": passed,
        "failed": failed,
        "completedAt": datetime.now(timezone.utc).isoformat(),
    }
    placement_match = summarise_placement_match_rates(overall_results.get("strategies", {}))
    if placement_match:
        overall_results["summary"]["placementMatchRates"] = placement_match
    overall_results["iterations"] = iterations
    if placement_session is not None:
        # Everything the A/A comparison and the C1 analysis need: the block,
        # the applied order + both seeds, the (r, mode, workers) cell, and the
        # per-level solver/live fractions with acceptance verdicts.
        overall_results["session"] = session_metadata(placement_session)

    write_run_results(
        overall_results,
        results_dir,
        passed=passed,
        failed=failed,
        total=total,
        ts=ts,
        do_visualize=do_visualize,
        graph_store=graph_store,
    )
