"""Shared CLI option decorators and helpers used across command modules."""

import sys

import click

neo4j_uri_option = click.option(
    "--neo4j-uri",
    default="bolt://localhost:7687",
    envvar="NEO4J_URI",
    help="Neo4j connection URI (default: bolt://localhost:7687)",
)
neo4j_user_option = click.option(
    "--neo4j-user",
    default="neo4j",
    envvar="NEO4J_USER",
    help="Neo4j username",
)
neo4j_password_option = click.option(
    "--neo4j-password",
    default="chaosprobe",
    envvar="NEO4J_PASSWORD",
    help="Neo4j password (default: chaosprobe)",
)


def get_graph_store(uri, user, password):
    """Create a Neo4jStore, handling missing dependency gracefully."""
    try:
        from chaosprobe.storage.neo4j_store import Neo4jStore
    except ImportError:
        click.echo(
            "Error: Neo4j support not installed.\n"
            "  Install with:  uv pip install chaosprobe[graph]",
            err=True,
        )
        sys.exit(1)
    return Neo4jStore(uri, user, password)
