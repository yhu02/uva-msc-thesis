# 4. Methodology

<!-- This chapter substantiates contribution claim 3 (the provenance-gated
campaign protocol) and defines the measurement design behind H1–H6. -->

This chapter defines how the research question of §1.2 is turned into
measurements: the three-layer measurement design (§4.1), the three fault
classes and the negative findings that shaped their selection (§4.2), the
multi-session campaign protocol that is itself a contribution (§4.3), the
statistical methods (§4.4), and the pinned cluster environment every claim
is scoped to (§4.5).

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

The confound-controlled user layer is the difference between "correlates
with the run" and "caused by the fault path", and it is load-bearing for
every user-layer statement in this thesis. The threat it addresses is
run-level drift: on a small virtualized cluster, entire runs are slower or
faster than each other for reasons unrelated to the fault — host load,
cache state, background reconciliation. Any mechanism metric measured in a
slow run will correlate with any latency measured in the same slow run, so
a naive correlation between mechanism and user outcome is uninterpretable.
The control routes break this degeneracy. Because they ride the same run
but do not traverse the killed service, they carry the run-level drift and
nothing else; a genuine propagation from mechanism to user must therefore
show an association on the dependent routes that is both significant and
clearly in excess of the control routes' association. The diagnostic also
works in reverse, and Chapter 5 uses it that way: when the *control* route
correlates with the mechanism more strongly than the dependent route does
(H3: ρ = 0.29 on control vs 0.07 on dependent), the association is
positively identified as a run-level confound rather than merely failing
significance. A within-run rank correlation — computed inside each run,
where run-level drift is constant by construction — confirms the read.

Each layer is read by a different instrument, and the instruments fail
independently — which is itself informative. The aggregate score derives
from LitmusChaos probe verdicts; the mechanism layer from PromQL samples of
kernel and infrastructure metrics; the user layer from the route prober's
active HTTP measurements under Locust traffic. We deliberately *audit* the
industry-style aggregate score rather than redesigning it: H1's question is
whether the instrument operators currently have can support the ranking
task they implicitly use it for, and answering that requires measuring the
score as-is, with its reliability quantified rather than assumed. The
layered design also means no single instrument's blind spot is fatal — a
property Chapter 5 exercises three times, when the score turns out noisy
under churn, saturated under load, and unusable under node drain, while
the other two layers keep reporting.

## 4.2 Fault classes

| Class | Fault | What it perturbs |
|---|---|---|
| **Churn** | `pod-delete` | Endpoint/identity turnover: EndpointSlice, conntrack, DNS reconvergence |
| **Load contention** | sustained 200-user Locust spike (near-no-op hog placeholder) | Genuine resource contention — the regime hogs cannot create here (Appendix B) |
| **Node failure** | `node-drain` of the target's node | Availability: blast radius and rescheduling |

