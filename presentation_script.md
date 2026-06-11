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
> ChaosProbe fills this gap. It is an automated framework that varies placement strategies, injects faults via LitmusChaos, and measures the response at three layers: the aggregate resilience score, the kernel and network mechanism layer, and the user-visible outcome. The contribution is establishing *at which layer* a placement effect appears under a given fault class -- and whether it reaches the user.

---

## Slide 3 -- Research Question & Hypotheses

> Our research question is: *Under which fault classes does pod placement measurably affect mechanism-level behaviour and user-visible outcomes -- and when do aggregate resilience scores obscure those effects?*
>
> The framing is deliberate. This is a fault-class-by-measurement-layer study, not a placement ranking, and not a refutation of the placement literature. A placement effect can appear at the aggregate-score layer, at a kernel/network mechanism layer, and/or at the user-visible layer -- and these layers need not agree. We state six falsifiable hypotheses, H1 through H6, grouped by fault class. The narrative arc across them: the score is blind, placement does act at the mechanism layer, that action does not reach the user under churn or load -- and where placement *does* bite users is availability under node failure, where it is predictable.
>
> **Churn (pod-delete) -- H1 through H3.**
>
> **H1: The aggregate resilience score cannot rank placement strategies.** A variance partition across 7 independent sessions and 147 churn iterations gives an ICC for strategy of 0.033, with a bootstrap confidence interval of 0.014 to 0.178 -- only 3.3 percent of score variance is between-strategy. The focal contrast, colocate at 64.0 versus spread at 74.3, is d = 0.46, which would need 73 iterations per strategy for 80 percent power; at the n = 3 actually run, the minimum detectable effect is about 51 score points -- larger than any gap that exists. Note the scope: the claim is that the score cannot *rank* placement strategies in this regime, not that aggregate scoring is useless in general.
>
> **H2: Placement reproducibly moves a kernel/network reconvergence signature.** During the kill cycle, spread flushes a median 38.5 percent of per-node connection-tracking state; colocate 2.7 percent. Spread exceeds colocate in all 7 of 7 sessions -- sign test p = 0.0156, Wilcoxon signed-rank p = 0.0225. A dedicated protocol-composition probe scopes the attribution: TCP dominates the conntrack table and drops sharply at the kill cycles under *both* placements -- kernel-side teardown, since kube-proxy never flushes TCP -- while the clearly placement-dependent component is the UDP/DNS pool, roughly 4 times larger under spread; the campaign's flush percentages remain statistically primary and unapportioned between those two paths. CPU throttling corroborates -- colocate is lowest in 6 of 7 sessions -- but it is a weaker, supporting signal only.
>
> **H3: The mechanism is decoupled from the user-visible outcome.** The flush-to-dependent-route-p95 correlation is rho = 0.07 -- not significant, and a TOST equivalence test declares it statistically equivalent to zero. Meanwhile the *control* route, which does not depend on the killed service, shows rho = 0.29, significant. A correlation that is stronger where it should not exist is the signature of a run-level confound, not causation. There is no dependency-specific propagation of the mechanism to the user.
>
> **Load contention -- H4 and H5.**
>
> **H4: Under genuine load, placement moves the mechanism, not (reproducibly) the user.** Across two i = 4 batches under a 200-user spike, colocate's east-west inter-service p95 sits about 1.3 to 1.4 times below spread's -- the mechanism effect replicates. The user-layer effect did not survive replication, so we state only the mechanism effect.
>
> **H5: A graph-derived metric separates node-local from spreading placements.** The cross-node call fraction -- computable from the dependency graph plus the placement, before any chaos -- was tested across two independent 8-strategy load batches. In both batches the two node-local placements, colocate and best-fit, with fraction near zero, took the two lowest east-west tails of eight -- joint null probability about 0.0013 -- sitting roughly 1.25 times below the spreading cluster. The continuous correlation did *not* replicate: batch 1's Spearman rho = 0.79 collapsed to rho = 0.25, not significant, in batch 2 -- so we claim a replicated two-regime separator, not a smooth predictor, and we never quote 0.79 without 0.25 next to it. It still makes the Neo4j dependency graph analytically load-bearing.
>
> **Node failure -- H6.**
>
> **H6: Co-location is a latency/availability trade-off.** Under a node drain, colocate loses all 11 services -- a 100 percent blast radius -- while spread loses 2 of 11, reproduced across two doctor-clean batches. A completed 6-strategy gradient run extends this: observed blast equals the placement-predicted blast for every placing strategy -- 11, 4, 3, 3, 2, 2 services -- Spearman rho = 1.0, n = 6. Recovery, however, is *not* monotone in blast, so the roughly 4-times recovery contrast -- 10.3 versus 2.6 seconds -- is claimed only at the colocate-versus-spread extremes. The same co-location that wins H5's latency loses H6's availability.
>
> Separately, the three *literature predictions* -- L1, colocate is worst; L2, spread isolates best; L3, recovery time predicts resilience -- are kept as context. Under this regime they are best described as inapplicable, never as refuted.

