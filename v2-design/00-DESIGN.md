# ChaosProbe v2 — Phase-0 design document

> **Status: PROPOSAL — the build has not started.** Nothing in this document is
> a finding. The pre-registration in
> [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) freezes only at the M2
> commit hash (see [`02-WORKPLAN.md`](02-WORKPLAN.md)); until that freeze,
> every design choice here is open to revision. All v1 evidence cited below is
> by hypothesis number (H1–H6) from
> [`chaosprobe/docs/explanation/hypotheses.md`](../chaosprobe/docs/explanation/hypotheses.md)
> and refers to archived, `doctor --strict`-clean runs only.

## 1. Thesis of the redesign

v1 was, in the end, an **observational case study**: eight named placement
strategies were applied as-is, and the study *observed* which measurement
layers moved under three fault classes. That design produced four defensible
results — the layered decoupling (mechanism moves, user layer does not; H2/H3
under churn, H4 under load), the two-regime cross-node-fraction separator
(H5, replicated twice), the measured latency/availability trade-off (H5 vs
H6), and the aggregate-score critique (H1: `ICC_strategy = 0.033`, CI
[0.014, 0.178], on 7 sessions / 147 iterations) — but every one of them is an
observation about strategies that happened to differ in many properties at
once. The strategies are bundles: `colocate` differs from `spread` in
cross-node fraction, per-node concentration, node count used, and scheduler
interaction simultaneously, so no v1 result can attribute an effect to a
*single* placement property, and the dose between the two regimes H5
identifies was never sampled.

**v2 is a designed interventional experiment.** Instead of eight bundles, v2
manipulates two continuous, pre-registered knobs — target cross-node fraction
and replication degree — and adds explicit mechanism interventions (NodeLocal
DNSCache, kube-proxy mode) whose predicted effects follow from v1's measured
mechanism. v1's results are recast as the **motivating pilot**: they tell us
which layers can move (mechanism: yes; availability under node faults: yes;
user layer: not reproducibly), which instrument cannot rank (the aggregate
score), and which mechanism component is placement-dependent (the UDP/DNS
conntrack pool, per the 2026-06-10 protocol probe). v2 exists to convert each
of those observations into a tested causal or dose-response claim — or to
falsify it.

The limitations chapter of v1 ([`thesis/07-threats.md`](../thesis/07-threats.md),
[`thesis/08-conclusion.md`](../thesis/08-conclusion.md) §8.2) is treated here
as the requirements list: single-replica design → replication-degree knob;
strategy bundles → continuous fraction knob; mechanism consistency without
causal proof → interventional arms; one environment → second-environment
replication arm; unstable aggregate score → layered scorecard with a
head-to-head reliability evaluation.

## 2. Placement engine v2

### 2.1 What v1's engine could not do

v1's mutator implements **per-service deterministic `nodeSelector` pinning**:
each service's pods are pinned to exactly one node. This has a structural
consequence documented in §8.2 of the conclusion: *all replicas of a service
land on the same node*, so multi-replica anti-affinity is impossible — the
E1 pilot (3 replicas × node-drain) was cancelled precisely because draining
the target node killed all 3 `productcatalogservice` replicas under **every**
strategy, making the experiment structurally null. v2's first engineering
deliverable removes that cap.

### 2.2 Mechanisms

The v2 engine emits **replica-level `podAffinity`/`podAntiAffinity` and
`topologySpreadConstraints`** (with `nodeSelector` retained only as a
fallback for degenerate cases), so that (a) different replicas of one service
can be steered to different nodes, and (b) placement is expressed as
constraints the scheduler satisfies rather than hard pins — with the achieved
placement *verified* afterwards, never assumed.

### 2.3 Two knobs replace eight strategies

**Knob A — target cross-node fraction** `f ∈ {0, 0.25, 0.5, 0.75, 1.0}`.
v1's H5 showed the cross-node call fraction separates two regimes (≈0 vs
0.70–0.82) but never sampled the interior — the lone intermediate point
(`best-fit` at 0.13 in batch 1) vanished in batch 2 and took the continuous
correlation with it (ρ 0.79 → 0.25). v2 samples the dose deliberately. A
**solver** chooses placements hitting each target given the service
dependency graph:

- *Algorithm sketch (greedy edge-cut assignment).* Take the inter-service
  edge set E from the dependency graph (the same `routeViewAggregate`-derived
  edges `scripts/cross_node_fraction.py` uses), with edge weights = observed
  call volume. Assign services to N nodes greedily: maintain per-node
  capacity (requests-based); at each step place the service whose assignment
  most reduces the gap between the current cut fraction (weight of
  cross-node edges / total weight) and the target f, subject to capacity;
  finish with a local-search pass (single-service moves and pairwise swaps)
  to close the residual gap. For f = 0 this degenerates to packing all
  communicating services together (capacity permitting); for f = 1 to
  separating every adjacent pair.
