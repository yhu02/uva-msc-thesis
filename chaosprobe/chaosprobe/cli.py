"""ChaosProbe CLI - Main entry point for the chaos testing framework."""

import json
import sys
from pathlib import Path
from typing import Optional

import click

from chaosprobe.config.loader import load_scenario
from chaosprobe.config.validator import validate_scenario
from chaosprobe.provisioner.kubernetes import KubernetesProvisioner
from chaosprobe.provisioner.setup import LitmusSetup
from chaosprobe.chaos.runner import ChaosRunner
from chaosprobe.collector.result_collector import ResultCollector
from chaosprobe.output.generator import OutputGenerator
from chaosprobe.output.comparison import compare_runs


def ensure_litmus_setup(namespace: str, experiments: list, auto_setup: bool = True) -> bool:
    """Ensure LitmusChaos is installed and configured.

    Args:
        namespace: Target namespace for experiments.
        experiments: List of experiment configurations.
        auto_setup: Whether to automatically install if missing.

    Returns:
        True if setup is ready.
    """
    setup = LitmusSetup()
    prereqs = setup.check_prerequisites()

    if not prereqs["kubectl"]:
        click.echo("Error: kubectl not found. Please install kubectl.", err=True)
        return False

    if not prereqs["cluster_access"]:
        click.echo("Error: Cannot access Kubernetes cluster. Check your kubeconfig.", err=True)
        return False

    if not prereqs["litmus_installed"]:
        if not auto_setup:
            click.echo("Error: LitmusChaos not installed. Run 'chaosprobe init' first.", err=True)
            return False

        if not prereqs["helm"]:
            click.echo("Error: helm not found. Please install helm for auto-setup.", err=True)
            return False

        click.echo("LitmusChaos not found. Installing automatically...")
        try:
            setup.install_litmus(wait=True)
            click.echo("  LitmusChaos installed successfully")
        except Exception as e:
            click.echo(f"Error installing LitmusChaos: {e}", err=True)
            return False

    click.echo("Setting up RBAC for namespace...")
    try:
        setup.setup_rbac(namespace)
        click.echo(f"  RBAC configured for namespace: {namespace}")
    except Exception as e:
        click.echo(f"Error setting up RBAC: {e}", err=True)
        return False

    exp_types = list(set(exp.get("type") for exp in experiments))
    for exp_type in exp_types:
        click.echo(f"  Installing experiment: {exp_type}")
        setup.install_experiment(exp_type, namespace)

    return True


@click.group()
@click.version_option()
def main():
    """ChaosProbe - Kubernetes chaos testing framework with AI-consumable output.

    Automatically provisions infrastructure, installs LitmusChaos, runs chaos
    experiments, and generates AI-consumable output for analysis.
    """
    pass


@main.command()
@click.option("--namespace", "-n", default="chaosprobe-test", help="Namespace for chaos experiments")
@click.option("--skip-litmus", is_flag=True, help="Skip LitmusChaos installation")
def init(namespace: str, skip_litmus: bool):
    """Initialize ChaosProbe and install LitmusChaos.

    This command sets up all prerequisites for running chaos experiments:
    - Installs LitmusChaos via Helm (if not already installed)
    - Creates RBAC configuration
    - Verifies cluster connectivity
    """
    click.echo("Initializing ChaosProbe...")

    setup = LitmusSetup()
    prereqs = setup.check_prerequisites()

    click.echo("\nChecking prerequisites:")
    click.echo(f"  kubectl: {'OK' if prereqs['kubectl'] else 'MISSING'}")
    click.echo(f"  helm: {'OK' if prereqs['helm'] else 'MISSING'}")
    click.echo(f"  Cluster access: {'OK' if prereqs['cluster_access'] else 'FAILED'}")
    click.echo(f"  LitmusChaos: {'Installed' if prereqs['litmus_installed'] else 'Not installed'}")

    if not prereqs["kubectl"]:
        click.echo("\nError: kubectl is required. Please install it first.", err=True)
        sys.exit(1)

    if not prereqs["cluster_access"]:
        click.echo("\nError: Cannot access Kubernetes cluster.", err=True)
        click.echo("Make sure your kubeconfig is properly configured.", err=True)
        sys.exit(1)

    if not skip_litmus and not prereqs["litmus_installed"]:
        if not prereqs["helm"]:
            click.echo("\nError: helm is required to install LitmusChaos.", err=True)
            click.echo("Install helm or use --skip-litmus if Litmus is already installed.", err=True)
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

    click.echo("\nChaosProbe initialized successfully!")
    click.echo(f"\nYou can now run scenarios with:")
    click.echo(f"  chaosprobe run <scenario.yaml> --output results.json")


