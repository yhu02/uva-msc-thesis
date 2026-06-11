# ChaosProbe — References & Related Work

Annotated bibliography supporting the thesis defense. Each entry notes (a) the
full citation, (b) where to find it, and (c) how it relates to specific thesis
claims.

The thesis tests three literature-derived hypotheses (L1–L3). Under the
single-replica `pod-delete` **churn** regime studied here, the experimental data
finds all three **inapplicable in this regime** — they do not transfer to this
fault class, rather than being universally refuted (the contention literature
remains valid for the contention/multi-replica regimes it was written about).
The reason points at a single mechanism: pod-delete is a *churn-based* fault, not
a *contention-based* one, so the placement-vs-resilience intuition encoded in the
contention literature does not transfer to it. References below are organized by
which claim they support.

---

## 1. Pod placement affects resilience (L1 / L2 source material)

These references inform the *pre-experiment* hypotheses. They are the
literature-derived intuition the thesis tests and finds **inapplicable** to the
single-replica pod-delete fault class — it does not transfer to this regime, and
this is not a claim that the intuition is wrong in the contention/multi-replica
settings it was written for.

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
reference. Source for L2's predicted ordering. The thesis finds this prescription
**does not transfer** to single-replica churn faults — it remains valid for the
multi-replica availability regime Kubernetes designed topology spread for.
**H6-scoping precision (verified 2026-06-11):** describe Medea as
*"qualitative resilience motivation plus a trace-driven unavailability
analysis (15-day machine-unavailability traces; 16 %/24 % median/max
container-unavailability improvement vs J-Kube)"* — never "purely
qualitative". Its performance evaluation (HBase/TensorFlow, 400-node
pre-production cluster) and its resilience evaluation (synthetic LRAs over
unavailability traces) are SEPARATE experiments on different setups: Medea
never measures a latency-vs-blast-radius pair on the same workload under
the same placements, which is exactly the gap H5+H6 fill.

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
for the prediction that co-location should hurt resilience. **The thesis finds
this work's contention model does not apply to single-replica churn faults** —
its findings hold under genuine contention but not under pod-delete churn in this
setup.

### Delimitrou & Kozyrakis (2014) — Quasar
> C. Delimitrou, C. Kozyrakis.
> *Quasar: Resource-Efficient and QoS-Aware Cluster Management.* ASPLOS 2014.
> Salt Lake City, UT, March 2014.

