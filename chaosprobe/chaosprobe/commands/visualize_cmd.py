"""CLI commands for visualization and ML dataset export."""

import sys
from typing import Optional

import click

from chaosprobe.commands.shared import (
    get_graph_store,
    neo4j_password_option,
    neo4j_uri_option,
    neo4j_user_option,
)


@click.command("visualize")
@click.option(
    "--summary",
    "-s",
    type=click.Path(exists=True),
    default=None,
    help="Path to a summary.json file",
)
@click.option(
    "--output-dir",
    "-o",
    default="charts",
    help="Directory to save generated charts",
)
@click.option(
    "--session",
    default=None,
    help="Session ID to visualize (Neo4j mode)",
)
@neo4j_uri_option
@neo4j_user_option
@neo4j_password_option
def visualize(
    summary: Optional[str],
    output_dir: str,
    session: Optional[str],
    neo4j_uri: Optional[str],
    neo4j_user: str,
    neo4j_password: str,
):
    """Generate visualization charts from experiment results.

    Can read from Neo4j graph database or a summary.json file.

    \b
    Examples:
      chaosprobe visualize --neo4j-uri bolt://localhost:7687 --session 20260402-013423
      chaosprobe visualize --summary results/20260227-140237/summary.json
    """
    from chaosprobe.output.visualize import (
        generate_from_dict,
        generate_from_summary,
    )

    if neo4j_uri:
        store = get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
        try:
            if not session:
                # Pick the most recent session
                sessions = store.list_sessions()
                if not sessions:
                    click.echo("No sessions found in Neo4j.", err=True)
                    return
                session = sessions[0]["session_id"]
                click.echo(f"Using most recent session: {session}")
            click.echo(f"Generating charts from Neo4j (session={session})...")
            summary_data = store.get_session_visualization_data(session)
            generated = generate_from_dict(summary_data, output_dir)
        except ImportError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        finally:
            store.close()
    elif summary:
        click.echo(f"Generating charts from {summary}...")
        try:
            generated = generate_from_summary(summary, output_dir)
        except ImportError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
    else:
        click.echo("Error: specify --neo4j-uri or --summary", err=True)
        sys.exit(1)

    if not generated:
        click.echo("No data available to visualize.")
        return

    click.echo(f"\nGenerated {len(generated)} file(s):")
    for path in generated:
        click.echo(f"  {path}")

    html_files = [p for p in generated if p.endswith(".html")]
    if html_files:
        click.echo(f"\nOpen the report: {html_files[0]}")


@click.command("ml-export")
@click.option(
    "--neo4j-uri",
    default=None,
    envvar="NEO4J_URI",
    help="Neo4j connection URI (export directly from graph)",
)
@click.option(
    "--neo4j-user",
    default="neo4j",
    envvar="NEO4J_USER",
    help="Neo4j username",
)
@click.option(
    "--neo4j-password",
    default="chaosprobe",
    envvar="NEO4J_PASSWORD",
    help="Neo4j password",
)
@click.option(
    "--output",
    "-o",
    required=True,
    type=click.Path(),
    help="Output file path (e.g. dataset.csv or dataset.parquet)",
)
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["csv", "parquet"]),
    default="csv",
    show_default=True,
    help="Output format",
)
@click.option(
    "--strategy",
    default=None,
    help="Filter by strategy name",
)
def ml_export(
    neo4j_uri: Optional[str],
    neo4j_user: str,
    neo4j_password: str,
    output: str,
    fmt: str,
    strategy: Optional[str],
):
    """Export ML-ready aligned time-series dataset.

    Reads experiment results from Neo4j and produces a feature matrix
    where each row is a time bucket with all metric columns aligned and
    labeled with the ground-truth anomaly type.

    \b
    Examples:
      chaosprobe ml-export --neo4j-uri bolt://localhost:7687 -o dataset.csv
    """
    from chaosprobe.output.ml_export import (
        export_from_neo4j,
        write_dataset,
    )

    if not neo4j_uri:
        click.echo("Error: --neo4j-uri is required", err=True)
        sys.exit(1)

    click.echo(f"Exporting from Neo4j ({neo4j_uri})...")
    rows = export_from_neo4j(
        uri=neo4j_uri,
        user=neo4j_user,
        password=neo4j_password,
        strategy=strategy,
    )

    if not rows:
        click.echo("No data found to export.")
        return

    path = write_dataset(rows, output, format=fmt)
    click.echo(f"\nExported {len(rows)} samples to {path}")

    # Print dataset summary
    strategies = set(r.get("strategy", "n/a") for r in rows)
    anomalies = set(r.get("anomaly_label", "n/a") for r in rows)
    click.echo(f"  Strategies: {', '.join(sorted(str(s) for s in strategies))}")
    click.echo(f"  Anomaly types: {', '.join(sorted(str(a) for a in anomalies))}")

    # Count columns (features)
    if rows:
        cols = set()
        for row in rows:
            cols.update(row.keys())
        meta_cols = {
            "timestamp",
            "epoch_s",
            "phase",
            "strategy",
            "anomaly_label",
            "run_id",
            "resilience_score",
            "overall_verdict",
        }
        feature_cols = cols - meta_cols
        click.echo(
            f"  Features: {len(feature_cols)} metric columns + {len(meta_cols)} metadata columns"
        )
