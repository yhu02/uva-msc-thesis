# ChaosProbe — six-month workplan

> **Status: PROPOSAL — the build has not started.** Companion to
> [`00-DESIGN.md`](00-DESIGN.md) and [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md).
> The pre-registration freezes at the end of M2; everything before that
> freeze is preparatory and may be revised. Claim structure (identical in
> all three documents): **H1/H2/H3/H5 = confirmatory family
> (Holm-corrected), H4 = descriptive, H6 = exploratory secondary.**

## M0 — Procurement gate (USER-OWNED, pre-M1b)

Both environments are external dependencies with cost; neither is assumed.

- **Primary cluster hardware:** the pinned **N = 6 workers × 8 GiB (≥4 vCPU
  each)** (DESIGN §7) must be **named and confirmed-to-exist or purchased**
  by the user. **Decision date: DATE-TBD-BY-USER** (informed by M1a's
  quantization report, below). The **8 × 4 GiB fallback** is available but
  changes the solver's reachable-fraction set, so adopting it requires the
  M1b solver gate to run at N = 8. **vCPU-escalation contingency (decide at
  M0):** M1a's report includes the *measured* request sums collected from
  the live earlier cluster (`kubectl` sum of `resources.requests`, ×3 for r = 3)
  — data that exists *before* the buy. If measured vCPU requests exceed
  ~1.3× the DESIGN §7.1 placeholders, procure **6 vCPU/node instead of 4**
  at M0 (DESIGN §7.1); the M1b capacity check then re-verifies against the
  procured hardware. This keeps the escalation decidable at M0 rather than
  discovered post-purchase.
- **Second environment (M5 transfer arm):** managed-Kubernetes **billing
  decision** with **budget owner = the user**. If undecided or unavailable
  by the **end of M4**, the transfer arm is dropped and reported as
  not-attempted (pre-registration, stopping rule 4).
- **M1b is explicitly contingent on M0.** M1a is not — it runs on the
  existing earlier cluster.

## M1a — Solver-feasibility spike (weeks 1–2, EXISTING earlier 4-worker cluster)

The project's top risk (DESIGN §9) is reached fast and cheap, decoupled from
the engine/prober/cluster build.

**Build:** the greedy edge-cut **fraction-targeting solver** plus a
**quantization study**: enumerate the reachable cut-fraction set for Online
Boutique's dependency graph at N ∈ {4, 6, 8} (the live 4-worker cluster
validates the solve→apply→schedule→verify loop; the N = 6/8 enumerations are
analytical). The output — which f-levels are reachable at which N — **feeds
the M0 hardware decision**.

**Exit criteria:**
- **Unit + property tests in CI, green BEFORE any live gate:** the
  achieved-fraction computation validated against an **independent second
  implementation** on hand-computed ground-truth graphs; the rejection rule
  fires on target misses; known-optimal cuts recovered on small graphs with
  known answers.
- **Analytical enumerator validated against live reality at N = 4:** the
  enumerator's predicted reachable-fraction set for the live 4-worker
  cluster must match the live achieved-f outcomes within **±0.02** per
  level. Without this, the N = 6/8 enumerations that feed M0 are an
  unvalidated model; with it, the analytical N = 6 claim inherits
  demonstrated fidelity.
- Quantization report (reachable f per N) delivered to the user for M0,
  **including the measured request sums from the live earlier cluster**
  (`kubectl` sum of `resources.requests`, with the ×3 r = 3 projection) so
  the M0 vCPU-escalation contingency is decidable before purchase.

## M1b — Engine + cluster + full GO/NO-GO gate at the pinned N (contingent on M0)

**Build:** the affinity/topology-spread placement engine (replica-level
podAffinity/antiAffinity + topologySpreadConstraints, replacing the earlier
nodeSelector mutator); provision the pinned primary cluster; deploy Online
Boutique. (The prober first-class-ification and the hotelReservation deploy
are **deliberately moved to M2's prep window** — M1 previously bundled four
hard deliverables against the earlier velocity.)

