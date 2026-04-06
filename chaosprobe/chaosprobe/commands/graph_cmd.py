"""CLI commands for Neo4j graph operations."""

import json

import click

from chaosprobe.commands.shared import (
    get_graph_store,
    neo4j_password_option,
    neo4j_uri_option,
    neo4j_user_option,
)


@click.group()
def graph():
    """Neo4j graph commands for topology and blast-radius analysis."""
    pass


@graph.command("status")
@neo4j_uri_option
@neo4j_user_option
@neo4j_password_option
def graph_status(neo4j_uri, neo4j_user, neo4j_password):
    """Check Neo4j connectivity and show node counts."""
    store = get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
    try:
        counts = store.status()
        click.echo("Neo4j connected ✓")
        click.echo(f"\n  {'Label':<22s} {'Count'}")
        click.echo(f"  {'─' * 32}")
        for label, count in counts.items():
            click.echo(f"  {label:<22s} {count}")
    finally:
        store.close()


@graph.command("sessions")
@neo4j_uri_option
@neo4j_user_option
@neo4j_password_option
def graph_sessions(neo4j_uri, neo4j_user, neo4j_password):
    """List all experiment sessions stored in Neo4j."""
    store = get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
    try:
        sessions = store.list_sessions()
        if not sessions:
            click.echo("No sessions found.")
            return
        click.echo(f"\n  {'Session ID':<22s} {'Runs':<6s} {'Strategies':<40s} {'First Run'}")
        click.echo(f"  {'─' * 80}")
        for s in sessions:
            strats = ", ".join(sorted(s.get("strategies", [])))
            click.echo(
                f"  {s['session_id']:<22s} {s['run_count']:<6d} "
                f"{strats:<40s} {s.get('first_run', '')}"
            )
    finally:
        store.close()


@graph.command("blast-radius")
@click.argument("service_name")
@click.option("--max-hops", default=3, type=int, help="Maximum dependency depth")
@neo4j_uri_option
@neo4j_user_option
@neo4j_password_option
def graph_blast_radius(service_name, max_hops, neo4j_uri, neo4j_user, neo4j_password):
    """Show the blast radius for a service (upstream dependents)."""
    from chaosprobe.graph.analysis import blast_radius_report

    store = get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
    try:
        report = blast_radius_report(store, service_name, max_hops=max_hops)
        click.echo(f"\nBlast radius for '{service_name}' (max {max_hops} hops):")
        if not report["affectedServices"]:
            click.echo("  No upstream dependents found.")
        else:
            click.echo(f"\n  {'Service':<35s} {'Hops'}")
            click.echo(f"  {'─' * 42}")
            for svc in report["affectedServices"]:
                click.echo(f"  {svc['name']:<35s} {svc['hops']}")
            click.echo(f"\n  Total affected: {report['totalAffected']}")
    finally:
        store.close()


@graph.command("topology")
@click.option("--run-id", required=True, help="Run ID to show topology for")
@neo4j_uri_option
@neo4j_user_option
@neo4j_password_option
def graph_topology(run_id, neo4j_uri, neo4j_user, neo4j_password):
    """Show placement topology for a specific run."""
    store = get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
    try:
        topo = store.get_topology(run_id)
        if not topo["nodes"] and not topo["unscheduled"]:
            click.echo(f"No topology data found for run {run_id}")
            return

        click.echo(f"\nTopology for run {run_id}:")
        for node_info in topo["nodes"]:
            deps = ", ".join(node_info["deployments"])
            click.echo(f"\n  Node: {node_info['node']}")
            click.echo(f"    Deployments: {deps}")

        if topo["unscheduled"]:
            click.echo(f"\n  Unscheduled: {', '.join(topo['unscheduled'])}")
    finally:
        store.close()


