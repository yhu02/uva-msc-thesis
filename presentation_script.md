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
> The framing is deliberate. Earlier work asks which placement strategy is most resilient and collapses the answer into a single aggregate resilience score. We found that score is not reproducible at this scale, so we **build the thesis on the primary-source metrics instead** -- the kernel and network signals that *do* reproduce across runs. We state four metric findings, M1 through M4, plus two supporting hypotheses, S1 and S2. Separately, three *legacy literature predictions* -- **L1** (colocate is the worst), **L2** (spread gives the best fault isolation), and **L3** (recovery time predicts resilience) -- are the contention-model expectations the results put to the test. We label those L-, distinct from the M- and S- claims, to keep the families clear.
>
> **Primary results -- the reproducible metrics (M1 through M3).** These are the spine of the thesis. Each is a primary-source metric, measured directly, and verified to reproduce across the collected runs.
>
> **M1: Spreading flushes cross-node connection state.** This is the headline metric. During pod-delete, the spread and default strategies flush 36 to 39 percent of the node's conntrack entries; colocate stays essentially flat, about minus 1.6 percent. This holds in every one of the twelve runs where both were measured -- a large, reproducible fault-response signal, and the dependent variable the noisy score never gave us.
>
> **M2: Co-location lowers CPU contention rather than raising it.** The densest placement, colocate, throttles *less than the scheduler's own default and than spread* -- a median during-chaos throttle rate of 1.54, below default at 1.90 and spread at 1.94 -- with lower CPU usage and CPU pressure than those two as well. (Note this is the absolute during-chaos rate, not a ratio, and the comparison is against default and spread; one or two other strategies sit lower still.) Colocate throttles below default in eleven of thirteen runs. It is the opposite of what Bubble-Up and the Mars 2011 contention model predict, where denser packing should mean more contention.
>
> **M3: At the mechanism layer, the "spread is safer" prescription does not transfer to churn.** Both reproducible metrics -- conntrack churn and CPU throttling -- favour co-location under a churn fault. So the literature's spread-isolation prescription, written for contention regimes, is *inapplicable* here at the mechanism layer -- not merely unmeasurable on the application score. This is a bounded, reproducible mechanism-level result, not an absence of evidence. It is a claim about the mechanism layer only: whether that mechanism reaches the user is a separate question, and in this single-replica churn regime it does **not** (the decoupling result).
>
> **Methodology (M4).**
>
> **M4: The application resilience score is decoupled from the reproducible metrics.** Where conntrack and throttling reproduce -- eleven or twelve runs out of thirteen -- the binary-probe resilience score does not: a single strategy spans 33 to 89 across runs. The score is a lossy, high-variance instrument; the node-level metrics are the reliable measurement. Recognising that, and dropping to the metric layer, is the methodological contribution.
>
> **Supporting hypotheses (S1 and S2).**
>
> **S1: The reproducible mechanism signal is churn-specific.** The contention fault -- a CPU hog -- does not reproduce any better than churn. Of the two cpu-hog runs we collected, one had every strategy score about 100, but the other saw widespread iteration failures: dependency-aware, adversarial and best-fit at 33, colocate at 67, with a standard deviation near 58. So the aggregate score is noisy under *both* fault classes. What the churn fault adds -- and the contention fault has not yet been shown to add at only two runs -- is the reproducible mechanism separation in M1 and M2, which is why expanding the multi-fault matrix is the priority for future work.
>
> **S2: The recovery-time decomposition is not a stable placement signal.** Recovery decomposes into deletion-to-scheduled plus scheduled-to-ready, but their split is run-dependent: in some runs the application-startup term dominates at 84 to 96 percent of the total, while in others -- including the most recent run -- the scheduling term dominates at up to about 78 percent. Recovery cannot carry the placement story in either direction, which is consistent with M4 and is also why L3 fails.
>
> All six claims are testable from data ChaosProbe already collects, and M1 and M2 are reproducible across the full run set. The baseline run -- a trivial pod-cpu-hog under default scheduling -- sits outside the set as a methodology control: a 100 percent score with zero variance validates the probes before any of these claims are evaluated.

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
>
> **A word on analytic weight.** Not all eight configurations carry equal weight in the results. The reproducible findings -- M1 and M2 -- rest on the contrast between three configurations: colocate, which keeps the target's network path node-local, versus spread and default, which expose it across nodes. The remaining four -- random, adversarial, best-fit, and dependency-aware -- were designed around a *resource-contention* hypothesis, and they serve here as a **generality check**: they widen the set of placements the noisy resilience score still fails to rank, which strengthens M4, and they are the placements where the contention model should finally bite under the cpu-hog matrix -- the priority for future work. They are not yet independently validated as reproducible signals -- half the run set predates their addition -- so the churn story is told on the three-configuration locality contrast, with the other four held in reserve for the contention matrix.