---

## Slide 4 -- Related Work

> The placement side of our work builds on systems like Google's Borg for resource-aware bin-packing, Medea for topology spread constraints, Sparrow for decentralized scheduling, and DeathStarBench for dependency-graph-aware placement.
>
> On the chaos engineering side, we build on the Principles of Chaos Engineering by Basiri et al., and use LitmusChaos as our fault injection engine -- it is a CNCF sandbox project that operates via ChaosEngine CRDs.
>
> The gap is clear: existing work studies placement *or* resilience, but no framework systematically varies placement under controlled chaos and measures the interaction across measurement layers. ChaosProbe bridges this gap with 8 placement configurations across 3 fault classes, with layered metrics, all stored in a Neo4j graph that preserves the causal topology.
>
> The table shows the literature-informed contention predictions -- the L1-to-L3 family: colocating pods shares cores leading to CPU throttling, shares memory leading to evictions, shares the network stack increasing latency, and shares disk bandwidth reducing I/O throughput. We test these as context; as we will see, they turn out to be inapplicable in the churn regime rather than confirmed or refuted.

---

## Slide 5 -- Placement Strategies

> We test 8 placement configurations. The independent variable is the placement strategy.
>
> **Baseline** is our control group -- default scheduler with a trivial fault (1% CPU for 1 second). We expect a 100% score to validate the methodology.
>
> **Default** uses the standard Kubernetes scheduler with full chaos injection. This is the placement null hypothesis -- what happens when we let the scheduler decide.
>
> **Colocate** pins all pods to a single node via `nodeSelector` (every deployment gets the same `kubernetes.io/hostname`). This is maximal co-location.
>
> **Spread** distributes pods evenly across workers via per-node `nodeSelector` assignment. This is minimal per-node concentration.
>
> **Random** uses a seeded random assignment per deployment. It is reproducible and serves as a null baseline for topology effects.
>
> **Adversarial** places the resource-heaviest pods on one node, creating an intentional hotspot. This is worst-fit scheduling.
>
> **Best-fit** uses bin-packing to concentrate pods into the fewest nodes, similar to Borg-style resource scoring.
>
> **Dependency-aware** co-locates communicating services via BFS partitioning of the service dependency graph.
>
> **A word on analytic weight.** Not all eight configurations carry equal weight in every result. The churn findings -- H2's reconvergence signature and H3's decoupling -- rest on the colocate-versus-spread locality contrast. All eight placements enter the load matrix: H5's cross-node fraction is computed per strategy, n = 8, across two independent batches. H6 contrasts the two extremes, colocate versus spread, under node drain, and a completed 6-strategy gradient run extends it -- observed blast matched the placement-predicted blast for every strategy. One honest aside: dependency-aware, our most distinctive strategy, did not deliver as implemented -- its cross-node fraction came out spread-like (0.73), so the BFS partition did not co-locate communicating services as intended. That is a candidate for future improvement, not a validated win.

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
> Three fault classes enter the matrix. **Churn**: pod-delete targeting productcatalogservice -- a central service in the dependency graph -- killed every 15 seconds for 120 seconds, with FORCE=true and PODS_AFFECTED_PERC=100; this is where H1 to H3 live. **Load contention**: a sustained 200-user Locust spike that makes the application genuinely resource-bound -- H4 and H5. **Node failure**: a drain of the node hosting the target -- H6, read at the availability layer. Resilience probes are 7 httpProbes across 4 sensitivity tiers plus 5 Rust cmdProbes.
>
> The baseline strategy swaps pod-delete for a trivial pod-cpu-hog on the same target -- 1 second duration, 1% CPU load on 0 cores -- so no pods are actually killed. All probes execute identically, and we expect a 100% score with zero recovery cycles. This validates the methodology.
>
> Infrastructure includes LitmusChaos for fault injection, Prometheus for cluster metrics, Neo4j for graph storage with 14 node types and 18 relationships, and Locust generating steady-state load at 50 users and 10 requests per second during churn runs.

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
> **GraphStorage** writes everything to Neo4j -- 14 node types, 18 relationships, all queryable via Cypher. As H5 will show, this graph is not just storage: the cross-node call fraction computed from it is the study's pre-chaos placement predictor.
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
> The resilience score is the mean probe success percentage across all probes, on a 0-100 scale. The verdict is PASS only if every probe passes. We also report a 95% bootstrap confidence interval on the mean, a 25th-percentile score, and a harmonic-mean variant that penalises low-score iterations more strongly -- per Dean and Barroso's *Tail at Scale* (2013), means alone hide what matters at the tails. For cross-strategy comparison we run pairwise Mann-Whitney U with Holm-Bonferroni correction, and the H1 analysis adds an ICC variance partition with bootstrap confidence intervals, TOST equivalence tests, and a power analysis.

