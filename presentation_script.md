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

> Our research question is: *How does pod placement topology affect microservice resilience under fault injection in Kubernetes?*
>
> We test three hypotheses, derived from the placement and chaos-engineering literature:
>
> **H1** -- Colocating all pods on a single node maximizes resource contention and produces the worst resilience scores. All services compete for shared CPU, memory, disk, and network on one machine. This is the prediction from Mars (2011) and Delimitrou (2014) on contention-aware co-scheduling.
>
> **H2** -- Spreading pods evenly across nodes minimizes per-node contention and limits the blast radius of a fault, yielding the best resilience scores. This is the topology-spread argument from Medea (Garefalakis 2018) and Borg's anti-affinity defaults (Verma 2015).
>
> **H3** -- Recovery time predicts resilience. Faster pod recovery yields higher resilience scores, because shorter unavailability windows mean fewer probe checks fall inside the fault window. This is the intuition behind Dean and Barroso's "Tail at Scale" (2013) and the Basiri chaos-engineering principles.
>
> The baseline run with a trivial pod-cpu-hog sits outside the hypothesis set -- it is a methodology control. A 100% baseline score validates the probes and scoring; any degradation would mean we cannot trust the measurement instrument.
>
> We measure across four dimensions: recovery time, inter-service latency, resource utilization, and I/O throughput. Spoiler -- as you will see in the results, all three hypotheses are refuted, and the three refutations point at the same mechanism.

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
> **Colocate** pins all pods to a single node using pod affinity. This is maximum contention and we expect the worst resilience.
>
> **Spread** distributes pods evenly across workers using topology spread constraints. This is minimum contention and we expect the best fault isolation.
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
> - **w1 and w2**: 2 vCPU, 2 GiB RAM each -- smaller workers
> - **w3 and w4**: 2 vCPU, 4 GiB RAM each -- larger workers
> - Total: 10 vCPU, 14 GiB across the cluster
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
> - **RecoveryWatcher** uses the Kubernetes Watch API to observe pod lifecycle events in real-time: deletion, scheduling, and ready timestamps.
> - **LatencyProber** measures HTTP route latency every 3.5 seconds by executing requests inside the cluster via kubectl exec.
> - **RedisProber** and **DiskProber** measure I/O throughput every 10 seconds via redis-cli and dd respectively.
> - **ResourceProber** fetches node and pod CPU and memory from the Metrics API every 5 seconds -- only across nodes hosting namespace pods.
> - **PrometheusProber** collects pod readiness, CPU throttling, memory, and network metrics via PromQL every 10 seconds.
>
> For resilience probes, we use 7 LitmusChaos httpProbes across 4 sensitivity tiers, all in Continuous mode:
> - **Tier 1 -- Strict** (2 probes, 2s interval, 3s timeout, 1 retry): the product page and homepage; expected to fail under 100% pod-delete and confirm chaos has impact.
> - **Tier 2 -- Moderate-tight** (2 probes, 5s interval, 3s timeout, 4 retries): pass only when recovery is fast.
> - **Tier 3 -- Moderate-loose** (2 probes including the cart route, 6s interval, 5s timeout, 4 retries): pass when recovery is moderate.
> - **Tier 4 -- Control** (1 probe, /_healthz, 4s interval, 5s timeout, 3 retries): pure infrastructure health; failure means severe node-level pressure.
>
> Alongside these we run 5 Rust cmdProbes -- check-redis, check-http-latency, check-dns-latency, check-tcp-connect, check-cart-flow -- which capture Redis collateral damage, post-chaos HTTP latency, DNS resolution time, TCP connect time, and a multi-route user journey.
>
> The resilience score is the mean probe success percentage across all probes, on a 0-100 scale. The verdict is PASS only if every probe passes.

---

## Slide 9 -- Results: Resilience Scores

