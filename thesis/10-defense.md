# Defense materials

Not a manuscript chapter — the viva preparation sheet. Built on the
post-correction claims (H5 two-regime separator; H2 dual-mechanism with the
measured UDP/DNS composition; H6 gradient). Supersedes any earlier Q&A that
predates those corrections.

## One-page summary (memorize)

**Problem.** Placement literature says locality and interference matter;
chaos practice collapses outcomes into aggregate scores. When chaos is
injected into a Kubernetes microservice application, does placement matter
uniformly across the aggregate score, mechanism metrics, and user-visible
outcomes — and can a single score see any of it?

**Approach.** I built ChaosProbe: it mutates placement (8 strategies),
injects LitmusChaos faults into Online Boutique, and measures three layers
separately — aggregate probe score, kernel/network mechanism metrics, and
route-level user outcomes with a dependent-vs-control confound check —
under a provenance gate (`doctor --strict`) that discards rather than
patches contaminated runs. The campaign unit is an independent session;
seven sessions, 147 churn iterations, every quoted number traceable to a
tamper-evident archive.

**Findings, in the order of the argument.** (1) The score is blind: 3.3 %
of its variance is between-strategy (ICC 0.033, CI [0.014, 0.178]); at the
iteration counts anyone actually runs, it cannot rank placements. (2)
Placement reproducibly moves mechanism layers: conntrack flush 38.5 % vs
2.7 % (7/7 sessions, sign p = .016); east-west tail 1.36–1.39× under load
(both batches). (3) Those mechanism effects do not reproducibly reach the
user: dependent-route correlation ρ = 0.07, TOST-decoupled, while the
control route correlates more — the signature of a run-level confound. The
one user-layer reading that looked strong (~3×) died under clean
replication. (4) Where placement does bite users is availability: drain
the target's node and observed blast equals placement-predicted blast for
all six strategies (ρ = 1.0) — colocate 11/11 services down, spread 2/11
— while the same co-location wins east-west latency (~1.25×, replicated).
One placement property, two opposing, measured consequences.

**Scope.** Single cluster (5-node KVM, k8s v1.28.6, kube-proxy ipvs),
single application, single-replica services. Direction and method
generalize; absolute numbers do not. No universal best placement is
claimed; no refutation of the placement literature is claimed.

**The credibility argument.** The provenance discipline retracted two of
our own headline numbers — the ~3× user-layer claim and the ρ = 0.79
continuous predictor. A method that retracts its own most striking results
when the evidence weakens is the method working as designed.

## Hostile questions and answers

**"Why is this a thesis and not a tool demo?"**
The contribution is empirical: where placement effects appear under chaos,
at which measurement layer, and when a score can see them. The tool is the
instrument. The four contribution claims (§1.3) are each falsifiable, and
two were *partially falsified by my own gate* — that is measurement
science, not a demo.

**"Your ρ = 0.79 collapsed to 0.25. Why should I trust anything else?"**
Because the collapse is the system working. The separation it pointed at —
node-local placements take the two lowest tails of eight in *both* batches
(joint null ≈ 0.0013) — replicated; the continuous law did not, so we
claim the separator and not the law. Every other headline number survived
the same replication discipline.

**"Isn't H6 just 'drain a node, lose its pods'?"**
The prediction is near-definitional; the *measurement* is the
contribution. Nothing in Medea (qualitative motivation plus a trace-driven
unavailability analysis, separate from its performance evaluation),
KEP-895 (qualitative availability rationale, scheduler-internal metrics
only), or AWS cell guidance (1/N arithmetic) measures the latency and
blast-radius faces on the same workload under identical placements. We
did, the predicted blast materialized exactly in every iteration across
the full strategy gradient, and recovery added a non-definitional ~4×
penalty at the extremes.

**"Your workload is TCP/gRPC but kube-proxy only flushes UDP conntrack.
Doesn't that break H2?"**
It broke our first interpretation, and the protocol probe settled it: TCP
dominates the table and drops at kill cycles under *both* placements —
kernel-side teardown via the 10–120 s close-state timeouts, no kube-proxy
involvement — while the placement-dependent component is the UDP/DNS pool,
~4× larger under spread, exactly the class kube-proxy's verified UDP-only
cleanup acts on. The measured H2 signal is real and placement-dependent;
the attribution names both paths and quantifies neither's share, because
the probe's single-iteration windows cannot apportion them.

**"Why TOST? Nobody in systems uses equivalence tests."**
Correct, and the thesis says so: the procedure is imported from
biostatistics (Schuirmann 1987; Lakens 2017), with ROPE-based equivalence
acceptance precedented in JMLR (Benavoli et al. 2017). A non-significant
correlation is absence of evidence; the decoupling claim needed evidence
of absence. The SESOI (|ρ| = 0.3) was fixed in committed code before the
campaign ran.

**"Why single replicas, when topology spread exists for replicated HA?"**
By design: single replicas isolate the *between-service* consequences of
placement (collateral effects, reconvergence, blast radius across the
dependency graph) from replica failover, which would mask them. The
multi-replica regime is structurally excluded, stated everywhere, and the
attempted multi-replica arm was cancelled when the pilot showed the spread
strategy pins all replicas of a service to one node — a documented
limitation, not an oversight.

**"Why didn't you use DeathStarBench?"**
Online Boutique is a real polyglot microservice graph and the study is a
deep case study, scoped as such. The method ports; a DeathStarBench
cross-check is named future work, not claimed.

**"Couldn't the H2 flush just be load noise?"**
Direction held in 7/7 independent sessions (sign test p = .016, Wilcoxon
p = .023) with the order of strategies fixed within sessions, so order
effects are constant across them. And the composition probe ties the
placement-dependent component to a concrete state class (the UDP/DNS
pool), not to an unexplained counter.

**"What did *you* do, versus the tooling?"**
§1.3 answers this head-on: the eight strategies, the three-layer design
with its confound check, the cross-node-fraction metric, the blast-trough
measurement, the provenance gate and discard-not-patch rule, the campaign
protocol, all committed analysis code, and the judgment calls — including
the two retractions. LitmusChaos, Prometheus, Locust, and Neo4j execute;
the experiment design and the inferences are mine.

**"Where are the raw data?"**
Seventeen tamper-evident archives (SHA-256 manifests, environment
fingerprints, scenario hashes), one per quoted run, mapped claim-by-claim
in Appendix A. Every figure
states its regeneration command.

**"What would change your conclusions?"**
A replicated user-layer placement effect under churn or load would break
H3/H4's decoupling; a placement ranking stable across sessions would break
H1; a blast radius diverging from per-node concentration would break H6.
Each is a named, runnable experiment — that falsifiability is the point.
