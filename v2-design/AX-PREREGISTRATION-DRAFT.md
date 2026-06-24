# ChaosProbe — Availability-Axis confirmatory study — pre-registration (**DRAFT, NOT FROZEN**)

> **Status: DRAFT — not yet binding.** This document specifies a *new,
> separate* confirmatory study whose purpose is to make the availability-axis
> findings — established only *exploratorily* in the thesis design-corrected
> re-analysis — **confirmatory**. It becomes binding only after (1) the
> open TBDs below are filled from a dedicated node-drain A/A calibration block,
> (2) a freeze commit + git tag, and (3) a DOI deposit of the frozen state,
> exactly as the original pre-registration was frozen
> ([`01-PREREGISTRATION.md`](01-PREREGISTRATION.md), DOI
> [10.5281/zenodo.20690836](https://doi.org/10.5281/zenodo.20690836)).
>
> **This document does NOT change anything in the thesis.** The frozen
> confirmatory family (H1–H5) and its Holm verdict stand untouched; the
> design-corrected re-analysis remains exploratory there. This study is the
> *honest, and only, route* to confirmatory status for the availability-axis
> effects: pre-register the corrected design **before** collecting the data that
> tests it. **No data testing the hypotheses below has been collected** (the
> C4 design-fix campaign is the *motivating pilot*, not confirmatory evidence —
> exactly as the v1 runs were the pilot for the v2 pre-registration).

Design context, knobs, instrumentation, and the layered scorecard are unchanged
from [`00-DESIGN.md`](00-DESIGN.md). The motivating pilot is the C4 node-drain
dose-response (DOI [10.5281/zenodo.20818800](https://doi.org/10.5281/zenodo.20818800))
and the pre-declared correction criteria in
[`DESIGN-FIX-SCOPE.md`](DESIGN-FIX-SCOPE.md); pilot numeric literals are quoted
only as motivation and are not registered evidence.

---

## 1. Why this study exists — the three construction limits it makes confirmatory

The thesis identified that three availability-side results were
**construction-limited** because `pod-delete` at r=1 cannot move availability
(the only replica is removed, so the trough is ≈1 pod for every placement):

| Thesis result | Construction limit | This study's fix |
|---|---|---|
| **H3** trough-depth co-primary | absolute 1-pod margin equals the realized r=1 depth → un-passable | **AX-H2**: node-drain (placement-dependent blast radius) + a **range-relative** margin and a continuous **integrated-outage** primary, frozen *before* data |
| **H4** placement frontier | availability face degenerate under `pod-delete` | **AX-H1** (availability dose-response, now falsifiable) + **AX-H4** (non-degenerate frontier) |
| **H5** availability sub-score ICC | 0.180 = *absence of signal* under `pod-delete`; and 1.0 *by construction* under deterministic node-drain placement | **AX-H3**: node-drain (supplies the outage) + a **dynamic** (duration-inclusive) sub-score read at **fixed placements** across replicate sessions, so within-condition variance is real run-to-run noise and the ICC is a valid test-retest estimate |

The design-fix demonstrated each effect is real *exploratorily*. This study
registers them as falsifiable confirmatory predictions **before** collecting the
fresh data that tests them.

---

## 2. Confirmatory family and multiplicity

- **Confirmatory family:** the single primary test of each of **AX-H1, AX-H2,
  AX-H3**, **Holm-corrected** across this three-member family at α = 0.05.
- **AX-H2's two continuous co-primaries** (integrated outage; user-route error
  rescue) are combined **both-must-pass (conjunction)**; AX-H2's single input to
  the outer Holm family is the max of the two p-values. A conjunction is
  conservative; no internal α split. (The range-relative fractional-depth
  reduction is a reported descriptive secondary, *not* a conjunction member —
  §3, AX-H2.)
- **AX-H4 is descriptive** — a registered figure + reporting protocol with
  margins, not a family member; no p-value registered (the same rationale as the
  thesis's H4: non-dominance cardinality is near self-confirming under bootstrap
  CIs).
- Sensitivity analyses (e.g. AX-H1 Spearman, the fractional-reduction form of the
  depth face) are non-confirmatory and labeled as such wherever reported.

---

## 3. Hypotheses

### AX-H1 — Availability dose-response under node-drain

**Statement.** During-drain EndpointSlice **trough depth** (fraction of
app-ready endpoints lost) **decreases monotonically** in the achieved cross-node
fraction f across the designed levels {0, 0.25, 0.5, 0.75, 1.0} at r=1 — spreading
shrinks the node-drain blast radius.

**Motivating pilot.** C4 design-fix (trough 1.00 packed → 0.36 spread); not
registered evidence.

**Design.** Every session is a **complete block** visiting all 5 f-levels in
randomized (recorded-seed) order — the design Page's L requires. Fault =
`node-drain` on the target's node; r=1.

**Test (primary, in family).** Page's L trend test across the ordered f-levels,
**registered for the monotone-decreasing alternative** (f ordered 0→1, predicted
trough-depth ranks descending — note this is the opposite direction to the
original V2-H1's ascending latency trend, so the alternative is set descending,
not copied), session-condition trough medians as units (one value per level per
session). Spearman over designed levels = non-confirmatory sensitivity check.
Fallback (if the solver gate forces nearest-achievable f): linear mixed-effects
model on continuous achieved-f (session random effect, test on the negative
slope) + Jonckheere–Terpstra for a descending trend.

**SESOI.** **[TBD — filled from the node-drain A/A block, §6.]** A monotone trend
with a total trough-depth reduction from f=0 to f=1 of **≥ [TBD], set no smaller
than the availability-face A/A 95% noise band** and below the pilot's ~0.64
absolute reduction, so a pilot-magnitude effect clears it with margin while
instrument noise cannot. A statistically detectable but sub-SESOI trend is
reported as below the bar, not as support. Planning value pending A/A: ≥ 0.25
absolute trough-depth reduction.

**Falsified by.** Non-monotone medians beyond noise (primary n.s. at the
Holm-adjusted α), or a monotone reduction smaller than the SESOI.

### AX-H2 — Replication rescue with a range-relative margin (fresh confirmatory run)

**Statement.** Under node-drain, anti-affine r=3 reduces user-visible
availability loss relative to r=1 by a pre-set margin, while packed r=3 ≈ r=1 —
an interaction: replication rescues availability only when replicas do not share
the failure domain. **This is a fresh run against a frozen margin, not a
re-analysis of the thesis's C2 data** (re-using C2 with a margin chosen after
seeing it would be exploratory).

**Design.** Between-subjects cells r1-packed, r3-packed, r3-anti-affine, each ×
**[TBD n, §6]**, `node-drain` at the registered f, on the capacity-feasible
round-robin packing instrument (per-service replica packing, services round-robin
across nodes — the M1b semantics).

**Confirmatory conjunction (both-must-pass).** Two continuous co-primaries only —
the knife-edge fractional-depth criterion is deliberately kept *out* of the gate
(below):
1. **Integrated outage = trough depth × outage duration (PRIMARY depth face).**
   Continuous, so it avoids the integer-pod quantization that made the thesis's
   fractional 1-pod margin knife-edge. Registered effect: significant ART-ANOVA
   r×mode interaction with the anti-affine-rescue direction.
2. **User-route error rescue.** Anti-affine r=3 reduces the during-drain
   user-route error rate by ≥ **0.302** (the registered margin, unchanged), with
   a significant interaction.

**Registered secondary (reported, NOT a gate).** The **range-relative
trough-depth reduction** — anti-affine trough depth ≤ 50% of the realized r=1
depth (the bar carried verbatim from [`DESIGN-FIX-SCOPE.md`](DESIGN-FIX-SCOPE.md)
FIX-H3) — is reported descriptively but is **not** part of the conjunction,
because the thesis showed it is bimodal/knife-edge at small blast radius (a fresh
run on the same ~11-pod app reproduces that quantization). Keeping it as a gate
would re-import exactly the fragility this redesign removes. It is reported with
its bootstrap CI so a reader sees whether the discrete depth face also moved.

**Packing control (TOST).** Packed r=3 must fall within the A/A-derived
equivalence band of r=1 on each continuous co-primary; falling outside flags the
instrument (not a finding), as in the original V2-H3.

**Test (primary, in family).** ART-ANOVA interaction on the integrated-outage
outcome; the conjunction additionally requires the ≥0.302 user-error rescue (with
its own significant interaction) and both TOST packing controls. AX-H2's Holm
input = max of the two co-primary p-values.

**Falsified by.** No interaction on the integrated-outage primary, or the
user-error rescue failing its margin/interaction, with the packing control passing
(instrument valid).

### AX-H3 — Availability sub-score reliability (dynamic outage, fixed-placement replicates)

**Statement.** On fresh node-drain sessions, the layered-scorecard
**availability** sub-score's condition-level test-retest **ICC ≥ 0.5**, and exceeds
the naive single-aggregate baseline computed on the same sessions. Using
`node-drain` rather than `pod-delete` is also what supplies the sustained outage
whose absence drove the thesis's 0.180.

**The construction fix (binding) — what a "condition" is, and where the noise
comes from.** The thesis ICC = 1.0 problem is that a *static* trough-depth count is
deterministic given the placement, so within-condition between-session variance is
zero. The fix has two binding parts, and is deliberately the **opposite** of
cross-seed sampling:

1. **Conditions are fixed placements.** Each condition is one f-level realized by a
   single **fixed, recorded solver seed** — one canonical placement per f-level,
   shared across all replicate sessions; only the order seed varies. The
   between-condition variance is then the true blast-radius difference across
   placements, and the within-condition variance is genuine **run-to-run
   measurement noise**. (Drawing a *different* placement per session — the
   cross-seed idea — would inject a structured placement factor into the
   within-condition term: distinct valid placements at one f can have materially
   different blast radii because *which* services co-locate with the drain target
   is structural, not noise. That is real outcome variance, not test-retest noise,
   so it would not yield a valid reliability estimand. Fixed placement is required.)
2. **The availability sub-score is the DYNAMIC outage** — integrated outage (trough
   depth × duration) and recovery-inclusive terms, not the static endpoint-loss
   count. The blast radius is fixed given the placement, but the outage *duration*
   and recovery dynamics vary run-to-run, so the dynamic sub-score has real
   within-condition noise at fixed placement; the static count does not.

**Registered variance partition (binding).** Facet **condition** = f-level /
placement (between); facet **session** = replicate at the fixed placement (within /
test-retest). ICC = between-condition / (between + within). The same fixed-seed
sessions serve AX-H1 (the per-f placement is held constant there too, which only
sharpens the dose-response).

**Registered threat — design-informed-by-pilot circularity.** The sub-score
layers were chosen because earlier work showed signal there; mitigations
(binding): sub-score definitions frozen at this study's freeze commit before any
of its data exists; reliability evaluated **only** on this study's sessions
(never on C1–C4); the absolute ICC ≥ 0.5 bar applies in addition to the
head-to-head comparison.

**Test (primary, in family).** Condition-level ICC of the dynamic availability
sub-score across the fixed-placement replicate sessions; point estimate ≥ 0.5
**and** bootstrap CI on ICC_availability − ICC_aggregate excludes zero. Mechanism
and user-tail sub-scores reported alongside (user-tail exploratory, as before).

**Not-runnable hatch (binding).** If the A/A block shows the dynamic availability
sub-score has **negligible within-condition variance** (still ~deterministic →
ICC degenerate toward 1.0) **or negligible between-condition variance**
(placements do not differ → ICC undefined / 0) **or that its within-f variance is
structured by placement rather than noise-like**, the test-retest ICC is not a
meaningful estimand: record that finding and report the availability signal
**descriptively** instead of forcing a reliability number.

**Falsified by.** Availability ICC < 0.5 (with non-degenerate, noise-like variance
per the hatch), or its reliability CI overlapping/below the aggregate's.

### AX-H4 — The corrected placement frontier (DESCRIPTIVE)

**Status.** Descriptive, not in the family (no p-value), retained for continuity
with H4.

**Reporting protocol (binding).** For each placement, report the **latency face**
(pre-chaos east-west p95) vs the **availability face** (node-drain integrated
outage + user-route error) with cluster-bootstrap CIs. Dominance declared only
with margins (δ_latency, δ_outage, δ_error — **[TBD from A/A, §6]**, no smaller
than the A/A 95% band). The pre-declared question: is the availability face
**non-degenerate** (varies materially across placements — the limit being
corrected), and does the frontier show a trade-off or a dominance ordering?
Either is a real result; degeneracy is the failure mode under correction.

---

## 4. Relationship to the frozen thesis study

- The thesis confirmatory family (H1, H2, H3, H5) and its Holm verdict are
  **not** re-opened, re-scored, or re-labeled by this study.
- AX-H1…AX-H4 are a **new** family with new data; their verdicts (when run) are
  reported as their own confirmatory study, citing the thesis design-fix as the
  motivating pilot.
- If this study is run and supports an effect, the correct claim becomes "the
  availability-axis effect, exploratory in the thesis, is **confirmed** in a
  pre-registered follow-up" — never a retroactive edit of the thesis verdicts.

---

## 5. A/A calibration protocol (node-drain)

A **new** A/A block is required because the existing A/A bands were measured on
`pod-delete` latency, not node-drain availability. Before any between-condition
comparison: **≥3 identical-placement node-drain session pairs** are run through the
full pipeline as if A/B. Within a pair the **placement is held fixed** (one
recorded solver seed, identical assignment) and only the **order seed varies** —
matching AX-H3's fixed-placement design (§3, §7) — so the pair measures the
availability-face **run-to-run** variance *at fixed placement* that AX-H3's
test-retest ICC needs. (A cross-seed pair would instead fold in structured
placement variance, which AX-H3 deliberately excludes, so it could not fill the
AX-H3 TBD it feeds.)

**Functions (registered honestly, as in the original §A/A):**
1. **Variance-component estimation** — within/between-session variance of the
   trough depth, integrated outage, and user-route error feed the power analysis
   (per-cell n) and every SESOI/margin/δ below. **This is what fills the §6 TBDs.**
2. **Pipeline sanity check** on null data.

The block is **not** a false-positive-rate estimator and registers no numeric
"FPR ≤ α" gate (the same Clopper–Pearson arithmetic as the original applies). A
significant **registered-unit** A/A finding triggers investigate→fix→rerun;
a second after a fix halts the campaign for instrument redesign (§7).

---

## 6. Open TBDs to fill before freeze (the complete list)

These are filled from §5's node-drain A/A block + a power analysis, then the
document is frozen. **Until every item is filled and the freeze commit + DOI are
made, this pre-registration is non-binding.**

1. **AX-H1 SESOI** — minimum trough-depth reduction f=0→f=1 (≥ A/A 95% band).
2. **AX-H2 margins** — the integrated-outage MDE check at the chosen n (the two
   *continuous* co-primaries are what n must power; the demoted fractional-depth
   secondary is descriptive, so n need not power it); the user-error margin (carry
   0.302 unless the node-drain A/A band differs); the TOST equivalence band per
   continuous co-primary.
3. **AX-H3** — from the A/A block, confirm the **dynamic** availability sub-score
   at **fixed placement** has (i) non-negligible within-condition (run-to-run)
   variance and (ii) non-negligible, noise-like between-condition variance. If
   within-condition variance is ~0 (still deterministic), between-condition
   variance is ~0 (no contrast), or the within-f variance is structured by
   placement, the ICC is not a valid test-retest estimand and AX-H3 is **not
   runnable** — record that finding and report availability descriptively rather
   than forcing it (the §3 not-runnable hatch). Fix the dynamic-sub-score
   aggregation formula at freeze, blind to comparison data.
4. **Per-cell n** — from the power analysis against the A/A variance and the
   SESOIs/margins above (the original landed n=8; re-derive for node-drain).
5. **AX-H4 δs** — δ_latency, δ_outage, δ_error from the A/A 95% bands.
6. **Node-drain taint rules** — the analogue of the per-f-level pre-window
   UDP-slope bands (D3), recomputed for the node-drain availability window if
   any pre-window transient is observed in the A/A block.
7. **Environment** — pinned cluster (8×2-vCPU/4-GiB, K8s v1.28.6, ipvs) for
   comparability **vs.** a second infrastructure (managed cluster / different
   kube-proxy mode / bare metal), which the thesis names as the single
   highest-value remaining follow-up. **Recommended: run the primary on a second
   infrastructure** so this study also discharges the external-validity threat;
   record the choice before freeze.

---

## 7. Session design, workloads, stopping rules (carried from the original)

Unchanged in discipline from [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md)
§Session design / §Workloads / §Stopping rules, with the fault set to
`node-drain` and these deltas:

- **Unit of analysis: the session** (one cluster lifetime, one commit).
- **Fixed-placement replicates** for AX-H3 (one canonical recorded solver seed per
  f-level, shared across replicate sessions; only order seed varies) — the only
  structural change from the original session design, and the basis of AX-H3's
  test-retest variance partition (§3). The same fixed-seed sessions serve AX-H1.
- **Complete-block sessions** for AX-H1 (all 5 f-levels, randomized recorded
  order; the per-f placement held to the fixed seed above).
- **Gating:** every session passes `doctor --strict`; achieved-f misses > 0.05
  rejected; the registered taint rules apply (node-drain availability-window
  variant per §6.6).
- **Deposit-before-analysis:** each campaign archived + DOI-deposited before its
  results are analyzed for writing — continuing the concept record
  10.5281/zenodo.20639145 and `isSupplementTo` this frozen pre-registration.
- **Workloads:** online-boutique (primary); hotelReservation as the pre-declared
  second workload if time permits (droppable first, per the original de-scope
  order).
- **Stopping rules:** the solver-gate (M1b), A/A-gate, capacity-null, and
  second-environment-droppable rules carry over verbatim.

---

## 8. Freeze + deposit procedure (to make this binding)

1. Run the §5 node-drain A/A block; fill every §6 TBD from it + the power
   analysis (record decisions in an `AX-AA-REPORT.md`, mirroring `M2-AA-REPORT.md`).
2. Resolve the §6.7 environment choice.
3. Freeze: commit + annotated git tag (e.g. `ax-prereg-freeze`); flip this
   document's status banner to FROZEN with the date and tag.
4. Deposit the frozen state + raw A/A data + gate artifacts under a DOI
   (`isSupplementTo` 10.5281/zenodo.20690836), as `FREEZE-DEPOSIT.md` did.
5. Only then collect AX confirmatory data.

## 9. Deviations policy

After the freeze commit, every deviation (changed n, dropped/added cell, modified
test, revised threshold) is logged in `DEVIATIONS.md` with date, reason, and
whether it was decided blind to outcome data — identical to the original.
