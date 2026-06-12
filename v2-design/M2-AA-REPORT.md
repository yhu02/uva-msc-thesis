# M2 report — A/A calibration block + power analysis: **PASS (with freeze decisions pending)**

> Status: A/A block complete (2026-06-12), 3 identical-placement session
> pairs (6 sessions), all banked `doctor --strict` clean. All 6 sessions ran
> at commit 47a4673 (#271, clean tree, per `runMetadata.git`); the canonical
> analysis script `scripts/m2_aa_analysis.py` landed mid-block as #272
> (analysis-only — no runtime-behavior change, so session validity is
> unaffected). Supplementary extraction + power analysis: this report.
> Cluster: the M0 fallback **N=8 × 4 GiB** (1 cp 6 GiB), Kubernetes v1.28.6.
> **The freeze itself waits on the decisions in §Freeze decisions (D1–D7).**

## Block design (registered protocol, instantiated)

The prereg registers: ≥3 identical-placement session pairs through the full
pipeline ("same f, r, mode, fault; nothing varied"). Instantiated as complete-block
C1-type sessions (pod-delete, r=1, packed, 5 f-levels × 3 iterations,
randomized condition order): a pair shares `--v2-solver-seed` (identical
placements, verified — exact `liveAchievedF` identity and identical
assignments within every pair); `--v2-order-seed` differs (order effects
randomize out). Two instantiation choices go beyond the prereg's A/A text
and are to be recorded as one-sentence wording amendments at the freeze:
(i) A/A sessions are complete blocks (all 5 levels), not single-cell
sessions; (ii) the order seed varies within a pair while the solver seed is
shared.

| Pair | Sessions (results/v2-aa/) | solverSeed | orderSeeds |
|---|---|---|---|
| 1 | 20260611-184530, 20260611-213923 | 0 | 11, 12 |
| 2 | 20260612-002516, 20260612-030816 | 1 | 21, 22 |
| 3 | 20260612-074544, 20260612-103215 | 2 | 31, 32 |

All 6 sessions: `doctor --strict` 0 errors; 90 churn iterations total,
**1 tainted** (below). The solver hit every target level within tolerance in
every session (f=0.0000 / 0.2667 / 0.5333 / 0.7333 / 1.0000 — gaps ≤ 0.034).

## Verdict — registered A/A functions

**1. Qualitative pipeline sanity check: PASS, with a structural caveat.**
The canonical analysis (per-pair Wilcoxon at the registered unit — one
session-condition value per level, n=5 levels per pair — plus the
cross-pair drift test) finds **no significant A/A finding at α=0.05** on
any of its four delta metrics (`ew_p95_during_ms`, `conntrack_flush_pct`,
`udp_conntrack_drop_pct`, `score`). **Caveat (disclose to the sign-off
reader):** the per-pair test is floor-limited near α: on 5 untied paired
levels its attainability floor is p≈0.0591 (exact sign floor 0.0625), and
the n=3 cross-pair sign test's floor is 0.25 — so for continuous metrics
the registered-unit tests cannot reach α=0.05 at this block size and their
PASS is partly by construction. (The implemented test *can* reject on tied
|Δ| — five same-sign equal-magnitude deltas give p=0.0369 via the
tie-corrected approximation, a pathway `m2_aa_analysis.py`'s docstring
flags as plausible for the 1-dp-rounded score — so the canonical rule is
not strictly inoperative; in practice the A/A significant-finding rule's
sensitivity lives in the supplementary iteration-level/pooled tests, which
can and did fire — F1.) Pair 2's `conntrack_flush_pct` in fact sits at the
untied floor (5/5 deltas one-signed, p=0.0591). This attainability limit
belongs in the D1 scope amendment. Note also these are
the canonical
*pipeline* metrics, not metrics registered in these forms — in particular
`udp_conntrack_drop_pct` is a ratio form of the drop, and the prereg
deliberately registers V2-H2(a) on **absolute** drops because the packed
arm's near-zero pool makes ratios ill-defined; the D4 consolidation must
align the canonical script's metric forms with the registered tests before
freeze. Tainted-iteration exclusion in the canonical script applies to the
`score` metric only (its other aggregates predate the taint plumbing); the
supplementary extraction excludes taints from every outcome. A
supplementary iteration-level *sensitivity* pairing (not the registered
unit) shows one hit — see §Findings log (F1).

**2. Variance-component estimation: DONE.** Components and noise bands
below feed the power analysis and the TBD SESOIs/margins. All values from
the taint-excluded supplementary artifact (`aa_block.py`; an earlier draft
of this report transcribed several rows from a pre-exclusion run — caught
in review and regenerated wholesale, see F4).

| Outcome (supplementary extraction) | sd_within (iter) | sd_between (session, within pair) | A/A band p95 of paired \|Δmedian\| | band vs level |
|---|---|---|---|---|
| East-west p95, pre-chaos [ms] | 3.56 | 1.26 | 4.44 | **11.2 %** of 39.5 ms |
| East-west p95, during-chaos [ms] | 0.48 | 0.55 | 1.97 | 4.8 % of 41.4 ms |
| UDP conntrack drop [entries] | 173 | 110 | 414 | 57 % of mean 732 |
| EndpointSlice trough depth [pods] | 0.35 | 0.23 | 1.0 | 118 % of 0.85 |
| Trough duration (proxy: pod recovery) [s] | 0.15 | 0.07 | 0.24 | 16 % of 1.47 |
| User-route error rate, during [rate] | 0.098 | 0.079 | 0.302 | **409 %** of 0.074 |
| Pre-window UDP slope [entries/min] | 389 | 311 | 1104 | see F2 |
| Aggregate resilience score [points] | 19.5 | 20.5 | 64 | **93 %** of 68.4 |

Two readings the design predicted, now quantified: the **v1 aggregate
score's per-condition A/A band runs to ±50–67 points** (per-condition max
paired |Δ|; pooled median 50, p95 = 64) on a ~68-point level — the
instrument V2-H5 replaces is as unreliable on v2 data as the prereg
assumed; and the **user-route error rate is so noisy (band ≈ 4× its
level)** that V2-H3's error-rate co-primary needs the margin discipline the
prereg already imposes.

