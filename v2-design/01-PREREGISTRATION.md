# ChaosProbe v2 — pre-registration (DRAFT, not yet frozen)

> **Status: DRAFT.** This document becomes binding only when frozen by commit
> hash at the end of Month 2 (after A/A calibration and power analysis — see
> [`02-WORKPLAN.md`](02-WORKPLAN.md)). Until that freeze, hypotheses, SESOIs,
> and n's may be revised; **after** it, any deviation is reported as a
> deviation. No campaign data informing these hypotheses may be collected
> before the freeze. The build has not started; placeholders marked **TBD**
> or **finalized at M2** are filled from M2's A/A variance estimates before
> freezing.

Design context, knobs, and instrumentation: [`00-DESIGN.md`](00-DESIGN.md).
All v1 references (H1–H6) are to archived, `doctor --strict`-clean runs
documented in `chaosprobe/docs/explanation/hypotheses.md`; **v1 numeric
literals are quoted only in DESIGN §10** (the canonical mapping table) and
cited from here by reference.

## Confirmatory family and multiplicity

- **Confirmatory family:** the **single primary test of each of V2-H1,
  V2-H2, V2-H3, and V2-H5**, **Holm-corrected** across this four-member
  family.
- **V2-H3's two co-primary outcomes** (trough depth × duration; user-route
  error rate) are combined as **both-must-pass (conjunction)**: V2-H3 is
  supported only if *both* outcomes show the registered interaction. A
  conjunction is conservative (it can only lower the per-hypothesis type-I
  rate), so no additional alpha adjustment is applied for the two outcomes.
- **V2-H4 is descriptive** — a registered figure and reporting protocol, not
  a member of the family; no p-value is registered.
- **V2-H6 is an exploratory secondary** — labeled as such, uncorrected,
  outside the family; its outcome is reported but cannot support a
  confirmatory claim.
