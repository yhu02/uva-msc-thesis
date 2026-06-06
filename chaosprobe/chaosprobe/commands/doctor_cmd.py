"""CLI command for data-quality issues in a summary.json.

Scans the per-strategy aggregates and per-iteration fields produced by
``chaosprobe run`` and surfaces anything that should be addressed
before the analysis is defended: tainted iterations, scheduler-overridden
placements, OOMKills, error iterations, missing recovery data.

Designed for a defender who wants to spot issues *before* the committee
asks "but what about iteration 3 of colocate?".
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import click


def _check_strategy(strategy_name: str, sdata: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Run every check against one strategy block.  Returns a list of
    ``(severity, message)`` tuples; severity is ``warn`` or ``error``."""
    issues: List[Tuple[str, str]] = []
    agg = sdata.get("aggregated") or {}
    iters = sdata.get("iterations") or []
    n_iters = len(iters)

    # Tainted iterations (pre-chaos baseline already degraded).
    tainted = agg.get("taintedIterations") or 0
    if tainted:
        reasons = agg.get("taintReasonCounts") or {}
        reason_str = (
            " (" + ", ".join(f"{k}={v}" for k, v in reasons.items()) + ")" if reasons else ""
        )
        issues.append(
            (
                "warn",
                f"{tainted}/{n_iters} iteration(s) tainted{reason_str}",
            )
        )
    if agg.get("allIterationsTainted"):
        issues.append(("error", "every iteration was tainted — results not usable"))

    # Error iterations (infra failure, all-Unknown probes, exceptions).
    errors = agg.get("errors") or 0
    if errors:
        issues.append(("warn", f"{errors}/{n_iters} iteration(s) errored"))

    # Placement match rate < 1.0 means scheduler overrode our intent.
    placement = sdata.get("placement") or {}
    diff = (placement.get("metadata") or {}).get("intendedActualDiff") or {}
    match_rate = diff.get("matchRate")
    if match_rate is not None and match_rate < 1.0:
        mismatched = len(diff.get("mismatched") or [])
        issues.append(
            (
                "warn" if match_rate >= 0.8 else "error",
                f"placement match rate {match_rate:.2f} "
                f"({mismatched} deployment(s) scheduled elsewhere)",
            )
        )

    # OOMKills indicate the strategy hit cgroup memory limits — confounds
    # the resilience signal with raw OOM behaviour.
    total_oom = agg.get("totalOOMKills") or 0
    if total_oom:
        iters_with_oom = agg.get("iterationsWithOOMKills") or 0
        issues.append(
            (
                "warn",
                f"{total_oom} OOMKill(s) across {iters_with_oom} iteration(s) "
                f"— resilience score includes self-OOM behaviour",
            )
        )

    # Node pressure conditions firing during the run.
    pressure = agg.get("nodePressureEvents") or {}
    fired = {
        cond: data
        for cond, data in pressure.items()
        if isinstance(data, dict) and data.get("iterationsWithEvent", 0) > 0
    }
    if fired:
        summary = ", ".join(
            f"{cond} on {data['iterationsWithEvent']}/{n_iters} iter"
            for cond, data in fired.items()
        )
        issues.append(("warn", f"node pressure conditions fired: {summary}"))

    # Missing recovery data — analyses on this strategy will have gaps.
    if n_iters > 0 and agg.get("meanRecoveryTime_ms") is None:
        issues.append(
            (
                "warn",
                "no recovery times collected — recovery-based stats unavailable",
            )
        )

    # Very small sample for CI.
    if n_iters < 3:
        issues.append(
            (
                "warn",
                f"only {n_iters} iteration(s) — CI / Mann-Whitney unreliable below n=3",
            )
        )

    return issues


