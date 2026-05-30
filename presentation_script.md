# ChaosProbe -- Presentation Script

Speaker notes for the thesis defense presentation.

---

## Slide 1 -- Title

> Good morning/afternoon. My name is Yvo Hu, and today I will present my MSc thesis: "Measuring the Impact of Chaos in Differing Placement Strategies within Cloud Systems." The tool I built is called ChaosProbe.

---

## Slide 2 -- Background & Motivation

> Modern applications are built as microservices -- decomposed services that communicate over the network and are orchestrated by Kubernetes. This architecture enables independent scaling, but it also means failures can cascade between services.
>
> The key problem is that the Kubernetes scheduler optimizes for *resource fit* -- packing pods efficiently onto nodes -- but it does not optimize for *resilience or fault isolation*. Pod placement determines which services share a node's CPU, memory, disk, and network. When services are co-located, a fault in one service can degrade its neighbours.
>
> Chaos engineering is the discipline of deliberately injecting faults to build confidence in a system's resilience. However, existing work studies placement *or* resilience, but rarely their interaction under controlled fault injection. There is no systematic framework that quantifies how placement topology affects chaos resilience.
>
> ChaosProbe fills this gap. It is an automated framework that varies placement strategies, injects faults via LitmusChaos, and measures the impact across four dimensions: recovery time, inter-service latency, resource utilization, and I/O throughput.

---

## Slide 3 -- Research Question & Hypotheses

> Our research question is: *How do multi-dimensional metrics decompose chaos response across pod-placement strategies in Kubernetes?*
>
> The framing is deliberate. Earlier work asks which placement strategy is most resilient and collapses the answer into a single aggregate score. We instead ask what each metric dimension uniquely reveals -- and what we can claim with confidence at this scale. We test ten hypotheses (H1–H10), grouped into four themes; I will walk each theme at a high level. Separately, three *legacy literature predictions* -- **L1** (colocate is the worst), **L2** (spread gives the best fault isolation), and **L3** (recovery time predicts resilience) -- are the contention-model expectations the results slides put to the test. I label these L-, distinct from the ten H-hypotheses, to keep the two families clear.
>
> **Theme 1 -- Instrument design (H1 through H3).** These hypotheses justify ChaosProbe's measurement architecture from first principles.
>
> **H1: Metrics carry disjoint information.** No single metric captures the full chaos response. Best-fit and spread tie on aggregate at 66.3, but they diverge on recovery time -- 1452 versus 1617 milliseconds -- and on during-chaos latency -- 100 versus 150 milliseconds. A single-metric framework would mis-classify them as equivalent.
>
> **H2: Phase decomposition is necessary.** Pre-chaos baselines themselves vary by placement. The spread strategy has a homepage latency of 231 milliseconds before chaos starts; colocate sits at 78. That is a 3× gap with no fault injected at all. During-chaos absolute values are uninterpretable without phase-aware normalisation.
>
> **H3: Per-pod granularity reveals heterogeneity.** Cross-pod stddev within a single service is non-zero and placement-dependent. Aggregate cluster metrics hide this; ChaosProbe's per-pod sampling exposes it via the LatencyProber's `meanCrossNodeStddev_ms` field.
>
> **Theme 2 -- Baseline and tail (H4 and H5).**
>
> **H4: Baseline predicts resilience.** Strategies with lower pre-chaos variance score higher under chaos. The resting-state fingerprint predicts the failure response before any fault is injected -- a result with real operational value, since operators can run the diagnostic without paying the cost of a chaos experiment.
>
> **H5: Mean masks tail.** p95 latency ranking across strategies differs from mean ranking. Tail percentiles concentrate placement effects -- this is exactly the warning from Dean and Barroso's *Tail at Scale*. Aggregate-mean reasoning would mislead operators about which strategies are at risk.
>
> **Theme 3 -- Mechanism (H6, H7, H8).** These hypotheses test the churn-vs-contention story directly with primary-source metrics.
>
> **H6: Cross-pod variance predicts leakage.** Pre-chaos cross-node stddev forecasts whether a strategy lands in the containment cluster -- with 3 to 17 percent conntrack flushing during chaos -- or the leakage cluster, with 30 to 49 percent.
>
> **H7: CPU throttling refutes the contention model.** Under pod-delete, colocate throttles less (0.89) than spread (1.52), the opposite of what Bubble-Up's contention prediction expects. Mars 2011 does not fit churn-based faults. This is a clean refutation of the contention model using the contention literature's own metric.
>
> **H8: SLO and conntrack signal leakage.** The Kubernetes Network Programming Latency SLO measures the churn mechanism directly. Conntrack flushing during chaos correlates one-to-one with the cluster split: spread loses 49 percent of pre-chaos entries, dependency-aware loses just 3 percent.
>
> **Theme 4 -- Recovery dynamics (H9 and H10).**
>
> **H9: Scheduling latency dominates recovery.** Recovery time decomposes into (deletion → scheduled) plus (scheduled → ready). Placement affects only the first term; the second is application-bound and approximately constant across strategies. This explains why aggregate recovery time is a poor predictor of resilience -- the placement-sensitive component is buried inside a larger placement-invariant one.
>
> **H10: Post-chaos asymmetry is a placement fingerprint.** Post-chaos metrics overshoot, undershoot, or stabilise differently per strategy. Redis throughput varies between 50 percent and 450 percent of pre-chaos values across strategies post-recovery. The pattern of asymmetry is itself a fingerprint of how each placement responds to fault recovery -- and most chaos work measures only during-fault impact, missing this entirely.
>
> All ten hypotheses are testable from data ChaosProbe already collects. The baseline run -- a trivial pod-cpu-hog under default scheduling -- sits outside the hypothesis set as a methodology control: a 100 percent score with zero variance validates the probes and scoring before any of these claims can be evaluated.