The three classes were chosen to perturb three distinct subsystems, so that
"does placement matter?" gets a per-mechanism answer rather than a pooled
one. Churn (`pod-delete`) destroys and recreates a service's only replica:
the pod's identity, IP, and endpoints turn over, and the cluster's
networking state — EndpointSlice membership, conntrack entries, DNS answers
— must reconverge. This is the disruption window the Kubernetes
SIG-Scalability community tracks with its own network-programming-latency
SLO ([SIG-Scalability SLO](https://github.com/kubernetes/community/blob/master/sig-scalability/slos/network_latency.md)).
Load contention drives the application against its resource limits while
fully available, making latency — not availability — the outcome under
stress. Node failure (`node-drain`) removes a whole failure domain, making
availability the outcome and blast radius the natural metric.

The composition of this fault matrix is itself an empirical result, and we
present the dead-ends as methodological findings because they show that the
*obvious* contention experiments probe the wrong layer on clusters of this
class (Appendix B gives the full autopsies). The standard chaos catalog
offers CPU and memory hog faults as the contention instruments, and all of
them null out here for documented, mechanistic reasons. `pod-cpu-hog` is
capped by the CFS quota at the victim container's own 200m CPU limit: the
stressor consumes the victim's budget, every other pod is untouched, the
application stays up, and the resilience score sits at ≈ 100. `node-cpu-hog`
loads the node, but Kubernetes CPU *requests* guarantee the lightweight
application pods their shares, so they remain responsive. `node-memory-hog`
fails more subtly: its stress helper computes its target against node
capacity, clamps to allocatable, and on an already-utilized 4 GiB worker
becomes the kubelet's *first eviction victim* — it self-evicts before any
application pod feels pressure (confirmed against the `litmus-go` source and
LitmusChaos [#3397](https://github.com/litmuschaos/litmus/issues/3397); a
100%-consumption probe produced zero MemoryPressure, OOM, or app-pod
evictions). The consequence is the design above: genuine contention must
come from *load* — the 200-user Locust spike, under which the application is
actually resource-bound — with the hog fault reduced to a near-no-op
placeholder so the experiment pipeline's phasing is preserved. Studies that
report "no placement effect under CPU hogs" without this analysis would be
reporting a property of the fault, not of placement.

A terminological note: the *churn-versus-contention* distinction used
throughout this thesis is our own framing, not an established taxonomy —
our literature search found no peer-reviewed work that classifies
`pod-delete` as a churn fault distinct from contention faults (§2.5). We
adopt it because the mechanisms differ categorically: a churn fault
perturbs *identity and networking state* (endpoints, conntrack, DNS) and
its user-visible damage is bounded by availability dynamics, while a
contention fault perturbs *resource budgets* and its damage is latency
under load. The negative findings above are what the distinction predicts:
faults marketed as contention instruments that cannot actually create
contention on this cluster produce null results at every layer.

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

The session is the campaign's unit of analysis because it is the unit at
which the dominant noise operates. H1's variance decomposition shows that
37.6% of score variance is run-to-run: whole invocations differ from each
other more than strategies differ within an invocation. Pooling iterations
across runs as if they were independent would therefore understate the
uncertainty of every between-strategy comparison. Treating the session as
the replicate — and requiring effects to reproduce *across* sessions, not
merely within one — is the conservative response.

We state the independence structure plainly. Sessions are independent
*invocations*: each is a separate `run` execution, launched at a different
time, with its own settle phases, its own realized placements, and its own
draw of environmental conditions. Each session ran on a single framework
commit throughout, but sessions are not all on *distinct* commits — s02 and
s03 share one commit, and s04–s07 share another (Appendix A lists the
mapping). Independence is therefore per invocation and day, not per code
version; what the single-commit rule guarantees is that no session mixes
code versions internally, which was a real defect of the pre-campaign
pooled run-set (16 runs across heterogeneous code versions and probe
counts, retained only as a pilot tier).

Two further design choices bound what the campaign can and cannot absorb.
Strategy order is fixed within each session, so any slow within-run drift
loads onto the same strategy positions in every session — a conservative
choice that converts a potential random confound into a constant one, at
the cost of not being able to separate order effects from strategy effects
within a single session (across sessions, the direction-consistency
requirement defends against this). And the `baseline` cell — placement
untouched, no fault injected — runs in every session as a no-fault control,
bounding what the score and the probers report when nothing is done.

The gate deserves its own paragraph because it is the protocol's teeth.
`doctor --strict` is an automated check applied to a finished run before
any of its numbers may be quoted: it verifies per-iteration data
completeness, probe verdicts, placement match rates, and taint status, and
in strict mode additionally the provenance fingerprint — scenario SHA-256
hashes against the committed YAML, kube-proxy mode and conntrack settings,
git commit and dirty flag, and the environment manifest (§3.2). The rule
attached to it is *discard, not patch*: a session that fails the gate is
excluded whole, never repaired by dropping its bad iterations and keeping
the rest, because selective repair is a selection effect on exactly the
noise H1 measures. The rule has been exercised, not merely stated — the
original H4 pilot, whose user-layer effect was the most striking number in
the study at the time, was discarded for dirty provenance and replaced
with two clean batches, which is how the study learned that the effect
does not replicate (§5.4).

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

**Model and notation.** Let *y<sub>sri</sub>* be the aggregate score of
iteration *i* of strategy *s* in session (run) *r*. H1's decomposition uses
the crossed random-effects model

> *y<sub>sri</sub>* = μ + α<sub>s</sub> + b<sub>r</sub> + ε<sub>sri</sub>,

with α<sub>s</sub> the strategy effect (variance σ²<sub>α</sub>),
b<sub>r</sub> the session effect (σ²<sub>b</sub>), and ε<sub>sri</sub> the
residual iteration noise (σ²<sub>ε</sub>). The quantity H1 reports is the
intraclass correlation of strategy,

> ICC<sub>strategy</sub> = σ²<sub>α</sub> / (σ²<sub>α</sub> + σ²<sub>b</sub> + σ²<sub>ε</sub>),

the fraction of total score variance attributable to which strategy was
running — the ceiling on how well *any* ranking procedure based on this
score can do. The components are estimated from the 147 campaign iterations
by the committed `scripts/score_variance.py`; the campaign estimates are
σ²<sub>α</sub> : σ²<sub>b</sub> : σ²<sub>ε</sub> = 3.3% : 37.6% : 59.1%
(§5.1).

**Cluster bootstrap, worked.** A naive bootstrap over the 147 iterations
would treat them as exchangeable, which they are not — iterations within a
session share the session effect b<sub>r</sub>. The cluster bootstrap
resamples at the level the dependence lives: draw 7 sessions *with
replacement* from {s01…s07} (a resample might be s02, s02, s04, s05, s05,
s07, s07), keep every drawn session's iterations intact, recompute
ICC<sub>strategy</sub> on the resampled data, repeat, and take the 2.5th and
97.5th percentiles of the resulting distribution as the 95% CI. This
preserves within-session correlation exactly and yields the interval
[0.014, 0.178] reported in §5.1 — wide, as expected with 7 clusters, but
bounded well below the range where score-based ranking would be feasible.

**Paired tests across sessions (H2).** The focal mechanism contrast is
evaluated at session level: for each session, the median conntrack flush of
`spread` minus that of `colocate`, giving 7 paired differences. The exact
sign test asks only for direction (all 7 positive: *p* = 2 × 0.5⁷ =
0.0156 two-sided); the Wilcoxon signed-rank test adds magnitude ordering
(W = 0, *p* = 0.0225). Both are reported because with *n* = 7 the sign test
is the assumption-free floor and Wilcoxon the more powerful companion.

**Correlation, equivalence, and the factorial helper.** Associations between
layers (H3) and between predictor and outcome (H5) use Spearman rank
correlation, which is invariant to the monotone-but-nonlinear scales these
metrics live on. For H3, a non-significant correlation alone would be
absence of evidence; the decoupling claim is therefore additionally
supported by TOST (two one-sided tests) equivalence testing, which asks
whether the association is *demonstrably small*, not merely undetected.
Where factorial structure is examined (e.g. strategy × phase in the
node-drain analyses), the aligned-rank-transform ANOVA provides a
nonparametric interaction test. **Power analysis** closes the H1 argument:
from the focal contrast's Cohen's *d* = 0.46, the standard two-sample
calculation gives ≈ 73 iterations per strategy for 80% power at α = .05
two-sided; inverting it at the *n* = 3 per session actually run gives a
minimum detectable effect of 2.29 pooled standard deviations — about 51
score points, larger than any gap that exists in the data (§5.1).

The argument *shape* follows Maricq et al. (OSDI 2018) — quantify the
environment's variability, then prescribe what repetition can and cannot
detect — with the precision note that their CONFIRM uses nonparametric
CI-width stopping, not formal power analysis. Distribution-first reporting
follows Hoefler & Belli (SC 2015): Chapter 5 reports medians, tails, and
intervals, not bare means, and the nonparametric toolkit above is the
default throughout.

## 4.5 Cluster environment

<!-- Values below are from the archived manifests (dist/*/artifact-manifest.json)
— the citable fingerprint. Pin versions; the H2 mechanism is version-sensitive. -->

- **Cluster**: 5-node kubeadm cluster (1 control plane + 4 workers, 4 GiB RAM
  each) on Vagrant-managed KVM/libvirt VMs on a single host.
- **Kubernetes v1.28.6** (pinned — kube-proxy/conntrack behaviour changed
  materially in v1.31–v1.32, see §6.3), containerd 1.7.11, Ubuntu 22.04.3 LTS
  nodes, Calico CNI.
- **kube-proxy mode: `ipvs`**, conntrack `maxPerCore` 32768 / `min` 131072,
  TCP established timeout 24 h, close-wait 1 h — all archived per run.
- **Workload**: Online Boutique (Google Cloud microservices-demo), 10 polyglot
  gRPC microservices + Redis, single replica per service (the regime under
  study), Locust load generator.

| Node | Role | vCPU | RAM | OS |
|---|---|---|---|---|
| control plane (×1) | kubeadm control plane | 2 | 12 GiB | Ubuntu 22.04.3 LTS |
| worker (×4) | workload nodes | 2 each | 4 GiB each | Ubuntu 22.04.3 LTS |

All five VMs run on a single physical host under KVM/libvirt, which makes
host-level interference — other VMs' scheduling, host I/O, host memory
pressure — a shared, time-varying influence on every measurement. We treat
this as a property of the environment to be *absorbed by design* rather
than eliminated: it is precisely the noise source that surfaces as the
37.6% run-to-run variance component in H1, that the session-level campaign
protocol blocks against (§4.3), and that the dependent-vs-control route
split controls for within runs (§4.1). The environment values above are not
incidental: the H2 mechanism is contingent on the kube-proxy mode (`ipvs`
here), the conntrack configuration, and the Kubernetes version, all of
which are stamped into every archived run's manifest so that each
mechanism-level claim carries its exact scope (§3.2). What does and does not
port out of this pinned environment is stated once, in §7.2's table.