**Proposed V2-H4 δ dominance margins (TBD fill-in, tied to D4):**
δ_latency = the EW-p95 A/A p95 band of the window D4 selects (4.4 ms
pre-chaos / 2.0 ms during-chaos); δ_blast = 1.0 pod trough depth and 0.30
user-route error rate (the availability-face bands above). The prereg ties
the δ values to the A/A noise band without a floor rule; we propose
adopting V2-H3's "no smaller than the A/A 95 % noise band" convention for
the H4 δs as well — to be recorded as a freeze wording amendment.

## Power analysis → per-cell n (recommendation)

Monte Carlo (20k reps/scenario) on the A/A variance components; both
α=0.05 (best case) and α=0.0125 (Holm worst case). Full numbers:
`/tmp/power_sim.py` (output `/tmp/power_sim_output.txt`), summarized:

- **V2-H1 (Page's L, SESOI = 15 % EW-p95 span):** overpowered — power
  0.89–0.92 at **n=4** sessions at α=0.0125 across the homo-/heteroscedastic
  sensitivity scenarios (0.915 primary, 0.891 heteroscedastic). Real
  resolution: a half-SESOI (7.5 %) trend needs n≈10. **Computed under the
  supplementary pre-chaos extraction** — contingent on D4 (see below).
- **V2-H2(a) (paired Wilcoxon, spread−packed UDP drop):** the effect to
  detect (~2000 entries) is a dose-endpoint-derived planning assumption —
  the f-100 vs f-000 drop contrast in this block, anchored to v1's measured
  spread-vs-colocate contrast — not an observed spread-vs-packed A/B
  measurement (none exists yet). At ~2000 entries vs the 414-entry band,
  power is 1.0 at every *attainable* n; the binding constraint is
  **Wilcoxon attainability (one-sided, the registered direction): n≥5 for
  any rejection at α=0.05, n≥7 at α=0.0125** (the §Verdict floors above are
  the two-sided A/A variants — both are correct for their tests).
- **V2-H2(b) (≥50 % shrinkage):** n=7 (α=0.0125) at true shrinkage 70–80 %;
  the fragile case is 60 % true shrinkage → **n=11**.
- **V2-H3 (interaction MDE at n=8/cell, α=0.0125):** ~0.8 pods trough
  depth / ~0.26 error rate — both inside the prereg's "margin ≥ A/A noise
  band" rule (bands: 1.0 pod / 0.302).

**Recommendation: n=8 sessions per cell** (Wilcoxon floor 7 + 1 margin;
H3 MDE ≈ the noise-band margin at 8/cell; H1 saturated). If C1 throughput
allows, n=11 additionally covers H2(b)'s 60 %-shrinkage case. (~3 h/session
on this cluster.) **No V2-H5 power analysis was run** (recorded judgment,
not an oversight): ICC-difference power requires assumed true sub-score
ICCs, and the sub-scores have no v2 pilot estimate to assume — their
definitions don't exist in code yet (§Instrumentation gaps). H5's n rides
on the C1 session count; revisit if the freeze sets sub-score definitions
that admit a pilot estimate.