---

## Slide 4 -- Related Work

> The placement side of our work builds on systems like Google's Borg for resource-aware bin-packing, Medea for topology spread constraints, Sparrow for decentralized scheduling, and DeathStarBench for dependency-graph-aware placement.
>
> On the chaos engineering side, we build on the Principles of Chaos Engineering by Basiri et al., and use LitmusChaos as our fault injection engine -- it is a CNCF sandbox project that operates via ChaosEngine CRDs.
>
> The gap is clear: existing work studies placement *or* resilience, but no framework systematically varies placement under controlled chaos and measures the interaction across multiple dimensions. ChaosProbe bridges this gap with 6 strategies, 2 fault types, and 4 metric dimensions, all stored in a Neo4j graph that preserves the causal topology.
>
> The table shows our literature-informed hypotheses: colocating pods shares cores leading to CPU throttling, shares memory leading to evictions, shares the network stack increasing latency, and shares disk bandwidth reducing I/O throughput.

---

## Slide 5 -- Placement Strategies

> We test 8 placement configurations. The independent variable is the placement strategy.
>
> **Baseline** is our control group -- default scheduler with a trivial fault (1% CPU for 1 second). We expect a 100% score to validate the methodology.
>
> **Default** uses the standard Kubernetes scheduler with full chaos injection. This is the placement null hypothesis -- what happens when we let the scheduler decide.
>
> **Colocate** pins all pods to a single node via `nodeSelector` (every deployment gets the same `kubernetes.io/hostname`). This is maximum contention and we expect the worst resilience.
>
> **Spread** distributes pods evenly across workers via per-node `nodeSelector` assignment. This is minimum contention and we expect the best fault isolation.
>
> **Random** uses a seeded random assignment per deployment. It is reproducible and serves as a null baseline for topology effects.
>
> **Adversarial** places the resource-heaviest pods on one node, creating an intentional hotspot. This is worst-fit scheduling.
>
> **Best-fit** uses bin-packing to concentrate pods into the fewest nodes, similar to Borg-style resource scoring.
>
> **Dependency-aware** co-locates communicating services via BFS partitioning of the service dependency graph.

---

## Slide 6 -- Experimental Setup

