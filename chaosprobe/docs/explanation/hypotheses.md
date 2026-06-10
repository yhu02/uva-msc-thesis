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
holds across both fault classes. A third fault class, **node failure** (H6),
turns to the **availability** layer the first two never bite: there placement
*does* matter, reproducibly and at the only layer that counts for a node drain —
the blast radius — which is the opposing face of the same co-location metric H5
uses for latency.

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

**Result — supported (clean campaign, primary evidence).** Across the
**7-session E2 campaign** (s01–s07: independent single-commit sessions, all
8 strategies × *i* = 3, 147 churn iterations, every session `doctor --strict`
clean and archived), only **3.3 %** of score variance is between-strategy
(`ICC_strategy = 0.033`, cluster-bootstrap 95 % CI **[0.014, 0.178]**); the
rest is iteration-level (59.1 %) and run-to-run (37.6 %) noise. The focal
`colocate` (64.0) vs `spread` (74.3) gap (*d* = 0.46) would need **73
iterations/strategy** for 80 % power, and at the *n* = 3 actually run per
session the minimum detectable effect is **2.29 sd ≈ 51 score points** —
larger than any gap that exists. The score isn't even stable within a single
run.

The earlier pooled run-set (mixed code versions, 16 runs) read the same way —
`ICC_strategy = 0.046`, focal gap *d* = 0.06 needing ≈ 3,982/strategy — and is
retained only as the pilot; quote the campaign numbers.

```
uv run python scripts/score_variance.py --results-dir campaign-results
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

**Result — supported (clean campaign, primary evidence).** Across the
7-session E2 campaign, `spread` flushes a **38.5 %** median vs `colocate`
**2.7 %**, with `spread > colocate` in **7 / 7** independent sessions —
**sign test *p* = 0.0156, paired Wilcoxon W = 0, *p* = 0.0225**. The earlier
pooled run-set agreed (36.6 % vs 1.9 %, 16/16 runs). This is the most
reproducible signal in the study and maps onto the Kubernetes SIG-Scalability
network-programming reconvergence window documented upstream (see references).

**Mechanism — measured by protocol decomposition (probe, 2026-06-10).** A
dedicated probe (per-node `conntrack -L` protocol counts sampled every 5 s
through one full `pod-delete` kill cycle under each placement; runs
`20260610-195929` spread / `20260610-201052` colocate, archived as
`run-20260610-200013` / `run-20260610-201131`; raw samples + pod spec in
`thesis/data/conntrack-probe/`) decomposes the flush:

| placement | pre-chaos TCP / UDP entries | UDP during kill cycle | TCP during kill cycle |
|---|---|---|---|
| `spread` | 3,857 / **1,822 (32 %)** | **−50 to −58 %** | *grows* (+6 to +16 %) |
| `colocate` | 2,993 / **72 (2 %)** | tiny pool (noise) | *grows* |

The placement-dependent signal is the **UDP (DNS) pool**: spreading the
target's dependents across nodes sustains a ~25× larger standing pool of UDP
conntrack entries (cross-node calls → connection churn → repeated DNS
resolution), and the kill cycle collapses it — consistent with kube-proxy's
*documented, deliberately UDP-only* active conntrack cleanup on endpoint
change (kubernetes/kubernetes #48370, #108523, #126130; ipvs mode on this
cluster). **TCP entries are never flushed and in fact grow during chaos**
(reconnect churn), exactly matching upstream behaviour (#100698, #104098).
The aggregate `conntrack_entries_per_node` drop H2 measures is therefore the
UDP share collapsing, visible only under placements that maintain a large UDP
pool. *Caveat:* the probe is one iteration per placement against the
campaign's 7 sessions — quote it for the composition and direction, not for
magnitudes.

A secondary contention signal (CPU throttling) is *weaker* and should be
reported as corroborating only: `colocate` throttles lowest in 6/7 campaign
sessions (and below `default` in 6/7), but in the pooled pilot `best-fit` was
lower still. Lead with conntrack; treat throttling as support, not a
standalone claim.

```
uv run python scripts/mechanism_metrics.py --results-dir campaign-results
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

**Result — decoupling supported (clean campaign + three pilot tests).**

