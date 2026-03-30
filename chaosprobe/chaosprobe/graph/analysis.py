"""High-level graph analysis functions for chaos experiment results.

All functions accept a ``Neo4jStore`` instance and return plain dicts
suitable for JSON serialization or CLI display.
"""

from typing import Any, Dict, List, Optional


def blast_radius_report(
    store: Any,
    service: str,
    max_hops: int = 3,
) -> Dict[str, Any]:
    """Compute the blast radius for a target service.

    Returns which upstream services would be affected if *service* fails,
    along with the hop distance for each.
    """
    affected = store.get_blast_radius(service, max_hops=max_hops)
    return {
        "targetService": service,
        "maxHops": max_hops,
        "affectedServices": affected,
        "totalAffected": len(affected),
    }


def topology_comparison(
    store: Any,
    run_ids: List[str],
) -> Dict[str, Any]:
    """Compare placement topologies across multiple runs.

    For each run returns the node→deployment mapping so the caller can
    see how different strategies distributed workloads.
    """
    topologies: Dict[str, Any] = {}
    for rid in run_ids:
        topologies[rid] = store.get_topology(rid)
    return {"runs": topologies}


def colocation_impact(
    store: Any,
    run_id: str,
) -> Dict[str, Any]:
    """Analyse resource contention caused by co-located deployments.

    Returns the per-node colocation groups and a contention summary.
    """
    groups = store.get_colocation_analysis(run_id)
    total_shared_nodes = len(groups)
    max_density = max((len(g["deployments"]) for g in groups), default=0)
    return {
        "runId": run_id,
        "sharedNodes": total_shared_nodes,
        "maxDensity": max_density,
        "groups": groups,
    }


def critical_path_analysis(
    store: Any,
) -> Dict[str, Any]:
    """Find the longest dependency chain in the service graph.

    This identifies the most sensitive path — a failure anywhere along
    it cascades through the most hops.
    """
    # Query the longest path in the DEPENDS_ON graph
    with store._driver.session() as session:
        result = session.run(
            "MATCH path = (a:Service)-[:DEPENDS_ON*]->(b:Service) "
            "WHERE NOT (b)-[:DEPENDS_ON]->() "
            "RETURN [n IN nodes(path) | n.name] AS chain, "
            "       length(path) AS depth "
            "ORDER BY depth DESC LIMIT 1"
        )
        record = result.single()
        if record:
            return {
                "chain": record["chain"],
                "depth": record["depth"],
            }
        return {"chain": [], "depth": 0}


def strategy_summary(
    store: Any,
    run_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Summarise experiment outcomes grouped by placement strategy.

    Returns per-strategy aggregates (avg score, avg recovery, run count).
    """
    rows = store.compare_strategies_graph(run_ids=run_ids)

    strategies: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        name = row["strategy"]
        if name not in strategies:
            strategies[name] = {
                "runs": 0,
                "scores": [],
                "recoveries": [],
            }
        strategies[name]["runs"] += 1
        strategies[name]["scores"].append(row["resilience_score"])
        if row.get("mean_recovery_ms") is not None:
            strategies[name]["recoveries"].append(row["mean_recovery_ms"])

    summary: Dict[str, Any] = {}
    for name, data in strategies.items():
        scores = data["scores"]
        recs = data["recoveries"]
        summary[name] = {
            "runCount": data["runs"],
            "avgResilienceScore": round(sum(scores) / len(scores), 2) if scores else None,
            "avgRecoveryMs": round(sum(recs) / len(recs), 2) if recs else None,
        }
    return {"strategies": summary}
