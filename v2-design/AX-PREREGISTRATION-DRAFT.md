# ChaosProbe — Availability-Axis confirmatory study — pre-registration (**FROZEN 2026-06-25 — tag `ax-prereg-freeze`**)

> **Status: FROZEN (2026-06-25), tag `ax-prereg-freeze`.** This document specifies
> a *new, separate* confirmatory study whose purpose is to make the
> availability-axis findings — established only *exploratorily* in the thesis
> design-corrected re-analysis — **confirmatory**. Its content is now **locked**:
> every change after this freeze commit is logged in [`DEVIATIONS.md`](DEVIATIONS.md)
> with date, reason, and whether it was decided blind to outcome data (§9). The
> three binding prerequisites: (1) the §6 TBDs were filled from a dedicated
> node-drain A/A calibration block — **done**; (2) this freeze commit + git tag —
> **done**; (3) a DOI deposit of the frozen state + raw A/A data — **the one
> remaining step before confirmatory data collection**, anchoring this tag
> externally exactly as the original pre-registration was frozen
> ([`01-PREREGISTRATION.md`](01-PREREGISTRATION.md), DOI
> [10.5281/zenodo.20690836](https://doi.org/10.5281/zenodo.20690836)).
> *(The filename retains the `-DRAFT` suffix only to preserve the cross-references
> that already point at it — thesis chapters, `AX-AA-REPORT.md`; this banner is
> authoritative on the frozen status.)*
>
> **Calibration outcome, frozen here.** The node-drain A/A calibration block ran
> (6 fixed-placement sessions, `results/ax-aa-nodedrain/`); every §6 TBD is filled
> and the environment (§6.7) is chosen — see [`AX-AA-REPORT.md`](AX-AA-REPORT.md).
> **The calibration retired AX-H3** (the availability outcomes are deterministic at
> fixed placement, so its test-retest ICC is not a valid estimand — the §3
> not-runnable hatch fired); the confirmatory family is **two members (AX-H1,
> AX-H2)**, availability reported descriptively via AX-H1 + AX-H4. **No data testing
> the (surviving) hypotheses has been collected** — that begins only after the DOI
> deposit.
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
| **H5** availability sub-score ICC | 0.180 = *absence of signal* under `pod-delete`; and 1.0 *by construction* under deterministic node-drain placement | **AX-H3** *(RETIRED at calibration)*: the A/A block showed the availability outcomes are deterministic at fixed placement even for the **dynamic** (duration-inclusive) sub-score — the within-condition variance the ICC needs is absent (the §3 not-runnable hatch). Availability is reported **descriptively** via AX-H1 + AX-H4 instead. See [`AX-AA-REPORT.md`](AX-AA-REPORT.md). |

The design-fix demonstrated each effect is real *exploratorily*. This study
registers the **surviving** effects as falsifiable confirmatory predictions
**before** collecting the fresh data that tests them; the calibration retired AX-H3
(the third row) when its precondition — noise-like within-condition variance — was
shown absent.

---

## 2. Confirmatory family and multiplicity

- **Confirmatory family:** the single primary test of each of **AX-H1 and AX-H2**,
  **Holm-corrected** across this **two-member** family at α = 0.05 (m = 2). *(AX-H3
  was a third member in the draft; the A/A calibration retired it — §3 AX-H3, §6.3.)*
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

**SESOI (filled from the A/A block — [`AX-AA-REPORT.md`](AX-AA-REPORT.md) §6.1).**
A monotone trend with a total fractional trough-depth reduction from f=0 to f=1 of
**≥ 0.25**. The availability-face A/A 95% noise band for trough depth is **0** (the
depth is deterministic at fixed placement), so the SESOI is a *substantive-effect*
floor, not a noise floor: 0.25 sits well below the pilot's realized ~0.64 fractional
reduction (calibration reproduced 1.00→0.36) so a pilot-magnitude effect clears it
with margin. A statistically detectable but sub-SESOI trend is reported as below the
bar, not as support.

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
**n = 8 sessions** (§6.4), `node-drain` at the registered f, on the capacity-feasible
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
instrument (not a finding), as in the original V2-H3. **A/A 95% equivalence bands
(filled, [`AX-AA-REPORT.md`](AX-AA-REPORT.md) §6.2): ±166 pod·s (integrated
outage), ±0.081 (user-route error).** The 0.302 user-error rescue margin clears its
band 3.7×; the integrated-outage band is conservative (inflated by a rare
duration quantum-jump at f-000).

**Test (primary, in family).** ART-ANOVA interaction on the integrated-outage
outcome; the conjunction additionally requires the ≥0.302 user-error rescue (with
its own significant interaction) and both TOST packing controls. AX-H2's Holm
input = max of the two co-primary p-values.

**Falsified by.** No interaction on the integrated-outage primary, or the
user-error rescue failing its margin/interaction, with the packing control passing
(instrument valid).

### AX-H3 — Availability sub-score reliability (dynamic outage, fixed-placement replicates) — **RETIRED at calibration**

> **RETIRED (2026-06-25), not a confirmatory family member.** The node-drain A/A
> calibration block ([`AX-AA-REPORT.md`](AX-AA-REPORT.md) §6.3, F1) showed the
> availability outcomes are **deterministic at fixed placement** — trough depth
> between-session sd = 0 (ICC = 1.0), and the **dynamic** outage's within-condition
> variance is zero at 3/5 conditions with only rare discrete duration quantum-jumps
> at 2/5 (not noise-like). The §"Not-runnable hatch" below therefore fired: the
> test-retest ICC is not a valid estimand, so AX-H3 is dropped and availability is
> reported descriptively via AX-H1 (dose-response) + AX-H4 (frontier). The original
> specification is retained below for the record. *Surviving-family Holm m = 2.*

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
with margins (**δ_latency = 6 ms, δ_outage = 166 pod·s, δ_error = 0.09** — filled
from the A/A 95% bands, [`AX-AA-REPORT.md`](AX-AA-REPORT.md) §6.5; each ≥ its band
so noise cannot manufacture a dominance). The pre-declared question: is the
availability face
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

## 6. TBDs — **ALL FILLED from the node-drain A/A block (2026-06-25)**

Filled from §5's node-drain A/A block ([`AX-AA-REPORT.md`](AX-AA-REPORT.md)); the
remaining steps to bind the document are the freeze commit + tag and the DOI
deposit (§8). All-pairs A/A 95% noise bands referenced below: trough depth **0**,
user-route error **0.081**, east-west p95 **5.33 ms**, integrated outage **165.5
pod·s** (integrated outage — and trough duration, not listed here — are
quantum-jump-dominated, hence bimodal/not-noise-like; user error and east-west p95
are smooth run-to-run noise).

1. **AX-H1 SESOI — FILLED: ≥ 0.25 fractional trough-depth reduction** (band = 0
   → a substantive-effect floor; realized pilot/calibration effect ~0.64). §3 AX-H1.
2. **AX-H2 margins — FILLED:** user-error rescue margin **0.302** carried (A/A band
   0.081, clears 3.7×); TOST equivalence bands **±166 pod·s** (integrated outage) /
   **±0.081** (user error); the integrated-outage between-mode interaction is
   over-powered at the chosen n (large depth-driven effect). §3 AX-H2.
3. **AX-H3 — RESOLVED: NOT RUNNABLE → RETIRED.** The A/A block showed the dynamic
   availability sub-score has **negligible, non-noise-like within-condition
   variance** at fixed placement (depth between-session sd = 0, ICC = 1.0; duration
   pinned to the chaos window with rare discrete quantum-jumps only). The §3
   not-runnable hatch fired: the test-retest ICC is not a valid estimand. AX-H3 is
   dropped from the confirmatory family (m = 2); availability is reported
   descriptively via AX-H1 + AX-H4. §3 AX-H3 (RETIRED), [`AX-AA-REPORT.md`](AX-AA-REPORT.md) F1.
4. **Per-cell n — FILLED:** **AX-H2 = 8 sessions/cell** (over-powered for both
   co-primaries; Wilcoxon one-sided floor n≥7 at the Holm α); **AX-H1 = 6
   complete-block sessions** (deterministic outcome → Page's L saturated). §Power.
5. **AX-H4 δs — FILLED:** δ_latency **6 ms**, δ_error **0.09**, δ_outage **166
   pod·s** (each ≥ its A/A band). §3 AX-H4.
6. **Node-drain taint rules — FILLED: none added.** No availability-window
   pre-chaos transient was observed (0 tainted iterations across the block); the
   existing pipeline gates (`app_ready_timeout`, `pre_chaos_errors_high`) are
   carried. The M2 D3 UDP pre-slope bands remain mechanism-context only, not the
   availability taint.
7. **Environment — RESOLVED: the pinned cluster** (8×{≤6}-vCPU, K8s v1.28.6, ipvs)
   used for the A/A block, for comparability. The external-validity threat (a second
   infrastructure) is **not** discharged by this study — it remains the named
   highest-value follow-up (user decision, 2026-06-25).

---

## 7. Session design, workloads, stopping rules (carried from the original)

Unchanged in discipline from [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md)
§Session design / §Workloads / §Stopping rules, with the fault set to
`node-drain` and these deltas:

- **Unit of analysis: the session** (one cluster lifetime, one commit).
- **Fixed-placement replicates** (one canonical recorded solver seed per f-level,
  shared across replicate sessions; only order seed varies) — the only structural
  change from the original session design. This was the basis of AX-H3's test-retest
  variance partition; **with AX-H3 retired (§3, §6.3) the fixed-placement design now
  serves AX-H1**, holding the per-f placement constant so the dose-response carries
  no placement-draw confound.
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

1. ✅ **DONE (2026-06-25).** Ran the §5 node-drain A/A block; filled every §6 TBD
   from it + the power analysis ([`AX-AA-REPORT.md`](AX-AA-REPORT.md)).
2. ✅ **DONE (2026-06-25).** Resolved the §6.7 environment choice (pinned cluster).
3. ✅ **DONE (2026-06-25).** Freeze: this commit + annotated git tag
   `ax-prereg-freeze`; status banner flipped to FROZEN with the date and tag.
4. **DOI deposit (next, user-owned — token-gated):** deposit the frozen state + raw
   A/A data + gate artifacts under a DOI (`isSupplementTo` 10.5281/zenodo.20690836),
   as `FREEZE-DEPOSIT.md` did.
5. Only then collect AX confirmatory data.

## 9. Deviations policy

After the freeze commit, every deviation (changed n, dropped/added cell, modified
test, revised threshold) is logged in `DEVIATIONS.md` with date, reason, and
whether it was decided blind to outcome data — identical to the original.
