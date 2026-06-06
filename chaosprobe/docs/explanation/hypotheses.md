# Hypotheses & findings

The empirical backbone of the thesis, stated as falsifiable hypotheses and tied
to the committed script that reproduces each number. This page documents *what
the tool's data shows*; the dissertation prose (full argument, related work)
lives outside this tree — see [`../../../references.md`](../../../references.md).

Every figure below is reproducible from `results/<run>/summary.json` via the
scripts in [`../../scripts/`](../../scripts), churn (`pod-delete`) runs only,
baseline and `cpu-hog` excluded unless noted.

## Research question

> **Under which chaos fault classes does pod placement measurably affect
> mechanism-level behaviour and user-visible outcomes in a Kubernetes
> microservice application, and when do aggregate resilience scores obscure
> those effects?**

This is framed as a **fault-class-by-measurement-layer** study, not a placement
ranking and not a refutation of the placement literature. Placement could move
(a) the aggregate availability score, (b) a kernel/network mechanism, and/or
(c) the user-facing outcome, and these layers need not agree — so the
contribution is establishing *at which layer* a placement effect appears under a
given fault class, and whether it propagates to the user.

The bulk of the evidence below instantiates that question for one fault class:
churn-based injection (`pod-delete`) on a **single-replica** deployment. There
the answer is sharp and layered — placement moves the mechanism layer but not
the user layer (H1–H3). A separate, **preliminary** load-contention pilot (H4)
probes a second regime where a user-visible effect is more likely; it is
reported as a pilot, not a settled result.

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

## H4 (preliminary pilot) — Under load contention, locality appears to win

> **Status: preliminary pilot, not a settled finding.** H4 rests on a single
> 3-iteration run launched from a dirty tree (see the provenance caveat below),
> so it is reported as a pilot that motivates a clean rerun — not as evidence on
> par with the reproducible H1–H3 results. Treat the direction as indicative and
> the magnitudes as not-yet-quotable.

**Statement (pilot hypothesis).** When the cluster is driven into *genuine*
resource contention by **load** (not an artificial hog), co-located/dense
placements may outperform spread: co-location would give the lowest
inter-service tail latency, spread the highest.

**Operationalization.** All 8 strategies × 3 iterations under a 200-user Locust
spike (`--load-profile spike`), with a near-no-op `cpu-hog` as a placeholder so
*load* is the stressor; compare during-chaos route tail latency (p95/max). The
binary score is read only as corroboration (it is noisy — H1).

**Why a hog won't do.** `pod-cpu-hog` is CFS-capped at the 200m container limit;
`node-cpu-hog` loads the node but CPU *requests* keep the light pods responsive
(both scored 100 with the app fully up). Contention only bites when the app is
actually resource-bound — i.e. under load.

**Result — preliminary (single 3-iteration pilot; dirty provenance).** In this
one run, colocate showed the lowest during-chaos tail latency and spread the
highest (`results/20260606-092037`, during-chaos). This is *not* a claim that
co-location is the best strategy in general — it is a single pilot point that
warrants a clean, replicated rerun before any ranking is asserted:

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

