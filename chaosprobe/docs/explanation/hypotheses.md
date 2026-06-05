# Hypotheses & findings

The empirical backbone of the thesis, stated as falsifiable hypotheses and tied
to the committed script that reproduces each number. This page documents *what
the tool's data shows*; the dissertation prose (full argument, related work)
lives outside this tree — see [`../../../references.md`](../../../references.md).

Every figure below is reproducible from `results/<run>/summary.json` via the
scripts in [`../../scripts/`](../../scripts), churn (`pod-delete`) runs only,
baseline and `cpu-hog` excluded unless noted.

## Research question

> Under churn-based fault injection (`pod-delete`) on a **single-replica**
> microservice deployment, does pod-placement strategy produce a **reproducible,
> user-visible** effect on resilience — and at which measurement layer
> (aggregate availability score, kernel/network mechanism, or service-level
> outcome) is that effect, if any, detectable?

The question is deliberately layered: placement could move (a) the aggregate
score, (b) a kernel/network mechanism, and/or (c) the user-facing outcome, and
these need not agree. The contribution is establishing *which layer* the effect
lives in — and that it does **not** propagate between them under this fault class.

## H1 — The aggregate resilience score cannot rank placements

**Statement.** The probe-based aggregate resilience score does not reproducibly
discriminate placement strategies; between-strategy differences are a small
fraction of total score variance and are undetectable at any feasible iteration
count.

**Operationalization.** Variance partition of the per-iteration score into
between-strategy / run-to-run / iteration components → `ICC_strategy`; Cohen's
*d* for the focal `colocate`–`spread` contrast and the iterations/strategy
needed for 80 % power (α = .05, two-sided).

**Prediction (falsifiable).** If placement drives the score, `ICC_strategy` is
large and the focal contrast is detectable at feasible *n*.

**Result — supported.** Only **4.6 %** of score variance is between-strategy
(`ICC_strategy = 0.046`); the rest is iteration-level (61.8 %) and run-to-run
(33.6 %) noise. The focal `colocate` (68.8) vs `spread` (70.2) gap is **1.4
points** (*d* = 0.06), requiring **≈ 3,982 iterations/strategy** for 80 % power.
Even the *widest* observed gap (`dependency-aware` 78.5 vs `default` 61.4,
*d* = 0.77) needs 26/strategy, and at the *n* = 3 actually run the minimum
detectable effect is **2.29 sd ≈ 51 score points** — larger than any gap that
exists. The score isn't even stable within a single run.

```
uv run python scripts/score_variance.py
```

## H2 — Placement reproducibly moves a kernel/network reconvergence signature

**Statement.** Under churn, spreading the target's dependents across nodes
flushes a large fraction of per-node connection-tracking state during the kill
cycle; co-location does not.

**Operationalization.** conntrack flush % = `(pre_mean − during_mean)/pre_mean`
of `conntrack_entries_per_node`, per strategy per run; cross-run consistency of
`spread > colocate`.

**Prediction (falsifiable).** `spread` flush > `colocate` flush in a large
majority of runs.

**Result — supported.** `spread` flushes a **36.6 %** median vs `colocate`
**1.9 %**, with `spread > colocate` in **16 / 16** runs. This is the most
reproducible signal in the study and maps onto the Kubernetes SIG-Scalability
network-programming reconvergence window documented upstream (see references).

A secondary contention signal (CPU throttling) is *weaker* and should be
reported as corroborating only: `colocate` throttles below `default` in 13/16
runs but is not the lowest strategy overall (`best-fit` is lower). Lead with
conntrack; treat throttling as support, not a standalone claim.

```
uv run python scripts/mechanism_metrics.py
```

## H3 — The mechanism is decoupled from the user-visible outcome

**Statement.** The reproducible mechanism (H2) does **not** translate into a
reproducible user-visible outcome: reconvergence metrics do not predict tail
latency or error rate on the fault-dependent route beyond a run-level confound.

**Operationalization.** Spearman(mechanism, dependent-route tail), where
*dependent* routes touch `productcatalogservice` (the chaos target) and
*control* routes do not. The **control route is the confound control** — it
rides the same run-level slowness but does not depend on the killed service, so
a genuine fault-specific link must show ρ(dependent) significant *and* clearly
exceeding ρ(control). Confirmed with a within-run rank correlation that removes
run-level effects entirely.

**Prediction (falsifiable).** A real link → ρ(dependent) significant and
≫ ρ(control).

**Result — decoupling supported (three independent tests).**

- Pooled: conntrack flush → dependent-route p95 is ρ = 0.15 (*p* = 0.18, n.s.).
- The only mechanism reaching significance (CoreDNS p99) is **stronger on the
  control route** (ρ = 0.54) than the dependent route (ρ = 0.31) — the signature
  of a run-level confound, not causation.
- Within-run (run effect removed): mean ρ ≈ **+0.10**, median ≈ 0.
- Robust to route classification (folding the homepage into the dependent set,
  using `/_healthz` alone as control: ρ = 0.19, *p* = 0.09).

The per-strategy table needs no statistics: `dependency-aware` has the **worst**
mechanism (conntrack entries grow 20 %) and the **best** dependent-route error
rate (1.4 %); `spread` flushes 9× more conntrack than `colocate` yet they tie on
what the user experiences (8.0 % vs 8.9 % error).

```
uv run python scripts/h3_mechanism_outcome.py --csv /tmp/h3_pairs.csv
```

## Synthesis

Under single-replica churn, placement leaves a large, reproducible footprint at
the **kernel layer (H2) that never reaches the user (H3)**, while the aggregate
**score is too noisy to see anything at all (H1)**. The operator takeaway is
sharp and counter-intuitive: *for churn faults on single-replica services, where
you put the pods is not a resilience lever — survivability is governed by
availability dynamics (the killed pod is simply gone), not topology.*

### Relationship to the literature predictions

The contention-model predictions — *colocate is worst* (L1), *spread isolates
best* (L2), *recovery time predicts resilience* (L3) — are **inapplicable**, not
refuted: placement does not move the outcome under this fault class in either
direction, and `pod-delete` is a *churn* fault while those predictions concern
*contention*. (Recovery time additionally fails on its own terms — its
two-phase split is unstable run-to-run.) Whether L1–L3 hold under contention is
open; see scope.

## Scope & threats

- **Fault class:** established for `pod-delete` (churn) only. The `cpu-hog`
  (contention) matrix has *n* = 2 and does not yet reproduce — the place L1–L3
  *should* bite, and the highest-value extension.
- **Single replica:** 100 % `pod-delete` guarantees full outage, so the
  outcome is dominated by availability, not topology. The production-relevant
  question — multi-replica anti-affinity (do replicas share a failure domain?) —
  is structurally excluded by this design and is the second key extension.
- **Pooled heterogeneity:** the run set mixes probe counts (7 vs 12 → different
  score granularity) and code versions; the run-to-run variance component partly
  reflects this. It is a fair source of non-reproducibility but should be
  disclosed.
- **Cluster:** virtualized 5-node / 10-vCPU cluster — absolute metric values are
  not portable; only the *direction* of the H2 effect is.

## Label provenance

Earlier drafts used `M1–M4 / S1–S2 / L1–L3`. The mapping: H1 ← M4 (now
quantified); H2 ← M1 (conntrack), with M2 (throttling) demoted to corroboration;
H3 is new and replaces M3's "spread is safer is refuted" overclaim with the
measured decoupling; S2 (recovery split) folds into the L3 note; L1–L3 are
reframed from "refuted" to "inapplicable."
