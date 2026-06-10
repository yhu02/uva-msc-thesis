# 6. Discussion

## 6.1 Layered decoupling holds across both fault classes

Under churn, placement leaves a large, reproducible footprint at the kernel
layer (H2) that never reaches the user (H3), while the aggregate score is too
noisy to see anything at all (H1). Load contention — the regime where the
user-visible outcome *is* latency — was expected to differ, and does not: the
east-west mechanism reproduces (1.36–1.39×) but the user-layer effect
collapsed in the clean replication (H4). Placement moves the mechanism in
both regimes; the user layer follows in neither.

TODO(author): prose — why this is the expected shape once `pod-delete` is
understood as a churn fault (the killed single replica is simply gone;
availability dynamics, not topology, govern survival) and load as a
mechanism-bounded regime in this environment.

## 6.2 The trade-off as the operator takeaway

H5 and H6 are the same graph property read on two axes: co-location minimizes
the east-west tail (33.9 ms vs ~43–46 ms) and maximizes node-failure blast
radius (11/11 vs 2/11) and recovery (≈10.3 s vs ≈2.6 s). The cross-node
fraction prices the *latency* face before any chaos; the services-per-node
concentration prices the *availability* face. An operator does not get to
optimize one without paying the other — and neither face is visible in the
aggregate score.

TODO(author): prose — connect to figure 5.8; state explicitly that this
quantifies a known qualitative trade-off (AWS cell-based architecture) on
the placement axis, with both faces measured on the same placements.

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

## 6.4 L1–L3: inapplicable in this regime, not refuted

The literature-derived predictions — L1 *colocate is worst* (Bubble-Up,
Quasar), L2 *spread isolates best* (Medea), L3 *recovery time predicts
resilience* (Tail at Scale) — are best described as **inapplicable in the
single-replica churn regime tested**, not refuted: churn is not the
contention regime they were written for; recovery's two-phase split is
unstable run-to-run, so L3 has no stable relationship to find on either side.
Under load — their actual regime — the locality intuition holds at the
mechanism layer but no user-layer ordering is asserted (H4).

TODO(author): prose — this re-framing (vs the earlier "refuted" drafts) is
itself a finding about how placement advice should be scoped by fault class.

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

TODO(author): close with the scope reminder — these are statements about
this regime/environment (ch. 7), with the *method* as the portable part.
