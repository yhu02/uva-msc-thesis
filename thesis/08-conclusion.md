# 8. Conclusion & future work

## 8.1 Conclusion

This thesis asked: **under which chaos fault classes does pod placement
measurably affect mechanism-level behaviour and user-visible outcomes in a
Kubernetes microservice application, and when do aggregate resilience scores
obscure those effects?** The answer the data supports is layered, and
different for each fault class tested.

Under single-replica `pod-delete` **churn**, placement moves the mechanism
layer, not the user layer, and the score is blind to both. Spreading the
target's dependents reproducibly flushes a large fraction of per-node
conntrack state during the kill cycle (38.5% vs 2.7% median, spread >
colocate in 7/7 independent sessions; H2), yet this most reproducible signal
in the study is statistically decoupled from the fault-dependent user routes
(H3) — and the aggregate score cannot rank the strategies at all, with 3.3%
of its variance between strategies and a minimum detectable effect larger
than any gap that exists (H1). Under **load contention**, the regime where
latency is the user-visible outcome, the same shape recurs: co-location
reproducibly lowers the east-west inter-service tail (1.36–1.39× across two
batches), but the user-layer effect did not survive clean replication, so
none is claimed (H4). Under **node failure**, placement finally moves the
layer that counts there — availability — and it does so with the opposite
sign to the latency face: the co-location that minimizes the east-west tail
(H5's cross-node-fraction separator — the two node-local placements held the
two lowest tails of eight in both batches, joint *p* ≈ 0.0013) maximizes
blast radius and recovery time
(11/11 services offline and ≈10.3 s recovery vs 2/11 and ≈2.6 s; H6). The
aggregate score, finally, obscures placement effects in every regime tested:
too noisy to rank under churn, uniformly saturated under load, and unusable
under drain.

The four contributions of §1.3 carry this answer, each within the boundary
drawn in Chapter 7. The layered-decoupling result, the trade-off pair, and
the score critique (claim 1) are statements about a single-replica
deployment on one small virtualized v1.28.6/ipvs cluster — directions and
layer structure, not absolute values, and never "spread is worse" or a best
strategy. ChaosProbe (claim 2) and the provenance-gated campaign protocol
(claim 3) are the portable parts: any cluster and workload can rerun this
design, and every number quoted here traces to an archived, hash-stamped run
(Appendix A). The negative findings (claim 4) bound which fault classes can
test placement at all on clusters of this class.

The methodological point is the one we would have the field retain. Chaos
evaluation of placement needs **layered measurement and provenance-gated
replication, not a single score**: each fault class deposited its placement
signal in a different layer, a one-number instrument missed all of them, and
the only findings that survived were the ones forced through independent
sessions and strict provenance — including one striking user-layer "result"
that did not.

## 8.2 Future work

### E1 — multi-replica × node-drain (skipped; structurally null as designed)

The natural "positive" experiment — 3 replicas per service, drain a node,
expect `spread` to keep services available while `colocate` loses them — was
piloted and **deliberately skipped**: ChaosProbe's `spread` strategy
implements per-service **deterministic nodeSelector pinning**, which spreads
*different services* across nodes but pins **all replicas of a given service
to one node** (pilot observation: all 3 `productcatalogservice` replicas on
one worker). Draining the target node therefore kills all N replicas
regardless of strategy — the multi-replica availability contrast **cannot
materialize** with the mutator as implemented; the experiment is structurally
null, not informative. Options for future work:

- **(a)** use default Kubernetes scheduling (topology spread) for the
  replicated arm — but then it is no longer a placement-*strategy* contrast;
- **(b)** extend the `spread` strategy with pod-anti-affinity /
  `topologySpreadConstraints` so a service's replicas land on distinct nodes
  (a mutator code change, then rerun E1);
- **(c)** accept H6's single-replica blast-radius result as the availability
  evidence (the option taken in this thesis) and leave multi-replica
  anti-affinity as the headline extension.

### P2 — path-scoped network latency × dependency-aware placement

The cleanest remaining shot at a *user-visible* locality effect: inject
latency on traffic to a central dependency and test whether node-local
placements (and a repaired `dependency-aware` — H5 showed its BFS partition
does not actually co-locate communicating services) beat `spread` on
dependent routes beyond the control gap. Key risk: the fault must actually
differentiate local vs cross-node paths (a packet/path check), else the
placement signal collapses.

### H7 — target-scoped cross-node fraction (discussion-tier)

A static predictor for the *churn mechanism*: the cross-node fraction
restricted to edges incident on the chaos target tracks conntrack flush
slightly better than the global fraction (ρ ≈ 0.34 target-scoped vs ≈ 0.30
global at 7 sessions) — suggestive, underpowered, explicitly **not** a claim;
worth a dedicated campaign.

### Other extensions

- **Third H5 batch / different cluster** — the second batch (§5.5) reproduced
  the two-regime separation but collapsed the continuous correlation
  (ρ 0.79 → 0.25); a third batch on a different cluster would test whether
  the *separation* transfers beyond this environment.
- **H6 gradient: denser concentrations** — the 6-strategy gradient (§5.6,
  observed = predicted blast, ρ = 1.0) is complete; the open questions are
  denser intermediate per-node concentrations and the multi-replica regime,
  plus an explanation for the non-monotone recovery times.
- **H2 flush apportionment** — the protocol-composition probe (§6.3) shows
  both mechanisms (kernel TCP teardown at kills; the placement-dependent
  UDP/DNS pool) but, at *i* = 1 with a ramp-contaminated baseline, cannot
  apportion the campaign's flush percentages between them; a steady-state
  multi-iteration probe would.
- Larger/bare-metal clusters, other CNIs and kube-proxy modes (iptables,
  nftables), production traffic, multi-replica workloads, and scheduler
  integration of the cross-node fraction as a scoring plugin.

Prioritized: the highest-value next step is the multi-replica × node-drain
experiment via option (b) — a small mutator extension unlocks the one regime
this study structurally excluded, where placement *should* finally reach the
user-visible layer, and a null there would be genuinely surprising rather
than designed-in. Close behind it sit the two cheap completions of existing
results — a second H5 batch and the H6 gradient — followed by the H2
protocol-composition probe, which converts the study's most reproducible
signal from mechanism-consistent to causally decomposed. P2 is the ambitious
shot and should be attempted only with the packet/path check in place. The
common thread is the method: every one of these experiments runs on the same
layered, provenance-gated protocol this thesis contributes, and each would
sharpen — not merely extend — the boundary of where placement matters.
