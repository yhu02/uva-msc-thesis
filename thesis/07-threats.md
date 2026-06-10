# 7. Threats to validity & scope

<!-- Ported from hypotheses.md "Scope & threats" + scope-of-claims.md.
Keep the two tables in sync with those sources. -->

## 7.1 Threats and defences

| Threat | Why it matters | Defence |
|---|---|---|
| **Single-replica design** | 100% `pod-delete` guarantees the only instance disappears, which can swamp topology effects. | Scope the claim to *single-replica churn* and layered measurement; multi-replica anti-affinity is named as future work, not claimed. |
| **Small virtualized cluster** | Four 4 GiB KVM/QEMU workers may not generalize. | Claim bounded external validity; report *direction* and *mechanism*, not absolute latency values. |
| **Version sensitivity** | kube-proxy / conntrack behaviour evolves across releases (materially in v1.31–v1.32). | Archive exact Kubernetes (v1.28.6), CNI, runtime, kube-proxy mode/conntrack settings, ChaosProbe commit (`runMetadata` / manifests); present as a measurement study of a specific environment. |
| **Placement mismatch** | The scheduler may not realize the intended placement. | Report `placementMatchRates`; flag or exclude mismatched iterations. |
| **Run-to-run drift** | Iteration noise can dominate (H1: 37.6% run-to-run + 59.1% iteration variance). | Independent single-commit sessions as unit of analysis; strategy order fixed; pre/post snapshots; run modelled as a random/blocking effect; cluster-bootstrap CIs. |
| **Dirty provenance** | Untracked files / missing metadata undermine credibility (the H4 pilot lesson). | Never quote results from runs failing `doctor --strict`; the dirty H4 pilot was replaced by two doctor-gated *i* = 4 batches. |
| **Metric-availability gaps** | Missing PromQL queries can manufacture fake zeros. | `metricAvailability` distinguishes "not collected" from "collected zero" (e.g. kube-proxy programming latency is recorded as *uncollected* here, and anchors the mechanism only conceptually). |
| **Overclaiming causality** | Run-level slowness can confound correlations. | Dependent-vs-control routes + within-run correlation; TOST for equivalence claims; causal language reserved for the manipulated variable (placement). |

## 7.2 What generalizes vs. what does not

| Aspect | Generalizes? | Why / caveat |
|---|---|---|
| The *method* (placement-aware, cross-layer, provenance-gated chaos evaluation) | **Yes** | The framework and analysis discipline are reusable on any cluster/workload. |
| The *direction* of the H2 mechanism effect (spread flushes more conntrack than colocate under churn) | **Direction only** | Reproducible here; absolute values are environment-specific. |
| Absolute metric values (latency, recovery s, flush %) | **No** | Tied to a small virtualized 5-node cluster. |
| Mechanism behaviour (conntrack, kube-proxy sync) | **Environment-contingent** | Depends on CNI, kube-proxy mode (ipvs here), kernel/conntrack settings — archived per run so the scope is explicit. |
| The score-instability finding | **This regime** | Established for single-replica `pod-delete`; "cannot rank placement strategies under session variance", not "scores don't work"; not asserted for multi-replica or contention-dominated regimes. |
| The H5 predictor | **As a validated static screen here** | Coarse (separates node-local from spreading placements), single batch; validation of the metric, not of the locality concept. |
| The H6 trade-off | **Qualitatively known; quantified here** | Two-point contrast, single-replica; the *quantification* is this environment's. |
| Anything about multi-replica / HA failover | **Not in scope** | Structurally excluded by the single-replica design (§8.2). |

## 7.3 Explicitly not claimed

Per [`scope-of-claims.md`](../chaosprobe/docs/explanation/scope-of-claims.md):
no universal "best" placement strategy; no "spread is worse/disproven"; no
proven causality for the conntrack mechanism (mechanistic consistency, not
controlled causal proof); no generalization to Kubernetes clusters broadly;
no "the score identifies the best strategy"; no unqualified "reproducible"
(only the mechanism metrics reproduce, and only with the archived rerun
package).

TODO(author): brief prose framing the tables (construct/internal/external
validity vocabulary if the committee expects it).
