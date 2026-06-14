#!/usr/bin/env python3
"""V2-H5 layered scorecard — three sub-scores + the reliability evaluation.

Implements the layered scorecard registered as DESIGN §5 / pre-registration
§V2-H5, with the aggregation formulas specified post-freeze (blind to all
campaign data) in ``v2-design/DEVIATIONS.md`` entry **D-2026-06-13-01**. The
constituent signals and the evaluation rule are frozen at the M2 commit
(tag ``v2-prereg-freeze``); only the scalar aggregation is added here.

The scorecard replaces the v1 aggregate ``score`` with **three per-layer
sub-scores**, each a single scalar per session-condition, higher = better,
range ``[0, 100]``:

1. **availability** (required, confirmatory) — EndpointSlice trough
   depth/duration + user-route error rate during fault.
2. **mechanism-reconvergence** (required, confirmatory) — protocol-labeled
   UDP-conntrack disturbance + reconvergence time.
3. **user-tail** (exploratory) — dependent-route p95 vs control-route p95,
   the H3 confound-controlled contrast.

Aggregation (D-2026-06-13-01)
-----------------------------
Per ITERATION each sub-score is computed; the session-condition value is the
**median over that condition's non-tainted iterations** (tainted iterations
excluded by reusing :func:`m2_aa_analysis.load_condition_outcomes`'s taint
plumbing). Each constituent is a ``[0, 1]`` "loss" (clamped to ``[0, 1]``);
the sub-score is ``100 * (1 - mean(losses))``. A ``None`` loss makes the
whole sub-score ``None`` for that iteration (not measurable).

- **availability** = mean of
  ``depth_loss`` = ``trough_depth_pods / baseline_ready_endpoints``
  (``baseline ≤ 0`` -> sub-score ``None``);
  ``duration_loss`` = ``trough_duration_real_s / chaos_window_seconds``
  (real series absent -> sub-score ``None``);
  ``error_loss`` = ``user_err_during``.
- **mechanism-reconvergence** = mean of
  ``disturbance_loss`` = ``udp_drop_entries / pre_chaos_UDP_pool``
  (``pool ≤ 0`` -> ``0.0``);
  ``reconverg_loss`` = ``conntrack_reconvergence_time_s / chaos_window_seconds``.
- **user-tail** = ``100 * min(1, control_p95 / dependent_p95)``
  (``dependent_p95 ≤ 0`` / no dependent route -> ``None``).

``chaos_window_seconds`` is sourced per iteration from the iteration record's
top-level ``anomalyLabels[*].parameters.duration_s`` (recorded
``TOTAL_CHAOS_DURATION``); falling back to the during-chaos sample span
(EndpointSlice time series, then conntrack UDP series).

Reliability evaluation (frozen §V2-H5)
--------------------------------------
Across a set of **campaign** sessions (NOT the A/A pairs): condition-level
ICC for each sub-score AND for the v1 aggregate ``score`` (ICC_old), using
:func:`chaosprobe.metrics.statistics.icc_bootstrap` on cells
``{(session-condition, session): [session-condition median]}`` (one
single-element cell per condition×session, per D-2026-06-13-01) — the
"strategy" role is the session-condition whose sub-score should reproduce,
the "run" role is the session (the test-retest replicate). For each
**required** sub-score (availability, mechanism): the bootstrap CI on
``ICC_sub - ICC_v1`` must exclude 0, AND ``ICC_sub ≥ 0.5`` with its CI
excluding ICC_v1. The two are combined as a **conjunction** (both must pass);
V2-H5's single Holm input is ``max(p_availability, p_mechanism)``. user-tail
is computed and reported identically but flagged **exploratory** and excluded
from the decision.

Graceful degradation: on the frozen A/A block (no EndpointSlice time series ->
``availability`` and ``mechanism`` sub-scores ``None``) the evaluation reports
"not evaluable" rather than crashing.

Usage
-----
    uv run python scripts/scorecard.py --results-dir results/v2-c1 \\
        [--json out.json] [--confidence 0.95] [--n-resamples 2000] [--seed 42]

Exit codes: 0 when both required sub-scores pass (V2-H5 PASS); 1 when V2-H5
is not evaluable or fails; 2 when fewer than the minimum sessions/conditions
needed for an ICC are present.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import random
import statistics as st
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from chaosprobe.metrics.statistics import _icc_point, _percentile, icc_bootstrap

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:  # pragma: no cover - `python scripts/x.py` adds it; imports don't
    sys.path.insert(0, _SCRIPTS_DIR)

from h3_mechanism_outcome import _ctrl, _dep  # noqa: E402  (sys.path bootstrap above)
from m2_aa_analysis import (  # noqa: E402  (sys.path bootstrap above)
    SLOPE_BAND_TAINT_REASON,
    es_trough,
    es_trough_duration_real,
    parse_session,
    udp_cluster_phase_mean,
    udp_pre_slope,
    udp_preslope_out_of_band,
    user_error_rate,
)

#: Analysis-output schema identifier (bump on breaking shape changes).
SCHEMA = "chaosprobe/scorecard/v1"

#: The three sub-score keys, in report order.  The first two are the
#: **required** (confirmatory) sub-scores; the third is **exploratory**.
SUBSCORES = ("availability", "mechanism", "user_tail")
REQUIRED_SUBSCORES = ("availability", "mechanism")
EXPLORATORY_SUBSCORES = ("user_tail",)

#: The frozen absolute reliability bar (pre-registration §V2-H5).
ABSOLUTE_ICC_BAR = 0.5

#: Fewer than this many distinct session-conditions (the ICC "strategy"
#: factor) leaves the between-condition variance unestimable.
MIN_CONDITIONS = 2


# ──────────────────────────────────────────────────────────────────────
# chaos_window_seconds sourcing (D-2026-06-13-01)
# ──────────────────────────────────────────────────────────────────────


def _phase_span(samples: Sequence[Dict[str, Any]], phase: str) -> Optional[float]:
    """Wall-clock span (s) of the timestamped samples in one phase, or None."""
    epochs: List[float] = []
    for smp in samples or []:
        if smp.get("phase") != phase:
            continue
        ts = smp.get("ts")
        if ts is None:
            continue
        try:
            epochs.append(datetime.fromisoformat(ts).timestamp())
        except (TypeError, ValueError):
            continue
    if len(epochs) < 2:
        return None
    return max(epochs) - min(epochs)


def chaos_window_seconds(it: Dict[str, Any]) -> Optional[float]:
    """The iteration's chaos window in seconds (D-2026-06-13-01 sourcing).

    Primary source: the iteration record's top-level
    ``anomalyLabels[*].parameters.duration_s`` (the recorded
    ``TOTAL_CHAOS_DURATION``) — the largest positive value across labels.
    Fallback: the during-chaos sample span (EndpointSlice time series, then
    conntrack samples).  ``None`` when no window can be sourced.
    """
    best = 0.0
    for label in it.get("anomalyLabels") or []:
        params = (label or {}).get("parameters") or {}
        dur = params.get("duration_s")
        if isinstance(dur, (int, float)) and dur > best:
            best = float(dur)
    if best > 0:
        return best
    m = it.get("metrics") or {}
    series = ((m.get("endpointSliceTimeSeries") or {}).get("samples")) or []
    span = _phase_span(series, "during-chaos")
    if span is not None and span > 0:
        return span
    span = _phase_span(m.get("conntrackProtocolSamples") or [], "during-chaos")
    if span is not None and span > 0:
        return span
    return None


# ──────────────────────────────────────────────────────────────────────
# Sub-score constituents (per iteration)
# ──────────────────────────────────────────────────────────────────────


def _clamp01(x: float) -> float:
    """Clamp a loss to the unit interval ``[0, 1]``."""
    return max(0.0, min(1.0, x))


def _series_last_pre_baseline(
    timeseries: Dict[str, Any], app_services: Sequence[str]
) -> Optional[int]:
    """Total ready endpoints in the last pre-chaos EndpointSlice sample, or None."""
    samples = (timeseries or {}).get("samples") or []
    best_ts: Optional[float] = None
    best_total: Optional[int] = None
    for smp in samples:
        if smp.get("phase") != "pre-chaos":
            continue
        ts = smp.get("ts")
        if ts is None:
            continue
        try:
            epoch = datetime.fromisoformat(ts).timestamp()
        except (TypeError, ValueError):
            continue
        services = smp.get("services") or {}
        total = 0
        measured = False
        for svc in app_services:
            ready = (services.get(svc) or {}).get("ready")
            if isinstance(ready, int):
                measured = True
                total += ready
        if not measured:
            continue
        if best_ts is None or epoch >= best_ts:
            best_ts = epoch
            best_total = total
    return best_total


def _snapshot_pre_baseline(
    endpoint_slices: Dict[str, Any], app_services: Sequence[str]
) -> Optional[int]:
    """Total ready endpoints in the pre/during/post ``preChaos`` snapshot, or None."""
    pre = ((endpoint_slices or {}).get("preChaos") or {}).get("services") or {}
    total = 0
    measured = False
    for svc in app_services:
        ready = (pre.get(svc) or {}).get("ready")
        if isinstance(ready, int):
            measured = True
            total += ready
    return total if measured else None


def baseline_ready_endpoints(metrics: Dict[str, Any], app_services: Sequence[str]) -> Optional[int]:
    """Baseline ready endpoints: last pre-chaos time-series sample, snapshot fallback."""
    series_baseline = _series_last_pre_baseline(
        metrics.get("endpointSliceTimeSeries") or {}, app_services
    )
    if series_baseline is not None:
        return series_baseline
    return _snapshot_pre_baseline(metrics.get("endpointSlices") or {}, app_services)


def udp_reconvergence_time_s(
    samples: Sequence[Dict[str, Any]], chaos_start_epoch: Optional[float] = None
) -> Optional[float]:
    """UDP-conntrack reconvergence time (s), mirroring ``es_trough_duration_real``.

    From the ``conntrackProtocolSamples`` UDP series: the pool baseline is the
    pre-chaos summed-UDP-over-nodes (per-node mean count summed over nodes, the
    same reduction :func:`m2_aa_analysis.udp_cluster_phase_mean` uses).  Chaos
    start is the first during-chaos sample's timestamp (or ``chaos_start_epoch``
    when given).  Reconvergence time = (first during/post-chaos sample whose
    summed-UDP-over-nodes is back at or above the baseline pool) minus chaos
    start.

    Edge cases (mirroring ``es_trough_duration_real``):

    - *Never drops* (UDP pool never falls below baseline after chaos start) ->
      ``0.0`` (a real, measured zero-duration disturbance).
    - *Never recovers* (drops but never returns to baseline in-window) ->
      ``lastSample.ts - chaos_start`` (observed lower bound).
    - *No pre-chaos baseline*, *no during/post samples*, or *no chaos start* ->
      ``None``.
    """
    baseline = udp_cluster_phase_mean(list(samples), "pre-chaos")
    if baseline is None:
        return None

    # Per-timestamp summed-UDP-over-nodes for during + post phases.  Each
    # sample carries a single node's count; group by timestamp and sum the
    # per-node counts seen at that timestamp.
    by_ts: Dict[float, float] = {}
    for smp in samples or []:
        if smp.get("proto") != "udp":
            continue
        if smp.get("phase") not in ("during-chaos", "post-chaos"):
            continue
        ts = smp.get("ts")
        if ts is None:
            continue
        try:
            epoch = datetime.fromisoformat(ts).timestamp()
        except (TypeError, ValueError):
            continue
        by_ts[epoch] = by_ts.get(epoch, 0.0) + float(smp.get("count", 0))
    if not by_ts:
        return None
    points = sorted(by_ts.items())  # [(epoch, summed_udp)]

    start = chaos_start_epoch if chaos_start_epoch is not None else points[0][0]
    after = [(epoch, total) for epoch, total in points if epoch >= start]
    if not after:
        return None
    drop_start: Optional[float] = None
    for epoch, total in after:
        if drop_start is None:
            if total < baseline:
                drop_start = epoch
        elif total >= baseline:
            return epoch - start
    if drop_start is None:
        return 0.0  # never dropped below baseline
    return after[-1][0] - start  # dropped but never recovered in-window


# ──────────────────────────────────────────────────────────────────────
# Sub-score scalars (per iteration), reusing the D4 extraction pieces
# ──────────────────────────────────────────────────────────────────────


def _route_p95_median(latency: Dict[str, Any], phase: str, classifier) -> Optional[float]:
    """Median over routes matching ``classifier`` of the route p95 in one phase.

    Median (not max) to match :func:`m2_aa_analysis.east_west_p95` — robust to
    a single-route excursion.  ``None`` when no matching route has a numeric
    ``p95_ms``.
    """
    routes = (((latency or {}).get("phases") or {}).get(phase) or {}).get("routes") or {}
    vals = [
        v["p95_ms"]
        for name, v in routes.items()
        if classifier(name) and isinstance(v, dict) and isinstance(v.get("p95_ms"), (int, float))
    ]
    return st.median(vals) if vals else None


def availability_subscore(it: Dict[str, Any], app_services: Sequence[str]) -> Optional[float]:
    """availability sub-score in ``[0, 100]`` for one iteration, or None.

    Mean of depth/duration/error losses (D-2026-06-13-01 A).  ``None`` when the
    baseline endpoint count is non-positive, the real trough-duration series is
    absent, the chaos window is unsourceable, or the user error rate is absent.
    """
    m = it.get("metrics") or {}
    baseline = baseline_ready_endpoints(m, app_services)
    if baseline is None or baseline <= 0:
        return None
    depth, _zeroed = es_trough(m.get("endpointSlices") or {}, app_services)
    if depth is None:
        return None
    duration_real = es_trough_duration_real(m.get("endpointSliceTimeSeries") or {}, app_services)
    if duration_real is None:
        return None  # V2-H5 runs only on C1 sessions that have the sampler
    window = chaos_window_seconds(it)
    if window is None or window <= 0:
        return None
    error_rate = user_error_rate(m.get("latency") or {}, "during-chaos")
    if error_rate is None:
        return None
    depth_loss = _clamp01(depth / baseline)
    duration_loss = _clamp01(duration_real / window)
    error_loss = _clamp01(error_rate)
    return 100.0 * (1.0 - st.mean([depth_loss, duration_loss, error_loss]))


def mechanism_subscore(it: Dict[str, Any]) -> Optional[float]:
    """mechanism-reconvergence sub-score in ``[0, 100]`` for one iteration, or None.

    Mean of disturbance/reconvergence losses (D-2026-06-13-01 B).  ``None`` when
    the UDP drop or reconvergence time is not measurable, or the chaos window is
    unsourceable.
    """
    m = it.get("metrics") or {}
    samples = m.get("conntrackProtocolSamples") or []
    udp_pre = udp_cluster_phase_mean(samples, "pre-chaos")
    udp_dur = udp_cluster_phase_mean(samples, "during-chaos")
    if udp_pre is None or udp_dur is None:
        return None
    udp_drop = udp_pre - udp_dur
    # pool <= 0 -> no pool to flush -> zero disturbance loss (D-2026-06-13-01 B).
    disturbance_loss = _clamp01(udp_drop / udp_pre) if udp_pre > 0 else 0.0
    reconverg = udp_reconvergence_time_s(samples)
    if reconverg is None:
        return None
    window = chaos_window_seconds(it)
    if window is None or window <= 0:
        return None
    reconverg_loss = _clamp01(reconverg / window)
    return 100.0 * (1.0 - st.mean([disturbance_loss, reconverg_loss]))


def user_tail_subscore(it: Dict[str, Any]) -> Optional[float]:
    """user-tail sub-score in ``[0, 100]`` for one iteration, or None (exploratory).

    ``100 * min(1, control_p95 / dependent_p95)`` over during-chaos routes,
    classified the same way as ``h3_mechanism_outcome.py`` (D-2026-06-13-01 C).
    ``None`` when there is no dependent route, ``dependent_p95 ≤ 0``, or no
    control route.
    """
    latency = (it.get("metrics") or {}).get("latency") or {}
    dep_p95 = _route_p95_median(latency, "during-chaos", _dep)
    ctrl_p95 = _route_p95_median(latency, "during-chaos", _ctrl)
    if dep_p95 is None or dep_p95 <= 0 or ctrl_p95 is None:
        return None
    return 100.0 * min(1.0, ctrl_p95 / dep_p95)


def iteration_subscores(
    it: Dict[str, Any], app_services: Sequence[str]
) -> Dict[str, Optional[float]]:
    """All three sub-scores for one raw iteration record (None where not measurable)."""
    return {
        "availability": availability_subscore(it, app_services),
        "mechanism": mechanism_subscore(it),
        "user_tail": user_tail_subscore(it),
    }


# ──────────────────────────────────────────────────────────────────────
# Per-condition extraction over campaign sessions (taint-excluded)
# ──────────────────────────────────────────────────────────────────────


@dataclass
class ConditionObs:
    """One session-condition: per-iteration sub-score + v1-score rows.

    ``None`` rows are taint-excluded (or unmeasurable) iterations, kept so the
    nested ICC's between-iteration component sees the real per-iteration spread.
    """

    run: str
    condition: str
    subscores: Dict[str, List[Optional[float]]] = field(
        default_factory=lambda: {key: [] for key in SUBSCORES}
    )
    v1_score: List[Optional[float]] = field(default_factory=list)


def load_condition_subscores(
    session_dir: str,
    condition: str,
    tainted: set,
    taints: List[str],
    slope_band_taint: bool = False,
) -> Optional[ConditionObs]:
    """Per-iteration sub-score + v1-score rows for one condition's raw file.

    Mirrors :func:`m2_aa_analysis.load_condition_outcomes`: loads the
    ``<condition>.json``, folds ``preChaosTaintReasons`` into ``tainted`` in
    place, and emits a ``None`` row for every tainted iteration (the registered
    "never quoted" exclusion).  ``None`` when the raw file is missing.

    When ``slope_band_taint`` is set (C1 analysis — see :func:`analyze`), an
    iteration whose pre-window UDP slope leaves its f-level's frozen D3 band
    (:func:`m2_aa_analysis.udp_preslope_out_of_band`) is tainted too, exactly as
    the canonical loader does; the A/A block that defined the bands is never
    gated by them.
    """
    path = os.path.join(session_dir, f"{condition}.json")
    if not os.path.isfile(path):
        return None
    with open(path) as fh:
        raw = json.load(fh)
    app_services = sorted(((raw.get("placement") or {}).get("assignments")) or {})
    obs = ConditionObs(run=os.path.basename(session_dir), condition=condition)
    for idx, it in enumerate(raw.get("iterations") or [], start=1):
        iteration = it.get("iteration", idx)
        if it.get("preChaosTaintReasons"):
            taints.extend(
                f"{condition} it{iteration}: pre-chaos {reason}"
                for reason in it["preChaosTaintReasons"]
            )
            tainted.add((condition, iteration))
        if (condition, iteration) in tainted:
            for key in SUBSCORES:
                obs.subscores[key].append(None)
            obs.v1_score.append(None)
            continue
        metrics = it.get("metrics") or {}
        if slope_band_taint and udp_preslope_out_of_band(
            udp_pre_slope(metrics.get("conntrackProtocolSamples") or []), condition
        ):
            taints.append(f"{condition} it{iteration}: {SLOPE_BAND_TAINT_REASON}")
            tainted.add((condition, iteration))
            for key in SUBSCORES:
                obs.subscores[key].append(None)
            obs.v1_score.append(None)
            continue
        row = iteration_subscores(it, app_services)
        for key in SUBSCORES:
            obs.subscores[key].append(row[key])
        score = it.get("resilienceScore")
        score_valid = isinstance(score, (int, float)) and it.get("verdict") != "ERROR"
        obs.v1_score.append(float(score) if score_valid else None)
    del raw  # one raw file in memory at a time
    return obs


def collect_conditions(
    results_dir: str,
    slope_band_taint: bool = False,
) -> Tuple[List[ConditionObs], List[str], List[str]]:
    """Every campaign session-condition's per-iteration sub-scores.

    Discovers ``<results-dir>/*/summary.json`` v2 sessions (via
    :func:`m2_aa_analysis.parse_session`), reads each accepted condition's raw
    file, and returns ``(conditions, warnings, taints)``.  Rejected / not-
    accepted conditions are skipped with a warning — registered-invalid data
    must not enter the reliability estimate.  ``taints`` is the flat list of
    excluded-iteration descriptions (engine-side + raw pre-chaos taints).
    """
    conditions: List[ConditionObs] = []
    warnings: List[str] = []
    all_taints: List[str] = []
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
        del summary
        if session is None:
            continue
        # Reuse the session's engine-side taint set (parse_session already
        # folded perIteration.taintReasons in); load_condition_subscores adds
        # each raw file's preChaosTaintReasons on top, exactly as m2 does.
        tainted, taints = session.tainted, session.taints
        for condition, level in session.levels.items():
            if not level.accepted:
                warnings.append(
                    f"{run}: condition {condition} not accepted "
                    f"({', '.join(level.rejection_reasons) or 'rejected'}) — excluded"
                )
                continue
            obs = load_condition_subscores(
                run_dir, condition, tainted, taints, slope_band_taint=slope_band_taint
            )
            if obs is None:
                warnings.append(
                    f"{run}: raw {condition}.json missing — condition carries no sub-scores"
                )
                continue
            conditions.append(obs)
        all_taints.extend(f"{run}: {taint}" for taint in taints)
    return conditions, warnings, all_taints


def _clean_iterations(values: Sequence[Optional[float]]) -> List[float]:
    """The non-None, non-NaN per-iteration values for an ICC cell."""
    return [float(v) for v in values if isinstance(v, (int, float)) and not math.isnan(float(v))]


# ──────────────────────────────────────────────────────────────────────
# Condition-level ICC + the V2-H5 evaluation (frozen §V2-H5)
# ──────────────────────────────────────────────────────────────────────


def condition_icc(
    metric_cells: Dict[Tuple[str, str], List[float]],
    confidence: float,
    n_resamples: int,
    seed: Optional[int],
) -> Dict[str, Any]:
    """Condition-level ICC for one metric via the cluster-bootstrap helper.

    ``metric_cells`` is ``{(condition, session): [per-iteration values]}`` — the
    "strategy" role is the session-condition, the "run" role is the session.
    """
    return dict(
        icc_bootstrap(metric_cells, confidence=confidence, n_resamples=n_resamples, seed=seed)
    )


def _diff_bootstrap_excludes_zero(
    sub_cells: Dict[Tuple[str, str], List[float]],
    v1_cells: Dict[Tuple[str, str], List[float]],
    confidence: float,
    n_resamples: int,
    seed: Optional[int],
) -> Dict[str, Any]:
    """Paired bootstrap on ``ICC_sub - ICC_v1`` over the shared resample draw.

    Both ICCs are recomputed on the SAME resampled set of conditions/sessions
    each iteration, so the difference is paired (cancels the shared sampling
    noise).  Returns the point difference, the CI, whether the CI excludes 0,
    and a two-sided bootstrap p-value for ``diff = 0`` (frozen §V2-H5: the CI
    on ICC_new − ICC_old must exclude zero).
    """

    def _point_diff(
        a: Dict[Tuple[Any, Any], List[float]],
        b: Dict[Tuple[Any, Any], List[float]],
    ) -> Optional[float]:
        ia = float(_icc_point(a)["icc"])
        ib = float(_icc_point(b)["icc"])
        if math.isfinite(ia) and math.isfinite(ib):
            return ia - ib
        return None

    point = _point_diff(dict(sub_cells), dict(v1_cells))

    # Group both metrics by the shared (condition -> sessions) structure so a
    # single resample draws the same conditions/sessions for both ICCs.
    cond_to_sessions: Dict[Any, List[Any]] = defaultdict(list)
    for cond, sess in sub_cells:
        cond_to_sessions[cond].append(sess)
    conditions: List[Any] = sorted(cond_to_sessions, key=repr)

    boot: List[float] = []
    if conditions:
        rng = random.Random(seed)
        n_cond = len(conditions)
        for _ in range(n_resamples):
            resampled_sub: Dict[Tuple[Any, Any], List[float]] = {}
            resampled_v1: Dict[Tuple[Any, Any], List[float]] = {}
            for ci in range(n_cond):
                cond = conditions[rng.randrange(n_cond)]
                sessions = cond_to_sessions[cond]
                synth = (cond, ci)
                for sj in range(len(sessions)):
                    sess = sessions[rng.randrange(len(sessions))]
                    resampled_sub[(synth, (sess, sj))] = sub_cells[(cond, sess)]
                    resampled_v1[(synth, (sess, sj))] = v1_cells[(cond, sess)]
            diff = _point_diff(resampled_sub, resampled_v1)
            if diff is not None:
                boot.append(diff)

    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    p_value: Optional[float] = None
    if boot:
        boot.sort()
        alpha = (1.0 - confidence) / 2.0
        ci_low = round(_percentile(boot, alpha), 4)
        ci_high = round(_percentile(boot, 1.0 - alpha), 4)
        # Two-sided bootstrap p for diff = 0: twice the smaller tail mass.
        below = sum(1 for d in boot if d <= 0.0) / len(boot)
        above = sum(1 for d in boot if d >= 0.0) / len(boot)
        p_value = round(min(1.0, 2.0 * min(below, above)), 4)

    excludes_zero = ci_low is not None and ci_high is not None and (ci_low > 0.0 or ci_high < 0.0)
    return {
        "pointDiff": round(point, 4) if point is not None else None,
        "ciLow": ci_low,
        "ciHigh": ci_high,
        "excludesZero": excludes_zero,
        "pValue": p_value,
        "nResamples": len(boot),
    }


def evaluate_subscore(
    name: str,
    sub_cells: Dict[Tuple[str, str], List[float]],
    v1_cells: Dict[Tuple[str, str], List[float]],
    confidence: float,
    n_resamples: int,
    seed: Optional[int],
) -> Dict[str, Any]:
    """The full per-sub-score evaluation row (frozen §V2-H5 test rule).

    Pass requires BOTH: (1) the bootstrap CI on ``ICC_sub - ICC_v1`` excludes
    0, AND (2) ``ICC_sub ≥ 0.5`` with its own CI excluding ICC_v1.  The
    comparator ICC_v1 is computed on the SAME (aligned) cells as the sub-score,
    so the head-to-head is fully paired.  ``evaluable`` is False when the
    sub-score has too few conditions / no measurable values (then pass is
    None — "not evaluable", never a crash).
    """
    icc_sub = condition_icc(sub_cells, confidence, n_resamples, seed)
    icc_v1 = condition_icc(v1_cells, confidence, n_resamples, seed)
    n_conditions = icc_sub["n_strategies"]
    # A condition observed in <2 sessions has no test-retest replicate to
    # disagree, so it inflates ICC toward 1.0 (a degenerate, silently
    # optimistic reliability). The frozen C1 design guarantees >=2 sessions
    # per condition; surfacing any thin-replication condition makes an
    # off-nominal inflation visible instead of silent.
    thin_replication = _thin_replication_conditions(sub_cells)
    evaluable = (
        n_conditions >= MIN_CONDITIONS and icc_sub["icc"] is not None and icc_v1["icc"] is not None
    )
    diff: Optional[Dict[str, Any]] = None
    abs_bar_ok: Optional[bool] = None
    ci_excludes_v1: Optional[bool] = None
    passed: Optional[bool] = None
    p_value: Optional[float] = None
    if evaluable:
        diff = _diff_bootstrap_excludes_zero(sub_cells, v1_cells, confidence, n_resamples, seed)
        icc_old = float(icc_v1["icc"])
        abs_bar_ok = float(icc_sub["icc"]) >= ABSOLUTE_ICC_BAR
        ci_excludes_v1 = icc_sub["ci_low"] is not None and float(icc_sub["ci_low"]) > icc_old
        passed = bool(diff["excludesZero"] and abs_bar_ok and ci_excludes_v1)
        p_value = diff["pValue"]
    return {
        "subscore": name,
        "role": "required" if name in REQUIRED_SUBSCORES else "exploratory",
        "evaluable": evaluable,
        "nConditions": n_conditions,
        "iccSub": icc_sub["icc"],
        "iccSubCiLow": icc_sub["ci_low"],
        "iccSubCiHigh": icc_sub["ci_high"],
        "iccV1Aligned": icc_v1["icc"],
        "diffVsV1": diff,
        "absBarOk": abs_bar_ok,
        "ciExcludesV1": ci_excludes_v1,
        "pass": passed,
        "pValue": p_value,
        "thinReplicationConditions": thin_replication,
    }


def _thin_replication_conditions(cells: Dict[Tuple[str, str], List[float]]) -> List[str]:
    """Conditions with <2 contributing sessions (no test-retest replicate).

    Each such condition makes the ICC degenerate toward 1.0; returned sorted
    so the report/JSON can flag the off-nominal inflation explicitly.
    """
    sessions_per_condition: Dict[str, int] = {}
    for condition, _session in cells:
        sessions_per_condition[condition] = sessions_per_condition.get(condition, 0) + 1
    return sorted(c for c, n in sessions_per_condition.items() if n < 2)


def _cell_median(values: Sequence[Optional[float]]) -> Optional[float]:
    """The session-condition unit (D-2026-06-13-01): median over untainted iters."""
    clean = _clean_iterations(values)
    return st.median(clean) if clean else None


def _metric_cells(
    conditions: Sequence[ConditionObs], metric: str
) -> Dict[Tuple[str, str], List[float]]:
    """ICC cells for one sub-score: ``{(condition, session): [session-median]}``.

    The registered unit is the **session-condition median over untainted
    iterations** (D-2026-06-13-01), so each cell is a single-element list — the
    "condition-level ICC" is then the test-retest reliability of that median
    (between-condition variance / total).  Cells whose median is unmeasurable
    (all iterations None) contribute nothing.
    """
    cells: Dict[Tuple[str, str], List[float]] = {}
    for obs in conditions:
        median = _cell_median(obs.subscores[metric])
        if median is not None:
            cells[(obs.condition, obs.run)] = [median]
    return cells


def _v1_cells_aligned(
    conditions: Sequence[ConditionObs], keep: set
) -> Dict[Tuple[str, str], List[float]]:
    """v1-score ICC cells (session medians) restricted to the cells a sub-score has.

    The paired ICC difference must compare the SAME cells, so the v1 cells are
    aligned to the sub-score's measurable cells.
    """
    cells: Dict[Tuple[str, str], List[float]] = {}
    for obs in conditions:
        key = (obs.condition, obs.run)
        if key not in keep:
            continue
        median = _cell_median(obs.v1_score)
        if median is not None:
            cells[key] = [median]
    return cells


def analyze(
    results_dir: str,
    confidence: float = 0.95,
    n_resamples: int = 2000,
    seed: Optional[int] = 42,
    slope_band_taint: bool = True,
) -> Dict[str, Any]:
    """The full V2-H5 scorecard reliability analysis as one JSON-ready dict.

    ``slope_band_taint`` defaults ON: this is the C1 campaign tool, so the
    frozen D3 pre-window UDP-slope gate (DEVIATIONS.md D-2026-06-14-01) applies.
    Disable it (``--no-slope-band-taint``) only to analyze the A/A block, which
    must never be gated by the bands it defined.
    """
    conditions, warnings, taints = collect_conditions(
        results_dir, slope_band_taint=slope_band_taint
    )

    # v1 aggregate ICC over all measurable cells (ICC_old comparator).
    v1_all = _metric_cells_v1(conditions)
    icc_v1 = condition_icc(v1_all, confidence, n_resamples, seed)

    subscore_rows: List[Dict[str, Any]] = []
    for name in SUBSCORES:
        sub_cells = _metric_cells(conditions, name)
        v1_aligned = _v1_cells_aligned(conditions, set(sub_cells))
        # Restrict the sub-score cells to those the v1 score also covers, so
        # the paired difference is over a common, fully-paired set.
        common = set(sub_cells) & set(v1_aligned)
        sub_cells = {k: v for k, v in sub_cells.items() if k in common}
        v1_aligned = {k: v for k, v in v1_aligned.items() if k in common}
        subscore_rows.append(
            evaluate_subscore(name, sub_cells, v1_aligned, confidence, n_resamples, seed)
        )

    by_name = {row["subscore"]: row for row in subscore_rows}
    required_rows = [by_name[name] for name in REQUIRED_SUBSCORES]
    all_evaluable = all(row["evaluable"] for row in required_rows)
    if all_evaluable:
        conjunction_pass = all(bool(row["pass"]) for row in required_rows)
        # Holm input = max(p_availability, p_mechanism) (frozen §V2-H5).
        p_values = [row["pValue"] for row in required_rows if row["pValue"] is not None]
        holm_input = max(p_values) if len(p_values) == len(required_rows) else None
        verdict = "PASS" if conjunction_pass else "FAIL"
    else:
        conjunction_pass = None
        holm_input = None
        verdict = "NOT_EVALUABLE"

    return {
        "schema": SCHEMA,
        "resultsDir": results_dir,
        "confidence": confidence,
        "nResamples": n_resamples,
        "seed": seed,
        "absoluteIccBar": ABSOLUTE_ICC_BAR,
        "deviation": "D-2026-06-13-01",
        "warnings": warnings,
        "nSessions": len({obs.run for obs in conditions}),
        "nConditionSessionCells": len(conditions),
        "taintedIterations": taints,
        "iccV1": {
            "icc": icc_v1["icc"],
            "ciLow": icc_v1["ci_low"],
            "ciHigh": icc_v1["ci_high"],
            "nConditions": icc_v1["n_strategies"],
            "nObservations": icc_v1["n_obs"],
        },
        "subscores": subscore_rows,
        "decision": {
            "requiredSubscores": list(REQUIRED_SUBSCORES),
            "exploratorySubscores": list(EXPLORATORY_SUBSCORES),
            "conjunctionPass": conjunction_pass,
            "holmInput": holm_input,
            "verdict": verdict,
        },
    }


def _metric_cells_v1(
    conditions: Sequence[ConditionObs],
) -> Dict[Tuple[str, str], List[float]]:
    """ICC cells (session medians) for the v1 aggregate score, all measurable cells."""
    cells: Dict[Tuple[str, str], List[float]] = {}
    for obs in conditions:
        median = _cell_median(obs.v1_score)
        if median is not None:
            cells[(obs.condition, obs.run)] = [median]
    return cells


# ──────────────────────────────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────────────────────────────


def _fmt(value: Optional[float], digits: int = 4) -> str:
    """Fixed-width numeric cell, ``-`` for absent."""
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "-"


def print_report(result: Dict[str, Any]) -> None:
    """Human-readable rendering of the analysis dict."""
    print(
        f"V2-H5 layered scorecard reliability — {result['resultsDir']} "
        f"(deviation {result['deviation']}, absolute ICC bar {result['absoluteIccBar']})"
    )
    print(
        f"\nSessions: {result['nSessions']}  "
        f"condition-session cells: {result['nConditionSessionCells']}"
    )
    if result["warnings"]:
        print("\n=== Warnings ===")
        for warning in result["warnings"]:
            print(f"  WARNING: {warning}")

    if result["taintedIterations"]:
        print("\n=== Tainted iterations (excluded from every sub-score) ===")
        for taint in result["taintedIterations"]:
            print(f"  TAINTED (excluded): {taint}")

    v1 = result["iccV1"]
    print(
        f"\nv1 aggregate ICC_old = {_fmt(v1['icc'])} "
        f"[{_fmt(v1['ciLow'])}, {_fmt(v1['ciHigh'])}]  "
        f"(conditions={v1['nConditions']}, obs={v1['nObservations']})"
    )

    print("\n=== Sub-scores ===")
    print(
        f"  {'sub-score':<14}{'role':<12}{'ICC':>8}{'CI':>20}"
        f"{'>=0.5?':>8}{'CI>v1?':>8}{'diff!=0?':>10}{'p':>9}  verdict"
    )
    for row in result["subscores"]:
        if not row["evaluable"]:
            print(
                f"  {row['subscore']:<14}{row['role']:<12}{'-':>8}{'(not evaluable)':>20}"
                f"{'-':>8}{'-':>8}{'-':>10}{'-':>9}  NOT EVALUABLE"
            )
            continue
        diff = row["diffVsV1"] or {}
        ci = f"[{_fmt(row['iccSubCiLow'])},{_fmt(row['iccSubCiHigh'])}]"
        excl = diff.get("excludesZero")
        verdict = "PASS" if row["pass"] else "FAIL"
        tag = "" if row["role"] == "required" else " (exploratory — excluded)"
        print(
            f"  {row['subscore']:<14}{row['role']:<12}{_fmt(row['iccSub']):>8}{ci:>20}"
            f"{('Y' if row['absBarOk'] else 'N'):>8}"
            f"{('Y' if row['ciExcludesV1'] else 'N'):>8}"
            f"{('Y' if excl else 'N'):>10}{_fmt(row['pValue']):>9}  {verdict}{tag}"
        )
        thin = row.get("thinReplicationConditions") or []
        if thin:
            print(
                f"    ! thin replication — {len(thin)} condition(s) with <2 sessions "
                f"inflate this ICC toward 1.0: {', '.join(thin)}"
            )

    decision = result["decision"]
    print("\n=== V2-H5 decision (required conjunction; user-tail excluded) ===")
    print(f"  conjunction pass: {decision['conjunctionPass']}")
    print(f"  Holm input (max p of required): {_fmt(decision['holmInput'])}")
    print(f"  VERDICT: {decision['verdict']}")


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface (also exercised by tests)."""
    parser = argparse.ArgumentParser(
        description=(
            "V2-H5 layered scorecard: computes the three per-iteration sub-scores "
            "(availability, mechanism-reconvergence, user-tail), aggregates each to "
            "the session-condition median over untainted iterations, and runs the "
            "frozen V2-H5 reliability evaluation (condition-level ICC per sub-score "
            "vs the v1 aggregate; required conjunction; user-tail exploratory). "
            "Aggregation formulas per DEVIATIONS.md D-2026-06-13-01."
        ),
    )
    parser.add_argument(
        "--results-dir",
        default="results/v2-c1",
        help="directory of <run>/summary.json v2 campaign sessions (default results/v2-c1)",
    )
    parser.add_argument(
        "--json",
        help="optional: write the full analysis dict to this path for downstream tooling",
    )
    parser.add_argument(
        "--confidence", type=float, default=0.95, help="bootstrap confidence level (default 0.95)"
    )
    parser.add_argument(
        "--n-resamples", type=int, default=2000, help="bootstrap resamples (default 2000)"
    )
    parser.add_argument("--seed", type=int, default=42, help="bootstrap RNG seed (default 42)")
    parser.add_argument(
        "--slope-band-taint",
        dest="slope_band_taint",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="apply the frozen D3 pre-window UDP-slope taint gate (default on; "
        "use --no-slope-band-taint to analyze the A/A block, which it must not gate)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Run the analysis; exit codes are documented in the module docstring."""
    args = build_parser().parse_args(argv)
    result = analyze(
        args.results_dir,
        confidence=args.confidence,
        n_resamples=args.n_resamples,
        seed=args.seed,
        slope_band_taint=args.slope_band_taint,
    )
    print_report(result)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"\nJSON written to {args.json}")
    verdict = result["decision"]["verdict"]
    if verdict == "PASS":
        return 0
    if verdict == "NOT_EVALUABLE":
        if result["nConditionSessionCells"] == 0:
            return 2
        return 1
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
