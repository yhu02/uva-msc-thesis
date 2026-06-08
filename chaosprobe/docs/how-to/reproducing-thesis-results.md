# Reproducing the thesis results

This document lists the exact configuration used to produce the numbers reported in the MSc thesis defence. Following these steps on a comparable cluster should reproduce the two mechanism-metric findings (conntrack flush, CPU throttling) and the contention-vs-churn attribution. The aggregate resilience score is **not** expected to reproduce a stable strategy ordering — its non-reproducibility is itself a headline finding (M4), so a reproducing run is judged on the mechanism metrics below, not on a strategy leaderboard.

## Cluster

Provisioned per `chaosprobe/proxmox-setup.md`:

| Node | Role | vCPU | RAM | Disk | OS |
|------|------|------|-----|------|----|
| cp1 | Control plane | 2 | 2 GiB | 20 GB | Ubuntu 22.04 |
| w1 | Worker | 2 | 4 GiB | 20 GB | Ubuntu 22.04 |
| w2 | Worker | 2 | 4 GiB | 20 GB | Ubuntu 22.04 |
| w3 | Worker | 2 | 4 GiB | 20 GB | Ubuntu 22.04 |
| w4 | Worker | 2 | 4 GiB | 20 GB | Ubuntu 22.04 |
| **Total** | | **10** | **18 GiB** | | |

K8s v1.28.6 • Calico CNI • containerd 1.7.11 • cgroup-v2 (Ubuntu 22.04 default — required for PSI).

Heterogeneous workers were standardised at 4 GiB in PR #25; running on a cluster with non-uniform workers re-introduces a confounder the thesis explicitly rules out.

## Workload

