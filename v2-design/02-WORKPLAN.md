# ChaosProbe v2 — six-month workplan

> **Status: PROPOSAL — the build has not started.** Companion to
> [`00-DESIGN.md`](00-DESIGN.md) and [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md).
> The pre-registration freezes at the end of M2; everything before that
> freeze is preparatory and may be revised.

## M1 — Placement engine v2, prober, cluster (GO/NO-GO gate)

**Build:** the affinity/topology-spread placement engine (replica-level
podAffinity/antiAffinity + topologySpreadConstraints, replacing the v1
nodeSelector mutator); the greedy edge-cut **fraction-targeting solver** with
post-schedule achieved-fraction verification; the protocol-labeled conntrack
prober as a first-class collector (per-node, per-protocol, 5 s, windows in
`summary.json`); provision the primary 6–8-worker cluster; deploy Online
Boutique and the chosen DeathStarBench app.

**Exit criteria:**
- Engine expresses packed and anti-affine r ∈ {1,2,3} (the contrast the
  cancelled v1 E1 pilot could not realize) — verified from live
  `podPlacements`.
- **Solver gate (go/no-go for the dose-response design):** solver hits every
  target f ∈ {0, 0.25, 0.5, 0.75, 1.0} within ±0.05 on the live cluster for
  both workloads, ≥3 consecutive attempts per level. **Go** → designed-dose
  V2-H1. **No-go** → invoke the pre-committed nearest-achievable-fraction
  fallback (achieved-f as regressor) and record the switch in the
  pre-registration before freeze. This gate is the project's top risk and is
  cleared before any other milestone proceeds.
- Prober output round-trips into `summary.json`; `doctor --strict` passes on
  a smoke session.

## M2 — A/A calibration → power analysis → pre-registration FREEZE

**Run:** ≥3 identical-placement A/A session pairs through the full pipeline;
measure the empirical false-positive rate; tighten gates/taints if > α and
repeat. Estimate per-cell variance; run power analyses against each SESOI;
fill every **TBD** in the pre-registration (per-cell n, V2-H3 margin).

**Exit criteria:** A/A false-positive rate ≤ α (abandon rule: halt and
redesign instrumentation if > 2α after tightening); all TBDs resolved;
**pre-registration frozen by commit hash and DOI-deposited.** No comparative
campaign data exists before this point.

## M3–M4 — Campaigns

- **C1 (dose-response, V2-H1):** f-levels × r = 1 × churn + load, n per cell
  from M2.
- **C2 (replication × drain, V2-H3):** r {1,3} × mode {packed, anti-affine}
  × node-drain.
- **C3 (DNS intervention, V2-H2):** cache on/off × f {0,1} × churn, paired
  sessions, randomized cache order. The kube-proxy iptables subset arm rides
  along on C1/C3 endpoint cells.

**Exit criteria (per campaign):** all cells at registered n with
`doctor --strict`-clean, fraction-verified sessions; campaign archived +
DOI before analysis for writing; interim analysis limited to data-quality
checks (no peeking at hypothesis outcomes between campaigns).

## M5 — Second environment + scorecard evaluation

**Run:** the reduced replication subset (C1 endpoints + C2 interaction cells)
on the second environment (different CNI or managed Kubernetes); direction-
only transfer test. Compute the layered scorecard and the v1 aggregate score
on **all** campaign sessions; run the V2-H5 reliability head-to-head
(ICC_new vs ICC_old = 0.033).

**Exit criteria:** second-environment subset complete and archived; V2-H4
frontier and V2-H5 comparison computed with bootstrap CIs; any registered
hypothesis now decidable is decided (support / below-SESOI / falsified), no
re-analysis.

## M6 — Analysis & writing

**Do:** full registered analysis; `DEVIATIONS.md` finalized; frontier figure
with CIs; thesis/paper chapters; reproduction package (archives, scripts,
commit hashes, DOIs) released.

**Exit criteria:** every registered hypothesis has a stated outcome traceable
to archived runs; all deviations logged; manuscript draft complete with the
same claims discipline as v1 (bounded scope, direction-over-magnitude
transfer, no universal-ranking claims).

## Schedule risks

- **M1 solver gate** is the critical path; the fallback keeps M2 on schedule
  at the cost of a weaker (observed-dose) design.
- **Cluster capacity** at r = 3 × 6–8 workers: if extremes fail readiness,
  the capacity-null stopping rule descopes rather than delays.
- **A/A failure at M2** is the one open-ended risk (halt-and-redesign);
  one month of slack is deliberately left between M5 and M6 deliverables to
  absorb a single A/A repeat cycle.
