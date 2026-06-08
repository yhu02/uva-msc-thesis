# Proposed experiments (research roadmap)

The validated results so far ([Hypotheses & findings](hypotheses.md), H1–H4) are a
careful but largely *negative / decoupling* story: under single-replica churn and
under load contention, placement moves a **mechanism** layer but not, reproducibly,
the **user** layer, and the aggregate score cannot rank. A complete thesis wants at
least one **positive, surprising, defensible** result. This page proposes the next
experiments that ChaosProbe can run to get there.

These are **proposals, not findings** — none have been run as a clean campaign yet.
They are labelled **P1–P4** to avoid colliding with the validated **H1–H4** in
`hypotheses.md`. Each is reconciled with what we have already *verified* this study
(so we don't re-propose something the data already rules out — see
[What we deliberately do not propose](#what-we-deliberately-do-not-propose)).

---

## P1 — Multi-replica + node-level fault: placement becomes a *user-visible* lever (the missing positive result)

**Why this is the priority.** The whole study is single-replica, which
*structurally excludes* the one thing pod topology-spread / anti-affinity exists for:
surviving the loss of a failure domain. 100% `pod-delete` on a single replica
guarantees a full outage, so placement *can't* help — which is exactly why H1–H4 see
decoupling. Multi-replica + a node-level fault is the regime where placement *should*
reach the user, and it is untested.

**Hypothesis (falsifiable).** With ≥3 replicas per service, under a **node-level**
fault (a worker `node-drain`/failure, or genuine node memory exhaustion), `spread`
(anti-affinity — replicas on distinct nodes) keeps the service available while
`colocate`/`best-fit` (replicas packed onto one node) suffer a full per-service
outage. Prediction: `spread` median user-facing availability/error-rate **>**
`colocate`, with a large effect size, where single-replica showed no difference.

- **IV:** placement (`spread` vs `colocate`/`best-fit`), and replica count (1 vs 3)
  as a second factor.
- **DV (primary, user layer):** route availability / Locust error-rate during the
  fault; recovery time (deletion→ready). **DV (mechanism):** pods lost per node,
  reschedule latency, EndpointSlice ready-count trajectory.
- **Controls:** fixed cluster, load profile, target service, fault duration; same
  scenario hash + batch/day blocking.
- **Mechanism:** colocate puts all N replicas of a service in one failure domain →
  node loss = service loss; spread bounds the loss to one replica.
- **Stats:** blocked design, batch/day as block, placement fixed effect;
  Holm-corrected Mann-Whitney + Cliff's δ; for the binary "service down during fault",
  exact/binomial CIs. ≥8 valid iterations × ≥2 batches.
- **Threats:** node-drain grace periods and PodDisruptionBudgets confound recovery
  timing; the cluster must have enough headroom to reschedule (else *both* strategies
  fail → null). Report eviction config + worker headroom.
- **Why interesting:** it turns the thesis from "placement doesn't matter (single
  replica)" into "placement matters at a *precise boundary* — multi-replica × node-
  level fault — and is invisible outside it." That **boundary** (fault-class ×
  **replication-degree** × measurement-layer) is a sharper contribution than
  fault-class × layer alone, and it is the *expected* result whose **absence** would
  signal a bug.
- **Feasibility:** node-drain is installed; needs (a) a `node-drain.yaml` scenario
  (mirror `node-cpu-hog.yaml`, `TARGET_NODES: auto`) and (b) a multi-replica
  capability — a `--replicas N` flag on `run`, or per-service `replicas` in the
  `deploy/` manifests. **Signal: strong. Risk: low.**

> **Folds in the memory angle.** Memory pressure only becomes *placement-sensitive*
> in this multi-replica node-failure regime — see the reconciliation note below.

---

## P2 — Path-scoped network latency × dependency-aware placement: the cleanest shot at a user-visible locality effect

**Hypothesis (falsifiable).** When latency is injected on traffic to a *central
dependency* (e.g. `productcatalogservice`), placements that keep that call **node-local**
— `colocate` and especially the never-evaluated **`dependency-aware`** strategy
(BFS-partition of the service graph) — show a reproducibly lower dependent-route p95
than `spread`/`default`, which route the call across the wire. Prediction:
`dependency-aware` p95 on product routes **<** `spread`, exceeding the `/_healthz`
control gap.

- **IV:** placement, focusing on `dependency-aware` vs `default`/`spread`.
- **DV:** dependent-route p95/p99 + error-rate; the **cross-node call fraction**
  (proposed metric below) as the mechanistic predictor.
