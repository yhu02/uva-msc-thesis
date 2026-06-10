# 3. The ChaosProbe framework

<!-- System chapter. Deep reference: chaosprobe/TECHNICAL.md and
chaosprobe/docs/. This chapter substantiates contribution claim 2. -->

ChaosProbe is the instrument that produces every number in this thesis: a
Python framework that mutates pod placement as a controlled variable, injects
LitmusChaos faults into the target application, probes the outcome at three
measurement layers concurrently, and emits structured, provenance-stamped
results. This chapter describes its architecture as built (the consolidated
technical reference lives in the repository alongside the code) and the
provenance-capture design that Chapter 4's campaign protocol depends on. The
framework is deliberately *evaluation-shaped* rather than
*optimization-shaped*: unlike the network-aware schedulers of §2.2, it does
not try to find a good placement — it realizes a chosen placement exactly,
disturbs it, and measures what happens at each layer.

**Figure 3.1: ChaosProbe experiment workflow** — placement mutation →
settle → load + chaos injection → post-chaos probing → aggregation →
provenance stamping → archive.

Each experiment iteration follows the pipeline in Figure 3.1. The placement
mutator first realizes the strategy under test and waits for the deployment
to settle; the load generator and the continuous probers then start, the
fault is injected and held for the chaos window, and probing continues
through a post-chaos phase so recovery is captured. The per-iteration data is
aggregated into a per-strategy summary, stamped with the run's provenance
manifest, and — for campaign sessions — archived as the citable artifact.
The chaos runner, the load generator, and every continuous prober run in
parallel during the chaos window; their outputs are joined only after the
iteration ends, so no prober's sampling cadence depends on another's.

## 3.1 Architecture

The implementation is a single Python package whose top-level modules map
one-to-one onto the responsibilities below: `placement/` (strategies and
the mutator), `chaos/` (the LitmusChaos runner), `orchestrator/` (phasing,
readiness, recovery, diagnostics), `metrics/` and `collector/` (the probers
and result collection), `loadgen/` (Locust), `storage/` (Neo4j),
`output/` (summary generation, charts, reports), and `commands/` (the CLI
surface). The description below follows that layout, so each architectural
claim in this chapter is checkable against a named module.

**CLI** (`chaosprobe`). The command-line interface is the framework's only
entry point, organized around the experiment lifecycle: `init` installs
cluster infrastructure (LitmusChaos, Prometheus, Neo4j, the in-cluster
registry), `run` executes a strategy × fault × iteration matrix, and the
analysis commands — `doctor`, `stats`, `power`, `report`, `recommend`,
`summarize`, `inspect`, `diff`, `export` — consume the resulting
`summary.json` without touching the cluster. A `cluster vagrant` command
group manages the Vagrant-provisioned local cluster (§4.5), and archiving a finished
run into the citable `dist/` store is performed by the committed
`scripts/archive_run.py`. Two properties matter methodologically. First,
`run` is **self-healing**: it verifies and reinstalls missing infrastructure
before each invocation, so experiments are re-entrant and a campaign session
never silently runs against a half-provisioned cluster. Second, the analysis
commands are pure functions of the stored output: every statistic in Chapter
5 can be recomputed from an archived `summary.json` without cluster access.