@main.command()
@click.option("--json", "json_output", is_flag=True, help="Output status as JSON")
def status(json_output: bool):
    """Check the status of ChaosProbe and its dependencies."""
    setup = LitmusSetup()
    prereqs = setup.check_prerequisites()

    if json_output:
        click.echo(json.dumps(prereqs, indent=2))
        return

    click.echo("ChaosProbe Status:")
    click.echo(f"  kubectl: {'OK' if prereqs['kubectl'] else 'MISSING'}")
    click.echo(f"  helm: {'OK' if prereqs['helm'] else 'MISSING'}")
    click.echo(f"  Cluster access: {'OK' if prereqs['cluster_access'] else 'FAILED'}")
    click.echo(f"  LitmusChaos installed: {'Yes' if prereqs['litmus_installed'] else 'No'}")
    click.echo(f"  LitmusChaos ready: {'Yes' if prereqs['litmus_ready'] else 'No'}")

    if prereqs["all_ready"]:
        click.echo("\nAll systems ready!")
    else:
        click.echo("\nRun 'chaosprobe init' to complete setup.")


@main.command()
@click.argument("scenario_file", type=click.Path(exists=True))
@click.option("--output", "-o", type=click.Path(), help="Output file for results JSON")
@click.option("--with-anomaly/--without-anomaly", default=True, help="Run with or without anomaly")
@click.option("--namespace", "-n", default=None, help="Override namespace from scenario")
@click.option("--timeout", "-t", default=300, help="Timeout in seconds for experiment completion")
@click.option("--dry-run", is_flag=True, help="Print manifests without applying")
@click.option("--no-auto-setup", is_flag=True, help="Disable automatic LitmusChaos installation")
def run(
    scenario_file: str,
    output: Optional[str],
    with_anomaly: bool,
    namespace: Optional[str],
    timeout: int,
    dry_run: bool,
    no_auto_setup: bool,
):
    """Run a chaos scenario and generate AI-consumable output.

    SCENARIO_FILE: Path to the scenario YAML configuration file.

    This command automatically:
    - Installs LitmusChaos if not present
    - Provisions the infrastructure defined in the scenario
    - Runs chaos experiments
    - Collects results and generates AI-consumable output
    """
    click.echo(f"Loading scenario from {scenario_file}...")

    try:
        scenario = load_scenario(scenario_file)
        validate_scenario(scenario)
    except Exception as e:
        click.echo(f"Error loading scenario: {e}", err=True)
        sys.exit(1)

    if namespace:
        scenario["spec"]["infrastructure"]["namespace"] = namespace

    target_namespace = scenario["spec"]["infrastructure"]["namespace"]
    experiments = scenario["spec"].get("experiments", [])

    if dry_run:
        click.echo("Dry run mode - printing manifests...")
        provisioner = KubernetesProvisioner(scenario, with_anomaly=with_anomaly)
        manifests = provisioner.generate_manifests()
        for manifest in manifests:
            click.echo("---")
            click.echo(manifest)
        return

    # Phase 0: Ensure LitmusChaos is set up
    click.echo("\n[0/4] Checking prerequisites...")
    if not ensure_litmus_setup(target_namespace, experiments, auto_setup=not no_auto_setup):
        sys.exit(1)

    click.echo(f"\nRunning scenario: {scenario['metadata']['name']}")
    click.echo(f"  With anomaly: {with_anomaly}")
    click.echo(f"  Namespace: {target_namespace}")

    # Phase 1: Provision infrastructure
    click.echo("\n[1/4] Provisioning infrastructure...")
    provisioner = KubernetesProvisioner(scenario, with_anomaly=with_anomaly)
    try:
        provisioner.provision()
        click.echo("  Infrastructure provisioned successfully")
    except Exception as e:
        click.echo(f"  Error provisioning infrastructure: {e}", err=True)
        sys.exit(1)

    # Phase 2: Run chaos experiments
    click.echo("\n[2/4] Running chaos experiments...")
    runner = ChaosRunner(scenario, timeout=timeout)
    try:
        runner.run_experiments()
        click.echo("  Experiments completed")
    except Exception as e:
        click.echo(f"  Error running experiments: {e}", err=True)
        # Continue to collect results even if experiments fail

    # Phase 3: Collect results
    click.echo("\n[3/4] Collecting results...")
    collector = ResultCollector(scenario)
    try:
        results = collector.collect()
        click.echo(f"  Collected results from {len(results)} experiments")
    except Exception as e:
        click.echo(f"  Error collecting results: {e}", err=True)
        sys.exit(1)

    # Phase 4: Generate output
    click.echo("\n[4/4] Generating AI output...")
    generator = OutputGenerator(scenario, results, with_anomaly=with_anomaly)
    output_data = generator.generate()

    if output:
        output_path = Path(output)
        output_path.write_text(json.dumps(output_data, indent=2))
        click.echo(f"  Output written to {output}")
    else:
        click.echo(json.dumps(output_data, indent=2))

    # Print summary
    click.echo(f"\n{'='*50}")
    click.echo("Summary:")
    click.echo(f"  Verdict: {output_data['summary']['overallVerdict']}")
    click.echo(f"  Resilience Score: {output_data['summary']['resilienceScore']:.1f}")
    click.echo(f"  Experiments: {output_data['summary']['passed']}/{output_data['summary']['totalExperiments']} passed")


