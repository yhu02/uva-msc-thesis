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

## H4 — Under load contention, locality wins (L1/L2 inverted)

**Statement.** When the cluster is driven into *genuine* resource contention by
**load** (not an artificial hog), co-located/dense placements outperform spread:
co-location gives the lowest inter-service tail latency, spread the highest.

**Operationalization.** All 8 strategies × 3 iterations under a 200-user Locust
spike (`--load-profile spike`), with a near-no-op `cpu-hog` as a placeholder so
*load* is the stressor; compare during-chaos route tail latency (p95/max). The
binary score is read only as corroboration (it is noisy — H1).

**Why a hog won't do.** `pod-cpu-hog` is CFS-capped at the 200m container limit;
`node-cpu-hog` loads the node but CPU *requests* keep the light pods responsive
(both scored 100 with the app fully up). Contention only bites when the app is
actually resource-bound — i.e. under load.

**Result — supported.** colocate is the best real strategy, spread the worst
(`results/20260606-092037`, during-chaos):

| strategy | mean score | /product p95 | homepage max | /cart p95 |
|---|---|---|---|---|
| baseline (control) | 100 | 611 | 1138 | 692 |
| **colocate** | 94 | **966** | **1492** | **556** |
| default | 94 | 1197 | 2755 | 1409 |
| best-fit | 100 | 1735 | 2971 | 1783 |
| random | 80 | 2289 | 4401 | 2005 |
| adversarial | 80 | 3016 | 4303 | 1774 |
| dependency-aware | 78 | 3022 | 3730 | 2285 |
| **spread** | 80 | **3183** | **4060** | **1989** |

colocate vs spread: /product p95 966 vs 3183 ms (3.3×), homepage max 1492 vs
4060 ms (2.7×), /cart p95 556 vs 1989 ms (3.6×). **L1 ("colocate worst") and L2
("spread isolates best") are inverted**: co-location keeps inter-service calls
node-local so latency stays low under load, while spread routes every call across
the network — the bottleneck under load — and cgroup requests absorb the
CPU-contention cost co-location would otherwise pay.

**Provenance caveat (preliminary).** This run launched from a dirty tree
(untracked `node-memory-hog.yaml`, unused), so `doctor --strict` flags it;
measurements are valid but the headline numbers should be re-confirmed from a
clean commit before final quotation. Single run, 3 iterations.

## Synthesis

Under single-replica churn, placement leaves a large, reproducible footprint at
the **kernel layer (H2) that never reaches the user (H3)**, while the aggregate
**score is too noisy to see anything at all (H1)**. The operator takeaway is
sharp and counter-intuitive: *for churn faults on single-replica services, where
you put the pods is not a resilience lever — survivability is governed by
availability dynamics (the killed pod is simply gone), not topology.*

Under **load** contention the picture sharpens (H4): there the user-visible
outcome *is* latency, and co-location's network-path locality wins decisively —
colocate has the lowest tail latency, spread the highest, inverting L1/L2. The
through-line across both regimes is **locality**: co-location lowers
inter-service latency under churn and under load alike; under single-replica
churn that benefit is swamped by the availability collapse (H3), but under load
it is the dominant effect. Net: on this Kubernetes setup, **spreading is never
the safer choice** — cgroup isolation absorbs the contention cost the literature
assumes co-location pays, leaving locality to dominate.

### Relationship to the literature predictions

*colocate is worst* (L1), *spread isolates best* (L2), *recovery time predicts
resilience* (L3). Under **churn** these are **inapplicable** — placement does not
move the outcome (`pod-delete` is a churn fault, not a contention one), and
recovery's two-phase split is unstable run-to-run, so L3 fails on its own terms.
Under **load contention** — the regime the literature is actually about — L1 and
L2 are not merely unsupported but **inverted** (H4): co-location is the *best*
real strategy and spread the *worst*. Across every regime tested, spreading is
never the safer choice.

## Scope & threats

- **Fault class & contention:** churn (`pod-delete`) is established across the
  full run set. Contention was probed two ways: resource *hog* faults
  (`pod-cpu-hog`, `node-cpu-hog`, `node-memory-hog`) are absorbed by cgroup
  limits/requests and do not degrade the app; genuine *load* contention (H4)
  does degrade it and inverts L1/L2 — but that rests on a single 3-iteration run
  with dirty provenance and needs a clean re-run to be final.
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
