# ChaosProbe — pre-registration (FROZEN 2026-06-13)

> **Status: FROZEN 2026-06-13** at git tag **`prereg-freeze`** (this
> commit). The document is now **binding**: hypotheses, SESOIs, per-cell n,
> outcome operationalizations, and the analysis code are fixed. From this
> point, **any** deviation (changed n, dropped/added cell, modified test,
> revised threshold) is logged in [`DEVIATIONS.md`](DEVIATIONS.md) with date,
> reason, and whether it was decided blind to outcome data (§Deviations
> policy). **No campaign (C1–C3) data informing these hypotheses has been
> collected before this freeze** — only the M2 A/A calibration block (null
> data, same-placement pairs) and the M1a/M1b solver-gate spikes, none of
> which test a registered hypothesis.
> Incorporated: the **M2 freeze amendments** (decisions D1–D7, applied
> 2026-06-12; see §M2 freeze amendments) filling every **TBD** /
> **finalized at M2** placeholder from the M2 A/A variance estimates and
> power analysis ([`M2-AA-REPORT.md`](M2-AA-REPORT.md)), and the **D7
> hotelReservation live-gate result** (PASS, 2026-06-13; §Workloads). The
> exact frozen state, raw A/A data, and gate artifacts are archived under DOI
> [**10.5281/zenodo.20690836**](https://doi.org/10.5281/zenodo.20690836)
> (published 2026-06-14; manifest [`FREEZE-DEPOSIT.md`](FREEZE-DEPOSIT.md)).

Design context, knobs, and instrumentation: [`00-DESIGN.md`](00-DESIGN.md).
All earlier references (H1–H6) are to archived, `doctor --strict`-clean runs
documented in `chaosprobe/docs/explanation/hypotheses.md`; **the earlier numeric
literals are quoted only in DESIGN §10** (the canonical mapping table) and
cited from here by reference.

## Confirmatory family and multiplicity

- **Confirmatory family:** the **single primary test of each of H1,
  H2, H3, and H5**, **Holm-corrected** across this four-member
  family.
- **H3's two co-primary outcomes** (trough depth × duration; user-route
  error rate) are combined as **both-must-pass (conjunction)**: H3 is
  supported only if *both* outcomes show the registered interaction. A
  conjunction is conservative (it can only lower the per-hypothesis type-I
  rate), so no additional alpha adjustment is applied for the two outcomes.
- **H4 is descriptive** — a registered figure and reporting protocol, not
  a member of the family; no p-value is registered.
- **H6 is an exploratory secondary** — labeled as such, uncorrected,
  outside the family; its outcome is reported but cannot support a
  confirmatory claim.
