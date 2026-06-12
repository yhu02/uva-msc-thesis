#!/usr/bin/env python3
"""M2 A/A calibration analysis (01-PREREGISTRATION.md §A/A calibration protocol).

Implements the amended A/A protocol (PR #261): the A/A block is a
**variance-component estimator plus a qualitative pipeline sanity check**,
NOT a false-positive-rate gate — no numeric "FPR <= alpha" criterion is
registered.  Any statistically significant A/A finding is reported as
``A/A FINDING — investigate`` (the amended rule: diagnose, fix the
doctor gates / taint rules / instrumentation, rerun the block; the halt
criterion lives in §Stopping rules 2).

What it does
------------
1. **Discovery + pairing.** Loads every ``<results-dir>/*/summary.json``
   carrying a ``v2Session`` block and groups sessions into A/A pairs by
   the identical-cell key ``(fault, solverSeed, replicas, mode, levels,
   workers)`` — two runs of the same cell are an A/A pair (chronological
   chunking within a cell; odd / unpaired sessions are warned about and
   excluded from pairing).  Levels a pair does not share, and levels whose
   condition was not accepted at apply time (``perLevel[].accepted`` false:
   rejected, drifted, or never executed), are excluded with a warning —
   never silently included or dropped.
2. **Per-pair deltas + null tests.** Per pair, per accepted f-level, per
   registered metric the A-vs-B delta is computed; per metric the
   level-paired samples go through Wilcoxon signed-rank (>= 5 paired
   levels) or the exact sign test (< 5).  Any p < alpha is an A/A
   finding.  *Power note (registered 5-level grid):* the most extreme
   exact two-sided p on 5 paired levels is 0.0625 (> 0.05), so an
   exactly-significant per-pair result does not exist at the default
   alpha; a per-pair finding can still fire through the helper's
   tie-corrected normal approximation (five same-sign equal-magnitude
   deltas give p = 0.0369 — plausible for the 1-dp-rounded score).  Such
   a finding is a trigger to inspect the exact sign test reported
   alongside (nested under ``test.sign_test``) during the investigation
   it opens.  p-values are the shared helpers' 4-dp rounded values.
3. **Cross-pair drift test.** Per metric, each pair is collapsed to one
   mean level-delta and the per-pair summaries are tested against zero
   (Wilcoxon at >= 5 pairs, exact sign test below) — the detector for a
   systematic first-vs-second-session drift that respects pair-level
   exchangeability.  Naive pooling of level-deltas across pairs is
   deliberately NOT done: within a pair the level-deltas share that
   pair's realized between-session offset, so a pooled test over-fires
   exactly when between-session noise is ordinary (see
   :func:`cross_pair_tests`).  At the registered minimum of 3 pairs the
   cross-pair sign test bottoms out at p = 0.25 — the pre-registration
   itself concedes a handful of A/A pairs cannot certify any alpha; the
   detector gains power as pairs accumulate.
4. **liveAchievedF identity check.** An identical solver seed must
   reproduce identical placements, so the recorded per-level
   ``liveAchievedF`` must be EXACTLY equal within a pair (on accepted
   levels); a mismatch can never be noise and is flagged loudly as a
   pipeline bug.
5. **Variance components.** Across all paired sessions, per metric: a
   nested decomposition into between pair-level cells /
   between-session-within-pair / between-iteration, computed by the
   cluster-bootstrap ICC helper
   (:func:`chaosprobe.metrics.statistics.icc_bootstrap`) on cells
   ``{((pair, level), session): per-iteration values}`` — its
   ``sig2_strat`` / ``sig2_run`` / ``sig2_iter`` are exactly the three
   nested ANOVA-style mean-square components (see ``_icc_point``).  The
   f-level rides inside the grouping factor, so designed level effects
   never inflate the within-pair noise estimates.
6. **Noise band table.** Per metric per level, the within-pair
   between-session SD (ddof=1 inside each pair, RMS-pooled across
   pairs) — the numbers the M2 power analysis and the SESOI
   finalization consume.

Registered delta metrics
------------------------
- ``ew_p95_during_ms`` — during-chaos east-west tail latency: mean over
  inter-service (``a->b``) routes of
  ``aggregated.routeViewAggregate[].latencyProber.during-chaos.meanP95_ms``
  (the same canonical field ``scripts/contention_routes.py`` reads).
- ``conntrack_flush_pct`` — the v1 mechanism definition
  (``scripts/mechanism_metrics.py``): ``(pre_mean - during_mean) /
  pre_mean * 100`` on the Prometheus phase aggregate
  ``conntrack_entries_per_node``.
- ``udp_conntrack_drop_pct`` — the same pre-vs-during drop computed on
  per-node UDP entry counts from ``metrics.conntrackProtocolSamples``
  (where present; the per-protocol prober is newer than some runs).
- ``score`` — ``aggregated.meanResilienceScore_healthyOnly`` (falling
  back to ``meanResilienceScore``), with per-iteration scores filtered
  the same way the aggregate is (ERROR verdicts out; tainted iterations
  out unless every valid iteration is tainted) feeding the
  between-iteration variance component.  The raw ``perIterationScores``
  list is NOT used: it includes the fabricated 0.0 scores of ERROR
  iterations, which are not valid resilience measurements.

``liveAchievedF`` is not delta-tested: it is the exact-equality
pipeline check (4) above.

Usage
-----
    uv run python scripts/m2_aa_analysis.py --results-dir results/v2-aa \\
        [--json out.json] [--alpha 0.05]

Exit codes: 0 clean and sufficient; 1 when any A/A finding or
liveAchievedF mismatch demands investigation; 2 when fewer than the
pre-registered minimum pairs were found (and nothing demands
investigation yet).
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics as st
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from chaosprobe.metrics.statistics import icc_bootstrap, sign_test, wilcoxon_signed_rank

#: Analysis-output schema identifier (bump on breaking shape changes).
SCHEMA = "chaosprobe/m2-aa-analysis/v1"

#: Pre-registered minimum number of A/A session pairs (§A/A calibration).
REGISTERED_MIN_PAIRS = 3

#: Default threshold for the "any statistically significant A/A finding" rule.
DEFAULT_ALPHA = 0.05

#: Below this many paired observations the Wilcoxon normal approximation is
#: meaningless; the exact sign test is used instead.
WILCOXON_MIN_LEVELS = 5

#: Registered A/A delta metrics (liveAchievedF is the separate identity check).
METRICS = ("ew_p95_during_ms", "conntrack_flush_pct", "udp_conntrack_drop_pct", "score")

#: Documented method string embedded in the JSON output (spec: document it).
VARIANCE_METHOD = (
    "nested variance components via chaosprobe.metrics.statistics.icc_bootstrap on "
    "cells {((pair, level), session): per-iteration values}: betweenPairLevel = "
    "population variance of pair-level cell grand means (designed f-level effects are "
    "absorbed here), betweenSessionWithinPair = mean over pair-level cells of the "
    "population variance of session means, betweenIteration = mean within-session "
    "population variance; metrics without per-iteration data contribute no iteration "
    "component"
)


@dataclass(frozen=True)
class PairKey:
    """The identical-cell key two sessions must share to form an A/A pair.

    Mirrors the pre-registration's cell definition ("same f, r, mode,
    fault; nothing varied"): the fault is part of the key, so sessions of
    different fault experiments can never pair as A/A.  ``levels`` and
    ``workers`` are stored sorted — the applied order comes from the
    recorded order seed, not from these lists, so ordering differences in
    the CLI arguments must not split a cell.
    """

    fault: str
    solver_seed: int
    replicas: int
    mode: str
    levels: Tuple[float, ...]
    workers: Tuple[str, ...]

    def label(self) -> str:
        """Human-readable cell descriptor (unique per key)."""
        levels = ",".join(f"{level:g}" for level in self.levels)
        return (
            f"fault={self.fault} seed={self.solver_seed} r={self.replicas} "
            f"mode={self.mode} levels={levels} workers={','.join(self.workers)}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """JSON-ready form."""
        return {
            "fault": self.fault,
            "solverSeed": self.solver_seed,
            "replicas": self.replicas,
            "mode": self.mode,
            "levels": list(self.levels),
            "workers": list(self.workers),
        }


@dataclass
class LevelObs:
    """One f-level of one session: identity fields + metric values."""

    condition: str
    target_f: float
    live_achieved_f: Optional[float]
    accepted: bool
    rejection_reasons: List[str]
    values: Dict[str, Optional[float]]
    score_iterations: List[float]


@dataclass
class Session:
    """One loaded v2 session summary, reduced to what the A/A analysis needs."""

    run: str
    run_id: Optional[str]
    timestamp: Optional[str]
    key: PairKey
    levels: Dict[str, LevelObs]


@dataclass
class Pair:
    """An A/A pair: two sessions of the identical cell."""

    label: str
    key: PairKey
    a: Session
    b: Session


# ──────────────────────────────────────────────────────────────────────
# Metric extraction (canonical field paths — see module docstring)
# ──────────────────────────────────────────────────────────────────────


def east_west_during_p95(strategy: Dict[str, Any]) -> Optional[float]:
    """Mean during-chaos p95 (ms) over the east-west (``a->b``) routes.

    Reads ``aggregated.routeViewAggregate[].latencyProber.during-chaos
    .meanP95_ms`` — the same canonical per-route field
    ``scripts/contention_routes.py`` reads for the v1 contention analysis.
    """
    rva = ((strategy.get("aggregated") or {}).get("routeViewAggregate")) or []
    values: List[float] = []
    for entry in rva:
        route = entry.get("route") or ""
        if "->" not in route:
            continue
        p95 = (((entry.get("latencyProber") or {}).get("during-chaos")) or {}).get("meanP95_ms")
        if p95 is not None:
            values.append(float(p95))
    return st.mean(values) if values else None


def _phase_mean(strategy: Dict[str, Any], metric: str, phase: str) -> Optional[float]:
    """Mean of a Prometheus phase-aggregate metric (mechanism_metrics.py path)."""
    phases = ((strategy.get("metrics") or {}).get("prometheus") or {}).get("phases") or {}
    entry = ((phases.get(phase) or {}).get("metrics") or {}).get(metric)
    return entry.get("mean") if isinstance(entry, dict) else None


def conntrack_flush_pct(strategy: Dict[str, Any]) -> Optional[float]:
    """Conntrack flush percentage per the v1 mechanism definition.

    ``(pre_mean - during_mean) / pre_mean * 100`` of
    ``conntrack_entries_per_node`` (positive = entries flushed), exactly as
    ``scripts/mechanism_metrics.py`` computes M1.
    """
    pre = _phase_mean(strategy, "conntrack_entries_per_node", "pre-chaos")
    during = _phase_mean(strategy, "conntrack_entries_per_node", "during-chaos")
    if pre and during is not None:
        return (pre - during) / pre * 100.0
    return None


def udp_conntrack_drop_pct(strategy: Dict[str, Any]) -> Optional[float]:
    """Per-protocol UDP drop percentage from ``metrics.conntrackProtocolSamples``.

    The v1 flush definition applied to the per-node UDP entry counts the
    protocol prober samples: ``(pre_mean - during_mean) / pre_mean * 100``
    over the ``proto == "udp"`` sample counts in each phase.  ``None`` when
    the prober was absent or either phase has no UDP samples.
    """
    samples = ((strategy.get("metrics") or {}).get("conntrackProtocolSamples")) or []
    by_phase: Dict[str, List[float]] = {"pre-chaos": [], "during-chaos": []}
    for sample in samples:
        if sample.get("proto") != "udp":
            continue
        phase = sample.get("phase")
        if phase in by_phase:
            by_phase[phase].append(float(sample["count"]))
    pre, during = by_phase["pre-chaos"], by_phase["during-chaos"]
    if pre and during and st.mean(pre) > 0:
        pre_mean = st.mean(pre)
        return (pre_mean - st.mean(during)) / pre_mean * 100.0
    return None


def aggregate_score(strategy: Dict[str, Any]) -> Optional[float]:
    """The healthy-only aggregate resilience score (valid-mean fallback).

    ``aggregated.meanResilienceScore_healthyOnly`` excludes both ERROR
    iterations and tainted (pre-chaos-degraded) iterations — the estimand
    the pre-registration's "no result from a tainted iteration" rule
    implies.  Older summaries without the healthy-only field fall back to
    ``meanResilienceScore`` (ERROR-excluded only).
    """
    aggregated = strategy.get("aggregated") or {}
    score = aggregated.get("meanResilienceScore_healthyOnly")
    if score is None:
        score = aggregated.get("meanResilienceScore")
    return float(score) if score is not None else None


def healthy_score_iterations(strategy: Dict[str, Any]) -> List[float]:
    """Per-iteration scores filtered exactly like the aggregate estimand.

    Mirrors ``aggregate_iterations``: ERROR-verdict iterations are
    excluded (their fabricated 0.0 is not a valid resilience
    measurement), then tainted iterations are excluded unless every valid
    iteration is tainted (the aggregation's healthy-or-valid fallback).
    The raw ``aggregated.perIterationScores`` list is deliberately NOT
    used — it includes the ERROR scores.
    """
    records = strategy.get("iterations") or []
    valid = [
        record
        for record in records
        if record.get("verdict") != "ERROR" and record.get("resilienceScore") is not None
    ]
    healthy = [record for record in valid if record.get("preChaosHealthy", True)]
    chosen = healthy if healthy else valid
    return [float(record["resilienceScore"]) for record in chosen]


# ──────────────────────────────────────────────────────────────────────
# Discovery + pairing
# ──────────────────────────────────────────────────────────────────────


def parse_session(run: str, summary: Dict[str, Any], warnings: List[str]) -> Optional[Session]:
    """Reduce one summary.json to a :class:`Session`, or ``None`` (warned)."""
    v2 = summary.get("v2Session")
    if not isinstance(v2, dict):
        warnings.append(f"{run}: no v2Session block — not a v2 session summary, skipped")
        return None

    faults = summary.get("faults") or {}
    if faults:
        fault_names = sorted(faults)
        if len(fault_names) > 1:
            warnings.append(
                f"{run}: {len(fault_names)} fault blocks — analyzing '{fault_names[0]}' only"
            )
        fault = fault_names[0]
        strategies = (faults[fault] or {}).get("strategies") or {}
    else:
        fault = ""
        strategies = summary.get("strategies") or {}

    try:
        key = PairKey(
            fault=fault,
            solver_seed=int(v2["solverSeed"]),
            replicas=int(v2["replicas"]),
            mode=str(v2["mode"]),
            levels=tuple(sorted(float(level) for level in v2["levels"])),
            workers=tuple(sorted(str(worker) for worker in v2["workers"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        warnings.append(f"{run}: malformed v2Session cell fields ({exc!r}) — skipped")
        return None

    levels: Dict[str, LevelObs] = {}
    for record in v2.get("perLevel") or []:
        condition = record.get("condition")
        if not condition:
            continue
        strategy = strategies.get(condition) or {}
        levels[condition] = LevelObs(
            condition=condition,
            target_f=float(record.get("targetF") or 0.0),
            live_achieved_f=record.get("liveAchievedF"),
            accepted=bool(record.get("accepted", True)),
            rejection_reasons=[str(reason) for reason in record.get("rejectionReasons") or []],
            values={
                "ew_p95_during_ms": east_west_during_p95(strategy),
                "conntrack_flush_pct": conntrack_flush_pct(strategy),
                "udp_conntrack_drop_pct": udp_conntrack_drop_pct(strategy),
                "score": aggregate_score(strategy),
            },
            score_iterations=healthy_score_iterations(strategy),
        )
    return Session(
        run=run,
        run_id=summary.get("runId"),
        timestamp=summary.get("timestamp"),
        key=key,
        levels=levels,
    )


def discover_sessions(results_dir: str) -> Tuple[List[Session], List[str]]:
    """Load every ``<results-dir>/*/summary.json`` into sessions (+ warnings)."""
    sessions: List[Session] = []
    warnings: List[str] = []
    for path in sorted(glob.glob(os.path.join(results_dir, "*", "summary.json"))):
        run = os.path.basename(os.path.dirname(path))
        try:
            with open(path) as fh:
                summary = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"{run}: unreadable summary.json ({exc}) — skipped")
            continue
        session = parse_session(run, summary, warnings)
        if session is not None:
            sessions.append(session)
    return sessions, warnings


def pair_sessions(sessions: List[Session], warnings: List[str]) -> List[Pair]:
    """Group sessions by identical cell key and chunk chronologically into pairs.

    A cell with an odd session count leaves its newest session unpaired —
    warned about, never silently dropped into a pair.
    """
    groups: Dict[PairKey, List[Session]] = defaultdict(list)
    for session in sessions:
        groups[session.key].append(session)
    pairs: List[Pair] = []
    for key in sorted(groups, key=lambda k: k.label()):
        members = sorted(groups[key], key=lambda s: (s.timestamp or "", s.run))
        for i in range(0, len(members) - 1, 2):
            pairs.append(
                Pair(label=f"pair-{len(pairs) + 1:02d}", key=key, a=members[i], b=members[i + 1])
            )
        if len(members) % 2:
            warnings.append(
                f"{members[-1].run}: unpaired session in cell [{key.label()}] "
                f"({len(members)} session(s) in cell) — excluded from pairing"
            )
    return pairs


# ──────────────────────────────────────────────────────────────────────
# Null tests (shared by the per-pair and cross-pair paths)
# ──────────────────────────────────────────────────────────────────────


def _null_test(
    values_a: List[float], values_b: List[float]
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[float]]:
    """Wilcoxon (>= WILCOXON_MIN_LEVELS paired obs) or exact sign test.

    Returns ``(test_dict, method, p)`` — all ``None`` for an empty sample.
    """
    n = len(values_a)
    if n == 0:
        return None, None, None
    if n >= WILCOXON_MIN_LEVELS:
        test = dict(wilcoxon_signed_rank(values_a, values_b))
        return test, "wilcoxon_signed_rank", float(test["p_two_sided"])  # type: ignore[arg-type]
    test = dict(sign_test(values_a, values_b))
    return test, "sign_test", float(test["p_two_sided"])  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────
# Per-pair analysis (deltas, null tests, liveAchievedF identity)
# ──────────────────────────────────────────────────────────────────────


def analyze_pair(
    pair: Pair, alpha: float, warnings: List[str]
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """One pair's record: per-level deltas, per-metric null test, identity check.

    Levels not shared by both sessions, or not accepted in either session
    (rejected / drifted / never-executed conditions), are excluded with a
    warning — registered-invalid data must not enter the calibration.

    Returns ``(record, findings, pipeline_bugs)``; each metric entry
    carries a ``meanDelta`` (the pair's mean level-delta) that feeds the
    cross-pair drift test.
    """
    a, b = pair.a, pair.b
    shared = sorted(set(a.levels) & set(b.levels), key=lambda c: a.levels[c].target_f)
    not_shared = sorted(set(a.levels) ^ set(b.levels))
    if not_shared:
        warnings.append(
            f"{pair.label}: level(s) {', '.join(not_shared)} present in only one "
            f"session — excluded from the pair analysis"
        )

    usable: List[str] = []
    not_accepted: Dict[str, List[str]] = {}
    for condition in shared:
        reasons: List[str] = []
        for session in (a, b):
            obs = session.levels[condition]
            if not obs.accepted:
                reasons.append(f"{session.run}: {', '.join(obs.rejection_reasons) or 'rejected'}")
        if reasons:
            not_accepted[condition] = reasons
            warnings.append(
                f"{pair.label}: level {condition} excluded — condition not accepted "
                f"({'; '.join(reasons)})"
            )
        else:
            usable.append(condition)

    pipeline_bugs: List[str] = []
    live: Dict[str, Any] = {}
    for condition in usable:
        live_a = a.levels[condition].live_achieved_f
        live_b = b.levels[condition].live_achieved_f
        equal = live_a == live_b  # exact, per the protocol; None == None is fine
        live[condition] = {"a": live_a, "b": live_b, "equal": equal}
        if not equal:
            pipeline_bugs.append(
                f"PIPELINE BUG — liveAchievedF mismatch in {pair.label} at {condition}: "
                f"{live_a!r} != {live_b!r} (an identical solver seed must reproduce "
                f"identical placements; this can never be noise)"
            )

    findings: List[str] = []
    metrics_out: Dict[str, Any] = {}
    for metric in METRICS:
        per_level: Dict[str, Any] = {}
        values_a: List[float] = []
        values_b: List[float] = []
        for condition in usable:
            value_a = a.levels[condition].values.get(metric)
            value_b = b.levels[condition].values.get(metric)
            delta = value_a - value_b if value_a is not None and value_b is not None else None
            per_level[condition] = {
                "a": value_a,
                "b": value_b,
                "delta": round(delta, 6) if delta is not None else None,
            }
            if delta is not None:
                values_a.append(value_a)
                values_b.append(value_b)
        test, method, p_value = _null_test(values_a, values_b)
        finding = p_value is not None and p_value < alpha
        if finding:
            findings.append(
                f"A/A FINDING — investigate: {pair.label} metric={metric} "
                f"{method} p={p_value} < alpha={alpha}"
            )
        mean_delta = st.mean(values_a) - st.mean(values_b) if values_a else None
        metrics_out[metric] = {
            "perLevel": per_level,
            "nLevelsTested": len(values_a),
            "meanDelta": round(mean_delta, 6) if mean_delta is not None else None,
            "method": method,
            "test": test,
            "p": p_value,
            "finding": finding,
        }

    record = {
        "pair": pair.label,
        "cell": pair.key.to_dict(),
        "sessions": [a.run, b.run],
        "levelsUsed": usable,
        "levelsNotShared": not_shared,
        "levelsNotAccepted": not_accepted,
        "liveAchievedF": {
            "matched": not pipeline_bugs,
            "perLevel": live,
            "mismatches": list(pipeline_bugs),
        },
        "metrics": metrics_out,
    }
    return record, findings, pipeline_bugs


def cross_pair_tests(
    mean_deltas: Dict[str, List[float]], alpha: float
) -> Tuple[Dict[str, Any], List[str]]:
    """Per metric, the cross-pair drift test: one mean level-delta per pair vs 0.

    Each pair is collapsed to its mean level-delta and the per-pair
    summaries are tested against zero (one-sample via the shared paired
    helpers: Wilcoxon at >= 5 pairs, exact sign test below).  A naive
    pooling of all level-deltas across pairs was deliberately rejected:
    the levels of one pair share that pair's realized between-session
    offset (exactly the ``betweenSessionWithinPair`` component this script
    estimates), so pooled level-deltas are not independent and a pooled
    Wilcoxon over-fires precisely when between-session noise is ordinary.
    Collapsing to one summary per pair respects the pair-level
    exchangeability; the cost is power — at the registered minimum of 3
    pairs the exact sign test's smallest two-sided p is 0.25, consistent
    with the pre-registration's concession that a handful of A/A pairs
    cannot certify any alpha.  The detector's power grows as pairs
    accumulate.
    """
    out: Dict[str, Any] = {}
    findings: List[str] = []
    for metric in METRICS:
        deltas = mean_deltas[metric]
        test, method, p_value = _null_test(deltas, [0.0] * len(deltas))
        finding = p_value is not None and p_value < alpha
        if finding:
            findings.append(
                f"A/A FINDING — investigate: cross-pair metric={metric} "
                f"{method} p={p_value} < alpha={alpha}"
            )
        out[metric] = {
            "nPairs": len(deltas),
            "meanDeltaPerPair": [round(delta, 6) for delta in deltas],
            "method": method,
            "test": test,
            "p": p_value,
            "finding": finding,
        }
    return out, findings


# ──────────────────────────────────────────────────────────────────────
# Variance components + noise band (the M2 power-analysis inputs)
# ──────────────────────────────────────────────────────────────────────


def _cell_values(metric: str, obs: LevelObs) -> List[float]:
    """The per-iteration values one session-level contributes for one metric.

    ``score`` uses the healthy-filtered per-iteration scores when present
    (that is what makes the between-iteration component estimable); every
    other metric — and a score without per-iteration records — contributes
    its single session-level value.
    """
    if metric == "score" and obs.score_iterations:
        return list(obs.score_iterations)
    value = obs.values.get(metric)
    return [value] if value is not None else []


def variance_components(pairs: List[Pair]) -> Dict[str, Any]:
    """Per-metric nested decomposition across all paired, accepted levels.

    See :data:`VARIANCE_METHOD` for the documented mapping of
    ``icc_bootstrap``'s components onto the A/A design.
    """
    out: Dict[str, Any] = {}
    for metric in METRICS:
        cells: Dict[Tuple[object, object], List[float]] = {}
        for pair in pairs:
            for session in (pair.a, pair.b):
                for condition, obs in session.levels.items():
                    if not obs.accepted:
                        continue
                    values = _cell_values(metric, obs)
                    if values:
                        cells[((pair.label, condition), session.run)] = values
        icc = icc_bootstrap(cells)
        out[metric] = {
            "betweenPairLevel": icc["sig2_strat"],
            "betweenSessionWithinPair": icc["sig2_run"],
            "betweenIteration": icc["sig2_iter"],
            "icc": icc["icc"],
            "iccCiLow": icc["ci_low"],
            "iccCiHigh": icc["ci_high"],
            "nPairLevelCells": icc["n_strategies"],
            "nObservations": icc["n_obs"],
        }
    return out


def noise_band(pairs: List[Pair]) -> List[Dict[str, Any]]:
    """Within-pair between-session SD per metric per level (the noise band).

    Per pair the two session values give a ddof=1 variance (``(a-b)^2 / 2``);
    pairs are pooled by the RMS (square root of the mean variance).  These
    SDs are the per-metric per-level noise bands the M2 power analysis and
    the SESOI finalization consume.  Only levels accepted in both sessions
    contribute.
    """
    by_cell: Dict[Tuple[str, str, float], List[Tuple[float, float]]] = defaultdict(list)
    for pair in pairs:
        shared = set(pair.a.levels) & set(pair.b.levels)
        for condition in shared:
            obs_a, obs_b = pair.a.levels[condition], pair.b.levels[condition]
            if not (obs_a.accepted and obs_b.accepted):
                continue
            for metric in METRICS:
                value_a = obs_a.values.get(metric)
                value_b = obs_b.values.get(metric)
                if value_a is not None and value_b is not None:
                    by_cell[(metric, condition, obs_a.target_f)].append((value_a, value_b))
    rows: List[Dict[str, Any]] = []
    for metric, condition, target_f in sorted(by_cell, key=lambda k: (METRICS.index(k[0]), k[2])):
        observed = by_cell[(metric, condition, target_f)]
        variances = [st.variance([value_a, value_b]) for value_a, value_b in observed]
        rows.append(
            {
                "metric": metric,
                "condition": condition,
                "targetF": target_f,
                "withinPairSessionSD": round(math.sqrt(st.mean(variances)), 6),
                "meanAbsDelta": round(
                    st.mean(abs(value_a - value_b) for value_a, value_b in observed), 6
                ),
                "nPairs": len(observed),
            }
        )
    return rows


# ──────────────────────────────────────────────────────────────────────
# Orchestration + report
# ──────────────────────────────────────────────────────────────────────


def analyze(results_dir: str, alpha: float) -> Dict[str, Any]:
    """The full A/A analysis as one JSON-ready dict."""
    sessions, warnings = discover_sessions(results_dir)
    pairs = pair_sessions(sessions, warnings)

    pair_records: List[Dict[str, Any]] = []
    findings: List[str] = []
    pipeline_bugs: List[str] = []
    mean_deltas: Dict[str, List[float]] = {metric: [] for metric in METRICS}
    for pair in pairs:
        record, pair_findings, pair_bugs = analyze_pair(pair, alpha, warnings)
        pair_records.append(record)
        findings.extend(pair_findings)
        pipeline_bugs.extend(pair_bugs)
        for metric in METRICS:
            mean_delta = record["metrics"][metric]["meanDelta"]
            if mean_delta is not None:
                mean_deltas[metric].append(mean_delta)
    cross_pair_out, cross_pair_findings = cross_pair_tests(mean_deltas, alpha)
    findings.extend(cross_pair_findings)

    return {
        "schema": SCHEMA,
        "resultsDir": results_dir,
        "alpha": alpha,
        "registeredMinPairs": REGISTERED_MIN_PAIRS,
        "varianceMethod": VARIANCE_METHOD,
        "sessions": [
            {
                "run": session.run,
                "runId": session.run_id,
                "timestamp": session.timestamp,
                "cell": session.key.to_dict(),
            }
            for session in sessions
        ],
        "warnings": warnings,
        "pairs": pair_records,
        "crossPairTests": cross_pair_out,
        "findings": findings,
        "pipelineBugs": pipeline_bugs,
        "varianceComponents": variance_components(pairs),
        "noiseBand": noise_band(pairs),
        "sufficiency": {
            "pairsFound": len(pairs),
            "registeredMinimum": REGISTERED_MIN_PAIRS,
            "sufficient": len(pairs) >= REGISTERED_MIN_PAIRS,
        },
    }


def _fmt(value: Optional[float], digits: int = 3) -> str:
    """Fixed-width numeric cell, ``-`` for absent."""
    return f"{value:.{digits}f}" if value is not None else "-"


def print_report(result: Dict[str, Any]) -> None:
    """Human-readable rendering of the analysis dict."""
    print(f"M2 A/A calibration analysis — {result['resultsDir']} (alpha={result['alpha']})")

    print(f"\n=== Sessions ({len(result['sessions'])}) ===")
    for session in result["sessions"]:
        print(f"  {session['run']}  runId={session['runId']}  ts={session['timestamp']}")

    if result["warnings"]:
        print("\n=== Warnings ===")
        for warning in result["warnings"]:
            print(f"  WARNING: {warning}")

    print(f"\n=== A/A pairs ({len(result['pairs'])}) ===")
    if not result["pairs"]:
        print("  (no pairs)")
    for record in result["pairs"]:
        cell = record["cell"]
        print(
            f"  {record['pair']}: {record['sessions'][0]} vs {record['sessions'][1]} "
            f"(fault={cell['fault']} seed={cell['solverSeed']} r={cell['replicas']} "
            f"mode={cell['mode']})"
        )
        live = record["liveAchievedF"]
        print(f"    liveAchievedF identity: {'OK' if live['matched'] else 'MISMATCH'}")
        for metric in METRICS:
            entry = record["metrics"][metric]
            verdict = "FINDING" if entry["finding"] else "ok"
            print(
                f"    {metric:<24} n={entry['nLevelsTested']} "
                f"method={entry['method'] or '-'} p={_fmt(entry['p'], 4)}  {verdict}"
            )

    print("\n=== Cross-pair drift test (one mean delta per pair vs 0) ===")
    for metric in METRICS:
        entry = result["crossPairTests"][metric]
        verdict = "FINDING" if entry["finding"] else "ok"
        print(
            f"  {metric:<24} n={entry['nPairs']} "
            f"method={entry['method'] or '-'} p={_fmt(entry['p'], 4)}  {verdict}"
        )

    if result["pipelineBugs"]:
        print("\n=== PIPELINE BUGS (liveAchievedF identity violated) ===")
        for bug in result["pipelineBugs"]:
            print(f"  {bug}")

    if result["findings"]:
        print("\n=== A/A findings (amended protocol: investigate, fix, rerun) ===")
        for finding in result["findings"]:
            print(f"  {finding}")
    else:
        print("\nNo A/A findings at alpha — qualitative sanity check passed.")

    print("\n=== Variance components (per metric; see varianceMethod in JSON) ===")
    header = (
        f"  {'metric':<24}{'between-pair-level':>20}{'between-session':>17}"
        f"{'between-iter':>14}{'ICC':>8}"
    )
    print(header)
    for metric in METRICS:
        comp = result["varianceComponents"][metric]
        print(
            f"  {metric:<24}{_fmt(comp['betweenPairLevel']):>20}"
            f"{_fmt(comp['betweenSessionWithinPair']):>17}"
            f"{_fmt(comp['betweenIteration']):>14}{_fmt(comp['icc']):>8}"
        )

    print("\n=== Noise band (within-pair between-session SD, per metric per level) ===")
    print(f"  {'metric':<24}{'level':>8}{'SD':>12}{'mean|d|':>12}{'pairs':>7}")
    for row in result["noiseBand"]:
        print(
            f"  {row['metric']:<24}{row['targetF']:>8.2f}{row['withinPairSessionSD']:>12.4f}"
            f"{row['meanAbsDelta']:>12.4f}{row['nPairs']:>7}"
        )

    sufficiency = result["sufficiency"]
    verdict = "SUFFICIENT" if sufficiency["sufficient"] else "INSUFFICIENT"
    print(
        f"\nA/A sufficiency: {sufficiency['pairsFound']} pair(s) found vs pre-registered "
        f">= {sufficiency['registeredMinimum']} — {verdict}"
    )


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface (also exercised by tests)."""
    parser = argparse.ArgumentParser(
        description=(
            "M2 A/A calibration analysis (pre-registration §A/A, amended in #261): "
            "pairs identical-cell v2 sessions, computes per-level per-metric deltas "
            "with Wilcoxon/sign null tests (any p < alpha => 'A/A FINDING — "
            "investigate'), checks liveAchievedF exact identity within pairs, and "
            "emits the variance-component decomposition + noise band table the M2 "
            "power analysis and SESOI finalization consume."
        ),
    )
    parser.add_argument(
        "--results-dir",
        default="results/v2-aa",
        help="directory of <run>/summary.json v2 session outputs (default results/v2-aa)",
    )
    parser.add_argument(
        "--json",
        help="optional: write the full analysis dict to this path for downstream tooling",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=DEFAULT_ALPHA,
        help=f"significance threshold for the A/A finding rule (default {DEFAULT_ALPHA})",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Run the analysis; exit codes are documented in the module docstring."""
    args = build_parser().parse_args(argv)
    result = analyze(args.results_dir, args.alpha)
    print_report(result)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"\nJSON written to {args.json}")
    if result["findings"] or result["pipelineBugs"]:
        return 1
    if not result["sufficiency"]["sufficient"]:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