- **Controls:** **verify the fault actually differentiates local vs non-local
  traffic** (a packet/path check) — this is the real risk; if the pod-level fault
  degrades all of the target's traffic regardless of hop, the placement signal
  collapses (cf. the advisory's network caveat).
- **Mechanism:** locality — fewer cross-node hops on the degraded path → lower tail.
- **Stats:** Holm-corrected Mann-Whitney on per-route p95 across runs; dependent-vs-
  control routes as the confound check (same as H3). ≥8 iters × ≥2 batches.
- **Why interesting:** it both (a) gives the user-visible locality effect that the
  hogs and aggregate load could not, and (b) finally **evaluates `dependency-aware`**,
  the one placement strategy that is *yours* — making the Neo4j dependency graph
  analytically load-bearing rather than mere storage.
- **Feasibility:** `contention-interservice-latency.yaml` (pod-network-latency on
  productcatalog) already exists — run-ready; path-scoping is the work. **Signal:
  high if path-scoped. Risk: medium** (easiest fault to get subtly wrong).

---

## P3 — Node-drain spread-isolation: the baseline that pairs with P1

**Hypothesis.** Under a single `node-drain`, `spread`/`default` lose at most one pod
per service and recover quickly, while `colocate` loses everything on the drained
node. Prediction: `spread` availability/recovery **>** `colocate`.

This is *expected* (it matches Kubernetes best practice), so it is a **sanity check /
positive control**, not a novel claim — but it validates the pipeline end-to-end on a
node-level fault and is the single-replica companion to P1. A surprising *failure* to
show it would indicate an instrumentation bug worth chasing.

- **Feasibility:** needs the same `node-drain.yaml` as P1. **Signal: strong (expected).
  Risk: low.** Report it as confirming the method, contrasted with the churn result.

---

## Proposed new metric — cross-node call fraction (cheap, predictive, novel)

A graph-derived metric computable **before any chaos**, from the service dependency
graph (`config/topology.py`) + the actual pod→node placement
(`placement/mutator.observe_pod_placements`): the **fraction of inter-service call
volume that crosses nodes** under each strategy. It is the natural *explanator* for
the east-west tail effect H4 already measured (placement → cross-node fraction →
east-west p95), and it makes the dependency graph load-bearing.

- **Compute:** for each `DEPENDS_ON` edge, is `src` co-located with `dst`? Aggregate
  (optionally weighted by call volume) into a 0–1 fraction per strategy.
- **Effort:** analysis-script only (like `scripts/contention_routes.py`); **zero
  cluster cost** — it can be validated *today* against the two existing
  load-contention runs (does the fraction rank-correlate with the measured east-west
  p95?).
- **Unlocks:** P2 (mechanistic predictor) and a standalone claim — "a placement's
  east-west penalty is predictable from a graph metric without running chaos."

---

## What we deliberately do not propose

- **A single-replica memory-hog "spread beats colocate" experiment.** This is the one
  reconciliation point with an externally-suggested plan: we **verified** this study
  that `node-memory-hog` *cannot induce node pressure* on this cluster — its stress
  helper is the kubelet's first eviction victim and self-evicts before app pods are
  touched (checked against the `litmus-go` source + LitmusChaos
  [#3397](https://github.com/litmuschaos/litmus/issues/3397); a 100% probe showed zero
  MemoryPressure/OOM/eviction). And `pod-memory-hog` OOM-kills the *target pod*
  regardless of where it sits — a pod-scoped, **placement-insensitive** fault, like
  churn. So "spread beats colocate under a memory hog" is expected to **null** at
  single-replica. Memory becomes placement-sensitive only via **multi-replica +
  node-level exhaustion**, which is folded into **P1** — not a standalone fault.
- **More synthetic CPU/IO hogs expecting a placement effect.** `pod-cpu-hog` is
  CFS-capped at the container limit (scores 100, no user impact); its CPU-throttling
  signal (M2) is *corroborating only*, not a headline (see
  [scope-of-claims](scope-of-claims.md)).

---

## Priority

Run **P1** (multi-replica × node-fault) for the positive headline + the boundary
result; build the **cross-node call fraction** metric now and validate it against the
existing load data (a new claim for free); then **P2** (path-scoped network ×
dependency-aware) as the ambitious user-visible-locality shot, with **P3** as its
node-level positive control. Together they move the thesis from "a careful negative
result" to "placement matters at a precise, *predictable* boundary."
