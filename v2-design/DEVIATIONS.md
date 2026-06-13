# ChaosProbe v2 — deviations from the frozen pre-registration

Per [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) §Deviations policy: the
pre-registration was **frozen 2026-06-13** at git tag `v2-prereg-freeze`.
After that commit, **every** deviation from the registered plan — a changed
per-cell n, a dropped or added cell, a modified or substituted test, a
revised threshold/SESOI/margin, an instrumentation change that affects an
outcome — is logged here, each with:

- **date** of the decision,
- **what** changed (registered value/rule → new value/rule),
- **why** (the reason),
- **blind?** — whether the decision was made *blind to outcome data* (i.e.
  before seeing the campaign results that the change could bias), and
- **decision ID** if it ties back to a numbered freeze decision.

Pre-freeze amendments (D1–D7, the M1b carry-overs, etc.) are **not**
deviations — they were applied before the freeze and are recorded in the
pre-registration's §M2 freeze amendments. This file is for changes made
**after** 2026-06-13.

---

## Deviations log

### D-2026-06-13-01 — V2-H5 sub-score aggregation formulas specified

- **date:** 2026-06-13
- **what:** the M2 freeze (tag `v2-prereg-freeze`) froze V2-H5's evaluation and
  the *constituent signals* of each sub-score but did not pin the *aggregation
  formulas*. They are specified here exactly (below). No registered hypothesis
  statement, falsification rule, SESOI, or n changes.
- **why:** the prereg claimed "sub-score definitions frozen at the M2 commit";
  the commit froze signals + evaluation but not the scalar formulas. Closing
  that gap.
- **blind?:** YES — fixed before any C1 reliability data exists; the only v2
  data is the M2 A/A null (same-placement) block, and the formulas were NOT
  tuned against it. This preserves the circularity-guard intent (definitions
  fixed before reliability data).
- **decision ID:** ties to V2-H5 (pre-registration §V2-H5) and DESIGN §5; this
  is the formula-level completion of the frozen signal/evaluation set.
- **formulas:** one scalar per session-condition, higher = better, range
  `[0, 100]`. Per ITERATION each sub-score is computed; the session-condition
  value is the **median over that condition's non-tainted iterations** (tainted
  iterations excluded via the existing `scripts/m2_aa_analysis.py` taint
  handling). Each constituent is a `[0, 1]` "loss" (clamped to `[0, 1]`); a
  sub-score is `100 × (1 − mean(losses))`. Direction: all three higher = better
  (100 = no degradation). The v1 aggregate `score` outcome (already defined in
  `m2_aa_analysis.py`) is the ICC_old comparator and is unchanged.

  **A) availability (required)** — mean of three losses:
  - `depth_loss = trough_depth_pods / baseline_ready_endpoints`, where
    `baseline_ready_endpoints` = total `ready` summed over the iteration's app
    services in the **last pre-chaos EndpointSlice sample** (from
    `metrics.endpointSliceTimeSeries`; falls back to
    `metrics.endpointSlices.preChaos` if the time series is absent or carries no
    pre-chaos sample). If `baseline ≤ 0` → loss undefined → sub-score `None`.
  - `duration_loss = trough_duration_real_s / chaos_window_seconds`, using the
    **real** trough duration (`es_trough_duration_real`, from the
    `endpointSliceTimeSeries` added in #280). If the real series is **absent**
    (e.g. frozen A/A data) → availability sub-score = `None` (V2-H5 runs only on
    C1 sessions that have the sampler).
  - `error_loss = user_route_error_rate during chaos` (the existing
    `user_err_during` outcome).

  **B) mechanism-reconvergence (required)** — mean of two losses:
  - `disturbance_loss = UDP_conntrack_drop_entries / pre_chaos_UDP_pool`, where
    `pre_chaos_UDP_pool` = mean UDP entries summed over nodes in the pre-chaos
    window (the existing `udp_cluster_phase_mean(..., "pre-chaos")` that the UDP
    drop extractor already uses). `pool ≤ 0` → `disturbance_loss = 0.0` (no pool
    to flush). (The ratio is used here only for sub-score scaling — NOT for the
    V2-H2 hypothesis test, which is registered on absolute UDP drops.)
  - `reconverg_loss = conntrack_reconvergence_time_s / chaos_window_seconds`,
    from a NEW extractor over the `conntrackProtocolSamples` UDP series:
    reconvergence time = (first during/post-chaos sample whose summed-UDP-over-
    nodes returns to ≥ the pre-chaos baseline pool) minus chaos-start. Never
    recovers in-window → capped at the last sample span (a lower bound); never
    drops → `0.0`; no baseline / no samples → `None`. (Mirrors the shape of
    `es_trough_duration_real`.)

  **C) user-tail (exploratory)** — `100 × min(1, control_route_p95 /
  dependent_route_p95)`. Control vs dependent routes are classified **the same
  way** as `scripts/h3_mechanism_outcome.py` (its `_dep` / `_ctrl` classifiers
  are reused, not reinvented). Uses **p95** (not p99), during-chaos. The p95
  over multiple matching routes is reduced by the **median** (consistent with
  `m2_aa_analysis.east_west_p95`, which also takes the median over routes).
  `dependent_p95 ≤ 0` (or no dependent route) → `None`.

  **chaos_window_seconds** is sourced per iteration from the iteration record's
  top-level `anomalyLabels[*].parameters.duration_s` (the recorded
  `TOTAL_CHAOS_DURATION`); when that is absent or non-positive it is derived
  from the during-chaos sample span (last − first during-chaos timestamp in the
  EndpointSlice time series, falling back to the conntrack UDP series). When no
  window can be sourced, the affected duration / reconvergence loss is `None`,
  propagating to a `None` sub-score.
