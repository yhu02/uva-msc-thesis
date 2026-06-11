# 2. Related work

<!-- Citation details (full citations, DOIs, links, verified positioning
notes) live in ../references.md — pull from there, cite author-year. -->

## 2.1 Positioning: the quadrant this thesis occupies

The four nearest works, and the axis on which each differs:

| Work | What it does | How this thesis differs |
|---|---|---|
| **MicroRes** — Yang et al. (ISSTA 2024), [arXiv:2212.12850](https://arxiv.org/abs/2212.12850) | Score-based resilience profiling: ranks degradation by whether it disseminates from system to user metrics. | **"They score the decoupling; we measure its mechanism"** under a manipulated variable (placement). MicroRes performs no variance decomposition, ICC, power, or test-retest reliability analysis of its index — H1's reliability critique is that gap. Scope H1 *against* their reported 0.86–0.90 accuracy / 0.92–0.95 F1: those are for **binary** resilient-vs-non-resilient classification with per-dataset tuned thresholds, so H1 must stay "the score cannot **rank placement strategies under session variance**" — never "aggregate scores don't work". |
| **Cast** — ICSE 2026 SEIP, [arXiv:2602.00972](https://arxiv.org/html/2602.00972v1) | Production resilience testing at Huawei Cloud: traffic record-and-replay + application/RPC-level fault injection (137 potential vulnerabilities, 89 confirmed). | Cast finds *application-layer fault-handling bugs*; this thesis measures *infrastructure-layer placement mechanisms*. Cast has no placement, scheduling, kernel/network-mechanism, or blast-radius content. |
| **Cloud-edge fault-injection study** — Liu et al. (2025), [arXiv:2507.16109](https://arxiv.org/abs/2507.16109) | Largest existing K8s failure-injection dataset (11,965 experiments), architecture-level benchmarking of cloud-edge deployments. | Benchmarks **where the cluster runs, not where pods land**: no churn-vs-contention distinction, no conntrack/kube-proxy mechanism, no placement-strategy comparison. Complementary and orthogonal. |
| **NetMARKS** — Wojciechowski et al. (INFOCOM 2021), [DOI 10.1109/INFOCOM42981.2021.9488670](https://ieeexplore.ieee.org/document/9488670/); **TraDE** (2024), [arXiv:2411.05323](https://arxiv.org/abs/2411.05323) | Locality as an *optimization objective*: network/traffic-aware schedulers that co-locate using runtime telemetry (NetMARKS: up to 37% response-time reduction). | They *optimize* with runtime metrics; **H5 validates a static, pre-chaos two-regime separator** — the cross-node dependency-edge fraction separates node-local from spreading placements, and that separation predicted the east-west tail in two independent batches (batch-1 ρ = 0.79 did **not** replicate as a continuous law: ρ = 0.25 n.s. in batch 2; the replicated claim is the separation, not a smooth correlation). The locality concept itself is theirs. NetMARKS reports end-to-end response time, not east-west p95: directional precedent, not like-for-like. |

These four works define the axes of the space this thesis sits in: MicroRes
and Cast evaluate resilience without touching placement, while NetMARKS/TraDE
and Liu et al. vary deployment topology without chaos-style evaluation of the
outcome. The intersection — manipulate placement as the controlled variable,
inject faults from distinct classes, measure the mechanism and user layers
simultaneously, and audit the reliability of the aggregate score current
practice would use instead — is empty, and that intersection is where this
thesis sits. The positioning is deliberately modest on each axis, and each
differentiator — including the precise scoping of H1 against MicroRes's
binary-classification success — is stated once, in the table above; the
MicroRes scoping is restated only where it bites, at the H1 result (§5.1).

Stated as coordinates: on the axis *what is manipulated*, this thesis sits
with the schedulers (placement) and against the profilers (observability
weighting, fault selection); on the axis *what is measured*, it sits below
the user-visible steady state, at the kernel/network mechanism layer, where
none of the four neighbors measure; on the axis *what is trusted*, it
treats the evaluation metric itself as an object of study rather than an
instrument taken on faith. The rest of this chapter walks the supporting
lineages: the scheduler literature that supplies the strategies and the
predictions (§2.2), the chaos-engineering tradition that supplies the
faults and the gap (§2.3), the measurement-methodology literature that
supplies H1's argument shape (§2.4), and the tail-latency work that
supplies L3 and the reporting discipline (§2.5).

## 2.2 Scheduler & placement lineage

- **Borg** — Verma et al. (EuroSys 2015): bin-packing/best-fit scheduling,
  anti-affinity defaults; source for the claim that production schedulers
  optimize for resource fit, not fault isolation; cited for the `best-fit`
  strategy. **Borg/Omega/Kubernetes** — Burns et al. (ACM Queue 2016): how
  Borg's lessons map onto the Kubernetes default scheduler.
- **Medea** — Garefalakis et al. (EuroSys 2018): the canonical
  "spread-is-best" topology-constraint reference; source of L2's predicted
  ordering. The thesis finds this prescription did not transfer to
  single-replica churn — it remains valid for the multi-replica availability
  regime it was written for.
- **Sparrow** — Ousterhout et al. (SOSP 2013): randomized scheduling; cited
  for the `random` strategy.
- **Quasar** — Delimitrou & Kozyrakis (ASPLOS 2014) and **Bubble-Up** — Mars
  et al. (MICRO 2011): the interference/contention model behind L1
  ("co-location hurts"); found *inapplicable in the single-replica churn
  regime* tested here, not wrong in the contention settings it was written
  for.
- Supporting: **Resource Central** — Cortez et al. (SOSP 2017) for the
  `adversarial` (worst-fit hotspot) strategy; **DeathStarBench** — Gan et al.
  (ASPLOS 2019), **Sinan** — Zhang et al. (ASPLOS 2021), and **METIS** —
  Karypis & Kumar (1998) for the `dependency-aware` strategy's graph-partition
  lineage.

This lineage serves the thesis in two distinct roles: it supplies the
*strategies* and it supplies the *predictions*. On the strategy side, each of
ChaosProbe's eight placement strategies (§3.1) is anchored in a documented
scheduling idea rather than invented ad hoc: `best-fit` realizes the
bin-packing objective production schedulers inherit from Borg
([Verma et al. 2015](https://dl.acm.org/doi/10.1145/2741948.2741964);
[Burns et al. 2016](https://dl.acm.org/doi/10.1145/2890784)); `random` is the
degenerate single-node case of Sparrow's randomized dispatch
([Ousterhout et al. 2013](https://dl.acm.org/doi/10.1145/2517349.2522716));
`adversarial` constructs the asymmetric resource hotspot that workload
characterization studies identify as the pathological case
([Cortez et al. 2017](https://dl.acm.org/doi/10.1145/3132747.3132772));
and `dependency-aware` partitions the service dependency graph in the spirit
of the microservice-topology literature
([Gan et al. 2019](https://dl.acm.org/doi/10.1145/3297858.3304013);
[Zhang et al. 2021](https://dl.acm.org/doi/10.1145/3445814.3446693)), using a
lightweight BFS variant of balanced k-way partitioning
([Karypis and Kumar 1998](https://www.cs.utexas.edu/~pingali/CS395T/2009fa/papers/metis.pdf)).
The remaining strategies anchor the design's two poles and its controls:
`colocate` operationalizes the locality objective of the network-aware
schedulers (§2.1), `spread` operationalizes Medea-style failure-domain
separation, `default` is the unmodified Kubernetes scheduler — the
resource-fit baseline operators actually get — and `baseline` is the
no-fault control that bounds what the instruments report when nothing is
injected. The set is deliberately a *spanning* set rather than a contest of
contenders: it covers the packing–spreading axis densely enough that H5's
cross-node fraction takes values across its whole range (0.00 to 0.80,
§5.5), which is what makes the predictor testable.

On the prediction side, the lineage yields the three literature-derived
expectations the study tests, labeled L1–L3. L1 — *co-location is the worst
placement under fault* — distills the interference model: Bubble-Up
([Mars et al. 2011](https://www.cs.virginia.edu/~skadron/Papers/mars_micro2011.pdf))
and Quasar
([Delimitrou and Kozyrakis 2014](https://www.csl.cornell.edu/~delimitrou/papers/2014.asplos.quasar.pdf))
both quantify how co-located workloads degrade each other through shared
resources, so a fault landing on a packed node should hurt most. L2 — *spread
isolates best* — distills Medea's topology-constraint reasoning
([Garefalakis et al. 2018](https://dl.acm.org/doi/abs/10.1145/3190508.3190549)):
placing components in distinct failure domains bounds the impact of any one
domain's failure. L3 — *recovery time predicts the resilience score* — is
derived in §2.5 from the tail-latency literature. Chapters 5 and 6 find all
three **inapplicable in the single-replica churn regime tested** (§6.4): the
fault class they implicitly assume (contention, multi-replica availability)
is not the fault class `pod-delete` realizes there. We state this finding
with care, because it is a scoping result, not a refutation — under load
contention, the regime L1 and L2 were actually written for, the locality
intuition behind them does hold at the mechanism layer (H4, H5), and the
availability reasoning behind L2 reappears, with its sign confirmed, in the
node-failure regime (H6).

## 2.3 Chaos-engineering lineage

- **Principles** — Basiri et al. (IEEE Software 2016); **Chaos
  Monkey/Simian Army** — Netflix (2011): fault-injection-by-killing, which
  ChaosProbe generalizes across controlled placements.
- **LitmusChaos**: the injection engine ChaosProbe drives (pod-delete,
  hogs, node-drain).
- **ChaosEater** — Kikuta et al. (ASE 2025 NIER): LLM-automated chaos —
  orthogonal, situates the 2025 landscape.
- **Gap evidence** — the ACM CSUR multi-vocal review (arXiv:2412.01416,
  DOI 10.1145/3777375; ≈90 sources through April 2024) defines chaos outcomes
  solely against steady-state user-visible indicators and contains no
  placement, kernel/scheduler, conntrack, or EndpointSlice content; the
  Dec-2025 SLR (arXiv:2512.16959) adds a recovery-pattern taxonomy and RES
  checklist, likewise without placement variation. Cite both as evidence the
  placement-aware, cross-layer, metric-reliability angle is **a specific gap,
  not a field-wide void**.
- Adjacent K8s failure analysis: **Mutiny!** — Barletta et al. (DSN 2024),
  etcd-state corruption (complementary layer); Yang et al. (arXiv:2405.18001),
  placement-vs-reliability via *modeling* rather than empirical injection.

Chaos engineering begins as a production practice: Netflix's Chaos Monkey
terminated instances at random to force teams to build for failure
([Netflix 2011](https://netflixtechblog.com/the-netflix-simian-army-16e57fbab116)),
and the practice was later codified as a discipline — hypothesize about
steady state, inject realistic faults, observe whether the steady state
survives ([Basiri et al. 2016](https://dl.acm.org/doi/abs/10.1109/MS.2016.60)).
ChaosProbe inherits the central fault of that tradition, the kill, but
inverts its key design choice: where Chaos Monkey kills at *random* to
exercise unknown weaknesses, ChaosProbe kills under *controlled, repeated
placements* to measure a specific variable's effect. The injection machinery
itself is delegated to LitmusChaos, a CNCF chaos toolkit whose experiment
CRDs (pod-delete, resource hogs, node-drain) ChaosProbe drives
programmatically (§3.1). At the automation frontier, ChaosEater
([Kikuta et al. 2025](https://arxiv.org/abs/2511.07865)) uses LLMs to design
and run chaos experiments end to end — orthogonal to this thesis's question,
but evidence that the field's current emphasis is on automating the loop, not
on auditing the metrics the loop optimizes.

The gap this thesis targets is visible in the field's own syntheses. The ACM
CSUR multi-vocal review ([arXiv:2412.01416](https://arxiv.org/abs/2412.01416),
≈90 sources through April 2024) defines chaos outcomes solely against
steady-state user-visible indicators — latency, error rate, throughput,
availability — and contains no placement, kernel/scheduler, conntrack, or
EndpointSlice content; its open-research-issues section is organizational
(culture, skills, resources), not metrological. The December 2025 SLR on
resilient microservices ([arXiv:2512.16959](https://arxiv.org/abs/2512.16959))
adds a recovery-pattern taxonomy and an evaluation-score checklist, again
without placement variation, and the JSS systematic review of chaos
experiments in microservice architectures
([Awad et al. 2025](https://www.sciencedirect.com/science/article/abs/pii/S092054892500145X))
is likewise silent on placement-strategy variation. We read these surveys
carefully: they show that the placement-aware, cross-layer,
metric-reliability angle is a *specific gap* in a well-consolidated
literature, not that the literature is deficient wholesale.

The nearest empirical Kubernetes failure studies work at adjacent layers.
Mutiny! ([Barletta et al. 2024](https://arxiv.org/abs/2404.11169)) shows how
corruption of cluster state (etcd) propagates to cluster-wide failures — a
control-plane layer this thesis does not touch, just as Mutiny! does not
touch placement. Liu et al.'s cloud-edge dataset (§2.1) is the scale
benchmark for K8s fault injection but varies environment, not placement. And
Yang et al. ([arXiv:2405.18001](https://arxiv.org/abs/2405.18001)) address
placement-versus-reliability directly but through analytical modeling — they
predict failure reduction where this thesis measures behavior under injected
faults. Together these works bracket the contribution: the layer (pod
placement within one cluster), the method (empirical injection under a
manipulated variable), and the measurement design (cross-layer with a
reliability audit) are each individually present in the literature's
neighborhood, but not combined.

Two further neighbors mark the methodological and forward-looking edges of
the space. Hagedoorn et al.
([arXiv:2604.00080](https://arxiv.org/abs/2604.00080)) empirically study how
*architectural topology* — the structure of the application's service graph
— affects microservice performance and energy use; they are a methodology
peer in varying a structural property and measuring the consequence, but
their manipulated variable is the application's design, where ours is its
*deployment* onto nodes with the application held fixed. And recent
constraint-based pod-packing work
([arXiv:2511.08373](https://arxiv.org/abs/2511.08373)) shows the scheduling
community continuing to refine packing objectives — relevant to §8.2's
suggestion that a validated static metric like the cross-node fraction
could be integrated into such schedulers as a scoring signal, closing the
loop from evaluation back to optimization.

## 2.4 Methodology precedents

- **Maricq et al. (OSDI 2018), "Taming Performance Variability"** — the
  argument-shape precedent for H1: quantify the noise, prescribe the
  repetitions (≈900k data points, 835 servers). Cite with precision: their
  CONFIRM tool uses nonparametric CI-width stopping, **not** formal
  hypothesis-test power analysis, and "blocked design" is this thesis's term.
- **Mytkowicz et al. (ASPLOS 2009)** — measurement bias ("Producing Wrong
  Data Without Doing Anything Wrong!"); the lineage does not start in 2018.
- **Hoefler & Belli (SC 2015)** — report distributions and CIs, not means.

H1's statistical framing follows an established argument shape in systems
measurement: before comparing systems, quantify the environment's own
variability and derive what repetition can and cannot detect. The precedent
is [Maricq et al. 2018](https://www.usenix.org/conference/osdi18/presentation/maricq),
who showed across roughly 900,000 data points on 835 supposedly identical
servers that hardware-level run-to-run variability is large enough to
undermine naive quantitative comparison, and built CONFIRM to recommend
repetition counts from historical variability. We cite this precedent with
precision: CONFIRM's stopping rule is nonparametric confidence-interval-width
stopping, *not* formal hypothesis-test power analysis, and "blocked design"
is this thesis's term for its own campaign structure, not Maricq et al.'s.
H1 performs the same move — variance decomposition, then a power and
minimum-detectable-effect calculation — on a chaos-engineering score rather
than a hardware benchmark.

The lineage does not start in 2018.
[Mytkowicz et al. 2009](https://www.semanticscholar.org/paper/3886c40229b3de318de668e0c0f4202079eb6f55)
demonstrated that measurement bias from factors as incidental as link order
and environment size can produce wrong conclusions in careful experiments —
the canonical warning that an unexamined measurement pipeline is itself a
threat to validity, which motivates this thesis's provenance capture (§3.2)
and its metric-availability bookkeeping (§7.1).
[Hoefler and Belli 2015](https://dl.acm.org/doi/10.1145/2807591.2807644)
codify the reporting discipline this thesis adopts: report distributions and
confidence intervals, not bare means, and treat nonparametric methods as the
default when normality is unestablished — the reason Chapter 4's toolkit is
built on rank-based tests, bootstrap intervals, and equivalence testing
rather than t-tests on means.

## 2.5 Tail latency

- **The Tail at Scale** — Dean & Barroso (CACM 2013): shared resources →
  latency variability → service-quality damage; the intuition L3 distilled
  into "recovery time predicts the score", which found no stable relationship
  in this regime (recovery's two-phase split is unstable run-to-run).

[Dean and Barroso 2013](https://dl.acm.org/doi/10.1145/2408776.2408794)
established the canonical chain from shared resources to latency variability
to user-visible service-quality damage, and the corollary that the *tail* of
the latency distribution, not its center, is what users experience in
fan-out architectures. The thesis draws on this work twice. First, it is the
source of L3, the third literature-derived prediction: if tail effects
dominate user experience, then the duration of the disruption window — here,
the target pod's recovery time — should predict the resilience score. The
data does not bear this out in the regime tested: recovery time's two-phase
decomposition (deletion-to-scheduled versus scheduled-to-ready) is itself
unstable run-to-run, so there is no stable relationship to find on either
side (§6.4). Second, the tail-first reporting convention shapes the
measurement design: ChaosProbe's route probers report p50/p95/p99 per route,
and every latency claim in Chapter 5 is a tail claim (p95), not a mean claim.

Finally, what is *not* in the literature. Our searches — general web search,
arXiv, Semantic Scholar, and Google Scholar's surfaced results, with the
coverage limits disclosed in [references.md §8](../references.md) — found no
peer-reviewed paper that frames `pod-delete` as a churn fault distinct from
contention faults, none that identifies conntrack/EndpointSlice reconvergence
as the dominant mechanism under pod-delete on small clusters, and none that
empirically compares six or more placement strategies under chaos. The
closest works, surveyed above, operate at a different layer (etcd
corruption), a different scope (cloud-edge environments without placement
comparison), or with a different method (analytical modeling). We state this
as the result of a bounded search rather than as proof of absence: ACM DL,
IEEE Xplore, and recent USENIX proceedings were not exhaustively swept, and
practitioner venues (KubeCon, SRECon, Linux Plumbers) may document parts of
the kill-cycle mechanism outside the peer-reviewed record. Within those
bounds, the churn-versus-contention mechanism framing appears to be a novel
contribution.
