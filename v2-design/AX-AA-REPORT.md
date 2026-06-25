# AX report — node-drain A/A calibration block + power analysis: **PASS (with freeze decisions pending)**

Companion to [`AX-PREREGISTRATION-DRAFT.md`](AX-PREREGISTRATION-DRAFT.md) §5–§6
and §8. This report fills the prereg's open TBDs from a dedicated **node-drain
A/A calibration block** and records the freeze decisions the user must make before
the AX pre-registration becomes binding. It mirrors
[`M2-AA-REPORT.md`](M2-AA-REPORT.md) in structure and discipline.

> **Headline finding (see §Findings F1 and §Freeze decision DA1): AX-H3 is
> NOT RUNNABLE as designed.** The availability outcomes are (near-)deterministic
> at fixed placement under node-drain — the prereg's bet that the *dynamic*
> outage would supply noise-like run-to-run variance does not hold — so the
> test-retest ICC is not a valid estimand. The prereg's own §3 "not-runnable
> hatch" anticipated exactly this. AX-H1, AX-H2, AX-H4 are unaffected and well
> calibrated. This is the A/A block doing its job: retiring a non-viable
> hypothesis **before** the freeze, not after collecting confirmatory data.

---

## Block design (registered protocol, instantiated)

Per prereg §5 / §7: **6 fixed-placement node-drain sessions** = 3 identical-placement
pairs, online-boutique, `node-drain` at r=1, complete-block over the 5 designed
f-levels {0, 0.25, 0.5, 0.75, 1.0}, 3 iterations/condition. **All 6 sessions share
`--v2-solver-seed 0`** (one canonical placement per f-level, held fixed across all
replicates); only `--v2-order-seed` varies (1…6) — the run-to-run, fixed-placement
design AX-H3's test-retest variance partition requires (prereg §3, §5).

| session dir | orderSeed | solverSeed | commit | git.dirty | doctor --strict |
|---|---|---|---|---|---|
| 20260625-055954 | 1 | 0 | 479d2399 | False | 0 err, 2 warn |
| 20260624-122901 | 2 | 0 | 1bc6f602 | False | 0 err, 2 warn |
| 20260624-150025 | 3 | 0 | 1bc6f602 | False | 0 err, 2 warn |
| 20260624-173302 | 4 | 0 | 479d2399 | False | 0 err, 2 warn |
| 20260624-201141 | 5 | 0 | 479d2399 | False | 0 err, 2 warn |
| 20260625-083446 | 6 | 0 | 479d2399 | False | 0 err, 2 warn |

**Provenance.** All 6 `git.dirty=False`. The block spans two commits (1bc6f602,
479d2399); the diff between them touches **only** `scripts/run_ax_aa_nodedrain.sh`
(the driver) and `uv.lock` (msgpack 1.1.2→1.2.1, a transport dependency) — the
measurement/orchestrator/metrics code path is byte-identical
(`git diff 1bc6f602 479d2399 -- chaosprobe/`). Sessions 1 and 6 were re-collected
on the clean HEAD (2026-06-25) to replace an originally dirty session-1 (untracked
driver, C2-style) and an interrupted session-6 partial; the superseded copies are
under `results/ax-aa-nodedrain-superseded/`. **0 tainted iterations** in any banked
session. Each session's two `doctor --strict` warnings are the expected node-drain
artifact: (i) all litmus resilience scores are 0 — probes go `Unknown` on the
drained node, so the real signal is the EndpointSlice trough, not the score; (ii) a
Locust offered-RPS spread across conditions (informational for calibration).

---

## Verdict — registered A/A functions

### 1. Variance components (all 6 sessions, fixed placement)

Unit per prereg §3: facet **condition** = f-level/placement (between);
facet **session** = replicate at the fixed placement (within / test-retest).
Computed on the taint-excluded canonical per-iteration extraction
(`scripts/ax_aa_all6_variance.py`, output
`results/ax-aa-nodedrain/ax-aa-all6-variance.txt`). ICC_test-retest =
between-condition / (between-condition + within-condition[between-session]).

