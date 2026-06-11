# Concepts

Background and rationale for ChaosProbe. This page explains the *why*; for
step-by-step tasks see the [how-to guides](../index.md),
and for the deep methodology (data flow, output schema, scoring formulas,
statistical derivations) see [`../../TECHNICAL.md`](../../TECHNICAL.md).

## What ChaosProbe is

ChaosProbe runs [LitmusChaos](https://litmuschaos.io/) experiments against
Kubernetes deployments and collects structured experiment data — recovery
times, latency, resource usage, probe verdicts — into a Neo4j graph, then turns
that data into statistically-grounded comparisons of **pod placement
strategies** under chaos. It exists to answer a research question: *under which
chaos fault classes does pod placement measurably affect mechanism-level
behaviour and user-visible outcomes — and when do aggregate resilience scores
obscure those effects?* This is deliberately a **fault-class-by-measurement-layer
study**, not a hunt for a single "best" strategy: a placement effect can appear
at one layer (e.g. a kernel/network mechanism) without reaching another (the
user-visible outcome), and an aggregate score can hide both. See
[hypotheses & findings](hypotheses.md) for the falsifiable hypotheses (H1–H5)
and their bounded claims.

## The AI feedback loop

The end-to-end pipeline is designed as a loop that produces ML-ready evidence
and feeds decisions back into the system:

```
run experiments → collect to Neo4j → label & align (ML export)
      ↑                                        │
      └──────── recommend / edit placement ◀───┘
```

1. **Deploy** a cluster (Vagrant / Kubespray) and **init** the infrastructure.
2. **Run** the experiment matrix across placement strategies.
3. **Collect** results, metrics, anomaly labels, and time-series into Neo4j.
4. **Export** aligned, labeled datasets for anomaly-classification and
   remediation models.
5. **Recommend** the placement strategy the evidence supports (`recommend`),
   and **compare** before/after a change to evaluate it.

The `recommend` command closes the loop on the analysis side: it consumes the
comparative statistics and renders an explicit, defensible decision rather than
leaving the reader to eyeball a table.

## Placement strategies

A placement strategy decides which node each pod lands on (via `nodeSelector`
injection). ChaosProbe ships several so their resilience can be compared head to
head:

| Strategy | Idea |
|---|---|
| `baseline` / `default` | No placement intervention — the scheduler's own choice. |
| `colocate` | Pack interdependent pods onto the same node (best latency, worst blast radius). |
| `spread` | Distribute pods across nodes (worst latency, best fault isolation). |
| `random` | Seeded random placement; iterations vary the seed to sample the distribution. |
| `adversarial` | Deliberately worst-case placement, to probe the lower bound. |
| `best-fit` | Bin-packing by resource fit. |
| `dependency-aware` | Placement informed by the service dependency graph. |

The central trade-off is **colocation latency vs. spread fault-isolation** — the
experiments quantify where each strategy sits on that curve. See
[`../../TECHNICAL.md`](../../TECHNICAL.md) (§7, Placement Experiment Design) for
the experimental controls.

## Resilience scoring

Each iteration yields a `resilienceScore` from probe-verdict success rates,
plus a separately-reported recovery split (deletion→scheduled, scheduled→ready)
that is *not* part of the score. The exact formula and weighting are in
[`../../TECHNICAL.md`](../../TECHNICAL.md) (§6).

## The statistics, and why {#statistics}

Resilience under chaos is noisy, small-n, and rarely normally distributed, so
ChaosProbe uses non-parametric methods throughout:

- **Bootstrap confidence intervals** — distribution-free CIs for per-strategy
  means (no normality assumption).
- **Mann-Whitney U** — rank-based test for "is strategy A better than B?",
  robust to outliers and non-normal data.
- **Holm-Bonferroni correction** — controls the family-wise error rate across
  the many pairwise comparisons in an 8-strategy matrix.
- **Cliff's delta** — a non-parametric *effect size*, so a result can be
  statistically significant *and* reported as small/medium/large.
- **Power analysis** (`power`) — how many iterations are needed to detect a
  target effect, so "n is too small" can be answered with a number.

This is why `stats`, `power`, and `recommend` exist as separate commands: they
report evidence, sample-size adequacy, and the resulting decision respectively.
Derivations are in [`../../TECHNICAL.md`](../../TECHNICAL.md) (§11).

## Supported chaos experiments

Any LitmusChaos experiment works via ChaosEngine YAML. Commonly used:

- **Pod:** `pod-delete`, `container-kill`, `pod-cpu-hog`, `pod-memory-hog`,
  `pod-io-stress`
- **Network:** `pod-network-loss`, `pod-network-latency`,
  `pod-network-corruption`
- **Node:** `node-cpu-hog`, `node-memory-hog`, `node-drain`

## System architecture

```
ChaosProbe CLI (cli.py + commands/)
      │
      ├── Cluster Manager — Vagrant (local) / Kubespray (production)
      ├── Setup Manager — Helm, LitmusChaos, ChaosCenter, metrics-server, Prometheus, Neo4j
      ├── Config Loader — directory-based, auto-classifies by kind (+ validator)
      ├── Infrastructure Provisioner — applies raw K8s manifests
      ├── Placement Engine — Strategy + Mutator (nodeSelector injection, rollout)
      ├── Chaos Runner — ChaosCenter GraphQL API (save, trigger, poll)
      ├── Result Collector — ChaosResult CRDs
      ├── Metrics Collection — RecoveryWatcher, continuous probers (latency,
      │     throughput, resources, Prometheus, per-node protocol-labeled
      │     conntrack), anomaly labels, cascade timeline
      ├── Storage — Neo4j graph store (topology, runs, metrics, time-series)
      ├── Graph Analysis — blast radius, topology comparison, colocation impact
      └── Output — visualization, ML export, comparison engine
```

The module-level reference for each of these is in
[`../../TECHNICAL.md`](../../TECHNICAL.md) (§2).
