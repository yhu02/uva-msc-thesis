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

Each strategy is exercised against two faults in two separate runs:

| Fault | Scenario file | Notes |
|---|---|---|
| Churn — `pod-delete` | `pod-delete.yaml` | CHAOS_INTERVAL=15s, FORCE=true, PODS_AFFECTED_PERC=100, target=`productcatalogservice`, duration=120s |
| Contention — `pod-cpu-hog` | `cpu-hog.yaml` | 1 core, 100% load, duration=120s, same target |

Baseline strategy uses a trivial `pod-cpu-hog` (1s @ 1% on 0 cores) to validate the probe + scoring pipeline — expected score 100%, zero recovery cycles.

## Strategies

All eight strategies are evaluated in one run:

```
baseline,default,colocate,spread,adversarial,random,best-fit,dependency-aware
```

`random` uses `--seed 42` for reproducibility; for the seed-variance analysis the run is repeated with seeds 42, 137, 271, 314, 1729 (five seeds × five iterations each = 25 random samples).

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

# Churn matrix — 5 iterations per strategy.
uv run chaosprobe run -n online-boutique \
  --experiment scenarios/online-boutique/pod-delete.yaml \
  --iterations 5 \
  --seed 42 \
  --output-dir results/churn

# Contention matrix — same shape, different experiment.
uv run chaosprobe run -n online-boutique \
  --experiment scenarios/online-boutique/cpu-hog.yaml \
  --iterations 5 \
  --seed 42 \
  --output-dir results/contention

# Statistics from each summary.json.
uv run chaosprobe stats -s results/churn/<timestamp>/summary.json --json -o results/churn/stats.json
uv run chaosprobe stats -s results/contention/<timestamp>/summary.json --json -o results/contention/stats.json
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
- **M2 / H7 (CPU throttling refutes the contention model):** under `pod-delete`, `colocate` should produce *less* throttling (`metrics.prometheus.phases.during-chaos.cpu_throttling.mean`) than `default` and `spread`. PSI (`cpu_pressure_some`) should agree where cgroup-v2 is present.

The aggregate `meanResilienceScore` is **not** expected to yield a stable strategy ordering: across the collected runs (≥3 iterations per strategy) no pairwise difference survives the `chaosprobe stats` Holm-Bonferroni correction, so an *absence* of significant pairs is the expected result (M4), not a sign of divergence. What signals a materially different cluster or workload is divergence on the mechanism metrics — e.g. `colocate` *worse* than `default`/`spread` on `cpu_throttling`, or `colocate` flushing more conntrack than `spread`. The threats-to-validity section of the thesis (slide 13) is the place to look first.
