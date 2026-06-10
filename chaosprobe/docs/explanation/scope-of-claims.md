# Scope of claims

What ChaosProbe's evidence **does** and **does not** support, in one place. The
detailed, falsifiable hypotheses live in [Hypotheses & findings](hypotheses.md)
and the full threats-to-validity table is there too; this page is the short,
scannable statement of scope — read it before quoting any result.

The study is framed as a **fault-class × measurement-layer** measurement study
backed by a systems artifact, **not** a placement ranking and **not** a
refutation of the placement literature. A placement effect can appear at the
aggregate-score layer, a kernel/network mechanism layer, and/or the user-visible
layer, and these layers need not agree — so the contribution is establishing *at
which layer* an effect appears under a given fault class, and whether it reaches
the user.

## What the evidence supports (keep)

- **ChaosProbe is a placement-aware chaos-evaluation framework** — controlled
  placement mutation, LitmusChaos fault injection, cross-layer metric
  collection, statistical comparison, Neo4j topology storage, provenance
  capture, and data-quality diagnostics.
- **The aggregate resilience score is too unstable to rank placements** in the
  single-replica `pod-delete` churn campaign — across 7 independent
  single-commit sessions (147 iterations) between-strategy variance is 3.3 % of
  total score variance (ICC CI [0.014, 0.178]), undetectable at any feasible
  iteration count (H1). The score should not be used alone to rank strategies.
- **Mechanism-level metrics can be more reproducible than the aggregate score** —
  under churn, spreading the target's dependents reproducibly moves a
  kernel/network reconvergence signature (conntrack flush, spread > colocate in
  7/7 independent sessions, sign test p = 0.016) that the score does not
  capture (H2).
- **Mechanism and user-visible layers can decouple** — a placement effect at the
  mechanism layer did not, in this single-replica regime, reach the user (H3).
- **A graph-derived metric predicts the east-west placement penalty** — across 8
  placements, the cross-node call fraction (computable from the dependency graph +
  placement, before any chaos) rank-correlates with the during-load east-west tail
  (ρ = 0.79, n = 8). Coarse (it mainly separates node-local placements from spreading
  ones) and single-batch, but it makes the dependency graph analytically load-bearing
  (H5).
- **Co-location is a measured latency/availability trade-off** — the same
  co-location that lowers the east-west tail (H5) raises node-failure blast radius
  and recovery time: under a node drain, `colocate` lost all 11 services (100%
  outage, ~10.3 s target recovery) vs `spread`'s 2 of 11 (~2.6 s), reproduced
  across two doctor-clean batches and measured from EndpointSlice outage troughs,
  not the score (H6). Two-point contrast (the extremes), single-replica — the
  quantification of a known qualitative trade-off, not its discovery.

## What is bounded or preliminary (weaken — never state flatly)

- *"`pod-delete` is a churn fault"* → *"`pod-delete` behaved consistently with a
  churn/reconvergence mechanism in this setup."*
- *"the spread-is-safer intuition is refuted"* → *"the usual spread intuition did
  not transfer cleanly to this fault class in this setup."*
- *"recovery time predicts nothing"* → *"recovery-time decomposition was unstable
  and did not provide a stable ranking signal here."*
- **A user-visible placement effect under load contention (H4)** is *not* claimed.
  Across two *i* = 4 batches the east-west inter-service locality reproduces
  (colocate ~1.3–1.4× lower tail than spread), but the user-facing effect did not
  survive replication — a ~2× dependency-specific reading in one batch collapsed to
  ~1.1× with no dependency specificity in the clean batch. State only the mechanism
  effect; the user-layer effect is run-dependent, not a finding.

## What is explicitly not claimed (remove)

- ❌ A universal "best" placement strategy across faults and clusters.
- ❌ "Spread is worse" / "spread is disproven."
- ❌ Proven causality for the conntrack/reconvergence mechanism (the claim is
  *mechanistic consistency* supported by Kubernetes networking semantics and
  layered measurement, not a controlled causal proof).
- ❌ Generalization to Kubernetes clusters broadly.
- ❌ "The resilience score identifies the best strategy."
- ❌ "The result is reproducible" — unqualified; only the **mechanism metrics**
  reproduce, and only with the archived rerun package.

## What generalizes vs. what does not

| Aspect | Generalizes? | Why / caveat |
|---|---|---|
| The *method* (placement-aware, cross-layer, provenance-gated chaos evaluation) | **Yes** | The framework and analysis discipline are reusable on any cluster/workload. |
| The *direction* of the H2 mechanism effect (spread flushes more conntrack than colocate under churn) | **Direction only** | Reproducible here; absolute values are environment-specific. |
| Absolute metric values (latency, recovery ms, flush %) | **No** | Tied to a small virtualized 5-node / 10-vCPU cluster. |
| Mechanism behaviour (conntrack, kube-proxy sync) | **Environment-contingent** | Depends on CNI, kube-proxy mode, kernel/conntrack settings — archived in `runMetadata` so the scope is explicit. |
| The score-instability finding | **This regime** | Established for single-replica `pod-delete`; not asserted for multi-replica or contention-dominated regimes. |
| Anything about multi-replica / HA failover | **Not in scope** | The single-replica design structurally excludes it (see below). |

## Where the claims stop (future work)

The single-replica design means 100% `pod-delete` guarantees a full outage, so the
outcome is dominated by availability rather than topology — the production-relevant
multi-replica anti-affinity question is **structurally excluded**, not answered.
Larger clusters, multi-replica services, other CNIs / kube-proxy modes, production
traffic, scheduler integration, and additional load-contention batches to settle
whether any user-layer effect survives replication (the east-west mechanism already
reproduces) are all future work, not claims.

## Before quoting any number

Every figure quoted as a *finding* must come from an archived, clean-provenance run
that passes `doctor --strict` (which now also gates kube-proxy mode, scenario
hashes, and the rest of the provenance fingerprint). See
[Reproduce the thesis results](../how-to/reproducing-thesis-results.md) for the
archive checklist and the two falsifiable bars a reproducing run must clear, and
[Hypotheses & findings](hypotheses.md) for the per-claim threats-to-validity table.