Campaign (7 sessions, 49 strategy-cells, TOST equivalence testing): conntrack
flush → dependent-route p95 is **ρ = 0.07 (*p* = 0.65)** while the *control*
route is ρ = 0.29 (*p* = 0.043) — the mechanism correlates with the route that
does **not** depend on the killed service, the signature of a run-level
confound, and the dependent-route association is **decoupled by TOST**. The
only dependent-significant association (TCP-retransmit delta, ρ = −0.32) is
*negative* — the direction opposite a propagation story.

The pooled pilot read the same way:

- Pooled: conntrack flush → dependent-route p95 is ρ = 0.15 (*p* = 0.18, n.s.).
- The only mechanism reaching significance (CoreDNS p99) is **stronger on the
  control route** (ρ = 0.54) than the dependent route (ρ = 0.31) — the same
  confound signature.
- Within-run (run effect removed): mean ρ ≈ **+0.10**, median ≈ 0.
- Robust to route classification (folding the homepage into the dependent set,
  using `/_healthz` alone as control: ρ = 0.19, *p* = 0.09).

The per-strategy table needs no statistics: `dependency-aware` has the **worst**
mechanism (conntrack entries grow 20 %) and the **best** dependent-route error
rate (1.4 %); `spread` flushes 9× more conntrack than `colocate` yet they tie on
what the user experiences (8.0 % vs 8.9 % error).