**Exit criteria:**
- Engine expresses packed and anti-affine **r ∈ {1, 3}** (the contrast the
  skipped E1 pilot could not realize) — verified from live
  `podPlacements`; engine unit tests in CI before the live smoke gate.
- **r = 3 anti-affine is schedulable at the pinned N** (every service's 3
  replicas on 3 distinct nodes; DESIGN §7.1) — explicit criterion, recorded
  in the gate artifact.
- Capacity budget verified: measured request sums (DESIGN §7.1 method)
  confirm ≥30 % headroom at the heaviest cell.
- **Solver gate (go/no-go for the dose-response design), at the pinned N:**
  the solver hits every target f ∈ {0, 0.25, 0.5, 0.75, 1.0} within ±0.05
  on the live cluster for Online Boutique — **3 consecutive attempts per
  f-level**, where an *attempt* is one full solve→apply→schedule→verify
  cycle from a restored (unpinned) state (amended from "clean app deploy"
  per M1B-REPORT §Pre-freeze amendments / pre-registration §M2 freeze
  amendments) and the per-level counter resets on a miss;
  outcome recorded by the committed verification artifact (solver log +
  achieved-f table checked by a `doctor` rule). The gate **must** run at
  the pinned N because the reachable-fraction set is N-dependent (and
  re-runs at N = 8 if the fallback cluster is adopted). **Go** →
  designed-dose H1. **No-go** → the pre-committed
  nearest-achievable-fraction fallback (achieved-f as regressor; H1's
  pre-registered mixed-model / Jonckheere–Terpstra tests), recorded in the
  pre-registration before freeze.

**Overrun handling (pre-declared):** de-scope order = drop the second
workload (hotelReservation) first, then the iptables arm (H6). An
**explicit slack week** sits between M1b and the M2 freeze.

## M2 — Prep window + A/A calibration → power analysis → FREEZE

**Prep window (moved from M1):** the protocol-labeled conntrack prober as a
first-class collector (per-node, per-protocol, 5 s, windows in
`summary.json`) — with **CI tests of its protocol counts against a synthetic
conntrack fixture before the live smoke gate**; deploy **hotelReservation**
(measure its exact service/edge counts, compute its fraction quantum and
capacity budget per DESIGN §7.1, and run the solver gate for its graph at
the pinned N).

**A/A block:** ≥3 identical-placement session pairs through the full
pipeline. Functions (per the pre-registration): **variance-component
estimation** (feeding the power analysis, SESOI noise bands, H3
margin/TOST band, H4 δ margins, and the UDP-slope taint check —
redefined at the freeze to per-f-level slope bands, pre-registration §M2
freeze amendments D3) and a
**qualitative pipeline sanity check**. **No numeric FPR gate** — bounding a
0.05 FPR would need on the order of 60+ A/A tests (see pre-registration
arithmetic). **Any statistically significant A/A finding → investigate,
fix, rerun the A/A block** (scoped at the freeze to registered-unit tests —
pre-registration §M2 freeze amendments D1).

**Freeze:** run power analyses against each SESOI; fill every **TBD**
(per-cell n, H3 margin and equivalence band, H1 noise band, H4 δ,
the per-f-level UDP-slope bands — pre-registration §M2 freeze amendments,
2026-06-12). **H5's sub-score definitions are frozen at the M2
commit, before any reliability data exists.**

**Exit criteria:** A/A block clean (no unexplained significant findings;
**halt rule: a second significant A/A finding after a fix** — registered-unit
scope per pre-registration amendment D1 → halt and
redesign instrumentation); prober round-trips into `summary.json` and
`doctor --strict` passes on a smoke session; hotelReservation service/edge
count measured, capacity check passed, and its solver gate decided (or the
workload de-scoped per the pre-declared order); all TBDs resolved;
**pre-registration frozen by commit hash and DOI-deposited.** No comparative
campaign data exists before this point.

## M3–M4 — Campaigns

- **C1 (dose-response, H1):** complete-block sessions visiting all 5
  f-levels in randomized order × r = 1 × churn + load, n per cell from M2.