[Online Boutique](https://github.com/GoogleCloudPlatform/microservices-demo) deployed via `chaosprobe/scenarios/online-boutique/pod-delete.yaml`. 11 services (10 polyglot microservices + Redis cart). Single replica per service — `pod-delete` at 100% therefore guarantees full unavailability.

Load: steady-state Locust profile, 50 users at 10 req/s. The built-in locustfile drives the catalog and cart paths.

## Fault matrix

The **core thesis matrix** is two fault classes, run separately — one churn, one
contention:

| Fault | Class | Scenario file | Notes |
|---|---|---|---|
| `pod-delete` | Churn | `pod-delete.yaml` | CHAOS_INTERVAL=15s, FORCE=true, PODS_AFFECTED_PERC=100, target=`productcatalogservice`, duration=120s |
| load contention | Contention | `load-contention.yaml` | sustained 200-user Locust spike (`--load-profile spike`) is the stressor, target=`frontend`, duration=120s; the chaos fault is a near-no-op `pod-cpu-hog` (CPU_LOAD=1) that only opens the during-chaos window. Metric: during-load route tail latency (p95), via `scripts/contention_routes.py` — **not** the resilience score |

The contention experiment is **load**, not a synthetic hog: hog faults do not
create placement-sensitive contention on this cluster. `pod-cpu-hog` is
CFS-capped at the container's CPU limit, so CPU *requests* keep the light app
pods responsive (smoke test: `colocate` and `default` both scored 100 despite
~3× throttling). `node-memory-hog` cannot induce node pressure either — the
stress helper is the kubelet's first eviction victim and self-evicts before the
app is touched (verified vs litmus-go source + issue #3397; see the scenario's
header). Both hog faults are **negative results, retained but not used** as the
contention experiment. Genuine contention needs real cross-pod competition that
cgroup requests do not isolate — i.e. real *load*: under a sustained 200-user
Locust spike the app pods contend for the node's actual CPU/network/cache beyond
their requests and the inter-service call path becomes the bottleneck.

Baseline strategy uses a trivial `pod-cpu-hog` (1s @ 1% on 0 cores) to validate
the probe + scoring pipeline — expected score 100%, zero recovery cycles.

## Strategies

The **core comparison set** is the three strategies that appear in every run and
carry the reproducible mechanism findings:

```
default,colocate,spread
```

This is deliberately narrow: statistical power matters more than breadth (the
`stats` underpowered-warning fires below 8 valid iterations per group, and the
aggregate score is too noisy to rank a wide matrix — see H1). `default` is the
scheduler-default control; `colocate` and `spread` are the maximal/minimal
contention placements that bracket the literature's "spread is safer" claim.

The other five strategies (`baseline` control plus `adversarial`, `random`,
`best-fit`, `dependency-aware`) are an **appendix-level generality check**, run
only when cluster time allows; they appear in none of the reproducible core
findings. When included, `random` uses `--seed 42`; for the seed-variance
appendix the run is repeated with seeds 42, 137, 271, 314, 1729.

## Invocations

```bash
cd chaosprobe
uv sync

# Provision K8s on Proxmox if you haven't yet.
uv run chaosprobe cluster create --hosts-file hosts.yaml
uv run chaosprobe cluster kubeconfig --host <cp1-ip> --user ubuntu
export KUBECONFIG=~/.kube/config-chaosprobe

# Install infrastructure once.
uv run chaosprobe init -n online-boutique

# Core churn run — 3 strategies, >=8 valid iterations each.
uv run chaosprobe run -n online-boutique \
  --strategies default,colocate,spread \
  --experiment scenarios/online-boutique/pod-delete.yaml \
  --iterations 8 \
  --seed 42 \
  --batch-id thesis-churn \
  --output-dir results/churn

# Core contention run — same 3 strategies, load-contention under a 200-user spike.
# Load is the stressor; the metric is during-load route tail latency (p95).
uv run chaosprobe run -n online-boutique \
  --strategies default,colocate,spread \
  --experiment scenarios/online-boutique/load-contention.yaml \
  --load-profile spike \
  --iterations 8 \
  --seed 42 \
  --batch-id thesis-contention \
  --output-dir results/contention

# Gate data quality BEFORE quoting anything (fails on tainted/low-n/provenance gaps).
uv run chaosprobe doctor -s results/churn/<timestamp>/summary.json --strict
uv run chaosprobe doctor -s results/contention/<timestamp>/summary.json --strict

# Statistics from each summary.json.
uv run chaosprobe stats -s results/churn/<timestamp>/summary.json --json -o results/churn/stats.json
uv run chaosprobe stats -s results/contention/<timestamp>/summary.json --json -o results/contention/stats.json
```

> **Iteration bar.** Quote a number as a *finding* only from **≥ 8 valid (non-tainted,
> non-error) iterations per strategy per fault** — 10 is the target. `doctor`
> reports the tainted/errored count; if valid iterations drop below 8 after
> exclusions, re-run rather than quoting an underpowered cell.

**Appendix runs (optional, only if cluster time allows):** the 5-strategy
generality check and the demoted `pod-cpu-hog` pilot — kept out of the core
matrix above:

```bash
# Generality check: full default set
# (baseline,default,colocate,spread,adversarial,random,best-fit,dependency-aware) — drop -s.
uv run chaosprobe run -n online-boutique \
  --experiment scenarios/online-boutique/pod-delete.yaml \
  --iterations 8 --seed 42 --batch-id appendix-generality \
  --output-dir results/appendix-generality

# Demoted pilot: pod-cpu-hog (CFS-throttling confound — appendix only, never a headline).
uv run chaosprobe run -n online-boutique \
  --strategies default,colocate,spread \
  --experiment scenarios/online-boutique/cpu-hog.yaml \
  --iterations 8 --seed 42 --batch-id appendix-podcpuhog \
  --output-dir results/appendix-podcpuhog

# Ambitious extension (time permitting): destination-scoped network latency/loss.
uv run chaosprobe run -n online-boutique \
  --strategies default,colocate,spread \
  --experiment scenarios/online-boutique/contention-network-loss/experiment.yaml \
  --iterations 8 --seed 42 --batch-id ext-network-loss \
  --output-dir results/ext-network-loss
```

The Locust target URL auto-detects via port-forward; supply `--target-url` if forwarding is unavailable.

## What gets collected

A reproducing run should produce, per iteration:

1. `metrics.recovery` — `deletionToScheduled_ms` + `scheduledToReady_ms` decomposition, per-cycle and aggregated.
2. `metrics.recovery.schedulerEvents` — scheduler + kubelet event timeline.
3. `metrics.podStatus` — per-container OOMKill counts, last-termination state, pressure conditions.
4. `metrics.utilization.pods[<pod>].phases.<phase>` — per-pod CPU/memory utilization fractions.
5. `metrics.latency.summary` — p50/p95/p99 + cross-node stddev + status-code distribution.
6. `metrics.prometheus.phases` — every PromQL bundle aggregated per phase.
7. `metrics.prometheus.metricAvailability` — `{label: bool}` flagging which bundles were collected.
8. `routeView` — Locust-vs-LatencyProber per-route join.
9. `preIterationSnapshot` / `postIterationSnapshot` — cluster-state drift detection.
10. `placement.intendedActualDiff` — intent-vs-actual match rate.

If `metrics.prometheus.metricAvailability` shows `false` for PSI / Felix / etcd_debugging_* labels, the cluster is not cgroup-v2 / not Calico / on a K8s version that renamed those metrics. The remaining analysis still holds, but the affected hypotheses cannot be evaluated.

## Bar for "reproduced"

The thesis rests on two *mechanism* metrics that reproduce across runs (M1, M2); the aggregate resilience score does **not** (M4). A reproducing run is judged on the mechanism metrics, computed over the `{colocate, default, spread}` comparison set that is present in every run — not on a strategy leaderboard:

- **M1 (conntrack flush separates spread from colocate):** under `pod-delete`, `spread` and `default` should flush a large fraction of `conntrack_entries_per_node` during the kill cycle (pre-chaos mean → during-chaos mean ≈ 36–39%), while `colocate` stays roughly flat (≈ −1.6%). Reproduced means `spread` flush > `colocate` flush. `scripts/mechanism_metrics.py` recomputes this directly from each `summary.json`.
- **M2 / H7 (CPU throttling runs counter to the contention model under churn):** under `pod-delete`, `colocate` should produce *less* throttling (`metrics.prometheus.phases.during-chaos.cpu_throttling.mean`) than `default` and `spread`. PSI (`cpu_pressure_some`) should agree where cgroup-v2 is present. This is a bounded, mechanism-layer observation — not a universal refutation of the contention model.
- **Contention experiment (`load-contention` under `--load-profile spike`):** this is the regime where placement moves the *mechanism*. Under a sustained 200-user load, `colocate` (inter-service calls stay node-local) is expected to show lower **east-west inter-service** tail latency than `spread` (every call crosses the network). This is computed from during-load route tails by `scripts/contention_routes.py` (which reads `aggregated.routeViewAggregate`), **not** from the resilience score (under sustained load the score is uniformly degraded / pre-chaos tainted — expected here, not a data-quality failure). Reproduced means colocate's inter-service p95 sits consistently below spread's (~1.3–1.4× across the east-west routes). The **user-visible** layer is **not** expected to separate reproducibly: per H4 (two *i* = 4 batches) the inter-service mechanism replicates but the user-facing magnitude does not, so do **not** quote a user-visible placement win here.

The aggregate `meanResilienceScore` is **not** expected to yield a stable strategy ordering: across the collected runs (≥ 8 valid iterations per strategy) no pairwise difference survives the `chaosprobe stats` Holm-Bonferroni correction, so an *absence* of significant pairs is the expected result (M4), not a sign of divergence. What signals a materially different cluster or workload is divergence on the mechanism metrics — e.g. `colocate` *worse* than `default`/`spread` on `cpu_throttling`, or `colocate` flushing more conntrack than `spread`. The threats-to-validity section of the thesis (slide 13) is the place to look first.

## Reproducibility manifest

Every number quoted as a *finding* must be traceable to an archived, clean-provenance run. Before quoting any run, gate it with `doctor --strict` and **never quote results from a run that fails it** (this is exactly why the original dirty H4 pilot was replaced by two `doctor`-gated *i* = 4 batches). Archive the following so a reviewer can reconstruct any figure or table:

| Requirement | What to archive or record | Where it already lives |
|---|---|---|
| **Raw data** | All `summary.json` files, per-iteration exports, Locust CSVs (incl. `stats_failures.csv`), Litmus `ChaosResult` CRDs, Kubernetes events, pre/post cluster snapshots, and any generated stats CSVs | `results/<timestamp>/` + `chaosprobe export` |
| **Scripts** | Every analysis script behind a quoted number, plus the bundling entry point | `scripts/{score_variance,mechanism_metrics,h3_mechanism_outcome,distribution_charts,contention_routes,fault_taxonomy,archive_run}.py` |
| **Environment** | Kubernetes version, CNI, kube-proxy mode + conntrack settings, container runtime, node counts and mem/CPU, ChaosProbe version, Python version, host OS | `summary.json → overall_results.runMetadata` (`chaosprobeVersion`, `pythonVersion`, `platform`, `kubernetes.*`, `cniHint`, `kubeProxy.{mode, conntrack}`) |
| **Cluster config** | Scheduler settings, topology labels, taints, resource limits/requests, any nodeSelectors/affinity | `scenarios/online-boutique/deploy/*.yaml` + this doc's Cluster table |
| **Randomness** | Base seed, per-iteration seed, strategy order per block | `--seed` (recorded in `summary.json`); seed set documented under Strategies above |
| **Scenario integrity** | SHA-256 of every scenario YAML + workload manifest backing the run | `summary.json → scenarioHashes[].{file, sha256}` (recorded automatically by `run`; `doctor` flags its absence, so `doctor --strict` fails any run that lacks it) |
| **Code integrity** | Git commit hash for ChaosProbe + workload manifests, dirty/clean flag | `summary.json → overall_results.runMetadata.git.{commit, shortCommit, dirty}` |
| **Batch / day identifier** | Which runs were launched together, to separate run-to-run cluster drift from strategy effects | `summary.json → batchId` (set with `run --batch-id`, defaults to the UTC date; emitted by `export` as the `batch_id` column for mixed-run analysis) |
| **Immutable bundle + artifact manifest** | One gzipped tarball per run plus a single `artifact-manifest.json` (run id, batch, commit + dirty flag, K8s/CNI/kube-proxy fingerprint, scenario hashes, and a SHA-256 of every file in the bundle) | `python scripts/archive_run.py --results-dir results/<run>/<ts> -o dist [--strict]` — `--strict` refuses to bless a run with provenance gaps (dirty tree, missing scenario hashes/metadata) |
| **Reviewer packaging** | One archive with raw runs, one with processed tables/figures, one manifest mapping every thesis figure/table → input files + script | build per thesis (see checklist below) |

**Provenance discipline (from the strategy review):**

- Run `doctor -s <run>/summary.json --strict` on **every** summary; exclude or clearly flag any run it fails.
- Report `placementMatchRates` and exclude/flag iterations where the scheduler overrode the intended placement.
- Use `metricAvailability` to distinguish "not collected" from "collected zero" — never read a missing PromQL bundle as a real zero.
- Block runs and randomize strategy order within each block; capture pre/post snapshots so run-to-run drift is modelled, not silently absorbed.
- For any claim quoted as final (not a pilot), prefer **8–10 clean repetitions per strategy** for churn and **6–8 per cell** for a network-latency positive control, per the power analysis in the review.
