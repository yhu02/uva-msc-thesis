# ChaosProbe v2 — pre-registration (DRAFT, not yet frozen)

> **Status: DRAFT.** This document becomes binding only when frozen by commit
> hash at the end of Month 2 (after A/A calibration and power analysis — see
> [`02-WORKPLAN.md`](02-WORKPLAN.md)). Until that freeze, hypotheses, SESOIs,
> and n's may be revised; **after** it, any deviation is reported as a
> deviation. No campaign data informing these hypotheses may be collected
> before the freeze. The build has not started; placeholders marked **TBD**
> are filled from M2's A/A variance estimates before freezing.

Design context, knobs, and instrumentation: [`00-DESIGN.md`](00-DESIGN.md).
All v1 references (H1–H6) are to archived, `doctor --strict`-clean runs
documented in `chaosprobe/docs/explanation/hypotheses.md`.

## Hypotheses

### V2-H1 — Dose-response of the east-west tail in cross-node fraction

**Statement.** Median east-west (inter-service) p95 latency increases
monotonically in the achieved cross-node fraction f across the designed
levels {0, 0.25, 0.5, 0.75, 1.0}.

**Motivating pilot.** v1-H5: node-local (f ≈ 0) placements showed ~1.25×
lower east-west tails than spreading (f ≈ 0.7–0.8) placements in two
independent batches, but the interior was never sampled and the continuous
correlation did not replicate (ρ 0.79 → 0.25).

**Test.** Page's trend test across the ordered f levels (session medians as
units; Spearman over designed levels as the sensitivity check). n sessions
per level: **TBD from M2 power analysis** (≥2 minimum, §Session design).

**SESOI.** A monotone trend with a total effect of **≥10 % increase in
east-west p95 from f = 0 to f = 1**. A statistically detectable but <10 %
trend is reported as below the SESOI, not as support.

**Falsified by.** Non-monotone medians beyond noise (Page's test n.s. at
α = 0.05), or a monotone trend smaller than the SESOI. If the two-regime
step shape recurs (flat interior, jump at the extremes), that is a
*distinct* registered outcome: "threshold, not dose-response" — it would
confirm v1-H5's separator reading and falsify the continuous-dose reading.

### V2-H2 — Intervention: NodeLocal DNSCache collapses the placement-dependent flush component

**Statement.** With NodeLocal DNSCache enabled, the spread-vs-packed
(f = 1 vs f = 0, r = 1) difference in during-churn UDP-conntrack drop shrinks
by **≥50 %** relative to cache-off, paired across sessions.

**Motivating pilot.** v1-H2 (flush 38.5 % vs 2.7 %, spread > colocate 7/7
sessions, sign test p = 0.0156) plus the protocol probe: the
placement-dependent component is UDP/DNS (~4× more UDP entries under spread;
kube-proxy's cleanup is deliberately UDP-only).

**Test.** Paired comparison (Wilcoxon signed-rank) of the per-session
spread-minus-packed UDP-drop difference, cache-on vs cache-off; sessions
paired by design (same cluster state window, randomized cache order).

**SESOI / falsified by.** Supported iff the median paired shrinkage is
≥50 % with a bootstrap CI excluding 0 % shrinkage. If the spread-vs-packed
UDP-drop difference persists at >50 % of its cache-off size, the UDP/DNS
account of H2's placement dependence is **wrong** and is reported as
falsified. TCP-drop behaviour is recorded but carries no registered
prediction (v1 evidence: kernel-side, not placement-mediated).

### V2-H3 — Replication rescue under node-drain

**Statement.** At r = 3 **anti-affine**, user-visible availability loss under
node-drain is smaller than at r = 1 by a pre-set margin, while r = 3
**packed** ≈ r = 1 — i.e. an interaction: replication rescues availability
only when replicas do not share the failure domain.

**Motivating pilot.** v1-H6 (blast = placement-predicted blast, ρ = 1.0,
n = 6; colocate 11/11 services down vs spread 2/11) and the cancelled E1
pilot, which showed v1's engine *could not* express anti-affinity (all 3
replicas pinned to one node → structurally null).

