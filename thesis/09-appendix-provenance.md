# Appendix A — Run provenance · Appendix B — Negative findings

> **Artifact availability.** All 17 run archives below, together with the
> conntrack protocol-probe bundle, SHA-256 checksums, and a claims→archives
> README, are published as a versioned public dataset:
> **DOI [10.5281/zenodo.20639146](https://doi.org/10.5281/zenodo.20639146)**
> (CC-BY-4.0, published 2026-06-11). Reviewers do not need repository access
> to verify any quoted number.

## A. Archived-run provenance

Generated from the per-archive `artifact-manifest.json` files in
[`chaosprobe/dist/`](../chaosprobe/dist) (schema 2.0.0). Shared environment
fingerprint across **all** archives: Kubernetes **v1.28.6**, kube-proxy mode
**ipvs** (conntrack `maxPerCore` 32768, `min` 131072, TCP established 24 h /
close-wait 1 h), containerd 1.7.11, Ubuntu 22.04.3 LTS, Calico CNI, namespace
`online-boutique`, git `dirty: false`, `provenanceWarnings: []`.

> Naming note: a run's `results/<timestamp>/` directory is stamped at launch,
> its `runId` at archive time — they can differ by seconds-to-minutes (e.g.
> `results/20260607-193021` ↔ `run-20260607-193053`). The runId is canonical.

| runId | batch | commit | archive | strategies × fault | i | role |
|---|---|---|---|---|---|---|
| run-20260607-123106 | 2026-06-07 | `50773a2c6f57` | `run-20260607-123106.tar.gz` | baseline, colocate, default, spread × {pod-delete, node-memory-hog} | 4 | node-memory-hog negative finding (Appendix B) |
| run-20260607-193053 | 2026-06-07 | `41e2c9a3e8bd` | `run-20260607-193053.tar.gz` | baseline, colocate, default, spread × load-contention | 4 | H4 batch A |
| run-20260607-221822 | 2026-06-07 | `829a52adb693` | `run-20260607-221822.tar.gz` | baseline, colocate, default, spread × load-contention | 4 | H4 batch B (clean replication, 0 taints) |
| run-20260608-070638 | 2026-06-08 | `a881a12344be` | `run-20260608-070638.tar.gz` | all 8 × load-contention | 4 | H5 (cross-node fraction) |
| run-20260608-233543 | 2026-06-08 | `68151857f9a4` | `run-20260608-233543.tar.gz` | all 8 × pod-delete | 3 | campaign **s01** |
| run-20260609-072731 | 2026-06-09 | `c8511f2fa093` | `run-20260609-072731.tar.gz` | all 8 × pod-delete | 3 | campaign **s02** |
| run-20260609-154457 | 2026-06-09 | `c8511f2fa093` | `run-20260609-154457.tar.gz` | all 8 × pod-delete | 3 | campaign **s03** |
| run-20260609-194339 | 2026-06-09 | `a80d25b5cb6f` | `run-20260609-194339.tar.gz` | all 8 × pod-delete | 3 | campaign **s04** |
| run-20260610-050703 | 2026-06-10 | `a80d25b5cb6f` | `run-20260610-050703.tar.gz` | all 8 × pod-delete | 3 | campaign **s05** |
| run-20260610-090416 | 2026-06-10 | `a80d25b5cb6f` | `run-20260610-090416.tar.gz` | all 8 × pod-delete | 3 | campaign **s06** |
| run-20260610-130249 | 2026-06-10 | `a80d25b5cb6f` | `run-20260610-130249.tar.gz` | all 8 × pod-delete | 3 | campaign **s07** |
| run-20260608-194827 | 2026-06-08 | `97799efa5ea5` | `run-20260608-194827.tar.gz` | colocate, default, spread × node-drain | 1 | H6 two-point batch (*i* = 1) |
| run-20260608-205229 | 2026-06-08 | `3d437cdd2e4a` | `run-20260608-205229.tar.gz` | colocate, default, spread × node-drain | 3 | H6 two-point batch (*i* = 3) |
| run-20260610-172430 | 2026-06-10 | `6696e6be175d` | `run-20260610-172430.tar.gz` | 6 placing strategies × node-drain | 3 | H6 gradient (doctor-clean) |
| run-20260610-200013 | 2026-06-10 | `e543fbb9e5fe` | `run-20260610-200013.tar.gz` | spread × pod-delete | 1 | H2 protocol probe (spread) |
| run-20260610-201131 | 2026-06-10 | `e543fbb9e5fe` | `run-20260610-201131.tar.gz` | colocate × pod-delete | 1 | H2 protocol probe (colocate) |
| run-20260610-202426 | 2026-06-10 | `e543fbb9e5fe` | `run-20260610-202426.tar.gz` | all 8 × load-contention | 4 | H5 **batch 2** (doctor-strict 0 errors; launching tree dirty in non-code files only: deck binary + 3 figure PNGs) |

"All 8" = baseline, default, colocate, spread, adversarial, random, best-fit,
dependency-aware. "Independent single-commit session" means each session ran
on one commit throughout; sessions may share a commit (s02/s03; s04–s07) —
independence is per *invocation/day*, not per commit.

Each archive carries the SHA-256 of the exact scenario YAML injected (e.g.
s07 `pod-delete.yaml` = `d1a57729…c79a58`) and of every data file
(`summary.json`, per-strategy JSON, charts).

### A.1 Claims → runs mapping

| Claim | Archived run(s) |
|---|---|
| **H1, H2, H3** (7-session churn campaign, 147 iterations) | s01–s07 = run-20260608-233543, run-20260609-072731, run-20260609-154457, run-20260609-194339, run-20260610-050703, run-20260610-090416, run-20260610-130249 |
| **H4** (load contention, two batches) | run-20260607-193053 (A) + run-20260607-221822 (B, clean) |
| **H5** (cross-node fraction, 8 strategies, two batches) | run-20260608-070638 (batch 1) + run-20260610-202426 (batch 2) |
| **H6** (node-drain blast radius, two batches) | run-20260608-194827 (*i* = 1) + run-20260608-205229 (*i* = 3) (run dirs contain colocate/default/spread; H6 quotes the colocate-vs-spread contrast) |
| **H6 gradient** (6 strategies × node-drain × *i* = 3) | run-20260610-172430 (`doctor --strict` clean; observed blast = predicted for all 6 strategies, Spearman ρ = 1.0) |
| **H2 protocol-composition probe** (2 × *i* = 1 pod-delete, spread/colocate) | run-20260610-200013 (spread) + run-20260610-201131 (colocate); raw 5-s protocol samples in `thesis/data/conntrack-probe/` |
| **H7** (discussion-tier, §8.2) | derived from s01–s07 (analysis-only; `campaign_status.py`) |
| Pooled pilot (H1/H2/H3 corroboration only) | pre-campaign mixed-version `results/` runs — **pilot tier, not archive-clean; never quote as findings** |
| node-memory-hog / hog negative findings | run-20260607-123106 (+ 1-iteration 100% probe, unarchived diagnostic) |

## B. Negative findings

These are deliberate, documented dead-ends — kept because they constrain what
fault classes can test placement on this class of cluster.

### B.1 CPU hog faults are absorbed by CFS (scores ≈ 100)

- `pod-cpu-hog` is CFS-capped at the container's own 200m CPU limit: the
  stress consumes the victim's quota, the app stays up, resilience score
  ≈ 100, no user impact. Its CPU-throttling side-signal is corroborating
  only (§5.2).
- `node-cpu-hog` loads the node, but CPU *requests* guarantee the light app
  pods their shares — the app remains responsive; score ≈ 100.
- Consequence: genuine contention must come from **load** (the 200-user
  Locust spike of H4/H5), not synthetic hogs.

### B.2 node-memory-hog self-eviction autopsy

The *i* = 4 batch (run-20260607-123106) showed every node-memory-hog cell
scoring ≈ 100; a follow-up 1-iteration probe at
`MEMORY_CONSUMPTION_PERCENTAGE: 100` produced **zero** MemoryPressure, OOM,
or app-pod evictions (worker steady ≈ 75% memory). Root cause, confirmed
against the `litmus-go` source: the percentage is computed against node
**capacity** and **clamped to allocatable** (not free memory), with no safety
margin — on an already-utilized node the stressor cannot sustain its target,
and the **helper pod is the kubelet's first eviction victim**: it self-evicts
before any app pod is touched (per `litmus-go`'s
[`calculateMemoryConsumption`](https://github.com/litmuschaos/litmus-go/blob/master/chaoslib/litmus/node-memory-hog/lib/node-memory-hog.go):
percentage of node capacity, clamped to allocatable, no in-use accounting).
No percentage or
absolute-MiB setting fixes this on 4 GiB workers. Memory pressure becomes
placement-sensitive only via multi-replica + node-level exhaustion (§8.2,
option b path).

### B.3 H7 — target-scoped cross-node fraction (suggestive only)

Recorded here for completeness: the global cross-node fraction does not
predict conntrack flush (single-session ρ ≈ 0.02); scoping the fraction to
edges incident on the chaos target tracks it slightly better and firmed
mildly across the campaign (ρ ≈ 0.34 target-scoped vs ≈ 0.30 global at 7
sessions). Underpowered, wobbly across interim reads — discussion-tier
material (§8.2), not a claim.
