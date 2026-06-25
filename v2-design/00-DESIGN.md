# ChaosProbe v2 — Phase-0 design document

> **Status: PROPOSAL — the build has not started.** Nothing in this document is
> a finding. The pre-registration in
> [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) freezes only at the M2
> commit hash (see [`02-WORKPLAN.md`](02-WORKPLAN.md)); until that freeze,
> every design choice here is open to revision. All v1 evidence cited below is
> by hypothesis number (H1–H6) from
> [`chaosprobe/docs/explanation/hypotheses.md`](../chaosprobe/docs/explanation/hypotheses.md)
> and refers to archived, `doctor --strict`-clean runs only. **v1 numeric
> literals are quoted in exactly one place — the §10 mapping table — and cited
> by reference everywhere else** (here and in the companion documents), so a
> re-derived v1 number needs one edit, not five.

## 1. Thesis of the redesign

v1 was, in the end, an **observational case study**: eight named placement
strategies were applied as-is, and the study *observed* which measurement
layers moved under three fault classes. That design produced four defensible
results — the layered decoupling (mechanism moves, user layer does not;
H2/H3 under churn, H4 under load), the two-regime cross-node-fraction
separator (H5, replicated twice), the measured latency/availability
trade-off (H5 vs H6), and the aggregate-score critique (H1: the score cannot
rank placements; the ICC and its CI are in the §10 table) — but every one of
them is an observation about strategies that happened to differ in many
properties at once. The strategies are bundles: `colocate` differs from
`spread` in cross-node fraction, per-node concentration, node count used,
and scheduler interaction simultaneously, so no v1 result can attribute an
effect to a *single* placement property, and the dose between the two
regimes H5 identifies was never sampled.

**v2 is a designed interventional experiment.** Instead of eight bundles, v2
manipulates two pre-registered knobs — target cross-node fraction and
replication degree — and adds explicit mechanism interventions (NodeLocal
DNSCache, kube-proxy mode) whose predicted effects follow from v1's measured
mechanism. v1's results are recast as the **motivating pilot**: they tell us
which layers can move (mechanism: yes; availability under node faults: yes;
user layer: not reproducibly), which instrument cannot rank (the aggregate
score), and which mechanism component is placement-dependent (the UDP/DNS
conntrack pool, per the 2026-06-10 protocol probe; §10). v2 exists to convert
each of those observations into a tested causal or dose-response claim — or
to falsify it.

**Claim structure (binding across all three documents).** The confirmatory
family is the single primary test of each of **V2-H1, V2-H2, V2-H3, and
V2-H5**, Holm-corrected across the family. **V2-H4 is descriptive** (a
registered figure and reporting protocol, not a falsifiable hypothesis), and
**V2-H6** (the kube-proxy iptables arm) is an **exploratory secondary**,
labeled as such and outside the family. Details: pre-registration,
§"Confirmatory family and multiplicity".

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
E1 pilot (3 replicas × node-drain) was **deliberately skipped as structurally
null** (piloted, never run as a campaign; see
[`thesis/08-conclusion.md`](../thesis/08-conclusion.md)) precisely because
draining the target node would kill all 3 `productcatalogservice` replicas
under **every** strategy, making the experiment uninformative by
construction. v2's first engineering deliverable removes that cap.

### 2.2 Mechanisms

The v2 engine emits **replica-level `podAffinity`/`podAntiAffinity` and
`topologySpreadConstraints`** (with `nodeSelector` retained only as a
fallback for degenerate cases), so that (a) different replicas of one service
can be steered to different nodes, and (b) placement is expressed as
constraints the scheduler satisfies rather than hard pins — with the achieved
placement *verified* afterwards, never assumed.

### 2.3 Two knobs replace eight strategies

**Knob A — target cross-node fraction** `f ∈ {0, 0.25, 0.5, 0.75, 1.0}`.
v1's H5 showed the cross-node call fraction separates two regimes but never
sampled the interior — the lone intermediate point (`best-fit` in batch 1)
vanished in batch 2 and took the continuous correlation with it (§10). v2
samples the dose deliberately. A **solver** chooses placements hitting each
target given the service dependency graph:

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
- *Honest uncertainty.* Whether the solver can hit interior targets at the
  **pinned N = 6 workers** (§7) with ~11 services is unknown — this is the
  top risk (§9) and the M1b gate. Because the reachable-fraction set is a
  function of N, the gate **must** run at the pinned N (§9).

