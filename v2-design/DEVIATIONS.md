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

### D-2026-06-14-01 — D3 per-f-level UDP-slope taint band edges specified

- **date:** 2026-06-14
- **what:** the M2 freeze (D3) registered the pre-window UDP-slope validity check
  as **per-f-level slope bands sourced from the 2026-06-12 A/A block** but did not
  pin the band-edge *rule* (how wide each band is). It is specified here as
  **`round(mean ± 3·SD)`** of the untainted per-iteration `udp_preslope_epm` at
  each f-level, pooled over the 6 A/A sessions, where SD is the **population SD**
  of that A/A reference set (`round` = Python round-half-to-even; no frozen edge
  sits on a half-integer). The reference set applies the same exclusions the
  canonical M2 path does — not-accepted conditions dropped, then per-iteration
  taint exclusion (every A/A condition was accepted, so this does not move the
  values; it keeps the audit script faithful to the canonical exclusion). No
  registered hypothesis statement, falsification rule, SESOI, n, or the D3
  decision itself changes — only the previously unspecified band-edge formula is
  fixed.
- **why:** the prereg cited the A/A slope ranges (≈ +140 to +870 entries/min at
  f-025/f-050, ≈ −6600 to −8800 at f-075/f-100) and said the bands come "from the
  A/A block artifact," but the exact edge rule was left open (task #11). A `3·SD`
  control limit taints essentially none of the A/A null (0/0/0/1/0 of n≈18 per
  level) while keeping a real margin beyond the observed envelope, so normal
  placement-coupled transients pass on C1 and only genuine anomalies (e.g. the
  s6 f-100 wedge, far outside the band) are caught. Rejected alternatives:
  empirical `[min,max]` (zero margin → over-taints C1); `median ± k·MAD` (the
  skewed f-025 distribution self-taints 6/18); `[p2.5, p97.5]` (drops 5% of the
  null by construction).