> Our target application is Google's Online Boutique -- 10 polyglot microservices plus a Redis cache, written in Go, C#, Python, Java, and Node.js. We run a single replica per service, which means a 100% pod-delete causes full unavailability of that service.
>
> The cluster runs on Proxmox with KVM/QEMU virtualization. We have 5 nodes:
> - **cp1**: the control plane, 2 vCPU and 2 GiB RAM -- it only runs infrastructure (Prometheus, Neo4j, ChaosCenter, metrics-server)
> - **w1 through w4**: 2 vCPU, 4 GiB RAM each -- uniform workers
> - Total: 10 vCPU, 18 GiB across the cluster
> - Running Kubernetes v1.28.6 with Calico CNI and containerd 1.7.11
>
> The placement matrix uses a single fault: **pod-delete** targeting productcatalogservice -- a central service in the dependency graph. Total chaos duration is 120 seconds, with deletions every 15 seconds (CHAOS_INTERVAL), FORCE=true, and PODS_AFFECTED_PERC=100. Resilience is evaluated by 7 httpProbes across 4 sensitivity tiers, plus 5 Rust cmdProbes for orthogonal signals.
>
> The baseline strategy swaps pod-delete for a trivial pod-cpu-hog on the same target -- 1 second duration, 1% CPU load on 0 cores -- so no pods are actually killed. All probes execute identically, and we expect a 100% score with zero recovery cycles. This validates the methodology.
>
> Infrastructure includes LitmusChaos for fault injection, Prometheus for cluster metrics, Neo4j for graph storage with 14 node types and 18 relationships, and Locust generating steady-state load at 50 users and 10 requests per second.

---

## Slide 7 -- System Architecture

> ChaosProbe has six core components:
>
> The **PlacementEngine** implements our 6 strategies by mutating the nodeSelector on each deployment to target specific nodes.
>
> **MetricsCollection** runs 6 continuous probers as background threads: recovery, latency, resources, Redis, disk, and Prometheus.
>
> **ResultAggregation** collects ChaosResult CRDs and probe verdicts, computes resilience scores, and tracks phases.
>
> The **Orchestrator** coordinates the full experiment lifecycle: strategy runner, run phases, preflight checks, and port-forward management.
>
> **GraphStorage** writes everything to Neo4j -- 14 node types, 18 relationships, all queryable via Cypher.
>
> **Visualization** produces matplotlib charts, an HTML report with appendix, and ML export in CSV or Parquet.
>
> Below, the infrastructure layer shows the existing tools we integrate: LitmusChaos, Prometheus, Neo4j, Kubernetes, Locust, and Proxmox.
>
> The experiment lifecycle follows 5 steps: configure (load and validate YAML), place (apply strategy by patching nodeSelector), inject chaos (ChaosEngine via ChaosCenter), measure (6 probers plus load generator), and store (Neo4j sync, charts, and export).

---

## Slide 8 -- Measurement Design

> Measurement happens in three phases:
> - **PreChaos** (60 seconds, the default `--settle-time`) -- establish steady-state baselines
> - **DuringChaos** (120 seconds for pod-delete) -- fault is active
> - **PostChaos** (60 seconds) -- observe recovery behaviour
>
> Six probers run alongside the experiment. Five of them extend `ContinuousProberBase` and sample on fixed intervals; the sixth, RecoveryWatcher, is event-driven.
> - **RecoveryWatcher** uses the Kubernetes Watch API to observe pod lifecycle events in real-time. The recovery summary splits the total interval into deletion-to-scheduled (a scheduler-side metric) and scheduled-to-ready (a container start-up metric), so analysis can attribute recovery stalls correctly.
> - **LatencyProber** measures HTTP route latency every 3.5 seconds by executing requests from every eligible pod in parallel via kubectl exec, recording per-pod and per-node variance alongside the aggregate.
> - **RedisProber** and **DiskProber** measure I/O throughput every 10 seconds via redis-cli and dd respectively.
> - **ResourceProber** fetches node and pod CPU and memory from the Metrics API every 5 seconds -- only across nodes hosting namespace pods.
> - **PrometheusProber** collects two query families every 10 seconds: application-side (`pod_ready`, CPU throttle, memory, network) and the *churn-mechanism* metrics that map directly to the K8s SIG-Scalability Network Programming Latency SLO -- `kubeproxy_network_programming_p99`, `kubeproxy_sync_proxy_rules_p99`, `coredns_request_duration_p99`, `conntrack_entries_per_node`, and TCP retransmit rates. These directly measure the kernel-reconvergence mechanism that the discussion identifies as the dominant resilience-degrading effect.
>
> For resilience probes, we use 7 LitmusChaos httpProbes across 4 sensitivity tiers, all in Continuous mode:
> - **Tier 1 -- Strict** (2 probes, 2s interval, 3s timeout, 1 retry): the product page and homepage; expected to fail under 100% pod-delete and confirm chaos has impact.
> - **Tier 2 -- Moderate-tight** (2 probes, 5s interval, 3s timeout, 4 retries): pass only when recovery is fast.
> - **Tier 3 -- Moderate-loose** (2 probes including the cart route, 6s interval, 5s timeout, 4 retries): pass when recovery is moderate.
> - **Tier 4 -- Control** (1 probe, /_healthz, 4s interval, 5s timeout, 3 retries): pure infrastructure health; failure means severe node-level pressure.
>
> Alongside these we run 5 Rust cmdProbes -- check-redis, check-http-latency, check-dns-latency, check-tcp-connect, check-cart-flow -- which capture Redis collateral damage, post-chaos HTTP latency, DNS resolution time, TCP connect time, and a multi-route user journey.
>
> The resilience score is the mean probe success percentage across all probes, on a 0-100 scale. The verdict is PASS only if every probe passes. We also report a 95% bootstrap confidence interval on the mean, a 25th-percentile score, and a harmonic-mean variant that penalises low-score iterations more strongly -- per Dean and Barroso's *Tail at Scale* (2013), means alone hide what matters at the tails. For cross-strategy comparison we run pairwise Mann-Whitney U with Holm-Bonferroni correction so the family-wise error rate is controlled across the 28 pairs in the strategy matrix.

