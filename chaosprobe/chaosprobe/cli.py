"""ChaosProbe CLI - Main entry point for the chaos testing framework."""

import click
from dotenv import find_dotenv, load_dotenv

from chaosprobe.commands.cleanup_cmd import cleanup
from chaosprobe.commands.cluster_cmd import cluster
from chaosprobe.commands.compare_cmd import compare
from chaosprobe.commands.dashboard_cmd import dashboard
from chaosprobe.commands.delete_cmd import delete
from chaosprobe.commands.diff_cmd import diff
from chaosprobe.commands.doctor_cmd import doctor
from chaosprobe.commands.export_cmd import export
from chaosprobe.commands.graph_cmd import graph
from chaosprobe.commands.init_cmd import init
from chaosprobe.commands.inspect_cmd import inspect
from chaosprobe.commands.placement_cmd import placement
from chaosprobe.commands.power_cmd import power
from chaosprobe.commands.probe_cmd import probe
from chaosprobe.commands.provision_cmd import provision
from chaosprobe.commands.recommend_cmd import recommend
from chaosprobe.commands.report_cmd import report
from chaosprobe.commands.run_cmd import run
from chaosprobe.commands.stats_cmd import stats
from chaosprobe.commands.status_cmd import status
from chaosprobe.commands.summarize_cmd import summarize
from chaosprobe.commands.visualize_cmd import ml_export, visualize


@click.group()
@click.version_option()
def main():
    """ChaosProbe - Kubernetes chaos testing framework with AI-consumable output.

    Deploys Kubernetes manifests, runs native LitmusChaos experiments,
    Scenarios are directories containing K8s manifests and ChaosEngine YAML.
    """
    # Load .env from CWD or any parent directory. Shell-exported vars win.
    load_dotenv(find_dotenv(usecwd=True), override=False)


main.add_command(cleanup)
main.add_command(cluster)
main.add_command(compare)
main.add_command(dashboard)
main.add_command(delete)
main.add_command(diff)
main.add_command(doctor)
main.add_command(export)
main.add_command(graph)
main.add_command(init)
main.add_command(inspect)
main.add_command(placement)
main.add_command(power)
main.add_command(probe)
main.add_command(provision)
main.add_command(recommend)
main.add_command(report)
main.add_command(run)
main.add_command(stats)
main.add_command(status)
main.add_command(summarize)
main.add_command(visualize)
main.add_command(ml_export)


if __name__ == "__main__":
    main()