- **blind?:** YES — derived only from the M2 A/A null (same-placement) block,
  with **no access to any C1 outcome**; the rule was fixed before C1 analysis.
  (C1-online-boutique data exists but was not consulted for this; the band feeds
  C1's taint gate, so deriving it from C1 would be circular.)
- **decision ID:** ties to freeze decision **D3** (pre-registration §Session
  design, §M2 freeze amendments) — the edge-rule completion of the registered
  per-f-level-band check.
- **frozen bands (entries/min):** an iteration is tainted (reason
  `udp_preslope_out_of_band`) when its pre-window UDP-entry slope falls outside
  its f-level's band.

  | f-level | band |
  |---|---|
  | f-000 | [−81, 56] |
  | f-025 | [−358, 1022] |
  | f-050 | [414, 1084] |
  | f-075 | [−11211, −3867] |
  | f-100 | [−8766, −5519] |

  The values are frozen in `chaosprobe/scripts/m2_aa_analysis.py`
  (`D3_UDP_SLOPE_BANDS_EPM`) and applied via
  `load_condition_outcomes(..., slope_band_taint=True)` — **C1 analysis only**;
  the A/A block that defined them is never gated by them.
  `chaosprobe/scripts/d3_slope_bands.py` re-derives them from `results/v2-aa/`
  (a parity test asserts the committed constants still match the raw data).

### D-2026-06-14-02 — D3 UDP-slope taint removed from the C1 V2-H1 / V2-H5 analyses

- **date:** 2026-06-14
- **what:** the frozen D3 pre-window UDP-slope taint (D-2026-06-14-01) is **not
  applied** to the C1 V2-H1 (`scripts/c1_h1_trend.py`) and V2-H5
  (`scripts/scorecard.py`) analyses — both now default `slope_band_taint=False`,
  with `--slope-band-taint` retained for a sensitivity run. The band/constants
  are unchanged; only their application to these two analyses is withdrawn. No
  hypothesis statement, SESOI, n, or other taint gate changes.
- **why:** applied to the real C1 online-boutique data, the D3 band taints
  **24/24 iterations at both f-025 and f-050** (zero complete blocks → Page's L
  and the scorecard cannot run). Diagnosis (read-only) established:
  1. **Not an instrument artifact** — pre-chaos UDP sampling is identical
     between the A/A block and C1 (≈88–96 samples, ~60 s window, 8 nodes).
  2. **A structural regime difference** — A/A's interior-level (f-025/f-050)
     pre-window UDP pool was small and *growing* (band positive there); C1
     re-places per f-level, so its pre-window catches a large post-re-placement
     DNS/UDP conntrack burst *draining* at every non-zero level (slope ≈ −9000).
     The A/A-derived band does not describe the C1 regime — the exact
     "placement-coupled transient" instability the M2 report pre-flagged (F2).
  3. **The taint discards the cleanest data** — the V2-H1 latency baseline
     (`ew_p95_pre_ms`) at the tainted levels is the *most* stable of all levels
     (f-050 CV ≈ 2 %, f-025 ≈ 12 %; the *passing* f-075 is the noisiest at
     ≈ 18 %). The east-west p95 is TCP/gRPC latency; the pre-window UDP pool is
     DNS conntrack and is not a validity precondition for it.
- **blind?:** **NO** — decided after observing that the registered gate tainted
  the interior levels on C1. The justification rests on objective, documentable
  facts (identical sampling; latency baseline cleanest at the tainted levels;
  the gated signal is a different protocol from the outcome) and on the M2 F2
  pre-flag, not on the hypothesis outcome. **Reported transparently both ways:**
  the primary V2-H1/V2-H5 results run with the slope-taint OFF; the
  slope-taint-ON result (unrunnable for V2-H1) is reported as the limitation.
- **decision ID:** ties to **D3** and to D-2026-06-14-01 (the band whose
  application is withdrawn here).
- **scope:** the withdrawal is limited to the C1 V2-H1 / V2-H5 latency-side
  analyses. The slope-taint remains applicable where the UDP/DNS conntrack pool
  *is* the measurement (V2-H2, the conntrack-mechanism hypothesis, C3). The
  forward fix — lengthening the post-(re)placement settle so the pre-chaos
  window starts after the conntrack burst drains — is recorded for C2/C3 as a
  protocol change, not applied retroactively to C1.

### D-2026-06-15-01 — V2-H3 trough-depth operationalized as a fraction

- **date:** 2026-06-15
- **what:** the V2-H3 EndpointSlice trough-depth co-primary is computed as a
  **fraction of app ready-endpoints lost** (`(baseline − min during/post) /
  baseline`, over the app services in the 15s series, infra excluded), not as
  an absolute pod count. The registered **1.0-pod** rescue margin / TOST band is
  expressed as a fraction by dividing by the r=1 app baseline (1.0 pod ÷ r=1
  ready ≈ 0.09 for online-boutique; computed per-analysis as
  `1.0 / median(r1 baseline ready)`). The hypothesis statement, the test (ART
  interaction), the n, and the error-rate co-primary (0.302) are unchanged.
- **why:** absolute pod depth **scales with the replica count** — when its node
  is drained, r=1 loses 1 pod, r=3 packed loses all 3 — so the registered
  *packed ≈ r1* TOST equivalence control (the 1.0-pod band was measured at r=1)
  cannot hold in pod units (packed structurally differs from r1 by the replica
  count, not by any rescue). The fraction is **r-invariant** (r1 ≈ packed ≈
  full-loss; anti-affine ≪) so the control becomes meaningful while the rescue
  contrast is preserved. Verified on the (instrument-invalid) first C2 campaign:
  absolute depth r1 8 / packed 17 / anti 1.5 (scales with r) vs fractional
  r1 0.57 / packed 0.63 / anti 0.04 (packed ≈ r1, anti rescues).
- **blind?:** the choice rests on a **structural/mechanical** property of
  absolute pod depth (it scales with r by construction), not on the V2-H3
  verdict; the first C2 campaign whose depth values illustrate it is being
  **discarded and re-run** (its error co-primary was invalid — port-forward bug,
  PR #288/#289), so no hypothesis outcome from valid data informed this. The
  fractional margin and verdict are insensitive to the exact band (the observed
  rescue ≈ 0.5 dwarfs the ≈ 0.09 margin).
- **decision ID:** ties to **V2-H3** (pre-registration §V2-H3); the trough-depth
  margin was registered as "1.0 pod" — this fixes its operationalization for the
  r-varying node-drain design.