---

## Slide 9 -- Results: Resilience Scores

> The results refute the hypotheses, but the methodology holds. The control held first: **baseline achieved 100% with standard deviation zero across three iterations** -- the probes and scoring work as designed, so the refutations are real signal, not measurement artefacts.
>
> The non-baseline strategies do not produce a monotonic ordering. Instead they bifurcate into two clusters, with a clear gap in the middle.
>
> The **containment cluster** sits at 83.0% with zero variance, and contains four strategies: colocate, random, dependency-aware, and adversarial. For every iteration in this cluster, only the two strict-tier probes fail -- the ones that directly target productcatalogservice and are expected to fail under any 100% pod-delete fault. Every other probe -- moderate, loose, healthz, and the five Rust cmdProbes -- passes.
>
> The **leakage cluster** sits at 49.7% to 66.3% with standard deviation around 29. Spread and best-fit average 66.3%, with collateral damage to non-target probes in about one iteration in three. Default averages 49.7%, with collateral damage in two iterations out of three.
>
> L1 predicted colocate would be the worst. Colocate ties for the top non-baseline score. L2 predicted spread would be the best. Spread sits in the leakage cluster. Both predictions are refuted -- but the more interesting observation is that the distinction between "containment" and "leakage" is not about density. Colocate and adversarial are dense; random and dependency-aware are not, yet all four are in the containment cluster. The dividing line is something else, which the discussion slide takes up.

---

## Slide 10 -- Results: Recovery Time & Latency

> Recovery time -- measured as the interval from pod deletion to pod ready -- is where L3 gets tested directly.
>
> The fastest recovery was **random at 1120 milliseconds**, followed by dependency-aware (1258), colocate (1336), adversarial (1362), default (1387), best-fit (1452), and spread (1617). The full spread is just 500 milliseconds end-to-end -- a tight band of 1.1 to 1.6 seconds across all six placement strategies.
>
> L3 predicted that faster recovery should yield higher resilience scores. The data does not support this. Random had the *fastest* recovery (1120ms) and tied for the *best* score (83.0). Spread had the *slowest* recovery (1617ms) and sat in the leakage cluster (66.3). But dependency-aware (1258ms, score 83) and default (1387ms, score 50) are within 130 milliseconds of each other on recovery yet 33 points apart on score. Across the six main strategies, recovery time and resilience score are essentially uncorrelated -- L3 is refuted.
>
> The lesson is that recovery speed is not the bottleneck under pod-delete. What matters is whether the chaos target's *in-flight network traffic* during the kill cycle has to cross node boundaries that are reconverging.
>
> Latency degradation between pre-chaos and during-chaos points at the same mechanism. **Colocate's during-chaos homepage latency was 83 milliseconds** -- the lowest of all strategies. Best-fit was 100, random 110, dependency-aware 111, default 113, spread 150, and adversarial 168. The during-chaos latency does not move monotonically with the score either, but the strategies that produce the cleanest probe outcomes are also the ones that hold their latency lowest during the kill cycle. The bifurcation between containment and leakage is the dominant signal, and the discussion slide explains the mechanism.