def _check_cross_strategy(strategies: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Run checks that compare strategies against each other.

    Surfaces inconclusive analyses (every CI overlaps with every other),
    cluster-wide problems (all strategies hit OOMKills), and outlier
    strategies (one strategy has wildly different load from the rest).
    """
    issues: List[Tuple[str, str]] = []
    if len(strategies) < 2:
        return issues

    # All-CIs-overlap → analysis is inconclusive.
    cis: Dict[str, Tuple[float, float]] = {}
    for name, sdata in strategies.items():
        ci = (sdata.get("aggregated") or {}).get("meanResilienceScore_ci95")
        if isinstance(ci, dict) and ci.get("low") is not None and ci.get("high") is not None:
            cis[name] = (float(ci["low"]), float(ci["high"]))
    if len(cis) >= 2:
        names = list(cis.keys())
        all_overlap = True
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a_low, a_high = cis[names[i]]
                b_low, b_high = cis[names[j]]
                if a_high < b_low or b_high < a_low:
                    all_overlap = False
                    break
            if not all_overlap:
                break
        if all_overlap:
            issues.append(
                (
                    "warn",
                    f"every pair of strategy CIs overlaps across {len(cis)} "
                    f"strategies — analysis is statistically inconclusive at "
                    f"this n; consider more iterations or seed variance",
                )
            )

    # Every strategy hit OOMKills → cluster is undersized or the workload
    # itself OOMs, not the placement.
    with_oom = [
        name
        for name, sdata in strategies.items()
        if ((sdata.get("aggregated") or {}).get("totalOOMKills") or 0) > 0
    ]
    if len(with_oom) == len(strategies) and len(strategies) >= 3:
        issues.append(
            (
                "warn",
                f"every strategy hit OOMKills ({len(strategies)}/{len(strategies)}) "
                f"— cluster likely undersized for the workload; not a "
                f"placement-attributable signal",
            )
        )

    # Every strategy had tainted iterations → cluster is unstable
    # independent of placement.
    all_tainted = [
        name
        for name, sdata in strategies.items()
        if ((sdata.get("aggregated") or {}).get("taintedIterations") or 0) > 0
    ]
    if len(all_tainted) == len(strategies) and len(strategies) >= 3:
        issues.append(
            (
                "warn",
                "every strategy had tainted iterations — cluster is unstable "
                "independent of placement",
            )
        )

    # Locust offered RPS skew across strategies.
    rps_means: Dict[str, float] = {}
    for name, sdata in strategies.items():
        load_agg = (sdata.get("aggregated") or {}).get("loadGenerationAggregate") or {}
        mean_rps = load_agg.get("meanRequestsPerSecond")
        if isinstance(mean_rps, (int, float)):
            rps_means[name] = float(mean_rps)
    if len(rps_means) >= 2:
        vals = list(rps_means.values())
        spread = (max(vals) - min(vals)) / max(max(vals), 0.1)
        if spread > 0.20:
            mn = min(rps_means, key=lambda k: rps_means[k])
            mx = max(rps_means, key=lambda k: rps_means[k])
            issues.append(
                (
                    "warn",
                    f"Locust offered RPS varies by {spread:.0%} across strategies "
                    f"(low: {mn}={rps_means[mn]:.1f}, high: {mx}={rps_means[mx]:.1f}) — "
                    f"load drift may confound the resilience comparison",
                )
            )

    return issues


def _check_schema_version(raw: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Check whether ``schemaVersion`` matches the current SCHEMA_VERSION.

    A mismatch usually means the summary was produced by a chaosprobe
    version with a different output shape — some doctor / stats / summarize
    checks may silently miss fields that have been renamed or removed.
    """
    from chaosprobe.output import SCHEMA_VERSION

    issues: List[Tuple[str, str]] = []
    version = raw.get("schemaVersion")
    if version is None:
        issues.append(
            (
                "warn",
                f"schemaVersion missing — current chaosprobe writes {SCHEMA_VERSION}; "
                f"some analysis tools may miss renamed fields",
            )
        )
    elif version != SCHEMA_VERSION:
        issues.append(
            (
                "warn",
                f"schemaVersion {version} differs from current {SCHEMA_VERSION} — "
                f"analysis tools may miss renamed or removed fields",
            )
        )
    return issues


def _check_run_metadata(raw: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Check run-level reproducibility metadata.

    A summary without ``runMetadata`` was either produced by an older
    chaosprobe version (before PR #44) or by a manually-assembled file.
    Either way, the reproducibility claim is weakened.  Specific gaps
    inside the block are surfaced too: dirty git, missing K8s server
    version, missing CNI hint, missing kube-proxy mode.
    """
    issues: List[Tuple[str, str]] = []
    md = raw.get("runMetadata")
    if not isinstance(md, dict):
        issues.append(
            (
                "warn",
                "runMetadata absent — summary was produced by an older "
                "chaosprobe version; reproducibility provenance is incomplete",
            )
        )
        return issues

    git = md.get("git") or {}
    if git.get("commit") is None:
        issues.append(
            (
                "warn",
                "git commit not recorded — runMetadata.git.commit is missing",
            )
        )
    if git.get("dirty") is True:
        issues.append(
            (
                "warn",
                f"data collected from a dirty working tree "
                f"(commit {git.get('shortCommit') or 'unknown'}) — "
                f"the recorded commit doesn't fully represent the running code",
            )
        )

    k8s = md.get("kubernetes") or {}
    if k8s.get("serverVersion") is None:
        issues.append(
            ("warn", "Kubernetes server version not recorded — portability claim unverifiable")
        )
    if md.get("cniHint") is None:
        issues.append(
            ("warn", "CNI hint not recorded — Felix / Calico-specific metrics unverifiable")
        )
    kube_proxy = md.get("kubeProxy") or {}
    if kube_proxy.get("mode") is None:
        issues.append(
            (
                "warn",
                "kube-proxy mode not recorded — iptables / ipvs / nftables-specific "
                "reconvergence claims (conntrack flush, sync latency) are environment-contingent "
                "and unverifiable without it",
            )
        )
    return issues


def _check_scenario_hashes(raw: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Check that the run recorded SHA-256 hashes of its scenario YAMLs.

    Without ``scenarioHashes`` a reviewer can't confirm a quoted result came
    from the scenario files on disk rather than a since-edited copy — silent
    scenario drift is one of the evidence-chain gaps the thesis advisory
    flags. Produced automatically by ``run`` (PR #208); a summary that lacks
    it was made by an older chaosprobe or assembled by hand. Surfaced as a
    warn so ``doctor --strict`` (the gate every quoted run must clear) fails
    on it.
    """
    issues: List[Tuple[str, str]] = []
    hashes = raw.get("scenarioHashes")
    if not isinstance(hashes, list) or not hashes:
        issues.append(
            (
                "warn",
                "scenario SHA-256 hashes not recorded — scenarioHashes is absent or empty, "
                "so silent scenario drift between this result and its YAMLs can't be ruled out",
            )
        )
    return issues


@click.command("doctor")
@click.option(
    "--summary",
    "-s",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to a summary.json produced by `chaosprobe run`.",
)
@click.option(
    "--strict",
    is_flag=True,
    help="Exit non-zero on any warn-level issue (not just errors).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit findings as JSON instead of a human-readable report.",
)
def doctor(summary: Path, strict: bool, as_json: bool):
    """Report data-quality issues in a summary.json.

    \b
    Examples:
      chaosprobe doctor -s results/20260530-142103/summary.json
      chaosprobe doctor -s summary.json --strict
      chaosprobe doctor -s summary.json --json
    """
    raw = json.loads(summary.read_text())
    strategies = raw.get("strategies") or {}

    report: Dict[str, List[Dict[str, str]]] = {}
    error_count = 0
    warn_count = 0

    def _tally(issues_list: List[Tuple[str, str]]) -> List[Dict[str, str]]:
        nonlocal error_count, warn_count
        for sev, _ in issues_list:
            if sev == "error":
                error_count += 1
            elif sev == "warn":
                warn_count += 1
        return [{"severity": sev, "message": msg} for sev, msg in issues_list]

    for name in sorted(strategies.keys()):
        issues = _check_strategy(name, strategies[name])
        if issues:
            report[name] = _tally(issues)

    cross = _check_cross_strategy(strategies)
    if cross:
        report["__cross_strategy__"] = _tally(cross)

    metadata = _check_run_metadata(raw)
    if metadata:
        report["__run_metadata__"] = _tally(metadata)

    scenario_hashes = _check_scenario_hashes(raw)
    if scenario_hashes:
        report["__scenario_hashes__"] = _tally(scenario_hashes)

    schema = _check_schema_version(raw)
    if schema:
        report["__schema_version__"] = _tally(schema)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "source": str(summary),
                    "strategiesChecked": len(strategies),
                    "errorCount": error_count,
                    "warnCount": warn_count,
                    "findings": report,
                },
                indent=2,
            )
        )
    else:
        if not report:
            click.echo(f"  ✓ no issues across {len(strategies)} strategies")
        else:
            _heading_map = {
                "__cross_strategy__": "cross-strategy",
                "__run_metadata__": "run metadata",
                "__schema_version__": "schema version",
            }
            for name, findings in report.items():
                heading = _heading_map.get(name, name)
                click.echo(f"\n  {heading}")
                for finding in findings:
                    marker = "✗" if finding["severity"] == "error" else "!"
                    click.echo(f"    {marker} {finding['message']}")
            click.echo("")
            click.echo(f"  summary: {error_count} error(s), {warn_count} warning(s)")

    if error_count > 0 or (strict and warn_count > 0):
        sys.exit(1)