## Findings log (registered rule: significant A/A finding → investigate → fix → rerun)

- **F1 — iteration-level offset, pair 1, during-chaos EW p95 (p=0.007).**
  A constant ~0.3–3.5 ms session-level offset (one session above its twin in
  11/15 cells). At the registered unit (condition-level medians) the same
  data is n.s. (p=0.28). Investigation: this is precisely *between-session
  variance*, which the iteration-level pairing is maximally sensitive to and
  which the registered session-as-unit analyses absorb by design; it is in
  the variance components feeding every margin. The prereg does not pin
  *which tests count* as "a statistically significant A/A finding" — the
  no-fix disposition below therefore needs both a sign-off (D1) and a freeze
  amendment scoping the rule (registered-unit tests vs any pipeline output).
  **Disposition (proposed): not a pipeline defect — no fix, no rerun;
  recorded here.**
- **F2 — the registered "pre-window UDP slope ≈ 0" taint rule is unworkable
  as written.** The pre-chaos UDP pool carries large placement-coupled
  transients (per-session per-level medians, printed by `aa_block.py`;
  per-level |Δ| bands in the artifact): ≈ +140 to +870 entries/min at
  f-025/f-050 (f-025 median ≈ 188, f-050 ≈ 750) and ≈ **−6600 to −8800
  entries/min at f-075/f-100** (decay following re-placement), consistent
  across all 6 sessions. A naive band-derived absolute threshold would taint
  most interior/high-f iterations. Needs redefinition before freeze (D3).
- **F3 — infrastructure wedge, session 6, condition f-100 (~14:10–14:30).**
  A transient pod-network dataplane wedge centered on one worker (libvirt/
  WSL2 flake class; diagnosed live: pod-IP TCP unreachable to worker5 while
  all other node pairs passed; self-cleared). The pipeline's (v1-inherited)
  taint gates caught the worst of it: iteration 3 tainted
  (`app_ready_timeout`, `pre_chaos_errors_high`) and excluded from the
  score aggregate and from every supplementary outcome (the canonical
  route/conntrack aggregates still include it — consolidation item, D4),
  removing a −1938-entry UDP-drop outlier from the supplementary numbers. Scope honestly stated: the
  apparent pair-3 UDP "A/A hit" existed only in the supplementary
  iteration-level test (p=0.038 → 0.069 after exclusion — marginal); the
  registered-unit test was n.s. with or without it3. Residual judgment call
  on the sibling iterations: D2.
- **F4 — analysis defects found & fixed during the block (transparency).**
  (i) The supplementary analysis draft initially *recorded* taints without
  *excluding* tainted iterations; fixed same-day. (ii) An earlier draft of
  *this report* transcribed several variance-table rows from the
  pre-exclusion run (trough duration, UDP pre-slope, score, error-rate
  level, EW-pre sd) — caught by the review pass; the table above is
  regenerated wholesale from the taint-excluded artifact. The prereg's
  literal wording is "no result is ever quoted from a rejected or tainted
  **session**"; excluding tainted *iterations* (v1 pipeline convention,
  and what `m2_aa_analysis.py` implements) is an interpretation the freeze
  wording must pin — under the literal session-level reading, session 6
  would be wholly excluded and pair 3 invalid. Folded into D2.

## Freeze decisions (user) — D1–D7

1. **D1:** Accept F1's disposition (iteration-level offset = between-session
   variance, not a pipeline defect → no block rerun)? Includes the freeze
   amendment pinning the A/A significant-finding (and halt) rule's scope:
   registered-unit tests only, or any pipeline output.
