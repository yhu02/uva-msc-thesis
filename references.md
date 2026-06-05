# ChaosProbe — References & Related Work

Annotated bibliography supporting the thesis defense. Each entry notes (a) the
full citation, (b) where to find it, and (c) how it relates to specific thesis
claims.

The thesis defends three literature-derived hypotheses, all three of which the
experimental data refutes. The refutations point at a single mechanism:
pod-delete is a *churn-based* fault, not a *contention-based* one, so the
placement-vs-resilience intuition encoded in the literature does not transfer.
References below are organized by which claim they support.

---

## 1. Pod placement affects resilience (L1 / L2 source material)

These references inform the *pre-experiment* hypotheses. They are the
literature-derived intuition the thesis tests and ultimately refutes for the
pod-delete fault class.

### Verma et al. (2015) — Borg
> A. Verma, L. Pedrosa, M. Korupolu, D. Oppenheimer, E. Tune, J. Wilkes.
> *Large-scale cluster management at Google with Borg.* EuroSys 2015.
> Bordeaux, France, April 21–24, 2015. DOI: `10.1145/2741948.2741964`.

- [Google Research PDF](https://research.google/pubs/large-scale-cluster-management-at-google-with-borg/)
- [ACM Digital Library](https://dl.acm.org/doi/10.1145/2741948.2741964)

**Relevance:** Best-fit / bin-packing scheduling; anti-affinity defaults.
Source for the slide-4 claim that "the K8s scheduler optimizes for resource
fit, not for resilience or fault isolation." Cite for the `best-fit` strategy.

### Garefalakis et al. (2018) — Medea
> P. Garefalakis, K. Karanasos, P. Pietzuch, A. Suresh, S. Rao.
> *Medea: Scheduling of Long Running Applications in Shared Production
> Clusters.* EuroSys 2018. DOI: `10.1145/3190508.3190549`.

- [Microsoft Research PDF](https://www.microsoft.com/en-us/research/wp-content/uploads/2018/01/medea-eurosys2018.pdf)
- [ACM Digital Library](https://dl.acm.org/doi/abs/10.1145/3190508.3190549)

**Relevance:** Topology spread constraints; the canonical "spread-is-best"
reference. Source for L2's predicted ordering. The thesis refutes the
applicability of this prescription to churn-based faults.

### Ousterhout et al. (2013) — Sparrow
> K. Ousterhout, P. Wendell, M. Zaharia, I. Stoica.
> *Sparrow: Distributed, Low Latency Scheduling.* SOSP 2013.
> DOI: `10.1145/2517349.2522716`.

- [SOSP PDF](https://sigops.org/s/conferences/sosp/2013/papers/p69-ousterhout.pdf)
- [ACM Digital Library](https://dl.acm.org/doi/10.1145/2517349.2522716)

**Relevance:** Decentralized randomized scheduling. Cite for the `random`
strategy and its seeded-reproducibility property.

### Mars et al. (2011) — Bubble-Up
> J. Mars, L. Tang, R. Hundt, K. Skadron, M. L. Soffa.
> *Bubble-Up: Increasing Utilization in Modern Warehouse Scale Computers via
> Sensible Co-locations.* MICRO 2011, pp. 248–259.

- [PDF (Virginia)](https://www.cs.virginia.edu/~skadron/Papers/mars_micro2011.pdf)

**Relevance:** The foundational contention-aware co-scheduling paper. Source
for the prediction that co-location should hurt resilience. **The thesis
directly refutes the applicability of this work to churn-based faults** — its
findings hold under contention but not under pod-delete churn.

### Delimitrou & Kozyrakis (2014) — Quasar
> C. Delimitrou, C. Kozyrakis.
> *Quasar: Resource-Efficient and QoS-Aware Cluster Management.* ASPLOS 2014.
> Salt Lake City, UT, March 2014.

- [Cornell PDF](https://www.csl.cornell.edu/~delimitrou/papers/2014.asplos.quasar.pdf)
- [Publications list](https://www.csl.cornell.edu/~delimitrou/Publications.html)

**Relevance:** Interference-aware placement with the same contention model as
Bubble-Up. The slides currently cite "Delimitrou 2014" — this is Quasar
specifically. Together with Mars et al. (2011), the contention-model
literature that the thesis refutes for pod-delete.

### Gan et al. (2019) — DeathStarBench
> Y. Gan et al. *An Open-Source Benchmark Suite for Microservices and Their
> Hardware-Software Implications for Cloud and Edge Systems.* ASPLOS 2019.
> DOI: `10.1145/3297858.3304013`.

- [Cornell PDF](https://www.csl.cornell.edu/~delimitrou/papers/2019.asplos.microservices.pdf)
- [ACM Digital Library](https://dl.acm.org/doi/10.1145/3297858.3304013)
- [GitHub (Delimitrou lab)](https://github.com/delimitrou/DeathStarBench)

**Relevance:** Dependency-graph-aware placement; cite for the
`dependency-aware` strategy's BFS partitioning logic.

### Burns et al. (2016) — Borg, Omega, and Kubernetes
> B. Burns, B. Grant, D. Oppenheimer, E. Brewer, J. Wilkes.
> *Borg, Omega, and Kubernetes.* ACM Queue 14, May 2016 (also Communications
> of the ACM 59(5), May 2016). DOI: `10.1145/2890784`.

- [ACM Queue](https://queue.acm.org/detail.cfm?id=2898444)
- [CACM article](https://cacm.acm.org/magazines/2016/5/201605-borg-omega-and-kubernetes/fulltext)
- [Google Research](https://research.google/pubs/borg-omega-and-kubernetes/)
- [ACM Digital Library](https://dl.acm.org/doi/10.1145/2890784)

**Relevance:** Slide 4 cites "Burns et al., ACM Queue 2016" in the
contention table (CPU contention row, attributed to Burns et al. 2016).
Slide 5 cites "Burns et al., ACM Queue 2016" on the Default-scheduler
strategy and "Borg (Verma 2015; Burns 2016)" on the Best-fit strategy.
Source for K8s design rationale and how Borg's scheduling lessons map onto
Kubernetes. Useful background for the slide-2 claim that the scheduler
optimizes for resource fit, not resilience.

### Cortez et al. (2017) — Resource Central
> E. Cortez, A. Bonde, A. Muzio, M. Russinovich, M. Fontoura, R. Bianchini.
> *Resource Central: Understanding and Predicting Workloads for Improved
> Resource Management in Large Cloud Platforms.* SOSP 2017.
> DOI: `10.1145/3132747.3132772`.

- [Microsoft Research](https://www.microsoft.com/en-us/research/publication/resource-central-understanding-predicting-workloads-improved-resource-management-large-cloud-platforms/)
- [ACM Digital Library](https://dl.acm.org/doi/10.1145/3132747.3132772)
- [PDF (fontoura.org)](https://fontoura.org/papers/ResourceCentral.pdf)

**Relevance:** Slide 5 cites "Worst-fit; Cortez 2017" on the Adversarial
strategy. Resource Central characterises Azure VM workloads and is the
canonical reference for resource-weighted hotspot / worst-fit placement
in production cloud settings. Cite alongside Mars 2011 for the adversarial
strategy's intentional asymmetric hotspot design.

### Karypis & Kumar (1998) — METIS
> G. Karypis, V. Kumar. *A Fast and High Quality Multilevel Scheme for
> Partitioning Irregular Graphs.* SIAM Journal on Scientific Computing,
> 20(1), 1998, pp. 359–392. Also: *Multilevel k-way Partitioning Scheme for
> Irregular Graphs.* Journal of Parallel and Distributed Computing, 48,
> 1998, pp. 96–129.

- [PDF (UT Austin)](https://www.cs.utexas.edu/~pingali/CS395T/2009fa/papers/metis.pdf)
- [SIAM J. Sci. Comput. abstract](https://users.cs.utah.edu/~hari/teaching/bigdata/SIREV41.00-Karypis.Kumar-Parallel.Multilevel.Graph.Partitioning.pdf)
- [GitHub (KarypisLab/METIS)](https://github.com/KarypisLab/METIS)

**Relevance:** The `dependency-aware` strategy's docstring in
[strategy.py:499](chaosprobe/chaosprobe/placement/strategy.py#L499) cites
METIS as the inspiration for balanced k-way graph partitioning. The
implementation is a lightweight BFS variant; METIS is the canonical
multilevel reference if a committee member asks "what's the relationship
to graph-partitioning literature?"

### Zhang et al. (2021) — Sinan
> Y. Zhang, W. Hua, Z. Zhou, G. E. Suh, C. Delimitrou.
> *Sinan: ML-Based and QoS-Aware Resource Management for Cloud
> Microservices.* ASPLOS 2021. DOI: `10.1145/3445814.3446693`.

- [Cornell PDF](https://www.csl.cornell.edu/~delimitrou/papers/2021.asplos.sinan.pdf)
- [ACM Digital Library](https://dl.acm.org/doi/10.1145/3445814.3446693)
- [arXiv:2105.13424](https://arxiv.org/abs/2105.13424)

**Relevance:** The `dependency-aware` strategy's docstring in
[strategy.py:500](chaosprobe/chaosprobe/placement/strategy.py#L500) cites
Sinan as related microservice-placement work. Sinan uses ML to predict
QoS impact of resource allocation across microservice tiers — a different
problem from ChaosProbe's chaos-injection evaluation, but the same
dependency-graph-aware mindset. Useful related-work citation.

---

## 2. Chaos engineering principles

### Basiri et al. (2016)
> A. Basiri, N. Behnam, R. de Rooij, L. Hochstein, L. Kosewski, J. Reynolds,
> C. Rosenthal. *Chaos Engineering.* IEEE Software, 33(3), 35–41, 2016.
> DOI: `10.1109/MS.2016.60`.

- [ACM Digital Library](https://dl.acm.org/doi/abs/10.1109/MS.2016.60)

**Relevance:** The principles-of-chaos-engineering foundational reference.
Cited on slide 2.

### Netflix (2011) — Chaos Monkey / Simian Army
> Netflix Technology Blog. *The Netflix Simian Army.* July 2011. Open-sourced
> as `Netflix/SimianArmy` (and later `Netflix/chaosmonkey`).

- [Netflix Tech Blog post](https://netflixtechblog.com/the-netflix-simian-army-16e57fbab116)
- [GitHub: Netflix/chaosmonkey](https://github.com/Netflix/chaosmonkey)

**Relevance:** The original production chaos tool — random instance termination
to force resilience. Cited on slide 4 as the historical precedent for
fault-injection-by-killing; ChaosProbe generalises the "kill" fault (pod-delete)
across controlled placement strategies rather than terminating at random.

### Yang et al. (2024) — MicroRes
> T. Yang, C. Lee, J. Shen, Y. Su, C. Feng, Y. Yang, M. R. Lyu.
> *MicroRes: Versatile Resilience Profiling in Microservices via Degradation
> Dissemination Indexing.* ISSTA 2024, Vienna. DOI: `10.1145/3650212.3652131`.

- [arXiv:2212.12850](https://arxiv.org/abs/2212.12850)
- [ACM Digital Library](https://dl.acm.org/doi/10.1145/3650212.3652131)
- [GitHub (authors)](https://github.com/yttty/MicroRes)

**Relevance:** Peer-reviewed (ISSTA 2024) chaos-engineering methodology that
ranks degradation by metric dissemination. Useful related-work positioning:
ChaosProbe varies *placement*; MicroRes varies *observability metric
weighting*.

### Kikuta et al. (2025) — ChaosEater
> D. Kikuta, H. Ikeuchi, K. Tajiri.
> *ChaosEater: LLM-Powered Fully Automated Chaos Engineering.* ASE 2025 NIER
> Track. (40th IEEE/ACM ASE.)

- [arXiv:2511.07865](https://arxiv.org/abs/2511.07865)

**Relevance:** Recent peer-reviewed chaos-engineering work (Nov 2025). LLM-
driven experiment design — orthogonal to ChaosProbe but useful for situating
the thesis in the 2025 chaos-engineering landscape.

---

## 3. Tail latency / recovery-time intuition (L3 source)

### Dean & Barroso (2013) — The Tail at Scale
> J. Dean, L. A. Barroso. *The Tail at Scale.* Communications of the ACM,
> 56(2), 74–80, February 2013. DOI: `10.1145/2408776.2408794`.

- [Barroso PDF](https://www.barroso.org/publications/TheTailAtScale.pdf)
- [CACM article](https://cacm.acm.org/research/the-tail-at-scale/)
- [ACM Digital Library](https://dl.acm.org/doi/10.1145/2408776.2408794)

**Relevance:** The "shared resources → latency variability → service-quality
damage" intuition. L3 distills this into the falsifiable claim that recovery
time predicts resilience score. The data refutes the simplest reading
(faster recovery → higher score), strengthening the case that what matters
is in-flight cross-node disruption rather than tail-of-recovery latency.

---

## 4. The churn-vs-contention mechanism (PRIMARY SUPPORT for the novel claim)

This is the most important section. The thesis's novel claim — that pod-delete
is a churn-based fault and the literature's placement intuitions don't transfer
— needs primary-source evidence for the mechanism. These references turn the
"kube-proxy / conntrack / CoreDNS reconvergence" claim from hand-waving into
documented behavior tracked by the Kubernetes project itself.

### Kubernetes SIG-Scalability — Network Programming Latency SLO
> Kubernetes Community. *Network Programming Latency SLO.* sig-scalability,
> kubernetes/community repo.

- [Official SLO spec](https://github.com/kubernetes/community/blob/master/sig-scalability/slos/network_latency.md)

**Relevance — MOST IMPORTANT CITATION FOR THE NOVEL CLAIM.** This is the
*official Kubernetes SLO* defining exactly the disruption window the thesis
claims is responsible for the refutations. From the spec:

> "Latency of programming in-cluster load balancing mechanism (e.g. iptables),
> measured from when service spec or list of its Ready pods change to when it
> is reflected in load balancing mechanism, measured as 99th percentile over
> last 5 minutes aggregated across all programmers."

The `network_programming_duration_seconds` Prometheus metric directly measures
the kill-cycle reconvergence delay. Cite on slide 12 to anchor the
"the kernel's TCP stack, conntrack table, kube-proxy iptables rules, and
CoreDNS cache all have to reconverge" claim.

### kubernetes/kubernetes Issue #82378
> *Investigate issues with Network Programming Latency SLO.* Maintainer-tracked
> issue, kubernetes/kubernetes.

- [GitHub Issue #82378](https://github.com/kubernetes/kubernetes/issues/82378)

**Relevance:** Primary-source documentation that the SLO disruption window is
a recognized correctness/reliability concern under pod churn.

### kubernetes/kubernetes Issue #100698
> *conntrack entries not cleared when switching service endpoints.*
> Maintainer-tracked issue, kubernetes/kubernetes.

- [GitHub Issue #100698](https://github.com/kubernetes/kubernetes/issues/100698)

**Relevance:** Primary-source documentation that established TCP connections
to the dead endpoint persist via stale conntrack entries during pod-delete.
This is the **exact mechanism** ChaosProbe's data is exposing. Cite alongside
#93143, #104098, #113203 for breadth.

### kubernetes/kubernetes Issues #93143, #104098, #113203
> Related conntrack / kube-proxy / endpoint-change issues documenting the
> same family of disruption mechanisms.

- [#93143 — conntrack entries not cleared when pod moves](https://github.com/kubernetes/kubernetes/issues/93143)
- [#104098 — Kubernetes doesn't clear conntrack entry for TCP](https://github.com/kubernetes/kubernetes/issues/104098)
- [#113203 — Connections stuck in conntrack when externalTrafficPolicy is "local"](https://github.com/kubernetes/kubernetes/issues/113203)

### kubernetes/kubernetes Issue #133474
> *Frequent churn between EndpointSlice objects.* Maintainer-tracked issue.

- [GitHub Issue #133474](https://github.com/kubernetes/kubernetes/issues/133474)

**Relevance:** EndpointSlice-level churn behavior sits one layer above
iptables/conntrack in the failure chain. Useful pre-emptive answer if a
committee member asks "why doesn't EndpointSlice batching mask the churn?"

### Versockas — Monitoring Kubernetes Controller Latency (practitioner blog)
> P. Versockas. *A Technique To Monitor Kubernetes Controller Latency.*

- [povilasv.me](https://povilasv.me/a-technique-to-monitor-kubernetes-controller-latency/)

**Relevance:** Practitioner write-up summarising the SLO and stating:
"Latency spikes correlate with pod churn, not application behavior." Sharp,
citable framing of the mechanism — secondary source, not peer-reviewed, but
useful for narrative.

---

## 5. Closest related work (positioning the contribution)

References that overlap with the thesis but cover different layers or scopes —
useful to demonstrate the contribution is complementary rather than redundant.

### Liu et al. (2025) — K8s Cloud-Edge Resilience Evaluation
> *Resilience Evaluation of Kubernetes in Cloud-Edge Environments via Failure
> Injection.* arXiv:2507.16109, July 2025.

- [arXiv:2507.16109](https://arxiv.org/abs/2507.16109)
- [HTML version](https://arxiv.org/html/2507.16109v1)

**Relevance — KEY POSITIONING CITATION.** Largest existing K8s failure-
injection dataset: 11,965 experiments. **Crucially, this paper does NOT:**
- distinguish churn-based from contention-based faults
- mention conntrack / kube-proxy as a mechanism
- compare placement strategies

So ChaosProbe is **complementary and orthogonal**. Cite on slide 4 with the
positioning: "the largest existing K8s resilience dataset does not
distinguish fault classes — ChaosProbe does, and the distinction matters."

### Barletta et al. (2024) — Mutiny! (DSN 2024)
> M. Barletta, M. Cinque, C. Di Martino, Z. T. Kalbarczyk, R. K. Iyer.
> *Mutiny! How does Kubernetes fail, and what can we do about it?*
> 54th IEEE/IFIP International Conference on Dependable Systems and
> Networks (DSN), 2024.

- [arXiv:2404.11169](https://arxiv.org/abs/2404.11169)

**Relevance:** Strongest peer-reviewed K8s-failure-analysis paper. Focuses on
cluster-state (etcd) corruption, not placement — so it's **complementary
rather than competing**. Worth citing on slide 4 alongside arXiv 2507.16109
as "the state of the art in K8s failure analysis." Quotable finding: "even a
single fault/error (e.g., a bit-flip) in the data stored can propagate"
causing cluster-wide failures, networking issues, and service
under/overprovisioning.

### Yang et al. (2024) — Network-Aware Reliability for Microservice Placement
> *Network-Aware Reliability Modeling and Optimization for Microservice
> Placement.* arXiv:2405.18001, 2024.

- [arXiv:2405.18001](https://arxiv.org/abs/2405.18001)

**Relevance:** Directly addresses placement-vs-reliability but via *modeling*
rather than empirical chaos injection. Useful contrast — they predict
service-failure reduction, ChaosProbe measures it under controlled fault
injection.

### Awad et al. (2025) — SLR on Chaos Experiments in Microservices
> *Chaos experiments in microservice architectures: A systematic literature
> review.* Journal of Systems and Software (JSS), 2025.

- [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S092054892500145X)

**Relevance:** Recent systematic literature review. Cite for related-work
positioning and to demonstrate that the literature is largely silent on
placement-strategy variation under chaos.

### Resilient Microservices SLR (2025)
> *Resilient Microservices: A Systematic Review of Recovery Patterns,
> Strategies, and Evaluation Frameworks.* arXiv:2512.16959, late 2025.

- [arXiv:2512.16959](https://arxiv.org/abs/2512.16959)

**Relevance:** Late-2025 SLR with a recovery-pattern taxonomy and Resilience
Evaluation Score (RES) checklist. Useful for situating ChaosProbe's scoring
methodology against community-standard evaluation frameworks.

### Hagedoorn et al. — Empirical Topology vs Microservice Performance
> *An Empirical Study on How Architectural Topology Affects Microservice
> Performance and Energy Usage.* arXiv:2604.00080.

- [arXiv:2604.00080](https://arxiv.org/abs/2604.00080)

**Relevance:** Empirical topology effects on microservice performance. A
methodology peer — they vary topology, ChaosProbe varies placement.

### "Priority Matters" — Constraint-Based Pod Packing (2025)
> *Priority Matters: Optimising Kubernetes Clusters Usage with Constraint-
> Based Pod Packing.* arXiv:2511.08373, 2025.

- [arXiv:2511.08373](https://arxiv.org/abs/2511.08373)

**Relevance:** Recent constraint-based K8s pod-packing work. Useful for
future-work discussion (RL-based placement policies, scheduler extensions).

---

## 6. Application benchmark

### Google Cloud Platform — microservices-demo (Online Boutique)
> *microservices-demo: Sample cloud-first application with 10 microservices
> showcasing Kubernetes, Istio, and gRPC.* Google Cloud Platform.

- [GitHub repository](https://github.com/GoogleCloudPlatform/microservices-demo)
- [Google Cloud documentation](https://cloud.google.com/service-mesh/docs/onlineboutique-install-kpt)

**Relevance:** The exact application used as the target workload. Source for
the slide-6 claim of "10 polyglot microservices + Redis cache" with Locust
load generator. The presentation should cite this directly rather than just
describing it.

---

## 7. Coverage gaps and notes

The Google Scholar pass surfaced **no peer-reviewed paper** that:
- frames pod-delete as a churn-based fault distinct from contention-based ones, or
- identifies kube-proxy / conntrack / EndpointSlice reconvergence as the
  dominant resilience-degrading mechanism under pod-delete on small clusters, or
- empirically compares 6+ placement strategies under chaos.

The closest peer-reviewed work — Mutiny! (DSN 2024), Liu et al. (arXiv 2025),
Yang et al. (arXiv 2024) — works at a different layer (etcd corruption), a
different scope (cloud-edge fault types without placement comparison), or uses
modeling rather than empirical measurement. The **churn-vs-contention
mechanism explanation appears to be a genuinely novel contribution** to the
peer-reviewed literature. This is worth stating explicitly on slide 4.

**Unverified references mentioned in code docstrings:**

The `_compute_dependency_aware` docstring in
[chaosprobe/placement/strategy.py:500](chaosprobe/chaosprobe/placement/strategy.py#L500)
also lists "μServe" and "Orca" as related microservice-placement work, but
neither was unambiguously locatable via web search. "Orca" matches the
OSDI 2022 transformer-serving paper (Yu et al.), which is unrelated to
microservice placement. "μServe" did not surface a clear academic source.
Before finalising the thesis report, either confirm these citations against
their original source (if known to the author) or remove them from the
docstring to avoid being asked at the defense about a reference you can't
support.

**Database coverage notes for the thesis report:**
- Searches went through general web search, arXiv, Semantic Scholar, and
  Google Scholar's surfaced results.
- For full peer-reviewed coverage, additionally search the ACM Digital
  Library, IEEE Xplore, and USENIX OSDI/NSDI/ATC 2023–2025 proceedings
  manually before finalising the thesis report. The marginal value is
  expected to be low — Scholar would have caught major peer-reviewed
  competitors if they existed.
- Industry talks (Linux Plumbers Conference, KubeCon + CloudNativeCon, SRECon)
  may contain documentation of the kill-cycle disruption mechanism that this
  bibliography does not include. Worth scanning those video archives for
  "kube-proxy programming latency," "endpoint propagation," and
  "conntrack reconvergence" if seeking additional grounding.