---

## Slide 9 -- Results: H1 -- The Score Cannot Rank Placements

> The methodology control holds first: **baseline achieved 100% with standard deviation zero** -- the probes and scoring work as designed. But the first finding is about the instrument itself: **the aggregate resilience score cannot rank placement strategies in this regime.**
>
> The evidence base is 7 independent sessions totalling 147 churn iterations. A variance partition of the per-iteration score gives an ICC for strategy of **0.033**, with a bootstrap 95% confidence interval of 0.014 to 0.178. In words: only **3.3 percent** of score variance is between-strategy; the rest is iteration-level and session-level noise.
>
> What would it take to detect the focal contrast anyway? Colocate averages 64.0, spread 74.3 -- an effect size of d = 0.46. Detecting that at 80 percent power needs **73 iterations per strategy**. At the three iterations per strategy actually run per session, the minimum detectable effect is roughly **51 score points** -- larger than any between-strategy gap that exists in the data.
>
> So the honest reading is scoped and precise: **the aggregate score cannot rank these placements.** Any single run will show *some* ordering, but that ordering is a draw of the noise. Note what this claim is not: it is not "aggregate scores don't work" -- it is that this score, in this regime, has no power to rank strategies. The literature predictions L1 to L3 are stated as score comparisons, so the score cannot adjudicate them -- they are inapplicable here, not refuted. The next slides show where the signal actually is.

---

## Slide 10 -- Results: H2 -- A Kernel Reconvergence Signature Moves

> If the score is blind, is placement doing nothing? No -- it leaves a large, consistent footprint one layer down.
>
> **H2 is the connection-tracking result.** During the kill cycle, spread flushes a median **38.5 percent** of per-node conntrack entries; colocate flushes **2.7 percent**. Spread exceeds colocate in **all 7 of 7 sessions** -- a sign test gives p = 0.0156, and a Wilcoxon signed-rank test on the paired session medians gives p = 0.0225. This is the most consistent signal in the study.
>
> The mechanistic reading: pod-delete tears down and rebuilds the target's network identity every fifteen seconds. Spreading services across nodes maximises the number of cross-node flows that have to reconverge after each kill; co-location keeps those paths node-local, so there is little to flush.
>
> The attribution is **protocol-scoped**, measured by a dedicated conntrack-composition probe -- per-node protocol counts sampled every 5 seconds through a full kill cycle under each placement. First: **TCP dominates the conntrack table under both placements and drops sharply at the kill cycles under both** -- minus 28 percent under spread, minus 21 percent under colocate within one cycle. Since kube-proxy never actively flushes TCP entries, those drops are kernel-side teardown of flows traversing the killed pod. Second: **the clearly placement-dependent component is the UDP slash DNS pool** -- under steady load, spread sustains roughly 4 times more UDP entries than colocate, chaos-window medians 910 versus 224, exactly the traffic class kube-proxy's documented UDP-only cleanup acts on. What the probe deliberately does *not* establish, with one iteration per placement and a ramp-contaminated baseline, is the apportionment: the campaign's 38.5-versus-2.7-percent flush medians remain statistically primary and are not split between the two paths. The seven sessions carry the statistics; the probe carries composition and event timing.
>
> CPU throttling corroborates the same locality picture -- colocate throttles least in 6 of 7 sessions -- but it is weaker and we treat it as supporting evidence only. The conntrack signature leads.