---

## Slide 11 -- Results: Resources & Throughput

> For resource utilization -- note that these metrics only aggregate across nodes actually hosting application pods, not idle nodes:
>
> **Colocate shows the highest CPU throttling**. All services compete for shared cores on a single node.
>
> **Spread shows the most stable resource usage**. Dedicated per-node capacity minimizes contention.
>
> Memory pressure correlates with placement density -- more pods per node means more memory competition.
>
> For I/O throughput:
>
> **Disk I/O degrades under colocate** due to shared disk bandwidth across all services on one node.
>
> **Redis throughput** varies with network locality. Interestingly, co-locating cart and redis-cart may actually benefit from low network latency, though overall contention still hurts.
>
> **Spread shows consistent throughput** with isolated I/O paths per node.

---

## Slide 12 -- Discussion

> All three literature-derived predictions (L1–L3) are refuted, but the more interesting observation is *how* the refutations group themselves.
>
> **L1 -- Colocate is the worst: refuted.** Colocate tied for the top non-baseline score at 83.0 with standard deviation zero -- the same score as random, dependency-aware, and adversarial. It is in the containment cluster, not at the bottom.
>
> **L2 -- Spread provides the best fault isolation: refuted.** Spread scored 66.3 with standard deviation 29, sitting in the leakage cluster. Default scored worse, at 49.7. Spread is neither best nor uniquely worst -- it just leaks more often than the containment cluster does.
>
> **L3 -- Recovery time predicts resilience: refuted.** Mean recovery time across the six main strategies clusters tightly between 1.1 and 1.6 seconds, yet resilience scores split into a bimodal distribution. Random has the fastest recovery and the top score, but the relationship breaks down across the rest of the strategies. Recovery speed and resilience are essentially uncorrelated.
>
> The methodology control held -- baseline scored 100 with zero variance -- so these refutations are signal, not noise.
>
> The real story the data tells is **not a monotonic placement-vs-resilience curve**. It is a **bifurcation**. Strategies fall into two clusters with a clear gap. The containment cluster -- colocate, random, dependency-aware, adversarial -- has only the directly-targeted probes failing. The leakage cluster -- spread, best-fit, default -- has collateral damage to non-target probes. The question is what separates them.
>
> The explanation comes from what pod-delete actually does. The placement literature builds its predictions on **contention-based faults**: CPU hogs, memory hogs, disk I/O stress. Under those faults, co-locating services causes shared resources to compete and degrade, and spreading services across nodes genuinely helps. The intuition behind L1, L2, and L3 all comes from this contention model.
>
> **Pod-delete is not a contention-based fault. It is a churn-based fault.** Every 15 seconds, the productcatalogservice pod is killed and a new one is scheduled. The kernel's TCP stack, conntrack table, kube-proxy iptables rules, and CoreDNS cache all have to reconverge to the new pod IP. The Kubernetes project tracks this directly -- it is the `network_programming_duration_seconds` SLO -- and while it is reconverging, every cross-node hop *to or through* the affected node is briefly disrupted.
>
> Which cluster a strategy lands in depends on whether its placement happens to confine the chaos target's network path to a small set of nodes that probes also use. Colocate confines it trivially -- everything is on one node. Adversarial groups heavy pods together, often including the target. Dependency-aware places dependencies on the same node by construction. Random landed lucky in this run, with the target on a node whose path was mostly intra-node from the probes' perspective.
>
> Spread, best-fit, and default leave the target's network path spread across the cluster. When the target's pod IP churns, more cross-node routes are disrupted, and more probes fail.
>
> Three insights follow:
>
> First, **the failure mode is bimodal, not monotonic**. Aggregate placement-vs-resilience curves hide the underlying bifurcation. Operators should not think "more spread = more resilient"; they should think "is the chaos target's network path confined or exposed."
>
> Second, **placement-vs-resilience intuition is fault-class-specific**. The literature is built on contention-based faults, and its conclusions do not transfer to churn-based faults. This is a publishable gap.
>
> Third, **containment is what predicts the outcome**, not density and not recovery speed. The right placement strategy for a churn-prone service is one that keeps the service's network path inside a small, well-controlled blast radius.

