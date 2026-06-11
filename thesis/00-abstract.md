# Abstract

<!-- Adapted from the "Defensible abstract" in
chaosprobe/docs/explanation/hypotheses.md, extended with the campaign-scale
evidence (H1–H3), H5, and H6. Wording is governed by scope-of-claims.md. -->

Kubernetes offers multiple placement mechanisms and rich observability, yet it
remains unclear when pod placement materially affects resilience under chaos
and when aggregate resilience scores obscure that effect. This thesis presents
**ChaosProbe**, a Kubernetes chaos-evaluation framework that varies
pod-placement strategies, injects LitmusChaos faults into the Online Boutique
microservice benchmark, collects Prometheus, Kubernetes, Locust, and
application-level metrics, and stores structured, provenance-stamped
experiment data for analysis.

Using ChaosProbe, we conduct a layered measurement study across aggregate
scores, mechanism-level signals, and user-visible outcomes, with a
**seven-session campaign** of independent single-commit runs (8 strategies ×
3 iterations per session; 147 churn iterations, every session
provenance-gated) as the primary evidence. The central finding is
fault-class-specific. Under single-replica `pod-delete` churn, placement
reproducibly changes kernel/network reconvergence signatures (conntrack flush:
spread 38.5% vs colocate 2.7%, spread > colocate in 7/7 sessions), but these
differences do not yield a stable user-visible advantage, and the aggregate
resilience score cannot rank placement strategies under session variance
(between-strategy variance 3.3% of total; ICC 95% CI [0.014, 0.178]). A second
fault class, load contention (two replicated *i* = 4 batches), reinforces this
layered picture: placement reproducibly moves the east-west inter-service
mechanism — co-located services keep calls node-local and show 1.36–1.39×
lower inter-service tail latency than spread — but the user-visible effect
does not survive replication, so no user-visible placement advantage is
claimed under load.

Two further results turn this decoupling into actionable structure. First, a
**graph-derived metric** — the cross-node fraction of the service dependency
graph's inter-service edges under a placement — separates node-local from
spreading placements *before any chaos is injected*, and that separation
predicts the east-west tail penalty in **two independent batches** (the two
node-local placements show the two lowest tails of eight both times, ~1.25×
below the spreading cluster; a continuous correlation did not replicate and
is not claimed). A protocol-composition probe further grounds the churn
mechanism: TCP entries dominate the conntrack table and drop sharply at the
kill cycles under both placements (kernel-side teardown), while the clearly
placement-dependent component is the UDP/DNS entry pool — ~4× larger under
spread, the traffic class kube-proxy's documented UDP-only cleanup acts on.
Second, under a
third fault class, **node failure**, the same co-location that minimizes
east-west latency maximizes blast radius: draining the node hosting the
target took all 11 services offline under `colocate` (recovery ≈ 10.3 s)
versus 2 of 11 under `spread` (≈ 2.6 s), reproduced across two
provenance-clean batches — and a six-strategy gradient run shows observed
blast equals placement-predicted blast for every strategy (Spearman ρ = 1.0).
Together these quantify a latency↔availability trade-off along a single
measurable property of the placement.

These results show that placement under chaos should not be evaluated with a
single score alone: resilience conclusions depend on both the fault class and
the measurement layer. The thesis contributes a reproducible experimental
framework, a bounded empirical study, and practical guidance for evaluating
placement-sensitive resilience claims in Kubernetes.

<!-- TODO(author): trim to the university's abstract word limit if required;
keep the three beats — framework, layered decoupling across fault classes,
the measured trade-off (H5+H6). -->