```
uv run python scripts/h3_mechanism_outcome.py --results-dir campaign-results --csv /tmp/h3_pairs.csv
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

## H5 — A graph-derived metric predicts the east-west placement penalty

**Statement.** The east-west (inter-service) tail-latency penalty H4 attributes to a
placement is predictable, *before any chaos*, from a graph metric: the **cross-node
call fraction** — the fraction of the service dependency graph's inter-service edges
whose endpoints sit on different nodes under that placement.

**Operationalization.** All 8 placement strategies × *i* = 4 under a 200-user Locust
spike (`results/20260608-070606`, archived). Per strategy, the cross-node fraction is
computed from the *actual* per-iteration `podPlacements` + the dependency edges in
`routeViewAggregate` (`scripts/cross_node_fraction.py`), and rank-correlated
(Spearman) with the during-load median east-west p95.

**Result — supported, coarsely.** ρ = **0.79** (n = 8, *p* < 0.05; critical ρ ≈ 0.74):

| strategy | cross-node frac | east-west p95 (ms) |
|---|---|---|
| **colocate** | 0.00 | **33.9** |
| **best-fit** | 0.13 | **35.3** |
| dependency-aware | 0.73 | 42.6 |
| spread | 0.73 | 43.5 |
| baseline | 0.70 | 43.5 |
| adversarial | 0.80 | 43.5 |
| default | 0.78 | 45.5 |
| random | 0.80 | 43.9 |

A metric computable from the graph alone predicts the measured tail — the one place
this study makes the Neo4j dependency graph *analytically* load-bearing rather than
mere storage. Two secondary findings:

- **Locality is not unique to `colocate`.** `best-fit` (bin-packing onto few nodes)
  also achieves a low cross-node fraction (0.13) and the second-lowest tail (35.3 ms):
  *any* node-packing placement gets the locality benefit.
- **`dependency-aware` did not deliver.** Its fraction (0.73) is *spread-like*, not
  intermediate — the BFS service-graph partition did **not** co-locate communicating
  services as intended, so its tail (42.6 ms) ≈ `spread` (43.5 ms). The study's most
  distinctive strategy does not beat the naive ones *as implemented*; the partition is
  a candidate for future improvement.

**Caveats (do not overstate this).**

- *Coarse, not a smooth law.* The correlation is carried by the two **node-local**
  placements (colocate, best-fit) sitting below the six **spreading** ones, which
  cluster at 0.70–0.80 fraction / 42–46 ms with no clean trend *within* the cluster.
  The defensible claim is "node-local placements have lower east-west tails and the
  fraction captures that," not a fine continuous predictor.
- *User layer stays weak.* On the user-facing routes `colocate` is only ~1.3× below
  `spread` and barely dependency-specific (control 1.29× ≈ dependent 1.34×) — H5 is an
  east-west **mechanism**-layer result, consistent with the H3/H4 decoupling, not a
  user-visible win.
- *Single batch.* One *i* = 4 run; "reproducible" needs ≥ 2 batches. The aggregate
  score still cannot rank (all 8 scored 100, all CIs overlap — H1).

```
uv run python scripts/cross_node_fraction.py -s results/20260608-070606/summary.json
```

## H6 — Co-location is a latency/availability trade-off: it shrinks east-west tail but enlarges node-failure blast radius

**Statement.** The same co-location that lowers the east-west tail (H5) *raises* the
blast radius and recovery time of a **node failure**. Under a node drain, the number
of services taken offline — and how long they take to recover — is determined by how
many of them the placement concentrated onto the drained node. Node failure is a third
fault class (after churn, H2–H3, and load contention, H4), and the first where this
study measures the **availability** axis directly.

**Operationalization.** `node-drain` on the node hosting `productcatalogservice`
(`TARGET_NODE: auto`, single replica), `colocate` vs `spread`, two `doctor`-clean
batches (*i* = 1 `results/20260608-194746`; *i* = 3 `results/20260608-205147`). The
**blast radius** is the number of services driven to **0 ready endpoints** at the
outage *trough*, read from EndpointSlice snapshots sampled every 15 s through the drain
(`scripts/blast_radius.py`) — *not* the resilience score, which is unusable here (a node
drain leaves every LitmusChaos probe `Unknown`; H1 again). Recovery is the target
deployment's deletion→ready time from the recovery watcher.

**Result — supported and reproduced.** Observed blast radius equals the
placement-predicted blast (services pinned to the drained node) in every iteration:

| placement | services on drained node | **blast radius (observed)** | target recovery (mean) |
|---|---|---|---|
| **colocate** | 11 / 11 | **11 — the whole app offline** | **10.3 s** |
| **spread** | 2 / 11 | **2 (18%)** | **2.6 s** |

- **The trade-off is the finding.** `colocate` is the *best* placement for east-west
  latency (H5: lowest tail, 33.9 ms) and simultaneously the *worst* for node failure
  (a single drain = 100% outage). `spread` is the mirror. One graph property
  (co-location), two opposing consequences — latency vs availability — now both
  measured: H5 is the latency face, H6 the availability face.
- **Recovery time is slower at the extremes contrast, but is *not* a gradient law.**
  In the colocate-vs-spread pair, `colocate` recovers ~**4× slower** (10.3 s vs
  2.6 s): when the node uncordons, its 11 evicted pods contend to reschedule at
  once, where `spread` has only 2. The 6-strategy gradient run (below) shows this
  does **not** generalize monotonically — intermediate-blast placements produced
  both fast (4.6 s) and slow (33 s) recoveries — so the recovery claim is scoped
  to the extremes contrast only.

**Gradient extension (6 strategies, 2026-06-10).** A gradient run — all six
placing strategies × `node-drain` × *i* = 3 (`results/20260610-172352`,
`doctor --strict` clean, archived as `run-20260610-172430`) — confirms blast
radius scales exactly with per-node concentration: **observed blast equals the
placement-predicted blast for every strategy** (colocate 11, random 4,
dependency-aware 3, best-fit 3, spread 2, adversarial 2; Spearman ρ = 1.0,
n = 6). This is the availability analogue of H5's cross-node-fraction
predictor: one graph-derived quantity (per-node concentration) predicts the
availability consequence with rank correlation 1.0, just as the cross-node
fraction predicts the latency consequence with ρ = 0.79.

**Caveats (do not overstate this).**
- *The prediction is near-definitional; the empirical content is that it holds.* "Drain
  a node and you lose the pods on it" is arithmetic. What the experiment adds is that the
  predicted blast **actually materializes** under real chaos (no partial survival from
  the single-replica pin), is **reproducible**, and drives a measurable **recovery-time**
  penalty — and that it is the *opposing* face of the very metric (H5) that makes
  co-location look good.
- *Single-replica, single cluster.* Multi-replica anti-affinity (which would let a
  service survive its node's drain) is out of scope here — see Scope & threats; the
  result is about *between-service* blast radius under deployment-level placement, the
  regime ChaosProbe's nodeSelector mutator realises.

```
uv run python scripts/blast_radius.py -s results/20260610-172352/summary.json   # gradient run
uv run python scripts/blast_radius.py -s results/20260608-205147/summary.json   # original two-point batch
```

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
| **Run-to-run drift** | Iteration noise can dominate (H1). | Block runs (strategy order is fixed within every session, so order effects are constant across sessions rather than randomized), capture pre/post snapshots, model run as a random/blocking effect. |
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
