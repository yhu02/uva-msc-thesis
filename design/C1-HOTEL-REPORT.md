# C1 report — dose-response, **hotelReservation** (external validity, exploratory)

**Scope.** Exploratory **external-validity** replication of C1 / **H1**
(east-west dose-response) on a second, structurally different workload —
DeathStarBench **hotelReservation** (deep frontend fan-out, per-service
datastore pairs, Go gRPC over **Consul** service discovery). Reported **outside
the frozen confirmatory Holm family** (which is closed on online-boutique); it
adjusts no confirmatory verdict. H2/H3 do not apply to pod-delete
dose-response (H3 is the C2-hotel report).

**Provenance.** Data: 8 complete-block sessions, hotelReservation, collected
2026-06-20/21 on `main` commit `bdf1ccb` (all 32 C1+C2 sessions
`runMetadata.git.dirty = false`). Frozen pre-analysis manifest:
`c1-c2-hotel-manifest.sha256` (raw `summary.json` + per-condition files);
deposited DOI [10.5281/zenodo.20792129](https://doi.org/10.5281/zenodo.20792129).
All 8 sessions `doctor --strict` clean (0 errors); **1 of 120 churn iterations** (one
iteration of session order-seed 8) carried an `app_ready_timeout` taint and is
excluded by the analysis per the registered healthy-only rule.

## Campaign as run

Complete-block design identical to the online-boutique C1: every session visits
all five cross-node-fraction levels `f ∈ {0, 0.25, 0.5, 0.75, 1.0}` in
randomized order (recorded seed), `r = 1`, `pod-delete` churn on `search` (the
deep `/hotels` path: frontend→search→{geo,rate}) + host-side Locust. 8 sessions
(order-seeds 1–8, solver-seed 0), 5 levels × 3 iterations = 120 churn iterations.

**Tooling required to measure hotelReservation** (all merged to `main`, general
ChaosProbe fixes, not study-specific knobs): a **wget-capable probe pod** for the
readiness gate (`require_wget`, #322 — the litmus subscriber-infra pod that sorts
first in hotel's namespace has a shell but no wget, which had silently failed
every gate probe), a **static-`topology.json` fallback** for the cross-node
fraction (#324 — Consul/gRPC services expose no `*_SERVICE_ADDR` env deps so the
env-derived dependency graph is empty), and the **sustained-during-gate warm-up**
(`--gate-sustained-load --gate-load-concurrency 6 --pre-gate-warmup 30`, #317/#318/#321).
With these, the gate passes untainted on hotel's stack (validated: 0 taints
across all but one of the 144 C1+C2 iterations).

## H1 — dose-response of the east-west tail

Registered primary test (`01-PREREGISTRATION.md` §H1): a **Page's L trend
test** over the five ordered levels, predicting a **monotone increase** in median
east-west p95 latency (`ew_p95_pre_ms`, per-iteration median over inter-service
routes of the route p95, pre-chaos window, loadgen→ excluded). Unit = the
session-condition median over untainted iterations. D3 UDP-slope taint OFF
(deviation D-2026-06-14-02), as in the OB C1.

| | value |
|---|---|
| complete blocks | 8 sessions |
| Page's L | **328.0** (z = −2.263) |
| p (one-sided, for increase) | **0.9882** |
| per-level median `ew_p95_pre_ms` (ms) | 9.34 / 6.44 / 6.32 / 7.10 / 5.40 |
| SESOI effect f0→f1 | 9.34 → 5.40, **Δ = −42.2 %** (SESOI ≥ 15 %) |

**Outcome: H1 is NOT supported on hotelReservation.** The registered
one-sided test for a monotone *increase* is non-significant (p = 0.99); the
observed trend is, if anything, mildly **downward** (the z statistic is negative,
and the f=0 level carries the highest median tail). The east-west p95 baseline
sits in a tight ~5–9 ms band across all five placement fractions with no
dose-ordering — co-locating vs. spreading hotel's services across nodes does not
move the inter-service latency tail in the predicted direction (or detectably at
all).

**Reading the verdict (external validity).** This **corroborates the
online-boutique C1**, and slightly strengthens its negative reading: OB detected
a statistically significant but **sub-SESOI** monotone increase (p = 0.0002,
13.35 % < 15 % SESOI) — a real but trivially small effect. hotelReservation shows
**no increase at all** on a deeper, Consul/gRPC topology. The earlier "spread reduces
east-west latency" intuition does not generalize to a second workload: across two
structurally different applications the placement→east-west-latency dose-response
is absent or negligible.

## Limitations

- **Exploratory, not confirmatory.** Outside the frozen Holm family; no
  multiplicity correction is applied and none is owed — this is a generalization
  probe, not a registered test.
- **Cluster scale.** Single libvirt cluster (1 control-plane + 8 workers);
  hotel is the lightest DeathStarBench app and the only realistic candidate at
  this scale. Absolute latencies (~5–9 ms) are small and may compress any
  placement effect that a larger fan-out or a more loaded cluster would expose.
- **One excluded iteration** (session 8, post-pause restart) — a single
  `app_ready_timeout`, 0.8 % of C1 iterations; the registered healthy-only rule
  excludes it. No other taints.
- **D3 slope-taint OFF** mirrors the OB C1 disposition (D-2026-06-14-02); the
  frozen low-churn D3 band does not generalize to per-level re-placement.
