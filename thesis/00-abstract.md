# Abstract

<!-- Adapted from the "Defensible abstract" in
chaosprobe/docs/explanation/hypotheses.md. Wording is governed by
scope-of-claims.md. One anchoring number per fault-class beat; the full
quantitative detail lives in Chapter 5. -->

Kubernetes offers multiple placement mechanisms and rich observability, yet
it remains unclear when pod placement materially affects resilience under
chaos, and when aggregate resilience scores obscure that effect. This thesis
presents **ChaosProbe**, a Kubernetes chaos-evaluation framework that varies
pod-placement strategies, injects LitmusChaos faults into the Online Boutique
microservice benchmark, and stores provenance-stamped experiment
data. Measurement is layered: every outcome is read simultaneously at the
aggregate-score, kernel/network-mechanism, and user-visible layers. Using
ChaosProbe, we conduct a bounded empirical study across three fault classes —
churn, load contention, and node failure. The primary evidence is a
seven-session campaign of independent, provenance-gated runs, plus
replicated load and node-drain batches.

The central finding is a measured latency↔availability trade-off: the same
co-location that minimizes inter-service latency under load maximizes blast
radius under node failure — and a single aggregate score sees none of it.
Beneath that trade-off sits a layered decoupling that recurs across fault
classes: placement reproducibly moves mechanism-layer signals that do not
reproducibly reach the user. Under churn, spreading the target's dependents
flushes far more kernel conntrack state than co-locating them, in 7 of 7
independent sessions — yet no stable user-visible advantage follows. The
aggregate score cannot rank placement strategies under session variance
(ICC 0.033). Under load, co-located services keep calls node-local and
hold 1.36–1.39× lower inter-service tail latency than spread across two
replicated batches; the user-visible effect did not survive replication and
is not claimed. A graph-derived cross-node fraction separates node-local from
spreading placements before any chaos is injected, and that separation
replicates; a continuous correlation did not and is not claimed. Under node
failure, draining the node hosting the target took 11 of 11 services offline
under colocate versus 2 of 11 under spread, and observed blast equaled
placement-predicted blast for every strategy.

These results show that placement under chaos should not be evaluated with a
single score alone: resilience conclusions depend on both the fault class and
the measurement layer. The thesis contributes a reproducible experimental
framework, a bounded empirical study whose every quoted number traces to an
archived run, and practical guidance for evaluating placement-sensitive
resilience claims in Kubernetes.