| outcome | between-condition sd | within-condition (between-session) sd | ICC | character |
|---|---|---|---|---|
| EndpointSlice trough **depth** (pods) | 2.33 | **0** | 1.000 | **deterministic** |
| services driven to 0 (blast radius) | 2.33 | **0** | 1.000 | **deterministic** |
| trough **duration**, ES time-series (s) | 6.82 | 3.54 | 0.788 | quantized (30/45/60 s) |
| trough duration proxy = recovery (s) | 0.58 | 0.50 | 0.581 | small, ~noise |
| **integrated outage** = depth×duration | 134.6 | 29.35 | 0.955 | quantized within |
| **user-route error** during drain | 0.081 | 0.024 | 0.917 | real run-to-run noise |
| east-west p95 pre-chaos (ms) | 1.70 | 1.43 | 0.585 | real run-to-run noise |

Per-session depth medians are **bit-identical** across all six sessions at every
f: f-000=11, f-025=9, f-050=7, f-075=7, f-100=4 pods (between-session sd exactly 0).
Trough duration is pinned to the chaos window — ~30 s at f-025/050/075 in all six
sessions (between-session sd ≈ 0.002–0.005 s), ~45 s at f-000, with **rare discrete
quantum-jumps**: one session jumped f-000 45→60 s and one jumped f-100 30→45 s.
Integrated outage inherits this: deterministic at 3/5 conditions (sd ≈ 0.01–0.04),
with a single-outlier jump at f-000 and f-100.

### 2. A/A-as-A/B null tests

`scripts/aa_block.py --results-dir results/ax-aa-nodedrain`
(`results/ax-aa-nodedrain/ax-aa-block.txt`). **No statistically significant A/A
finding at α=0.05** across every outcome, pairing level (condition-median and
iteration), and pair — the registered investigate→fix→rerun trigger does **not**
fire. (Pipeline-sanity caveat in §Findings F2: `aa_block.py`'s solver-seed pairing
consumes only one of the three pairs because all six sessions share one solver
seed; the variance/band numbers in this report are therefore taken from the
all-6-session analysis, which is the correct unit for this all-identical-placement
design — the null tests are reported from `aa_block.py` as-is.)

### 3. All-pairs A/A noise bands (|session_i − session_j| at fixed f, pooled, n=75 pairs)

Computed by `scripts/ax_aa_all6_variance.py` (the "All-pairs A/A noise bands"
block of `results/ax-aa-nodedrain/ax-aa-all6-variance.txt`) — every unordered pair
of the six replicate sessions at each f contributes one |Δ|, pooled over conditions.

| outcome | median | p95 | max |
|---|---|---|---|
| trough depth (pods) | 0 | **0** | 0 |
| trough duration real (s) | 0.004 | **15.05** | 15.05 |
| integrated outage (pod·s) | 0.027 | **165.5** | 165.6 |
| user-route error (rate) | 0.003 | **0.081** | 0.091 |
| east-west p95 pre (ms) | 0.76 | **5.33** | 7.03 |

The trough-duration and integrated-outage bands are bimodal — ~0 for almost all
pairs, with a sparse jump to one duration quantum (15 s ⇒ 165 pod·s at f-000's
11-pod depth) — not a smooth noise distribution. The east-west p95 band differs
from `aa_block.py`'s 5.82 ms because the two use different units: this all-pairs
band pools all 75 |Δ| over the six replicates, whereas `aa_block.py` reports the
n=5 paired |A−B| of its single consumed pair (§2 / F2). Both bracket δ_latency = 6 ms
(§6.5), so the TOST/dominance margin holds under either.

---

## TBDs filled (prereg §6)

1. **AX-H1 SESOI** — trough-depth A/A 95 % band = **0** (deterministic). Observed
   pilot-scale reduction here: fractional trough depth 1.00 (f-000) → 0.36 (f-100),
   **reduction 0.64**. **SESOI = 0.25 fractional trough-depth reduction** (the
   prereg planning value): comfortably above the zero noise band and below the 0.64
   realized effect. (Because depth is deterministic, any positive reduction exceeds
   the band; the 0.25 floor is a *substantive-effect* floor, not a noise floor.)
2. **AX-H2 margins** —
   - **User-route error rescue: keep 0.302.** A/A band 0.081 (p95); margin clears
     it 3.7×.
   - **Integrated-outage co-primary:** the between-mode effect AX-H2 tests is
     depth-driven and large; at the landed n the interaction is amply powered
     (§Power). TOST equivalence band per continuous co-primary from the A/A 95 %
     bands: **±0.081 (user error)**, **±166 pod·s (integrated outage)** — note the
     integrated-outage band is inflated by the rare duration quantum-jump and is
     therefore conservative.