colocate vs spread in this run: /product p95 966 vs 3183 ms (3.3×), homepage max
1492 vs 4060 ms (2.7×), /cart p95 556 vs 1989 ms (3.6×). The proposed mechanism
is that co-location keeps inter-service calls node-local so latency stays low
under load, while spread routes every call across the network — the bottleneck
under load — and cgroup requests absorb the CPU-contention cost co-location would
otherwise pay. In this single pilot the L1/L2 ordering ("colocate worst", "spread
isolates best") *appears inverted*; whether that inversion is reproducible is
exactly what the clean rerun must establish.

**Provenance caveat (why this is a pilot, not a result).** This run launched from
a dirty tree (untracked `node-memory-hog.yaml`, unused), so `doctor --strict`
flags it. The per-iteration measurements are internally valid, but with a single
run at *n* = 3 and dirty provenance the magnitudes must not be quoted as
findings; they should be re-confirmed from a clean commit with adequate
replication (the review suggests 6–8 clean repetitions per cell) before any
inversion is asserted.

## Synthesis

Under single-replica churn, placement leaves a large, reproducible footprint at
the **kernel layer (H2) that never reaches the user (H3)**, while the aggregate
**score is too noisy to see anything at all (H1)**. The operator takeaway is
sharp and counter-intuitive, and is **bounded to this regime**: *for churn faults
on single-replica services in this setup, where you put the pods is not a
user-visible resilience lever — survivability is governed by availability
dynamics (the killed pod is simply gone), not topology.*

A **preliminary** load-contention pilot (H4) suggests the picture may differ when
the user-visible outcome *is* latency rather than availability: there
co-location's network-path locality appeared to lower tail latency in a single
run. That points at **locality** as a candidate through-line across regimes —
co-location keeps inter-service calls node-local — but H4 is a pilot, so this
stays a hypothesis to confirm with a clean, replicated rerun, not a conclusion.

We deliberately **do not** claim a universally best strategy, that "spreading is
never the safer choice", or that the placement literature is refuted. Kubernetes
provides topology spread, anti-affinity, and PodDisruptionBudgets precisely for
availability-sensitive (multi-replica) workloads — a regime this single-replica
design structurally excludes (see Scope & threats). The defensible claim is the
narrow one: *some placement intuitions from contention-focused literature did not
transfer to the single-replica churn regime tested here.*

### Relationship to the literature predictions

*colocate is worst* (L1), *spread isolates best* (L2), *recovery time predicts
resilience* (L3). Under **churn** these are best described as **inapplicable in
this regime** rather than refuted: placement does not move the user-visible
outcome (`pod-delete` is a churn fault, not a contention one), and recovery's
two-phase split is unstable run-to-run, so L3 has no stable relationship to find
on either side. Under **load contention** — the regime the contention literature
is actually about — the H4 pilot *hints* that the L1/L2 ordering may invert, but
that is a preliminary signal from one dirty-provenance run and is held as a
hypothesis pending a clean, replicated rerun.

## Scope & threats

For the short, scannable statement of what the evidence does and does not
support — and what generalizes vs. what does not — see
[Scope of claims](scope-of-claims.md). This section is the detailed version.

- **Fault class & contention:** churn (`pod-delete`) is established across the
  full run set. Contention was probed two ways: resource *hog* faults
  (`pod-cpu-hog`, `node-cpu-hog`, `node-memory-hog`) are absorbed by cgroup
  limits/requests and do not degrade the app; genuine *load* contention (H4)
  does degrade it and *appears* to reorder the strategies in a single
  3-iteration pilot with dirty provenance — but that is a pilot, not a finding,
  and needs a clean, replicated re-run before any reordering is claimed.
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

### Threats to validity (and how they are defended)

| Threat | Why it matters | Defence |
|---|---|---|
| **Single-replica design** | 100 % `pod-delete` guarantees the only instance disappears, which can swamp topology effects. | Scope the claim to *single-replica churn* and layered measurement; multi-replica anti-affinity is named as future work, not claimed. |
| **Small virtualized cluster** | Four 4 GiB KVM/QEMU workers may not generalize. | Claim bounded external validity; report *direction* and *mechanism*, not absolute latency values. |
| **Version sensitivity** | kube-proxy / conntrack behaviour evolves across releases. | Archive exact Kubernetes, CNI, runtime, ChaosProbe, and commit metadata (`runMetadata`); present as a measurement study of a specific environment. |
| **Placement mismatch** | The scheduler may not realize the intended placement. | Report `placementMatchRates`; flag or exclude mismatched iterations. |
| **Run-to-run drift** | Iteration noise can dominate (H1). | Block runs, randomize strategy order, capture pre/post snapshots, model run as a random/blocking effect. |
| **Dirty provenance** | Untracked files / missing metadata undermine credibility (H4). | Never quote results from runs failing `doctor --strict`; rerun H4 cleanly or hold it as a pilot. |
| **Metric-availability gaps** | Missing PromQL queries can manufacture fake zeros. | Use `metricAvailability` to distinguish "not collected" from "collected zero". |
| **Overclaiming causality** | Run-level slowness can confound correlations. | Use dependent vs control routes and within-run correlation; reserve causal language for the manipulated variable (placement). |

### Defensible abstract

> Kubernetes offers multiple placement mechanisms and rich observability, yet it
> remains unclear when pod placement materially affects resilience under chaos
> and when aggregate resilience scores obscure that effect. This thesis presents
> **ChaosProbe**, a Kubernetes chaos-evaluation framework that varies
> pod-placement strategies, injects LitmusChaos faults into the Online Boutique
> microservice benchmark, collects Prometheus, Kubernetes, Locust, and
> application-level metrics, and stores structured experiment data for analysis.
> Using ChaosProbe, we conduct a layered measurement study across aggregate
> scores, mechanism-level signals, and user-visible outcomes. The central finding
> is fault-class-specific: under single-replica `pod-delete` churn, placement
> reproducibly changes kernel/network reconvergence signatures, but these
> differences do not yield a stable user-visible advantage and are poorly
> captured by aggregate resilience scores. A preliminary load-contention pilot
> suggests latency-dominated faults may expose stronger user-visible placement
> effects, motivating a clean follow-up. These results show that placement under
> chaos should not be evaluated with a single score alone: resilience conclusions
> depend on both the fault class and the measurement layer. The thesis
> contributes a reproducible experimental framework, a bounded empirical study,
> and practical guidance for evaluating placement-sensitive resilience claims in
> Kubernetes.

## Label provenance

Earlier drafts used `M1–M4 / S1–S2 / L1–L3`. The mapping: H1 ← M4 (now
quantified); H2 ← M1 (conntrack), with M2 (throttling) demoted to corroboration;
H3 is new and replaces M3's "spread is safer is refuted" overclaim with the
measured decoupling; S2 (recovery split) folds into the L3 note; L1–L3 are
reframed from "refuted" to "inapplicable."