@graph.command("details")
@click.argument("run_id")
@neo4j_uri_option
@neo4j_user_option
@neo4j_password_option
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def graph_details(run_id, neo4j_uri, neo4j_user, neo4j_password, json_output):
    """Show all stored data for a specific run from Neo4j."""
    store = get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
    try:
        details = store.get_run_details(run_id)
        if not details:
            click.echo(f"No data found for run {run_id}")
            return

        if json_output:
            click.echo(json.dumps(details, indent=2, default=str))
            return

        exp = details["experiment"]
        click.echo(f"\nRun: {exp.get('run_id')}")
        click.echo(f"  Strategy:         {exp.get('strategy')}")
        click.echo(f"  Verdict:          {exp.get('verdict')}")
        click.echo(f"  Resilience Score: {exp.get('resilience_score')}")
        click.echo(f"  Duration:         {exp.get('duration_s')}s")
        click.echo(f"  Total Restarts:   {exp.get('total_restarts')}")

        if exp.get("mean_recovery_ms") is not None:
            click.echo("\n  Recovery:")
            click.echo(f"    Mean:   {exp.get('mean_recovery_ms'):.0f}ms")
            click.echo(f"    Median: {exp.get('median_recovery_ms'):.0f}ms")
            click.echo(f"    Min:    {exp.get('min_recovery_ms')}ms")
            click.echo(f"    Max:    {exp.get('max_recovery_ms')}ms")
            click.echo(f"    P95:    {exp.get('p95_recovery_ms')}ms")
            click.echo(
                f"    Cycles: {exp.get('completed_cycles')} completed, {exp.get('incomplete_cycles')} incomplete"
            )

        if exp.get("load_profile"):
            click.echo(f"\n  Load Generation: {exp.get('load_profile')}")
            click.echo(
                f"    Requests: {exp.get('load_total_requests')} ({exp.get('load_total_failures')} failures)"
            )
            click.echo(
                f"    Avg Response: {exp.get('load_avg_response_ms')}ms  P95: {exp.get('load_p95_response_ms')}ms"
            )

        cycles = details.get("recoveryCycles", [])
        if cycles:
            click.echo(f"\n  Recovery Cycles ({len(cycles)}):")
            for c in cycles:
                click.echo(
                    f"    #{c.get('seq', '?')}: {c.get('total_recovery_ms')}ms "
                    f"(sched: {c.get('deletion_to_scheduled_ms')}ms, "
                    f"ready: {c.get('scheduled_to_ready_ms')}ms)"
                )

        phases = details.get("metricsPhases", [])
        if phases:
            click.echo(f"\n  Metrics Phases ({len(phases)}):")
            for p in phases:
                click.echo(
                    f"    {p.get('metric_type'):<12s} {p.get('phase'):<15s} "
                    f"({p.get('sample_count', 0)} samples)"
                )

        pods = details.get("podSnapshots", [])
        if pods:
            click.echo(f"\n  Pod Snapshots ({len(pods)}):")
            for p in pods:
                click.echo(
                    f"    {p.get('name'):<45s} {p.get('phase'):<10s} "
                    f"node={p.get('node')}  restarts={p.get('restart_count')}"
                )

        results = details.get("experimentResults", [])
        if results:
            click.echo(f"\n  Experiment Results ({len(results)}):")
            for r in results:
                click.echo(
                    f"    {r.get('name')}: {r.get('verdict')} "
                    f"(probe success: {r.get('probe_success_pct')}%)"
                )
    finally:
        store.close()


@graph.command("compare")
@click.option("--run-ids", required=True, help="Comma-separated run IDs to compare")
@neo4j_uri_option
@neo4j_user_option
@neo4j_password_option
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def graph_compare(run_ids, neo4j_uri, neo4j_user, neo4j_password, json_output):
    """Compare strategies across runs using graph data."""
    from chaosprobe.graph.analysis import strategy_summary

    ids = [r.strip() for r in run_ids.split(",")]
    store = get_graph_store(neo4j_uri, neo4j_user, neo4j_password)
    try:
        summary = strategy_summary(store, run_ids=ids)
        if json_output:
            click.echo(json.dumps(summary, indent=2))
            return

        strategies = summary.get("strategies", {})
        if not strategies:
            click.echo("No data found for the given run IDs.")
            return

        click.echo(f"\n  {'Strategy':<18s} {'Runs':<6s} {'Avg Score':<12s} {'Avg Recovery'}")
        click.echo(f"  {'─' * 50}")
        for name, data in strategies.items():
            score_str = (
                f"{data['avgResilienceScore']:.1f}"
                if data["avgResilienceScore"] is not None
                else "n/a"
            )
            rec_str = (
                f"{data['avgRecoveryMs']:.0f}ms" if data["avgRecoveryMs"] is not None else "n/a"
            )
            click.echo(f"  {name:<18s} {data['runCount']:<6d} {score_str:<12s} {rec_str}")
    finally:
        store.close()
