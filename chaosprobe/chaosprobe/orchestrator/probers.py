"""Prober lifecycle helpers for the ``chaosprobe run`` command.

Creates, starts, stops, and collects results from all continuous
measurement probers (latency, throughput, resources, Prometheus,
recovery watcher) and the optional Locust load generator.
"""

from typing import Any, Dict, Tuple

import click

# ---------------------------------------------------------------------------
# Create & start
# ---------------------------------------------------------------------------

def create_and_start_probers(
    namespace: str,
    target_deployment: str,
    *,
    measure_latency: bool,
    measure_redis: bool,
    measure_disk: bool,
    measure_resources: bool,
    measure_prometheus: bool,
    prometheus_url: Tuple[str, ...],
    http_routes: Any = None,
    expected_chaos_duration: float | None = None,
) -> Dict[str, Any]:
    """Create continuous probers and start them in parallel.

    Returns a dict keyed by prober name with the prober instances (or
    None for disabled probers).
    """
    from chaosprobe.metrics.latency import ContinuousLatencyProber
    from chaosprobe.metrics.prometheus import ContinuousPrometheusProber
    from chaosprobe.metrics.recovery import RecoveryWatcher
    from chaosprobe.metrics.resources import ContinuousResourceProber
    from chaosprobe.metrics.throughput import ContinuousDiskProber, ContinuousRedisProber

    watcher = RecoveryWatcher(namespace, target_deployment)
    latency_prober = (
        ContinuousLatencyProber(
            namespace,
            http_routes=http_routes,
            exclude_prefixes=[target_deployment],
            expected_chaos_duration=expected_chaos_duration,
        )
        if measure_latency
        else None
    )
    redis_prober = ContinuousRedisProber(namespace) if measure_redis else None
    disk_prober = (
        ContinuousDiskProber(namespace, exclude_services=[target_deployment])
        if measure_disk
        else None
    )
    resource_prober = (
        ContinuousResourceProber(namespace, target_deployment)
        if measure_resources
        else None
    )
    prometheus_prober = (
        ContinuousPrometheusProber(
            namespace,
            prometheus_urls=list(prometheus_url) if prometheus_url else None,
        )
        if measure_prometheus
        else None
    )

    # Propagate expected chaos duration to all probers so the base class
    # can cap "during-chaos" phase labeling (previously only latency had this).
    if expected_chaos_duration is not None:
        for p in (redis_prober, disk_prober, resource_prober, prometheus_prober):
            if p is not None:
                p._expected_chaos_duration = expected_chaos_duration

    probers_to_start = [
        (label, p)
        for label, p in [
            ("recovery watcher", watcher),
            ("inter-service latency probing", latency_prober),
            ("Redis throughput probing", redis_prober),
            ("disk I/O throughput probing", disk_prober),
            ("resource utilization probing", resource_prober),
            ("Prometheus metrics collection", prometheus_prober),
        ]
        if p is not None
    ]
    labels = [label for label, _ in probers_to_start if label != "recovery watcher"]
    if labels:
        click.echo(f"    Starting {', '.join(labels)}...")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=len(probers_to_start)) as executor:
        futures = {executor.submit(p.start): label for label, p in probers_to_start}
        for f in as_completed(futures):
            f.result()

    return {
        "watcher": watcher,
        "latency": latency_prober,
        "redis": redis_prober,
        "disk": disk_prober,
        "resource": resource_prober,
        "prometheus": prometheus_prober,
    }


# ---------------------------------------------------------------------------
# Stop & collect
# ---------------------------------------------------------------------------

def stop_and_collect_probers(
    probers: Dict[str, Any],
    locust_runner: Any = None,
) -> Dict[str, Any]:
    """Stop all probers and collect their results.

    Returns a dict with keys matching prober names, values are result dicts.
    Also includes ``load_stats`` if a Locust runner was active.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    active = [
        p
        for p in [
            locust_runner,
            probers.get("latency"),
            probers.get("redis"),
            probers.get("disk"),
            probers.get("resource"),
            probers.get("prometheus"),
            probers.get("watcher"),
        ]
        if p is not None
    ]
    if active:
        with ThreadPoolExecutor(max_workers=len(active)) as executor:
            stop_futs = {executor.submit(p.stop): p for p in active}
            for f in as_completed(stop_futs):
                try:
                    f.result()
                except Exception:
                    pass

    results: Dict[str, Any] = {}
    error_breakdown: Dict[str, int] = {}

    # Locust
    if locust_runner:
        try:
            stats = locust_runner.collect_stats()
            results["load_stats"] = stats
            click.echo(
                f"    Load: {stats.total_requests} reqs, "
                f"p95={stats.p95_response_time_ms:.0f}ms, "
                f"err={stats.error_rate:.2%}"
            )
        except Exception as e:
            click.echo(f"    Warning: failed to collect load stats: {e}", err=True)
        finally:
            locust_runner.cleanup()

    # Latency
    if probers.get("latency"):
        try:
            data = probers["latency"].result()
            results["latency"] = data
            phase_data = data.get("phases", {})
            during = phase_data.get("during-chaos", {})
            click.echo(f"    Latency: {during.get('sampleCount', 0)} samples during chaos")
            if data.get("probeErrors", 0) > 0:
                error_breakdown["latency"] = data["probeErrors"]
        except Exception as e:
            click.echo(f"    Warning: failed to collect latency data: {e}", err=True)

    # Redis
    if probers.get("redis"):
        try:
            data = probers["redis"].result()
            results["redis"] = data
            rp = data.get("phases", {}).get("during-chaos", {})
            click.echo(f"    Redis: {rp.get('sampleCount', 0)} samples during chaos")
        except Exception as e:
            click.echo(f"    Warning: failed to collect Redis data: {e}", err=True)

    # Disk
    if probers.get("disk"):
        try:
            data = probers["disk"].result()
            results["disk"] = data
            dp = data.get("phases", {}).get("during-chaos", {})
            click.echo(f"    Disk: {dp.get('sampleCount', 0)} samples during chaos")
        except Exception as e:
            click.echo(f"    Warning: failed to collect disk data: {e}", err=True)

    # Resources
    if probers.get("resource"):
        try:
            data = probers["resource"].result()
            results["resource"] = data
            if data.get("available"):
                rp = data.get("phases", {}).get("during-chaos", {})
                click.echo(f"    Resources: {rp.get('sampleCount', 0)} samples during chaos")
            else:
                click.echo(f"    Resources: {data.get('reason', 'unavailable')}")
        except Exception as e:
            click.echo(f"    Warning: failed to collect resource data: {e}", err=True)

    # Prometheus
    if probers.get("prometheus"):
        try:
            data = probers["prometheus"].result()
            results["prometheus"] = data
            if data.get("available"):
                pp = data.get("phases", {}).get("during-chaos", {})
                click.echo(f"    Prometheus: {pp.get('sampleCount', 0)} samples during chaos")
            else:
                click.echo(f"    Prometheus: {data.get('reason', 'unavailable')}")
        except Exception as e:
            click.echo(f"    Warning: failed to collect Prometheus data: {e}", err=True)

    # Recovery watcher
    if probers.get("watcher"):
        results["recovery"] = probers["watcher"].result()

    # Include per-prober error breakdown so users can diagnose which
    # probers had issues instead of a single aggregated count.
    if error_breakdown:
        results["probeErrorBreakdown"] = error_breakdown
        click.echo(f"    Probe errors: {error_breakdown}")

    return results