- [Cornell PDF](https://www.csl.cornell.edu/~delimitrou/papers/2014.asplos.quasar.pdf)
- [Publications list](https://www.csl.cornell.edu/~delimitrou/Publications.html)

**Relevance:** Interference-aware placement with the same contention model as
Bubble-Up. The slides currently cite "Delimitrou 2014" — this is Quasar
specifically. Together with Mars et al. (2011), the contention-model
literature the thesis finds **inapplicable** to single-replica pod-delete (it
does not transfer to this churn fault class).

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
weighting*. Two sharper positioning points (verified against the full text):
(a) MicroRes performs **no** variance decomposition, ICC, power, or
test-retest reliability analysis of its index — H1's reliability critique is
the gap; but it reports 0.86–0.90 accuracy / 0.92–0.95 F1 for *binary*
resilient-vs-non-resilient classification (per-dataset tuned thresholds), so
H1 must stay scoped to "cannot rank placement strategies under session
variance", never "aggregate scores don't work". (b) MicroRes's premise —
resilience = degradation failing to disseminate from system to user metrics —
is conceptually adjacent to H3: MicroRes *scores* the decoupling; this thesis
*measures* it as a mechanism under a manipulated variable (placement).

### Kikuta et al. (2025) — ChaosEater
> D. Kikuta, H. Ikeuchi, K. Tajiri.
> *ChaosEater: LLM-Powered Fully Automated Chaos Engineering.* ASE 2025
> (40th IEEE/ACM Int'l Conf. on Automated Software Engineering), NIER track,
> pp. 3861–3865. DOI: `10.1109/ASE63991.2025.00331`.

- [arXiv:2511.07865](https://arxiv.org/abs/2511.07865) (the NIER paper — cite this, not the 114-page extended report arXiv:2501.11107)
- [DOI](https://doi.org/10.1109/ASE63991.2025.00331)
- [GitHub](https://github.com/ntt-dkiku/chaos-eater)

**Relevance:** Formally published peer-reviewed chaos-engineering work
(verified 2026-06-11; note NIER is a short-paper new-ideas track — cite it
as such). Automates the CE cycle with LLM agents on Kubernetes; full-text
verified to contain zero placement/scheduling/blast-radius/conntrack
content — adjacent lane, does not preempt H2/H5/H6.

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
time predicts resilience score. The data does **not support** this simplest
reading (faster recovery → higher score) in this regime — recovery's two-phase
split is itself unstable run-to-run — strengthening the case that what matters
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
claims is responsible for the contention intuitions not transferring to churn.
From the spec:

> "Latency of programming in-cluster load balancing mechanism (e.g. iptables),
> measured from when service spec or list of its Ready pods change to when it
> is reflected in load balancing mechanism, measured as 99th percentile over
> last 5 minutes aggregated across all programmers."

The `network_programming_duration_seconds` Prometheus metric is the SLO's own
measure of the kill-cycle reconvergence delay. ChaosProbe's prober queries it
(`kubeproxy_network_programming_p99`), but the run outputs' `metricAvailability`
map records it as **uncollected on the experiment cluster** (kube-proxy's
metrics endpoint was not scraped) — so this SLO anchors the mechanism
*conceptually* while the empirical reconvergence signal comes from the proxies
that were captured (`conntrack_entries_per_node`, `coredns_request_duration_p99`,
TCP retransmits). Cite on slide 12 to anchor the "the kernel's TCP stack,
conntrack table, kube-proxy iptables rules, and CoreDNS cache all have to
reconverge" claim.

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

### kubernetes/kubernetes Issues #48370, #108523, #126130, #129982 — active conntrack flush is UDP-only
> The kube-proxy conntrack-cleanup family of issues documenting *which*
> protocol's entries kube-proxy actively deletes on endpoint churn.

- [#48370 — flush stale UDP conntrack entries when endpoints change](https://github.com/kubernetes/kubernetes/issues/48370)
- [#108523 — conntrack entries removed on service/endpoint deletion are UDP & SCTP](https://github.com/kubernetes/kubernetes/issues/108523)
- [#126130 — conntrack reconciler; "UDP is the only protocol that requires this"](https://github.com/kubernetes/kubernetes/issues/126130)
- [#129982 — v1.32 netlink-reconciler regression: any UDP-port change triggered a full cleanup pass (since fixed)](https://github.com/kubernetes/kubernetes/issues/129982)

**Relevance — protocol-scopes the H2 mechanism (important).** Upstream
maintainers concluded that kube-proxy's *active* conntrack flush on endpoint
churn applies to **UDP only**; TCP entries are deliberately never actively
flushed and resolve via RST/teardown/timeouts (consistent with #100698 and
#104098 above). Online Boutique's east-west traffic is gRPC/**TCP**, so the
thesis must **not** attribute the measured flush (H2) to the kube-proxy flush
path alone: the candidate mechanisms for TCP-entry disappearance are
kernel-side teardown on pod-IP removal (RST/REJECT, CNI cleanup, state
expiry), with the UDP/DNS flush path as a contributor. Note also that
kube-proxy conntrack behaviour changed materially across v1.31–v1.32 (the
netlink reconciler) — always pin the cluster's Kubernetes version when citing
this mechanism. Round-2 verification (2026-06-11) added: the UDP-only
property is verified across **both** implementations — the exec-based
cleaner (≤v1.31, `conntrack -D … -p udp`, visible verbatim in
[#125467](https://github.com/kubernetes/kubernetes/issues/125467) logs) and
the netlink reconciler (≥v1.32,
[`cleanup.go`](https://github.com/kubernetes/kubernetes/blob/master/pkg/proxy/conntrack/cleanup.go)
filtering `protocol != UDP`); pre-reconciler kube-proxy also flushed SCTP
(treated like UDP — irrelevant here, no SCTP). Phrase as a verified property
of all existing call sites, **not** an API guarantee.

### Linux nf_conntrack TCP state-machine timeouts (H2 teardown semantics)
> *Netfilter conntrack sysctl reference.* kernel.org documentation.

- [docs.kernel.org/networking/nf_conntrack-sysctl.html](https://docs.kernel.org/networking/nf_conntrack-sysctl.html)

**Relevance — the required hedge on "kernel-side teardown".** TCP conntrack
entries are not destroyed instantly on close; they transition into
short-timeout states: `nf_conntrack_tcp_timeout_close` = 10 s,
`_close_wait` = 60 s, `_fin_wait` = 120 s, `_time_wait` = 120 s — vs
`_established` = 432,000 s (5 days). The probe's −28 %/−21 % within-cycle
TCP drops are therefore phrased as *"FIN/RST-driven transition into
close states expiring ≤ 120 s"*, not instantaneous deletion; abruptly
severed flows without a sequence-valid close can linger in `ESTABLISHED`
(modern kernels lower the timeout only for sequence-valid RSTs), which is
why the drops are partial.

### Kubernetes DNS conntrack races (background for the UDP/DNS pool)
> kubernetes/kubernetes [#56903](https://github.com/kubernetes/kubernetes/issues/56903)
> ("DNS intermittent delays of 5s", 2017–2019); XING engineering,
> [*A reason for unexplained connection timeouts on Kubernetes/Docker*](https://tech.xing.com/a-reason-for-unexplained-connection-timeouts-on-kubernetes-docker-abd041cf7e02);
> Pumputis (Weaveworks), *Racy conntrack and DNS lookup timeouts* (site
> defunct — use an archive.org link).

**Relevance:** the canonical operational literature establishing that
Kubernetes DNS traffic is a major UDP-conntrack workload with well-known
pathologies. **Two distinct races — do not conflate:** (a) the same-socket
conntrack insert/confirm race on parallel A/AAAA queries (Pumputis;
kernel fix in Linux 5.0; mitigated by NodeLocal DNSCache), diagnosed via
the rising `insert_failed` counter; (b) the SNAT port-allocation race that
`--random-fully` addresses (XING). Cited as background for why the
standing UDP pool is DNS-dominated; the thesis's placement-dependent
pool-size observation itself has no prior description in the literature
checked (claimed as observation, scoped to this environment).

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

### Wojciechowski et al. (2021) — NetMARKS
> K. Wojciechowski et al. *NetMARKS: Network Metrics-AwaRe Kubernetes
> Scheduler Powered by Service Mesh.* IEEE INFOCOM 2021.
> DOI: `10.1109/INFOCOM42981.2021.9488670`.

- [IEEE Xplore](https://ieeexplore.ieee.org/document/9488670/)

**Relevance — H5's directional precedent; word H5's novelty against it.**
A Kubernetes scheduler extender using *runtime* Istio service-mesh traffic
metrics; reduces application response time up to 37% and inter-node bandwidth
up to 50% by network-aware colocation. Establishes empirically that colocation
lowers latency — the direction H4/H5 replicate. The difference: NetMARKS
*optimizes* using runtime telemetry; H5 *validates a static, pre-chaos
graph-derived two-regime separator* — the cross-node dependency-edge
fraction separates node-local from spreading placements and that separation
predicted the east-west tail in two independent batches (batch-1 ρ = 0.79
collapsed to ρ = 0.25 in batch 2; claim the replicated separation, never
ρ = 0.79 alone). Claim the empirical validation of the static separator as
the contribution — not the locality concept, which NetMARKS (and the
graph-partitioning placement literature, e.g. TraDE below) already own as an
optimization objective. Note it reports end-to-end response time, not
east-west p95 — directional precedent, not like-for-like.

### TraDE (2024) — cross-node traffic-aware placement
> *TraDE: Network and Traffic-aware Adaptive Scheduling for Microservices
> Under Dynamics.* arXiv:2411.05323, 2024.

- [arXiv:2411.05323](https://arxiv.org/abs/2411.05323)

**Relevance:** Recent placement work using cross-node traffic/edge weight as
an explicit scheduling objective — co-cite with NetMARKS when scoping H5's
novelty to the *empirical validation* of the static metric.

### Cast (ICSE 2026) — production resilience testing at Huawei Cloud
> *Cast: Automated resilience testing via online traffic record-and-replay
> with fault injection.* ICSE 2026 SEIP track. arXiv:2602.00972.

- [arXiv (HTML)](https://arxiv.org/html/2602.00972v1)

**Relevance:** The nearest 2026 industrial resilience-testing system — eight
months in production, 137 potential vulnerabilities (89 confirmed) via
traffic replay and application/RPC-level fault injection. Positioning:
Cast finds *application-layer fault-handling bugs*; this thesis measures
*infrastructure-layer placement mechanisms* — no placement, scheduling,
kernel/network-mechanism, or blast-radius content.

### ACM CSUR multi-vocal chaos-engineering review (2024)
> *Chaos Engineering: A Multi-Vocal Literature Review.* arXiv:2412.01416;
> ACM Computing Surveys. DOI: `10.1145/3777375`.

- [arXiv:2412.01416](https://arxiv.org/abs/2412.01416)
- [ACM Digital Library](https://dl.acm.org/doi/10.1145/3777375)

**Relevance — gap evidence.** Field synthesis through April 2024 (≈90
sources): defines chaos outcomes solely against steady-state user-visible
indicators (latency, error rate, throughput, availability); contains no
placement, kernel/scheduler, conntrack, or EndpointSlice content, and its
open-research-issues section is organizational (culture, skills, resources),
not statistical rigor of resilience metrics. Cite (together with the
2512.16959 SLR above) as evidence the thesis's placement-aware, cross-layer,
metric-reliability angle is absent from the consolidated literature —
phrased as a *specific* gap, not a field-wide void.

### AWS Well-Architected — cell-based architecture (blast radius)
> *Reducing the Scope of Impact with Cell-Based Architecture.* AWS
> Well-Architected whitepaper.

- [AWS documentation](https://docs.aws.amazon.com/wellarchitected/latest/reducing-scope-of-impact-with-cell-based-architecture/reducing-scope-of-impact-with-cell-based-architecture.html)

**Relevance — H6's practitioner anchor.** The industry articulation of the
qualitative principle H6 quantifies: concentrating workload into one failure
domain enlarges the scope of impact ("blast radius") when that domain fails.
Position H6 as the controlled, measured quantification of this known
trade-off on the placement axis (same placements, opposing latency vs
availability gradients with H5) — not as discovering the trade-off.

---

## 6. Methodology precedents (H1's statistical framing)

### Maricq et al. (2018) — Taming Performance Variability
> A. Maricq, D. Duplyakin, I. Jimenez, C. Maltzahn, R. Stutsman, R. Ricci.
> *Taming Performance Variability.* OSDI 2018, pp. 409–425.

- [USENIX page](https://www.usenix.org/conference/osdi18/presentation/maricq)
- [PDF](https://www.usenix.org/system/files/osdi18-maricq.pdf)

**Relevance — the stylistic model for the H1 chapter.** The venue-accepted
precedent for "quantify the noise, prescribe the repetitions": a ~900,000-
data-point, 835-server campaign showing supposedly identical hardware varies
run-to-run, undermining quantitative system comparison, with CONFIRM
recommending repetition counts from historical variability. Cite as the
*argument-shape* precedent for H1's ICC + power analysis — with precision:
CONFIRM uses nonparametric CI-width stopping, **not** formal hypothesis-test
power analysis, and "blocked design" is this thesis's term, not the paper's.

### Mytkowicz et al. (2009) — Producing Wrong Data Without Doing Anything Wrong!
> T. Mytkowicz, A. Diwan, M. Hauswirth, P. F. Sweeney. ASPLOS 2009.

- [Semantic Scholar](https://www.semanticscholar.org/paper/3886c40229b3de318de668e0c0f4202079eb6f55)

**Relevance:** The classic measurement-bias paper — co-cite with Maricq so the
variability-methodology lineage doesn't start in 2018.

### Hoefler & Belli (2015) — Scientific Benchmarking of Parallel Computing Systems
> T. Hoefler, R. Belli. SC 2015. DOI: `10.1145/2807591.2807644`.

- [ACM Digital Library](https://dl.acm.org/doi/10.1145/2807591.2807644)

**Relevance:** Rigorous-benchmarking methodology (report distributions and
CIs, not means) — third leg of the H1 methodology lineage.

### TOST / equivalence-testing package (H3's "evidence of absence")
> D. J. Schuirmann. *A comparison of the two one-sided tests procedure and
> the power approach for assessing the equivalence of average
> bioavailability.* J. Pharmacokinetics & Biopharmaceutics 15, 1987.
> DOI: `10.1007/BF01068419`.
> D. Lakens. *Equivalence Tests: A Practical Primer for t Tests,
> Correlations, and Meta-Analyses.* Social Psychological and Personality
> Science 8(4), 2017. [DOI](https://journals.sagepub.com/doi/10.1177/1948550617697177)
> D. Lakens, A. M. Scheel, P. M. Isager. *Equivalence Testing for
> Psychological Research.* AMPPS 1(2), 2018.
> [DOI](https://journals.sagepub.com/doi/10.1177/2515245918770963)
> A. Benavoli, G. Corani, J. Demšar, M. Zaffalon. *Time for a Change: a
> Tutorial for Comparing Multiple Classifiers Through Bayesian Analysis.*
> JMLR 18(77), 2017. [paper](https://jmlr.org/papers/v18/16-305.html)

**Relevance (verified 2026-06-11):** no native TOST precedent exists in
systems-performance venues — the thesis says so explicitly and imports the
procedure from biostatistics (Schuirmann 1987 original; Lakens 2017 as the
practical primer, incl. the verbatim point that researchers "often
incorrectly conclude an effect is absent based [on] a nonsignificant
result"). Benavoli et al. is the peer-reviewed CS-venue precedent for
*affirmatively accepting* practical equivalence (ROPE; accepts equivalence
in 22 % of NHST non-rejections in its case study — "impossible with the
NHST"). TOST requires a pre-declared SESOI (Lakens/Scheel/Isager): ours is
|ρ| = 0.3, fixed in the committed analysis code before the campaign. Do
**not** cite Furia/Feldt/Torkar (TSE 2021) as a TOST/ROPE-procedure
precedent — verified to contain neither; citable only for the NHST critique.

### KEP-895 — Pod Topology Spread (upstream trade-off rationale)
> Kubernetes Enhancement Proposal 895, sig-scheduling.

- [KEP-895](https://github.com/kubernetes/enhancements/tree/master/keps/sig-scheduling/895-pod-topology-spread)

**Relevance — H6 scoping citation.** The upstream design rationale for
spreading motivates "high availability" qualitatively; its only
quantitative criteria are **scheduler-internal** (plugin execution-latency
p90 ≤ 100 ms plus two scheduler-log error-frequency SLOs — phrase it that
way, not "only plugin latency"). Establishes that the
availability-vs-performance trade-off is asserted but unquantified
upstream — part of the H6 "qualitatively known, quantified here" scoping
triplet with Medea (§1) and AWS cell placement (§5).

---

## 7. Application benchmark

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

## 8. Coverage gaps and notes

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

**Removed unverifiable docstring citations:**

The `_compute_dependency_aware` docstring previously listed "μServe" and "Orca"
as related microservice-placement work, but neither was unambiguously locatable
("Orca" matches the unrelated OSDI 2022 transformer-serving paper by Yu et al.;
"μServe" surfaced no clear academic source), so both were removed from the code
docstrings. The retained dependency-aware citations — DeathStarBench, Sinan, and
METIS — are all verifiable above.

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