---

## Slide 11 -- Results: H3 -- The Mechanism Does Not Reach the User

> So placement moves a kernel-layer signal. Does that translate into anything the user feels? This is H3, and the answer is no -- in three independent tests.
>
> First, the direct correlation: conntrack flush against dependent-route p95 latency -- the route that actually depends on the killed service -- is **rho = 0.07**, not significant. And we go further than "not significant": a TOST equivalence test declares the association **statistically equivalent to zero** -- the decoupling is supported, not merely unproven.
>
> Second, the confound control. The *control* route -- which does **not** depend on the killed service -- shows **rho = 0.29, significant**. A mechanism-to-latency correlation that is stronger on the route with no dependency on the fault is the classic signature of a **run-level confound**: slow runs are slow everywhere, and the mechanism metric simply rides along. It is not causation.
>
> Third, there is no dependency-specific propagation: nothing in the data shows the mechanism reaching the user *through the dependency that was attacked*.
>
> The layered churn story is now complete: the score is blind (H1), the kernel layer moves with placement (H2), and the user layer does not follow (H3). The bounded operator reading: for churn faults on single-replica services in this setup, where you put the pods is not a user-visible resilience lever -- the killed pod is simply gone, and survivability is governed by availability dynamics, not topology.

---

## Slide 12 -- Results: H4 & H5 -- Load Contention and a Graph Predictor

> Churn is one fault class. The contention literature is about a different one -- so H4 drives the cluster into *genuine* resource contention with a sustained 200-user Locust spike. As the negative-findings slide will show, hog faults never achieve this; load does.
>
> **H4: the mechanism effect replicates; the user-layer effect does not.** Across two independent i = 4 batches, colocate's east-west inter-service p95 sits consistently below spread's -- the median spread-to-colocate ratio is 1.39 in one batch and 1.36 in the other, so colocate is roughly **1.3 to 1.4 times faster at the inter-service layer**. Co-location keeps inter-service calls node-local; spread routes every call across the network, which is the bottleneck under load. On the **user-facing** routes, however, a roughly 2x reading in the first batch collapsed to about 1.1x in the clean-provenance batch, with no dependency specificity. So we claim **no user-visible placement effect under load** -- only the mechanism effect, which matches the churn decoupling rather than overturning it.
>
> **H5 asks whether that east-west penalty is predictable before any chaos runs.** The metric is the **cross-node call fraction**: the fraction of the dependency graph's inter-service edges whose endpoints land on different nodes under a given placement -- computable from the Neo4j graph plus the placement alone. We tested it across **two independent 8-strategy batches**. In both, the two node-local placements -- colocate and best-fit, fraction near zero -- took the **two lowest east-west tails of eight**: per batch that has a null probability of 1 in 28, jointly about **0.0013**, and the node-local pair sits roughly **1.25 times below** the spreading cluster -- about 34.6 versus 43.5 milliseconds in batch 1, 34.5 versus 42.4 in batch 2.
>
> Two framing cautions. First, the **continuous correlation did not replicate**: batch 1's Spearman rho = 0.79 was carried by best-fit's intermediate 0.13 point, and in batch 2 -- where best-fit packed fully to fraction zero and the spreading cluster showed no internal trend -- it collapsed to **rho = 0.25, not significant**. We therefore claim H5 as a **replicated two-regime separator**, never as a smooth predictor, and we never quote 0.79 without 0.25 next to it. Second, locality-as-objective already belongs to the literature -- NetMARKS and the graph-partitioning schedulers; our contribution is validating the graph-derived fraction against measured during-load tails, which makes the dependency graph analytically load-bearing rather than mere storage. One secondary finding worth naming: dependency-aware's partition did not co-locate as intended -- its fraction is spread-like, 0.73 in both batches -- so the study's most distinctive strategy does not beat the naive ones as implemented.

---

## Slide 13 -- Results: H6 -- The Latency/Availability Trade-Off

