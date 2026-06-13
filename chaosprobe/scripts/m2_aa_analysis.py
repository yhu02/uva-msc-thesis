#!/usr/bin/env python3
"""M2 A/A calibration analysis (01-PREREGISTRATION.md §A/A calibration protocol).

Implements the amended A/A protocol (PR #261): the A/A block is a
**variance-component estimator plus a qualitative pipeline sanity check**,
NOT a false-positive-rate gate — no numeric "FPR <= alpha" criterion is
registered.  Any statistically significant A/A finding is reported as
``A/A FINDING — investigate`` (the amended rule: diagnose, fix the
doctor gates / taint rules / instrumentation, rerun the block; the halt
criterion lives in §Stopping rules 2).

This module is also the **shared per-iteration extraction library** for
the supplementary A/A analysis: ``scripts/aa_block.py`` imports
:func:`extract_iteration`, :func:`load_condition_outcomes` and the taint
plumbing from here, so the canonical and supplementary numbers are
computed by one extraction and can never drift apart (the M2 report's
"analysis consolidation" item).

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
   never silently included or dropped.  Metric values come from the raw
   per-condition files (``<run>/<condition>.json``), reduced one file at a
   time to per-iteration scalars (the files are 20–100 MB).
2. **Per-pair deltas + null tests.** Per pair, per accepted f-level, per
   registered metric the A-vs-B delta is computed at the registered unit
   (the session-condition median over untainted iterations); per metric
   the level-paired samples go through Wilcoxon signed-rank (>= 5 paired
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

Registered delta metrics (D4 consolidation)
-------------------------------------------
The metric forms implement freeze decision **D4** of the M2 report
(``v2-design/M2-AA-REPORT.md`` §Freeze decisions, §Instrumentation gaps):
one canonical A/A extraction whose forms match the registered tests —
the supplementary (median-over-routes, pre-chaos, taint-excluded)
operationalization won D4, and V2-H2 is registered on **absolute** UDP
drops, not the pct ratio (the prereg disclaims ratio denominators: the
packed arm's near-zero pool makes them ill-defined).

- ``ew_p95_pre_ms`` — the V2-H1 east-west outcome (D4 winner): per
  iteration, the **median over inter-service (``a->b``) routes** of the
  route p95 in the **pre-chaos** window, read from the raw iteration's
  ``metrics.latency.phases``.  Routes containing ``loadgenerator->`` are
  excluded (DESIGN §4: the host-side load generator is excluded from
  edge accounting by construction).
- ``udp_conntrack_drop_entries`` — V2-H2's registered **absolute** UDP
  conntrack drop: per iteration, cluster UDP entries (per-node phase
  mean of ``metrics.conntrackProtocolSamples`` counts, summed over
  nodes) pre-chaos minus during-chaos.
- ``conntrack_flush_pct`` — the v1 mechanism definition, kept as v1
  context: per iteration, ``(pre_mean - during_mean) / pre_mean * 100``
  on the iteration's Prometheus phase aggregate
  ``conntrack_entries_per_node`` (the same definition
  ``scripts/mechanism_metrics.py`` computes as M1, now per iteration so
  taint exclusion applies).
- ``score`` — the v1 aggregate resilience score: per-iteration
  ``resilienceScore`` with ERROR-verdict iterations excluded (their
  fabricated 0.0 is not a valid resilience measurement).

Every metric's session-condition value is the **median over untainted
iterations** — the registered unit ("session medians as the unit").
Tainted iterations (``taintReasons`` recorded in ``summary.json``'s
``v2Session.perLevel[].perIteration`` plus ``preChaosTaintReasons`` in
the raw iteration records) are excluded from EVERY metric — the
prereg's "no result is ever quoted from a tainted iteration" rule,
which the pre-D4 script applied to ``score`` only (M2-AA-REPORT.md
§Verdict).  Excluded iterations keep a ``None`` row so per-iteration
pairing alignment is preserved.

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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from chaosprobe.metrics.statistics import icc_bootstrap, sign_test, wilcoxon_signed_rank

#: Analysis-output schema identifier (bump on breaking shape changes).
#: v2: D4 consolidation — per-iteration extraction, taint exclusion on
#: every metric, ``ew_p95_pre_ms`` + ``udp_conntrack_drop_entries`` forms.
SCHEMA = "chaosprobe/m2-aa-analysis/v2"

#: Pre-registered minimum number of A/A session pairs (§A/A calibration).
REGISTERED_MIN_PAIRS = 3

#: Default threshold for the "any statistically significant A/A finding" rule.
DEFAULT_ALPHA = 0.05

#: Below this many paired observations the Wilcoxon normal approximation is
#: meaningless; the exact sign test is used instead.
WILCOXON_MIN_LEVELS = 5

#: Registered A/A delta metrics (liveAchievedF is the separate identity
#: check).  Forms per D4 — see the module docstring.
METRICS = ("ew_p95_pre_ms", "udp_conntrack_drop_entries", "conntrack_flush_pct", "score")

#: Every per-iteration outcome :func:`extract_iteration` produces.  The
#: canonical analysis tests :data:`METRICS`; the supplementary analysis
#: (``scripts/aa_block.py``) consumes the broader set for its variance
#: outcomes and noise bands.
ITERATION_OUTCOMES = (
    "ew_p95_pre_ms",
    "ew_p95_during_ms",
    "udp_conntrack_drop_entries",
    "udp_conntrack_drop_pct",
    "conntrack_flush_pct",
    "es_trough_depth_pods",
    "es_zero_services",
    "trough_duration_s",
    "trough_duration_real_s",
    "user_err_during",
    "loadgen_err",
    "udp_preslope_epm",
    "score",
)

#: Documented method string embedded in the JSON output (spec: document it).
VARIANCE_METHOD = (
    "nested variance components via chaosprobe.metrics.statistics.icc_bootstrap on "
    "cells {((pair, level), session): per-iteration values}: betweenPairLevel = "
    "population variance of pair-level cell grand means (designed f-level effects are "
    "absorbed here), betweenSessionWithinPair = mean over pair-level cells of the "
    "population variance of session means, betweenIteration = mean within-session "
    "population variance; per-iteration values are the taint-excluded extraction of "
    "the raw per-condition files (D4)"
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
    """One f-level of one session: identity fields + metric values.

    ``values`` holds the registered unit — the session-condition median
    over untainted iterations — per metric; ``iteration_values`` the
    per-iteration rows behind it (``None`` rows are taint-excluded
    iterations, kept so pairing alignment is preserved).
    """

    condition: str
    target_f: float
    live_achieved_f: Optional[float]
    accepted: bool
    rejection_reasons: List[str]
    values: Dict[str, Optional[float]] = field(
        default_factory=lambda: {metric: None for metric in METRICS}
    )
    iteration_values: Dict[str, List[Optional[float]]] = field(default_factory=dict)


@dataclass
class Session:
    """One loaded v2 session summary, reduced to what the A/A analysis needs."""

    run: str
    run_id: Optional[str]
    timestamp: Optional[str]
    key: PairKey
    levels: Dict[str, LevelObs]
    tainted: Set[Tuple[str, Any]] = field(default_factory=set)
    taints: List[str] = field(default_factory=list)


@dataclass
class Pair:
    """An A/A pair: two sessions of the identical cell."""

    label: str
    key: PairKey
    a: Session
    b: Session


# ──────────────────────────────────────────────────────────────────────
# Per-iteration outcome extraction (the D4 canonical extraction; shared
# with scripts/aa_block.py)
# ──────────────────────────────────────────────────────────────────────


def is_east_west(route: str) -> bool:
    """An inter-service edge group (every member ``a->b``), load generator excluded."""
    parts = route.split(",")
    return all("->" in p for p in parts) and not any(
        p.strip().startswith("loadgenerator->") for p in parts
    )


def is_user_route(route: str) -> bool:
    """A user-facing probe route: no edge arrow, health-check endpoint excluded."""
    return "->" not in route and route != "/_healthz"


def east_west_p95(latency: Dict[str, Any], phase: str) -> Optional[float]:
    """Median across east-west routes of the route p95 in one phase, or None.

    The D4-winning V2-H1 operationalization (median over routes — robust
    to single-route excursions; see v2-design/M2-AA-REPORT.md D4).
    """
    routes = (((latency or {}).get("phases") or {}).get(phase) or {}).get("routes") or {}
    vals = [
        v["p95_ms"]
        for k, v in routes.items()
        if is_east_west(k) and isinstance(v, dict) and isinstance(v.get("p95_ms"), (int, float))
    ]
    return st.median(vals) if vals else None


def user_error_rate(latency: Dict[str, Any], phase: str) -> Optional[float]:
    """errorCount / (sampleCount + errorCount) over user routes in one phase."""
    routes = (((latency or {}).get("phases") or {}).get(phase) or {}).get("routes") or {}
    err = n = 0
    for k, v in routes.items():
        if not (is_user_route(k) and isinstance(v, dict)):
            continue
        e = v.get("errorCount") or 0
        s = v.get("sampleCount") or 0
        err += e
        n += e + s
    return err / n if n else None


def udp_cluster_phase_mean(samples: List[Dict[str, Any]], phase: str) -> Optional[float]:
    """Cluster UDP entries in a phase: per-node mean count, summed over nodes."""
    per_node: Dict[str, List[float]] = {}
    for smp in samples or []:
        if smp.get("proto") == "udp" and smp.get("phase") == phase:
            per_node.setdefault(smp["node"], []).append(float(smp["count"]))
    if not per_node:
        return None
    return sum(st.mean(v) for v in per_node.values())


def udp_pre_slope(samples: List[Dict[str, Any]]) -> Optional[float]:
    """Pre-chaos cluster UDP slope (entries/min): per-node OLS slope, summed."""
    per_node: Dict[str, List[Tuple[float, float]]] = {}
    for smp in samples or []:
        if smp.get("proto") == "udp" and smp.get("phase") == "pre-chaos" and smp.get("ts"):
            t = datetime.fromisoformat(smp["ts"]).timestamp()
            per_node.setdefault(smp["node"], []).append((t, float(smp["count"])))
    slopes = []
    for pts in per_node.values():
        if len(pts) < 2:
            continue
        pts.sort()
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        mx, my = st.mean(xs), st.mean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx == 0:
            continue
        slopes.append(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx)
    return sum(slopes) * 60.0 if slopes else None  # per-second -> per-minute


def _series_total_ready(sample: Dict[str, Any], app_services: Sequence[str]) -> Optional[int]:
    """Total ready endpoints across *app_services* in one time-series sample.

    Returns ``None`` when none of the app services have an integer ``ready``
    count in this sample (so a sample carrying no usable signal is skipped
    rather than treated as a zero-ready trough).
    """
    services = (sample or {}).get("services") or {}
    total = 0
    measured = False
    for svc in app_services:
        ready = (services.get(svc) or {}).get("ready")
        if isinstance(ready, int):
            measured = True
            total += ready
    return total if measured else None


def es_trough_duration_real(
    endpoint_slice_timeseries: Dict[str, Any], app_services: Sequence[str]
) -> Optional[float]:
    """Real trough DURATION (seconds) from the EndpointSlice time series, or None.

    This is the V2-H3 instrument the M2 report asked for — the duration of
    the availability trough measured directly from the 15s-cadence
    EndpointSlice samples (``metrics.endpointSliceTimeSeries``), replacing
    the mean-pod-recovery *proxy* (``trough_duration_s``) wherever the
    series is present.

    Definition (chosen for this PR; documented in the PR body):

    - **baseline** = total ready endpoints (summed over *app_services*) in
      the **last pre-chaos sample** — the healthy level the trough is
      measured against.
    - **drop start** = the first sample at or after the baseline sample
      whose total ready is **strictly below baseline**.
    - **recovery** = the first sample after drop start whose total ready is
      **back at or above baseline**.
    - **duration** = ``recovery.ts - dropStart.ts`` (wall-clock span in
      seconds).

    Edge cases:

    - *Never drops* (ready never falls below baseline after the baseline
      sample) -> ``0.0`` (a real, measured zero-duration trough).
    - *Never recovers* (drops but never returns to baseline within the
      sampled window) -> ``lastSample.ts - dropStart.ts`` (the observed
      lower bound on the duration; the window ended still degraded).
    - *No pre-chaos baseline*, *no usable samples*, or *no app-service
      signal* -> ``None`` (not measurable -> the caller falls back to the
      proxy).
    """
    samples = (endpoint_slice_timeseries or {}).get("samples") or []
    # (timestamp_epoch, total_ready, phase) for samples with a usable signal.
    points: List[Tuple[float, int, str]] = []
    for smp in samples:
        ts = smp.get("ts")
        total = _series_total_ready(smp, app_services)
        if ts is None or total is None:
            continue
        try:
            epoch = datetime.fromisoformat(ts).timestamp()
        except (TypeError, ValueError):
            continue
        points.append((epoch, total, str(smp.get("phase") or "")))
    if not points:
        return None
    points.sort(key=lambda p: p[0])

    # Baseline = the last pre-chaos sample's total ready.  Without a
    # pre-chaos sample there is no healthy reference, so duration is not
    # measurable from the series (fall back to the proxy).
    pre = [p for p in points if p[2] == "pre-chaos"]
    if not pre:
        return None
    baseline = pre[-1][1]
    baseline_ts = pre[-1][0]

    after = [p for p in points if p[0] >= baseline_ts]
    drop_start: Optional[float] = None
    for epoch, total, _phase in after:
        if drop_start is None:
            if total < baseline:
                drop_start = epoch
        elif total >= baseline:
            return epoch - drop_start
    if drop_start is None:
        return 0.0  # never dropped below baseline
    return after[-1][0] - drop_start  # dropped but never recovered in-window


def es_trough(
    endpoint_slices: Dict[str, Any], app_services: Sequence[str]
) -> Tuple[Optional[float], Optional[float]]:
    """(trough depth in pods, services driven to zero) from the duringChaos snapshot."""
    pre = ((endpoint_slices or {}).get("preChaos") or {}).get("services") or {}
    dur = ((endpoint_slices or {}).get("duringChaos") or {}).get("services") or {}
    if not pre or not dur:
        return None, None
    depth = 0
    zeroed = 0
    measured = 0
    for svc in app_services:
        p = (pre.get(svc) or {}).get("ready")
        q = (dur.get(svc) or {}).get("ready")
        if not isinstance(p, int) or not isinstance(q, int):
            continue
        measured += 1
        depth += max(0, p - q)
        if p > 0 and q == 0:
            zeroed += 1
    if not measured:
        return None, None
    return float(depth), float(zeroed)


def _iteration_phase_mean(metrics: Dict[str, Any], metric: str, phase: str) -> Optional[float]:
    """Mean of a Prometheus phase-aggregate metric inside one raw iteration."""
    phases = ((metrics or {}).get("prometheus") or {}).get("phases") or {}
    entry = ((phases.get(phase) or {}).get("metrics") or {}).get(metric)
    return entry.get("mean") if isinstance(entry, dict) else None


def iteration_conntrack_flush_pct(metrics: Dict[str, Any]) -> Optional[float]:
    """Conntrack flush percentage per the v1 mechanism definition, per iteration.

    ``(pre_mean - during_mean) / pre_mean * 100`` of
    ``conntrack_entries_per_node`` (positive = entries flushed), the same
    definition ``scripts/mechanism_metrics.py`` computes as M1 — applied
    to one iteration's Prometheus phase aggregates so tainted iterations
    can be excluded (D4).
    """
    pre = _iteration_phase_mean(metrics, "conntrack_entries_per_node", "pre-chaos")
    during = _iteration_phase_mean(metrics, "conntrack_entries_per_node", "during-chaos")
    if pre and during is not None:
        return (pre - during) / pre * 100.0
    return None


def extract_iteration(
    it: Dict[str, Any], app_services: Sequence[str]
) -> Dict[str, Optional[float]]:
    """Reduce one raw iteration record to the registered scalar outcomes.

    The single shared extraction (D4): the canonical metrics plus the
    supplementary variance outcomes ``scripts/aa_block.py`` reports.
    """
    m = it.get("metrics") or {}
    latency = m.get("latency") or {}
    samples = m.get("conntrackProtocolSamples") or []
    udp_pre = udp_cluster_phase_mean(samples, "pre-chaos")
    udp_dur = udp_cluster_phase_mean(samples, "during-chaos")
    udp_drop = (udp_pre - udp_dur) if (udp_pre is not None and udp_dur is not None) else None
    udp_drop_pct = (
        100.0 * udp_drop / udp_pre if (udp_drop is not None and udp_pre and udp_pre > 0) else None
    )
    depth, zeroed = es_trough(m.get("endpointSlices") or {}, app_services)
    # Real trough DURATION from the 15s EndpointSlice time series when the
    # sampler produced one (V2-H3 instrument); None when the series is
    # absent (e.g. frozen M2 A/A data), so the proxy below is used instead.
    duration_real = es_trough_duration_real(m.get("endpointSliceTimeSeries") or {}, app_services)
    rec = ((m.get("recovery") or {}).get("summary")) or {}
    mean_rec = rec.get("meanRecovery_ms")
    lg = (it.get("loadGeneration") or {}).get("stats") or m.get("loadGeneration") or {}
    score = it.get("resilienceScore")
    # An ERROR verdict fabricates a 0.0 score — not a valid measurement.
    score_valid = isinstance(score, (int, float)) and it.get("verdict") != "ERROR"
    return {
        "ew_p95_pre_ms": east_west_p95(latency, "pre-chaos"),
        "ew_p95_during_ms": east_west_p95(latency, "during-chaos"),
        "udp_conntrack_drop_entries": udp_drop,
        "udp_conntrack_drop_pct": udp_drop_pct,
        "conntrack_flush_pct": iteration_conntrack_flush_pct(m),
        "es_trough_depth_pods": depth,
        "es_zero_services": zeroed,
        "trough_duration_s": mean_rec / 1000.0 if isinstance(mean_rec, (int, float)) else None,
        "trough_duration_real_s": duration_real,
        "user_err_during": user_error_rate(latency, "during-chaos"),
        "loadgen_err": (
            lg.get("errorRate") if isinstance(lg.get("errorRate"), (int, float)) else None
        ),
        "udp_preslope_epm": udp_pre_slope(samples),
        "score": float(score) if score_valid else None,
    }


def summary_tainted_iterations(
    per_level: Sequence[Dict[str, Any]],
) -> Tuple[Set[Tuple[str, Any]], List[str]]:
    """Tainted ``(condition, iteration)`` pairs recorded in the session summary.

    Reads ``v2Session.perLevel[].perIteration[].taintReasons`` — the
    engine-side taint channel; the raw files' ``preChaosTaintReasons``
    are folded in later by :func:`load_condition_outcomes`.
    """
    tainted: Set[Tuple[str, Any]] = set()
    taints: List[str] = []
    for lvl in per_level:
        cond = lvl.get("condition")
        if not cond:
            continue
        for pi in lvl.get("perIteration") or []:
            for reason in pi.get("taintReasons") or []:
                taints.append(f"{cond} it{pi.get('iteration')}: {reason}")
                tainted.add((cond, pi.get("iteration")))
    return tainted, taints


def load_condition_outcomes(
    session_dir: str,
    condition: str,
    tainted: Set[Tuple[str, Any]],
    taints: List[str],
) -> Optional[Dict[str, List[Optional[float]]]]:
    """Per-iteration outcome rows for one condition's raw ``<condition>.json``.

    Returns ``{outcome: [value-or-None per iteration]}`` for every
    :data:`ITERATION_OUTCOMES` key, or ``None`` when the raw file is
    missing (the caller decides how loudly to complain).  Iterations
    carrying ``preChaosTaintReasons`` are added to ``tainted`` /
    ``taints`` in place; every tainted iteration contributes a ``None``
    row for every outcome — the registered "never quoted" exclusion,
    with index alignment preserved so paired tests drop the pair.
    Raw files are 20–100 MB: one is loaded at a time and freed before
    the next.
    """
    path = os.path.join(session_dir, f"{condition}.json")
    if not os.path.isfile(path):
        return None
    with open(path) as fh:
        raw = json.load(fh)
    app_services = sorted(((raw.get("placement") or {}).get("assignments")) or {})
    per_outcome: Dict[str, List[Optional[float]]] = {key: [] for key in ITERATION_OUTCOMES}
    for idx, it in enumerate(raw.get("iterations") or [], start=1):
        iteration = it.get("iteration", idx)
        if it.get("preChaosTaintReasons"):
            taints.extend(
                f"{condition} it{iteration}: pre-chaos {reason}"
                for reason in it["preChaosTaintReasons"]
            )
            tainted.add((condition, iteration))
        if (condition, iteration) in tainted:
            for key in ITERATION_OUTCOMES:
                per_outcome[key].append(None)
            continue
        row = extract_iteration(it, app_services)
        for key in ITERATION_OUTCOMES:
            per_outcome[key].append(row[key])
    del raw  # one raw file in memory at a time
    return per_outcome


def _median_or_none(values: Sequence[Optional[float]]) -> Optional[float]:
    """Median of the non-None, non-NaN entries; None for an empty sample."""
    clean = [float(v) for v in values if isinstance(v, (int, float)) and not math.isnan(float(v))]
    return st.median(clean) if clean else None


# ──────────────────────────────────────────────────────────────────────
# Discovery + pairing
# ──────────────────────────────────────────────────────────────────────


def parse_session(run: str, summary: Dict[str, Any], warnings: List[str]) -> Optional[Session]:
    """Reduce one summary.json to a :class:`Session`, or ``None`` (warned).

    Metric values are NOT filled in here — they come from the raw
    per-condition files via :func:`load_session_outcomes` (so the 60+ MB
    summary dict can be freed before any raw file is opened).
    """
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
    else:
        fault = ""

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

    per_level = v2.get("perLevel") or []
    tainted, taints = summary_tainted_iterations(per_level)
    levels: Dict[str, LevelObs] = {}
    for record in per_level:
        condition = record.get("condition")
        if not condition:
            continue
        levels[condition] = LevelObs(
            condition=condition,
            target_f=float(record.get("targetF") or 0.0),
            live_achieved_f=record.get("liveAchievedF"),
            accepted=bool(record.get("accepted", True)),
            rejection_reasons=[str(reason) for reason in record.get("rejectionReasons") or []],
        )
    return Session(
        run=run,
        run_id=summary.get("runId"),
        timestamp=summary.get("timestamp"),
        key=key,
        levels=levels,
        tainted=tainted,
        taints=taints,
    )


def load_session_outcomes(session: Session, session_dir: str, warnings: List[str]) -> None:
    """Fill every level's metric values from the raw per-condition files.

    Per level: load ``<session_dir>/<condition>.json``, reduce it to
    taint-excluded per-iteration outcome rows (:func:`load_condition_outcomes`),
    and set the registered-unit values — the median over untainted
    iterations per metric.  A missing raw file leaves the level's values
    ``None`` with a warning (never silently zeroed).
    """
    for condition, obs in session.levels.items():
        per_outcome = load_condition_outcomes(
            session_dir, condition, session.tainted, session.taints
        )
        if per_outcome is None:
            warnings.append(
                f"{session.run}: raw {condition}.json missing — level carries no metric values"
            )
            continue
        obs.iteration_values = per_outcome
        obs.values = {metric: _median_or_none(per_outcome[metric]) for metric in METRICS}


def discover_sessions(results_dir: str) -> Tuple[List[Session], List[str]]:
    """Load every ``<results-dir>/*/summary.json`` into sessions (+ warnings)."""
    sessions: List[Session] = []
    warnings: List[str] = []
    for path in sorted(glob.glob(os.path.join(results_dir, "*", "summary.json"))):
        run_dir = os.path.dirname(path)
        run = os.path.basename(run_dir)
        try:
            with open(path) as fh:
                summary = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"{run}: unreadable summary.json ({exc}) — skipped")
            continue
        session = parse_session(run, summary, warnings)
        del summary  # free the 60+ MB summary before touching the raw files
        if session is not None:
            load_session_outcomes(session, run_dir, warnings)
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

    The untainted per-iteration values (that is what makes the
    between-iteration component estimable); a level without per-iteration
    data (raw file missing) contributes its single session-level value
    when present.  NaN values (legacy/foreign raw files) are dropped like
    ``None`` — one NaN would silently null the whole decomposition.
    """
    values = [
        float(v)
        for v in obs.iteration_values.get(metric, [])
        if isinstance(v, (int, float)) and not math.isnan(float(v))
    ]
    if values:
        return values
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
        "taintedIterations": [
            f"{session.run}: {taint}" for session in sessions for taint in session.taints
        ],
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

    print("\n=== Tainted iterations (excluded from every metric, D4) ===")
    if result["taintedIterations"]:
        for taint in result["taintedIterations"]:
            print(f"  TAINTED (excluded): {taint}")
    else:
        print("  (none)")

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
                f"    {metric:<28} n={entry['nLevelsTested']} "
                f"method={entry['method'] or '-'} p={_fmt(entry['p'], 4)}  {verdict}"
            )

    print("\n=== Cross-pair drift test (one mean delta per pair vs 0) ===")
    for metric in METRICS:
        entry = result["crossPairTests"][metric]
        verdict = "FINDING" if entry["finding"] else "ok"
        print(
            f"  {metric:<28} n={entry['nPairs']} "
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
        f"  {'metric':<28}{'between-pair-level':>20}{'between-session':>17}"
        f"{'between-iter':>14}{'ICC':>8}"
    )
    print(header)
    for metric in METRICS:
        comp = result["varianceComponents"][metric]
        print(
            f"  {metric:<28}{_fmt(comp['betweenPairLevel']):>20}"
            f"{_fmt(comp['betweenSessionWithinPair']):>17}"
            f"{_fmt(comp['betweenIteration']):>14}{_fmt(comp['icc']):>8}"
        )

    print("\n=== Noise band (within-pair between-session SD, per metric per level) ===")
    print(f"  {'metric':<28}{'level':>8}{'SD':>12}{'mean|d|':>12}{'pairs':>7}")
    for row in result["noiseBand"]:
        print(
            f"  {row['metric']:<28}{row['targetF']:>8.2f}{row['withinPairSessionSD']:>12.4f}"
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
            "M2 A/A calibration analysis (pre-registration §A/A, amended in #261; "
            "metric forms per the D4 consolidation, v2-design/M2-AA-REPORT.md): "
            "pairs identical-cell v2 sessions, computes per-level per-metric deltas "
            "at the registered unit (session-condition medians over untainted "
            "iterations) with Wilcoxon/sign null tests (any p < alpha => 'A/A "
            "FINDING — investigate'), checks liveAchievedF exact identity within "
            "pairs, and emits the variance-component decomposition + noise band "
            "table the M2 power analysis and SESOI finalization consume."
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
