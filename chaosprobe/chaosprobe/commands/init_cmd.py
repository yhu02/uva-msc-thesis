"""CLI command: chaosprobe init — install all infrastructure on a Kubernetes cluster."""

import sys

import click

from chaosprobe.orchestrator import portforward as pf
from chaosprobe.orchestrator.preflight import check_pods_ready
from chaosprobe.provisioner.setup import LitmusSetup


def _pf_ensure(svc: str, ns: str, ports: list[str], host: str, port: int) -> bool:
    return pf.ensure(svc, ns, ports, host, port)


@click.command()
@click.option(
    "--namespace",
    "-n",
    default="chaosprobe-test",
    help="Namespace for chaos experiments",
)
@click.option("--skip-litmus", is_flag=True, help="Skip LitmusChaos installation")
@click.option(
    "--skip-dashboard",
    is_flag=True,
    help="Skip ChaosCenter dashboard installation",
)
def init(namespace: str, skip_litmus: bool, skip_dashboard: bool):
    """Initialize ChaosProbe and install all infrastructure on existing cluster.

    This command sets up all prerequisites for running chaos experiments:
    - Installs Helm and LitmusChaos automatically
    - Creates RBAC configuration
    - Installs metrics-server, Prometheus, and Neo4j
    - Installs the ChaosCenter dashboard by default (disable with --skip-dashboard)
    - Registers the target namespace as ChaosCenter infrastructure

    Requires an existing Kubernetes cluster. Options:
    - Use 'chaosprobe cluster vagrant init/up/deploy' for local development
    - Use 'chaosprobe cluster create' for bare metal/cloud VMs with Kubespray
    """
    click.echo("Initializing ChaosProbe...")

    setup = LitmusSetup(skip_k8s_init=True)
    prereqs = setup.check_prerequisites()

    click.echo("\nChecking prerequisites:")
    click.echo(f"  kubectl: {'OK' if prereqs['kubectl'] else 'MISSING'}")
    click.echo(f"  helm: {'OK' if prereqs['helm'] else 'MISSING'}")
    click.echo(f"  git: {'OK' if prereqs['git'] else 'MISSING'}")
    click.echo(f"  ssh: {'OK' if prereqs['ssh'] else 'MISSING'}")
    click.echo(f"  ansible: {'OK' if prereqs['ansible'] else 'Not installed (optional)'}")

    if not prereqs["kubectl"]:
        click.echo("\nError: kubectl is required. Please install it first.", err=True)
        sys.exit(1)

    click.echo("\nValidating cluster...")
    is_valid, message = setup.validate_cluster()
    if not is_valid:
        click.echo(f"  Error: {message}", err=True)
        click.echo("\nNo cluster configured. Options:")
        click.echo(
            "  1. Use 'chaosprobe cluster vagrant up' to start a local libvirt/Vagrant cluster"
        )
        click.echo("  2. Use 'chaosprobe cluster create' to deploy with Kubespray")
        click.echo("  3. Configure kubectl to connect to an existing cluster")
        sys.exit(1)
    click.echo(f"  {message}")

    setup._init_k8s_client()
    prereqs = setup.check_prerequisites()

    click.echo(f"  Cluster access: {'OK' if prereqs['cluster_access'] else 'No cluster'}")
    click.echo(f"  LitmusChaos: {'Installed' if prereqs['litmus_installed'] else 'Not installed'}")

    # Ensure local-path-provisioner is running (needed for PVCs)
    click.echo("\nEnsuring local-path-provisioner...")
    if setup.is_local_path_provisioner_running():
        click.echo("  local-path-provisioner: already running")
    else:
        click.echo("  local-path-provisioner not found, installing...")
        if setup.ensure_local_path_provisioner():
            click.echo("  local-path-provisioner: running")
        else:
            click.echo("  WARNING: local-path-provisioner may not be ready yet", err=True)

    if not skip_litmus and not prereqs["litmus_installed"]:
        if not prereqs["helm"]:
            click.echo("\nHelm not found. Installing automatically...")
            try:
                setup.ensure_helm()
                click.echo("  Helm installed successfully")
            except Exception as e:
                click.echo(f"  Error installing helm: {e}", err=True)
                sys.exit(1)

        click.echo("\nInstalling LitmusChaos...")
        try:
            setup.install_litmus(wait=True)
            click.echo("  LitmusChaos installed successfully")
        except Exception as e:
            click.echo(f"  Error: {e}", err=True)
            sys.exit(1)

    click.echo(f"\nSetting up RBAC for namespace: {namespace}")
    try:
        setup.setup_rbac(namespace)
        click.echo("  RBAC configured successfully")
    except Exception as e:
        click.echo(f"  Error: {e}", err=True)
        sys.exit(1)

    # ── Install infrastructure components (parallel) ──

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _deployment_has_pvc(name: str, ns: str) -> bool:
        """Check if a deployment has any PVC volume."""
        try:
            dep = setup.apps_api.read_namespaced_deployment(name, ns)
            volumes = dep.spec.template.spec.volumes or []
            return any(v.persistent_volume_claim is not None for v in volumes)
        except Exception:
            return True  # can't check — assume OK

    def _install_metrics_server():
        if setup.is_metrics_server_installed():
            # Verify --kubelet-insecure-tls is present
            try:
                dep = setup.apps_api.read_namespaced_deployment(
                    "metrics-server", "kube-system"
                )
                containers = dep.spec.template.spec.containers or []
                args = containers[0].args or [] if containers else []
                if "--kubelet-insecure-tls" not in args:
                    setup.install_metrics_server(wait=True)
                    return "metrics-server", "repaired (added --kubelet-insecure-tls)"
            except Exception:
                pass
            return "metrics-server", "already installed"
        if setup.install_metrics_server(wait=True):
            return "metrics-server", "installed"
        return "metrics-server", "installed but not yet ready"

    def _install_prometheus():
        if setup.is_prometheus_installed():
            if not _deployment_has_pvc("prometheus-server", "monitoring"):
                setup.install_prometheus(wait=True)
                return "Prometheus", "repaired (added persistent storage)"
            return "Prometheus", "already installed"
        if setup.install_prometheus(wait=True):
            return "Prometheus", "installed"
        return "Prometheus", "installed but not yet ready"

    def _install_neo4j():
        if setup.is_neo4j_installed():
            if not _deployment_has_pvc("neo4j", "neo4j"):
                setup.install_neo4j(wait=True)
                return "Neo4j", "repaired (added persistent storage)"
            return "Neo4j", "already installed"
        if setup.install_neo4j(wait=True):
            return "Neo4j", "installed"
        return "Neo4j", "installed but not yet ready"

    click.echo("\nInstalling infrastructure components (parallel)...")
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_install_metrics_server): "metrics-server",
            executor.submit(_install_prometheus): "Prometheus",
            executor.submit(_install_neo4j): "Neo4j",
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                name, status = future.result()
                click.echo(f"  {name}: {status}")
            except Exception as e:
                click.echo(f"  WARNING: Failed to install {label}: {e}", err=True)

    click.echo("\nChaosProbe initialized successfully!")

    if not skip_dashboard:
        if not setup.is_chaoscenter_installed():
            click.echo("\nInstalling ChaosCenter dashboard...")
            try:
                ok = setup.install_chaoscenter(wait=True)
                if ok:
                    click.echo("  ChaosCenter installed successfully!")
                    url = setup.get_dashboard_url()
                    if url:
                        click.echo(f"  Dashboard URL: {url}")
                    click.echo(
                        f"  Default credentials: "
                        f"username={setup.CHAOSCENTER_DEFAULT_USER} "
                        f"password={setup.CHAOSCENTER_DEFAULT_PASS}"
                    )
                    click.echo(f"\n  Connecting namespace '{namespace}' to ChaosCenter...")
                    try:
                        _cc_auth_svc = LitmusSetup.CHAOSCENTER_AUTH_SVC
                        _cc_auth_port = LitmusSetup.CHAOSCENTER_AUTH_PORT
                        _cc_server_svc = LitmusSetup.CHAOSCENTER_SERVER_SVC
                        _cc_server_port = LitmusSetup.CHAOSCENTER_SERVER_PORT
                        if not _pf_ensure(
                            _cc_auth_svc, "litmus",
                            [f"{_cc_auth_port}:{_cc_auth_port}"],
                            "localhost", _cc_auth_port,
                        ):
                            raise RuntimeError(
                                f"Port-forward to auth server (:{_cc_auth_port}) not reachable"
                            )
                        if not _pf_ensure(
                            _cc_server_svc, "litmus",
                            [f"{_cc_server_port}:{_cc_server_port}"],
                            "localhost", _cc_server_port,
                        ):
                            raise RuntimeError(
                                f"Port-forward to GraphQL server (:{_cc_server_port}) not reachable"
                            )
                        setup.ensure_chaoscenter_configured(
                            namespace=namespace,
                            base_host="http://localhost",
                        )
                        click.echo("  Infrastructure registered successfully!")
                    except Exception as e:
                        click.echo(f"  Warning: Could not register infrastructure: {e}")
                else:
                    click.echo("  ChaosCenter installation timed out.", err=True)
            except Exception as e:
                click.echo(f"  Error installing ChaosCenter: {e}", err=True)
        else:
            click.echo("\nChaosCenter is already installed.")
            url = setup.get_dashboard_url()
            if url:
                click.echo(f"  Dashboard URL: {url}")

    # ── Establish persistent port-forwards for infrastructure ──
    click.echo("\nSetting up port-forwards for infrastructure services...")

    # Prometheus
    prom_forwarded = False
    for ns in ("monitoring", "prometheus", "kube-prometheus"):
        for label in ("app=prometheus,component=server", "app.kubernetes.io/name=prometheus"):
            if check_pods_ready(ns, label):
                if not pf.check_port("localhost", 9090):
                    pf.start("prometheus-server", ns, ["9090:9090"])
                if pf.check_port("localhost", 9090):
                    click.echo("  Prometheus:    localhost:9090")
                else:
                    click.echo("  Prometheus:    WARNING - port-forward failed", err=True)
                prom_forwarded = True
                break
        if prom_forwarded:
            break
    if not prom_forwarded:
        click.echo("  Prometheus:    WARNING - no ready pods found", err=True)

    # Neo4j
    if check_pods_ready("neo4j", "app=neo4j"):
        if not pf.check_port("localhost", 7687):
            pf.ensure("neo4j", "neo4j", ["7687:7687", "7474:7474"], "localhost", 7687)
        if pf.check_port("localhost", 7687):
            click.echo("  Neo4j:         localhost:7687")
        else:
            click.echo("  Neo4j:         WARNING - port-forward failed", err=True)
    else:
        click.echo("  Neo4j:         WARNING - no ready pods found", err=True)

    # ChaosCenter
    if not skip_dashboard:
        _cc_f_svc = LitmusSetup.CHAOSCENTER_FRONTEND_SVC
        _cc_f_port = LitmusSetup.CHAOSCENTER_FRONTEND_PORT
        _cc_a_svc = LitmusSetup.CHAOSCENTER_AUTH_SVC
        _cc_a_port = LitmusSetup.CHAOSCENTER_AUTH_PORT
        _cc_s_svc = LitmusSetup.CHAOSCENTER_SERVER_SVC
        _cc_s_port = LitmusSetup.CHAOSCENTER_SERVER_PORT

        if not pf.check_port("localhost", _cc_f_port):
            pf.start(_cc_f_svc, "litmus", [f"{_cc_f_port}:{_cc_f_port}"])
        if not pf.check_port("localhost", _cc_a_port):
            pf.start(_cc_a_svc, "litmus", [f"{_cc_a_port}:{_cc_a_port}"])
        if not pf.check_port("localhost", _cc_s_port):
            pf.start(_cc_s_svc, "litmus", [f"{_cc_s_port}:{_cc_s_port}"])

        if pf.check_port("localhost", _cc_f_port):
            click.echo(f"  ChaosCenter:   http://localhost:{_cc_f_port}")
        else:
            click.echo(f"  ChaosCenter:   WARNING - frontend port-forward failed", err=True)

    # Load target (frontend application service)
    try:
        from kubernetes import client as k8s_client_mod

        svc_list = k8s_client_mod.CoreV1Api().list_namespaced_service(namespace)
        frontend_svc = None
        for svc in svc_list.items:
            if "frontend" in svc.metadata.name and "external" not in svc.metadata.name:
                frontend_svc = svc.metadata.name
                break
        if frontend_svc:
            _load_port = 8089
            if not pf.check_port("localhost", _load_port):
                pf.ensure(frontend_svc, namespace, [f"{_load_port}:80"], "localhost", _load_port)
            if pf.check_port("localhost", _load_port):
                click.echo(f"  Load target:   http://localhost:{_load_port} ({frontend_svc})")
            else:
                click.echo(f"  Load target:   WARNING - port-forward to {frontend_svc} failed", err=True)
        else:
            click.echo("  Load target:   no frontend service found in namespace (will need --target-url)")
    except Exception:
        click.echo("  Load target:   skipped (namespace services not available yet)")

    click.echo("\nPort-forwards are running in the background.")
    click.echo("You can now run scenarios with:")
    click.echo("  chaosprobe run <scenario-dir> --output-dir results/")