> H5 made co-location look good. H6 shows the bill: the third fault class, **node failure**, read at the availability layer -- the layer churn and load never reached.
>
> We drain the node hosting the target service, under colocate and under spread, across two doctor-clean batches. The **blast radius** is the number of services driven to zero ready endpoints at the outage trough, read from EndpointSlice snapshots sampled through the drain -- not from the resilience score, which is unusable here because a node drain leaves every LitmusChaos probe Unknown; that is H1 biting again.
>
> The result: **colocate loses all 11 services -- the whole application offline, a 100 percent blast radius -- with about 10.3 seconds of target recovery. Spread loses 2 of 11 -- 18 percent -- with about 2.6 seconds.** The observed blast equals the placement-predicted blast -- the services pinned to the drained node -- in every iteration.
>
> The **completed 6-strategy gradient run** extends this beyond the extremes: observed blast equals the placement-predicted blast for **all six placing strategies** -- 11, 4, 3, 3, 2, 2 services -- **Spearman rho = 1.0, n = 6**. Per-node concentration predicts the availability consequence exactly: the availability analogue of H5's separator. **Recovery time, however, is not monotone in blast** -- the intermediate placements produced both the fastest recovery, 4.6 seconds, and the slowest, 33.3 seconds -- so the roughly 4-times recovery contrast, 10.3 versus 2.6 seconds, is claimed only at the colocate-versus-spread extremes, where it reproduced across both original batches.
>
> **The trade-off is the finding.** The same co-location that gives the lowest east-west tail in H5 produces the worst node-failure outage in H6; spread is the mirror image. One placement property -- co-location -- two opposing measured consequences: latency and availability. And this is where placement finally *does* bite the user in this study: at the availability layer, predictably from the placement itself.
>
> Three honesty notes. The prediction "drain a node and you lose the pods on it" is near-definitional -- the empirical content is that the predicted blast actually materializes under real chaos, repeats across batches, and drives a measurable recovery-time penalty. This is the **quantification of a known qualitative trade-off** -- cell-based architecture practice already knows concentration trades blast radius for locality -- not a discovery. And while the blast prediction held exactly across the full strategy gradient, the recovery claim stays scoped to the extremes, because recovery is not monotone in blast.

---

## Slide 14 -- Negative Findings & Literature Predictions

> Before concluding, the experiments that *failed* -- they are load-bearing, because they explain why the study's path through fault classes looks the way it does. Call this slide "why the obvious experiments are wrong."
>
> **Hog faults are absorbed, not felt.** The obvious contention experiment is a CPU hog. But pod-cpu-hog is CFS-capped at the container's own 200-millicore limit -- the hog throttles itself, not the application. node-cpu-hog loads the node, but the app pods' CPU *requests* keep the light pods responsive. Both scored 100 with the application fully up. Contention only bites when the application is genuinely resource-bound -- which is why H4 uses real load instead.
>
> **node-memory-hog evicts itself first.** On our 4 GiB workers, the kubelet evicts the LitmusChaos helper pod before any application pod feels memory pressure -- the experiment kills its own instrument, not the app. This matches LitmusChaos issue #3397. The general lesson: negative findings bound the fault taxonomy -- they tell you which experiments measure the application and which measure the harness.
>
> **The literature predictions, L1 to L3, end as inapplicable -- never refuted.** L1, colocate is worst: the score cannot adjudicate it (H1), and the churn mechanism points the other way -- colocate flushes the least connection state. L2, spread isolates best: under churn, spreading maximises the cross-node flows the kill cycle tears down; though note that under node drain, spread genuinely does win availability (H6) -- the intuition has a regime where it holds. L3, recovery predicts resilience: the recovery decomposition is unstable run-to-run and the score is too noisy to predict -- no stable relationship on either side. These predictions come from contention regimes that the churn fault class never enters. Their inapplicability here is itself a fault-class-specific result.

---

## Slide 15 -- Threats to Validity

