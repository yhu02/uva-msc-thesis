# 5. Results

<!-- Numbers in this chapter are FINAL (post-#245): the 7-session campaign
(s01–s07) is the primary H1–H3 evidence; the pooled pre-campaign run-set is
pilot-only. Every number traces to an archived run via Appendix A
(09-appendix-provenance.md). Wording per scope-of-claims.md. -->

**Figure 5.1 (stub): the fault-class × measurement-layer core matrix** —
which layer moves under churn / load / node failure, and whether it reaches
the user. `TODO(author): 3×3 matrix figure; this is the thesis-in-one-picture.`

## 5.1 H1 — The aggregate score cannot rank placement strategies

**Statement.** The probe-based aggregate resilience score does not
reproducibly discriminate placement strategies; between-strategy differences
are a small fraction of total score variance and undetectable at feasible
iteration counts.

**Result — supported (7-session campaign, 147 churn iterations).**

| Quantity | Value |
|---|---|
| `ICC_strategy` | **0.033** (cluster-bootstrap 95% CI **[0.014, 0.178]**) |
| Variance partition | between-strategy **3.3%** / run-to-run **37.6%** / iteration **59.1%** |
| Focal contrast (colocate 64.0 vs spread 74.3) | *d* = 0.46 → **73 iterations/strategy** for 80% power |
| MDE at the *n* = 3 actually run per session | 2.29 sd ≈ **51 score points** — larger than any gap that exists |

The earlier pooled run-set (16 mixed-version runs: ICC 0.046, focal *d* = 0.06
needing ≈3,982/strategy) read the same way and is retained as the pilot only.

Scope (per §2.1): this is "cannot **rank placement strategies under session
variance**" — MicroRes's 0.86–0.90 *binary* classification accuracy is a
different task and is not contradicted.

**Figure 5.2 (stub): per-strategy score distributions across sessions**
(overlapping spreads, means clustered 64–74). **Figure 5.3 (stub): ICC
trajectory across sessions 1→7** (0.222 → 0.033 as run-to-run variance
accrues). `TODO(author): generate from scripts/score_variance.py output.`

## 5.2 H2 — Placement reproducibly moves a kernel/network reconvergence signature

**Statement.** Under churn, spreading the target's dependents across nodes
flushes a large fraction of per-node conntrack state during the kill cycle;
co-location does not.

**Result — supported (7/7 sessions).** Median conntrack flush: `spread`
**38.5%** vs `colocate` **2.7%**; `spread > colocate` in **7/7** independent
sessions — **sign test *p* = 0.0156, paired Wilcoxon W = 0, *p* = 0.0225**.
The pooled pilot agreed (36.6% vs 1.9%, 16/16 runs). This is the most
reproducible signal in the study.

Secondary, corroborating only: `colocate` throttles CPU lowest in 6/7
sessions (the pooled pilot had `best-fit` lower still) — lead with conntrack.

Mechanism attribution carries a **protocol-scoping caveat** (§6.3):
kube-proxy's *active* conntrack flush on endpoint churn is **UDP-only**
upstream; Online Boutique's east-west traffic is gRPC/TCP, so the measured
flush must be attributed to kernel-side TCP teardown on pod-IP removal plus
the UDP/DNS flush path — not to kube-proxy alone.

**Figure 5.4 (stub): per-session spread-vs-colocate conntrack flush %**
(paired lines, 7 sessions). `TODO(author): from scripts/mechanism_metrics.py.`

## 5.3 H3 — The mechanism is decoupled from the user-visible outcome

**Statement.** The reproducible mechanism (H2) does not translate into a
reproducible user-visible outcome on the fault-dependent routes beyond a
run-level confound.

**Result — decoupling supported (campaign + three pilot tests).** Across the
7 sessions (49 strategy-cells): conntrack flush → **dependent**-route p95 is
**ρ = 0.07 (*p* = 0.65)** and **decoupled by TOST**, while the **control**
route shows ρ = 0.29 (*p* = 0.043) — the mechanism correlates with the route
that does *not* depend on the killed service: the signature of a run-level
confound, not propagation. The only dependent-significant association
(TCP-retransmit delta, ρ = −0.32) is *negative* — opposite a propagation
story. Pilot agreement: pooled ρ = 0.15 n.s.; the one significant mechanism
(CoreDNS p99) stronger on control (0.54) than dependent (0.31); within-run
mean ρ ≈ +0.10; robust to route reclassification.

No statistics needed for the headline table: `dependency-aware` has the
*worst* mechanism (conntrack +20%) and the *best* dependent-route error rate
(1.4%); `spread` flushes 9× more than `colocate` yet they tie on user-visible
error (8.0% vs 8.9%).

**Figure 5.5 (stub): mechanism-vs-outcome scatter** (dependent vs control
routes, per strategy-cell). `TODO(author): from scripts/h3_mechanism_outcome.py.`

## 5.4 H4 — Under load contention, placement moves the mechanism, not (reproducibly) the user

**Result — mechanism replicates; user layer does not.** Two *i* = 4 batches
(the second with fully clean, doctor-gated provenance):

- *Replicated*: east-west inter-service p95, median spread/colocate ratio
  **1.39× (batch A)** and **1.36× (batch B)** — co-location keeps calls
  node-local.
- *Not replicated*: user-facing during-load ratios collapsed from ~2.1–2.4×
  (batch A, dependent > control) to ~1.05–1.40× (batch B, dependent ≈
  control — no dependency specificity). The dirty pilot's "co-location ~3×
  better at the user layer" reading did not survive replication.

**No user-visible placement effect is claimed under load.** The finding
matches the churn result: layered decoupling holds across both fault classes.

## 5.5 H5 — A graph-derived metric predicts the east-west placement penalty

**Result — supported, coarsely.** Across all 8 strategies under a 200-user
spike (*i* = 4), the **cross-node call fraction** (computed pre-chaos from
the dependency graph + actual `podPlacements`) rank-correlates with the
during-load median east-west p95: **Spearman ρ = 0.79 (n = 8, *p* < 0.05;
critical ρ ≈ 0.74)**.

| strategy | cross-node frac | east-west p95 (ms) |
|---|---|---|
| **colocate** | 0.00 | **33.9** |
| **best-fit** | 0.13 | **35.3** |
| dependency-aware | 0.73 | 42.6 |
| spread | 0.73 | 43.5 |
| baseline | 0.70 | 43.5 |
| adversarial | 0.80 | 43.5 |
| default | 0.78 | 45.5 |
| random | 0.80 | 43.9 |

Secondary findings: locality is **not unique to `colocate`** (`best-fit`'s
bin-packing also lands a low fraction and the second-lowest tail — any
node-packing placement gets the benefit); **`dependency-aware` did not
deliver** (fraction 0.73 is spread-like — the BFS partition did not co-locate
communicating services as intended; its tail ≈ spread's).

Caveats: the correlation is carried by the two **node-local** placements
sitting below the six **spreading** ones (clustered 0.70–0.80 / 42–46 ms with
no clean within-cluster trend) — a coarse separator, not a smooth law; the
user layer stays weak (~1.3×, not dependency-specific); single batch; this is
**validation of a static predictor**, not of the locality concept (§2.1).

**Figure 5.6 (stub): cross-node fraction vs east-west p95 scatter** (8
strategies, the two node-local points below the spreading cluster).
`TODO(author): from scripts/cross_node_fraction.py.`

## 5.6 H6 — Co-location is a latency/availability trade-off

**Result — supported and reproduced (two doctor-clean node-drain batches:
*i* = 1 and *i* = 3).** Draining the node hosting `productcatalogservice`:

| placement | services on drained node | blast radius (observed) | target recovery (mean) |
|---|---|---|---|
| **colocate** | 11 / 11 | **11 — whole app offline** | **≈ 10.3 s** |
| **spread** | 2 / 11 | **2 (18%)** | **≈ 2.6 s** |

Observed blast equals placement-predicted blast in every iteration, measured
from EndpointSlice outage troughs (15 s sampling) — not the score, which is
unusable here (a drain leaves every Litmus probe `Unknown`; H1 again).
Recovery scales with concentration too: 11 evicted pods contend to reschedule
at once vs 2.

**The trade-off is the finding**: the same co-location that gives the lowest
east-west tail (H5: 33.9 ms) gives a 100% single-drain outage; `spread` is
the mirror. One graph property, two opposing measured consequences. Framing
per scope-of-claims: this is the **quantification of a known qualitative
trade-off** (cf. AWS cell-based-architecture blast-radius guidance), not its
discovery; the prediction is near-definitional — the empirical content is
that it materializes under real chaos, reproduces, and drives a measured
recovery penalty.

Caveats: two-point contrast (the extremes), single-replica, single cluster.

> **[gradient run results: 6 strategies × node-drain — in flight]** — the
> intermediate-concentration placements (`best-fit`, `dependency-aware`,
> `adversarial`, `random`) to test whether blast radius scales continuously
> with per-node concentration (the availability analogue of H5's predictor).
> `TODO(author): insert results + doctor verdict + archive ID when banked.`

**Figure 5.7 (stub): EndpointSlice ready-count trough timeline through the
drain** (colocate vs spread). **Figure 5.8 (stub): THE trade-off figure** —
per-strategy cross-node fraction on one axis, east-west p95 (H5) and
node-drain blast radius (H6) as opposing gradients. `TODO(author): figure
5.8 is the headline figure of the thesis.`

## 5.7 Per-claim evidence table

| Claim | Data | Test | Figure | Archived run(s) (Appendix A) |
|---|---|---|---|---|
| H1: score cannot rank under session variance | s01–s07, 147 iterations | variance partition; ICC + cluster-bootstrap CI; power/MDE | 5.2, 5.3 | run-20260608-233543 … run-20260610-130249 (7 archives) |
| H2: conntrack flush, spread > colocate | s01–s07 | sign test (7/7, p = 0.0156); Wilcoxon (W = 0, p = 0.0225) | 5.4 | same 7 archives |
| H3: mechanism ⟂ dependent-route outcome | s01–s07, 49 cells | Spearman dep 0.07 vs ctrl 0.29*; TOST equivalence | 5.5 | same 7 archives |
| H4: east-west replicates, user layer does not | 2 × *i* = 4 load batches | ratio replication (1.36–1.39×); dep-vs-ctrl collapse | — | run-20260607-193053, run-20260607-221822 |
| H5: cross-node fraction predicts east-west tail | 8 strategies × *i* = 4 | Spearman ρ = 0.79, n = 8 | 5.6, 5.8 | run-20260608-070638 |
| H6: blast radius + recovery trade-off | 2 node-drain batches | predicted = observed blast, every iteration; recovery contrast | 5.7, 5.8 | `results/20260608-194746`, `results/20260608-205147` (on disk; archive pending) |
