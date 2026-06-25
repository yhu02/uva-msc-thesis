"""Pure multi-iteration aggregation and cross-strategy comparison helpers.

Side-effect-free data-shaping extracted from ``run_phases`` so the
research-validity-critical roll-up logic (per-iteration aggregation, the
comparison table, recovery/route/node-pressure roll-ups) lives in one
directly unit-testable place instead of a 1500-line module mixing it with
terminal output and presentation regeneration.
"""

import statistics
from typing import Any, Dict, List


def _build_comparison_table_impl(
    strategies: Dict[str, Any], iterations: int
) -> List[Dict[str, Any]]:
    """Build the comparison table from strategy results.

    This is the implementation moved from cli.py's _build_comparison_table.
    """
    table: List[Dict[str, Any]] = []
    for name, data in strategies.items():
        row: Dict[str, Any] = {
            "strategy": name,
            "status": data.get("status", "unknown"),
            "verdict": "ERROR",
            "resilienceScore": 0.0,
            "stddevScore": 0.0,
            "scoreRange": "",
            "avgRecovery_ms": None,
            "maxRecovery_ms": None,
            "stddevRecovery_ms": None,
            "perIterationScores": [],
        }
        if data.get("status") == "error":
            row["verdict"] = "ERROR"
            table.append(row)
            continue

        if iterations > 1:
            agg = data.get("aggregated", {})
            row["verdict"] = "PASS" if agg.get("passRate", 0) == 1.0 else "FAIL"
            # Prefer healthy-only mean when tainted iterations exist,
            # so scores reflect actual strategy resilience rather than
            # accumulated damage from cascading iteration poisoning.
            if agg.get("taintedIterations", 0) > 0 and not agg.get("allIterationsTainted", False):
                healthy_mean = agg.get(
                    "meanResilienceScore_healthyOnly",
                )
                row["resilienceScore"] = (
                    healthy_mean
                    if healthy_mean is not None
                    else agg.get("meanResilienceScore", 0.0)
                )
                healthy_sd = agg.get(
                    "stddevResilienceScore_healthyOnly",
                )
                row["stddevScore"] = (
                    healthy_sd if healthy_sd is not None else agg.get("stddevResilienceScore", 0.0)
                )
            else:
                row["resilienceScore"] = agg.get("meanResilienceScore", 0.0)
                row["stddevScore"] = agg.get("stddevResilienceScore", 0.0)
            min_s = agg.get("minResilienceScore")
            max_s = agg.get("maxResilienceScore")
            if min_s is not None and max_s is not None:
                row["scoreRange"] = f"{min_s:.0f}-{max_s:.0f}"
            row["avgRecovery_ms"] = agg.get("meanRecoveryTime_ms")
            row["maxRecovery_ms"] = agg.get("maxRecoveryTime_ms")
            row["stddevRecovery_ms"] = agg.get("stddevRecoveryTime_ms")
            row["perIterationScores"] = agg.get("perIterationScores", [])
        else:
            exp = data.get("experiment", {})
            row["verdict"] = exp.get("overallVerdict", "UNKNOWN")
            row["resilienceScore"] = exp.get("resilienceScore", 0.0)
            metrics = data.get("metrics", {})
            recovery = metrics.get("recovery", {}).get("summary", {}) if metrics else {}
            row["avgRecovery_ms"] = recovery.get("meanRecovery_ms")
            row["maxRecovery_ms"] = recovery.get("maxRecovery_ms")
        # All-ERROR strategies keep status "completed" but leave
        # meanResilienceScore/stddev as None (aggregate_iterations' all-ERROR
        # guard emits null, not a fabricated 0.0).  The printed and written
        # comparison row formats these with ``:.1f`` downstream, so coerce None
        # to 0.0 here to avoid a TypeError that aborts the run's final summary.
        # The underlying summary fields stay null so stats/recommend/doctor
        # still see "no data" (mirrors output/visualize.py's coercion).
        if row["resilienceScore"] is None:
            row["resilienceScore"] = 0.0
        if row["stddevScore"] is None:
            row["stddevScore"] = 0.0
        table.append(row)
    return table


# ---------------------------------------------------------------------------
# 6.  Multi-iteration aggregation
# ---------------------------------------------------------------------------


