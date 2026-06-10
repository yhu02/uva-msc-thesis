# 3. The ChaosProbe framework

<!-- System chapter. Deep reference: chaosprobe/TECHNICAL.md and
chaosprobe/docs/. This chapter substantiates contribution claim 2. -->

**Figure 3.1 (stub): ChaosProbe experiment workflow** — placement mutation →
settle → load + chaos injection → post-chaos probing → aggregation →
provenance stamping → archive. `TODO(author): export the workflow diagram;
source candidates exist in the presentation deck.`

## 3.1 Architecture

TODO(author): one prose paragraph per bullet; keep the bullets as the
section skeleton.

- **CLI** (`chaosprobe`): `init` / `run` / `doctor` / `stats` / `report` /
  `recommend` / `archive`, plus cluster lifecycle (`cluster vagrant ...`).
  `run` is self-healing (installs missing infra) so experiments are
  re-entrant.
- **Placement mutator & strategies**: deployment-level `nodeSelector`
  mutation realizing eight strategies — `baseline` (no-fault control),
  `default` (K8s scheduler), `colocate`, `spread`, `adversarial`
  (worst-fit hotspot), `random` (seeded), `best-fit` (bin-packing),
  `dependency-aware` (BFS partition of the service graph). Placement match
  rates are recorded per iteration (`placementMatchRates`).
- **LitmusChaos runner**: drives ChaosEngine experiments (`pod-delete`,
  CPU/memory hogs, `node-drain`) with per-iteration settle → chaos → post
  phasing, retry-with-best-attempt quality handling, and cleanup guards
  (e.g. uncordoning leaked cordons after node-drain).
- **Probers (cross-layer)**:
  - *Prometheus mechanism metrics* — `conntrack_entries_per_node`, CoreDNS
    request latency, TCP retransmits, CPU throttling, kube-proxy programming
    latency (where scraped).
  - *Route latency prober* — per-route user-visible p50/p95/p99 + error rate,
    split into fault-**dependent** vs **control** routes.
  - *Redis, disk, resource probers* — application state, I/O, and
    node/container resource trajectories.
  - *EndpointSlice snapshots* — ready-endpoint counts sampled every 15 s
    through the chaos window; the outage-trough basis of the H6 blast-radius
    metric.
- **Load generation**: Locust profiles (steady and 200-user spike) against
  Online Boutique's user-facing routes.
- **Storage**: structured per-iteration JSON (`summary.json`) plus a **Neo4j
  graph** of services, dependency edges, and pod→node placements. H5 makes
  the graph analytically load-bearing: the cross-node call fraction is
  computed from its edges joined with `podPlacements`.
- **Analysis commands**: `doctor` (data-quality gate, `--strict` for
  provenance), `stats` (pairwise statistics), `report` (HTML/Markdown),
  `recommend`, and the campaign scripts (`scripts/score_variance.py`,
  `mechanism_metrics.py`, `h3_mechanism_outcome.py`, `cross_node_fraction.py`,
  `blast_radius.py`, `campaign_status.py`).

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

TODO(author): paragraph on why provenance is a first-class feature (the
advisory review's "raw artifacts missing" blocker; the dirty-pilot H4 lesson
in §5.4).