**Placement mutator & strategies.** Placement is manipulated at the
deployment level: the mutator (`placement/mutator.py`) patches each
deployment's `nodeSelector` to pin services to nodes, realizing eight
strategies defined in `placement/strategy.py` — `baseline` (a no-fault
control that leaves placement and injects nothing), `default` (the unmodified
Kubernetes scheduler), `colocate` (all services on the target's node),
`spread` (services distributed across workers, deterministic per-service
pinning), `adversarial` (a worst-fit resource hotspot), `random` (seeded, so
reproducible), `best-fit` (bin-packing onto the fewest nodes), and
`dependency-aware` (a BFS partition of the service dependency graph; §2.2
gives each strategy's literature anchor). Because a `nodeSelector` is a
request to the scheduler, not a guarantee of the realized state, the mutator
verifies the outcome: each iteration records per-service
**`placementMatchRates`** — the fraction of pods that actually landed where
the strategy intended — so mismatched iterations are visible to the analysis
rather than silently polluting it (§7.1). This deployment-level,
single-replica design is a scoping decision with consequences the thesis
returns to repeatedly: it cleanly manipulates *between-service* topology, but
it structurally excludes multi-replica anti-affinity questions (§7.2, §8.2).

**LitmusChaos runner.** Fault injection is delegated to LitmusChaos: the
runner (`chaos/runner.py`) saves, triggers, and polls ChaosEngine experiments
(`pod-delete`, CPU/memory hogs, `node-drain`) through the ChaosCenter GraphQL
API, wrapping each in the settle → chaos → post phasing that gives the
probers stable pre- and post-fault baselines. The aggregate resilience
score of §4.1 is computed from these experiments' probe verdicts —
including command probes whose images `run` builds and pushes to an
in-cluster registry — which is why the score's failure modes track the
probes' own dependencies on cluster health (§6.1). The runner carries two pieces
of hardening that campaign-scale operation forced. Injection attempts retry
with best-attempt quality handling: a transient Litmus failure retries, and
if no attempt is fully clean the best attempt is kept *and marked*, so the
data-quality gate (`doctor`) can taint or exclude the iteration instead of
the run aborting or the defect passing unnoticed. And destructive faults have
cleanup guards — after `node-drain`, the runner uncordons any node left
cordoned by a failed experiment teardown, because a leaked cordon would
silently change the cluster available to every subsequent iteration.

**Orchestration, readiness gates, and recovery measurement.** Between the
mutator and the runner sits the orchestrator, which owns the property the
statistics of Chapter 4 quietly depend on: that every iteration of every
strategy is measured under the same protocol. A pre-flight module validates
the scenario and cluster state before each iteration; readiness gates hold
the iteration until the target pod is running, the application answers
end-to-end, and a warmup period has passed — so the pre-chaos baseline is a
settled system, not a deployment still converging from the previous
placement mutation. Timing arithmetic derives the probe windows from the
declared chaos duration, so settle, chaos, and post-chaos phases are
identically proportioned across strategies and sessions. A recovery watcher
follows the target deployment's pod lifecycle through the kill cycle,
recording deletion, scheduled, and ready timestamps — the source of the
recovery times quoted in H6 and of the two-phase recovery decomposition
whose run-to-run instability is itself a finding (§6.4). When a Litmus
probe returns an `Unknown` verdict, a diagnostics module captures the
cluster state at that moment, which is how the study can assert *why* the
score is unusable under node drain (§5.6) rather than merely observing
that it is.

**Probers (cross-layer).** The measurement design of §4.1 is realized by a
set of continuous probers that run concurrently through every iteration. The
*Prometheus mechanism prober* samples kernel- and infrastructure-layer
signals via PromQL: `conntrack_entries_per_node` (the H2 signal), CoreDNS
request-duration tails, TCP retransmits, and CPU throttling; kube-proxy's own
network-programming-latency metric is queried but recorded as uncollected on
this cluster (§7.1) — the framework's `metricAvailability` map distinguishes
"not collected" from "collected zero" precisely so such gaps cannot
manufacture fake values. The *route latency prober* measures the user layer:
per-route p50/p95/p99 and error rate against the application's HTTP routes,
with routes classified as fault-**dependent** (they touch the chaos target)
or **control** (they do not) — the confound-control split that H3 and H4
rest on. *Redis, disk, and resource probers* track application state, I/O,
and node/container resource trajectories. Finally, an *EndpointSlice
snapshotter* records per-service ready-endpoint counts every 15 s through
the chaos window; its outage troughs are the basis of H6's blast-radius
metric, which deliberately bypasses both the score and the route layer
(under a node drain, every Litmus probe returns `Unknown` and the score is
unusable — §5.6).

**Load generation.** User traffic is generated by Locust profiles against
Online Boutique's user-facing routes: a steady profile (50 users) provides
background traffic for churn experiments, and a 200-user spike profile
drives the cluster into the genuine resource contention that the H4/H5
experiments require — the regime that, as Appendix B documents, synthetic
hog faults cannot create on this cluster. The load generator runs in
parallel with the probers throughout the chaos window, so the route prober
measures latency under the same traffic the fault perturbs.

**Storage.** Each run emits structured per-iteration JSON aggregated into a
per-strategy `summary.json` (output schema 2.0.0) — the single artifact all
analysis consumes — and writes a **Neo4j graph** of services, dependency
edges, and per-iteration pod→node placements. For most of the study the
graph is storage; H5 makes it **analytically load-bearing**: the cross-node
call fraction — H5's static predictor — is computed by joining the graph's
inter-service edges with the recorded `podPlacements`, so the prediction
exists *before any chaos is injected* and depends on the graph being a
faithful model of the application's call structure. The `summary.json`
itself is organized for exactly the analyses Chapter 5 performs: per
strategy it carries the per-iteration scores, probe verdicts, recovery
timings, placement match rates, the per-route latency/error aggregates
split by dependent-vs-control classification, the mechanism metric
time-series summaries with their `metricAvailability` flags, and the
`routeViewAggregate` from which the east-west inter-service tails of H4/H5
are read. Nothing downstream re-queries the cluster; the file is the
experiment's complete, self-describing record.

**Analysis commands.** Downstream analysis is split between the CLI and
committed campaign scripts. `doctor` is the data-quality gate: it checks
per-iteration completeness, probe verdicts, and placement match, and in
`--strict` mode additionally refuses runs with incomplete provenance (§3.2).
`stats` computes pairwise strategy statistics; `report` renders
HTML/Markdown reports; `recommend` summarizes the per-strategy comparison.
The campaign-level numbers in Chapter 5 come from dedicated scripts —
`scripts/score_variance.py` (H1's variance partition, ICC, bootstrap CI,
power), `mechanism_metrics.py` (H2), `h3_mechanism_outcome.py` (H3),
`cross_node_fraction.py` (H5), `blast_radius.py` (H6), and
`campaign_status.py` (session bookkeeping) — each committed in the
repository, so every quoted figure names the code path that produced it.

Three design principles cut across these components and are worth stating
once. First, **measurement is separated from judgment**: the probers record
what happened — including taints, partial data, and `Unknown` verdicts —
and the decision about whether an iteration's data is usable belongs to
`doctor`, applied after the fact under explicit, versioned rules. Nothing
in the collection path drops inconvenient data. Second, **absence is
recorded, not interpolated**: the `metricAvailability` map exists because
the most dangerous failure mode of a metrics pipeline is a missing query
that reads as zero; a metric ChaosProbe could not collect is stored as
uncollected, and the analysis scripts treat it as such. The kube-proxy
network-programming-latency metric is the live example — queried, never
scraped on this cluster, and therefore cited in this thesis only as
conceptual anchoring for the mechanism, never as data (§7.1). Third,
**every number is regenerable**: the chain from CLI flags to scenario YAML
to `summary.json` to analysis script is deterministic given the archived
inputs, which is what allows Appendix A to function as a claims→runs
mapping rather than a bibliography of irreproducible measurements.

The framework's limitations are part of its honest description. Placement
is mutated at deployment level with `nodeSelector`, which is what makes
the realized placement verifiable, but also what restricts the study to
between-service topology — per-service replica anti-affinity is out of
reach of the current mutator, a boundary that shapes the future-work
design in §8.2. The probers sample at fixed cadences (EndpointSlice
snapshots every 15 s), so sub-cadence transients are invisible; H6's
blast-radius troughs are well above this resolution, but finer-grained
availability dynamics would need a faster snapshotter. And the framework
measures one cluster at a time: nothing in the design prevents running the
same campaign on different infrastructure, but the cross-environment
comparison itself is future work, not a shipped feature.

## 3.2 Provenance capture

Every run is stamped with a manifest (`artifact-manifest.json`) carrying:

- **scenario SHA-256 hashes** (the exact fault YAML injected),
- **batch/day IDs** and run ID,
- **kube-proxy mode and conntrack settings** (mode, `maxPerCore`, `min`,
  TCP timeouts) — required because the H2 mechanism is version- and
  mode-sensitive,
- **git commit** (+ dirty flag) of the framework code,
- **environment fingerprint**: Kubernetes server version, container runtime,
  node OS, CNI hint, namespace, strategy/fault matrix, iteration count.

`doctor --strict` refuses a run whose provenance is incomplete or whose
scenario hashes drift; `scripts/archive_run.py` banks the run under
`chaosprobe/dist/` as the citable artifact (Appendix A).

Provenance is a first-class feature of the framework, not packaging
convenience, for three reasons. First, the claims demand it: the H2
mechanism is sensitive to the Kubernetes version, the kube-proxy mode, and
the kernel's conntrack configuration (§6.3 — upstream conntrack handling
changed materially across v1.31–v1.32), so a conntrack-flush number quoted
without its environment fingerprint is not interpretable, let alone
reproducible. The manifest pins exactly the parameters on which the
mechanism's scope depends.

Second, the study learned the cost of weak provenance empirically. The
original H4 pilot produced the most striking user-layer number in the study
— co-location apparently ~3× better at the user layer under load — but the
run had untracked working-tree changes and incomplete metadata. When it was
replaced by two `doctor`-gated *i* = 4 batches, the user-layer effect
collapsed and only the mechanism-layer effect survived (§5.4). Had the dirty
pilot been quoted, the thesis's headline would have been an artifact of an
unreproducible run state. The lesson is institutionalized as the campaign
rule that no number is quoted from a run failing `doctor --strict`, and as
the manifest's dirty flag, which makes an uncommitted-code run permanently
visible as such.

Third, the external advisory review of this work flagged missing raw
artifacts as a blocker: findings quoted in prose with no bankable evidence
trail. The archive pipeline answers that structurally rather than
procedurally — `scripts/archive_run.py` packages each run's `summary.json`,
per-strategy data, charts, and manifest, with per-file SHA-256 hashes, under
`chaosprobe/dist/`, and Appendix A maps every claim in Chapter 5 to the
archives that carry it. The provenance chain is thus verifiable end to end:
from a number in this document, to a named archive, to hashed data files, to
the exact scenario YAML and code commit that produced them.