3. **AX-H3 variance check — FAILS the runnability conditions.** The dynamic
   availability sub-score has **negligible, non-noise-like within-condition
   variance** at fixed placement: depth between-session sd = 0 (deterministic);
   duration between-session variance ≈ 0 at 3/5 conditions with sparse discrete
   quantum-jumps at 2/5; integrated-outage within-condition variance is 0 at 3/5
   and a single outlier at 2/5. This is precisely the prereg §3 not-runnable
   hatch ("negligible within-condition variance → ICC degenerate toward 1.0" **and**
   "within-f variance structured rather than noise-like"). **AX-H3 is not a valid
   test-retest estimand → report availability descriptively** (via AX-H1 +
   AX-H4), not as a reliability number. See DA1.
4. **Per-cell n** — **n = 8** sessions/cell for AX-H2 (carry the original landed n;
   between-mode effects are large and the user-error within-cell sd ≈ 0.05, so n=8
   is over-powered; the Wilcoxon one-sided attainability floor is n≥7 at the Holm
   α). AX-H1's outcome is deterministic, so its complete-block Page's L is saturated
   at the **n = 6** complete-block sessions already used for calibration; the
   confirmatory AX-H1 run collects fresh complete-block sessions (recommend n = 6,
   matching the A/A count). See DA3.
5. **AX-H4 δs** — from the A/A 95 % bands: **δ_latency = 6 ms** (band 5.33),
   **δ_error = 0.09** (band 0.081), **δ_outage = 166 pod·s** (band 165.5; conservative,
   quantum-jump-driven — flag for review at freeze, DA4). Each is ≥ its band so
   instrument noise cannot manufacture a dominance.
6. **Node-drain taint rules** — **no availability-window pre-chaos transient was
   observed** (0 tainted iterations; pre-chaos availability is full in all 6
   sessions). No new availability-specific pre-window band is needed; carry the
   existing pipeline gates (`app_ready_timeout`, `pre_chaos_errors_high`). The M2
   D3 UDP pre-slope bands remain a *mechanism-context* taint, not the availability
   taint, and are not load-bearing for the AX availability outcomes.
7. **Environment** — **user's call (DA6).** Prereg §6.7 recommends running the AX
   primary on a **second infrastructure** to also discharge the external-validity
   threat; the alternative is the pinned cluster (8×{≤6}-vCPU, K8s v1.28.6, ipvs)
   used here. Record the choice before freeze.

---

## Power analysis → per-cell n (recommendation)