**Test.** Aligned-rank-transform (ART) ANOVA on user-visible availability
loss (EndpointSlice trough depth × duration, and user-route error rate as
co-primary), factors r ∈ {1, 3} × mode {packed, anti-affine}; the registered
effect is the **interaction term**. Margin (the "pre-set margin" for the
anti-affine r = 3 vs r = 1 contrast): **TBD at freeze**, set from A/A
variance, no smaller than the A/A 95 % noise band.

**Falsified by.** No interaction (e.g. anti-affine r = 3 ≈ r = 1: replication
does not rescue), or packed r = 3 < r = 1 (the packing control fails,
implying the engine does not pack as specified — an instrument failure,
triggering the validity checks, not a finding).

### V2-H4 — The placement frontier is non-degenerate

**Statement.** No placement (f, r, mode) dominates both faces — east-west
tail (latency) and blast radius/recovery (availability) — and the Pareto
frontier contains **≥2 non-dominated placements**.

**Test.** Descriptive, with cluster-bootstrap CIs on each placement's
two-face coordinates; a placement dominates iff its CI region beats another's
on both faces simultaneously. No p-value is registered; this hypothesis is
falsified descriptively.

**Falsified by.** A single placement whose CI region dominates all others on
both faces. Per `00-DESIGN.md` §6, that outcome would be reported prominently
as the headline result, not suppressed — the registration exists to prevent
the opposite temptation.

### V2-H5 — The layered scorecard is more reliable than the v1 aggregate score

**Statement.** On identical session data, the test-retest reliability of the
layered sub-scores exceeds that of the v1 aggregate score:
ICC(sub-scores) > ICC(v1 aggregate), where v1's campaign value was
ICC = 0.033 [0.014, 0.178].

**Test.** Both instruments computed per session from the same raw data;
condition-level ICC for each; bootstrap CI on the **difference**
ICC_new − ICC_old must exclude zero. Evaluated per sub-score (availability,
mechanism, user-tail), Holm-corrected; the scorecard "passes" only if at
least the availability and mechanism sub-scores individually beat the
aggregate.

**Falsified by.** Any sub-score's reliability CI overlapping or falling below
the aggregate's — reported as the scorecard failing its own test.

## A/A calibration protocol (gates everything above)

Before **any** between-condition comparison: **≥3 identical-placement session
pairs** (same f, r, mode, fault; nothing varied) are run and pushed through
the full analysis pipeline as if they were A/B comparisons.

- The pipeline's empirical false-positive rate across all registered tests
  must be **≤ nominal α (0.05)**. If it exceeds α, the `doctor` gates and
  taint rules are tightened and the A/A block is **repeated** before any
  comparison runs.
- A/A variance estimates also fix the TBD quantities: per-cell n (power
  analysis at 80 % power for each SESOI), and V2-H3's margin.

## Session design

- **Unit of analysis: the session** (one provisioned cluster lifetime, one
  commit), exactly as in v1's E2 campaign — between-session variance was
  37.6 % of score variance in v1; ignoring it produced v1's retracted
  user-layer readings.
- **Randomized condition order within session**, from a recorded seed
  (v1 fixed the order, making order effects constant but unmeasurable; v2
  randomizes and records).
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

1. **Solver gate (M1).** If the solver cannot hit f targets within ±0.05 on
   the live cluster, switch to the pre-committed **nearest-achievable-
   fraction fallback** (achieved-f as regressor; V2-H1 becomes an
   observed-dose trend test). The switch is recorded here before freeze.
2. **A/A gate (M2).** If, after one round of gate/taint tightening, the A/A
   false-positive rate still exceeds **2α**, the campaign is **halted** and
   the instrumentation redesigned; no comparative claims are made from the
   existing pipeline.
3. **Capacity null.** If both extreme placements (f = 0 and f = 1) fail
   app-readiness gates on the primary cluster (cluster cannot host the
   design), the affected campaign is descoped to the achievable region and
   the descope reported — not silently narrowed.

## Deviations policy

After the freeze commit, every deviation (changed n, dropped cell, modified
test) is logged in a `DEVIATIONS.md` adjacent to this file, with date,
reason, and whether it was decided blind to outcome data.