@main.command()
@click.argument("scenario_file", type=click.Path(exists=True))
@click.option("--namespace", "-n", default=None, help="Override namespace from scenario")
@click.option("--with-anomaly/--without-anomaly", default=True, help="Provision with or without anomaly")
@click.option("--dry-run", is_flag=True, help="Print manifests without applying")
def provision(
    scenario_file: str,
    namespace: Optional[str],
    with_anomaly: bool,
    dry_run: bool,
):
    """Provision infrastructure from a scenario without running experiments.

    SCENARIO_FILE: Path to the scenario YAML configuration file.
    """
    click.echo(f"Loading scenario from {scenario_file}...")

    try:
        scenario = load_scenario(scenario_file)
        validate_scenario(scenario)
    except Exception as e:
        click.echo(f"Error loading scenario: {e}", err=True)
        sys.exit(1)

    if namespace:
        scenario["spec"]["infrastructure"]["namespace"] = namespace

    provisioner = KubernetesProvisioner(scenario, with_anomaly=with_anomaly)

    if dry_run:
        click.echo("Dry run mode - printing manifests...")
        manifests = provisioner.generate_manifests()
        for manifest in manifests:
            click.echo("---")
            click.echo(manifest)
        return

    click.echo(f"Provisioning infrastructure...")
    click.echo(f"  With anomaly: {with_anomaly}")
    click.echo(f"  Namespace: {scenario['spec']['infrastructure']['namespace']}")

    try:
        provisioner.provision()
        click.echo("Infrastructure provisioned successfully")
    except Exception as e:
        click.echo(f"Error provisioning infrastructure: {e}", err=True)
        sys.exit(1)


@main.command()
@click.argument("baseline_file", type=click.Path(exists=True))
@click.argument("afterfix_file", type=click.Path(exists=True))
@click.option("--output", "-o", type=click.Path(), help="Output file for comparison JSON")
def compare(baseline_file: str, afterfix_file: str, output: Optional[str]):
    """Compare baseline results with after-fix results.

    BASELINE_FILE: Path to the baseline results JSON file.
    AFTERFIX_FILE: Path to the after-fix results JSON file.
    """
    click.echo(f"Comparing {baseline_file} with {afterfix_file}...")

    try:
        baseline_data = json.loads(Path(baseline_file).read_text())
        afterfix_data = json.loads(Path(afterfix_file).read_text())
    except Exception as e:
        click.echo(f"Error loading result files: {e}", err=True)
        sys.exit(1)

    comparison = compare_runs(baseline_data, afterfix_data)

    if output:
        output_path = Path(output)
        output_path.write_text(json.dumps(comparison, indent=2))
        click.echo(f"Comparison written to {output}")
    else:
        click.echo(json.dumps(comparison, indent=2))

    # Print summary
    click.echo(f"\n{'='*50}")
    click.echo("Comparison Summary:")
    click.echo(f"  Fix Effective: {comparison['conclusion']['fixEffective']}")
    click.echo(f"  Confidence: {comparison['conclusion']['confidence']:.2f}")
    click.echo(f"  Resilience Score Change: {comparison['comparison']['resilienceScoreChange']:+.1f}")


@main.command()
@click.argument("namespace")
@click.option("--scenario", "-s", type=click.Path(exists=True), help="Scenario file to cleanup resources for")
@click.option("--all", "cleanup_all", is_flag=True, help="Cleanup all chaosprobe resources in namespace")
def cleanup(namespace: str, scenario: Optional[str], cleanup_all: bool):
    """Cleanup provisioned resources in a namespace.

    NAMESPACE: The Kubernetes namespace to cleanup.
    """
    click.echo(f"Cleaning up resources in namespace: {namespace}")

    if scenario:
        try:
            scenario_data = load_scenario(scenario)
            provisioner = KubernetesProvisioner(scenario_data, with_anomaly=False)
            provisioner.cleanup()
            click.echo("Scenario resources cleaned up successfully")
        except Exception as e:
            click.echo(f"Error cleaning up: {e}", err=True)
            sys.exit(1)
    elif cleanup_all:
        provisioner = KubernetesProvisioner(
            {"spec": {"infrastructure": {"namespace": namespace, "resources": []}}},
            with_anomaly=False
        )
        provisioner.cleanup_namespace()
        click.echo("All resources cleaned up successfully")
    else:
        click.echo("Please specify --scenario or --all", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