> The results refute the hypotheses, but the methodology holds.
>
> First, the control: **baseline achieved exactly 100% with zero standard deviation across three iterations**. The probes and scoring work as designed -- so the refutations that follow are real signal, not measurement artefacts.
>
> The non-baseline ranking is not what the literature predicted. **Colocate scored 83.0% with standard deviation zero** -- the highest non-baseline score and perfectly stable across iterations. **Spread scored 52.3%** with high variance, among the worst non-baseline strategies.
>
> The middle tier -- best-fit, adversarial, and dependency-aware -- all clustered around 69%, with high iteration-to-iteration variance.
>
> Default and random sit at roughly 50%, on par with spread.
>
> H1 predicted colocate would be worst. It was the best non-baseline. H2 predicted spread would be best. It was among the worst. Both literature-derived hypotheses are refuted by the score data alone. The next slides examine recovery time and latency -- which is where H3 gets tested -- and the discussion ties all three refutations together.

---

## Slide 10 -- Results: Recovery Time & Latency

> Recovery time -- measured as the interval from pod deletion to pod ready -- is where H3 gets tested directly.
>
> The fastest recovery was **dependency-aware at 1229 milliseconds**, followed by adversarial (1248), colocate (1333), best-fit (1461), spread (1576), and default (1596). Random is an outlier at 9395 milliseconds, almost six times slower than anything else -- this is likely a node-affinity collision during scheduling.
>
> H3 predicted that faster recovery should produce higher resilience scores. The data does not support this. Dependency-aware had the *fastest* recovery (1229ms) but only a mid-tier score (69). Colocate had *slower* recovery (1333ms) but the best score (83). Spread had a recovery time in the same band as colocate (1576ms) but its score was the second-worst (52). Recovery time and resilience score are essentially uncorrelated across the 6 main strategies -- H3 is refuted.
>
> The lesson is that recovery speed is not the dominant factor under pod-delete. What matters is whether *in-flight probe traffic* during the kill cycle has to cross the affected node.
>
> Latency degradation between pre-chaos and during-chaos tells the same story. **Colocate's during-chaos homepage latency was 99 milliseconds** -- the lowest of all strategies. **Spread's was 229 milliseconds** -- more than double. Adversarial, best-fit, and dependency-aware sit between them. The strategies that disrupt the *fewest* cross-node paths during the kill cycle suffer the *least* latency degradation. This is the mechanism that explains all three refutations, which I cover on the discussion slide.

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

