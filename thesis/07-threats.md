# 7. Threats to validity & scope

<!-- Ported from hypotheses.md "Scope & threats" + scope-of-claims.md.
Keep the two tables in sync with those sources. -->

This chapter states the boundary of every claim. We organize the threats in
the standard vocabulary. **Construct validity** asks whether the measured
quantities mean what the claims need them to mean: whether the probe-based
aggregate score measures "resilience" at all is itself under test (H1
quantifies its reliability rather than assuming it), the conntrack flush
percentage is a *proxy* for reconvergence whose causal decomposition is
explicitly pending (§6.3), and the `metricAvailability` bookkeeping exists
because a missing metric silently read as zero would corrupt the construct
it stands for. **Internal validity** asks whether observed differences are
attributable to the manipulated variable: the threats here are run-level
drift, placement mismatch, and dirty provenance, and the defenses — the
dependent-vs-control route split, within-run correlation,
`placementMatchRates`, and the `doctor --strict` gate — are built into the
design (§4.1, §4.3) rather than applied post hoc. **External validity** asks
how far the results carry: a single small virtualized cluster, one
workload, one Kubernetes version and kube-proxy mode, and a single-replica
deployment bound the claims tightly, which is why §7.2 separates what
generalizes (the method, the mechanism's direction) from what does not (the
absolute values, anything multi-replica). **Conclusion validity** asks
whether the statistics support the inferences drawn: with 7 sessions and
*n* = 3 iterations per cell, the campaign leans on nonparametric tests,
cluster-bootstrap intervals, equivalence testing for the decoupling claims,
and explicit power/MDE calculations rather than significance hunting
(§4.4). The tables below give the threat-by-threat detail.

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

Two of these threats deserve a sentence beyond the table. The
single-replica design is not only a threat but a *scoping instrument*: it
is what makes `pod-delete` a pure churn fault (the outage is total by
construction, so any placement signal must appear at a non-availability
layer), and it is simultaneously what excludes the multi-replica
anti-affinity regime from every claim. And the run-to-run drift row is not
an admission that the environment was too noisy — quantifying that noise
and what it does to score-based ranking *is* H1; the campaign design exists
because the drift was measured, not despite it.

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

The asymmetry of this table is deliberate. The portable row is the first
one: the measurement design, the campaign protocol, and the analysis
scripts transfer to any cluster and workload unchanged, and a replication
on different infrastructure is the most valuable follow-up this study
could receive. Everything below it degrades gracefully from "direction
only" to "not in scope" — and where a row says environment-contingent, the
archived manifests make the contingency checkable rather than vague: a
reader who wants to know whether a result could apply to their cluster can
compare their kube-proxy mode, conntrack settings, and Kubernetes version
against the archived fingerprint instead of guessing.

## 7.3 Explicitly not claimed

Per [`scope-of-claims.md`](../chaosprobe/docs/explanation/scope-of-claims.md):
no universal "best" placement strategy; no "spread is worse/disproven"; no
proven causality for the conntrack mechanism (mechanistic consistency, not
controlled causal proof); no generalization to Kubernetes clusters broadly;
no "the score identifies the best strategy"; no unqualified "reproducible"
(only the mechanism metrics reproduce, and only with the archived rerun
package).

These exclusions are commitments, not disclaimers: each one names a stronger
claim that the data could be mistaken to support and that we decline. The
list was not assembled at writing time; it accumulated during the study, as
earlier, stronger phrasings ("refuted", "spread is disproven", a ~3× user-
layer advantage) failed replication or scrutiny and were retired (§5.4,
§6.4). Where this document's prose and that scope statement could ever be
read to disagree, the scope statement governs.
