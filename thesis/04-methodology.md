# 4. Methodology

<!-- This chapter substantiates contribution claim 3 (the provenance-gated
campaign protocol) and defines the measurement design behind H1–H6. -->

## 4.1 Three-layer measurement design

Each experiment is measured at three layers that need not agree — the study's
unit of explanation is *which layer moves*:

1. **Aggregate score** — the probe-based resilience score (the industry-style
   single number; H1 audits its reliability).
2. **Mechanism metrics** — kernel/network signals: conntrack entries per
   node, CoreDNS tail latency, TCP retransmits, CPU throttling, east-west
   inter-service route tails (H2, H4, H5).
3. **User-visible routes** — per-route tail latency and error rate, with a
   built-in **dependent-vs-control confound check**: *dependent* routes
   touch the chaos target (`productcatalogservice`), *control* routes do
   not, so a genuine fault-specific effect must show on dependent routes
   *beyond* the control route's run-level drift (H3, H4).

TODO(author): prose — why a confound-controlled user layer is the difference
between "correlates with the run" and "caused by the fault path".

## 4.2 Fault classes

| Class | Fault | What it perturbs |
|---|---|---|
| **Churn** | `pod-delete` | Endpoint/identity turnover: EndpointSlice, conntrack, DNS reconvergence |
| **Load contention** | sustained 200-user Locust spike (near-no-op hog placeholder) | Genuine resource contention — the regime hogs cannot create here (Appendix B) |
| **Node failure** | `node-drain` of the target's node | Availability: blast radius and rescheduling |

## 4.3 Campaign design

- **Unit of analysis: the session.** One `run` invocation on one git commit =
  one independent session. The primary campaign is **7 sessions (s01–s07)**,
  each all 8 strategies × *i* = 3 (147 churn iterations total), banked under
  `campaign-results/` and archived to `dist/`.
- **Gating.** Every session must pass `doctor --strict` (provenance + data
  quality) before its numbers are used; failed/partial sessions are discarded,
  not patched.
- **Blocking & controls.** `baseline` is a no-fault control; strategy order is
  fixed within a session; batch/day IDs allow day-level blocking; pre/post
  snapshots bound run drift.
- **Replication discipline.** Mechanism claims require direction-consistency
  across sessions (H2: 7/7); user-layer claims require survival across
  independent batches (H4's did not, and is therefore not claimed).

## 4.4 Statistics

- **Variance partition** of the per-iteration score into between-strategy /
  run-to-run / iteration components; `ICC_strategy` with a **cluster
  bootstrap** 95% CI (resampling sessions, preserving within-session
  correlation).
- **Paired Wilcoxon signed-rank + exact sign tests** across sessions for the
  focal spread-vs-colocate mechanism contrast (H2).
- **Spearman rank correlations** for mechanism→outcome (H3) and
  predictor→tail (H5), plus **TOST equivalence testing** to support
  "decoupled" as a positive statistical statement rather than absence of
  evidence.
- **ART ANOVA** (aligned-rank transform) as a nonparametric factorial helper
  where interactions are examined (e.g. node-drain analyses).
- **Power analysis** for H1: Cohen's *d* of the focal contrast → iterations
  needed for 80% power (α = .05, two-sided), and the minimum detectable
  effect at the *n* actually run.

The argument *shape* follows Maricq et al. (OSDI 2018) — quantify the
environment's variability, then prescribe what repetition can and cannot
detect — with the precision note that their CONFIRM uses nonparametric
CI-width stopping, not formal power analysis. Distribution-first reporting
follows Hoefler & Belli (SC 2015).

TODO(author): notation + formal model (random-effects decomposition), and a
worked example of the cluster bootstrap.

## 4.5 Cluster environment

<!-- Values below are from the archived manifests (dist/*/artifact-manifest.json)
— the citable fingerprint. Pin versions; the H2 mechanism is version-sensitive. -->

- **Cluster**: 5-node kubeadm cluster (1 control plane + 4 workers, 4 GiB RAM
  each) on Vagrant-managed KVM/libvirt VMs on a single host.
- **Kubernetes v1.28.6** (pinned — kube-proxy/conntrack behaviour changed
  materially in v1.31–v1.32, see §6.4), containerd 1.7.11, Ubuntu 22.04.3 LTS
  nodes, Calico CNI.
- **kube-proxy mode: `ipvs`**, conntrack `maxPerCore` 32768 / `min` 131072,
  TCP established timeout 24 h, close-wait 1 h — all archived per run.
- **Workload**: Online Boutique (Google Cloud microservices-demo), 10 polyglot
  gRPC microservices + Redis, single replica per service (the regime under
  study), Locust load generator.

TODO(author): table of per-node specs + a note on host-level
virtualization noise (it is part of what the campaign design absorbs).
