# hotelReservation external validity — attempted, deferred (with the tooling banked)

**Status: DEFERRED.** The second-workload (hotelReservation) external-validity
replication of C1 (V2-H1 dose-response) and C2 (V2-H3 replication-rescue) was
attempted but **not run to data**: a fundamental mismatch between
hotelReservation's slow post-restart recovery and ChaosProbe's
restart-for-clean-baseline methodology makes every iteration's readiness gate
time out (the system is genuinely unavailable at each iteration's start), which
the v2 analysis correctly excludes. The confirmatory family (V2-H1/H2/H3/H5) +
V2-H4/H6 are complete and unaffected; this was the optional exploratory
extension.

## What works (validated live, tooling merged)

hotelReservation is deployed and the full measurement pipeline was validated on
it — the data ChaosProbe collects is clean once the app has recovered:

- **East-west latency face** (the registered V2-H1 primary outcome `ew_p95`):
  11 inter-service routes measured with real p95 (e.g. `frontend->search` ~5 ms,
  `search->geo`, `*->mongodb/memcached`). hotelReservation uses Consul + gRPC and
  exposes no `*_SERVICE_ADDR` env vars, so the new **static-`topology.json`
  east-west route fallback** supplies these routes (the prober TCP-connects, so
  gRPC ports work without a health RPC).
- **North-south user routes** (`user_err`): `/hotels`, `/recommendations`,
  `/user`, `/` all measured with samples and 0 errors once recovered, via the
  new **query-string-preserving** route extraction (these routes error without
  their params).
- **Solver/placement gate** for hotel already passed at N=8 (`m1b-gate-artifact-hotel.json`).

## The blocker (fundamental, not a code bug)

After a rollout-restart of its app services, hotelReservation's frontend cannot
re-resolve its gRPC backends through Consul for **~2–4 minutes** (measured:
`/hotels` returned errors for the full 110 s+ of a post-restart probe, recovering
only later). ChaosProbe restarts all app services before **every** iteration to
establish a clean baseline, so each iteration begins with hotel down. The
app-ready gate (correctly) times out (240 s) and records an `app_ready_timeout`
taint; the v2 analysis excludes tainted iterations. The gate is doing its job —
the system really is not ready — so this is not fixable by relaxing the gate
without measuring chaos on a not-yet-recovered system.

Resolving it would require a research-validity/SUT decision, none a clean fix:
skip the per-iteration restart for hotel (changes the per-iteration baseline),
add a multi-minute post-restart settle (campaign becomes very slow), or speed up
hotel's Consul resolver (deep SUT work). Deferred pending that decision.

## Tooling banked from this attempt (general ChaosProbe improvements)

All converge-reviewed and merged regardless of hotel — they benefit any
Consul/gRPC or query-parameterized workload, and they fixed real fresh-install
gaps:

1. **Static-topology east-west route fallback** — east-west latency for workloads
   without `*_SERVICE_ADDR` env-var dependencies (`PlacementMutator.get_topology_dependency_routes`).
2. **Stateful-infra restart exclusion** (`is_stateful_infra`) — the clean-baseline
   restart no longer cycles datastores / service-discovery / tracing (Consul,
   mongodb, memcached, jaeger, redis), which had wiped Consul's registry.
3. **Frontend Service `:80`** for hotel (portless cluster URLs work for both
   litmus probes and the in-cluster prober).
4. **ChaosCenter fresh-install bootstrap** — policy-compliant managed password
   (litmus 3.x 8–16 char + complexity) and automatic default-project creation
   (a fresh ChaosCenter previously could not be bootstrapped).
5. **Query-string preservation** in north-south latency routes.
6. **North-south-only app-ready gate** by default (`gate_east_west=False`) —
   east-west edges are covered by K8s readiness and still measured, not gating.

## To resume

hotel is deployed and solver-gated; the scenarios (`scenarios/hotel-reservation/
pod-delete.yaml`, `node-drain.yaml`) and topology are in place. The only open
item is the restart-vs-recovery decision above. Pick one (likely: skip the
per-iteration restart for hotel, or a dependency-ordered restart so the frontend
restarts last), validate a clean untainted smoke, then run C1 then C2 mirroring
the frozen design — reported as **exploratory external validity, outside the
Holm family**.