> For internal validity:
>
> Our results are based on a single application -- Google Online Boutique. It is a representative benchmark, but other topologies may yield different results.
>
> We run a single replica per service, which means 100% pod-delete guarantees full unavailability and a node drain takes out every service pinned to that node. Production systems typically run multiple replicas with anti-affinity; that regime is structurally excluded by this design, so our results are about between-service blast radius under deployment-level placement, not replica failover.
>
> Contrast coverage is uneven: the churn findings rest on the colocate-versus-spread locality contrast; H5 covers all 8 placements across two independent batches; H6's extremes contrast is extended by a completed 6-strategy gradient for blast radius -- recovery is validated only at the extremes.
>
> The cluster uses Vagrant with the libvirt (KVM/QEMU) provider, which introduces virtualization overhead. Bare-metal clusters may show different performance, especially for I/O metrics. Absolute values do not generalize; only directions and mechanisms are claimed, and only for this environment.
>
> For external validity:
>
> Our cluster has 5 nodes (1 control plane at 12 GiB, 4 uniform 4-GiB workers) with a total of 10 vCPU and 28 GiB. Larger clusters may show different placement effects.
>
> Three fault classes were tested -- churn, load contention, and node failure. The hog faults were absorbed by cgroup limits (a negative finding rather than a data point), and network partitions and disk faults remain untested.
>
> We used steady-state load at 50 users for churn and a 200-user spike for the load regime. Production-like traffic may behave differently.
>
> Metric portability is also a threat -- PSI requires cgroup-v2, Felix metrics require Calico, and the etcd_debugging_* names are K8s-version-fragile. The `metricAvailability` map in our Prometheus prober surfaces which of these were collected on a given run, so the analysis is honest about what data was actually available. And every figure quoted as a finding comes from an archived run that passes `doctor --strict` provenance gating.

---

## Slide 16 -- Conclusion & Future Work

> Our contributions:
>
> First, the **ChaosProbe framework** itself -- an automated, placement-aware chaos evaluation tool for Kubernetes, with provenance capture and data-quality gating built in.
>
> Second, a **fault-class-by-measurement-layer study**: three fault classes -- churn, load contention, node failure -- read across the score, mechanism, and user layers, stated as six falsifiable hypotheses, H1 through H6.
>
> Third, the **Neo4j dependency graph made analytically load-bearing**: the cross-node call fraction computed from it is a replicated pre-chaos separator of the east-west placement regimes (H5).
>
> Fourth, the **statistical and provenance discipline**: ICC variance partitions with bootstrap confidence intervals, TOST equivalence testing, power analysis, and doctor-gated clean-provenance runs behind every quoted number.
>
> The findings, as one arc: **the aggregate score cannot rank placements** -- only 3.3 percent of its variance is between-strategy (H1). That does not mean placement is inert: it reproducibly moves mechanism-layer signals in both regimes tested -- the conntrack reconvergence signature under churn (H2) and the east-west inter-service tail under load (H4) -- but those mechanism effects **do not reach the user** (H3, and H4's user layer did not survive replication). Where placement *does* bite users is the **availability layer under node failure**: co-location trades the best east-west latency (H5) for a 100 percent node-drain blast radius (H6), and both faces of that trade-off are predictable from the dependency graph before any chaos runs.
>
> Future work: multi-replica anti-affinity is the production-relevant question this single-replica design structurally excludes. The completed H6 gradient confirmed the blast prediction across all six placing strategies; understanding why recovery is *not* monotone in blast is open. Apportioning the conntrack flush between kernel TCP teardown and kube-proxy's UDP-only cleanup needs a steady-state, multi-iteration probe. More load batches would settle whether any user-layer effect survives replication. And the cross-node-fraction separator is a natural candidate for scheduler integration.
>
> To summarize: resilience conclusions depend on both the fault class and the measurement layer. A single score is blind to placement; placement acts at the mechanism layer without reaching the user under churn and load; and where it does reach the user -- availability under node failure -- it is predictable. Evaluating placement under chaos therefore requires layered measurement, not a leaderboard.

---

## Slide 17 -- Thank You

> Thank you. I am happy to take any questions.
>
> To recap: 8 placement configurations, 3 fault classes, 6 hypotheses. The aggregate resilience score cannot rank placements (H1) -- that is itself a finding. Placement does move the mechanism layer in both churn and load regimes (H2, H4), but those effects are decoupled from the user (H3). Where placement reaches users is availability under node failure: the co-location that wins east-west latency (H5's replicated two-regime separator) loses the node-drain blast radius (H6 -- blast predicted exactly across the 6-strategy gradient, rho = 1.0) -- one placement property, two opposing measured consequences. The literature predictions L1 to L3 end as inapplicable in this regime, not refuted. ChaosProbe gives operators a framework to make these fault-class and layer distinctions empirically rather than by default.