**Knob B — replication degree × packing mode.** `r ∈ {1, 3}` replicas per
service, crossed with a binary **replica-packing mode**: *packed* (all
replicas co-scheduled on one node via podAffinity — reproducing v1's
structural behaviour deliberately, as a control) vs *anti-affine* (replicas
on distinct nodes via `podAntiAffinity`/`topologySpreadConstraints`). This is
exactly the contrast the skipped E1 pilot could not realize, now realizable
by construction. r = 1 reproduces the v1 regime and anchors comparability.
**r = 2 is deliberately omitted**: no registered hypothesis, campaign, or
analysis samples it, so a middle level would inflate the cell count and the
M1b acceptance burden for zero analytic payload.

The eight v1 strategies are retired as experimental conditions. `baseline`
(no fault) survives as the A/A and calibration control; v1 strategy names may
be kept as presets that map onto (f, r, mode) coordinates for continuity
checks.

## 3. Interventional arms

v1's mechanism claims are *consistency* claims, not causal proof
(scope-of-claims: "mechanistic consistency … not a controlled causal
proof"). v2 adds two interventions that the v1 mechanism account makes
predictions about — so the account can fail.

**Arm 1 — NodeLocal DNSCache on/off.** The 2026-06-10 protocol probe (§10)
showed the clearly placement-dependent conntrack component is **UDP (DNS)**:
spread sustains several-fold more UDP entries than packed placements,
matching kube-proxy's deliberately UDP-only cleanup. NodeLocal DNSCache
moves pod DNS to a node-local cache, removing most cross-node UDP DNS flows
from conntrack. **V2-H2 is a two-part confirmatory conjunction** (see
pre-registration): **(a)** the placement-dependence replication —
between-placement, cache-off arms: spread's during-churn UDP drop exceeds
packed's, a paired *directional* comparison of absolute drops, for which
the packed arm IS the comparator (no ratio denominator is involved);
**(b)** the intervention — within-spread, paired: **with the cache ON,
spread's during-churn UDP-conntrack drop collapses by ≥50 %** against its
own cache-off drop (the within-spread *ratio* uses spread's own drop as
denominator precisely because the packed arm's pool is too small for a
ratio — but only for part (b)). Family input: max(p_a, p_b). If spread's
UDP drop persists largely unchanged with the cache on, the UDP/DNS account
of H2's placement dependence is wrong. This is the mechanism *proof* step
v1 explicitly deferred ("H2 flush apportionment", §8.2).

**Arm 2 — kube-proxy ipvs vs iptables (exploratory secondary, V2-H6).** v1
ran ipvs only and flagged mechanism behaviour as environment-contingent
(threats table: "depends on CNI, kube-proxy mode, kernel/conntrack
settings"). A reduced condition subset — **the f = 0 and f = 1 endpoints
only**, riding on the C1/C3 endpoint cells — is repeated under iptables mode
to test **mode-contingency**. This arm is registered as **V2-H6, an
exploratory secondary with a decidable direction-preservation criterion**
(sign test across ≥5 sessions; see pre-registration): does the
spread-vs-packed *direction* of the UDP-drop contrast survive a proxy-mode
change? No prediction is registered on magnitude, because the v1 evidence is
mode-specific by construction. V2-H6 sits **outside the confirmatory
family** and is **second in the pre-declared de-scope order** (after the
second workload) if M1 overruns.

## 4. Instrumentation v2

- **Protocol-labeled conntrack prober, first-class.** The ad-hoc v1 probe
  (per-node `conntrack -L` protocol counts, 5 s cadence) graduates into a
  built-in collector: per-node, per-protocol (TCP by state class, UDP, other),
  every 5 s, for every iteration, with sampling windows (pre/chaos/post
  boundaries) recorded in `summary.json`. This fixes the v1 probe's two
  defects: *i* = 1 (no replication) and window contamination (the Locust ramp
  inside the pre-window), which left the v1 flush medians (§10)
  unapportioned between kernel TCP teardown and UDP/DNS cleanup.
- **Load generator placement and windows, pre-registered.** Locust runs
  **host-side** (outside the cluster, as in v1). By construction it is
  therefore **excluded from the cross-node-fraction edge accounting and from
  the per-node conntrack aggregation** — it occupies no cluster node the
  solver controls and contributes no in-cluster pod. The window protocol is
  fixed: the pre-chaos baseline window **starts only after the load ramp
  completes plus a 60 s settle**. A pre-registered validity check —
  **per-f-level pre-window UDP-slope bands** derived from the M2 A/A block
  (redefined at the M2 freeze from the original "slope ≈ 0" absolute
  threshold, which the A/A block showed is unworkable: the pre-window pool
  carries placement-coupled transients — pre-registration §M2 freeze
  amendments, D3) — taints any iteration whose pre-window slope falls
  outside its level's band. This closes the exact contamination path the
  v1 probe documented.
- **EndpointSlice trough sampler retained** (15 s cadence) — the H6
  blast-radius instrument, unchanged; it is the availability-face DV.
- **Per-edge east-west latency retained** — the H4/H5 instrument
  (`routeViewAggregate` inter-service edges); it is the latency-face DV.
- All collectors report `metricAvailability` so "not collected" never reads
  as zero, and all runs remain `doctor --strict`-gated, as in v1.

## 5. Layered scorecard (replacing the aggregate score)

H1 established that the v1 aggregate score cannot rank placements (ICC and
MDE in §10 / hypotheses.md). v2 does not patch the score; it replaces it
with a **layered scorecard**: three per-layer sub-scores, each reported with
a CI and never summed into one number:

1. **Availability** — EndpointSlice trough depth/duration + user-route error
   rate during fault;
2. **Mechanism-reconvergence** — protocol-labeled conntrack disturbance +
   reconvergence time;
3. **User-tail** — dependent-route p95/p99 vs control-route, the H3
   confound-controlled contrast.

**Evaluation is falsifiable and guarded against circularity.** Because the
three sub-scores were chosen *because* v1 showed signal in those layers,
evaluating them on v1 data would be circular — that circularity is a named,
registered threat (pre-registration, V2-H5). The mitigations: (1) sub-score
definitions are **frozen at the M2 commit**, before any v2 reliability data
exists; (2) test-retest reliability is evaluated **exclusively on fresh v2
campaign sessions, never on the v1 sessions** that informed the design;
(3) the bar is double — each retained sub-score must both beat the v1
aggregate's ICC (ICC_old, §10) *and* reach an **absolute** ICC ≥ 0.5. If the
sub-scores fail either bar, the scorecard fails its own test and is reported
as failing. The v1 score is still computed per v2 session, for exactly this
comparison.

## 6. Headline objective: the placement Pareto frontier

v1 ended with a two-point trade-off: the same co-location that wins the
latency face (H5) loses the availability face (H6) (§10). v2's headline
deliverable is that trade-off as a **measured Pareto frontier**: for each
designed placement (f, r, mode), plot east-west tail (latency face) against
blast radius/recovery (availability face), with bootstrap CIs, and identify
the non-dominated set. The substantive question replication degree adds:
**where does r buy back availability** — does an anti-affine r = 3 placement
at low f recover the availability that f = 0/r = 1 sacrifices, yielding
frontier points v1's design could not produce?

**V2-H4 is registered as descriptive, not confirmatory.** Under noisy,
overlapping bootstrap-CI regions, "the frontier contains ≥2 non-dominated
placements" is nearly self-confirming — the noisier the data, the more
non-dominated points appear. The frontier is therefore a **registered figure
with a reporting protocol**: dominance is declared only with margins
δ_latency / δ_blast tied to the A/A noise band (finalized at the M2 freeze:
δ_latency = 4.4 ms, δ_blast = 1.0 pod trough depth + 0.302 user-route error
rate, floored at the A/A 95 % noise band — pre-registration §M2 freeze
amendments, V2-H4), and the
non-dominated set is reported under those margins (pre-registration, V2-H4).
A single placement dominating all others by ≥ δ on both faces would be
reported prominently as the headline result, not suppressed.

## 7. Environments and workloads

- **Primary cluster — pinned: N = 6 worker nodes × 8 GiB (≥4 vCPU each)**,
  flat-virtualization or bare-metal — larger than v1's 4 workers, both to
  give the solver headroom for interior f targets and to weaken the
  small-cluster external-validity threat (threats table row 2). **Fallback:
  8 workers × 4 GiB** — explicitly noted as a *different design point*: the
  solver's reachable-fraction set is a function of N, so adopting the
  fallback **requires the M1b solver gate to re-run at N = 8** before
  anything downstream proceeds. Hardware existence/procurement is the
  user-owned M0 gate ([`02-WORKPLAN.md`](02-WORKPLAN.md)).
- **Replication arm:** a **second, deliberately different environment** —
  different CNI and/or a managed Kubernetes service — running a reduced
  condition subset, to test whether the dose-response *direction* (not
  magnitudes) transfers. v1 explicitly identified an independent-
  infrastructure replication as "the most valuable follow-up this study
  could receive" (§7.2). This arm is **pre-declared droppable** (condition
  and reporting in the pre-registration's stopping rules): the V2-H4 and
  V2-H5 analyses depend only on primary-environment data and complete
  regardless.
- **Workloads:** Online Boutique (11 services; continuity with v1 — all v1
  graph metrics carry over) **plus DeathStarBench `hotelReservation`** (~15
  services — the **lightest** DSB application and the only realistic
  candidate at this cluster scale; different topology and RPC stack), so no
  claim rests on a single dependency-graph shape. **DSB `socialNetwork`
  (~27 services) is explicitly excluded as infeasible on this hardware.**
  hotelReservation's exact service/edge counts are measured at deploy (M2
  prep window) and feed its fraction quantum and capacity check, which are
  M2 exit criteria; if it does not fit the capacity budget below, the
  second-workload claim is dropped per the pre-declared de-scope order —
  first to go, before the iptables arm.

### 7.1 Capacity budget (pinned N = 6 × 8 GiB)

The arithmetic the v1 cluster never had. All request figures marked (m) are
**to-be-measured-exactly placeholders**; the method is fixed now: sum the
deployed pods' requests from
`kubectl get pods -n <ns> -o json` (per-container
`resources.requests` summed per resource), recorded in the M1b/M2 gate
artifacts.

- **Online Boutique at r = 1:** 11 services ≈ 11 app pods; sum of requests
  ≈ **~1.6 GiB memory / ~1.7 vCPU** (m).
- **At r = 3:** ≈ **33 app pods**, ≈ ~4.8 GiB / ~5.1 vCPU (m, ×3).
- **Infrastructure overhead:** Prometheus, LitmusChaos, the in-cluster
  registry, metrics-server, and the new conntrack prober DaemonSet (1 pod ×
  6 nodes) ≈ ~2–3 GiB / ~2–3 vCPU (m). The host-side load generator adds
  nothing in-cluster (§4).
- **Total at the heaviest cell (Online Boutique, r = 3):** ≈ ~8 GiB /
  ~8 vCPU of requests. Both resources are netted symmetrically: memory
  **48 GiB total → ~42 GiB allocatable** after system/kubelet reservations;
  vCPU **24 total (6 × 4) → ~21 vCPU allocatable** after the same
  reservations (~0.5 vCPU/node for system daemons + kubelet). Headroom at
  the heaviest cell: memory ~81 % (8/42), vCPU ~62 % (8/21); at **2× the
  placeholder requests** (16 GiB / 16 vCPU): memory ~62 %, vCPU ~24 % —
  so at 2×, **vCPU is the binding resource and the >30 % headroom claim
  holds only at measured-≈-placeholder levels**. The *measured* request
  sums are therefore collected in **M1a from the live v1 cluster**
  (`kubectl` sum of `resources.requests`, ×3 for the r = 3 projection) —
  data that exists **before the M0 purchase** — and the escalation decision
  is taken **at M0**: if measured vCPU requests exceed ~1.3× these
  placeholders, the spec to procure escalates to **6 × 6 vCPU** (the
  contingency is listed in the WORKPLAN M0 gate), keeping ≥30 % vCPU
  headroom at the heaviest cell. The M1b capacity check then re-verifies
  the same sums against the *procured* hardware's allocatable on both
  resources.
- **Anti-affinity feasibility:** at r = 3 anti-affine, each service's 3
  replicas need **3 schedulable, distinct nodes**; N = 6 satisfies this with
  slack. "r = 3 anti-affine schedulable at the pinned N" is an **explicit
  M1b exit criterion** (it can only be evaluated on the pinned-N cluster,
  not in the M1a spike), and the capacity-null stopping rule covers
  anti-affine scheduling failure, not only the f = 0/f = 1 extremes
  (pre-registration, stopping rules).
- hotelReservation's analogous budget (≈ 15 × r app pods plus its
  datastores) is computed the same way at deploy and gated at M2. Its
  r = 3 anti-affine distinct-node requirement is the same 3-of-6-nodes
  predicate as Online Boutique's and is therefore equally satisfiable at
  the pinned N = 6 — asserted here, verified at its M2 gate.

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
> quantized far from the targets. Therefore the solver is built and
> validated **first**, in two stages ([`02-WORKPLAN.md`](02-WORKPLAN.md)):
> **M1a**, a cheap solver-feasibility spike (algorithm + quantization study,
> runnable on the existing v1 4-worker cluster, no new hardware) whose
> output informs the M0 hardware decision; then **M1b**, the full go/no-go
> gate **at the pinned N = 6**. The gate MUST run at the pinned N because
> the reachable-fraction set is N-dependent, and it re-runs if the
> 8 × 4 GiB fallback cluster is adopted. Gate terms ("attempt",
> "consecutive", the recorded artifact) are defined decidably in the
> pre-registration's stopping rules. Both the solver and the engine carry
> CI-gated unit/property tests — including validation of the
> achieved-fraction computation against an independent second
> implementation — **before** any live smoke gate (workplan M1a/M1b exit
> criteria).
>
> **Fallback (pre-committed):** if interior targets are unreachable at ±0.05,
> v2 switches to a **nearest-achievable-fraction design** — the solver emits
> its closest achievable placements, the *achieved* fraction (measured
> post-schedule from `podPlacements`) becomes the regressor, and V2-H1's
> analysis switches to the **pre-registered fallback tests** (linear
> mixed-effects model on achieved-f as the primary, Jonckheere–Terpstra as
> the nonparametric secondary — see V2-H1). This weakens the design from
> designed-dose to observed-dose but preserves falsifiability; the switch,
> if taken, is recorded in the pre-registration before freezing.

