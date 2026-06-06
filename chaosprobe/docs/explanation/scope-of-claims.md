# Scope of claims

An explicit statement of what the ChaosProbe study *claims*, what it
deliberately *does not*, and where the results generalize. It exists so a
reader (or examiner) never has to infer the boundary of a claim from the prose
around it. It consolidates the scoping discipline applied across
[hypotheses & findings](hypotheses.md) and the
[reproduction guide](../how-to/reproducing-thesis-results.md), and incorporates
the MSc thesis advisory review.

> The numbers behind every claim live in [hypotheses.md](hypotheses.md) (H1–H4),
> each tied to a committed reproduction script. This page states the *scope*, not
> the magnitudes — quote figures from there, gated by `doctor --strict`.

## What we claim (and how it is bounded)

Each claim is bounded to **single-replica `pod-delete` churn on the Online
Boutique benchmark, on a small virtualized cluster**, unless stated otherwise.

| Claim | Bound |
|---|---|
| ChaosProbe is a placement-aware chaos-evaluation framework: controlled placement mutation, LitmusChaos fault injection, cross-layer metric collection, statistical comparison, and topology-aware storage. | An artifact claim — holds independent of the experimental regime. |
| The aggregate resilience score is too noisy to rank placements at feasible iteration counts in this campaign (H1). | This campaign / this cluster / this score definition. Stated as score *instability*, not as a property of all resilience scores. |
| Placement reproducibly moves a kernel/network reconvergence signature under churn — `spread` flushes conntrack, `colocate` does not (H2). | The *direction* of the effect is the claim; absolute magnitudes are environment-specific and not portable. |
| That mechanism is decoupled from the user-visible outcome in this regime (H3). | Single-replica churn, where availability dominates topology. Decoupling is a measured result here, not a general law. |
| Mechanism-level metrics are more reproducible than the aggregate score here. | This campaign; offered as a measurement-layer observation. |

## What we do NOT claim

- **No universal "best strategy."** No placement is asserted to be best across
  faults and workloads.
- **Not "spread is worse" / "spread is never safer."** Kubernetes provides
  topology spread, anti-affinity, and PodDisruptionBudgets precisely for
  availability-sensitive (multi-replica) workloads — a regime this
  single-replica design structurally excludes.
- **The placement literature is not "refuted."** Under churn the contention-era
  predictions (colocate worst, spread isolates best, recovery predicts
  resilience) are best described as **inapplicable in this regime**, not
  disproven.
- **No causal claim beyond the manipulated variable (placement).** The
  conntrack mechanism is *consistent with* documented Kubernetes
  service-networking behaviour; we claim mechanistic consistency, not proven
  causation, and use dependent-vs-control routes plus within-run correlation to
  guard against run-level confounds.
- **No claim that any single number "reproduces"** except the specific mechanism
  metrics (H2) for which a rerun package exists.

## Where the results generalize — and where they stop

| Generalizes | Stops at |
|---|---|
| The *direction* of the H2 churn/reconvergence mechanism (spread flushes more per-node connection-tracking state than colocate during the kill cycle). | Absolute latency / conntrack / score *values* — tied to the 5-node, 10-vCPU KVM cluster. |
| The methodological point: an aggregate score and a mechanism metric can disagree, so placement-resilience claims must name the fault class **and** the measurement layer. | Multi-replica anti-affinity (do replicas share a failure domain?) — structurally excluded by single-replica `pod-delete`. |
| The framework and analysis pipeline (reusable on any cluster/workload). | Other CNIs, kube-proxy modes, kernel/conntrack settings, and Kubernetes versions — recorded in `runMetadata`, claims presented as environment-contingent. |

## Claim discipline (keep / weaken / remove)

Applied across the thesis prose, docs, and presentation materials (per the
advisory review):

| Status | Wording |
|---|---|
| **Keep** | "Placement-aware chaos-evaluation framework with controlled placement mutation, cross-layer measurement, and statistical tooling." |
| **Keep** | "Aggregate resilience scores were unstable in this churn campaign and should not rank strategies alone." |
| **Keep** | "Mechanism metrics can be more reproducible than aggregate scores." |
| **Weaken** | "pod-delete *is* a churn fault" → "pod-delete behaved consistently with a churn/reconvergence mechanism in this setup." |
| **Weaken** | "spread is safer is refuted" → "the usual spread intuition did not transfer cleanly to this fault class in this setup." |
| **Weaken** | "recovery time predicts nothing" → "recovery-time decomposition was unstable and did not provide a stable ranking signal here." |
| **Remove** | Any universal "best strategy" claim across all placements and faults. |
| **Remove** | Any central claim resting on `pod-cpu-hog` until rerun cleanly with verified labels/limits (now held as the H4 pilot). |

## Open research-methodology recommendations (surfaced, not shipped)

The advisory also recommends changes to the **experimental methodology** and
**system under test**. Those are research-validity decisions for the author, so
they are recorded here rather than silently applied in code:

- **Trade breadth for depth in the headline analysis** — run the *core* claim
  set on `default`/`colocate`/`spread` only, with ≥ 8 (target 10) valid
  iterations per strategy per fault. *Status: open.* Note this is about the
  **headline** matrix; the exploratory run set deliberately exercises all eight
  strategies for generality coverage, so reducing the headline matrix does not
  mean dropping strategies from exploratory runs.
- **Add `node-memory-hog` as the contention case**, with an intensity pilot
  sweep before the main campaign — node-scoped memory pressure acts where
  placement matters (kubelet eviction, OOM, node pressure), unlike CPU-hog
  faults that cgroup limits absorb. *Status: scenario present
  (`scenarios/online-boutique/node-memory-hog.yaml`); campaign not yet run.*
- **Strengthen the analysis to a strategy × fault interaction model**
  (aligned-rank or mixed-effects), complementing the existing pairwise
  Mann-Whitney + Holm + Cliff's delta + bootstrap CIs. *Status: open.*
- **One destination-scoped network latency/loss experiment** and **one limited
  multi-replica variant** as ambitious extensions. *Status: future work.*

See [hypotheses.md](hypotheses.md) for the falsifiable statements and the
[reproduction guide](../how-to/reproducing-thesis-results.md) for the exact
spec and the provenance bar every quoted run must clear.
