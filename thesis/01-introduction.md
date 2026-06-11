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

Consider the concrete decision this thesis instruments. An operator runs a
ten-service microservice storefront on a small Kubernetes cluster and must
choose how to place it: pack the services onto as few nodes as possible, or
spread them across all of them. The packing intuition comes from the
network-aware scheduling literature — co-located services keep their calls
node-local, and schedulers built on that observation report response-time
reductions of up to 37% ([Wojciechowski et al. 2021](https://ieeexplore.ieee.org/document/9488670/)).
The spreading intuition comes from the availability literature — replicas and
services in distinct failure domains survive the loss of any one of them
([Garefalakis et al. 2018](https://dl.acm.org/doi/abs/10.1145/3190508.3190549)) —
and from the interference literature, which warns that co-location invites
resource contention ([Mars et al. 2011](https://www.cs.virginia.edu/~skadron/Papers/mars_micro2011.pdf);
[Delimitrou and Kozyrakis 2014](https://www.csl.cornell.edu/~delimitrou/papers/2014.asplos.quasar.pdf)).

What does today's tooling tell this operator? The Kubernetes scheduler scores
nodes on resource fit, not fault isolation — a design it inherits from Borg
([Verma et al. 2015](https://dl.acm.org/doi/10.1145/2741948.2741964);
[Burns et al. 2016](https://dl.acm.org/doi/10.1145/2890784)) — so the default
placement answers a different question than the one the operator is asking.
Chaos-engineering tools answer the resilience question directly, but they
answer it with one number: inject a fault, probe the steady state, emit a
score. Whether that score can actually *discriminate* between the packed and
the spread placement — whether its between-strategy signal exceeds its
run-to-run noise — is, to our knowledge, unexamined.

The cost of choosing wrong is real on both axes, and asymmetric. If the
operator spreads when latency was the binding constraint, every inter-service
call crosses the network and the tail latency budget erodes continuously,
under exactly the load conditions where it matters most. If the operator packs
when availability was the binding constraint, the failure of a single node
takes the whole application down at once — a blast-radius failure mode the
cloud-architecture literature warns about qualitatively
([AWS Well-Architected](https://docs.aws.amazon.com/wellarchitected/latest/reducing-scope-of-impact-with-cell-based-architecture/reducing-scope-of-impact-with-cell-based-architecture.html))
but that placement-evaluation tooling does not measure. And if the operator
trusts an aggregate resilience score to adjudicate, they may be reading noise:
this thesis measures the score's reliability for exactly this ranking task and
finds it insufficient in the regime studied (H1, §5.1).

This thesis connects the two bodies of practice. We build a framework that
*manipulates* placement as a controlled variable, injects faults from three
distinct classes, and measures the outcome at three layers simultaneously —
the aggregate score, the kernel/network mechanism, and the user-visible
routes — so that the question "does placement matter under chaos?" can be
answered per fault class and per layer rather than with a single number.

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

We decompose the main question into four sub-questions, one per fault class
and one for the instrument itself:

- **SQ1 (churn).** Under single-replica `pod-delete` churn, at which
  measurement layer does a placement effect appear, and does it propagate to
  the user-visible routes? (Answered by H2 and H3, §5.2–5.3.)
- **SQ2 (load contention).** Under sustained load — the regime where the
  user-visible outcome *is* latency — does placement move the inter-service
  mechanism, and does that effect reach the user-facing routes reproducibly?
  (Answered by H4 and H5, §5.4–5.5.)
- **SQ3 (node failure).** Under a node drain, how does placement move the
  availability axis — blast radius and recovery time — and how does that
  relate to the latency axis measured under load? (Answered by H6, §5.6.)
- **SQ4 (score reliability).** Can the aggregate resilience score rank
  placement strategies at all, given session-level variance, at any feasible
  iteration count? (Answered by H1, §5.1.)

In crosswalk form: SQ1 → H2/H3, SQ2 → H4/H5, SQ3 → H6, SQ4 → H1.

SQ4 is logically prior to the others: if the standard instrument could rank
placements reliably, the layered measurement design would be a refinement.
Because it cannot (in this regime), the layered design is what produces the
findings at all.

The study that answers these questions, at a glance: eight placement
strategies (a no-fault control, the default scheduler, and six controlled
mutations spanning packing, spreading, randomization, and graph-aware
placement), realized by deployment-level `nodeSelector` mutation on the
Online Boutique benchmark — ten polyglot gRPC microservices plus Redis, one
replica per service — on a pinned five-node cluster (Kubernetes v1.28.6,
kube-proxy in ipvs mode). The churn evidence is a seven-session campaign of
independent, provenance-gated run invocations (8 strategies × 3 iterations
per session, 147 iterations); the load-contention evidence is two replicated
four-iteration batches under a 200-user spike; the node-failure evidence is
two clean node-drain batches. Every quoted number traces to an archived run
(Appendix A), and the single-replica scoping — which makes `pod-delete` a
pure churn fault and excludes multi-replica failover questions — is carried
explicitly through every claim (Chapter 7).

## 1.3 Contributions

The thesis makes four explicit claims. The claims are ordered by decreasing
novelty; claim 1 itself bundles three findings of differing novelty, stated
separately so each is a one-line falsifiable target:

1. **Novel empirical contribution** — three findings:
   - **1a — layered decoupling.** Under both single-replica churn and load
     contention, placement reproducibly moves a mechanism-layer signal
     (conntrack flush 38.5% vs 2.7%, H2; east-west tail 1.36–1.39×, H4) that
     does **not** reproducibly reach the user-visible layer (H3, H4).
   - **1b — measured trade-off pair.** The same co-location property that
     minimizes east-west tail latency (H5) maximizes node-failure blast
     radius and recovery time (11/11 vs 2/11 services offline; ≈10.3 s vs
     ≈2.6 s, H6).
   - **1c — score-reliability critique.** The aggregate resilience score
     cannot rank placement strategies under session variance (ICC 0.033;
     between-strategy variance 3.3% of total, H1).

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

**What the candidate did versus what the tooling did.** Because the
framework automates heavily, we state the division of labor plainly.
*Authored for this thesis*: the placement mutator and all eight strategies;
the three-layer measurement design with its dependent-vs-control route
confound check; the cross-node-fraction metric (H5) and the blast-radius
trough measurement (H6); the provenance gate (`doctor --strict`), the
discard-not-patch rule, and the multi-session campaign protocol; the
churn-vs-contention fault-class framing; every committed analysis script
(variance partition, paired tests, TOST, figure generation); and the
judgment calls the gate forced — including retracting two of our own
headline numbers (§5.8). *Integrated off-the-shelf*: LitmusChaos (fault
execution), Prometheus/Locust (collection and load), Neo4j (graph storage),
and the Online Boutique application. The science — what to vary, what to
measure, what to believe — is the candidate's; the third-party components
execute it.

Claim 1 is substantiated by Chapters 5 and 6. Chapter 5 reports each
hypothesis with its data, statistics, and archived provenance; Chapter 6
argues that the three results are one structure — placement effects are real
but layer-bound, the two faces of co-location are the same graph property
read on opposing axes, and the aggregate score sees none of it. Every part of
the claim is bounded by Chapter 7: it is a statement about single-replica
deployments on one small virtualized cluster, with the *direction* of the
mechanism effects and the *method* as the portable parts, not the absolute
numbers.

Claim 2 is substantiated by Chapter 3, which describes the framework against
its actual code layout and documents the design decisions that the empirical
chapters depend on: deployment-level placement mutation with per-iteration
match-rate verification, cross-layer probing with explicit metric-availability
bookkeeping, and provenance stamping of every run. The framework is open and
the analysis scripts that produce each quoted number are committed alongside
it.

Claim 3 is substantiated by Chapter 4 and Appendix A. The campaign protocol —
independent run invocations, each on a single code commit, each gated by an
automated provenance and data-quality check before its numbers may be used —
is what separates the headline results (7/7-session mechanism replication,
campaign-level variance decomposition) from the pilot tier. Appendix A maps
every claim to the archived runs that carry it.

Claim 4 is substantiated by Appendix B and discussed in Chapters 4 and 6. The
negative findings are presented as methodological results in their own right:
they explain *why* the obvious contention experiments probe the wrong layer on
clusters of this class, and they redirect the experimental design toward load
as the contention stressor — a lesson that future placement-under-chaos
studies can reuse directly.

## 1.4 Thesis outline

Chapter 2 positions the thesis against four bodies of literature — resilience
profiling, production resilience testing, scheduler and placement research,
and measurement methodology — and locates it at their empty intersection.
Chapter 3 describes ChaosProbe, the framework that produces all data in the
study, and its provenance-capture design. Chapter 4 defines the three-layer
measurement design, the three fault classes, the multi-session campaign
protocol, the statistical methods, and the pinned cluster environment.
Chapter 5 reports the results for H1–H6 with final campaign numbers and
per-claim provenance. Chapter 6 interprets the results as a single layered
structure and derives the operator-facing implications. Chapter 7 states the
threats to validity and the boundary of every claim. Chapter 8 concludes and
lays out future work, including the experiments that this study's design
decisions deliberately excluded. Appendix A contains the archived-run
provenance tables; Appendix B documents the negative findings.
