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
| **NetMARKS** — Wojciechowski et al. (INFOCOM 2021), [DOI 10.1109/INFOCOM42981.2021.9488670](https://ieeexplore.ieee.org/document/9488670/); **TraDE** (2024), [arXiv:2411.05323](https://arxiv.org/abs/2411.05323) | Locality as an *optimization objective*: network/traffic-aware schedulers that co-locate using runtime telemetry (NetMARKS: up to 37% response-time reduction). | They *optimize* with runtime metrics; **H5 empirically validates a static, pre-chaos predictor** (the cross-node dependency-edge fraction, ρ = 0.79 vs measured east-west p95). The claim is the correlational validation of the static metric — the locality concept itself is theirs. NetMARKS reports end-to-end response time, not east-west p95: directional precedent, not like-for-like. |

TODO(author): prose paragraph synthesizing the quadrant — the thesis sits at
the empty intersection: placement-manipulating, mechanism-measuring,
reliability-auditing chaos evaluation.

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

TODO(author): prose — trace how L1–L3 were derived from this lineage and
forward-reference ch. 5/6 where they are found inapplicable in this regime.

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

## 2.4 Methodology precedents

- **Maricq et al. (OSDI 2018), "Taming Performance Variability"** — the
  argument-shape precedent for H1: quantify the noise, prescribe the
  repetitions (≈900k data points, 835 servers). Cite with precision: their
  CONFIRM tool uses nonparametric CI-width stopping, **not** formal
  hypothesis-test power analysis, and "blocked design" is this thesis's term.
- **Mytkowicz et al. (ASPLOS 2009)** — measurement bias ("Producing Wrong
  Data Without Doing Anything Wrong!"); the lineage does not start in 2018.
- **Hoefler & Belli (SC 2015)** — report distributions and CIs, not means.

## 2.5 Tail latency

- **The Tail at Scale** — Dean & Barroso (CACM 2013): shared resources →
  latency variability → service-quality damage; the intuition L3 distilled
  into "recovery time predicts the score", which found no stable relationship
  in this regime (recovery's two-phase split is unstable run-to-run).

TODO(author): closing paragraph — what is *not* in the literature (no
peer-reviewed paper frames pod-delete as churn vs contention, identifies
conntrack/EndpointSlice reconvergence as the dominant mechanism under
pod-delete on small clusters, or compares 6+ placement strategies under
chaos; see references.md §8 for database coverage and its limits).