def aggregate_iterations(
    iteration_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute aggregated statistics across multiple iterations."""
    if not iteration_results:
        return {
            "overallVerdict": "FAIL",
            "passRate": 0.0,
            "meanResilienceScore": 0.0,
            "totalExperiments": 0,
            "passed": 0,
            "failed": 0,
            "meanRecoveryTime_ms": None,
            "medianRecoveryTime_ms": None,
            "maxRecoveryTime_ms": None,
        }

    scores = [ir["resilienceScore"] for ir in iteration_results]
    verdicts = [ir["verdict"] for ir in iteration_results]
    pass_count = sum(1 for v in verdicts if v == "PASS")

    # Exclude ERROR iterations (infra failures, all-Unknown probes) from
    # score statistics.  These are not valid measurements — including
    # their 0.0 scores would drag down the mean and inflate stddev
    # without reflecting actual strategy resilience.
    valid_iters = [ir for ir in iteration_results if ir["verdict"] != "ERROR"]
    error_count = len(iteration_results) - len(valid_iters)

    valid_scores = [ir["resilienceScore"] for ir in valid_iters]

    # Track how many iterations had a healthy pre-chaos baseline.
    # Tainted iterations (pre-chaos already degraded) produce unreliable
    # scores because they reflect accumulated damage, not strategy resilience.
    healthy_iters = [ir for ir in valid_iters if ir.get("preChaosHealthy", True)]
    tainted_count = len(valid_iters) - len(healthy_iters)
    all_tainted = len(healthy_iters) == 0 and tainted_count > 0
    healthy_scores = (
        [ir["resilienceScore"] for ir in healthy_iters] if healthy_iters else valid_scores
    )

    # bootstrap_ci feeds both the resilience-score CI (only computed when there
    # are valid scores) and the recovery CI further down (which runs even for
    # all-ERROR strategies that still produced recovery data), so import it
    # unconditionally here rather than inside the valid-scores branch.
    from chaosprobe.metrics.statistics import bootstrap_ci

    # When *every* iteration errored (infra failure / all-Unknown probes),
    # there is no valid measurement to summarise.  Computing the score stats
    # from the raw scores would re-inject exactly the meaningless 0.0s the
    # ERROR verdict exists to exclude — strategy_runner marks all-Unknown-probe
    # iterations ERROR precisely so "Score 0.0 ... is not a valid resilience
    # measurement; it's an infrastructure failure" never enters the stats.
    # Emit null score statistics (not a fabricated 0.0) plus the
    # ``allIterationsError`` flag so analysis can tell "no valid data" apart
    # from "genuinely scored zero".  The diagnostic roll-ups further down
    # (taintReasonCounts, probeVerdictTally, recovery) still populate from the
    # ERROR iterations.
    if valid_scores:
        healthy_stddev = (
            round(statistics.stdev(healthy_scores), 1) if len(healthy_scores) > 1 else 0.0
        )
        valid_stddev = round(statistics.stdev(valid_scores), 1) if len(valid_scores) > 1 else 0.0

        # ── Tail-aware score variants ──────────────────────────────────────
        # The arithmetic mean hides exactly the tail failures Dean & Barroso
        # ("The Tail at Scale", CACM 2013) argue dominate user-perceived
        # quality.  Surface them so the user can pick the right point estimate
        # for their argument and so the discussion can refer to actual tail
        # percentiles rather than just the mean.
        from chaosprobe.metrics.statistics import _percentile

        sorted_valid = sorted(valid_scores)
        p25_score = _percentile(sorted_valid, 0.25)
        # Harmonic mean penalises low values disproportionately.  Compute on
        # (score + 1) to avoid divide-by-zero when a probe was fully wiped
        # out (score=0); subtract 1 after.  Bounded to [0, 100] for sanity.
        harm_mean = statistics.harmonic_mean([s + 1.0 for s in valid_scores]) - 1.0
        harm_mean = max(0.0, min(100.0, harm_mean))

        # ── Bootstrap CI for the mean ──────────────────────────────────────
        # With n=3 and stddev~25-30, the point-estimate gap between many
        # strategies is well inside the noise floor.  Reporting a bootstrap
        # 95% CI makes the uncertainty visible up-front rather than burying
        # it.
        mean_ci = bootstrap_ci(valid_scores, statistic="mean")

        score_stats: Dict[str, Any] = {
            "meanResilienceScore": round(statistics.mean(valid_scores), 1),
            "meanResilienceScore_healthyOnly": round(statistics.mean(healthy_scores), 1),
            "stddevResilienceScore": valid_stddev,
            "stddevResilienceScore_healthyOnly": healthy_stddev,
            "minResilienceScore": min(valid_scores),
            "maxResilienceScore": max(valid_scores),
            "p25ResilienceScore": round(p25_score, 1),
            "harmonicMeanResilienceScore": round(harm_mean, 1),
            "meanResilienceScore_ci95": {
                "low": mean_ci["ci_low"],
                "high": mean_ci["ci_high"],
                "n": mean_ci["n"],
                "n_resamples": mean_ci["n_resamples"],
            },
        }
    else:
        score_stats = {
            "meanResilienceScore": None,
            "stddevResilienceScore": None,
            "minResilienceScore": None,
            "maxResilienceScore": None,
        }

    # passRate and overallVerdict are computed over *valid* (non-ERROR)
    # iterations only — consistent with the score statistics above, which
    # already exclude ERROR iterations.  Counting ERROR iterations in the
    # denominator would let a single transient infra failure (K8s API blip,
    # ChaosCenter unreachable, all-Unknown probes) drag passRate below 1.0 and
    # mislabel an otherwise-passing strategy FAIL in the comparison table —
    # exactly the infra-noise contamination the ERROR exclusion exists to stop.
    n_valid = len(valid_iters)
    agg: Dict[str, Any] = {
        "overallVerdict": "PASS" if n_valid and pass_count == n_valid else "FAIL",
        "passRate": round(pass_count / n_valid, 2) if n_valid else 0.0,
        **score_stats,
        "totalExperiments": len(iteration_results),
        "passed": pass_count,
        "failed": len(verdicts) - pass_count - error_count,
        "errors": error_count,
        "allIterationsError": not valid_iters,
        "taintedIterations": tainted_count,
        "allIterationsTainted": all_tainted,
        "perIterationScores": scores,
    }

    # Taint reason taxonomy across iterations.  preChaosTaintReasons is
    # a list (multiple gates can fire on one iteration); counting per
    # reason answers "is the taint pattern consistent" — same reason
    # every time suggests a clear root cause; mixed reasons usually
    # reflect cluster noise.
    taint_reason_counts: Dict[str, int] = {}
    for ir in iteration_results:
        reasons = ir.get("preChaosTaintReasons") or []
        if not isinstance(reasons, list):
            continue
        for reason in reasons:
            if not isinstance(reason, str):
                continue
            taint_reason_counts[reason] = taint_reason_counts.get(reason, 0) + 1
    if taint_reason_counts:
        agg["taintReasonCounts"] = taint_reason_counts

    # Collect per-probe verdict tallies across iterations
    probe_tally: Dict[str, Dict[str, int]] = {}
    for ir in iteration_results:
        for pname, pverdict in ir.get("probeVerdicts", {}).items():
            probe_tally.setdefault(pname, {"Pass": 0, "Fail": 0, "Unknown": 0})
            if pverdict in probe_tally[pname]:
                probe_tally[pname][pverdict] += 1
            else:
                probe_tally[pname]["Unknown"] += 1
    if probe_tally:
        agg["probeVerdictTally"] = probe_tally
        # Per-probe success-rate + Wilson 95% CI.  A defender comparing
        # probe-level success between strategies needs intervals — a
        # 4/5 Pass and a 80/100 Pass are both "80%" by point estimate
        # but have very different uncertainty.
        from chaosprobe.metrics.statistics import wilson_ci as _wilson_ci

        success_rates: Dict[str, Dict[str, Any]] = {}
        for pname, counts in probe_tally.items():
            decided = counts["Pass"] + counts["Fail"]
            success_rates[pname] = {
                **_wilson_ci(counts["Pass"], decided),
                "unknown": counts["Unknown"],
            }
        agg["probeSuccessRates"] = success_rates

    # Aggregate recovery metrics from metrics.recovery.summary
    all_recovery_times: List[float] = []
    for ir in iteration_results:
        rm = ir.get("metrics", {})
        if rm:
            summary = rm.get("recovery", {}).get("summary", {})
            mean_r = summary.get("meanRecovery_ms")
            if mean_r is not None:
                all_recovery_times.append(mean_r)

    if all_recovery_times:
        all_max = []
        all_p95 = []
        for ir in iteration_results:
            rm = ir.get("metrics", {})
            if rm:
                summary = rm.get("recovery", {}).get("summary", {})
                max_r = summary.get("maxRecovery_ms")
                if max_r is not None:
                    all_max.append(max_r)
                p95_r = summary.get("p95Recovery_ms")
                if p95_r is not None:
                    all_p95.append(p95_r)

        agg["meanRecoveryTime_ms"] = round(statistics.mean(all_recovery_times), 1)
        agg["stddevRecoveryTime_ms"] = (
            round(statistics.stdev(all_recovery_times), 1) if len(all_recovery_times) > 1 else 0.0
        )
        agg["medianRecoveryTime_ms"] = round(statistics.median(all_recovery_times), 1)
        agg["maxRecoveryTime_ms"] = max(all_max) if all_max else None
        # Coefficient of variation = stddev / mean.  Decouples spread
        # from scale: a strategy that always recovers in 1000±100 ms
        # (CV=0.10) is steadier than one at 200±100 ms (CV=0.50) even
        # though their stddevs are identical.  Pure within-strategy
        # jitter signal — a defender pointing at a low-CV strategy can
        # claim "predictable recovery", not just "fast on average".
        if agg["meanRecoveryTime_ms"] and agg["meanRecoveryTime_ms"] > 0:
            agg["recoveryTimeCV"] = round(
                agg["stddevRecoveryTime_ms"] / agg["meanRecoveryTime_ms"], 3
            )
        else:
            agg["recoveryTimeCV"] = None
        # Aggregate p95: use mean of per-iteration p95 values.
        # Each all_p95 element is already a p95 from that iteration;
        # averaging them gives a representative cross-iteration p95.
        # (Taking max() would report the worst-case outlier, not a
        # proper aggregate percentile.)
        agg["p95RecoveryTime_ms"] = round(statistics.mean(all_p95), 1) if all_p95 else None

        # The thesis's H9 attribution is "scheduling latency dominates
        # recovery" — it lives or dies on the mean of meanRecovery_ms and
        # its split. A point estimate without a CI is not defensible at n=5
        # iterations × ~25-30 stddev, so surface the bootstrap interval
        # alongside the point estimate (matches meanResilienceScore_ci95).
        recovery_ci = bootstrap_ci(all_recovery_times, statistic="mean")
        agg["meanRecoveryTime_ms_ci95"] = {
            "low": recovery_ci["ci_low"],
            "high": recovery_ci["ci_high"],
            "n": recovery_ci["n"],
            "n_resamples": recovery_ci["n_resamples"],
        }

        # Surface the deletion->scheduled vs scheduled->ready split.  Lets
        # downstream analysis distinguish scheduler stalls (large d2s, e.g.
        # affinity collision) from genuine container-start latency (large s2r).
        all_d2s: List[float] = []
        all_s2r: List[float] = []
        for ir in iteration_results:
            rm = ir.get("metrics", {})
            if not rm:
                continue
            summary = rm.get("recovery", {}).get("summary", {})
            v = summary.get("meanDeletionToScheduled_ms")
            if v is not None:
                all_d2s.append(v)
            v = summary.get("meanScheduledToReady_ms")
            if v is not None:
                all_s2r.append(v)

        if all_d2s:
            mean_d2s = round(statistics.mean(all_d2s), 1)
            stddev_d2s = round(statistics.stdev(all_d2s), 1) if len(all_d2s) > 1 else 0.0
            agg["meanDeletionToScheduled_ms"] = mean_d2s
            agg["stddevDeletionToScheduled_ms"] = stddev_d2s
            agg["deletionToScheduledCV"] = round(stddev_d2s / mean_d2s, 3) if mean_d2s > 0 else None
            d2s_ci = bootstrap_ci(all_d2s, statistic="mean")
            agg["meanDeletionToScheduled_ms_ci95"] = {
                "low": d2s_ci["ci_low"],
                "high": d2s_ci["ci_high"],
                "n": d2s_ci["n"],
                "n_resamples": d2s_ci["n_resamples"],
            }
        if all_s2r:
            mean_s2r = round(statistics.mean(all_s2r), 1)
            stddev_s2r = round(statistics.stdev(all_s2r), 1) if len(all_s2r) > 1 else 0.0
            agg["meanScheduledToReady_ms"] = mean_s2r
            agg["stddevScheduledToReady_ms"] = stddev_s2r
            agg["scheduledToReadyCV"] = round(stddev_s2r / mean_s2r, 3) if mean_s2r > 0 else None
            s2r_ci = bootstrap_ci(all_s2r, statistic="mean")
            agg["meanScheduledToReady_ms_ci95"] = {
                "low": s2r_ci["ci_low"],
                "high": s2r_ci["ci_high"],
                "n": s2r_ci["n"],
                "n_resamples": s2r_ci["n_resamples"],
            }

        # Aggregate Locust load-generation stats across iterations so each
        # strategy reports the actual offered RPS / error rate that drove
        # its score.  Without this, a reviewer cannot rule out load drift
        # as the cause of inter-strategy score differences.
        rps_vals: List[float] = []
        err_vals: List[float] = []
        resp_vals: List[float] = []
        for ir in iteration_results:
            lg = ir.get("loadGeneration") or {}
            stats = lg.get("stats") or {}
            v = stats.get("requestsPerSecond")
            if v is not None:
                rps_vals.append(float(v))
            v = stats.get("errorRate")
            if v is not None:
                err_vals.append(float(v))
            v = stats.get("p95ResponseTime_ms") or stats.get("avgResponseTime_ms")
            if v is not None:
                resp_vals.append(float(v))
        if rps_vals or err_vals or resp_vals:

            def _ci_block(values: List[float]) -> Dict[str, Any]:
                ci = bootstrap_ci(values, statistic="mean")
                return {
                    "low": ci["ci_low"],
                    "high": ci["ci_high"],
                    "n": ci["n"],
                    "n_resamples": ci["n_resamples"],
                }

            load_agg: Dict[str, Any] = {}
            if rps_vals:
                load_agg["meanRequestsPerSecond"] = round(statistics.mean(rps_vals), 2)
                load_agg["stddevRequestsPerSecond"] = (
                    round(statistics.stdev(rps_vals), 2) if len(rps_vals) > 1 else 0.0
                )
                load_agg["meanRequestsPerSecond_ci95"] = _ci_block(rps_vals)
            if err_vals:
                load_agg["meanErrorRate"] = round(statistics.mean(err_vals), 4)
                load_agg["meanErrorRate_ci95"] = _ci_block(err_vals)
            if resp_vals:
                load_agg["meanResponseTime_ms"] = round(statistics.mean(resp_vals), 1)
                load_agg["meanResponseTime_ms_ci95"] = _ci_block(resp_vals)
            agg["loadGenerationAggregate"] = load_agg
    else:
        agg["meanRecoveryTime_ms"] = None
        agg["stddevRecoveryTime_ms"] = None
        agg["medianRecoveryTime_ms"] = None
        agg["maxRecoveryTime_ms"] = None

    # ── Per-strategy scheduler-event roll-up ──────────────────────────
    # Each iteration carries a metrics.recovery.schedulerEvents list of
    # {reason, ...} dicts.  Aggregating reason counts across iterations
    # makes "FailedScheduling fires 4x more often on adversarial than on
    # spread" — and the same for image-pull / BackOff / Killing — a
    # directly readable per-strategy number rather than something a
    # reader has to compute by hand from the iteration list.
    scheduler_event_totals: Dict[str, int] = {}
    scheduler_event_per_iter: Dict[str, List[int]] = {}
    iterations_with_events = 0
    for ir in iteration_results:
        events = (ir.get("metrics", {}).get("recovery", {}) or {}).get("schedulerEvents")
        if not events:
            continue
        iterations_with_events += 1
        per_iter_counts: Dict[str, int] = {}
        for e in events:
            reason = e.get("reason") if isinstance(e, dict) else None
            if not reason:
                continue
            scheduler_event_totals[reason] = scheduler_event_totals.get(reason, 0) + 1
            per_iter_counts[reason] = per_iter_counts.get(reason, 0) + 1
        for reason, count in per_iter_counts.items():
            scheduler_event_per_iter.setdefault(reason, []).append(count)

    if scheduler_event_totals:
        # `meanPerIteration` denominates by the number of iterations that
        # carried any events, not by total iterations — this prevents a
        # silent zero from non-recording iterations (e.g. probe-only runs)
        # from biasing the per-strategy attribution downward.
        agg["schedulerEventCounts"] = {
            reason: {
                "total": total,
                "meanPerIteration": round(
                    statistics.mean(scheduler_event_per_iter.get(reason, [0])), 2
                ),
                "maxPerIteration": max(scheduler_event_per_iter.get(reason, [0])),
                "iterationsObserved": len(scheduler_event_per_iter.get(reason, [])),
            }
            for reason, total in scheduler_event_totals.items()
        }
        agg["schedulerEventIterationsCovered"] = iterations_with_events

    route_view_agg = _aggregate_route_views(iteration_results)
    if route_view_agg:
        agg["routeViewAggregate"] = route_view_agg

    # ── OOMKill / restart roll-up across iterations ──────────────────────
    # _collect_pod_status records totalOOMKills and totalRestarts per
    # iteration.  Without a per-strategy total, "colocate produced 4×
    # more OOMKills than spread" cannot be read off the summary — a
    # reader had to walk the iterations list by hand.
    oom_per_iter: List[int] = []
    restart_per_iter: List[int] = []
    iters_with_oom = 0
    iters_with_restart = 0
    for ir in iteration_results:
        ps = (ir.get("metrics") or {}).get("podStatus") or {}
        oom = ps.get("totalOOMKills")
        if isinstance(oom, (int, float)):
            oom_per_iter.append(int(oom))
            if int(oom) > 0:
                iters_with_oom += 1
        restarts = ps.get("totalRestarts")
        if isinstance(restarts, (int, float)):
            restart_per_iter.append(int(restarts))
            if int(restarts) > 0:
                iters_with_restart += 1

    if oom_per_iter:
        agg["totalOOMKills"] = sum(oom_per_iter)
        agg["meanOOMKillsPerIteration"] = round(statistics.mean(oom_per_iter), 2)
        agg["maxOOMKillsPerIteration"] = max(oom_per_iter)
        agg["iterationsWithOOMKills"] = iters_with_oom
    if restart_per_iter:
        agg["totalRestarts"] = sum(restart_per_iter)
        agg["meanRestartsPerIteration"] = round(statistics.mean(restart_per_iter), 2)
        agg["maxRestartsPerIteration"] = max(restart_per_iter)
        agg["iterationsWithRestarts"] = iters_with_restart

    node_pressure = _aggregate_node_pressure_events(iteration_results)
    if node_pressure:
        agg["nodePressureEvents"] = node_pressure

    # Per-iteration experimentDuration_s — the end-to-end wall-clock of
    # the chaos window.  Aggregating across iterations lets a defender
    # ask "did this strategy's runs take noticeably longer than that
    # one's" and surfaces between-iteration cluster slow-down.
    durations: List[float] = []
    for ir in iteration_results:
        d = ir.get("experimentDuration_s")
        if isinstance(d, (int, float)):
            durations.append(float(d))
    if durations:
        agg["meanExperimentDuration_s"] = round(statistics.mean(durations), 1)
        agg["maxExperimentDuration_s"] = round(max(durations), 1)
        agg["minExperimentDuration_s"] = round(min(durations), 1)
        if len(durations) > 1:
            agg["stddevExperimentDuration_s"] = round(statistics.stdev(durations), 1)

    # Locust failure-class roll-up: aggregate errorRate already tells
    # us *how often* requests failed; failureClasses tells us *why*.
    # Connection refused vs timeout vs HTTP 5xx have very different
    # mechanisms (network programming SLO breach, kernel conntrack
    # churn, app circuit breaker).  Aggregating per (error, name) key
    # across iterations makes "colocate hit conntrack-timeouts on every
    # iteration, spread never did" a single number per strategy.
    failure_totals: Dict[str, Dict[str, Any]] = {}
    failure_iters_observed: Dict[str, int] = {}
    for ir in iteration_results:
        lg = ir.get("loadGeneration") or {}
        stats_block = lg.get("stats") or {}
        classes = stats_block.get("failureClasses") or []
        if not isinstance(classes, list):
            continue
        per_iter_seen: set = set()
        for entry in classes:
            if not isinstance(entry, dict):
                continue
            error = entry.get("error") or ""
            name = entry.get("name") or ""
            occ = entry.get("occurrences")
            if not isinstance(occ, (int, float)):
                continue
            key_str = f"{error} | {name}" if name else error
            if not key_str:
                continue
            bucket = failure_totals.setdefault(
                key_str,
                {
                    "error": error,
                    "name": name,
                    "totalOccurrences": 0,
                    "iterationsObserved": 0,
                },
            )
            bucket["totalOccurrences"] += int(occ)
            per_iter_seen.add(key_str)
        for key_str in per_iter_seen:
            failure_iters_observed[key_str] = failure_iters_observed.get(key_str, 0) + 1

    if failure_totals:
        for key_str, count in failure_iters_observed.items():
            failure_totals[key_str]["iterationsObserved"] = count
        agg["loadFailureClasses"] = sorted(
            failure_totals.values(), key=lambda v: -v["totalOccurrences"]
        )

    # Distribution-shape signal.  Fixed bucket boundaries make per-strategy
    # histograms directly comparable.  Mean + stddev + CV tell us spread;
    # the histogram tells us *shape* — bimodal (a few catastrophic recoveries
    # on top of a steady fast tier) looks the same as unimodal-with-noise by
    # stddev alone but very different here.
    if all_recovery_times:
        agg["recoveryTimeHistogram_ms"] = _bucket_recovery_times(all_recovery_times)

    return agg


def _aggregate_route_views(
    iteration_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per-route aggregation of ``routeView`` across iterations.

    Each iteration's ``routeView`` carries a Locust (outside-cluster) and
    a LatencyProber (in-pod) view per route.  This roll-up sums Locust
    request/failure counts, averages Locust + LatencyProber p95s, and
    reports how many iterations each route was observed in — so
    "during-chaos /cart p95 ran 3× higher under colocate than spread"
    becomes a directly readable per-strategy number.
    """
    by_route: Dict[str, Dict[str, Any]] = {}
    for ir in iteration_results:
        rv = ir.get("routeView") or []
        for entry in rv:
            if not isinstance(entry, dict):
                continue
            route = entry.get("route")
            if not route:
                continue
            bucket = by_route.setdefault(
                route,
                {
                    "locust_requests": [],
                    "locust_failures": [],
                    "locust_p95": [],
                    "lp_phase_p95": {},
                    "iterations": 0,
                },
            )
            bucket["iterations"] += 1

            loc = entry.get("locust")
            if isinstance(loc, dict):
                req = loc.get("requests")
                if isinstance(req, (int, float)):
                    bucket["locust_requests"].append(float(req))
                fail = loc.get("failures")
                if isinstance(fail, (int, float)):
                    bucket["locust_failures"].append(float(fail))
                p95 = loc.get("p95ResponseTime_ms")
                if isinstance(p95, (int, float)):
                    bucket["locust_p95"].append(float(p95))

            lp = entry.get("latencyProber")
            if isinstance(lp, dict):
                for phase_name, phase_data in lp.items():
                    if not isinstance(phase_data, dict):
                        continue
                    p95 = phase_data.get("p95_ms") or phase_data.get("p95ResponseTime_ms")
                    if isinstance(p95, (int, float)):
                        bucket["lp_phase_p95"].setdefault(phase_name, []).append(float(p95))

    out: List[Dict[str, Any]] = []
    for route, bucket in by_route.items():
        entry_out: Dict[str, Any] = {
            "route": route,
            "iterations": bucket["iterations"],
        }
        if bucket["locust_requests"] or bucket["locust_failures"] or bucket["locust_p95"]:
            locust_out: Dict[str, Any] = {}
            if bucket["locust_requests"]:
                locust_out["totalRequests"] = int(sum(bucket["locust_requests"]))
            if bucket["locust_failures"]:
                locust_out["totalFailures"] = int(sum(bucket["locust_failures"]))
            if bucket["locust_p95"]:
                locust_out["meanP95_ms"] = round(statistics.mean(bucket["locust_p95"]), 1)
                locust_out["iterationsObserved"] = len(bucket["locust_p95"])
            entry_out["locust"] = locust_out

        if bucket["lp_phase_p95"]:
            lp_out: Dict[str, Any] = {}
            for phase_name, values in bucket["lp_phase_p95"].items():
                lp_out[phase_name] = {
                    "meanP95_ms": round(statistics.mean(values), 1),
                    "iterationsObserved": len(values),
                }
            entry_out["latencyProber"] = lp_out
        out.append(entry_out)

    # Stable order: Locust-side routes first (load-generator perspective),
    # then LatencyProber-only routes alphabetically — matches build_route_view.
    out.sort(key=lambda r: (0 if "locust" in r else 1, r["route"]))
    return out


def summarise_placement_match_rates(
    strategies: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Per-strategy intent-vs-actual placement match rate roll-up.

    Surfaces ``placement.metadata.intendedActualDiff.matchRate`` for every
    strategy that captured one — produced by ``PlacementMutator.apply_strategy``
    after the rollout settles.  A strategy that returns matchRate=1.0
    placed every deployment as intended; lower values mean the scheduler
    overrode the nodeSelector (e.g. taint, resource fit, topology spread
    failure).  The thesis's per-strategy ranking only holds if the
    intended placement actually applied; this is the verification.

    Returns ``{strategy_name: {matchRate, matched, mismatched}}`` — the
    counts are convenient for downstream rendering.  Strategies without
    an intent-vs-actual diff (baseline, default) are omitted.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for name, sdata in (strategies or {}).items():
        placement = (sdata or {}).get("placement") or {}
        metadata = placement.get("metadata") or {}
        diff = metadata.get("intendedActualDiff")
        if not isinstance(diff, dict):
            continue
        match_rate = diff.get("matchRate")
        if match_rate is None:
            continue
        out[name] = {
            "matchRate": match_rate,
            "matched": len(diff.get("matched") or []),
            "mismatched": len(diff.get("mismatched") or []),
        }
    return out


# K8s kubelet conditions that fire ``status="True"`` when the node is
# under pressure.  ``Ready`` is intentionally excluded — it's the
# inverse of an event (False means trouble).
_NODE_PRESSURE_CONDITIONS = (
    "MemoryPressure",
    "DiskPressure",
    "PIDPressure",
    "NetworkUnavailable",
)


def _aggregate_node_pressure_events(
    iteration_results: List[Dict[str, Any]],
) -> Dict[str, Dict[str, int]]:
    """Per-strategy node-pressure event counts.

    A condition fires when its ``status`` field equals ``"True"`` on the
    kubelet's node-status report.  Counts two things per condition:

    * ``iterationsWithEvent`` — number of iterations where *at least one*
      hosting node had this condition firing.  Distinguishes "one bad
      iteration" from "every iteration was under memory pressure".
    * ``totalNodeEvents`` — total ``(iteration, node)`` pairs where the
      condition fired.  Captures fan-out across nodes within an iteration
      (a `spread` placement under pressure on all 4 workers is worse
      than a `colocate` placement under pressure on just one).

    Reads from both ``metrics.nodeInfo`` (single hosting node, the
    pre-existing field) and ``metrics.nodeInfoAll`` (every hosting node,
    added separately).  Returns ``{}`` when no iteration carried either —
    the caller omits the block in that case.
    """
    by_condition: Dict[str, Dict[str, int]] = {
        c: {"iterationsWithEvent": 0, "totalNodeEvents": 0} for c in _NODE_PRESSURE_CONDITIONS
    }
    saw_any_node_info = False
    for ir in iteration_results:
        metrics = ir.get("metrics") or {}
        nodes: List[Dict[str, Any]] = []
        node_info_all = metrics.get("nodeInfoAll")
        if isinstance(node_info_all, dict) and node_info_all:
            saw_any_node_info = True
            for entry in node_info_all.values():
                if isinstance(entry, dict):
                    nodes.append(entry)
        else:
            single = metrics.get("nodeInfo")
            if isinstance(single, dict) and single:
                saw_any_node_info = True
                nodes.append(single)

        if not nodes:
            continue

        fired_this_iter: set = set()
        for entry in nodes:
            conditions = entry.get("conditions") or {}
            if not isinstance(conditions, dict):
                continue
            for cond_name in _NODE_PRESSURE_CONDITIONS:
                cond = conditions.get(cond_name)
                if isinstance(cond, dict) and cond.get("status") == "True":
                    by_condition[cond_name]["totalNodeEvents"] += 1
                    fired_this_iter.add(cond_name)
        for cond_name in fired_this_iter:
            by_condition[cond_name]["iterationsWithEvent"] += 1

    if not saw_any_node_info:
        return {}
    return by_condition


def _bucket_recovery_times(values: List[float]) -> Dict[str, int]:
    """Return ``{bucket_label: count}`` for recovery-time samples.

    Bucket boundaries cover the realistic chaosprobe-recovery range:

    * < 500ms             — pre-pulled image, healthy node, scheduler hot
    * 500-1000ms          — typical kubelet bring-up
    * 1000-2000ms         — modest scheduler stall or container init
    * 2000-5000ms         — significant pull / mount / probe wait
    * 5000-10000ms        — pathological pull, restart loop, eviction
    * >= 10000ms          — failure mode (recovery effectively timed out)

    Labels are returned in stable order so a downstream consumer
    iterates them as histogram bars.
    """
    labels = (
        "lt_500ms",
        "500_to_1000ms",
        "1000_to_2000ms",
        "2000_to_5000ms",
        "5000_to_10000ms",
        "gte_10000ms",
    )
    counts = {label: 0 for label in labels}
    for v in values:
        if v < 500:
            counts["lt_500ms"] += 1
        elif v < 1000:
            counts["500_to_1000ms"] += 1
        elif v < 2000:
            counts["1000_to_2000ms"] += 1
        elif v < 5000:
            counts["2000_to_5000ms"] += 1
        elif v < 10000:
            counts["5000_to_10000ms"] += 1
        else:
            counts["gte_10000ms"] += 1
    return counts