- Sensitivity analyses (e.g. H1's Spearman check) are non-confirmatory
  and labeled as such wherever reported.

## Hypotheses

### H1 — Dose-response of the east-west tail in cross-node fraction

**Statement.** Median east-west (inter-service) p95 latency increases
monotonically in the achieved cross-node fraction f across the designed
levels {0, 0.25, 0.5, 0.75, 1.0}.

**Motivating pilot.** The earlier study's H5 (two-regime separator, interior never sampled);
see DESIGN §10 / `hypotheses.md`.

**Design (required for the test's validity).** Every C1 session is a
**complete block**: it visits **all 5 f-levels in randomized order**
(recorded seed). Sessions are therefore replicated complete ordered blocks,
which is exactly the design Page's L requires.

**Test (primary, in family).** Page's trend test across the ordered f levels
(session medians as units, one observation per level per session). Spearman
over designed levels is the sensitivity check (non-confirmatory). n sessions
per level: **8** — every C1 session is a complete block visiting all 5
levels, so this is 8 C1 sessions (filled at M2 — D5: Page's L is saturated,
power 0.89–0.92 at the Holm-worst α already at n = 4 under both variance
scenarios; [`M2-AA-REPORT.md`](M2-AA-REPORT.md)).

**Outcome operationalization (pinned at the M2 freeze — D4).** "Median
east-west p95" is computed as: per iteration, the **median over
inter-service routes of the route p95**, with **loadgenerator→ routes
excluded**; window = **pre-chaos**; the unit entering Page's test is the
**session-condition median** of those per-iteration values (one value per
f-level per session). The canonical extraction is
`scripts/m2_aa_analysis.py` (schema, #275); campaign analyses use the
same code path. (The alternative mean-over-routes / during-chaos form was
rejected at M2 because its A/A band alone exceeds the SESOI span —
[`M2-AA-REPORT.md`](M2-AA-REPORT.md) D4.)

**Fallback path (pre-registered now).** If the M1b solver gate forces the
nearest-achievable-fraction design (DESIGN §9), the regressor becomes
continuous achieved-f and Page's test no longer applies. The replacement is
fixed here: **primary = linear mixed-effects model** with achieved-f as a
continuous fixed effect and session as a random effect (test on the
achieved-f slope); **secondary (nonparametric) = Jonckheere–Terpstra** over
the ordered achievable levels. The switch, if taken, is recorded before
freeze. **Recorded before freeze: the switch is NOT taken** — the M1b
solver gate **passed** (all 5 levels within ±0.05, 3/3 consecutive
attempts; [`m1b-gate-artifact.json`](m1b-gate-artifact.json), run at the
adopted 8 × 4 GiB fallback cluster per Stopping rule 1's re-run provision).
The designed-level dose design and Page's test stand.

**SESOI.** A monotone trend with a total effect of **≥15 % increase in
east-west p95 from f = 0 to f = 1**. Derivation: the bar is set **between
the A/A noise band — measured at M2 as 11.2 % of the pre-chaos level (p95
paired |Δmedian| 4.44 ms on a 39.5 ms level), which the 15 % SESOI (a
5.93 ms span) exceeds, satisfying the registered SESOI-exceeds-band
requirement under the D4 operationalization — and the earlier measured ~25 %
two-regime separation** (DESIGN §10), so that
an effect at the earlier magnitude clears it with margin while anything inside
instrument noise cannot. A statistically detectable but <15 % trend is
reported as below the SESOI, not as support.

**Falsified by.** Non-monotone medians beyond noise (primary test n.s. at
the Holm-adjusted α), or a monotone trend smaller than the SESOI. If the
two-regime step shape recurs (flat interior, jump at the extremes), that is
a *distinct* registered outcome: "threshold, not dose-response" — it would
confirm the earlier H5's separator reading and falsify the continuous-dose reading.

### H2 — Placement-dependence replicates, and the DNS intervention explains it

**Statement (two-part conjunction — both parts confirmatory).** H2
deliberately tests **both halves of the earlier H2 claim**: that the
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
  earlier H2 replication under the first-class prober.
- **(b) Mechanism intervention (within-spread, paired):** with NodeLocal
  DNSCache enabled, spread's own UDP drop shrinks by **≥50 %** relative to
  its cache-off drop. The denominator is spread's cache-off drop, which the earlier study
  measured as large (DESIGN §10), so the ratio is well-defined.

**Combination rule.** (a) AND (b) — a conjunction, both must pass
(conservative; no internal correction). H2's single input to the outer
Holm family is **max(p_a, p_b)**.

**Secondary check (registered, not in family).** The packed (f = 0) arm is
expected to show **no cache effect**: its UDP pool sits near the noise floor
(~72–224 entries in the earlier protocol probe — DESIGN §10;
`thesis/data/conntrack-probe/`), which is precisely why it cannot serve as a
ratio denominator. A material cache effect in the packed arm would be
reported as evidence against the cross-node-DNS account.

**Motivating pilot.** The earlier study's H2 (flush asymmetry, 7/7 sessions) + the protocol
probe (the placement-dependent component is UDP/DNS; kube-proxy's cleanup is
deliberately UDP-only); see DESIGN §10 / `hypotheses.md`.

**Test (primary, in family).** (a) Paired Wilcoxon signed-rank on
per-session (spread − packed) UDP-drop differences, cache-off arms;
(b) **one-sided Wilcoxon signed-rank of the per-session paired shrinkage of
spread's UDP drop (cache-on vs cache-off) against the 50 % bar** (pinned at
the M2 freeze — D6: the stricter of the two candidate forms, and the one
the M2 power analysis was run on; the shrinkage median and its bootstrap CI
are still reported, descriptively); sessions paired by design (same cluster
state window, randomized cache order).

**SESOI / falsified by.** (a) falsified if the spread > packed direction
does not hold (test n.s. at the Holm-adjusted α) — placement-dependence
failed to replicate; (b) supported iff the one-sided Wilcoxon rejects
"shrinkage ≤ 50 %" at the Holm-adjusted α (decision rule amended at the M2
freeze to the stricter test form — D6; the draft's median-with-CI wording is
quoted in §M2 freeze amendments) — if spread's UDP drop persists at >50 %
of its cache-off size with the cache on, the UDP/DNS account is **wrong**
and reported as falsified. Either part failing falsifies H2 as
registered (the parts are reported separately so a (a)-pass/(b)-fail is
visible as "placement-dependent but not via DNS"). TCP-drop behaviour is
recorded but carries no registered prediction (earlier evidence: kernel-side,
not placement-mediated).

### H3 — Replication rescue under node-drain

**Statement.** At r = 3 **anti-affine**, user-visible availability loss under
node-drain is smaller than at r = 1 by a pre-set margin, while r = 3
**packed** is equivalent to r = 1 — i.e. an interaction: replication rescues
availability only when replicas do not share the failure domain.

**Motivating pilot.** The earlier study's H6 (blast = placement-predicted blast) and the
deliberately skipped E1 pilot (the earlier engine could not express anti-affinity);
see DESIGN §10 / `hypotheses.md`.

**Test (primary, in family).** Aligned-rank-transform (ART) ANOVA on
user-visible availability loss, factors r ∈ {1, 3} × mode {packed,
anti-affine}; the registered effect is the **interaction term**, evaluated
on **two co-primary outcomes — EndpointSlice trough depth × duration, and
user-route error rate — combined as both-must-pass (conjunction; see
§Confirmatory family)**. Margin (for the anti-affine r = 3 vs r = 1
contrast), set from A/A variance per the registered "no smaller than the
A/A 95 % noise band" rule (filled at the M2 freeze): **trough depth
1.0 pod; user-route error rate 0.302** — the per-outcome A/A 95 % noise
bands (p95 of paired |Δmedian|, taint-excluded) from the 2026-06-12 A/A
block ([`M2-AA-REPORT.md`](M2-AA-REPORT.md)). Caveat recorded with the
fill: the A/A between-session sd is measured *within same-solver-seed
pairs*; cross-seed between-session variance may be larger, so the margin
is not set tighter than the band.

**Packed-cell semantics (M1b carry-over, folded in at the M2 freeze).**
The packed mode is **per-service replica packing** — each service's
replicas on exactly one node — with **services round-robin distributed
across nodes** (capacity-feasible), not all services on a single node, as
implemented and verified in the M1b gate.

**Packing control (instrument check, TOST).** The packed r = 3 ≈ r = 1
control is registered as a **TOST equivalence test**: packed r = 3 must fall
**within the A/A-derived equivalence band of r = 1** (band finalized at the
M2 freeze: the same per-outcome bands as the rescue margin — 1.0 pod trough
depth, 0.302 user-route error rate). Falling **outside the band in either
direction** flags the instrument (the engine does not pack as specified —
an instrument failure triggering the validity checks, not a finding). A bare
"packed r3 < r1" inequality is *not* used: near-equal arms differ by noise
in both directions, and an unthresholded inequality would make the control a
coin flip.

**Falsified by.** No interaction on either co-primary outcome (e.g.
anti-affine r = 3 ≈ r = 1: replication does not rescue), with the packing
control passing its TOST (instrument valid).

### H4 — The placement frontier (DESCRIPTIVE — registered figure + reporting protocol)

**Status.** **Descriptive, not a falsifiable hypothesis, and not in the
confirmatory family.** The label H4 is retained for continuity. Rationale
(registered): under cluster-bootstrap CIs on noisy two-face coordinates,
"≥2 non-dominated placements" is nearly self-confirming — the noisier the
data, the more non-dominated points appear — so no confirmatory claim is
registered on frontier cardinality.

**Reporting protocol (binding).** For each placement (f, r, mode), report
east-west tail (latency face) vs blast radius/recovery (availability face)
with cluster-bootstrap CIs. **Dominance is declared only with margins**:
placement A dominates B iff A is better than B by **≥ δ_latency on the
latency face AND ≥ δ_blast on the availability face**, with δ values tied to
the A/A noise band — finalized at the M2 freeze: **δ_latency = 4.4 ms** (the
pre-chaos east-west-p95 A/A p95 band, matching the pre-chaos window pinned
by D4) and **δ_blast = 1.0 pod trough depth and 0.302 user-route error
rate** (the availability-face bands; [`M2-AA-REPORT.md`](M2-AA-REPORT.md)).
The δs adopt H3's floor convention — **no smaller than the A/A 95 %
noise band** (an amendment: the draft tied δ to the band without any floor
rule — §M2 freeze amendments). The non-dominated set under those
margins is reported, with the margins stated alongside the figure. A single
placement dominating all others by ≥ δ on both faces would be reported
prominently as the headline result, not suppressed — this protocol exists to
prevent the opposite temptation.

### H5 — The layered scorecard is reliable, and more reliable than the aggregate

**Statement.** On fresh campaign sessions, each **required** layered
sub-score is more reliable than the aggregate score computed on the same
sessions (ICC(sub-score) > ICC(aggregate); ICC_old as recorded in
DESIGN §10 is the motivating pilot), **and** reaches an **absolute bar:
ICC ≥ 0.5**. The sub-score roles are fixed here: **required (confirmatory):
availability and mechanism-reconvergence** — the two layers the earlier study showed carry
signal a score must capture; **exploratory (reported, not confirmatory):
user-tail** — the earlier central finding is precisely that the user layer decouples
from placement under these fault classes, so its sub-score's reliability is
informative but not load-bearing for the instrument claim. Beating ICC_old
alone is a near-zero bar (the aggregate is already known to be unreliable);
the absolute bar is what makes the scorecard's reliability a claim about
usefulness, not just superiority over a broken instrument.

**Registered threat — design-informed-by-the-earlier-study circularity.** The three
sub-scores were chosen *because* the earlier study showed signal in those layers; an
evaluation on the earlier sessions would therefore be circular. **Mitigation
(binding):** (1) sub-score definitions are **frozen at the M2 commit,
before any reliability data exists**; (2) reliability is evaluated
**exclusively on campaign sessions — never on the earlier sessions**; (3) the
absolute ICC ≥ 0.5 bar applies in addition to the head-to-head comparison.

**Test (primary, in family).** Both instruments computed per session from
the same raw data; condition-level ICC for each; for **each of the two
required sub-scores**: bootstrap CI on the difference ICC_new − ICC_old must
exclude zero, **and** the ICC point estimate must reach ≥ 0.5 with its CI
excluding ICC_old. The two required sub-scores are combined as a
**conjunction — both must pass** (conservative; no internal multiplicity
correction needed). H5's single input to the outer four-member Holm
family is **max(p_availability, p_mechanism)** — the larger of the two
required sub-scores' p-values, matching the conjunction rule. The user-tail
sub-score is evaluated identically but reported as exploratory, uncorrected,
outside both H5's decision rule and the family.

Sub-score aggregation formulas were specified post-freeze on 2026-06-13, blind
to all campaign data — see [`DEVIATIONS.md`](DEVIATIONS.md) entry
D-2026-06-13-01; the constituent signals and the evaluation rule above are
unchanged from the freeze.

**Falsified by.** **Either required sub-score** (availability or
mechanism-reconvergence) failing the absolute ICC ≥ 0.5 bar, or its
reliability CI overlapping or falling below the aggregate's — reported as
the scorecard failing its own test. The user-tail sub-score's outcome can
neither pass nor falsify H5; it is reported alongside. (Pass rule and
falsification rule are logical complements over the same two-element
required set.)

### H6 — Exploratory secondary: iptables-mode direction transfer

**Status.** **Exploratory secondary, outside the confirmatory family,
uncorrected, labeled as such.** Reduced cells: the **f = 0 and f = 1
endpoints only** (riding on C1/C3 endpoint cells). **Second in the
pre-declared de-scope order** (dropped after the second workload if M1
overruns).

**Statement.** The **spread-vs-packed direction of the UDP-drop contrast
(H2's cache-off arms)** is preserved under kube-proxy **iptables** mode:
spread's during-churn UDP-conntrack drop exceeds packed's, as under ipvs.

**Test.** Sign test across **≥5 sessions** at the f = 0/f = 1 endpoints
only. No magnitude prediction is registered — the earlier evidence is
mode-specific by construction; direction is the only transferable claim.

**Reported as.** Direction preserved / direction not preserved /
not-attempted (if de-scoped), in the exploratory-results section only.

## A/A calibration protocol

Before **any** between-condition comparison: **≥3 identical-placement
session pairs** (same f, r, mode, fault; nothing varied) are run and pushed
through the full analysis pipeline as if they were A/B comparisons.

**Block-design instantiation (recorded at the M2 freeze).** A/A sessions
are **complete blocks** — each visits all 5 f-levels — not single-cell
sessions; within a pair the **solver seed is shared** (identical
placements, verified by exact `liveAchievedF` and assignment identity)
while the **order seed varies** (order effects randomize out). Both choices
go beyond the original "nothing varied" wording and are recorded as
amendments (§M2 freeze amendments). The 2026-06-12 block
([`M2-AA-REPORT.md`](M2-AA-REPORT.md)) ran 3 such pairs (6 sessions), all
`doctor --strict` clean.

**What the A/A block can and cannot do (registered honestly).** Its two
achievable functions are:

1. **Variance-component estimation** — within- and between-session variance
   feed the M2 power analysis (per-cell n), the SESOI noise bands (H1),
   the H3 margin and TOST equivalence band, H4's δ dominance margins,
   and the per-f-level pre-window UDP-slope bands (§Session design; D3).
2. **Qualitative pipeline sanity check** — the full pipeline runs end-to-end
   on null data and its outputs are inspected.

**It is NOT a false-positive-rate estimator, and no numeric "FPR ≤ α" gate
is registered.** The arithmetic is plain: to upper-bound a 0.05
false-positive rate, observing **zero** significant A/A findings in n
independent tests gives a 95 % Clopper–Pearson upper bound of
1 − 0.05^(1/n), which reaches 0.05 only at **n ≥ 59** — on the order of
**60+ A/A tests**, far beyond what 3 session pairs (FPR resolution
{0, 1/3, 2/3, 1}) can deliver. A handful of A/A pairs cannot certify any α.

**Rule (replaces the dropped FPR gate; scope pinned at the M2 freeze —
D1).** **A statistically significant finding in a REGISTERED-UNIT A/A test
triggers investigation and a fix-then-rerun**: the cause is diagnosed, the
`doctor` gates / taint rules / instrumentation are fixed, and the A/A block
is repeated before any comparison runs. The registered-unit tests are the
**per-pair Wilcoxon on session-condition values** (one value per f-level
per session, n = 5 levels per pair) and the **cross-pair drift test**.
Supplementary iteration-level tests are run and reported as **sensitivity
checks only — they are NOT rule triggers**: the iteration-level pairing is
maximally sensitive to between-session variance, which the registered
session-as-unit analyses absorb by design and which the variance components
already carry into every margin. The halt criterion is in §Stopping rules.

**Attainability floor (disclosed with the rule).** At this block size the
registered-unit tests are floor-limited near α: on 5 untied paired levels
the per-pair test's attainability floor is p ≈ 0.0591 (exact sign floor
0.0625); the tied-|Δ| pathway can still reject at p = 0.0369 (five
same-sign equal-magnitude deltas under the tie-corrected approximation);
the n = 3 cross-pair sign test's floor is 0.25. A registered-unit PASS at
this block size is therefore partly by construction for continuous
metrics; sensitivity to subtler defects lives in the supplementary
iteration-level checks, which are reported alongside.

**2026-06-12 A/A block outcome (recorded).** No significant
registered-unit finding on any delta metric. One supplementary
iteration-level finding (pair 1, during-chaos east-west p95, p = 0.007 — a
constant ~0.3–3.5 ms session-level offset; n.s. at the registered unit,
p = 0.28) was investigated and dispositioned as **between-session
variance, not a pipeline defect — no fix, no rerun** (D1; F1 in
[`M2-AA-REPORT.md`](M2-AA-REPORT.md)).

## Session design

- **Unit of analysis: the session** (one provisioned cluster lifetime, one
  commit), exactly as in the earlier E2 campaign — between-session variance was a
  large share of the earlier score variance (DESIGN §10); ignoring it produced the earlier
  retracted user-layer readings.
- **C1 sessions are complete blocks:** every C1 session visits **all 5
  f-levels in randomized order**, from a recorded seed (the earlier study fixed the order,
  making order effects constant but unmeasurable; this study randomizes and
  records). This is what licenses Page's L for H1.
- **Load generator (binding, from DESIGN §4):** Locust runs **host-side**,
  excluded by construction from cross-node-fraction edge accounting and
  per-node conntrack aggregation. The pre-chaos baseline window starts only
  **after the load ramp completes + 60 s settle**. Validity check
  (**redefined at the M2 freeze — D3**; the draft's "slope ≈ 0" wording is
  quoted in §M2 freeze amendments): the A/A block showed the pre-window UDP
  pool carries **placement-coupled transients**, so an absolute ≈ 0
  threshold is unworkable — per-level slopes run ≈ +140 to +870 entries/min
  at f-025/f-050 and ≈ −6600 to −8800 entries/min at f-075/f-100 (decay
  after re-placement), consistently across all 6 A/A sessions. The
  registered check is therefore **per-f-level slope bands**: an iteration
  is **tainted when its pre-window UDP-entry slope falls outside its
  f-level's A/A band**, the bands sourced from the 2026-06-12 A/A block
  artifact ([`M2-AA-REPORT.md`](M2-AA-REPORT.md), F2).
- **n = 8 sessions per cell** (filled at the M2 freeze — D5, from the M2
  power analysis, [`M2-AA-REPORT.md`](M2-AA-REPORT.md)): the one-sided
  Wilcoxon attainability floor at the Holm-worst α (0.0125) is n = 7, plus
  1 margin; the H3 interaction MDE at 8/cell (~0.8 pods trough depth /
  ~0.26 error rate) sits inside the registered noise-band margins; H1 is
  saturated (power ≥ 0.89 at n = 4). Known limitation (recorded): H2(b)
  at a true shrinkage of 60 % would need n = 11; n = 8 powers the 70–80 %
  planning case.
- **Gating:** every session must pass `doctor --strict`; sessions whose
  achieved fraction misses target by >0.05 are rejected (logged, counted,
  reported). **Taint semantics (pinned at the M2 freeze — D2; the draft's
  "tainted session" wording is quoted in §M2 freeze amendments): tainted
  ITERATIONS are excluded from every metric** — their rows are preserved as
  `None` so pairing structure is kept — and a **SESSION is excluded only
  when every iteration of any one of its conditions is tainted**. No result
  is ever quoted from a rejected session or from a tainted iteration.
- **Registered taint rules (enumerated at the M2 freeze — D3, so they carry
  registered-rule weight).** An iteration is tainted by any of the
  operational gates implemented in the pipeline: `app_ready_timeout` (the
  proactive functional readiness gate timed out), `pre_chaos_errors_high`
  (>10 % pre-chaos probe errors), `pre_chaos_latency_degraded` (pre-chaos
  p95 AND mean above threshold; skipped by design under an active load
  profile), `iteration_exception` (the iteration crashed),
  `unknown_probes_after_retries` (unknown-dominated chaos verdict after
  retries) — plus the per-condition placement-validity gates
  `placement_verification_failed` and `fraction_target_missed` (achieved f
  off target by >0.05), and the per-f-level pre-window UDP-slope band check
  above (D3).
- **Versioning:** each campaign is archived (raw `summary.json`s + collector
  raws + manifests + commit hash) and deposited with a DOI before its results
  are analyzed for writing.

## Workloads (added at the M2 freeze)

- **online-boutique (primary):** M1b solver gate **PASS** at the adopted
  N = 8 × 4 GiB fallback cluster — all 5 f-levels within ±0.05, 3/3
  consecutive attempts, ≥30 % capacity headroom, r = 3 anti-affine and
  packed both schedulable ([`m1b-gate-artifact.json`](m1b-gate-artifact.json),
  [`M1B-REPORT.md`](M1B-REPORT.md)).
- **hotelReservation (second workload):** decided before freeze in two
  steps. Static gate (recorded): solver gate on the static upstream-derived
  graph, in-memory — every f-target hit exactly through the seed sweep
  (1/16 quanta); capacity check from declared manifest requests, PASS at
  N = 8 × 4 GiB; live deploy availability check 2026-06-11. Per **D7**, the
  full M1b-protocol **live** solve → apply → schedule → verify gate was
  additionally run before the freeze; its verdict: **PASS** (2026-06-13,
  [`m1b-gate-artifact-hotel.json`](m1b-gate-artifact-hotel.json),
  `chaosprobe/m1b-gate-artifact/v2` schema). All 5 f-levels accepted **3/3 on the first three attempts**
  (live-pod fraction exactly on target); r = 3 anti-affine 19/19 services
  on distinct nodes (50.0 s) and r = 3 packed 19/19 (54.6 s); capacity
  headroom 48 % CPU / 69 % memory (≥ 30 % floor). **Instrument-defect
  transparency** (mirroring the online-boutique gate's two pre-pass
  failures): the first two live runs FAILED f = 0.00 (best streak 1/3 in 6
  attempts) — the solver reached the f = 0 optimum from only ~22 % of seeds
  on this 19-service / 16-edge **tree** topology, so the 3-consecutive rule
  could not converge there. Diagnosed offline as a local-minimum trap
  (collapsing to one node requires cut-increasing intermediate moves) and
  fixed with a deterministic collapsed warm-start (the closed-form f = 0
  optimum) in the solver — f = 0 hit-rate 22 % → 100 %, capacity paths
  unchanged, online-boutique M1a reachability preserved. The PASS above is
  the live gate on the fixed solver.
- De-scope order under overrun: §Stopping rules, rule 5.

## Stopping / abandon rules

1. **Solver gate (M1b) — decidable terms.** An **"attempt"** is one full
   solve → apply → schedule → verify cycle **starting from a restored
   (unpinned) state** — not a full app redeploy (amended per the M1b
   carry-over: the scheduling decision under test is identical, and a full
   redeploy per attempt would triple gate duration for no informational
   gain; the artifact records the protocol as `attemptProtocol`; the
   draft's "clean app deploy" wording is quoted in §M2 freeze amendments).
   Within an attempt the solver runs a **sweep of up to 5 distinct seeds**
   (stepped so consecutive attempts never re-try each other's seeds); the
   **first solver-accepted solution is applied**, with a best-gap fallback,
   and the attempt is **judged on live pods only** (`liveAchievedF`); the
   seeds tried are recorded in the artifact (`solverSeedsTried`, #276).
   The gate passes a level when **3 consecutive attempts at that f-level**
   each land within ±0.05 of target; the counter is **per f-level and resets
   on a miss** at that level. The gate outcome is recorded by a **committed
   verification artifact** — the solver log plus the achieved-f table,
   checked by a `doctor` rule — not by judgment. The gate **must run at the
   pinned N = 6** (the reachable-fraction set is N-dependent) and re-runs if
   the 8 × 4 GiB fallback cluster is adopted. **No-go** → the pre-committed
   nearest-achievable-fraction fallback (achieved-f as regressor; H1
   switches to its pre-registered mixed-model / Jonckheere–Terpstra tests).
   The switch is recorded here before freeze. **Outcome (recorded): the
   fallback cluster was adopted at M0, the gate ran at N = 8 per this
   rule's re-run provision, and it PASSED** (§Workloads); the no-go
   fallback is not taken.
2. **A/A gate (M2).** Any statistically significant **registered-unit** A/A
   finding (scope pinned at the M2 freeze — D1; see §A/A) → investigate,
   fix, rerun the A/A block. **Halt criterion: a second statistically
   significant registered-unit A/A finding after a fix** — the campaign
   is **halted** and the instrumentation redesigned; no comparative claims
   are made from the existing pipeline.
3. **Capacity null — decidable predicate.** The rule fires at a cell when
   the **app-ready gate fails in >50 % of iterations in ≥2 consecutive
   sessions at that cell**, with the failure signature **distinguished from
   the earlier benign capacity-timeout taint** (see
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
   transfer arm is dropped and **reported as not-attempted**. H4 and
   H5 depend only on primary-environment data and complete regardless.
5. **De-scope order under M1 overrun (pre-declared).** First drop the
   second workload (hotelReservation), then the iptables arm (H6).
   Recorded as deviations.

## M2 freeze amendments (2026-06-12)

All amendments below were decided **before the freeze and before any
comparative campaign data exists** (the only data collected is the A/A
calibration block itself, which the prereg always designated as the source
of these fill-ins). Decision IDs D1–D7 refer to
[`M2-AA-REPORT.md`](M2-AA-REPORT.md) §Freeze decisions, accepted by the
user 2026-06-12; the M1b carry-overs were pre-announced in
[`M1B-REPORT.md`](M1B-REPORT.md) §Pre-freeze amendments. Each amendment is
also applied to the body text above, so the document reads consistently.

1. **(D1) A/A significant-finding rule scoped to registered-unit tests.**
   The draft rule said "any statistically significant A/A finding" without
   pinning which tests count. Now scoped: **registered-unit tests only**
   (per-pair Wilcoxon on session-condition values + cross-pair drift);
   supplementary iteration-level tests are reported sensitivity checks, not
   rule triggers. Rationale: iteration-level pairing is maximally sensitive
   to between-session variance, which the registered session-as-unit
   analyses absorb by design. The registered-unit attainability floors are
   disclosed in §A/A (untied n = 5 floor p ≈ 0.0591 / exact 0.0625;
   tied-|Δ| pathway p = 0.0369; cross-pair n = 3 floor 0.25). The
   2026-06-12 block's F1 (iteration-level p = 0.007) is recorded as
   between-session variance — no fix, no rerun. Applied in §A/A and
   §Stopping rules 2.
2. **(D2) Taint semantics pinned.** Old wording (§Session design): "No
   result is ever quoted from a rejected or tainted session." Replaced:
   **tainted iterations are excluded from every metric** (`None` rows
   preserve pairing); a **session is excluded only when every iteration of
   any one condition is tainted**. This registers the earlier-inherited pipeline
   convention the analysis code implements. Consequence recorded: A/A
   session 6's f-100 untainted sibling iterations are **kept** (the
   iteration-level gates as implemented are trusted; the M2 variance table
   uses this), rather than excluding all of s6 f-100.
3. **(D3) Pre-window UDP-slope validity check redefined.** Old wording
   (§Session design): "the pre-window UDP-entry slope must be ≈ 0
   (threshold tied to the A/A noise band, finalized at M2); iterations
   violating it are tainted." Replaced by **per-f-level slope bands** from
   the 2026-06-12 A/A block artifact: the pre-window UDP pool carries
   placement-coupled transients (~+140 to +870 entries/min at f-025/f-050;
   ~−6600 to −8800 at f-075/f-100), so an absolute ≈ 0 threshold would
   taint most interior/high-f iterations (F2). An iteration is tainted when
   its pre-window slope falls outside its level's A/A band. The operational
   taint gates are also now **enumerated as registered taint rules**
   (§Session design) so they carry registered-rule weight in D2-style
   decisions. The old "absolute threshold finalized at M2" framing is
   removed everywhere (incl. §A/A function 1 and DESIGN §4 — propagated).
4. **(D4) H1 outcome operationalization pinned.** Per-iteration
   **median over inter-service routes of route p95** (loadgenerator→
   routes excluded), **pre-chaos window**, **session-condition median** as
   the unit; canonical extraction `scripts/m2_aa_analysis.py` (schema,
   #275). SESOI check recorded: 15 % of the pre-chaos level (39.5 ms →
   5.93 ms span) vs the A/A p95 band of 11.2 % — the SESOI exceeds the
   band, as the SESOI derivation requires. The rejected mean-over-routes /
   during-chaos form fails that requirement (its f-025 band alone exceeds
   the SESOI span).
5. **(D5) Per-cell n = 8 sessions.** Fills H1's "n TBD" and §Session
   design's "n per cell TBD". Basis (M2 power analysis): Wilcoxon
   attainability floor 7 at the Holm-worst α + 1 margin; H3 MDE ≈ the
   noise-band margin at 8/cell; H1 saturated at n = 4. Limitation
   recorded: H2(b) at 60 % true shrinkage would need n = 11.
6. **(D6) H2(b) decision rule pinned to the stricter form.** Old wording
   (H2 SESOI/falsified-by): "(b) supported iff the median paired
   shrinkage is ≥50 % with a bootstrap CI excluding 0 %". Replaced:
   **one-sided Wilcoxon of the per-session paired shrinkage against the
   50 % bar** (the form the power analysis was run on; strictly more
   conservative). The median + bootstrap CI remain reported descriptively.
   The falsification substance (UDP/DNS account wrong if the drop persists
   at >50 % with the cache on) is unchanged.
7. **(D7) hotelReservation gate run live — PASS.** The static gate
   (in-memory seed-sweep solver gate on the static topology;
   declared-requests capacity math; live deploy availability check) is
   recorded in §Workloads, and the M1b-protocol **live** gate was run
   before the freeze rather than accepting the static gate alone as
   "decided". Verdict: **PASS** (2026-06-13,
   [`m1b-gate-artifact-hotel.json`](m1b-gate-artifact-hotel.json)) — all 5
   f-levels 3/3 first-attempt, r = 3 anti-affine + packed both 19/19,
   capacity 48 % / 69 % headroom. Recorded with instrument-defect
   transparency: the first two live runs failed f = 0 on a solver
   search-reliability defect (≈22 % seed hit-rate on the tree topology),
   fixed by a collapsed warm-start (f = 0 → 100 %); the PASS is on the
   fixed solver. No placeholders remain.
8. **(H3 TBDs) Margin and TOST band filled:** rescue margin = max A/A
   95 % noise band per outcome — **trough depth 1.0 pod, user-route error
   rate 0.302**; TOST equivalence band = the same bands. Same-seed-pair
   caveat recorded (margin not set tighter than the band).
9. **(H4 TBDs + floor rule) δs filled and floor convention adopted:**
   δ_latency = **4.4 ms** (pre-chaos EW-p95 A/A p95 band, matching D4's
   window), δ_blast = **1.0 pod + 0.302 error rate**. Amendment proper: the
   draft tied the δs to the A/A noise band but had **no floor rule**; the
   δs now adopt H3's "no smaller than the A/A 95 % noise band"
   convention.
10. **(M1b carry-over) "Attempt" definition.** Old wording (§Stopping
    rules 1): "one full solve → apply → schedule → verify cycle starting
    from a clean app deploy". Replaced: **from a restored (unpinned)
    state** — the scheduling decision under test is identical and a full
    redeploy per attempt would triple gate duration for no informational
    gain (`attemptProtocol` in the artifact). Additionally, per #276,
    attempts solve through a **≤5-distinct-seed sweep** (consecutive
    attempts never re-try each other's seeds), the first accepted solution
    is applied, and attempts are judged on **live pods only**.
11. **(M1b carry-over) Packed-cell semantics.** Packed mode = per-service
    replica packing with services round-robin distributed
    (capacity-feasible), as implemented and verified — recorded in H3.
12. **(Block design) A/A instantiation recorded.** A/A sessions are
    complete blocks (all 5 f-levels); within a pair the solver seed is
    shared and the order seed varies — beyond the draft's "same f, r,
    mode, fault; nothing varied" wording (§A/A).
13. **(M1b outcome) Solver-gate result and fallback disposition recorded.**
    The gate ran at the adopted N = 8 fallback cluster per Stopping rule
    1's re-run provision and PASSED; H1's
    nearest-achievable-fraction fallback switch is **not taken** (recorded
    in H1 and §Stopping rules 1).

**REMAINING-OPEN (the complete list):**

- **None.** Every TBD / "finalized at M2" marker in the draft is resolved
  above, and the D7 hotelReservation live-gate result (§Workloads) is
  filled (PASS, 2026-06-13). The freeze waits only on the freeze commit +
  DOI deposit — mechanics, not open questions.

## Deviations policy

After the freeze commit, every deviation (changed n, dropped cell, modified
test) is logged in a `DEVIATIONS.md` adjacent to this file, with date,
reason, and whether it was decided blind to outcome data.
