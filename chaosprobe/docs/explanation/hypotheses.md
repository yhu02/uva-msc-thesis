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
the user layer (H1–H3). A second fault class, load contention (H4, two *i* = 4
batches), tests whether a different regime lets the effect reach the user: it
does not. Placement reproducibly moves the inter-service mechanism there too, but
the user-layer effect does not survive replication — so the layered decoupling
holds across both fault classes.

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

## H4 — Under load contention, placement moves the mechanism, not (reproducibly) the user

> **Status: the mechanism-layer effect replicates; the user-layer effect does
> not.** The original dirty 3-iteration pilot was replaced by two *i* = 4 batches
> (one with fully clean, `doctor`-gated provenance). The east-west inter-service
> locality reproduces across both; the user-facing magnitude does **not**, so no
> user-visible placement effect is claimed under load.

**Statement (pilot hypothesis).** When the cluster is driven into *genuine*
resource contention by **load** (not an artificial hog), co-located/dense
placements may outperform spread: co-location would give the lowest
inter-service tail latency, spread the highest.

**Operationalization.** `default`, `colocate`, `spread` (+ `baseline` control)
× *i* = 4 under a sustained 200-user Locust spike (`--load-profile spike`), with a
near-no-op `cpu-hog` placeholder so *load* is the stressor. The metric is
during-load route tail latency (p95), read from the canonical `routeViewAggregate`
via `scripts/contention_routes.py` — not the resilience score (H1: too noisy, and
uniformly degraded under load). Two batches: `results/20260607-193021` (A) and
`results/20260607-221744` (B, clean provenance, 0 taints).

**Why a hog won't do.** `pod-cpu-hog` is CFS-capped at the 200m container limit;
`node-cpu-hog` loads the node but CPU *requests* keep the light pods responsive
(both scored 100 with the app fully up). Contention only bites when the app is
actually resource-bound — i.e. under load.

**Result — the mechanism reproduces, the user layer does not.**

*Reproducible — east-west inter-service tail.* Colocate's inter-service p95 sits
consistently below spread's: the median spread/colocate ratio across the 11
east-west routes is **1.39× (batch A)** and **1.36× (batch B)** — direction and
magnitude agree. Co-location keeps inter-service calls node-local; spread routes
every call across the network, the bottleneck under load.

*Not reproducible — user-facing routes.* The during-load p95 ratio (spread /
colocate) on the user-facing routes swings sharply between the two batches:

| route | batch A | batch B (clean) |
|---|---|---|
| `/` (homepage) | 2.36× | 1.05× |
| `/product` | 2.42× | 1.40× |
| `/cart` | 2.09× | 1.08× |
| `/_healthz` (control) | 1.93× | 1.26× |
| dependent vs control | dependent **>** control | dependent **≈** control |

Batch A read as a strong, *dependency-specific* user-layer effect (dependent
routes degrade more than the control); batch B — the clean-provenance batch —
shows it largely collapsing, with **no** dependency specificity (dependent 1.23×
≈ control 1.26×). The original dirty pilot's "co-location is ~3× better at the
user layer" reading (`results/20260606-092037`) **did not survive replication**;
the swing tracks host load at run time, not placement.

**Conclusion.** Under load contention, placement **reproducibly moves a
mechanism-layer signal (east-west inter-service tail, colocate ~1.3–1.4× faster)
but does not reproducibly move the user-visible outcome.** This *matches* rather
than contrasts with the churn result (H2/H3): in both fault classes placement
perturbs a mechanism that does not reliably reach the user, and the aggregate
score cannot rank (H1). The unified takeaway is a **layered decoupling that holds
across both fault classes tested** — not a regime where load "reaches the user."

## Synthesis

Under single-replica churn, placement leaves a large, reproducible footprint at
the **kernel layer (H2) that never reaches the user (H3)**, while the aggregate
**score is too noisy to see anything at all (H1)**. The operator takeaway is
sharp and counter-intuitive, and is **bounded to this regime**: *for churn faults
on single-replica services in this setup, where you put the pods is not a
user-visible resilience lever — survivability is governed by availability
dynamics (the killed pod is simply gone), not topology.*

Load contention (H4) was expected to differ — there the user-visible outcome *is*
latency, not availability. Across two *i* = 4 batches, co-location does
reproducibly lower the **east-west inter-service** tail (~1.3–1.4× vs spread:
**locality** is the through-line across regimes), but that mechanism effect does
**not** reproducibly reach the **user-facing** routes — a strong user-layer
reading in one batch collapsed in the clean replication. So load contention
*reinforces* the decoupling rather than overturning it: placement moves the
mechanism in both regimes; the user layer follows in neither.

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
is actually about — replication (H4, two batches) shows co-location *does* lower
the inter-service tail (consistent with the locality intuition behind L1/L2 at
the mechanism layer), but this does **not** translate into a reproducible
user-visible ordering, so no L1/L2 inversion is asserted at the user layer.

## Scope & threats

For the short, scannable statement of what the evidence does and does not
support — and what generalizes vs. what does not — see
[Scope of claims](scope-of-claims.md). This section is the detailed version.

- **Fault class & contention:** churn (`pod-delete`) is established across the
  full run set. Contention was probed two ways: resource *hog* faults
  (`pod-cpu-hog`, `node-cpu-hog`, `node-memory-hog`) are absorbed by cgroup
  limits/requests and do not degrade the app; genuine *load* contention (H4)
  does degrade it, and across two replicated *i* = 4 batches placement
  reproducibly moves the east-west inter-service mechanism (colocate ~1.3–1.4×
  lower inter-service tail) but does **not** reproducibly reach the user layer —
  so no user-visible reordering is claimed under load.
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
| **Dirty provenance** | Untracked files / missing metadata undermine credibility (H4). | Never quote results from runs failing `doctor --strict`; the original dirty H4 pilot was replaced by two `doctor`-gated *i* = 4 batches. |
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
> captured by aggregate resilience scores. A second fault class, load
> contention (two replicated *i* = 4 batches), reinforces this layered picture:
> placement reproducibly moves the east-west inter-service mechanism — co-located
> services keep calls node-local and show lower inter-service tail latency — but
> the user-visible effect does not survive replication, so no user-visible
> placement advantage is claimed under load. These results show that placement under
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