> All three literature-derived hypotheses are refuted, and the three refutations point at the same mechanism.
>
> **H1 -- Colocate is the worst: refuted.** Colocate scored 83.0 with zero variance. It was the best non-baseline strategy, not the worst. Its during-chaos frontend latency was 99 milliseconds, lower than every other strategy.
>
> **H2 -- Spread provides the best fault isolation: refuted.** Spread scored 52.3, with standard deviation 27. Its during-chaos latency was 229 milliseconds, more than double colocate's. It was among the worst non-baseline strategies, not the best.
>
> **H3 -- Recovery time predicts resilience: refuted.** Mean recovery time across the 6 main strategies clusters tightly between 1229 and 1596 milliseconds, yet resilience scores span from 52 to 83. Dependency-aware had the fastest recovery but only a mid-tier score; colocate had slower recovery but the best score. Recovery speed and resilience are essentially uncorrelated.
>
> The methodology control held: baseline scored 100 with zero variance, so these refutations are signal, not noise.
>
> So what is going on? The explanation comes from looking carefully at what pod-delete actually does.
>
> The placement literature builds its predictions on **contention-based faults**: CPU hogs, memory hogs, disk I/O stress. Under those faults, co-locating services causes shared resources to compete and degrade, and spreading services across nodes genuinely helps. The intuition behind H1, H2, and H3 all comes from this contention model.
>
> **Pod-delete is not a contention-based fault. It is a churn-based fault.** Every 15 seconds, the productcatalogservice pod is killed and a new one is scheduled. The kernel's TCP stack, conntrack table, kube-proxy iptables rules, and CoreDNS cache all have to reconverge to the new pod IP. While this is happening, every cross-node hop to or through the affected node is briefly disrupted.
>
> This mechanism explains all three refutations at once:
>
> Under colocate, every probe path stays kernel-local on the saturated node. The kill cycle proceeds without ever crossing a node boundary -- so most probes never see the disruption. Only the probes that *directly target* productcatalogservice fail. This is why colocate scores highest and has the lowest during-chaos latency.
>
> Under spread, every backend call has to cross the node hosting productcatalogservice or its replacement. That cross-node hop is exactly what pod-delete churn disrupts. So the entire dependency graph sees the disruption, and resilience scores collapse.
>
> And H3 fails because **recovery time is not the bottleneck** under churn. All strategies recover in roughly 1.2 to 1.6 seconds; the score gap of 30+ percentage points does not come from how long the new pod takes to be ready. It comes from in-flight cross-node failures *during* the kill cycle. If your probe call crosses the affected node while conntrack is reconverging, it fails regardless of how fast the replacement pod is scheduled.
>
> Three insights follow:
>
> First, **placement-vs-resilience intuition is fault-class-specific**. The literature is built on contention-based faults, and its conclusions do not transfer to churn-based faults. This is a publishable gap.
>
> Second, **co-location is not pathological under churn**. It is, in fact, optimal -- because it minimises the surface area for cross-node disruption during the kill cycle.
>
> Third, **the right placement strategy depends on the expected fault class**. Operators should not pick "spread for resilience" as a universal default; they should pick spread for contention-prone workloads and co-locate for churn-prone services.

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
> Our cluster has 5 nodes (1 control plane, 4 workers) with a total of 10 vCPU and 14 GiB. The workers have heterogeneous memory -- w1 and w2 have 2 GiB while w3 and w4 have 4 GiB. Larger clusters with more uniform resources may show different placement effects.
>
> We only tested pod-delete and pod-cpu-hog. Network partitions, disk faults, and memory pressure faults may reveal different strategy rankings.
>
> We used steady-state load at 50 users and 10 requests per second. Bursty or production-like traffic patterns may affect results differently.

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
> Key findings: all three literature-derived hypotheses are refuted, and the three refutations point at the same mechanism. Under pod-delete on a single-replica critical service, colocate scored 83.0 with zero variance, while spread scored 52.3 with high variance. Recovery time does not predict score -- the strategies all recover in roughly the same 1.2 to 1.6 second band, yet scores span 52 to 83. The mechanism is that pod-delete is a churn-based fault, not a contention-based one, so co-located services keep their probe paths kernel-local during the kill cycle and avoid cross-node TCP and conntrack disruption. Spread amplifies exactly the cross-node traffic that pod churn disrupts. The methodology control held -- baseline scored 100% with zero variance -- so the refutations are real, not noise.
>
> Future work: the most important next step is to test contention-based faults -- pod-cpu-hog, pod-memory-hog, network-latency injection -- and check whether the ordering flips back to the literature direction. If it does, that confirms the fault-class-specific story and turns it into a concrete operator recommendation: pick the placement strategy by the dominant fault class for the workload. Other directions include multi-replica services where restart can happen on a peer pod, larger clusters with production-like traffic, and integrating per-fault-class placement guidance into existing schedulers like Borg or Medea.
>
> To summarize: placement-vs-resilience intuition from the literature is fault-class-specific. The community has implicitly assumed contention-based faults, and the resulting "spread is safer" guidance does not survive contact with churn-based faults like pod-delete. Co-location wins under churn because it minimises the surface area for cross-node disruption during the kill cycle.

---

## Slide 15 -- Thank You

> Thank you. I am happy to take any questions.
>
> To recap: 6 placement strategies plus baseline and default-scheduler controls, 4 metric dimensions, 7 httpProbes across 4 tiers plus 5 Rust cmdProbes, and three literature-derived hypotheses -- all three refuted, pointing at one shared mechanism. The placement-vs-resilience intuition in the literature implicitly assumes faults are contention-based. Pod-delete is churn-based, and under churn the prescription inverts: co-location wins, spread loses, and recovery speed is not the bottleneck. ChaosProbe gives operators a framework to make this distinction empirically rather than by default.