- **C2 (replication × drain, H3):** r {1, 3} × mode {packed, anti-affine}
  × node-drain, including the TOST packing control.
- **C3 (placement-dependence + DNS intervention, H2):** cache on/off ×
  f {0, 1} × churn, paired sessions, randomized cache order; two in-family
  primaries combined as a conjunction — (a) between-placement cache-off
  contrast (spread > packed; the packed arm is the comparator) and (b)
  within-spread cache shrinkage ≥50 %; the packed-arm *no-cache-effect*
  expectation is the registered secondary check (not in family).
- **H6 (exploratory, iptables):** the f = 0/f = 1 endpoint cells only,
  riding on C1/C3 endpoints, ≥5 sessions; droppable second in the de-scope
  order.

**Exit criteria (per campaign):** all cells at registered n with
`doctor --strict`-clean, fraction-verified sessions; campaign archived +
DOI before analysis for writing; interim analysis limited to data-quality
checks (no peeking at hypothesis outcomes between campaigns).

## M5 — Primary-environment analyses (decoupled) + optional second environment

**Always (independent of procurement):** compute the **H4 frontier**
(descriptive protocol: cluster-bootstrap CIs, δ dominance margins from M2)
and the **H5 scorecard reliability** evaluation — both depend **only on
primary-environment data** and complete regardless of the second
environment. H5 is evaluated **exclusively on campaign sessions**
(never the earlier sessions), against both bars (beats ICC_old; absolute ICC ≥ 0.5).

**If the second environment landed (M0):** run the reduced replication
subset (C1 endpoints + C2 interaction cells) on it (different CNI or managed
Kubernetes); direction-only transfer test. **If not available by end of M4:
dropped, reported as not-attempted** (pre-registration, stopping rule 4).

**Exit criteria:** frontier + reliability analyses computed with bootstrap
CIs; second-environment subset either complete-and-archived or recorded as
not-attempted; any registered hypothesis now decidable is decided (support /
below-SESOI / falsified), no re-analysis.

## M6 — Analysis & writing

**Do:** full registered analysis; `DEVIATIONS.md` finalized; frontier figure
with CIs and stated δ margins; thesis/paper chapters; reproduction package
(archives, scripts, commit hashes, DOIs) released.

**Exit criteria:** every registered hypothesis has a stated outcome traceable
to archived runs (confirmatory family Holm-corrected; H4 reported
descriptively; H6 reported as exploratory or not-attempted); all
deviations logged; manuscript draft complete with the same claims discipline
as the earlier study (bounded scope, direction-over-magnitude transfer, no
universal-ranking claims).

## Schedule risks

- **M0 procurement** is the schedule's external dependency: M1b cannot start
  without the user's hardware decision (DATE-TBD-BY-USER), and the M5
  transfer arm dies quietly (reported as not-attempted) if the billing
  decision never lands. Both failure modes are pre-declared, so neither
  blocks a decidable hypothesis.
- **M1a in weeks 1–2** front-loads the top risk cheaply; its quantization
  report de-risks the M0 sizing before money is spent. **Named residual
  risk:** only N = 4 is validated live before procurement — the N = 6
  reachability feeding the purchase is analytical (fidelity-checked against
  the live N = 4 results per the M1a exit criterion, but still a model).
  M1b is the first live confirmation at the pinned N; the
  nearest-achievable-fraction fallback is the pre-committed mitigation if
  the model misled the purchase.
- **M1b solver gate** remains the critical path; the fallback keeps M2 on
  schedule at the cost of a weaker (observed-dose) design. The pre-declared
  de-scope order (second workload, then H6) plus the slack week before
  the freeze absorb an engine/cluster overrun.
- **Cluster capacity at r = 3** is budgeted with ≥30 % headroom at the
  pinned N (DESIGN §7.1); if readiness still fails, the decidable
  capacity-null rule (including the anti-affine distinct-nodes case)
  descopes rather than delays.
- **A/A failure at M2** is the one open-ended risk (halt on a second
  significant finding after a fix); one month of slack is deliberately left
  between M5 and M6 deliverables to absorb a single A/A repeat cycle.
