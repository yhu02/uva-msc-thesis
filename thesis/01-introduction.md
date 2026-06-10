# 1. Introduction

## 1.1 Problem

The placement literature says **where pods land matters**: locality lowers
inter-service latency (NetMARKS, TraDE), interference makes co-location risky
(Bubble-Up, Quasar), and schedulers from Borg to Medea encode spread- and
packing-heuristics precisely because topology is assumed to drive performance
and availability. Chaos-engineering practice, meanwhile, evaluates resilience
by injecting faults and **collapsing the outcome into an aggregate score** —
a single number per experiment (cf. the CSUR multi-vocal review: outcomes are
defined solely against steady-state user-visible indicators).

These two bodies of practice have not been connected: nobody has measured *at
which layer* a placement effect appears under a given fault class, and whether
an aggregate score can see it at all. If placement moves a kernel-level
mechanism but not the user-visible outcome — or moves availability but not
latency — a single score is the wrong instrument, and placement advice derived
from it is unreliable.

TODO(author): expand — concrete motivating scenario (operator choosing
between packing and spreading a microservice app; what tooling tells them
today), and the cost of getting it wrong on each axis.

## 1.2 Research question

<!-- Verbatim from chaosprobe/docs/explanation/hypotheses.md — do not reword. -->

> **Under which chaos fault classes does pod placement measurably affect
> mechanism-level behaviour and user-visible outcomes in a Kubernetes
> microservice application, and when do aggregate resilience scores obscure
> those effects?**

This is framed as a **fault-class-by-measurement-layer** study, not a
placement ranking and not a refutation of the placement literature. A
placement effect can appear at (a) the aggregate-score layer, (b) a
kernel/network mechanism layer, and/or (c) the user-visible layer, and these
layers need not agree — so the contribution is establishing *at which layer*
an effect appears under a given fault class, and whether it reaches the user.

TODO(author): derive 3–4 explicit sub-questions (one per fault class + one
for score reliability) from the main question.

## 1.3 Contributions

The thesis makes four explicit claims, in decreasing order of novelty:

1. **Novel empirical contribution.** A *layered decoupling* result that holds
   across two fault classes — under both single-replica churn and load
   contention, placement reproducibly moves a mechanism-layer signal
   (conntrack flush under churn, H2; east-west inter-service tail under load,
   H4) that does **not** reproducibly reach the user-visible layer (H3, H4) —
   together with a *measured latency↔availability trade-off pair* (H5 + H6:
   the same co-location property that minimizes east-west tail latency
   maximizes node-failure blast radius and recovery time) and a
   *score-reliability critique* (H1: the aggregate resilience score cannot
   rank placement strategies under session variance; ICC 0.033, between-
   strategy variance 3.3% of total).

2. **Engineering contribution.** **ChaosProbe**: a placement-aware
   chaos-evaluation framework — a placement mutator with eight strategies, a
   LitmusChaos experiment runner, cross-layer probers (Prometheus mechanism
   metrics, route latency, Redis, disk, resources, EndpointSlice snapshots),
   Locust load generation, and a Neo4j dependency/topology graph that H5 makes
   **analytically load-bearing** (the cross-node fraction is computed from the
   graph, not merely stored in it).

3. **Replication/validation contribution.** A *provenance-gated multi-session
   campaign protocol*: independent single-commit sessions as the unit of
   analysis, every session gated by `doctor --strict` (scenario hashes,
   kube-proxy mode, environment fingerprint), every quoted number traceable to
   an archived run (Appendix A).

4. **Pilot/appendix contribution.** Documented negative findings — CPU/memory
   hog faults absorbed by cgroup limits (scores ≈ 100), the `node-memory-hog`
   self-eviction autopsy — and the discussion-tier H7 thread (target-scoped
   cross-node fraction as a flush predictor).

TODO(author): one paragraph per claim connecting it to the chapters that
substantiate it (claim 1 → ch. 5–6; claim 2 → ch. 3; claim 3 → ch. 4 +
Appendix A; claim 4 → Appendix B).

## 1.4 Thesis outline

TODO(author): one sentence per chapter (see `thesis/README.md` chapter map).
