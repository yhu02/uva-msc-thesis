# 6. Discussion

## 6.1 Layered decoupling holds across both fault classes

Under churn, placement leaves a large, reproducible footprint at the kernel
layer (H2) that never reaches the user (H3), while the aggregate score is too
noisy to see anything at all (H1). Load contention — the regime where the
user-visible outcome *is* latency — was expected to differ, and does not: the
east-west mechanism reproduces (1.36–1.39×) but the user-layer effect
collapsed in the clean replication (H4). Placement moves the mechanism in
both regimes; the user layer follows in neither.

Once `pod-delete` is understood as a *churn* fault, this is the expected
shape rather than a surprise. The injected event is the disappearance and
recreation of a service's only replica. During the kill window the service
is simply gone: every dependent request fails identically whether the
surviving services are packed on one node or spread across four, because
what governs the user-visible outcome is availability dynamics — the
deletion-to-ready cycle of the single replica — and not the topology around
it. What placement *does* govern is how much networking state the churn
event invalidates: a spread placement holds the target's connections across
node boundaries, so the kill cycle tears down and reconverges a large
fraction of per-node conntrack state (H2's 38.5% median flush), where a
co-located placement holds them node-locally and barely registers (2.7%).
The mechanism layer is exactly where a topology variable should leave its
mark under a churn fault — and the user layer is exactly where it should
not, because the user-facing damage is dominated by the outage itself.
This interpretation is ours; what is measured is the layer split (H2, H3),
and the single-replica scoping of it is stated in §7.2.

Load contention is the regime where the same logic was expected to break:
the application stays fully available, the outcome under stress is latency,
and latency is what locality moves. The mechanism half of that expectation
holds — co-location keeps east-west calls node-local and reproducibly
lowers the inter-service tail in both batches (H4) — but the effect
attenuates before it reaches the user-facing routes, whose during-load
ratios collapsed to ≈1.1–1.4× with no dependency specificity in the clean
batch. In this environment the user-facing tail under a 200-user spike is
dominated by the cluster-wide saturation itself — every route, dependent or
not, is queueing — so the placement-attributable east-west difference is a
second-order term. We mark this as interpretation: what replication
establishes is only that the mechanism effect survives and the user-layer
effect does not (§5.4).

It is worth being precise about *how* the aggregate score fails, because it
fails differently in each regime — and the failure modes compound rather
than cancel. Under churn the score is **noisy**: it varies, but 96.7% of
its variance is run-to-run and iteration noise, so the between-strategy
signal is undetectable at any feasible iteration count (H1). Under load it
is **saturated**: all eight strategies scored 100 in the H5 run, because
the application stays available under contention and availability is what
the probes measure — the score has no dynamic range exactly where the
latency action is (§5.5). Under node drain it is **absent**: the drain
takes down the infrastructure the Litmus probes themselves depend on,
every probe returns `Unknown`, and the score is undefined just when the
availability outcome is most dramatic (§5.6). A single-number instrument
would need to be unreliable in none of these ways to rank placements
across fault classes; this one is unreliable in all three, each for a
different, diagnosable reason. That is the constructive content of H1: not
that scoring is hopeless, but that the failure modes are layer- and
regime-specific, and an evaluation pipeline must know which one it is in.

The decoupling result also returns something to the work it is positioned
against. MicroRes's premise (§2.1) is that resilience consists in
degradation *failing to disseminate* from system metrics to user metrics —
their index scores that dissemination. H3 and H4 are, in effect, a
mechanism-level account of when and why that dissemination fails under a
manipulated variable: under churn the system-layer perturbation
(conntrack reconvergence) is real, large, and placement-controlled, yet
dissemination to the user layer is statistically absent; under load the
east-west perturbation likewise stops short of the user-facing routes.
Where MicroRes treats non-dissemination as the *definition* of resilience
to be scored, this thesis measures it as a *phenomenon* with a fault-class
structure — they score the decoupling, we measure its mechanism — and the
two readings are complementary rather than competing.

The negative findings of Appendix B belong in this picture, because they
explain why the *obvious* experimental designs would have probed the wrong
layer entirely. The catalog contention faults — CPU and memory hogs — are
absorbed by the very resource-isolation machinery (CFS quota, requests,
kubelet eviction ordering) that they try to stress: the hog spends its own
budget, or evicts itself first, and the application never becomes
resource-bound (§4.2, Appendix B). A study that ran "placement × cpu-hog"
and found nothing would have learned a property of the fault, not of
placement. Recognizing the hogs as layer-mismatched instruments — and
replacing them with genuine load — is what made the H4/H5 regime testable
at all, and we present that redirect as a methodological contribution in
its own right.

## 6.2 The trade-off as the operator takeaway

H5 and H6 are the same graph property read on two axes: co-location minimizes
the east-west tail (33.9 ms vs ~43–46 ms) and maximizes node-failure blast
radius (11/11 vs 2/11) and recovery (≈10.3 s vs ≈2.6 s). The cross-node
fraction prices the *latency* face before any chaos; the services-per-node
concentration prices the *availability* face. An operator does not get to
optimize one without paying the other — and neither face is visible in the
aggregate score.

Figure 5.8 renders this as a single picture: per-strategy cross-node
fraction on one axis, with the east-west p95 (H5) and the node-drain blast
radius (H6) as opposing gradients. Reading it left to right, the node-local
placements (`colocate`, `best-fit`) sit at the latency optimum and the
availability pessimum; the spreading placements sit at the mirror point.
The figure is the thesis's headline because both gradients are *measured on
the same placements*, in the same environment, from the same dependency
graph — the latency face under a 200-user load (run-20260608-070638), the
availability face under node drains across two clean batches. The pair
H5+H6 is what elevates the study's mostly-decoupling story into actionable
structure: the placement decision is not "does placement matter?" but
"which face of this measured trade-off does your SLO price higher?"

We are explicit about what is and is not new here. That concentrating
workload into one failure domain enlarges the blast radius when that domain
fails is a known qualitative principle — it is the premise of cell-based
architecture guidance in industry
([AWS Well-Architected](https://docs.aws.amazon.com/wellarchitected/latest/reducing-scope-of-impact-with-cell-based-architecture/reducing-scope-of-impact-with-cell-based-architecture.html))
— and the H6 prediction taken alone is near-definitional (drain a node,
lose the pods on it). The contribution is the *quantification on the
placement axis with both faces measured*: the same placements that H5
prices for latency are drained and priced for availability, the predicted
blast radius materializes exactly in every iteration, and concentration
additionally drives a ~4× recovery-time penalty that is not definitional
(it arises from rescheduling contention when 11 evicted pods restart at
once). H6 as it stands is a two-point contrast between the extremes; the
intermediate-concentration gradient is the natural completion (§5.6, §8.2).

## 6.3 The H2 mechanism: attribution with protocol scoping

The conntrack-flush signature maps onto the reconvergence window the
Kubernetes SIG-Scalability network-programming SLO defines, but the
attribution must be protocol-scoped: upstream maintainers document
kube-proxy's *active* conntrack flush on endpoint churn as **UDP-only**
(kubernetes/kubernetes #48370, #108523, #126130; TCP entries are deliberately
never actively flushed — #100698, #104098). Online Boutique's east-west
traffic is gRPC/**TCP**, so the measured flush is attributed to **kernel-side
TCP teardown on pod-IP removal** (RST/REJECT, CNI cleanup, state expiry) with
the **UDP/DNS flush path as a contributor** — not to kube-proxy alone. A
re-attribution pass plus a protocol-composition probe (which protocol's
entries actually disappear during the kill cycle) is **pending**; until it
lands, the claim stays "a reproducible conntrack reconvergence signature",
mechanism-consistent rather than causally decomposed. Conntrack behaviour
also changed materially across K8s v1.31–v1.32; the result is pinned to
v1.28.6 (§4.5).

Spelled out, the candidate mechanisms behind the measured flush are these.
First, **kernel-side teardown of TCP state when the pod's IP disappears**:
when the deleted pod's network namespace is torn down, established
connections to its IP die — peers receive resets or time out, the CNI
(Calico here) withdraws the pod's routes and addresses, and the conntrack
entries keyed on that IP become dead state that the kernel reaps. None of
this requires kube-proxy's flush path; it is a property of pod-IP lifetime.
Second, **kube-proxy's active UDP flush as a contributor**: the workload's
service discovery is DNS, and DNS traffic is UDP — the one traffic class
for which kube-proxy (in ipvs mode on this cluster) *does* actively delete
conntrack entries on endpoint change.
The measured per-node entry counts cannot distinguish these contributions,
because `conntrack_entries_per_node` is protocol-blind. That is exactly the
gap the pending protocol-composition probe addresses: sampling the conntrack
table by protocol through the kill cycle would decompose the flush into its
TCP-teardown and UDP-flush components. Until then, the defensible statement
is the one H2 makes — placement reproducibly moves a conntrack
reconvergence signature whose direction and magnitude are
mechanism-consistent with Kubernetes networking semantics — and not a
causal decomposition among the candidate paths.

## 6.4 L1–L3: inapplicable in this regime, not refuted

The literature-derived predictions — L1 *colocate is worst* (Bubble-Up,
Quasar), L2 *spread isolates best* (Medea), L3 *recovery time predicts
resilience* (Tail at Scale) — are best described as **inapplicable in the
single-replica churn regime tested**, not refuted: churn is not the
contention regime they were written for; recovery's two-phase split is
unstable run-to-run, so L3 has no stable relationship to find on either side.
Under load — their actual regime — the locality intuition holds at the
mechanism layer but no user-layer ordering is asserted (H4).

Earlier drafts of this work stated these results as refutations — "the
spread-is-safer intuition is disproven" — and the correction is itself one
of the study's findings about how placement advice should be handled. The
contention literature's predictions come with an implicit regime attached:
Bubble-Up and Quasar model steady-state interference between co-located
workloads, and Medea's spread prescription protects multi-replica services
against failure-domain loss. Single-replica `pod-delete` churn satisfies
neither premise — there is no sustained contention, and there is no second
replica for isolation to save — so finding the predictions inert there is
evidence about their *scope*, not their truth. The study's own data makes
the same point from the other side: in the regimes the predictions were
written for, they resurface with the expected sign — under load contention
the locality logic behind L1/L2 holds at the mechanism layer (H4, H5), and
under node failure the isolation logic behind L2 is precisely what H6
measures, with spread limiting blast radius exactly as Medea's reasoning
implies. The practical lesson is that placement advice is *fault-class
advice*: a recommendation like "spread for resilience" is meaningful only
together with the fault class and replication regime it was derived in, and
transplanting it across regimes — as a single aggregate score implicitly
invites — is how operators end up optimizing against the wrong failure
mode.

## 6.5 Practical implications

- **Don't rank placements by one score.** In this regime the aggregate score
  cannot rank strategies at any feasible iteration count (H1); a score-based
  "winner" is noise.
- **Measure the layer your fault class perturbs.** Churn shows up in
  endpoint/conntrack reconvergence; load in east-west tails; node failure in
  EndpointSlice availability troughs. A single user-layer or score-layer
  probe misses all three.
- **Price co-location's two faces.** Co-location is simultaneously the best
  latency placement and the worst node-failure placement here (H5 + H6);
  choose per workload SLO, not per folklore.
- **The cross-node fraction prices the latency face pre-chaos.** It is
  computable from the dependency graph + a proposed placement before any
  experiment — a cheap static screen (with H6's concentration count as its
  availability counterpart).

Each of these is an implication of a measured result, and each inherits that
result's scope. The first follows from H1's variance decomposition, and it
is the one with the sharpest operational edge: an evaluation pipeline that
runs a handful of chaos iterations per placement and picks the
highest-scoring one is — in this regime — selecting on noise, with a
minimum detectable effect (~51 score points at *n* = 3) far larger than any
real between-strategy gap. The second is the constructive alternative the
three-layer design demonstrates: each fault class deposited its placement
signal in a different, *predictable* layer, so instrumenting that layer
(conntrack/EndpointSlice for churn, east-west route tails under load,
ready-endpoint troughs under drain) recovers the signal the score loses.
The third and fourth turn H5+H6 into a screening procedure: given a
dependency graph and a candidate placement, the cross-node fraction prices
the latency face and the services-per-node concentration prices the
availability face, both before any experiment is run — with chaos
experiments then reserved for validating the shortlisted candidates rather
than searching the whole space.

These are statements about this regime and this environment — a
single-replica deployment on a small virtualized v1.28.6/ipvs cluster, per
the scope tables of Chapter 7 — and we do not claim they transfer beyond
it. What is portable is the *method*: manipulate placement, measure at the
layer the fault class perturbs, control the user layer with
dependent-vs-control routes, gate every number on provenance, and audit the
aggregate score's reliability before trusting it to rank anything.
