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
  current single-replica `pod-delete` churn campaign — between-strategy variance
  is a small fraction of total score variance, undetectable at any feasible
  iteration count (H1). The score should not be used alone to rank strategies.
- **Mechanism-level metrics can be more reproducible than the aggregate score** —
  under churn, spreading the target's dependents reproducibly moves a
  kernel/network reconvergence signature (conntrack flush) that the score does
  not capture (H2).
- **Mechanism and user-visible layers can decouple** — a placement effect at the
  mechanism layer did not, in this single-replica regime, reach the user (H3).

## What is bounded or preliminary (weaken — never state flatly)

- *"`pod-delete` is a churn fault"* → *"`pod-delete` behaved consistently with a
  churn/reconvergence mechanism in this setup."*
- *"the spread-is-safer intuition is refuted"* → *"the usual spread intuition did
  not transfer cleanly to this fault class in this setup."*
- *"recovery time predicts nothing"* → *"recovery-time decomposition was unstable
  and did not provide a stable ranking signal here."*
- **Load-contention reordering (H4)** is a single pilot from a dirty-provenance,
  3-iteration run — a hypothesis to confirm with a clean, replicated rerun, not a
  finding.

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
traffic, scheduler integration, a clean node-memory-hog contention campaign, and a
clean H4 load-contention rerun are all future work, not claims.

## Before quoting any number

Every figure quoted as a *finding* must come from an archived, clean-provenance run
that passes `doctor --strict` (which now also gates kube-proxy mode, scenario
hashes, and the rest of the provenance fingerprint). See
[Reproduce the thesis results](../how-to/reproducing-thesis-results.md) for the
archive checklist and the two falsifiable bars a reproducing run must clear, and
[Hypotheses & findings](hypotheses.md) for the per-claim threats-to-validity table.
