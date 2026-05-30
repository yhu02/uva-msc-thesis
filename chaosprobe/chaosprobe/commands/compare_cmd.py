"""CLI command: chaosprobe compare — diff baseline vs after-fix run results."""

import json
import sys
from pathlib import Path
from typing import Optional

import click

from chaosprobe.commands.shared import (
    get_graph_store,
    neo4j_password_option,
    neo4j_uri_option,
    neo4j_user_option,
)
from chaosprobe.output.comparison import compare_runs


@click.command()
@click.argument("baseline", type=str)
@click.argument("afterfix", type=str)
@click.option("--output", "-o", type=click.Path(), help="Output file for comparison JSON")
@neo4j_uri_option
@neo4j_user_option
@neo4j_password_option
def compare(
    baseline: str,
    afterfix: str,
    output: Optional[str],
    neo4j_uri: Optional[str],
    neo4j_user: str,
    neo4j_password: str,
):
    """Compare baseline results with after-fix results.

    BASELINE: Run ID (Neo4j) or path to baseline results JSON file.
    AFTERFIX: Run ID (Neo4j) or path to after-fix results JSON file.

    \b
    Examples:
      chaosprobe compare run-2026-04-02-1234 run-2026-04-02-5678 --neo4j-uri bolt://localhost:7687
      chaosprobe compare baseline.json afterfix.json  # legacy JSON file mode
    """
    # Auto-detect file mode: if both arguments look like file paths, use JSON files
    baseline_is_file = Path(baseline).exists()
    afterfix_is_file = Path(afterfix).exists()

    if baseline_is_file and afterfix_is_file:
        click.echo(f"Comparing JSON files: {baseline} vs {afterfix}...")
        try:
            baseline_data = json.loads(Path(baseline).read_text())
            afterfix_data = json.loads(Path(afterfix).read_text())
        except Exception as e:
            click.echo(f"Error loading result files: {e}", err=True)
            sys.exit(1)
    elif neo4j_uri:
        click.echo(f"Comparing runs from Neo4j: {baseline} vs {afterfix}...")
        store = get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
        try:
            baseline_data = store.get_run_output(baseline)
            afterfix_data = store.get_run_output(afterfix)
        finally:
            store.close()
        if not baseline_data:
            click.echo(f"Error: run '{baseline}' not found in Neo4j", err=True)
            sys.exit(1)
        if not afterfix_data:
            click.echo(f"Error: run '{afterfix}' not found in Neo4j", err=True)
            sys.exit(1)
    else:
        click.echo(
            "Error: arguments are not existing files and no --neo4j-uri provided",
            err=True,
        )
        sys.exit(1)

    comparison = compare_runs(baseline_data, afterfix_data)

    if output:
        output_path = Path(output)
        output_path.write_text(json.dumps(comparison, indent=2))
        click.echo(f"Comparison written to {output}")
    else:
        click.echo(json.dumps(comparison, indent=2))

    click.echo(f"\n{'=' * 50}")
    click.echo("Comparison Summary:")
    click.echo(f"  Fix Effective: {comparison['conclusion']['fixEffective']}")
    click.echo(f"  Confidence: {comparison['conclusion']['confidence']:.2f}")
    click.echo(
        f"  Resilience Score Change: " f"{comparison['comparison']['resilienceScoreChange']:+.1f}"
    )