- Sensitivity analyses (e.g. V2-H1's Spearman check) are non-confirmatory
  and labeled as such wherever reported.

## Hypotheses

### V2-H1 — Dose-response of the east-west tail in cross-node fraction

**Statement.** Median east-west (inter-service) p95 latency increases
monotonically in the achieved cross-node fraction f across the designed
levels {0, 0.25, 0.5, 0.75, 1.0}.

**Motivating pilot.** v1-H5 (two-regime separator, interior never sampled);
see DESIGN §10 / `hypotheses.md`.

**Design (required for the test's validity).** Every C1 session is a
**complete block**: it visits **all 5 f-levels in randomized order**
(recorded seed). Sessions are therefore replicated complete ordered blocks,
which is exactly the design Page's L requires.

**Test (primary, in family).** Page's trend test across the ordered f levels
(session medians as units, one observation per level per session). Spearman
over designed levels is the sensitivity check (non-confirmatory). n sessions
per level: **TBD from M2 power analysis** (≥2 minimum, §Session design).

**Fallback path (pre-registered now).** If the M1 solver gate forces the
nearest-achievable-fraction design (DESIGN §9), the regressor becomes
continuous achieved-f and Page's test no longer applies. The replacement is
fixed here: **primary = linear mixed-effects model** with achieved-f as a
continuous fixed effect and session as a random effect (test on the
achieved-f slope); **secondary (nonparametric) = Jonckheere–Terpstra** over
the ordered achievable levels. The switch, if taken, is recorded before
freeze.

**SESOI.** A monotone trend with a total effect of **≥15 % increase in
east-west p95 from f = 0 to f = 1**. Derivation: the bar is set **between
the A/A noise band** (placeholder; finalized at M2 — the SESOI must exceed
it) **and v1's measured ~25 % two-regime separation** (DESIGN §10), so that
an effect at the v1 magnitude clears it with margin while anything inside
instrument noise cannot. A statistically detectable but <15 % trend is
reported as below the SESOI, not as support.

**Falsified by.** Non-monotone medians beyond noise (primary test n.s. at
the Holm-adjusted α), or a monotone trend smaller than the SESOI. If the
two-regime step shape recurs (flat interior, jump at the extremes), that is
a *distinct* registered outcome: "threshold, not dose-response" — it would
confirm v1-H5's separator reading and falsify the continuous-dose reading.

### V2-H2 — Placement-dependence replicates, and the DNS intervention explains it

**Statement (two-part conjunction — both parts confirmatory).** V2-H2
deliberately tests **both halves of the v1-H2 claim**: that the
during-churn UDP-conntrack drop is *placement-dependent*, and that the
dependence runs through DNS. The round-1 redefinition that moved the
between-placement contrast out of the family had silently changed H2's
meaning from placement-dependence to DNS-dependence; this version restores
it explicitly:

- **(a) Placement-dependence (between-placement, cache-off arms):**
  spread's (f = 1, r = 1) during-churn UDP-entry drop **exceeds** packed's
  (f = 0, r = 1), paired per session. This is directional — a paired sign /
  Wilcoxon comparison of two absolute drops — so it has **no ratio
  denominator** and is immune to the packed arm's near-zero pool. It is the
  v1-H2 replication under the first-class prober.
- **(b) Mechanism intervention (within-spread, paired):** with NodeLocal
  DNSCache enabled, spread's own UDP drop shrinks by **≥50 %** relative to
  its cache-off drop. The denominator is spread's cache-off drop, which v1
  measured as large (DESIGN §10), so the ratio is well-defined.

**Combination rule.** (a) AND (b) — a conjunction, both must pass
(conservative; no internal correction). V2-H2's single input to the outer
Holm family is **max(p_a, p_b)**.

**Secondary check (registered, not in family).** The packed (f = 0) arm is
expected to show **no cache effect**: its UDP pool sits near the noise floor
(~72–224 entries in the v1 protocol probe — DESIGN §10;
`thesis/data/conntrack-probe/`), which is precisely why it cannot serve as a
ratio denominator. A material cache effect in the packed arm would be
reported as evidence against the cross-node-DNS account.

**Motivating pilot.** v1-H2 (flush asymmetry, 7/7 sessions) + the protocol
probe (the placement-dependent component is UDP/DNS; kube-proxy's cleanup is
deliberately UDP-only); see DESIGN §10 / `hypotheses.md`.

**Test (primary, in family).** (a) Paired Wilcoxon signed-rank on
per-session (spread − packed) UDP-drop differences, cache-off arms;
(b) Wilcoxon signed-rank on the per-session paired shrinkage of spread's
UDP drop (cache-on vs cache-off); sessions paired by design (same cluster
state window, randomized cache order).

**SESOI / falsified by.** (a) falsified if the spread > packed direction
does not hold (test n.s. at the Holm-adjusted α) — placement-dependence
failed to replicate; (b) supported iff the median paired shrinkage is ≥50 %
with a bootstrap CI excluding 0 % — if spread's UDP drop persists at >50 %
of its cache-off size with the cache on, the UDP/DNS account is **wrong**
and reported as falsified. Either part failing falsifies V2-H2 as
registered (the parts are reported separately so a (a)-pass/(b)-fail is
visible as "placement-dependent but not via DNS"). TCP-drop behaviour is
recorded but carries no registered prediction (v1 evidence: kernel-side,
not placement-mediated).

### V2-H3 — Replication rescue under node-drain

**Statement.** At r = 3 **anti-affine**, user-visible availability loss under
node-drain is smaller than at r = 1 by a pre-set margin, while r = 3
**packed** is equivalent to r = 1 — i.e. an interaction: replication rescues
availability only when replicas do not share the failure domain.

**Motivating pilot.** v1-H6 (blast = placement-predicted blast) and the
deliberately skipped E1 pilot (v1's engine could not express anti-affinity);
see DESIGN §10 / `hypotheses.md`.

**Test (primary, in family).** Aligned-rank-transform (ART) ANOVA on
user-visible availability loss, factors r ∈ {1, 3} × mode {packed,
anti-affine}; the registered effect is the **interaction term**, evaluated
on **two co-primary outcomes — EndpointSlice trough depth × duration, and
user-route error rate — combined as both-must-pass (conjunction; see
§Confirmatory family)**. Margin (for the anti-affine r = 3 vs r = 1
contrast): **TBD at freeze**, set from A/A variance, no smaller than the A/A
95 % noise band.

**Packing control (instrument check, TOST).** The packed r = 3 ≈ r = 1
control is registered as a **TOST equivalence test**: packed r = 3 must fall
**within the A/A-derived equivalence band of r = 1** (band finalized at M2,
same band as the rescue margin). Falling **outside the band in either
direction** flags the instrument (the engine does not pack as specified —
an instrument failure triggering the validity checks, not a finding). A bare
"packed r3 < r1" inequality is *not* used: near-equal arms differ by noise
in both directions, and an unthresholded inequality would make the control a
coin flip.

**Falsified by.** No interaction on either co-primary outcome (e.g.
anti-affine r = 3 ≈ r = 1: replication does not rescue), with the packing
control passing its TOST (instrument valid).

### V2-H4 — The placement frontier (DESCRIPTIVE — registered figure + reporting protocol)

**Status.** **Descriptive, not a falsifiable hypothesis, and not in the
confirmatory family.** The label V2-H4 is retained for continuity. Rationale
(registered): under cluster-bootstrap CIs on noisy two-face coordinates,
"≥2 non-dominated placements" is nearly self-confirming — the noisier the
data, the more non-dominated points appear — so no confirmatory claim is
registered on frontier cardinality.

**Reporting protocol (binding).** For each placement (f, r, mode), report
east-west tail (latency face) vs blast radius/recovery (availability face)
with cluster-bootstrap CIs. **Dominance is declared only with margins**:
placement A dominates B iff A is better than B by **≥ δ_latency on the
latency face AND ≥ δ_blast on the availability face**, with δ values tied to
the A/A noise band (**finalized at M2**). The non-dominated set under those
margins is reported, with the margins stated alongside the figure. A single
placement dominating all others by ≥ δ on both faces would be reported
prominently as the headline result, not suppressed — this protocol exists to
prevent the opposite temptation.

### V2-H5 — The layered scorecard is reliable, and more reliable than the v1 aggregate

**Statement.** On fresh v2 campaign sessions, each **required** layered
sub-score is more reliable than the v1 aggregate score computed on the same
v2 sessions (ICC(sub-score) > ICC(v1 aggregate); ICC_old as recorded in
DESIGN §10 is the motivating pilot), **and** reaches an **absolute bar:
ICC ≥ 0.5**. The sub-score roles are fixed here: **required (confirmatory):
availability and mechanism-reconvergence** — the two layers v1 showed carry
signal a score must capture; **exploratory (reported, not confirmatory):
user-tail** — v1's central finding is precisely that the user layer decouples
from placement under these fault classes, so its sub-score's reliability is
informative but not load-bearing for the instrument claim. Beating ICC_old
alone is a near-zero bar (the aggregate is already known to be unreliable);
the absolute bar is what makes the scorecard's reliability a claim about
usefulness, not just superiority over a broken instrument.

**Registered threat — design-informed-by-v1 circularity.** The three
sub-scores were chosen *because* v1 showed signal in those layers; an
evaluation on v1 sessions would therefore be circular. **Mitigation
(binding):** (1) sub-score definitions are **frozen at the M2 commit,
before any v2 reliability data exists**; (2) reliability is evaluated
**exclusively on v2 campaign sessions — never on v1 sessions**; (3) the
absolute ICC ≥ 0.5 bar applies in addition to the head-to-head comparison.

**Test (primary, in family).** Both instruments computed per session from
the same v2 raw data; condition-level ICC for each; for **each of the two
required sub-scores**: bootstrap CI on the difference ICC_new − ICC_old must
exclude zero, **and** the ICC point estimate must reach ≥ 0.5 with its CI
excluding ICC_old. The two required sub-scores are combined as a
**conjunction — both must pass** (conservative; no internal multiplicity
correction needed). V2-H5's single input to the outer four-member Holm
family is **max(p_availability, p_mechanism)** — the larger of the two
required sub-scores' p-values, matching the conjunction rule. The user-tail
sub-score is evaluated identically but reported as exploratory, uncorrected,
outside both V2-H5's decision rule and the family.

**Falsified by.** **Either required sub-score** (availability or
mechanism-reconvergence) failing the absolute ICC ≥ 0.5 bar, or its
reliability CI overlapping or falling below the aggregate's — reported as
the scorecard failing its own test. The user-tail sub-score's outcome can
neither pass nor falsify V2-H5; it is reported alongside. (Pass rule and
falsification rule are logical complements over the same two-element
required set.)

### V2-H6 — Exploratory secondary: iptables-mode direction transfer

**Status.** **Exploratory secondary, outside the confirmatory family,
uncorrected, labeled as such.** Reduced cells: the **f = 0 and f = 1
endpoints only** (riding on C1/C3 endpoint cells). **Second in the
pre-declared de-scope order** (dropped after the second workload if M1
overruns).

**Statement.** The **spread-vs-packed direction of the UDP-drop contrast
(V2-H2's cache-off arms)** is preserved under kube-proxy **iptables** mode:
spread's during-churn UDP-conntrack drop exceeds packed's, as under ipvs.

**Test.** Sign test across **≥5 sessions** at the f = 0/f = 1 endpoints
only. No magnitude prediction is registered — v1's evidence is
mode-specific by construction; direction is the only transferable claim.

**Reported as.** Direction preserved / direction not preserved /
not-attempted (if de-scoped), in the exploratory-results section only.

## A/A calibration protocol

Before **any** between-condition comparison: **≥3 identical-placement
session pairs** (same f, r, mode, fault; nothing varied) are run and pushed
through the full analysis pipeline as if they were A/B comparisons.

**What the A/A block can and cannot do (registered honestly).** Its two
achievable functions are:

1. **Variance-component estimation** — within- and between-session variance
   feed the M2 power analysis (per-cell n), the SESOI noise bands (V2-H1),
   the V2-H3 margin and TOST equivalence band, V2-H4's δ dominance margins,
   and the pre-window UDP-slope taint threshold (§Session design).
2. **Qualitative pipeline sanity check** — the full pipeline runs end-to-end
   on null data and its outputs are inspected.

**It is NOT a false-positive-rate estimator, and no numeric "FPR ≤ α" gate
is registered.** The arithmetic is plain: to upper-bound a 0.05
false-positive rate, observing **zero** significant A/A findings in n
independent tests gives a 95 % Clopper–Pearson upper bound of
1 − 0.05^(1/n), which reaches 0.05 only at **n ≥ 59** — on the order of
**60+ A/A tests**, far beyond what 3 session pairs (FPR resolution
{0, 1/3, 2/3, 1}) can deliver. A handful of A/A pairs cannot certify any α.

**Rule (replaces the dropped FPR gate).** **Any statistically significant
A/A finding triggers investigation and a fix-then-rerun**: the cause is
diagnosed, the `doctor` gates / taint rules / instrumentation are fixed, and
the A/A block is repeated before any comparison runs. The halt criterion is
in §Stopping rules.

## Session design

- **Unit of analysis: the session** (one provisioned cluster lifetime, one
  commit), exactly as in v1's E2 campaign — between-session variance was a
  large share of v1 score variance (DESIGN §10); ignoring it produced v1's
  retracted user-layer readings.
- **C1 sessions are complete blocks:** every C1 session visits **all 5
  f-levels in randomized order**, from a recorded seed (v1 fixed the order,
  making order effects constant but unmeasurable; v2 randomizes and
  records). This is what licenses Page's L for V2-H1.
- **Load generator (binding, from DESIGN §4):** Locust runs **host-side**,
  excluded by construction from cross-node-fraction edge accounting and
  per-node conntrack aggregation. The pre-chaos baseline window starts only
  **after the load ramp completes + 60 s settle**. Validity check: the
  pre-window UDP-entry slope must be ≈ 0 (threshold tied to the A/A noise
  band, **finalized at M2**); iterations violating it are tainted.
- **≥2 sessions per cell minimum**; actual n per cell **TBD from the M2
  power analysis** against each SESOI — placeholders here are deliberately
  not numbers, to avoid anchoring before the A/A variance is known.
- **Gating:** every session must pass `doctor --strict`; sessions whose
  achieved fraction misses target by >0.05 are rejected (logged, counted,
  reported). No result is ever quoted from a rejected or tainted session.
- **Versioning:** each campaign is archived (raw `summary.json`s + collector
  raws + manifests + commit hash) and deposited with a DOI before its results
  are analyzed for writing.

## Stopping / abandon rules

1. **Solver gate (M1b) — decidable terms.** An **"attempt"** is one full
   solve → apply → schedule → verify cycle starting from a clean app deploy.
   The gate passes a level when **3 consecutive attempts at that f-level**
   each land within ±0.05 of target; the counter is **per f-level and resets
   on a miss** at that level. The gate outcome is recorded by a **committed
   verification artifact** — the solver log plus the achieved-f table,
   checked by a `doctor` rule — not by judgment. The gate **must run at the
   pinned N = 6** (the reachable-fraction set is N-dependent) and re-runs if
   the 8 × 4 GiB fallback cluster is adopted. **No-go** → the pre-committed
   nearest-achievable-fraction fallback (achieved-f as regressor; V2-H1
   switches to its pre-registered mixed-model / Jonckheere–Terpstra tests).
   The switch is recorded here before freeze.
2. **A/A gate (M2).** Any statistically significant A/A finding →
   investigate, fix, rerun the A/A block (see §A/A). **Halt criterion: a
   second statistically significant A/A finding after a fix** — the campaign
   is **halted** and the instrumentation redesigned; no comparative claims
   are made from the existing pipeline.
3. **Capacity null — decidable predicate.** The rule fires at a cell when
   the **app-ready gate fails in >50 % of iterations in ≥2 consecutive
   sessions at that cell**, with the failure signature **distinguished from
   v1's benign capacity-timeout taint** (see
   `chaosprobe/docs/how-to/reproducing-thesis-results.md`): a route showing
   ~100 % errors / zero successful pre-chaos samples is a broken probe — a
   bug to fix, not capacity. The rule covers **the anti-affine r = 3 arm,
   not only the f = 0/f = 1 extremes**: a service's 3 replicas need 3
   schedulable distinct nodes, and the rule fires if anti-affine scheduling
   fails (replicas Pending on anti-affinity at the pinned N). On firing, the
   affected campaign is descoped to the achievable region and the descope
   reported — not silently narrowed.
4. **Second-environment transfer arm — pre-declared droppable.** If the
   second environment is **unavailable by the end of M4** (procurement /
   billing gate M0 unexercised, per [`02-WORKPLAN.md`](02-WORKPLAN.md)), the
   transfer arm is dropped and **reported as not-attempted**. V2-H4 and
   V2-H5 depend only on primary-environment data and complete regardless.
5. **De-scope order under M1 overrun (pre-declared).** First drop the
   second workload (hotelReservation), then the iptables arm (V2-H6).
   Recorded as deviations.

## Deviations policy

After the freeze commit, every deviation (changed n, dropped cell, modified
test) is logged in a `DEVIATIONS.md` adjacent to this file, with date,
reason, and whether it was decided blind to outcome data.