2. **D2:** Taint semantics + session 6. The prereg says tainted **session**;
   the pipeline excludes tainted **iterations**. Pin the rule at freeze
   (proposal: iteration-level exclusion + a session-level rule, e.g. "a
   session with every iteration of any condition tainted is excluded").
   Then: s6's f-100 untainted siblings ran during partial degradation —
   (a) trust the iteration-level gates as implemented — keep (the numbers
   above use this); (b) exclude all of s6 f-100 / rerun s6 as a documented
   deviation.
3. **D3:** Redefine the UDP pre-slope taint rule (F2). Candidates:
   per-f-level slope bands from this block; or a longer post-placement
   settle before the pre-window; or drop the slope taint in favor of the
   existing `pre_chaos_errors_high` family. The operational taint gates
   should also be enumerated in the prereg at freeze if they are to carry
   registered-rule weight in D2-style decisions.
4. **D4:** Pin V2-H1's outcome operationalization — the prereg's "median
   east-west p95" doesn't pin median-over-what or the window, and the two
   implementations differ materially **at the same during-chaos window**:
   canonical #272 = mean over routes (f-025 within-pair session SD
   **6.9 ms**); supplementary = median over routes (f-025 deltas ~0.2 ms).
   **The SESOI-exceeds-band requirement currently holds only under the
   supplementary extraction** (pre-chaos median-over-routes: band 11.2 % <
   15 %); under the canonical mean-over-routes form, the f-025 band alone
   (6.9 ms) exceeds the SESOI span under either anchor — 6.2 ms anchored to
   the during-chaos level (41.4 ms) it is measured at, 5.9 ms anchored to
   the pre-chaos level (39.5 ms) — so the SESOI or the operationalization
   would have to change. The H1 power numbers above are
   computed under the supplementary extraction; if D4 selects another, the
   H1 scenario must be re-run (cheap — same simulator, new variance inputs)
   before the n freeze. The two scripts consolidate to whichever wins.
5. **D5:** Per-cell n: **8** (recommended) vs 11 (covers H2(b) @ 60 %
   shrinkage).
6. **D6:** V2-H2(b)'s planning test: literal registered form (median ≥50 %
   + bootstrap CI excluding 0) vs the stricter Wilcoxon-vs-50 % used in the
   power analysis (conservative; n=7–11 driver).
7. **D7:** hotelReservation gate sufficiency. What exists: solver gate
   decided **on the static upstream-derived graph, in-memory** (every
   f-target hit exactly in a seed-sweep unit test — 1/16 quanta), capacity
   check from **declared manifest requests** (static math, PASS at
   N=8×4 GiB), and a **live deploy availability check** (2026-06-11). The
   M1b-style *live* solve→apply→verify protocol has NOT run for this
   workload. Accept the static gate as "decided" (recording the
   downgraded procedure as a freeze deviation), or run the live gate
   before freeze (~1 h, cluster is up).

Caveat recorded for H3 margins: between-session sd here is *within
same-seed pairs*; cross-seed between-session variance may be larger
(condition-order × placement-history effects randomize but don't vanish).
The H3 margin should not be set tighter than the band on this account.

## Instrumentation gaps carried forward (fix before the affected campaign)

- **15 s EndpointSlice trough sampler** (DESIGN §4) is not in the collected
  data — only pre/during/post snapshots; trough *duration* is currently
  proxied by pod recovery time. Needed before C2/node-drain (V2-H3).
- **Layered sub-scores (V2-H5)** are not implemented in the package; their
  *definitions* freeze at M2 regardless (prereg circularity mitigation).
  The aggregate-score A/A band (±50–67 pts per condition) is banked as the
  comparator.
- **Analysis consolidation** per D4: one canonical extraction shared by the
  A/A script and the campaign analyses; align the canonical metric forms
  with the registered tests (absolute UDP drop, not the pct ratio).

## M2 exit criteria status (workplan)

| Criterion | Status |
|---|---|
| A/A block clean (no unexplained significant findings) | ✅ registered-unit tests all n.s.; F1 explained, disposition pending D1 |
| Prober round-trips into `summary.json`; smoke `doctor --strict` passes | ✅ (all 6 sessions; prober data first-class) |
| hotelReservation measured + capacity check + solver gate decided | ◑ static gate decided (in-memory, exact); live M1b-style gate not run — D7 |
| All TBDs resolved | ⏳ values proposed above (incl. V2-H4 δ); pending D1–D7 |
| Power analyses against each SESOI | ✅ this report (H1 re-run needed if D4 changes the extraction) |
| PREREG wording amendments (M1b carry-overs + D1–D4 scoping + block-design instantiation) | ⏳ at freeze |
| Pre-registration frozen by commit hash + DOI-deposited | ⏳ blocked on D1–D7 |