- *Verification.* After scheduling, recompute the **achieved** fraction from
  the recorded `podPlacements` (post-schedule, per iteration). A session is
  **rejected** if any iteration's achieved fraction misses its target by
  more than **0.05**; rejected sessions are logged, not silently dropped.
- *Honest uncertainty.* Whether the solver can hit interior targets on a
  6–8-node cluster with ~11 services is unknown — this is the top risk
  (§9) and the M1 gate.

**Knob B — replication degree × packing mode.** `r ∈ {1, 2, 3}` replicas per
service, crossed with a binary **replica-packing mode**: *packed* (all
replicas co-scheduled on one node via podAffinity — reproducing v1's
structural behaviour deliberately, as a control) vs *anti-affine* (replicas
on distinct nodes via `podAntiAffinity`/`topologySpreadConstraints`). This is
exactly the contrast the cancelled E1 pilot could not realize, now realizable
by construction. r = 1 reproduces the v1 regime and anchors comparability.

The eight v1 strategies are retired as experimental conditions. `baseline`
(no fault) survives as the A/A and calibration control; v1 strategy names may
be kept as presets that map onto (f, r, mode) coordinates for continuity
checks.

## 3. Interventional arms

v1's mechanism claims are *consistency* claims, not causal proof
(scope-of-claims: "mechanistic consistency … not a controlled causal
proof"). v2 adds two interventions that the v1 mechanism account makes
predictions about — so the account can fail.

**Arm 1 — NodeLocal DNSCache on/off.** The 2026-06-10 protocol probe (runs
`20260610-195929`/`20260610-201052`, archived) showed the clearly
placement-dependent conntrack component is **UDP (DNS)**: spread sustains ~4×
more UDP entries than colocate, matching kube-proxy's deliberately UDP-only
cleanup. NodeLocal DNSCache moves pod DNS to a node-local cache, removing
most cross-node UDP DNS flows from conntrack. **Prediction (falsifiable): with
the cache ON, the placement-dependent (spread-vs-packed) component of the
during-churn UDP-conntrack drop collapses** (quantified bar in V2-H2). If the
spread-vs-packed flush difference persists unchanged with the cache on, the
UDP/DNS account of H2's placement dependence is wrong. This is the mechanism
*proof* step v1 explicitly deferred ("H2 flush apportionment", §8.2).

**Arm 2 — kube-proxy ipvs vs iptables (subset arm).** v1 ran ipvs only and
flagged mechanism behaviour as environment-contingent (threats table:
"depends on CNI, kube-proxy mode, kernel/conntrack settings"). A reduced
condition subset (the f = 0 and f = 1 endpoints × churn, plus the V2-H2 cells)
is repeated under iptables mode to test **mode-contingency**: does the H2
signature's direction survive a proxy-mode change? No prediction is
registered on magnitude — only direction — because the v1 evidence is
mode-specific by construction.

## 4. Instrumentation v2

- **Protocol-labeled conntrack prober, first-class.** The ad-hoc v1 probe
  (per-node `conntrack -L` protocol counts, 5 s cadence) graduates into a
  built-in collector: per-node, per-protocol (TCP by state class, UDP, other),
  every 5 s, for every iteration, with sampling windows (pre/chaos/post
  boundaries) recorded in `summary.json`. This fixes the v1 probe's two
  defects: *i* = 1 (no replication) and window contamination (the Locust ramp
  inside the pre-window), which left the 38.5 % vs 2.7 % flush medians
  unapportioned between kernel TCP teardown and UDP/DNS cleanup.
- **EndpointSlice trough sampler retained** (15 s cadence) — the H6
  blast-radius instrument, unchanged; it is the availability-face DV.
- **Per-edge east-west latency retained** — the H4/H5 instrument
  (`routeViewAggregate` inter-service edges); it is the latency-face DV.
- All collectors report `metricAvailability` so "not collected" never reads
  as zero, and all runs remain `doctor --strict`-gated, as in v1.

## 5. Layered scorecard (replacing the aggregate score)

H1 established that the v1 aggregate score cannot rank placements
(`ICC_strategy = 0.033`; MDE ≈ 51 points at n = 3). v2 does not patch the
score; it replaces it with a **layered scorecard**: three per-layer
sub-scores, each reported with a CI and never summed into one number:

1. **Availability** — EndpointSlice trough depth/duration + user-route error
   rate during fault;
2. **Mechanism-reconvergence** — protocol-labeled conntrack disturbance +
   reconvergence time;
3. **User-tail** — dependent-route p95/p99 vs control-route, the H3
   confound-controlled contrast.

**Evaluation is head-to-head and falsifiable:** test-retest reliability of
the layered sub-scores vs the v1 aggregate score computed **on identical
session data** — ICC_new vs ICC_old = 0.033. If the sub-scores are not more
reliable than the aggregate they replace (V2-H5), the scorecard fails its own
test and is reported as failing. The v1 score is still computed per run, for
exactly this comparison.

## 6. Headline objective: the placement Pareto frontier

v1 ended with a two-point trade-off: the same co-location that wins the
latency face (H5: node-local placements hold the two lowest east-west tails
of eight, twice) loses the availability face (H6: 11/11 services down under
drain vs 2/11, observed blast = predicted blast at ρ = 1.0, n = 6). v2's
headline deliverable is that trade-off as a **measured Pareto frontier**:
for each designed placement (f, r, mode), plot east-west tail (latency face)
against blast radius/recovery (availability face), with bootstrap CIs, and
identify the non-dominated set. The substantive question replication degree
adds: **where does r buy back availability** — does an anti-affine r = 3
placement at low f recover the availability that f = 0/r = 1 sacrifices,
yielding frontier points v1's design could not produce? V2-H3 and V2-H4 in
the pre-registration state the falsifiable forms. We expect a non-degenerate
frontier; a single dominating placement would itself be a publishable
surprise and would be reported as such, not suppressed.

## 7. Environments and workloads

- **Primary:** flat-virtualization or bare-metal cluster, **6–8 worker
  nodes** — larger than v1's 4 workers, both to give the solver headroom for
  interior f targets and to weaken the small-cluster external-validity threat
  (threats table row 2).
- **Replication arm:** a **second, deliberately different environment** —
  different CNI and/or a managed Kubernetes service — running a reduced
  condition subset, to test whether the dose-response *direction* (not
  magnitudes) transfers. v1 explicitly identified an independent-infrastructure
  replication as "the most valuable follow-up this study could receive"
  (§7.2).
- **Workloads:** Online Boutique (continuity with v1; all v1 graph metrics
  carry over) **plus one DeathStarBench application** (different topology and
  RPC stack), so no claim rests on a single dependency graph shape.

## 8. Explicit non-goals

- **No new fault types.** v2 uses pod-delete (churn), node-drain
  (availability), and load (contention) only — the three classes v1
  characterized. v1's verified negative results stand: no memory-hog or
  CPU-hog arms (`node-memory-hog` self-evicts; `pod-cpu-hog` is CFS-capped —
  see proposed-experiments §"What we deliberately do not propose").
- **No LLM components.** No learned placement policies, no LLM-driven
  analysis in the measurement path.
- **No universal-ranking claims.** v2 produces dose-response curves, a
  frontier, and mechanism tests for two workloads on two environments — not a
  "best placement" verdict. The v1 scope discipline (claims bounded to
  environment, direction-over-magnitude for transfer) carries over unchanged.

## 9. TOP RISK — the fraction-targeting solver, gated first

> **The entire dose-response design (V2-H1, and the frontier's interior
> points) depends on the solver actually hitting interior targets
> f ∈ {0.25, 0.5, 0.75} within ±0.05 on a real cluster.** This is unproven
> and is the single point of failure: with ~11 services, integer node
> assignments, and capacity constraints, achievable cut fractions may be
> quantized far from the targets. Therefore the solver is built and validated
> **first** — Month 1, against the live scheduler, before any other v2 work
> is committed (the M1 go/no-go gate in [`02-WORKPLAN.md`](02-WORKPLAN.md)).
>
> **Fallback (pre-committed):** if interior targets are unreachable at ±0.05,
> v2 switches to a **nearest-achievable-fraction design** — the solver emits
> its closest achievable placements, the *achieved* fraction (measured
> post-schedule from `podPlacements`) becomes the regressor, and V2-H1's
> trend test is run over achieved-f values instead of designed levels. This
> weakens the design from designed-dose to observed-dose but preserves
> falsifiability; the switch, if taken, is recorded in the pre-registration
> before freezing.

## 10. Relation of v1 evidence to v2 hypotheses

| v1 result (archived) | v2 successor | What changes |
|---|---|---|
| H5 two-regime separator (2 batches; ρ 0.79 → 0.25) | V2-H1 dose-response | Interior of the dose sampled by design |
| H2 flush 38.5 % vs 2.7 % (7/7 sessions) + protocol probe (UDP placement-dependence) | V2-H2 DNS-cache intervention | Consistency → controlled intervention |
| H6 blast = predicted (ρ = 1.0, n = 6); E1 cancelled (structural) | V2-H3 replication rescue | Anti-affinity becomes expressible; E1 finally runnable |
| H5 × H6 trade-off (two-point) | V2-H4 Pareto frontier | Two points → frontier with CIs |
| H1 score ICC = 0.033 [0.014, 0.178] | V2-H5 scorecard reliability | Critique → constructive replacement, tested head-to-head |

The falsifiable statements, SESOIs, tests, and stopping rules for V2-H1–H5
live in [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md); the schedule and
gates in [`02-WORKPLAN.md`](02-WORKPLAN.md).