- **AX-H1 (Page's L, descending trend, SESOI = 0.25 fractional depth reduction):**
  the outcome is deterministic given placement, so the per-condition medians are
  identical across sessions and the monotone trend is perfectly consistent — Page's
  L is significant at the minimum attainable n. The realized reduction (0.64) is
  2.6× the SESOI. **Saturated; n = 6 complete-block sessions suffices.** Sensitivity
  (Spearman, Jonckheere–Terpstra) are likewise saturated.
- **AX-H2 (ART-ANOVA r×mode interaction on integrated outage + ≥0.302 user-error
  rescue):** the between-mode effect is depth-driven and large relative to the
  within-cell noise (user-error within-cell sd ≈ 0.05 vs a 0.302 margin; integrated
  outage between-mode contrast ≫ the 29 pod·s within-condition sd). **n = 8/cell**
  is over-powered for both co-primaries and clears the Wilcoxon one-sided
  attainability floor (n≥7 at the Holm α). TOST packing-control band per §6.2.
- **AX-H3:** **no power analysis** — the hypothesis is not runnable (DA1); an ICC
  power calc requires an assumable true reliability that the determinism here
  precludes.
- **AX-H4 (descriptive):** no p-value; δs in §6.5.

**Recommendation: AX-H2 n = 8/cell; AX-H1 n = 6 complete-block sessions.**
(~2.5 h/session on this cluster.)

---

## Findings log (registered rule: significant A/A finding → investigate → fix → rerun)

- **F1 — availability outcomes are (near-)deterministic at fixed placement; AX-H3
  is not runnable.** Root cause (structural, not a defect): under node-drain the
  blast radius (which pods sit on the drained node) is fixed by the placement, and
  the outage duration is pinned to the fixed chaos window with deterministic
  recovery. The dynamic-sub-score fix the prereg proposed assumed duration/recovery
  would supply run-to-run noise; the A/A shows duration is essentially quantized and
  fixed (sparse 15 s jumps at 2/5 conditions, 0 elsewhere). The resulting ICC (0.95
  for integrated outage) is an artifact of the deterministic between-condition
  signal plus two quantization outliers, not evidence of a reliable measurement of
  a run-to-run-varying quantity. **Disposition (proposed): drop AX-H3 from the
  confirmatory family; report availability descriptively via AX-H1 (dose-response)
  + AX-H4 (frontier). Holm family shrinks 3→2 (AX-H1, AX-H2).** Needs sign-off (DA1).
- **F2 — `aa_block.py` pairing consumes only 1 of 3 pairs for an all-identical-
  placement block.** Its `PairKey` groups by `solverSeed`+cell and chunks within a
  group into pairs; with all 6 sessions on one solver seed it forms one pair and
  reports the other 4 sessions as "extra … ignored". Not a data defect, but the
  band/variance numbers must come from the all-6-session unit (which they do here,
  via `ax_aa_all6_variance.py`). Candidate code follow-up (not blocking): teach
  `aa_block.py` to treat an all-identical-placement group as N replicates rather
  than one pair. Recorded; the null tests it does run are valid (n.s.).
- **F3 — no A/A false-positive hit.** Unlike the M2 block (which had marginal
  iteration-level offsets), every AX outcome is n.s. at every pairing level. The
  block is clean.

---

## Freeze decisions (user) — DA1–DA7

**DA1 and DA6 settled by the user 2026-06-25; DA2–DA5 and DA7 adopt the
calibration-derived recommendations below (the user may adjust any before the
freeze commit).**

1. **DA1 (headline) — SETTLED: drop AX-H3.** Per F1, AX-H3 is removed from the
   confirmatory family; availability is reported descriptively via AX-H1
   (dose-response) + AX-H4 (frontier). The determinism is structural, not an
   instrumentation gap, so no alternative noise source is pursued.
2. **DA2 — adopted: AX-H1 SESOI = 0.25** fractional trough-depth reduction
   (band = 0; realized effect 0.64).
3. **DA3 — adopted: per-cell n = AX-H2 8/cell, AX-H1 6 complete-block sessions.**
4. **DA4 — adopted: AX-H4 δs = δ_latency 6 ms, δ_error 0.09, δ_outage 166 pod·s.**
   (δ_outage is conservative/quantum-jump-driven; a duration-robust alternative —
   depth-only dominance with duration reported separately — is flagged for a look
   at the freeze commit.)
5. **DA5 — adopted: no availability pre-window taint band; carry the pipeline
   gates** (`app_ready_timeout`, `pre_chaos_errors_high`) — no transient observed.
6. **DA6 — SETTLED: pinned cluster** (8×{≤6}-vCPU libvirt, K8s v1.28.6, ipvs). The
   external-validity threat is **not** discharged by this study (user's accepted
   trade-off); it remains the named highest-value follow-up.
7. **DA7 — adopted: Holm family size = 2** (AX-H1, AX-H2) at α=0.05. AX-H2 Holm
   input = max(p_integrated-outage, p_user-error); AX-H1 input = the Page's L p.

**Remaining (user-owned, per prereg §8):** freeze commit + annotated tag
`ax-prereg-freeze`, flip the prereg banner to FROZEN, then DOI-deposit the frozen
state + this block's raw data (`isSupplementTo` 10.5281/zenodo.20690836). The
deposit is token-gated.

---

## AX A/A exit criteria status

- [x] ≥3 identical-placement node-drain session pairs through the full pipeline (6 sessions / 3 pairs).
- [x] All sessions `doctor --strict` clean (0 errors), `git.dirty=False`, 0 tainted iterations.
- [x] Variance components + 95 % noise bands estimated for every AX outcome.
- [x] No significant A/A finding (no investigate→fix→rerun trigger).
- [x] Every prereg §6 TBD filled or escalated to a DA decision.
- [x] DA1 (drop AX-H3) + DA6 (pinned cluster) settled by the user; DA2–DA5, DA7 adopt the calibration recommendations.
- [ ] **Freeze commit + tag `ax-prereg-freeze` + DOI deposit (user-owned).**