## 10. Relation of v1 evidence to v2 hypotheses

**This table is the single canonical home of v1 numeric literals in the v2
plan.** §1–§6 above, the pre-registration's per-hypothesis pilot cites, and
the workplan reference rows here (or `hypotheses.md` directly) instead of
restating numbers.

| v1 result (archived) | v1 literals (canonical) | v2 successor | What changes |
|---|---|---|---|
| H5 two-regime separator (2 batches) | ~1.25× ≈ 25 % east-west tail separation, f ≈ 0 vs ≈ 0.70–0.82; lone interior point 0.13 vanished in batch 2; ρ 0.79 → 0.25 | V2-H1 dose-response (**confirmatory**) | Interior of the dose sampled by design |
| H2 flush (7/7 sessions, sign test p = 0.0156) + protocol probe (UDP placement-dependence) | flush medians 38.5 % vs 2.7 %; UDP entries spread 910 vs colocate 224 (~4×); packed/colocate UDP pool ~72–224 entries across the probe window | V2-H2 placement-dependence + DNS-cache intervention (**confirmatory**, two-part conjunction) | Consistency → replication (between-placement, directional) plus controlled intervention (within-spread ratio, whose denominator is spread's own drop — avoiding the near-zero packed pool for the ratio while keeping packed as the part-(a) comparator) |
| H6 blast = predicted; E1 deliberately skipped (structurally null) | ρ = 1.0, n = 6; colocate 11/11 services down vs spread 2/11 | V2-H3 replication rescue (**confirmatory**) | Anti-affinity becomes expressible; E1 finally runnable |
| H5 × H6 trade-off (two-point) | (see rows above) | V2-H4 Pareto frontier (**descriptive**) | Two points → frontier with CIs and δ dominance margins |
| H1 score reliability | ICC = 0.033, CI [0.014, 0.178], 7 sessions / 147 iterations; MDE ≈ 51 points at n = 3; between-session variance 37.6 % of score variance | V2-H5 scorecard reliability (**confirmatory**) | Critique → constructive replacement; absolute bar + fresh-data evaluation |
| Mode-contingency caveat (ipvs-only; threats table) | — (no numeric claim) | V2-H6 iptables direction transfer (**exploratory secondary**) | Untested caveat → decidable direction-preservation check |

The falsifiable statements, SESOIs, tests, multiplicity policy, and stopping
rules for V2-H1–H6 live in [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md);
the schedule and gates in [`02-WORKPLAN.md`](02-WORKPLAN.md). Family
membership — **V2-H1/H2/H3/H5 confirmatory (Holm-corrected), V2-H4
descriptive, V2-H6 exploratory** — is identical in all three documents.
