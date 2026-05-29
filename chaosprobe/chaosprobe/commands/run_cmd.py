"""CLI command: chaosprobe run — automated full experiment matrix."""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from chaosprobe.config.loader import load_scenario
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
)
from chaosprobe.orchestrator.run_phases import (
    init_graph_store,
    run_preflight_checks,
    summarise_placement_match_rates,
    write_run_results,
)
from chaosprobe.orchestrator.strategy_runner import RunContext, execute_strategy
from chaosprobe.placement.mutator import PlacementMutator
from chaosprobe.placement.strategy import PlacementStrategy
from chaosprobe.probes.builder import (
    DEFAULT_REGISTRY,
    RustProbeBuilder,
    ensure_image_pull_secret,
    extract_cmdprobe_images,
    patch_probe_images,
    prepull_probe_images,
)
from chaosprobe.provisioner.setup import LitmusSetup, UnknownExperimentType


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
            pass
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


def _load_and_prepare_scenario(
    experiment: str,
    namespace: Optional[str],
    deploy: bool = True,
) -> Tuple[dict, str, Path, Optional[List[dict]]]:
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

    # Auto-build Rust cmdProbes if probes/ directory exists.
    # Always pushes to the configured registry — cluster nodes can only pull
    # the image via `docker pull`, so push is unconditionally required.
    if shared_scenario.get("probes"):
        click.echo(f"\n  Found {len(shared_scenario['probes'])} Rust probe(s), building...")
        registry = os.environ.get("CHAOSPROBE_REGISTRY", DEFAULT_REGISTRY)
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
        if ensure_image_pull_secret(namespace, registry):
            click.echo("  Registry credentials synced to cluster")

        # Defensive belt: if any cmdProbe in the spec still has the
        # placeholder image (shouldn't happen given the strictness above,
        # but covers a probe defined in YAML without a matching binary),
        # abort rather than silently dropping it from the experiment.
        _assert_no_unbuilt_cmdprobes(shared_scenario["experiments"])

    return shared_scenario, namespace, experiment_file, service_routes


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
    if collect_logs:
        click.echo("  Logs:       Collecting container logs from target deployment")
    if baseline_duration > 0:
        click.echo(f"  Baseline:   {baseline_duration}s steady-state collection before chaos")
    click.echo("")


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
    default="baseline,default,colocate,spread,adversarial,random,best-fit,dependency-aware",
    help="Comma-separated strategies to test (default: all)",
)
@click.option("--timeout", "-t", default=300, type=int, help="Timeout per experiment in seconds")
@click.option("--seed", default=42, type=int, help="Seed for the random strategy")
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
    "--experiment",
    "-e",
    multiple=True,
    default=("scenarios/online-boutique/placement-experiment.yaml",),
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
    type=int,
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
    "--baseline-duration",
    type=int,
    default=0,
    help=(
        "Seconds to collect steady-state 'normal' metrics before chaos"
        " (default: 0 = use settle time)"
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
    settle_time: int,
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
    prometheus_url: Tuple[str, ...],
    baseline_duration: int,
    neo4j_uri: Optional[str],
    neo4j_user: str,
    neo4j_password: str,
):
    """Run placement experiments automatically.

    Iterates through placement strategies (baseline, default, colocate,
    spread, adversarial, random, best-fit, dependency-aware), applies
    each placement, runs the shared experiment, collects results
    (including pod recovery metrics), and saves everything to a
    timestamped results directory.

    \b
    Example:
      chaosprobe run -n online-boutique
      chaosprobe run -n online-boutique -s colocate,spread
      chaosprobe run -n online-boutique -o results/my-run
      chaosprobe run -n online-boutique -i 3  # 3 iterations per strategy
    """
    strategy_list = [s.strip() for s in strategies.split(",")]
    valid_strategies = {"baseline", "default"} | {s.value for s in PlacementStrategy}
    for s in strategy_list:
        if s not in valid_strategies:
            click.echo(
                f"Error: Unknown strategy '{s}'. Valid: {', '.join(sorted(valid_strategies))}",
                err=True,
            )
            sys.exit(1)

    # Sort by contention severity: low-contention strategies first so
    # lingering node pressure from heavy strategies doesn't skew results.
    # baseline/default (not in the enum) get order 0/-1 to run first.
    def _sort_key(name: str) -> int:
        try:
            return PlacementStrategy(name).execution_order
        except ValueError:
            return -1 if name == "baseline" else 0

    strategy_list.sort(key=_sort_key)

    if iterations < 1:
        click.echo("Error: --iterations must be >= 1", err=True)
        sys.exit(1)

    # Create output directory
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    results_dir = Path(output_dir) / ts
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

    # Pre-load every additional scenario so any parse errors fail fast
    # before we spin up the cluster setup.  Each entry is a (label,
    # scenario_dict, fault_types) triple keyed for downstream loops.
    fault_scenarios: List[Tuple[str, Dict[str, Any], List[str]]] = []
    for exp_path in experiment:
        label = Path(exp_path).stem  # e.g. "placement-experiment" / "placement-cpu-hog"
        if exp_path == primary_experiment:
            scenario_dict = shared_scenario
        else:
            scenario_dict, _ns_unused, _file_unused, _routes_unused = _load_and_prepare_scenario(
                exp_path, namespace, deploy=False
            )
        fault_scenarios.append((label, scenario_dict, extract_experiment_types(scenario_dict)))

    # Ensure LitmusChaos is ready once with all required experiment types
    # across the whole fault matrix.
    experiment_types: List[str] = []
    for _label, _scn, types in fault_scenarios:
        for t in types:
            if t not in experiment_types:
                experiment_types.append(t)
    # Baseline uses pod-cpu-hog (trivial fault) instead of pod-delete
    if "baseline" in strategy_list and "pod-cpu-hog" not in experiment_types:
        experiment_types.append("pod-cpu-hog")
    if not _ensure_litmus_setup(namespace, experiment_types):
        click.echo("Error: LitmusChaos setup failed", err=True)
        sys.exit(1)

    # Create reusable instances
    mutator = PlacementMutator(namespace)
    metrics_collector = MetricsCollector(namespace)

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

    overall_results: Dict[str, Any] = {
        "runId": f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "runMetadata": gather_run_metadata(core_api=core_api),
        "namespace": namespace,
        "iterations": iterations,
        # Multi-fault matrix: keyed as ``faults[label][strategy]``.  When
        # only one fault is supplied (the historical default), the
        # outer key is the scenario filename stem so downstream consumers
        # can still find a stable label.
        "faults": {label: {"strategies": {}} for label, _, _ in fault_scenarios},
        "faultExperiments": [label for label, _, _ in fault_scenarios],
        # Flat view, kept for backwards compatibility with the existing
        # visualizer / HTML report / per-strategy file writer.  Keys are
        # bare strategy names when there is one fault, ``f"{fault}__{strategy}"``
        # otherwise.  Both views point at the same per-strategy dict, so
        # writes through either are observed by both.
        "strategies": {},
    }
    _multi_fault = len(fault_scenarios) > 1

    total = len(strategy_list) * len(fault_scenarios)
    passed = 0
    failed = 0

    # Neo4j graph store — primary data store
    graph_store = None
    if neo4j_uri:
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
        except ImportError:
            click.echo(
                "Error: Neo4j driver not installed"
                " (install with: uv pip install chaosprobe[graph])",
                err=True,
            )
            sys.exit(1)
        except Exception as e:
            click.echo(f"Error: Neo4j connection failed ({e})", err=True)
            click.echo(
                "Neo4j is required as the primary data store. Check connection and retry.", err=True
            )
            sys.exit(1)

    # Snapshot node pod-request usage ONCE per run.  Reused for every
    # best-fit invocation so its computed topology is reproducible across
    # strategies (otherwise lingering chaos infra / monitoring pods from
    # earlier strategies silently shift best-fit's bin capacity, making
    # results non-comparable between strategies in the same run).
    #
    # CRITICAL: exclude the app deployments' own pods from the snapshot.
    # Best-fit is about to repack those pods; their current footprint is
    # not "already used" capacity it has to work around.  If we left them
    # in, best-fit would see nodes as far fuller than reality and
    # over-spread to dodge imagined collisions.  The snapshot should
    # represent only the *immovable* baseline: kube-system, monitoring,
    # chaos infra, loadgen, etc.
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

    # Persist the exact view best-fit was placed against so analysis can
    # reproduce its decisions from the results JSON alone.
    overall_results["nodeUsageSnapshot"] = {
        node: {"cpu_millicores": cpu_m, "memory_bytes": mem_b}
        for node, (cpu_m, mem_b) in node_usage_snapshot.items()
    }

    # Pre-pull cmdProbe images onto every worker node BEFORE iterations
    # start.  Combined with imagePullPolicy: IfNotPresent on the probe
    # specs, this eliminates the per-tick registry round-trips that were
    # the dominant source of "Unknown" probe verdicts under chaos (cmdProbe
    # pods couldn't pull in time when the chaos pod was bursting CPU/network
    # on the same node, dragging the score down by ~8 points per missed
    # probe even though the SUT was healthy).
    # Pre-pull probe images across the UNION of all fault scenarios so a
    # later fault doesn't trigger a fresh image pull mid-run.
    all_probe_images: List[str] = []
    seen_images: set = set()
    for _label, scn, _types in fault_scenarios:
        for img in extract_cmdprobe_images(scn.get("experiments", [])):
            if img not in seen_images:
                seen_images.add(img)
                all_probe_images.append(img)
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

    # ── Outer loop: per-fault scenario ─────────────────────────────────
    # When multiple --experiment / -e flags were passed, run the full
    # placement matrix once per fault.  This realises the "test fault
    # class to refute or confirm the churn-vs-contention story"
    # recommendation from the critical review.
    for fault_label, fault_scenario, _fault_types in fault_scenarios:
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
            iterations=iterations,
            baseline_duration=baseline_duration,
            measure_latency=measure_latency,
            measure_redis=measure_redis,
            measure_disk=measure_disk,
            measure_resources=measure_resources,
            measure_prometheus=measure_prometheus,
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
        )

        for idx, strategy_name in enumerate(strategy_list, 1):
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
                strategy_result = {
                    "strategy": strategy_name,
                    "fault": fault_label,
                    "status": "error",
                    "placement": None,
                    "experiment": None,
                    "metrics": None,
                    "error": str(e),
                }
                strategy_passed = False
            strategy_result["fault"] = fault_label
            overall_results["faults"][fault_label]["strategies"][strategy_name] = strategy_result
            flat_key = f"{fault_label}__{strategy_name}" if _multi_fault else strategy_name
            overall_results["strategies"][flat_key] = strategy_result
            if strategy_result["status"] == "error":
                failed += 1
            elif strategy_passed:
                passed += 1
            else:
                failed += 1

            # Persist partial results after each strategy so that a crash
            # in a later strategy doesn't lose everything collected so far.
            _save_partial_results(overall_results, results_dir)

    # ── Final cleanup: clear placement ──
    click.echo(f"\n{'─' * 60}")
    click.echo("Cleanup: Clearing placement constraints...")
    try:
        mutator.clear_placement(wait=False)
        click.echo("  Placement cleared.")
    except Exception as e:
        click.echo(f"  Warning: cleanup failed: {e}")

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