---

## Slide 13 -- Threats to Validity

> For internal validity:
>
> Our results are based on a single application -- Google Online Boutique. It is a representative benchmark, but other topologies may yield different results.
>
> We run a single replica per service, which means 100% pod-delete guarantees full unavailability. Production systems typically run multiple replicas, so our results represent worst-case single-replica scenarios.
>
> The cluster uses KVM/QEMU virtualization, which introduces overhead. Bare-metal clusters may show different performance, especially for I/O metrics.
>
> For external validity:
>
> Our cluster has 5 nodes (1 control plane, 4 uniform 4-GiB workers) with a total of 10 vCPU and 18 GiB. Larger clusters may show different placement effects.
>
> We only tested pod-delete and pod-cpu-hog. Network partitions, disk faults, and memory pressure faults may reveal different strategy rankings.
>
> We used steady-state load at 50 users and 10 requests per second. Bursty or production-like traffic patterns may affect results differently.
>
> Metric portability is also a threat -- PSI requires cgroup-v2, Felix metrics require Calico, and the etcd_debugging_* names are K8s-version-fragile. The `metricAvailability` map in our Prometheus prober surfaces which of these were collected on a given run, so the same analysis is honest about what data was actually available.

---

## Slide 14 -- Conclusion & Future Work

> Our contributions:
>
> First, the **ChaosProbe framework** itself -- an automated, placement-aware chaos testing tool for Kubernetes.
>
> Second, a **systematic evaluation** of 6 placement strategies under 2 fault types across 4 metric dimensions.
>
> Third, **Neo4j graph storage** that preserves causal relationships for topology-aware analysis -- 14 node types, 18 relationship types.
>
> Fourth, a **reproducible methodology** with seeded randomness, exact configurations, and an automated comparison pipeline.
>
> Key findings: all three literature-derived predictions (L1–L3) are refuted, and the three refutations point at the same mechanism. Under pod-delete on a single-replica critical service, colocate scored 83.0 with zero variance, while spread scored 66.3 with high variance. Recovery time does not predict score -- the strategies all recover in roughly the same 1.1 to 1.6 second band, yet scores span roughly 50 to 83. The mechanism is that pod-delete is a churn-based fault, not a contention-based one, so co-located services keep their probe paths kernel-local during the kill cycle and avoid cross-node TCP and conntrack disruption. Spread amplifies exactly the cross-node traffic that pod churn disrupts. The methodology control held -- baseline scored 100% with zero variance -- so the refutations are real, not noise.
>
> The framework now supports a multi-fault matrix out of the box: pass `-e placement-experiment.yaml -e placement-experiment-cpuhog.yaml` and every placement strategy runs once under each fault class. The contention-fault scenario (pod-cpu-hog on the same target) is the most informative single experiment to add, because it directly tests whether the ordering flips back to the literature direction under contention. If it does, the fault-class-specific story is confirmed and turns into a concrete operator recommendation: pick the placement strategy by the dominant fault class for the workload. Other directions still ahead include multi-replica services where restart can happen on a peer pod, larger clusters with production-like traffic, memory- and network-fault classes, and integrating per-fault-class placement guidance into existing schedulers like Borg or Medea.
>
> To summarize: placement-vs-resilience intuition from the literature is fault-class-specific. The community has implicitly assumed contention-based faults, and the resulting "spread is safer" guidance does not survive contact with churn-based faults like pod-delete. Co-location wins under churn because it minimises the surface area for cross-node disruption during the kill cycle.

---

## Slide 15 -- Thank You

> Thank you. I am happy to take any questions.
>
> To recap: 6 placement strategies plus baseline and default-scheduler controls, 4 metric dimensions, 7 httpProbes across 4 tiers plus 5 Rust cmdProbes, and three literature-derived predictions (L1–L3) -- all three refuted, pointing at one shared mechanism. The placement-vs-resilience intuition in the literature implicitly assumes faults are contention-based. Pod-delete is churn-based, and under churn the prescription inverts: co-location wins, spread loses, and recovery speed is not the bottleneck. ChaosProbe gives operators a framework to make this distinction empirically rather than by default.