---

## Slide 6 -- Experimental Setup

> Our target application is Google's Online Boutique -- 10 polyglot microservices plus a Redis cache, written in Go, C#, Python, Java, and Node.js. We run a single replica per service, which means a 100% pod-delete causes full unavailability of that service.
>
> The cluster is provisioned with Vagrant using the libvirt (KVM/QEMU) provider. We have 5 nodes:
> - **cp1**: the control plane, 2 vCPU and 12 GiB RAM -- it only runs infrastructure (Prometheus, Neo4j, ChaosCenter, metrics-server)
> - **worker1 through worker4**: 2 vCPU, 4 GiB RAM each -- uniform workers
> - Total: 10 vCPU, 28 GiB across the cluster
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
> Below, the infrastructure layer shows the existing tools we integrate: LitmusChaos, Prometheus, Neo4j, Kubernetes, Locust, and Vagrant/libvirt.
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
> - **PrometheusProber** collects two query families every 10 seconds: application-side (`pod_ready`, CPU throttle, memory, network) and *churn-mechanism* metrics that approximate the K8s SIG-Scalability Network Programming Latency SLO. The prober queries the SLO's own `kubeproxy_network_programming_p99` and `kubeproxy_sync_proxy_rules_p99`, but the run outputs' `metricAvailability` map records both as **uncollected on this cluster** (kube-proxy's metrics endpoint was not scraped), so the reconvergence evidence rests on the proxies that *were* captured -- `conntrack_entries_per_node`, `coredns_request_duration_p99`, and TCP retransmit rates -- which track the same kill-cycle reconvergence window the SLO defines.
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

## Slide 9 -- Results: Why the Score Is Demoted (M4)

> The methodology control holds first: **baseline achieved 100% with standard deviation zero** -- the probes and scoring work as designed. But the central finding on this slide is a negative one about the instrument itself: **the aggregate resilience score does not reproduce across runs.**
>
> By "the thirteen runs" I mean the churn (pod-delete) runs with at least three iterations, collected after the east-west probe fix, with the baseline control passing -- the set on which the measurement is trustworthy (the n=2 pilot runs, the single-iteration smoke run, and the cpu-hog runs sit outside it). When we look across those thirteen runs, the *same* strategy's score swings across a wide band. Colocate ranges from 49.7 to 83. Spread ranges from 33 to 88.7 -- sometimes the worst strategy, sometimes the best. Default ranges from 33 to 83. The run-to-run standard deviation within a single strategy is between 11 and 17 points.
>
> That matters because the gap *between* strategies is tiny by comparison. Colocate averages 69.5; spread averages 70.5 -- a one-point difference, swamped by a within-strategy spread of fifteen. At three iterations per run, no pairwise difference between the main strategies survives a Mann-Whitney test with Holm correction.
>
> So the honest reading is: **the aggregate resilience score cannot rank these placements.** Any single run will show *some* ordering -- an earlier draft of this very deck reported a clean "containment versus leakage" split -- but that ordering is a draw of the noise, not a stable property of the strategy. L1, L2, and L3 are stated in terms of which strategy scores better, and the score has no power to adjudicate that. The next two slides show where the signal actually is: the per-mechanism kernel and network metrics, which *do* reproduce.

---

## Slide 10 -- Results: Recovery Time & Latency

> Recovery time -- the interval from pod deletion to pod ready -- is where L3 gets tested, and the test is structural rather than a horse-race between strategies.
>
> When we decompose recovery into its two phases -- deletion-to-scheduled, then scheduled-to-ready -- the split turns out to be run-dependent. In some runs the scheduled-to-ready phase, which is application startup, is **84 to 96 percent of the total**; in others -- including the most recent run -- the deletion-to-scheduled phase dominates at up to about 78 percent. The decomposition does not point consistently at either term.
>
> That is why **L3 is inapplicable here on its own terms, not refuted by a correlation.** Recovery time can't predict the resilience outcome: the split between its two phases is itself unstable run-to-run, and the resilience score it would supposedly predict is non-reproducible. There is no stable relationship to find on either side.
>
> Latency tells the second part of the story, and the lesson here is about *where* the fault signal lives. On the route that depends on the killed service, during-chaos latency has a mean of 231 milliseconds -- but a p95 of 619 and a maximum of 3,334. The tail is fourteen times the mean. Meanwhile routes that don't depend on the target stay flat at 70 to 110 milliseconds throughout. The impact is route-specific and concentrated in the tail; a mean-based SLO would miss it entirely. This is the *Tail at Scale* point made concrete, and it is why our scoring keeps p95 and a harmonic-mean variant rather than reporting means alone.

---

## Slide 11 -- Results: Primary Metrics — Conntrack & CPU (M1, M2)

> This slide is the heart of the thesis: the two primary-source metrics that **do** reproduce across runs, where the aggregate score did not.
>
> **M1, connection-tracking churn, is the headline.** During the kill cycle, spread and default flush **36 to 39 percent** of the node's conntrack entries at the median; colocate stays essentially flat, about minus 1.6 percent. This holds in **all twelve runs** where both were measured -- a large, reproducible effect. It is the mechanism for everything else: pod-delete tears down and rebuilds the target's network identity every fifteen seconds, and spreading services across nodes maximises the number of cross-node flows that have to reconverge. Co-location keeps those paths node-local, so there is nothing to flush. This conntrack delta is the dependent variable we build on, in place of the score.
>
> **M2 is CPU throttling, and it inverts the contention model.** The densest placement -- colocate -- throttles *less than default and spread*, with a median during-chaos throttle rate of 1.54, below default at 1.90 and spread at 1.94, and with lower CPU usage and pressure than those two. Colocate throttles below default in **eleven of the thirteen runs**. The contention literature, Bubble-Up and Mars 2011, predicts the opposite: denser packing should mean more contention and more throttling. Under a churn-based fault, that prediction does not hold here -- a bounded, mechanism-layer observation, not a universal refutation of the contention model.
>
> Both metrics favour co-location, and that is the synthesis -- **M3**: the literature's "spread is safer" prescription does not transfer to churn at the mechanism layer, reproducibly, even though the application score is too noisy to settle it. The mechanism is unambiguously churn-driven, not contention-driven. This is a mechanism-layer claim only -- it does not assert a user-visible advantage, which the decoupling result shows is absent in this regime.

---

## Slide 12 -- Discussion

> The discussion has two layers: what the aggregate score can and cannot tell us, and what the mechanism metrics resolve in its place.
>
> Start with the instrument. **The aggregate resilience score is the wrong tool to adjudicate L1, L2, and L3.** It is not reproducible -- a single strategy spans 33 to 89 across runs -- so any "colocate beats spread" or "spread beats colocate" claim is a draw of the noise. The three predictions are framed as score comparisons, and the score has no power to settle them. So we resolve them at the mechanism layer, where the metrics *do* reproduce.
>
> **L1 -- Colocate is the worst: not supported.** On the score, colocate is indistinguishable from the rest. On the mechanism that actually reproduces, colocate throttles *less than default and spread* and flushes essentially no connection state. Whatever "worst" would mean here, colocate is not it.
>
> **L2 -- Spread provides the best fault isolation: not supported, and arguably inverted.** Spread flushes 28 to 52 percent of conntrack entries during churn -- the most of any strategy, in every run measured. Under a churn fault, spreading does not isolate the blast radius; it *enlarges* it, because it maximises the cross-node flows that pod-delete tears down.
>
> **L3 -- Recovery time predicts resilience: inapplicable on its own terms.** The split between recovery's two phases is itself unstable run-to-run -- application startup dominates in some runs, the scheduling term in others -- and the score it would predict is non-reproducible. There is no stable relationship on either side to confirm or deny.
>
> Now the mechanism. The placement literature builds its predictions on **contention-based faults** -- CPU hogs, memory hogs, disk stress -- where co-locating services makes them compete and spreading genuinely helps. The intuition behind L1, L2, and L3 all comes from that contention model.
>
> **Pod-delete is not a contention fault. It is a churn fault.** Every 15 seconds the target pod is killed and rescheduled, and the kernel's conntrack table, kube-proxy rules, and CoreDNS cache must reconverge to a new pod IP. We measure that reconvergence directly through conntrack flushing -- and it is exactly the metric that separates the strategies reproducibly. Co-location keeps the target's path node-local, so little reconverges; spreading exposes more cross-node routes to the churn.
>
> Three takeaways. First, **the aggregate resilience score is not a reliable ranking instrument at this scale** -- the contribution is recognising that and dropping to the mechanism layer, not publishing a strategy leaderboard. Second, **placement-vs-resilience intuition is fault-class-specific**: the literature's contention-based conclusions do not transfer to churn, and our two reproducible signals -- throttling and conntrack -- show the churn mechanism directly. Third, the operationally useful variable is **network-path locality under churn**, not density and not recovery speed: keep a churn-prone service's path confined and the kernel has less to reconverge.

---

## Slide 13 -- Threats to Validity

> For internal validity:
>
> Our results are based on a single application -- Google Online Boutique. It is a representative benchmark, but other topologies may yield different results.
>
> We run a single replica per service, which means 100% pod-delete guarantees full unavailability. Production systems typically run multiple replicas, so our results represent worst-case single-replica scenarios.
>
> The reproducible mechanism findings rest on three configurations -- colocate, spread, and default. The four resource-contention strategies -- random, adversarial, best-fit, and dependency-aware -- appear in only half the run set and in none of the reproducible findings, so they function as a generality check rather than independently validated signals; confirming or refuting their intended contention-driven separation needs more cpu-hog runs.
>
> The cluster uses Vagrant with the libvirt (KVM/QEMU) provider, which introduces virtualization overhead. Bare-metal clusters may show different performance, especially for I/O metrics.
>
> For external validity:
>
> Our cluster has 5 nodes (1 control plane at 12 GiB, 4 uniform 4-GiB workers) with a total of 10 vCPU and 28 GiB. Larger clusters may show different placement effects.
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
> Key findings: the headline is methodological. **The aggregate resilience score is not reproducible** -- a single strategy spans 33 to 89 across thirteen runs -- so it cannot rank placements, and the literature predictions L1 to L3, which are score comparisons, cannot be settled on it. **The mechanism layer, by contrast, does reproduce.** CPU throttling orders colocate below default in eleven of thirteen runs, and conntrack flushing separates spread from colocate in all twelve measured -- spread flushing 28 to 52 percent, colocate staying flat. Both point at the same cause: pod-delete is a churn-based fault, not a contention-based one, so co-located services keep their paths kernel-local during the kill cycle while spread amplifies exactly the cross-node traffic that churn disrupts. Recovery's two-phase split is unstable run-to-run, so it predicts nothing either way. The methodology control held -- baseline scored 100% with zero variance on the included runs -- so the non-reproducibility is a genuine property of the score, not a measurement artefact.
>
> The framework now supports a multi-fault matrix out of the box: pass `-e pod-delete.yaml -e cpu-hog.yaml` and every placement strategy runs once under each fault class. We have two contention-fault runs so far, but they do not yet reproduce -- one was clean, one had widespread failures -- so the most valuable next step is simply *more* cpu-hog runs, to establish whether the ordering flips back to the literature direction under contention. If it does, the fault-class-specific story is confirmed and turns into a concrete operator recommendation: pick the placement strategy by the dominant fault class for the workload. Other directions still ahead include multi-replica services where restart can happen on a peer pod, larger clusters with production-like traffic, memory- and network-fault classes, and integrating per-fault-class placement guidance into existing schedulers like Borg or Medea.
>
> To summarize: placement-vs-resilience intuition from the literature is fault-class-specific, but the more important caution is methodological -- the aggregate resilience score is too noisy to rank placements at this scale, and the reproducible signal lives in the mechanism metrics. Those metrics show the contention model's "spread is safer" guidance does not survive contact with a churn-based fault: under pod-delete, co-location minimises the cross-node connection state that has to reconverge during the kill cycle, while spread maximises it.

---

## Slide 15 -- Thank You

> Thank you. I am happy to take any questions.
>
> To recap: 6 placement strategies plus baseline and default-scheduler controls, 4 metric dimensions, 7 httpProbes across 4 tiers plus 5 Rust cmdProbes, and three literature-derived predictions (L1–L3). The aggregate resilience score turned out too non-reproducible to adjudicate those predictions -- that is itself a finding -- so the result rests on the two mechanism signals that *do* reproduce: CPU throttling and conntrack flushing. Both show that pod-delete is churn-based, not contention-based, and at the mechanism layer the contention model's spread-isolation prescription does not transfer to churn -- co-location keeps the network path local while spread exposes it, and recovery speed is not the bottleneck. That mechanism-layer effect does not, in this single-replica regime, reach the user; the contribution is the layered measurement discipline that separates those cases, not a universal "best strategy". ChaosProbe gives operators a framework to make this distinction empirically rather than by default.
