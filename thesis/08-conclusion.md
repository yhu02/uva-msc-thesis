# 8. Conclusion & future work

## 8.1 Conclusion

TODO(author): prose synthesis — restate the research question; answer it per
fault class × layer (churn: mechanism yes / user no / score blind; load:
mechanism yes / user not reproducibly; node failure: availability yes, and
opposite in sign to the latency face); name the four contributions (§1.3) and
the boundary of each claim (ch. 7). End on the methodological point: chaos
evaluation of placement needs layered measurement and provenance-gated
replication, not a single score.

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

- **Second H5 batch** — the cross-node-fraction validation is single-batch;
  one replication would lift it to the same evidentiary tier as H2/H4's
  mechanism results.
- **H6 gradient completion** — intermediate-concentration strategies × node
  drain (run in flight at scaffold time; §5.6 stub) to test continuous
  blast-radius scaling.
- **H2 re-attribution** — the protocol-composition probe (§6.3): decompose
  the conntrack flush into TCP-teardown vs UDP/DNS-flush contributions.
- Larger/bare-metal clusters, other CNIs and kube-proxy modes (iptables,
  nftables), production traffic, multi-replica workloads, and scheduler
  integration of the cross-node fraction as a scoring plugin.

TODO(author): prioritize + close.
