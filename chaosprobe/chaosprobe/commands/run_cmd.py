"""CLI command: chaosprobe run — automated full experiment matrix."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

from chaosprobe.config.loader import load_scenario
from chaosprobe.config.topology import parse_topology_from_scenario
from chaosprobe.config.validator import validate_scenario
from chaosprobe.metrics.collector import MetricsCollector
from chaosprobe.orchestrator.preflight import (
    LITMUS_INFRA_DEPLOYMENTS,
    extract_experiment_types,
    extract_target_deployment,
    wait_for_healthy_deployments,
)
from chaosprobe.orchestrator.run_phases import (
    init_graph_store,
    run_preflight_checks,
    write_run_results,
)
from chaosprobe.orchestrator.strategy_runner import RunContext, execute_strategy
from chaosprobe.placement.mutator import PlacementMutator
from chaosprobe.placement.strategy import PlacementStrategy
from chaosprobe.provisioner.setup import LitmusSetup


def _ensure_litmus_setup(
    namespace: str,
    experiment_types: list[str],
) -> bool:
    """Pre-flight check that all infrastructure is installed.

    Verifies that LitmusChaos, metrics-server, Prometheus, Neo4j, and
    ChaosCenter are available.  Does NOT install anything — run
    'chaosprobe init' first to set up the infrastructure.
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

    setup._init_k8s_client()
    prereqs = setup.check_prerequisites()

    if not prereqs["litmus_installed"]:
        click.echo(
            f"Error: LitmusChaos not installed. Run 'chaosprobe init -n {namespace}' first.",
            err=True,
        )
        return False

    click.echo("Setting up RBAC for namespace...")
    try:
        setup.setup_rbac(namespace)
        click.echo(f"  RBAC configured for namespace: {namespace}")
    except Exception as e:
        click.echo(f"Error setting up RBAC: {e}", err=True)
        return False

    for exp_type in set(experiment_types):
        click.echo(f"  Installing experiment: {exp_type}")
        if not setup.install_experiment(exp_type, namespace):
            click.echo(f"  WARNING: Failed to install experiment '{exp_type}'", err=True)

    # ── Pre-flight checks: verify infrastructure is available ──
    ok = True

    from concurrent.futures import ThreadPoolExecutor

    def _check_component(check_fn, name):
        return name, check_fn()

    checks = [
        (setup.is_local_path_provisioner_running, "local-path-provisioner"),
        (setup.is_metrics_server_installed, "metrics-server"),
        (setup.is_prometheus_installed, "Prometheus"),
        (setup.is_neo4j_installed, "Neo4j"),
        (setup.is_chaoscenter_installed, "ChaosCenter"),
    ]

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_check_component, fn, name) for fn, name in checks]
        for future in futures:
            name, available = future.result()
            if not available:
                click.echo(f"  {name}: NOT FOUND — run 'chaosprobe init' first", err=True)
                ok = False
            else:
                click.echo(f"  {name}: available")

    if not ok:
        click.echo(
            f"\nError: Missing infrastructure. Run 'chaosprobe init -n {namespace}' to install.",
            err=True,
        )
    return ok


# ------------------------------------------------------------------
# Helpers extracted from run() to keep the main command manageable
# ------------------------------------------------------------------

