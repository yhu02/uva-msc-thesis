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
> We test three hypotheses:
>
> **H1** -- Colocating all pods on a single node maximizes resource contention and produces the worst resilience scores. All services compete for shared CPU, memory, disk, and network on one machine.
>
> **H2** -- Spreading pods evenly across nodes minimizes per-node contention and limits the blast radius of a fault, yielding the best resilience scores.
>
> **H3** -- A baseline experiment with a trivial fault and default scheduling should produce 100% resilience. Any degradation would indicate pre-existing instability in the setup.
>
> We measure across four dimensions: recovery time, inter-service latency, resource utilization, and I/O throughput.

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

> Our target application is Google's Online Boutique -- 12 polyglot microservices written in Go, C#, Python, Java, and Node.js. We run a single replica per service, which means a pod-delete causes full unavailability of that service.
>
> The cluster runs on Proxmox with KVM/QEMU virtualization. We have 5 nodes:
> - **cp1**: the control plane, 2 vCPU and 2 GiB RAM -- it only runs infrastructure (Prometheus, Neo4j, ChaosCenter, metrics-server)
> - **w1 and w2**: 2 vCPU, 2 GiB RAM each -- smaller workers
> - **w3 and w4**: 2 vCPU, 4 GiB RAM each -- larger workers
> - Total: 10 vCPU, 14 GiB across the cluster
> - Running Kubernetes v1.28.6 with Calico CNI and containerd 1.7.11
>
> We inject two fault types:
> - **pod-delete** targeting productcatalogservice -- a central service in the dependency graph. 120 seconds duration with deletions every 5 seconds, using 6 httpProbes across 4 sensitivity tiers.
> - **pod-cpu-hog** targeting currencyservice -- 60 seconds of 100% CPU load on 1 core, with 1 httpProbe.
>
> The baseline swaps pod-delete for a trivial pod-cpu-hog: 1 second duration, 1% CPU load. All probes execute identically, and we expect 100% score with zero recovery cycles.
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
> - **PreChaos** (30 seconds) -- establish steady-state baselines
> - **DuringChaos** (120 seconds for pod-delete, 60 seconds for pod-cpu-hog) -- fault is active
> - **PostChaos** -- observe recovery behaviour
>
> Six continuous probers run as background threads:
> - **RecoveryWatcher** uses the Kubernetes Watch API to observe pod lifecycle events in real-time: deletion, scheduling, and ready timestamps.
> - **LatencyProber** measures HTTP route latency every 2 seconds by executing requests inside the cluster via kubectl exec.
> - **RedisProber** and **DiskProber** measure I/O throughput every 10 seconds via redis-cli and dd respectively.
> - **ResourceProber** fetches node CPU and memory from the Metrics API every 5 seconds -- only across nodes hosting namespace pods.
> - **PrometheusProber** collects pod readiness, CPU throttling, memory, and network metrics via PromQL every 10 seconds.
>
> For resilience probes, we use 6 LitmusChaos httpProbes at different sensitivity tiers -- strict (2-second interval, 1 retry), moderate (3-4 second interval, 2-3 retries), and an edge probe (5-second interval, 5 retries with 15-second timeout).
>
> The resilience score is the mean probe success percentage across all probes, on a 0-100 scale. The verdict is PASS only if all probes pass.

---

## Slide 9 -- Results: Resilience Scores

> Looking at the resilience scores across all strategies:
>
> **Baseline achieves exactly 100%**, as expected -- this validates our experimental methodology and confirms H3.
>
> **Colocate consistently has the lowest scores** -- maximum resource contention degrades all probes. This supports H1.
>
> **Spread has the highest scores among non-baseline strategies** -- fault isolation limits the blast radius. This supports H2.
>
> **Default** scores moderately -- the Kubernetes scheduler provides some isolation, but it is not intentional resilience optimization.
>
> **Random and adversarial** show variable results depending on where the resource hotspot lands.
>
> All three hypotheses are supported by the experimental evidence.

---

## Slide 10 -- Results: Recovery Time & Latency

> For recovery time -- measured as the interval from pod deletion to pod ready:
>
> **Colocate has the longest recovery**. The scheduler faces contention on the saturated node, delaying rescheduling.
>
> **Spread has the fastest recovery**. Dedicated node resources allow immediate rescheduling without competition.
>
> **Baseline has zero recovery cycles** because no pods are actually deleted -- it serves as the control.
>
> For latency degradation between pre-chaos and during-chaos:
>
> **Colocate shows the highest degradation**. Shared CPU, memory, and network stack amplify latency when the fault is active.
>
> **Spread shows minimal increase**. Fault isolation contains the impact to the targeted service's node.
>
> **Adversarial also shows high degradation** -- the resource-heavy hotspot node amplifies cross-service contention.

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

> All three hypotheses are supported:
>
> **H1: Colocate is the worst**. It has the lowest resilience scores, longest recovery times, highest latency degradation, and most CPU throttling. Supported.
>
> **H2: Spread provides the best fault isolation**. It has the best non-baseline scores, minimal latency increase, and fastest recovery. Supported.
>
> **H3: Baseline achieves 100%**. The trivial fault produces perfect scores with zero recovery cycles, confirming our measurement validity. Supported.
>
> Three key insights emerge:
>
> First, **placement matters**. Topology is not just a resource concern -- it directly determines fault blast radius and recovery characteristics.
>
> Second, **the default scheduler is not enough**. It provides some isolation, but it is not optimized for resilience. Intentional placement is needed for fault-tolerant systems.
>
> Third, the impact is **multi-dimensional**. Placement affects all measured dimensions -- recovery, latency, resources, and throughput. It is not a single-metric problem.

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
> Key findings: placement topology has a measurable and significant impact on chaos resilience. Colocate is consistently the worst. Spread consistently provides the best fault isolation. All three hypotheses are supported.
>
> Future work includes multi-fault injection for complex failure scenarios, larger cluster scale with 20+ nodes and 100+ services, ML-based anomaly detection on the collected dataset, custom placement policies using reinforcement learning, production-like traffic patterns, and integration with GitOps for automated remediation.
>
> To summarize: pod placement topology has a measurable and significant impact on microservice resilience under chaos injection. Topology-aware scheduling is essential for building resilient cloud-native systems.

---

## Slide 15 -- Thank You

> Thank you. I am happy to take any questions.
>
> To recap the numbers: 6 placement strategies, 2 fault types, 4 metric dimensions, and all 3 hypotheses supported by experimental evidence.