def _load_and_prepare_scenario(
    experiment: str,
    namespace: Optional[str],
) -> Tuple[dict, str, Path, Optional[List[dict]]]:
    """Load, validate, deploy manifests, and discover topology.

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

    # Auto-deploy application manifests from scenario's deploy/ directory
    deploy_dir = Path(shared_scenario["path"]) / "deploy"
    if deploy_dir.is_dir():
        import subprocess as _sp

        yamls = sorted(deploy_dir.glob("*.yaml")) + sorted(deploy_dir.glob("*.yml"))
        if yamls:
            click.echo(f"  Deploying {len(yamls)} manifest(s) from {deploy_dir.name}/...")
            _sp.run(
                ["kubectl", "create", "namespace", namespace],
                capture_output=True, text=True,
            )
            result = _sp.run(
                ["kubectl", "apply", "-f", str(deploy_dir), "-n", namespace],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                click.echo(f"  Warning: kubectl apply failed: {result.stderr.strip()}", err=True)
            else:
                applied = sum(1 for line in result.stdout.strip().split("\n") if line.strip())
                click.echo(f"  Applied {applied} resource(s) to {namespace}")

    # Discover service topology
    service_routes = parse_topology_from_scenario(shared_scenario) or None
    if service_routes:
        click.echo(
            f"  Topology:   {len(service_routes)} service dependencies"
            " discovered from manifests"
        )
    else:
        click.echo("  Topology:   no deploy/ directory found; service dependency graph empty")

    # Auto-build Rust cmdProbes if probes/ directory exists
    if shared_scenario.get("probes"):
        click.echo(f"\n  Found {len(shared_scenario['probes'])} Rust probe(s), building...")
        try:
            from chaosprobe.probes.builder import RustProbeBuilder, patch_probe_images

            builder = RustProbeBuilder(registry="chaosprobe", load_kind=True)
            built_images = builder.build_all(shared_scenario["path"])
            if built_images:
                n = patch_probe_images(shared_scenario["experiments"], built_images)
                click.echo(f"  Built and patched {n} cmdProbe image(s)")
        except Exception as e:
            click.echo(f"Warning: Rust probe build failed: {e}", err=True)
            click.echo("  Continuing without auto-built probes...", err=True)

    return shared_scenario, namespace, experiment_file, service_routes


def _clear_stale_placement(mutator: PlacementMutator, namespace: str) -> None:
    """Clear leftover nodeSelector constraints and rollout-restart app deployments."""
    from kubernetes import client as k8s_client_mod

    click.echo("Clearing stale placement constraints...")
    for _attempt in range(3):
        try:
            mutator.clear_placement(wait=True, timeout=120)
            break
        except Exception as _e:
            if _attempt < 2:
                click.echo(f"  Retry clearing placement ({_e})...", err=True)
                import time as _time
                _time.sleep(5)
            else:
                click.echo(f"  WARNING: could not clear placement ({_e})", err=True)

    # Ensure ALL app deployments use RollingUpdate before the restart
    # patch.  Previous runs leave deployments with Recreate strategy,
    # which kills all pods during a rollout restart.
    click.echo("Ensuring safe rollout strategy for all deployments...")
    try:
        _apps_api = k8s_client_mod.AppsV1Api()
        _all_deps = _apps_api.list_namespaced_deployment(namespace)
        for _dep in _all_deps.items:
            _name = _dep.metadata.name
            if _name in LITMUS_INFRA_DEPLOYMENTS:
                continue
            _strat = _dep.spec.strategy
            if _strat and _strat.type == "Recreate":
                _apps_api.patch_namespaced_deployment(
                    name=_name,
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
        _apps_api = k8s_client_mod.AppsV1Api()
        _all_deps = _apps_api.list_namespaced_deployment(namespace)
        _restart_names = [
            d.metadata.name
            for d in _all_deps.items
            if d.metadata.name not in LITMUS_INFRA_DEPLOYMENTS
        ]
        _now = datetime.now(timezone.utc).isoformat()
        for _dep_name in _restart_names:
            _apps_api.patch_namespaced_deployment(
                name=_dep_name,
                namespace=namespace,
                body={
                    "spec": {
                        "template": {
                            "metadata": {
                                "annotations": {
                                    "chaosprobe.io/restartedAt": _now,
                                }
                            }
                        }
                    }
                },
            )
        click.echo(f"  Triggered rollout restart for {len(_restart_names)} deployment(s)")
    except Exception as e:
        click.echo(f"  WARNING: rollout restart failed ({e})", err=True)


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
    click.echo(f"  Settle:     {settle_time}s between placement and experiment")
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
    "--namespace", "-n", default=None,
    help="Namespace containing the application (default: read from experiment YAML)",
)
@click.option(
    "--output-dir", "-o", default="results",
    help="Base directory for results (a timestamped subdirectory is created)",
)
@click.option(
    "--strategies", "-s", default="baseline,default,colocate,spread,antagonistic,random",
    help="Comma-separated strategies to test (default: all)",
)
@click.option("--timeout", "-t", default=300, type=int, help="Timeout per experiment in seconds")
@click.option("--seed", default=42, type=int, help="Seed for the random strategy")
@click.option(
    "--settle-time", default=30, type=int,
    help="Seconds to wait after placement before running experiment",
)
@click.option(
    "--experiment", "-e", default="scenarios/online-boutique/placement-experiment.yaml",
    help="Path to the placement experiment YAML file",
)
@click.option(
    "--iterations", "-i", default=1, type=int,
    help="Number of iterations per strategy (default: 1)",
)
@click.option("--provision", is_flag=True, help="Provision a fresh cluster from scenario cluster config before running")
@click.option(
    "--load-profile", type=click.Choice(["steady", "ramp", "spike"]), default="steady",
    help="Locust load profile during each experiment (default: steady)",
)
@click.option("--locustfile", type=click.Path(exists=True), default=None, help="Custom Locust file for load generation")
@click.option(
    "--target-url", default=None,
    help="Target URL for load generation (default: auto port-forward)",
)
@click.option("--visualize/--no-visualize", "do_visualize", default=True, show_default=True, help="Generate visualization charts after experiments complete")
@click.option("--measure-latency/--no-measure-latency", "measure_latency", default=True, show_default=True, help="Measure inter-service latency during each experiment")
@click.option("--measure-redis/--no-measure-redis", "measure_redis", default=True, show_default=True, help="Measure Redis throughput during each experiment")
@click.option("--measure-disk/--no-measure-disk", "measure_disk", default=True, show_default=True, help="Measure disk I/O throughput during each experiment")
@click.option("--measure-resources/--no-measure-resources", "measure_resources", default=True, show_default=True, help="Measure node/pod resource utilization during each experiment")
@click.option("--collect-logs/--no-collect-logs", "collect_logs", default=True, show_default=True, help="Collect container logs from target deployment after each experiment")
@click.option("--measure-prometheus/--no-measure-prometheus", "measure_prometheus", default=True, show_default=True, help="Query Prometheus for cluster metrics during each experiment")
@click.option("--prometheus-url", multiple=True, help="Prometheus server URL(s); repeat for multiple instances (auto-discovered if omitted)")
@click.option("--baseline-duration", type=int, default=0, help="Seconds to collect steady-state 'normal' metrics before chaos (default: 0 = use settle time)")
@click.option("--neo4j-uri", default="bolt://localhost:7687", envvar="NEO4J_URI", help="Neo4j connection URI (default: bolt://localhost:7687). Enables graph storage.")
@click.option("--neo4j-user", default="neo4j", envvar="NEO4J_USER", help="Neo4j username (default: neo4j)")
@click.option("--neo4j-password", default="chaosprobe", envvar="NEO4J_PASSWORD", help="Neo4j password (default: chaosprobe)")
def run(
    namespace: Optional[str],
    output_dir: Optional[str],
    strategies: str,
    timeout: int,
    seed: int,
    settle_time: int,
    experiment: str,
    iterations: int,
    provision: bool,
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

    Iterates through placement strategies (baseline, colocate, spread,
    antagonistic, random), applies each placement, runs the shared
    experiment, collects results (including pod recovery metrics), and
    saves everything to a timestamped results directory.

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

    if iterations < 1:
        click.echo("Error: --iterations must be >= 1", err=True)
        sys.exit(1)

    # Create output directory
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    results_dir = Path(output_dir) / ts
    results_dir.mkdir(parents=True, exist_ok=True)

    # Load scenario, deploy manifests, discover topology, build probes
    shared_scenario, namespace, experiment_file, service_routes = (
        _load_and_prepare_scenario(experiment, namespace)
    )

    # Optionally provision cluster from scenario's cluster config
    if provision:
        cluster_config = shared_scenario.get("cluster")
        if cluster_config:
            click.echo("\nProvisioning cluster from scenario config...")
            setup = LitmusSetup(skip_k8s_init=True)
            try:
                vagrant_dir = setup.provision_from_cluster_config(cluster_config)
                click.echo(f"  Cluster provisioned at {vagrant_dir}")
                click.echo("  Deploying Kubernetes on Vagrant VMs...")
                setup.vagrant_deploy_cluster(vagrant_dir)
                setup.vagrant_fetch_kubeconfig(vagrant_dir)
                click.echo("  Cluster ready.")
            except Exception as e:
                click.echo(f"Error provisioning cluster: {e}", err=True)
                sys.exit(1)
        else:
            click.echo("Warning: --provision specified but no cluster config in scenario", err=True)

    # Ensure LitmusChaos is ready once
    experiment_types = extract_experiment_types(shared_scenario)
    if not _ensure_litmus_setup(namespace, experiment_types):
        click.echo("Error: LitmusChaos setup failed", err=True)
        sys.exit(1)

    # Create reusable instances
    mutator = PlacementMutator(namespace)
    metrics_collector = MetricsCollector(namespace)

    _print_run_banner(
        namespace, experiment_file, strategy_list, iterations,
        results_dir, timeout, settle_time,
        measure_latency=measure_latency, measure_redis=measure_redis,
        measure_disk=measure_disk, measure_resources=measure_resources,
        measure_prometheus=measure_prometheus, prometheus_url=prometheus_url,
        collect_logs=collect_logs, baseline_duration=baseline_duration,
    )

    # ── Fresh-start: clear stale placement before pre-flight ──
    _clear_stale_placement(mutator, namespace)

    # ── Pre-flight checks ──────────────────────────────────────
    click.echo("\nPre-flight checks...")
    preflight = run_preflight_checks(
        namespace,
        measure_prometheus=measure_prometheus,
        prometheus_url=prometheus_url,
        neo4j_uri=neo4j_uri,
        load_profile=load_profile,
        target_url=target_url,
        timeout=timeout,
    )
    core_api = preflight["core_api"]
    chaoscenter_config = preflight["chaoscenter_config"]
    target_url = preflight["target_url"]
    frontend_pf_port = preflight["frontend_pf_port"]

    click.echo("")

    # Extract target deployment from experiment spec for recovery metrics
    target_deployment = extract_target_deployment(shared_scenario)

    overall_results: Dict[str, Any] = {
        "runId": f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "namespace": namespace,
        "iterations": iterations,
        "strategies": {},
    }

    total = len(strategy_list)
    passed = 0
    failed = 0

    # Neo4j graph store — primary data store
    graph_store = None
    if neo4j_uri:
        click.echo("Connecting to Neo4j graph store...")
        try:
            graph_store = init_graph_store(
                neo4j_uri, neo4j_user, neo4j_password,
                namespace, service_routes=service_routes,
            )
            click.echo("  Neo4j: connected and schema ready")
        except ImportError:
            click.echo(
                "Error: Neo4j driver not installed (install with: uv pip install chaosprobe[graph])",
                err=True,
            )
            sys.exit(1)
        except Exception as e:
            click.echo(f"Error: Neo4j connection failed ({e})", err=True)
            click.echo(
                "Neo4j is required as the primary data store. Check connection and retry.", err=True
            )
            sys.exit(1)

    # Build shared context for strategy execution
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
        shared_scenario=shared_scenario,
        service_routes=service_routes,
        target_deployment=target_deployment,
        core_api=core_api,
        chaoscenter_config=chaoscenter_config,
        frontend_pf_port=frontend_pf_port,
        metrics_collector=metrics_collector,
        mutator=mutator,
        graph_store=graph_store,
        ts=ts,
    )

    for idx, strategy_name in enumerate(strategy_list, 1):
        strategy_result, strategy_passed = execute_strategy(
            run_ctx, strategy_name, idx, total,
        )
        overall_results["strategies"][strategy_name] = strategy_result
        if strategy_result["status"] == "error":
            failed += 1
        elif strategy_passed:
            passed += 1
        else:
            failed += 1

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
